# Open Ai Jobs Search — FastAPI Backend

Backend multi-proveedor de IA para la búsqueda automatizada de empleo. Orquesta un pipeline completo: desde la búsqueda de empleos (alimentada por el **microservicio de ingesta**) hasta la generación de CV/cover letter optimizados para ATS, preparación de entrevistas, tracking de resultados y calibración de fit.

> **Inspirado en:** [MadsLorentzen/ai-job-search](https://github.com/MadsLorentzen/ai-job-search)

---

## Tabla de contenidos

- [Arquitectura](#arquitectura)
- [Microservicio de ingesta](#microservicio-de-ingesta)
- [Pipeline completo](#pipeline-completo)
- [Estructura](#estructura)
- [Prerrequisitos](#prerrequisitos)
- [Setup](#setup)
- [Seed de administrador](#seed-de-administrador)
- [Migraciones (Alembic)](#migraciones-alembic)
- [Endpoints](#endpoints)
- [LLM Orchestrator](#llm-orchestrator)
- [Drafter-Reviewer Pipeline](#drafter-reviewer-pipeline)
- [Variables de entorno](#variables-de-entorno)
- [Testing](#testing)
- [Deployment](#deployment)
- [Convenciones de diseño](#convenciones-de-diseño)

---

## Stack

- **FastAPI** (async) + **Pydantic v2** + **SQLAlchemy 2.0** (async, asyncpg)
- **LiteLLM** — capa adaptadora multi-proveedor (Anthropic, OpenAI, NVIDIA NIM, LM Studio, Ollama)
- **LLMOrchestrator** — sistema de colas persistente, failover automático, rate limiting, health monitoring, WebSocket real-time
- **Supabase** (PostgreSQL) — Session Pooler o Direct Connection
- **Typst** — compilación de PDFs (CV + cover letter) **in-process**, sin subprocesos ni LaTeX
- **Microservicio de Ingesta** (Telegram → `ingested_jobs`) — la API principal **lee** los empleos de la tabla compartida
- **i18n** — soporte multi-idioma (en, es) con detección automática vía cookie/Accept-Language
- **WebSocket** — notificaciones real-time del estado de la cola de ejecución
- **Fernet** (cryptography) — cifrado de API keys por usuario en DB
- **aiocache** — cache en memoria de KPIs de dashboard
- **Sentry** — monitoreo de errores en producción
- **Resend** — email transaccional (upgrades, donaciones)

---

## Arquitectura

```
┌──────────────────────────────────────────────────────────────────┐
│                        Frontend (Next.js)                        │
└──────────────────────────────┬───────────────────────────────────┘
                               │ HTTP / WebSocket
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                      API Principal (FastAPI)                     │
│                                                                  │
│  Auth │ Providers │ Setup │ Jobs/Search │ Rank │ Apply │        │
│  Interview │ Outcome │ Upskill │ Expand │ Salary │ Verify        │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                    LLMOrchestrator                         │  │
│  │  Provider/Model Manager │ ExecutionQueue │ QueueNotifier   │  │
│  │  (failover, rate limits, checkpoints, WebSocket real-time) │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Microservicio de Ranking (rankjobs :8002) → cola de ranking     │
│  (FOR UPDATE SKIP LOCKED, sesiones cortas LOAD/RANK/SAVE)        │
└───────────────┬───────────────────────────────────┬──────────────┘
                │ LEE ingested_jobs                 │ POST /api/v1/ingest
                ▼                                   ▼
┌────────────────────────────┐      ┌────────────────────────────────┐
│       Supabase / Postgres  │      │  Microservicio de Ingesta      │
│  ingested_jobs (TTL 72h)   │◀─────│  Telegram → parse → INSERT     │
│  + tablas del pipeline     │      │  (regex deterministas, sin LLM)│
└────────────────────────────┘      └────────────────────────────────┘
```

> El `POST /api/v1/ingest` es un endpoint **del microservicio** (su propia ruta); la API principal solo lo consume vía `INGEST_SERVICE_URL`.

### Principios arquitectónicos

1. **El LLM es un componente del pipeline, no el centro.** Todo lo que puede resolverse con algoritmos deterministas (keyword extraction, score normalization, ATS checks, skill linting, content validation) se implementa sin LLM.

2. **Failover automático.** Si un proveedor responde 429, timeout o error, el orquestador cambia automáticamente al siguiente modelo/proveedor sin interrumpir la cola.

3. **Checkpoints persistentes.** Cada job se persiste en DB. Si el backend se reinicia, retoma desde el último checkpoint. Nunca desde cero.

4. **Sanitización antes de validación.** Las respuestas del LLM se sanitizan (truncar arrays, trim strings, reparar JSON, llenar defaults) ANTES de pasarlas por Pydantic. Solo se rechazan respuestas irrecuperables.

5. **Worker de ranking como microservicio independiente.** El ranking corre en un microservicio propio (repo `open-ai-jobs-search-microservice-rankjobs-backend`, puerto 8002), separado de la API. Usa `FOR UPDATE SKIP LOCKED` para evitar contención, sesiones de BD cortas (solo LOAD y SAVE), y LLM calls sin conexión a DB abierta.

6. **La ingesta de empleos es responsabilidad del microservicio.** La API principal nunca scrapea portales: lee la tabla compartida `ingested_jobs` y, si no hay resultados suficientes, dispara una ingesta al microservicio vía `POST /api/v1/ingest`.

---

## Microservicio de ingesta

La búsqueda de empleos está separada en un **microservicio propio** (repo `open-ai-jobs-search-microservice-searchjobs-backend`):

```
Telegram (canales públicos) → [Microservicio] → Supabase (ingested_jobs) → [API Principal] → Frontend
```

- El microservicio escucha canales públicos de Telegram, parsea con regex deterministas (sin LLM) y escribe en `ingested_jobs` (TTL 72h).
- La API principal **solo hace SELECT** sobre `ingested_jobs` (modelos `IngestedJob` / `IngestJob` en `app/db/models.py`, migradas por el propio microservicio con su alembic `alembic_version_ingest`).
- Sincronización vía **DB compartida**: sin callbacks, sin webhooks. El microservicio INSERTA, la API lee.
- URL del microservicio configurable con `INGEST_SERVICE_URL` (default `http://localhost:8001`).

Ver el README del microservicio para su setup (Telethon, api_id/api_hash, migraciones propias).

---

## Pipeline completo

```mermaid
flowchart LR
    A[0. Providers<br/>Configurar LLM] --> B[1. Setup<br/>Perfil candidato]
    B --> C[2. Search<br/>Búsqueda de jobs]
    C --> D[3. Rank<br/>Evaluación de fit]
    D --> E[4. Apply<br/>CV + carta tailored]
    D --> H[7. Upskill<br/>Plan aprendizaje]
    D --> I[8. Expand<br/>Expansión competencias]
    E --> F[5. Interview<br/>Prep entrevista]
    F --> G[6. Outcome<br/>Registro resultados]
    E --> J[9. Verify<br/>Checklist ATS + calidad]
    G --> K[10. Calibrate<br/>Calibración de fit]
```

### Fase 0 — Provider Setup
Cada usuario configura su propio proveedor LLM (API key + modelo). Las credenciales se cifran con Fernet y se almacenan por usuario. El usuario puede seleccionar el modelo específico dentro de cada proveedor. Soporta: Anthropic, OpenAI, NVIDIA NIM, LM Studio, Ollama (catálogo en `app/schemas/providers.py`).

### Fase 1 — Setup (perfil candidato)
Perfil completo: datos personales, experiencia, educación, skills, **perfil conductual** (DISC, fortalezas, áreas de crecimiento), y **ejemplos STAR** para entrevistas.

### Fase 2 — Search (búsqueda de empleos)
Los empleos **no se scrapean en este backend**: el microservicio de ingesta lee Telegram y los escribe en `ingested_jobs`.

- `POST /api/v1/jobs/search` — busca por keywords/ubicación en `ingested_jobs` (solo jobs no expirados).
- Si hay menos de 5 resultados, dispara una ingesta al microservicio (`POST {INGEST_SERVICE_URL}/api/v1/ingest`) y responde con `ingest_job_id` + `fresh=false`.
- El estado de la ingesta se consulta en `GET /api/v1/jobs/search/{ingest_job_id}/status` (queued → running → done/failed).

### Fase 3 — Rank (evaluación de fit)

Evaluación multi-dimensión usando el **RankAnalyzer** determinista:
- **Técnico**: keyword overlap, experiencia requerida, skills matching (3 niveles: exacto → categoría → señal semántica)
- **Experiencia**: años, seniority, industrias relevantes
- **Comportamental**: alineación con perfil conductual
- **Carrera**: salario (si hay datos), ubicación, modalidad (remote/hybrid)
- **Fit general**: combinación ponderada + qualitative reasoning del LLM

Los jobs a rankear provienen de la búsqueda (`ingested_jobs`) o de `job_ids` explícitos; al rankear se persisten como `JobPosting`.

**Microservicio de ranking**: El ranking se ejecuta en un microservicio independiente (repo `open-ai-jobs-search-microservice-rankjobs-backend`, puerto 8002). El API crea los jobs en una cola (`execution_job_items`) y el microservicio los reclama con `FOR UPDATE SKIP LOCKED`. Cada item se procesa en 3 fases:
1. **LOAD** — sesión corta para cargar candidato + job posting
2. **RANK** — sin conexión a DB: extracción → scores cuantitativos → llamada LLM
3. **SAVE** — sesión corta para persistir `RankEvaluation` + actualizar `JobPosting`

Esto permite escalar el microservicio horizontalmente y garantiza que no haya sesiones abiertas durante LLM calls.

### Fase 4 — Apply (generación de documentos)
**Pipeline Drafter-Reviewer-Revise** de 3 etapas:
1. **Drafter**: genera CV + cover letter estructurados (JSON) adaptados al posting específico
2. **Reviewer**: agente con contexto fresco que critica el draft buscando keywords faltantes, framing débil, claims inventados, lenguaje genérico
3. **Revise**: el drafter recibe el feedback y corrige

Luego de la revisión:
- **CV Cutter**: si el CV supera 2 páginas, corta bullets por relevancia (keyword overlap + unicidad + referencias en cover letter)
- **Compilación**: PDFs generados **in-process con Typst** (`app/services/pdf_compiler_typst.py`, templates en `app/external/typst/`) — sin subprocesos, sin LaTeX
- **ATS Check**: verifica parseabilidad del PDF (sin `(cid:*)` markers, keyword coverage ≥70%, email/nombre como texto literal, orden de lectura correcto). Usa `pdftotext`/`pdfinfo` (poppler) si están disponibles; si no, devuelve advertencia sin bloquear
- **Verification Checklist**: 10+ checks deterministas + LLM para consistencia y claims fabricados

> La compilación de PDFs es 100% **Typst** (in-process); no se compila LaTeX en ningún punto del pipeline.

### Fase 5 — Interview (preparación)
Prep pack completo: research de empresa, preguntas probables mapeadas a ejemplos STAR del candidato, bridge answers para gaps de experiencia, **mock interview** (chat interactivo donde el LLM juega el rol del entrevistador, con historial de conversación persistente).

### Fase 6 — Outcome (tracking)
Registro de resultados: entrevista, oferta, rechazo, silencio. Calibración del fit framework basada en qué realmente consiguió entrevistas.

### Fase 7 — Upskill (análisis de gaps)
4-pass analysis: hard gaps → synthesis → heatmap → learning plan. Compara skills del perfil contra los requerimientos de todos los jobs rankeados.

### Fase 8 — Expand (enriquecimiento de perfil)
Escanea fuentes públicas (GitHub, portfolio, LinkedIn) para descubrir competencias no explícitas en el CV.

### Fase 9 — Verify (verificación de documentos)
Checklist de 10+ verificaciones deterministas + 1 verificación con LLM ejecutada como servicio independiente. Verifica nombre, email, rol, empresa, fechas, estructura del documento, placeholders, markers CID, keywords, y consistencia cualitativa.

### Fase 10 — Calibrate (calibración de fit)
Análisis 100% determinista que correlaciona outcomes reales (entrevistas, ofertas, rechazos) con keywords, skills y patrones para refinar el framework de ranking. Genera métricas de funnel (aplicación → entrevista → oferta → hired) y recomendaciones accionables.

---

## Estructura

```
FastAPI-backend/
├── app/
│   ├── main.py                    # app factory: create_app() (+ app = create_app() a nivel módulo)
│   ├── core/                      # settings, security (JWT + Fernet), logging, task_manager, i18n
│   ├── llm/                       # adaptador LiteLLM
│   ├── db/
│   │   ├── models.py              # SQLAlchemy models (+ IngestedJob / IngestJob del microservicio)
│   │   └── session.py             # async engine + session factory
│   ├── schemas/                   # Pydantic v2 request/response
│   ├── services/                  # Lógica de negocio
│   │   ├── auth.py                # Registro, login, upgrade, donaciones
│   │   ├── setup.py               # Perfil candidato + conductual + STAR
│   │   ├── job_search.py          # Lee ingested_jobs y dispara el microservicio
│   │   ├── rank.py / rank_analyzer.py / rank_extractor.py / rank_jobs.py
│   │   ├── apply.py / apply_json.py   # Drafter-Reviewer-Revise (JSON + Typst)
│   │   ├── pdf_compiler_typst.py  # Compilación PDF in-process con Typst
│   │   ├── ats_check.py / pdf_verifier.py / cv_cutter.py
│   │   ├── verification.py        # Verification checklist (10+ checks)
│   │   ├── interview.py / outcome.py / upskill.py / expand.py
│   │   ├── fit_calibration.py / pipeline_reset.py / reset.py
│   │   ├── provider_credentials.py / provider_models.py / tiers.py / email.py
│   │   ├── orchestrator/          # LLMOrchestrator, ExecutionQueue, ProviderManager, etc.
│   │   └── salary/                # Salary benchmarking
│   ├── api/
│   │   ├── deps.py                # get_db, get_current_user, get_llm_provider, get_locale
│   │   └── v1/                    # auth, providers, orchestrator, setup, rank, apply, interview,
│   │                              # outcome, expand, upskill, salary, verification,
│   │                              # pipeline_reset, reset, admin, dashboard, analytics, users, jobs
│   ├── utils/                     # pdf_verifier.py
│   └── external/
│       └── typst/                 # Templates Typst (cv, cover letter, entry)
├── tests/
│   ├── unit/                      # ~21 archivos de test (SQLite in-memory + mocks)
│   └── integration/
├── alembic/                       # 23 migraciones
├── scripts/                       # run_api.py, test_e2e.py, test_e2e_full.py, test_e2e_inprocess.py
├── entrypoint.sh                  # Docker entrypoint (arranca uvicorn)
├── dev.ps1                        # Script desarrollo: arranca la API
├── pyproject.toml
├── Dockerfile                     # Python 3.11 + Typst (in-process)
└── fly.toml                       # Config Fly.io (proceso web)
```

---

## Prerrequisitos

| Componente | Propósito | Instalación |
|------------|-----------|-------------|
| **Python ≥ 3.11** | Runtime | `python.org` o `pyenv` |
| **Microservicio de Ingesta** | Alimenta `ingested_jobs` (Telegram) | Repo `open-ai-jobs-search-microservice-searchjobs-backend` |
| **poppler-utils** (opcional) | `pdftotext` / `pdfinfo` para el ATS check | `apt install poppler-utils` (o binarios en el PATH) |
| **Supabase** | Base de datos PostgreSQL | Proyecto en supabase.com |
| **LLM Provider** | API key (Anthropic, OpenAI, NVIDIA NIM, LM Studio, Ollama) | Según provider |

> No se requiere LaTeX/MiKTeX: los PDFs se compilan con **Typst** (pip, in-process).

---

## Setup

```bash
# 1. Entorno virtual
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 2. Instalar dependencias
pip install -e ".[dev]"

# 3. Variables de entorno
cp .env.example .env
# Editar DATABASE_URL, JWT_SECRET_KEY, INGEST_SERVICE_URL

# 4. Migraciones
alembic upgrade head
```

### Arranque (2 procesos + microservicio)

El backend consta de la API + dos microservicios (ingesta y ranking):

- **Microservicio de Ingesta** (puerto 8001) — alimenta `ingested_jobs`. Setup en su propio README (`uvicorn app.main:app --port 8001`).
- **Microservicio de Ranking** (puerto 8002) — procesa la cola de ranking. Setup en su propio README (repo `open-ai-jobs-search-microservice-rankjobs-backend`).
- **API** (uvicorn) — sirve endpoints HTTP en `http://127.0.0.1:8000`

```powershell
# Windows — arranca la API
.\dev.ps1
```

```bash
# Linux/Mac — terminal 1: API
uvicorn app.main:app --reload

# Terminal 2: Microservicio de ranking
cd ../open-ai-jobs-search-microservice-rankjobs-backend && uvicorn app.main:app --port 8002

# Alternativa: script que arranca la API
python scripts/run_api.py
```

Healthcheck: `GET /api/v1/health`

---

## Seed de administrador

Para promover un usuario existente a administrador, ejecuta esta query directamente en **Supabase SQL Editor** (o cualquier cliente PostgreSQL):

```sql
-- 🔥 Cambiar rol a admin para el usuario con email específico
UPDATE users
SET role = 'admin',
    tier = 'premium',
    updated_at = NOW()
WHERE email = 'tu-email@ejemplo.com';

-- ✅ Verificar
SELECT id, email, role, tier, is_active FROM users WHERE email = 'tu-email@ejemplo.com';
```

> ⚠️ Después de cambiar el rol, el usuario debe **cerrar sesión y volver a iniciarla** para que el JWT se genere con el nuevo rol (`admin`). Una vez logueado como admin, accede a `/admin` en el frontend.

### Insertar un admin desde cero

Si el usuario aún no existe, primero genera un **bcrypt hash** de la contraseña:

```bash
# Con Node.js
node -e "require('bcryptjs').hash('tu-contraseña', 10).then(console.log)"
```

O desde Python:

```python
import bcrypt
hash = bcrypt.hashpw(b"tu-contraseña", bcrypt.gensalt()).decode()
print(hash)
```

Luego inserta:

```sql
INSERT INTO users (id, email, hashed_password, full_name, is_active, role, tier, active_provider, preferred_language, created_at, updated_at)
VALUES (
  gen_random_uuid()::text,
  'admin@ejemplo.com',
  '$2b$12$...',  -- ← reemplaza con el hash bcrypt que generaste
  'Admin User',
  TRUE,
  'admin',
  'premium',
  'anthropic',
  'en',
  NOW(),
  NOW()
);
```

---

## Migraciones (Alembic)

```bash
alembic revision --autogenerate -m "descripción"
alembic upgrade head
alembic history
```

> ⚠️ Las tablas `ingested_jobs` / `ingest_jobs` las migra el **microservicio de ingesta** con su propio alembic (`alembic_version_ingest`). No crearlas desde este repo.

---

## Endpoints

Todos bajo `/api/v1` (salvo el WebSocket).

### Health

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/v1/health` | Liveness probe |

### Auth

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/v1/auth/register` | Registro de usuario |
| POST | `/api/v1/auth/login` | Login (devuelve JWT, rate-limited por IP+email) |
| GET | `/api/v1/auth/me` | Perfil del usuario actual |
| DELETE | `/api/v1/auth/account` | Eliminar cuenta (requiere contraseña + confirmación) |
| POST | `/api/v1/auth/upgrade` | Solicitar upgrade de plan (notifica al admin) |
| POST | `/api/v1/auth/donate` | Notificar donación |

### Providers — Configuración LLM

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/v1/providers/` | Catálogo de proveedores conocidos |
| POST | `/api/v1/providers/` | Guardar credencial cifrada |
| GET | `/api/v1/providers/me` | Proveedores del usuario |
| GET | `/api/v1/providers/me/active` | Proveedor activo actual |
| GET | `/api/v1/providers/me/model` | Modelo seleccionado del proveedor activo |
| PUT | `/api/v1/providers/active` | Cambiar proveedor activo |
| PATCH | `/api/v1/providers/{provider}` | Actualizar credencial parcial |
| DELETE | `/api/v1/providers/{provider}` | Eliminar credencial |
| POST | `/api/v1/providers/test` | Probar conexión del proveedor activo |
| GET | `/api/v1/providers/{provider}/models` | Listar modelos disponibles |
| PUT | `/api/v1/providers/{provider}/models` | Seleccionar modelo activo |
| POST | `/api/v1/providers/{provider}/models` | Sincronizar modelos disponibles |

### Setup — Perfil del candidato

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/v1/setup/profile` | Crear perfil |
| GET | `/api/v1/setup/profile` | Obtener perfil |
| PATCH | `/api/v1/setup/profile` | Actualizar perfil |
| DELETE | `/api/v1/setup/profile` | Eliminar perfil |
| POST | `/api/v1/setup/profile/complete` | Marcar completo |
| GET | `/api/v1/setup/behavioral-profile` | Perfil conductual (DISC, fortalezas) |
| PUT | `/api/v1/setup/behavioral-profile` | Actualizar perfil conductual |
| GET | `/api/v1/setup/star-examples` | Listar ejemplos STAR |
| POST | `/api/v1/setup/star-examples` | Crear ejemplo STAR |
| DELETE | `/api/v1/setup/star-examples/{id}` | Eliminar ejemplo STAR |

### Jobs — Búsqueda de empleos (ingesta microservicio)

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/v1/jobs/search` | Buscar jobs en `ingested_jobs` (dispara ingesta si hay <5 resultados) |
| GET | `/api/v1/jobs/search/{ingest_job_id}/status` | Estado de una ingesta (queued → running → done/failed) |

### Rank — Evaluación de fit

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/v1/rank/` | Ejecutar ranking (header `Idempotency-Key`, rate-limited) |
| GET | `/api/v1/rank/status/{job_id}` | Estado de un job de ranking |
| POST | `/api/v1/rank/cancel/{job_id}` | Cancelar un job de ranking |
| GET | `/api/v1/rank/jobs/count` | Jobs pendientes/rankeados |
| GET | `/api/v1/rank/jobs` | Jobs rankeados (filtros: min_score, verdict) |
| GET | `/api/v1/rank/jobs/{job_id}/evaluation` | Evaluación completa de un job |

### Apply — Generación de CV/carta

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/v1/apply/` | Generar aplicación (pipeline drafter-reviewer, async) |
| GET | `/api/v1/apply/available-jobs` | Jobs rankeados disponibles para aplicar |
| GET | `/api/v1/apply/{application_id}` | Obtener aplicación por ID |
| GET | `/api/v1/apply/{application_id}/status` | Estado del pipeline (queued → … → verified/failed) |
| GET | `/api/v1/apply/` | Listar aplicaciones |
| POST | `/api/v1/apply/{application_id}/verify` | Ejecutar verification checklist |

### Interview — Preparación de entrevistas

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/v1/interview/` | Generar prep pack |
| GET | `/api/v1/interview/{prep_id}` | Obtener prep por ID |
| GET | `/api/v1/interview/` | Listar preps |
| POST | `/api/v1/interview/{prep_id}/mock` | Iniciar mock interview / enviar respuesta |

### Outcome — Registro de resultados

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/v1/outcome/` | Registrar outcome |
| PATCH | `/api/v1/outcome/{outcome_id}` | Actualizar outcome |
| GET | `/api/v1/outcome/{outcome_id}` | Obtener outcome |
| GET | `/api/v1/outcome/` | Listar outcomes |
| GET | `/api/v1/outcome/tracker/rows` | Filas del tracker (CSV-like) |
| GET | `/api/v1/outcome/calibration` | Reporte de calibración del fit framework |

### Expand — Expansión de competencias

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/v1/expand/` | Expandir competencias (CV, LinkedIn, diplomas, GitHub) |
| GET | `/api/v1/expand/{expansion_id}` | Obtener expansión por ID |
| GET | `/api/v1/expand/` | Listar expansiones |

### Upskill — Plan de aprendizaje

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/v1/upskill/` | Ejecutar análisis (aggregate o targeted) |
| GET | `/api/v1/upskill/{upskill_id}` | Obtener análisis por ID |
| GET | `/api/v1/upskill/` | Listar análisis |

### Salary — Benchmarking salarial

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/v1/profile/salary-data` | Cargar datos de salario (JSON) |
| GET | `/api/v1/profile/salary-data` | Obtener datos de salario del usuario |
| DELETE | `/api/v1/profile/salary-data` | Eliminar datos de salario |

### Dashboard & Analytics

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/v1/dashboard/stats` | KPIs del pipeline (jobs, ranked, applications, hired) |
| GET | `/api/v1/dashboard/pipeline` | Progreso del pipeline por paso |
| GET | `/api/v1/analytics/funnel` | Datos de funnel de conversión |

### Pipeline Reset

| Método | Ruta | Descripción |
|--------|------|-------------|
| DELETE | `/api/v1/pipeline-reset` | Resetear pipeline (jobs, evaluaciones, aplicaciones, preps, outcomes, historial de cola, expansiones, upskill) |

### Orchestrator — Monitoreo y control

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/v1/orchestrator/queue` | Estado de la cola de ejecución (cacheado 1s) |
| POST | `/api/v1/orchestrator/queue/control` | Controlar cola: `pause`, `resume`, `cancel`, `retry_failed` |
| GET | `/api/v1/orchestrator/jobs/{job_id}` | Estado de un job de ejecución |
| GET | `/api/v1/orchestrator/providers` | Salud de todos los proveedores |
| GET | `/api/v1/orchestrator/models` | Salud de los modelos (filtro `?provider=`) |
| WebSocket | `/api/v1/orchestrator/ws?token=<JWT>` | Notificaciones real-time de la cola |

### Users & Admin

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/v1/users/usage` | Uso y límites del usuario autenticado |
| GET | `/api/v1/admin/users` | Listar usuarios (admin) |
| GET | `/api/v1/admin/users/{user_id}` | Detalle de un usuario (admin) |
| PATCH | `/api/v1/admin/users/{user_id}` | Actualizar usuario: role, tier (admin) |
| DELETE | `/api/v1/admin/users/{user_id}` | Eliminar usuario (admin) |

### Configuración

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/v1/reset/` | Resetear perfil/documentos del usuario (requiere confirmación) |

---

## LLM Orchestrator

El orchestrator es el corazón del sistema de ejecución. Reemplaza las llamadas directas a LiteLLM. Se compone de módulos en `app/services/orchestrator/`.

### Provider Registry

Cada proveedor tiene:
- **status**: `healthy`, `degraded`, `down`, `disabled`
- **priority**: orden de preferencia (1 = más preferido)
- **cooldown_until**: timestamp de cuándo se desbloquea
- **last_latency**: última latencia en ms
- **success_rate**: ratio de éxito (0.0 - 1.0)
- **429_count**: contador de rate limits
- **timeout_count**: contador de timeouts
- **health_score**: score compuesto (0-100)

### Model Registry

Cada modelo tiene:
- **state**: `READY`, `BUSY`, `COOLDOWN`, `DISABLED`
- **priority**: dentro del mismo proveedor
- **cost**: costo por 1M tokens
- **context**: tamaño de contexto máximo
- **average_latency**: latencia promedio
- **average_success**: tasa de éxito promedio

### Failover automático

```
429 recibido
  → Leer Retry-After o exponential backoff
  → Marcar modelo en COOLDOWN
  → Intentar: mismo proveedor → mismo proveedor otro modelo
    → otro proveedor mismo modelo → siguiente proveedor
  → Pausar cola solo si ningún modelo disponible
```

### Queue states

`pending` → `queued` → `running` → `retrying` → `rate_limited` → `cooling_down` → `completed` | `failed` | `cancelled` | `skipped`

### Concurrencia

Configurable con `ORCHESTRATOR_MAX_CONCURRENCY` (default 4). Semaphore-based. Respeta rate limits por proveedor.

---

## Drafter-Reviewer Pipeline

El pipeline de 3 etapas para generación de documentos:

```
Job Posting + Candidate Profile
        │
        ▼
┌───────────────────┐
│    STAGE 1        │
│    DRAFT          │
│                   │
│  Llama al LLM     │
│  para generar     │
│  CV + cover       │
│  letter (JSON)    │
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│    STAGE 2        │
│    REVIEW         │
│                   │
│  Llama SEPARADA   │
│  (sin contexto    │
│  del draft previo)│
│                   │
│  - Keywords       │
│    faltantes      │
│  - Claims         │
│    inventados     │
│  - Framing débil  │
│  - Lenguaje       │
│    genérico       │
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│    STAGE 3        │
│    REVISE         │
│                   │
│  Draft original + │
│  feedback del     │
│  reviewer         │
│                   │
│  Genera versión   │
│  corregida        │
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│    POST-REVISE    │
│                   │
│  - CV Cutter      │
│    (≤2 páginas)   │
│  - Compilación    │
│    PDF con Typst  │
│    (in-process)   │
│  - ATS Check      │
│  - Verification   │
│    Checklist      │
└───────────────────┘
```

### Verification Checklist (~10 checks)

**Deterministas:**
- Nombre del candidato en el CV
- Email literal en el CV (no solo ícono)
- Rol del posting en el profile statement
- Empresa en la cover letter
- Fechas en formato consistente
- Estructura del documento correcta (sin placeholders `[YOUR_NAME]` sin reemplazar)
- Sin `(cid:*)` markers en el PDF (vía `pdftotext`)
- Keywords del posting ≥70% presentes en CV

**Con LLM (1 llamada al final):**
- Profile statement específico al rol (no genérico)
- Sin claims fabricados
- Tono consistente entre CV y cover letter

---

## Variables de entorno

| Variable | Requerida | Default | Descripción |
|----------|-----------|---------|-------------|
| `DATABASE_URL` | ✅ | — | PostgreSQL async (`postgresql+asyncpg://...`) |
| `JWT_SECRET_KEY` | ✅ | `change-me` | Firma JWT (≥64 chars en prod). También deriva la clave Fernet |
| `INGEST_SERVICE_URL` | — | `http://localhost:8001` | URL del microservicio de ingesta |
| `LLM_DEFAULT_PROVIDER` | — | `anthropic` | Proveedor por defecto (`anthropic`, `openai`, `nvidia_nim`, `lm_studio`, `ollama`) |
| `LLM_TIMEOUT` | — | `180` | Timeout de llamadas LLM (segundos) |
| `ANTHROPIC_API_KEY` | — | — | Fallback si no hay credencial en DB |
| `OPENAI_API_KEY` | — | — | Fallback si no hay credencial en DB |
| `NVIDIA_NIM_API_KEY` | — | — | Fallback si no hay credencial en DB |
| `LM_STUDIO_API_BASE` | — | `http://localhost:1234/v1` | Base URL para LM Studio |
| `JWT_ALGORITHM` | — | `HS256` | Algoritmo JWT |
| `JWT_EXPIRE_MINUTES` | — | `1440` | Expiración JWT (24h) |
| `APP_ENV` | — | `development` | `development` / `production` |
| `LOG_LEVEL` | — | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `CORS_ORIGINS` | — | `http://localhost:3000` | Orígenes CORS (separados por coma) |
| `RATE_LIMIT_ATTEMPTS` | — | `5` | Intentos fallidos de login antes de bloquear |
| `RATE_LIMIT_WINDOW_SECONDS` | — | `900` | Ventana de rate limiting (15 min) |
| `DEFAULT_LANGUAGE` | — | `en` | Idioma por defecto (`en`, `es`) |
| `RESEND_API_KEY` | — | — | API key para email transaccional |
| `RESEND_FROM_EMAIL` | — | `onboarding@resend.dev` | Remitente de los correos |
| `ADMIN_EMAIL` | — | `admin@openajobs.com` | Email del admin (upgrades, donaciones) |
| `SENTRY_DSN` | — | — | DSN de Sentry para errores en producción |
| `ORCHESTRATOR_MAX_CONCURRENCY` | — | `4` | Workers LLM concurrentes del orquestador |
| `DOCUMENTS_DIR` | — | `documents` | Directorio de documentos de usuario |
| `TRACKER_PATH` | — | `documents/tracker.json` | Tracker de aplicaciones (CSV) |

> Las API keys de proveedores LLM se registran vía API (`POST /api/v1/providers/`) y se almacenan **cifradas con Fernet** en DB por usuario. Las variables `*_API_KEY` del `.env` son solo fallback.

---

## Testing

```bash
# Tests unitarios (SQLite in-memory, sin dependencias externas)
pytest tests/unit/ -v

# Tests de integración
pytest tests/integration/ -v

# Todos los tests
pytest -v

# Con coverage
pytest --cov=app --cov-report=term-missing

# Módulo específico
pytest tests/unit/test_rank.py -v --tb=short
```

**E2E con el microservicio** (requiere el microservicio corriendo en `:8001`):

```bash
python scripts/test_e2e.py        # ingest → API principal lee ingested_jobs
python scripts/test_e2e_full.py   # search → select → rank con job_ids
```

---

## Deployment

La app deploya en **Fly.io** con Docker sobre `python:3.11-slim-bookworm`. Los PDFs se compilan con **Typst** (vía pip, in-process). No requiere LaTeX, MiKTeX ni Bun.

El `fly.toml` define un solo proceso:
- **`web`** — API HTTP (uvicorn)

```bash
# Desplegar API (proceso web)
flyctl deploy --process-groups web

# Variables de entorno
flyctl secrets set DATABASE_URL="postgresql+asyncpg://..." \
    JWT_SECRET_KEY="$(openssl rand -hex 32)" \
    INGEST_SERVICE_URL="https://ingesta-tu-app.fly.dev" \
    CORS_ORIGINS='["https://tu-frontend.com"]' \
    ANTHROPIC_API_KEY="..."

```

> **Microservicio de ingesta:** debe estar desplegado y apuntar `INGEST_SERVICE_URL` hacia él (ver su README). La tabla `ingested_jobs` es compartida.

Ver `fly.toml` para configuración de máquina (1GB RAM).

El `entrypoint.sh` arranca directamente uvicorn (la variable `DOCKER_PROCESS` ya no se usa: el worker de ranking vive en su propio microservicio).

---

## Convenciones de diseño

- **`main.py` es app factory** (`create_app()`) y además expone `app = create_app()` a nivel de módulo (para `uvicorn app.main:app`).
- **Schemas Pydantic separados** de modelos SQLAlchemy. Cada recurso tiene `XCreate`, `XUpdate`, `XOut`.
- **Dependencias centralizadas** en `api/deps.py` (`get_db`, `get_current_user`, `get_llm_provider`, `get_locale`).
- **Excepciones de negocio** en `exceptions.py` con handlers registrados. No `HTTPException` en servicios.
- **LLM siempre vía LLMOrchestrator**, nunca SDK directo de proveedor.
- **Sanitización antes de validación**: truncar arrays, reparar JSON, llenar defaults antes de Pydantic.
- **Determinista primero**: si se puede resolver sin LLM, se resuelve sin LLM.
- **Logging estructurado** con `logging` estándar: request_id por petición, archivos por categoría (`app.access`, `app.sql`, `app.llm`, `scraper.log`) y handlers/filters propios en `app/core/logging`.
- **Rate limiting** en login (por IP+email) y en `/rank`.
- **i18n** con detección vía cookie `NEXT_LOCALE` o `Accept-Language`.
