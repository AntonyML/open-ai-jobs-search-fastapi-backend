# Artifact lifecycle plan — `generated_cvs/` y `generated/`

> Estado: **aprobado por revisión, Fase 0 ejecutada.** Fases 1a–4 pendientes de implementar.
> Recuento de la auditoría: ver `docs/artifact-audit-results.md`.

## Contexto

Los PDFs del CV generator (`generated_cvs/`) y del pipeline de apply (`generated/`)
acumulan archivos huérfanos porque el borrado nunca libera disco y no existe un GC.

Principio rector: **la DB es la única fuente de verdad. Los PDFs son artefactos
derivados de `cv_json` y siempre se pueden recompilar (`compile_cv`).** Por tanto,
borrar el archivo de forma agresiva es 100% seguro. `cv_json` queda persistido en la
fila hasta que el sweeper la purga tras la retención.

## Inventario de deuda (verificado en código)

| # | Deuda | Ubicación |
|---|---|---|
| 1 | `soft_delete_cv` de CVs `personalized` solo marca `is_deleted=True`; el PDF queda en disco para siempre | `app/services/cv_generator.py:462` |
| 2 | Comentario falso: "keeps the PDF on disk for existing downloads" — `download_cv` usa `get_cv` que filtra `is_deleted=False`, así que el PDF eliminado no se puede descargar: huérfano puro | `app/api/v1/cv.py:214`, `cv_generator.py:312-326` |
| 3 | `_hard_delete_base` es el único camino que libera espacio; regenerar/deleter adaptados deja huérfanos | `cv_generator.py:378-388` |
| 4 | `delete_account` borra la fila `User` pero no las carpetas de archivos | `app/services/auth.py:146-173` |
| 5 | `reset` (scope documents) no cubre `generated_cvs/` ni `generated/` | `app/services/reset.py:65-100` |
| 6 | `job_data` reset no limpia `generated/` (PDFs de applications) | `app/services/job_data.py:37-120` |
| 7 | Rutas relativas resueltas contra `Path.cwd()` → frágil entre deploys/instancias | `cv_generator.py:487-492`, `apply.py:621` |
| 8 | Sin GC: no limpia huérfanos por crash entre compile/commit, filas de bases descartadas, regeneración de adaptados, cuentas borradas | — |
| 9 | `reset.py` hardcodea paths absolutos de terceros (`E:/Dev/PoryectosDeTerceros/...`): deuda muerta + sobre-escritura de archivos ajenos | `reset.py:103-157` |

## Decisiones tomadas (revisión)

- **Secuenciación**: Fase 0 → Fase 1a (fix, deploy solo) → Fase 1b (abstracción) → Fase 2 (sweeper) → Fase 3 (resets/account) → Fase 4 (object storage, condicional). No mezclar fases en un mismo PR/deploy.
- **Retención**: 0 días para el archivo (derivado, se borra inmediato), 30 días para la fila (`is_deleted=True` + `deleted_at`), luego el sweeper purga la fila.
- **`generated/` en la abstracción desde el día uno** (Fase 1b). Abstracción parcial = dos code paths = inconsistencia.
- **Sweeper simple**: corre solo al startup y vía CLI. No task periódica de background (over-engineering a esta escala; se agrega intervalo solo si en el futuro hace falta).
- **Métricas**: ver sección Observabilidad.
- **Rollback**: ver sección Rollback.

---

## Fase 0 — Auditoría (✅ ejecutada)

**Definición de hecho**

- Script dry-run re-ejecutable: `app/scripts/audit_artifacts.py` — clasifica archivos
  como `active` / `deleted` / `orphan` / `unexpected` y cuantifica bytes. Tolerante a DB caída (auditoría solo-filesystem).
- Reporte: `docs/artifact-audit-results.md`.

**Números reales (14-08-2026)**

| Categoría | Archivos | Bytes | Acción |
|---|---:|---:|---|
| Activos (`is_deleted=False`) | 4 | 117.9 KB | keep |
| Borrados (`is_deleted=True`) | 4 | 125.0 KB | borrar archivo (retención 0) |
| Huérfanos (sin fila) | 1 | 27.5 KB | borrar (es `9c3dcd66-….pdf`) |
| Carpetas vacías | 1 (`test-user-id/`) | — | borrar |
| Filas activas con archivo faltante | 0 | — | — |
| Filas sin `pdf_path` | 0 | — | — |

Total recuperable hoy: **~152.5 KB** (4 borrados + 1 huérfano) + 1 carpeta vacía.

---

## Fase 1a — Fix del delete (deploy SOLO)

**Problema activo**: un usuario borra un CV y el PDF queda en disco para siempre.
Es un bug, no deuda. No necesita abstracción nueva: un `os.remove()` en el lugar correcto.

**Cambios**

1. `app/services/cv_generator.py` — en `soft_delete_cv`, antes de `record.is_deleted = True`,
   borrar el PDF best-effort (mismo patrón que `_hard_delete_base`: `resolve_pdf_path` +
   `contextlib.suppress(OSError)` + `unlink()`).
   - `deleted` (personalized): borra archivo + marca fila.
   - `base`: queda como ya está (row + PDF, con promoción del anterior si es active).
2. Corregir el comentario falso en `cv_generator.py:312` (docstring del módulo/`soft_delete_cv`
   y del modelo `GeneratedCV` en `models.py:1477-1479`).
3. **Migración** `deleted_at` en `generated_cvs` con **backfill**:
   `UPDATE generated_cvs SET deleted_at = COALESCE(updated_at, created_at) WHERE is_deleted = TRUE AND deleted_at IS NULL`.
   Sin esto el sweeper no sabe cuándo expiran las filas borradas preexistentes.
4. **1 test**: `test_soft_delete_personalized_removes_pdf` — crea el PDF en disco,
   lo borra por la API, assert `not pdf.exists()` y fila `is_deleted=True`.

**Criterio de aceptación**

- Borrar un CV en `/cv-builder/documents` elimina su PDF de `generated_cvs/` inmediatamente.
- Las 4 filas `deleted` existentes quedan con `deleted_at` backfilled.

**Métricas**

- Log en cada delete directo: `artifact_delete cv_id=… user_id=… type=personalized file_removed=true|false`.
  Si el unlink falla → `file_removed=false` + `logger.warning` (nunca rompe el request).

**Rollback**

- Cambio **aditivo** (unlink + flag + columna nullable). Rollback trivial: revert del commit.
  No hay datos que migrar hacia atrás (la columna es nullable).

---

## Fase 1b — Abstracción `artifact_store.py`

Razón: hoy hay dos writers discrecionales (`cv_generator`, `apply`) con paths relativos
y limpieza inconsistente. La abstracción centraliza root absoluto, sanitización y borrado.

**Cambios**

1. Nuevo `app/services/artifact_store.py`:
   - root absoluto vía settings (`cv_storage_path`, `generated_path`).
   - `store(scope, user_id, artifact_id, payload_bytes)` / `resolve(...)`
   - `remove_file(...)` y `remove_user_dir(...)` (ambos best-effort, idempotentes).
   - **sanitización**: `user_id` / `artifact_id` validados (UUID / whitelist regex) contra
     path traversal (`../`, separadores, etc.). Los IDs actuales son UUIDs.
2. Refactor:
   - `cv_generator._persist_and_compile`, `_hard_delete_base`, `soft_delete_cv`,
     `resolve_pdf_path`, `build_pdf_url` → pasan por `artifact_store`.
   - `apply.py` writer (`generated_dir`, `cv_pdf_path`) → pasa por `artifact_store`.
   - `cv.py` ruta `download` → usa `artifact_store.resolve`.
   - Elimina dependencias de `Path.cwd()`.
3. **`generated/` entra desde el día uno** (decisión de revisión).
4. Tests: path traversal (user_id malicioso), resolve, remove idempotente, root absoluto.

**Observabilidad**: cada operación de store/remove loguea scope + user_id. Errores
inesperados → Sentry (best-effort; nunca rompe el request).

**Rollback**: refactor que preserva comportamiento (mismas rutas, mismo layout).
Revert del PR. Sin migración de datos. Opcional: feature flag `artifact_store_enabled`.

---

## Fase 2 — Sweeper (startup + CLI, NO periódico)

Razón de la simplificación: el sweeper periódico agrega complejidad operativa
(paralelismo, multi-worker, qué pasa si se cae). A esta escala no apaga incendios:
el startup purga lo que dejó un crash y la CLI purga la deuda acumulada.

**Cambios**

1. Nuevo `app/services/storage_sweeper.py`:
   - `async sweep(db, cutoff=now, retention_days=30)` — **reloj inyectable** por parámetro
     para testear sin esperar 30 días (decisión de revisión).
   - Purga, en lotes y best-effort:
     1. archivos de filas `is_deleted=True` con `deleted_at < cutoff - retention_days` → luego la fila.
     2. archivos huérfanos (sin fila) bajo `generated_cvs/` y `generated/`.
     3. carpetas de usuarios sin filas / que ya no existen → `remove_user_dir`.
   - idempotente y tolerante (archivo ya inexistente no es error).
2. Hook en `lifespan` de `app/main.py` (startup only, mismo patrón del purge TTL existente).
3. Script CLI: `python -m app.scripts.sweep_artifacts --dry-run | --execute`.
   Dry-run por defecto.
4. Settings: `artifact_retention_days=30`, `artifact_sweep_enabled=true`.

**Métricas (cada corrida, línea de log `artifact_sweep`)**:
`files_swept` (direct vs sweeper), `bytes_reclaimed`, `orphans_found`, `rows_purged`,
`errors`. Errores inesperados → Sentry.

**Test del sweeper (time):** `sweep(cutoff=fixed_past)` con temp dirs y filas `deleted_at`
fijadas al pasado — verifica expiración sin esperar el reloj real.

**Rollback**: `artifact_sweep_enabled=false` lo apaga; dry-run por defecto limita riesgo.

---

## Fase 3 — Reset & account

1. `delete_account` (`app/services/auth.py`): tras borrar la fila del usuario, llamar
   `artifact_store.remove_user_dir("generated_cvs", uid)` y `remove_user_dir("generated", uid)`.
2. `reset.py` scope documents: sumar `generated_cvs/` al preview + cleanup; y
   **eliminar los paths hardcodeados de terceros** (`_reset_workspace_guidance_files`) —
   deuda de seguridad: sobre-escribe archivos fuera del repo.
3. `job_data.py`: limpiar `generated/{uid}` (PDFs de applications huérfanos por el reset).
4. Tests por cada camino (account delete limpia carpetas; reset previsualiza storage CVs).

**Rollback**: aditivo; revert del PR.

---

## Fase 4 — Object storage (condicional)

La abstracción `artifact_store` permite cambiar backend a S3/R2 sin tocar lógica de
dominio. **Solo** si hay multi-instancia/escala horizontal real. No bloquea el resto.

---

## Observabilidad (requisito transversal)

| Métrica | Fuente |
|---|---|
| Archivos borrados por delete directo (con/sin error) | log `artifact_delete` |
| Archivos barridos por sweeper + bytes reclamados | log `artifact_sweep` (startup + CLI) |
| Huérfanos encontrados por corrida | log `artifact_sweep` |
| Errores de borrado de archivos | log `artifact_delete file_removed=false` + Sentry si es inesperado |

Criterio: sin estas métricas no se sabe si el sweeper funciona o si hay un leak nuevo.

---

## Orden de deploy & criterios de aceptación generales

**Orden**: Fase 1a (sola) → verificar → Fase 1b → Fase 2 → Fase 3.

1. Borrar un CV en `/cv-builder/documents` elimina su PDF de `generated_cvs/` al instante.
2. Tras un sweep no queda ningún archivo huérfano; `generated_cvs/` solo contiene activos + borrados dentro de retención.
3. Borrar cuenta elimina todas las carpetas del usuario en disco.
4. `reset` y `job_data` previsualizan y limpian el storage de CVs.
5. Tests unitarios verdes, offline (sin LLM/typst), siguiendo el patrón existente.

## Riesgos & mitigaciones

- **Archivo en uso durante download**: el sweeper solo toca filas `deleted`/expedidas/huérfanas;
  `download` solo sirve filas activas. Sin carrera real.
- **Multi-worker**: sweeper idempotente + tolerante a `FileNotFoundError`; se ejecuta en la
  instancia de startup (única) o vía CLI.
- **Pérdida de datos**: solo PDFs derivados; `cv_json` en DB permite recompilar. Retención de fila 30 días permite rescate lógico.
- **DB caída en startup**: el hook del sweeper se ejecuta best-effort (mismo patrón que el purge TTL); no bloquea el arranque.