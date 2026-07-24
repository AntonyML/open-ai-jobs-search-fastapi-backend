# Open Ai Jobs Search — FastAPI Backend

Backend multi-proveedor de IA para la búsqueda automatizada de empleo. Orquesta un pipeline completo: desde el scraping de portales hasta la generación de CV/cover letter optimizados para ATS, preparación de entrevistas, tracking de resultados y calibración de fit.

> **Inspirado en:** [MadsLorentzen/ai-job-search](https://github.com/MadsLorentzen/ai-job-search)

---

## Tabla de contenidos

- [Stack](#stack)
- [Arquitectura](#arquitectura)
- [Pipeline completo](#pipeline-completo)
- [Estructura](#estructura)
- [Prerrequisitos](#prerrequisitos)
- [Setup](#setup)
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
- **LiteLLM** — capa adaptadora multi-proveedor (Anthropic, OpenAI, NVIDIA NIM, Groq, OpenRouter, LM Studio, Ollama)
- **LLMOrchestrator** — sistema de colas persistente, failover automático, rate limiting, health monitoring, WebSocket real-time
- **Supabase** (PostgreSQL) — Session Pooler o Direct Connection
- **APScheduler** — scraping periódico
- **Bun/TypeScript** scrapers (linkedin, jobindex, jobnet, jobdanmark, freehire, jobbank)
- **LaTeX** (lualatex + xelatex) — generación de CV y cover letters tailored
- **i18n** — soporte multi-idioma (en, es) con detección automática vía cookie/Accept-Language
- **WebSocket** — notificaciones real-time del estado de la cola de ejecución
- **Fernet** (cryptography) — cifrado de API keys por usuario en DB
- **Sentry** — monitoreo de errores en producción
- **Resend** — email transaccional (upgrades, donaciones)

---

## Arquitectura

```
┌──────────────────────────────────────────────────────────────────┐
│                     LLMOrchestrator                               │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ Provider     │  │ Model        │  │ ExecutionQueue        │  │
│  │ Manager      │  │ Manager      │  │ (concurrency,         │  │
│  │ (health,     │  │ (states,     │  │  checkpointing,       │  │
│  │  priorities, │  │  selection,  │  │  persistence)         │  │
│  │  cooldowns)  │  │  costs)      │  │                       │  │
│  └──────────────┘  └──────────────┘  └───────────────────────┘  │
│  ┌──────────────┐  ┌──────────────────────────────────────────┐ │
│  │ QueueNotifier│  │ LLMResponseSanitizer                     │ │
│  │ (WebSocket   │  │ (repair JSON, truncate arrays,           │ │
│  │  real-time)  │  │  fill defaults before Pydantic)          │ │
│  └──────────────┘  └──────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────┐
│                      Services Layer                               │
│  Scrape → Rank → Apply → Interview → Outcome → Upskill           │
│  Expand → Verification → ATS Check → CV Cutter → PDF Compiler    │
│  Fit Calibration → Salary Benchmarking → Pipeline Reset          │
│  Tiers → Email → Provider Credentials → Provider Models          │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Deterministic Analyzers                        │
│  RankAnalyzer │ SkillLinter │ ContentGuard │ PDFVerifier         │
│  SalaryLookup │ KeywordExtractor │ AtsChecker                   │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Cross-Cutting                                  │
│  i18n (en/es) │ Middleware (PII guard) │ Dashboard & Analytics   │
└──────────────────────────────────────────────────────────────────┘
```

### Principios arquitectónicos

1. **El LLM es un componente del pipeline, no el centro.** Todo lo que puede resolverse con algoritmos deterministas (keyword extraction, score normalization, ATS checks, skill linting, content validation) se implementa sin LLM.

2. **Failover automático.** Si un proveedor responde 429, timeout o error, el orquestador cambia automáticamente al siguiente modelo/proveedor sin interrumpir la cola.

3. **Checkpoints persistentes.** Cada job se persiste en DB. Si el backend se reinicia, retoma desde el último checkpoint. Nunca desde cero.

4. **Sanitización antes de validación.** Las respuestas del LLM se sanitizan (truncar arrays, trim strings, reparar JSON, llenar defaults) ANTES de pasarlas por Pydantic. Solo se rechazan respuestas irrecuperables.

5. **Worker de ranking como proceso separado.** El ranking corre en un worker independiente del API (proceso Fly.io separado). Usa `FOR UPDATE SKIP LOCKED` para evitar contención, sesiones de BD cortas (solo LOAD y SAVE), y LLM calls sin conexión a DB abierta.

---

## Pipeline completo

```mermaid
flowchart LR
    A[0. Providers<br/>Configurar LLM] --> B[1. Setup<br/>Perfil candidato]
    B --> C[2. Scrape<br/>Búsqueda de jobs]
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
Cada usuario configura su propio proveedor LLM (API key + modelo). Las credenciales se cifran con Fernet y se almacenan por usuario. El usuario puede seleccionar el modelo específico dentro de cada proveedor. Soporta: Anthropic, OpenAI, NVIDIA NIM, Groq, OpenRouter, LM Studio, Ollama.

### Fase 1 — Setup (perfil candidato)
Perfil completo: datos personales, experiencia, educación, skills, **perfil conductual** (DISC, fortalezas, áreas de crecimiento), y **ejemplos STAR** para entrevistas.

### Fase 2 — Scrape
Scraping multi-portal vía scrapers Bun/TS. Deduplicación automática por `(portal, external_id)`. Los scrapers corren en paralelo via `asyncio.gather`.

### Fase 3 — Rank (evaluación de fit)

Evaluación multi-dimensión usando el **RankAnalyzer** determinista:
- **Técnico**: keyword overlap, experiencia requerida, skills matching (3 niveles: exacto → categoría → señal semántica)
- **Experiencia**: años, seniority, industrias relevantes
- **Comportamental**: alineación con perfil conductual
- **Carrera**: salario (si hay datos), ubicación, modalidad (remote/hybrid)
- **Fit general**: combinación ponderada + qualitative reasoning del LLM

**Worker separado**: El ranking se ejecuta en un proceso independiente (`app/worker.py`). El API crea los jobs en una cola (`execution_job_items`) y el worker los reclama con `FOR UPDATE SKIP LOCKED`. Cada item se procesa en 3 fases:
1. **LOAD** — sesión corta para cargar candidato + job posting
2. **RANK** — sin conexión a DB: extracción → scores cuantitativos → llamada LLM
3. **SAVE** — sesión corta para persistir `RankEvaluation` + actualizar `JobPosting`

Esto permite escalar workers horizontalmente y garantiza que no haya sesiones abiertas durante LLM calls.

### Fase 4 — Apply (generación de documentos)
**Pipeline Drafter-Reviewer-Revise** de 3 etapas:
1. **Drafter**: genera CV + cover letter en LaTeX adaptados al posting específico
2. **Reviewer**: agente con contexto fresco que investiga la empresa y critica los drafts buscando keywords faltantes, framing débil, claims inventados, lenguaje genérico
3. **Revise**: el drafter recibe el feedback y corrige

Luego de la revisión:
- **CV Cutter**: si el CV supera 2 páginas, corta bullets por relevancia (keyword overlap + unicidad + referencias en cover letter)
- **Compilación**: `lualatex` para CV, `xelatex` para cover letter, con verificación de páginas y orphans
- **ATS Check**: verifica parseabilidad del PDF (sin `(cid:*)` markers, keyword coverage ≥70%, orden de lectura correcto)
- **Verification Checklist**: 10+ checks deterministas + LLM para consistencia y claims fabricados

### Fase 5 — Interview (preparación)
Prep pack completo: research de empresa, preguntas probables mapeadas a ejemplos STAR del candidato, bridge answers para gaps de experiencia, **mock interview** (chat interactivo donde el LLM juega el rol del entrevistador, con historial de conversación persistente).

### Fase 6 — Outcome (tracking)
Registro de resultados: entrevista, oferta, rechazo, silencio. Calibración del fit framework basada en qué realmente consiguió entrevistas.

### Fase 7 — Upskill (análisis de gaps)
4-pass analysis: hard gaps → synthesis → heatmap → learning plan. Compara skills del perfil contra los requerimientos de todos los jobs rankeados.

### Fase 8 — Expand (enriquecimiento de perfil)
Escanea fuentes públicas (GitHub, portfolio, LinkedIn) para descubrir competencias no explícitas en el CV.

### Fase 9 — Verify (verificación de documentos)
Checklist de 10+ verificaciones deterministas + 1 verificación con LLM ejecutada como servicio independiente. Verifica nombre, email, rol, empresa, fechas, LaTeX balanceado, placeholders, markers CID, keywords, y consistencia cualitativa.

### Fase 10 — Calibrate (calibración de fit)
Análisis 100% determinista que correlaciona outcomes reales (entrevistas, ofertas, rechazos) con keywords, skills y patrones para refinar el framework de ranking. Genera métricas de funnel (aplicación → entrevista → oferta → hired) y recomendaciones accionables.

---

## Estructura

```
FastAPI-backend/
├── app/
│   ├── main.py                    # app factory: create_app()
│   ├── worker.py                  # Worker separado de ranking (FOR UPDATE SKIP LOCKED)
│   ├── core/                      # config (settings, security, logging, scheduler, task_manager, i18n)
│   ├── llm/                       # adaptador LiteLLM
│   ├── db/
│   │   ├── models.py              # SQLAlchemy models (~20 tablas)
│   │   └── session.py             # async engine + session factory
│   ├── schemas/                   # Pydantic v2 request/response (21 módulos)
│   ├── services/                  # Lógica de negocio (~28 módulos)
│   │   ├── auth.py                # Registro, login, upgrade, donaciones
│   │   ├── setup.py               # Perfil candidato + conductual + STAR
│   │   ├── scrape.py              # Orquestación scrapers Bun/TS
│   │   ├── rank.py                # Ranking + RankAnalyzer determinista
│   │   ├── rank_analyzer.py       # Cómputo cuantitativo de scores (5 dimensiones)
│   │   ├── rank_extractor.py      # Extracción estructurada (skills, años, modalidad, etc.)
│   │   ├── rank_jobs.py           # Orquestación del job de ranking (idempotencia)
│   │   ├── apply.py               # Drafter-Reviewer-Revise pipeline
│   │   ├── interview.py           # Prep + mock interview
│   │   ├── outcome.py             # Tracking + fit calibration
│   │   ├── upskill.py             # Skill gap analysis (4-pass)
│   │   ├── expand.py              # Perfil enrichment
│   │   ├── verification.py        # Verification checklist (10+ checks)
│   │   ├── ats_check.py           # ATS parseability
│   │   ├── cv_cutter.py           # Relevance-weighted trimming
│   │   ├── pdf_compiler.py        # LaTeX compilation loop
│   │   ├── pipeline_reset.py      # Clean slate reset
│   │   ├── provider_credentials.py # Fernet-encrypted creds
│   │   ├── provider_models.py     # Model catalog per provider
│   │   ├── tiers.py               # Límites free/premium
│   │   ├── email.py               # Resend email integration
│   │   ├── reset.py               # Reset de datos de usuario
│   │   ├── fit_calibration.py     # Correlación outcome → keyword
│   │   ├── add_portal.py          # Registro de portales
│   │   ├── add_template.py        # Registro de templates LaTeX
│   │   ├── salary/                # Salary benchmarking
│   │   └── orchestrator/          # 9 módulos (LLMOrchestrator, ExecutionQueue, ProviderManager, ModelManager, etc.)
│   ├── utils/                     # pdf_verifier.py, skill_linter.py
│   ├── middleware/                # content_guard.py (detección PII)
│   ├── external/                  # scrapers Bun/TS (6), templates LaTeX
│   ├── api/
│   │   ├── deps.py                # get_db, get_current_user, get_llm_provider, get_locale
│   │   └── v1/                    # ~17 routers (auth, setup, scrape, rank, apply, interview, outcome, expand, upskill, salary, providers, orchestrator, verification, add_portal, add_template, pipeline_reset, reset, dashboard, users, admin)
│   └── exceptions.py              # Excepciones centralizadas
├── tests/
│   ├── unit/                      # ~20 archivos de test (SQLite in-memory + mocks)
│   └── integration/
├── alembic/                       # 15 migraciones DB
├── scripts/                       # check_miktex.py
├── entrypoint.sh                  # Docker entrypoint (API o worker según DOCKER_PROCESS)
├── dev.ps1                        # Script desarrollo: arranca API + worker localmente
├── pyproject.toml
├── Dockerfile                     # Multi-stage (Python + MiKTeX + Bun)
└── fly.toml                       # Config Fly.io (procesos app + worker)
```

---

## Prerrequisitos

| Componente | Propósito | Instalación |
|------------|-----------|-------------|
| **Python 3.12.10 | Runtime | `python.org` o `pyenv` |
| **Bun** | Ejecutar scrapers TS heredados | `npm install -g bun` |
| **LaTeX** (lualatex + xelatex) | Compilar CV y cover letters | MiKTeX o TeX Live |
| **Supabase** | Base de datos PostgreSQL | Proyecto en supabase.com |
| **LLM Provider** | API key (Anthropic, OpenAI, NVIDIA, Groq, OpenRouter, etc.) | Según provider |

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
# Editar DATABASE_URL, JWT_SECRET_KEY

# 4. Migraciones
alembic upgrade head

# 5. Instalar dependencias de los scrapers (TypeScript)
bun install --cwd app/external/scrapers/freehire-search/cli
bun install --cwd app/external/scrapers/jobbank-search/cli
bun install --cwd app/external/scrapers/jobdanmark-search/cli
bun install --cwd app/external/scrapers/jobindex-search/cli
bun install --cwd app/external/scrapers/jobnet-search/cli
bun install --cwd app/external/scrapers/linkedin-search/cli

# 6. Servidor de desarrollo

El backend consta de dos procesos:
- **API** (uvicorn) — sirve endpoints HTTP
- **Worker** — procesa la cola de ranking en segundo plano

Para arrancar ambos a la vez:

```powershell
# Windows — arranca API + worker automáticamente
.\dev.ps1
```

```bash
# Linux/Mac — terminal 1: API
uvicorn app.main:create_app --factory --reload

# Terminal 2: Worker
python -m app.worker
```

Servidor en `http://127.0.0.1:8000`. Healthcheck: `GET /api/v1/health`

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

---

## Endpoints

### Health

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/v1/health` | Liveness probe |

### Auth

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/v1/auth/register` | Registro de usuario |
| POST | `/api/v1/auth/login` | Login (devuelve JWT) |
| GET | `/api/v1/auth/me` | Perfil del usuario actual |
| DELETE | `/api/v1/auth/account` | Eliminar cuenta (requiere confirmación) |
| POST | `/api/v1/auth/upgrade` | Solicitar upgrade de plan |
| POST | `/api/v1/auth/donate` | Notificar donación |

### Providers — Configuración LLM

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/v1/providers/` | Catálogo de proveedores conocidos |
| POST | `/api/v1/providers/` | Guardar credencial cifrada |
| GET | `/api/v1/providers/me` | Proveedores del usuario |
| GET | `/api/v1/providers/me/active` | Proveedor activo actual |
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

### Scrape — Búsqueda de empleos

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/v1/scrape/` | Ejecutar scraping multi-portal |
| GET | `/api/v1/scrape/runs` | Historial de corridas |
| GET | `/api/v1/scrape/jobs` | Jobs encontrados (filtros: status, portal) |
| GET | `/api/v1/scrape/jobs/{job_id}` | Detalle de un job |

### Rank — Evaluación de fit

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/v1/rank/` | Ejecutar ranking (focus_area, re_rank, top_n) |
| GET | `/api/v1/rank/jobs` | Jobs rankeados (filtros: min_score, verdict) |
| GET | `/api/v1/rank/jobs/{job_id}/evaluation` | Evaluación completa de un job |

### Apply — Generación de CV/carta

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/v1/apply/` | Generar aplicación (pipeline drafter-reviewer) |
| GET | `/api/v1/apply/{application_id}` | Obtener aplicación por ID |
| GET | `/api/v1/apply/` | Listar aplicaciones |
| GET | `/api/v1/apply/{application_id}/status` | Estado del pipeline de generación |
| POST | `/api/v1/apply/{application_id}/verify` | Ejecutar verification checklist |
| GET | `/api/v1/apply/{application_id}/verify` | Resultado de verificación |

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
| POST | `/api/v1/salary/data` | Cargar datos de salario (JSON o Excel) |
| GET | `/api/v1/salary/data` | Obtener datos de salario del usuario |
| DELETE | `/api/v1/salary/data` | Eliminar datos de salario |

### Dashboard & Analytics

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/v1/dashboard/stats` | KPIs del pipeline (scraped, ranked, applications, hired) |
| GET | `/api/v1/dashboard/pipeline` | Progreso del pipeline por paso |
| GET | `/api/v1/analytics/funnel` | Datos de funnel de conversión |

### Pipeline Reset

| Método | Ruta | Descripción |
|--------|------|-------------|
| DELETE | `/api/v1/pipeline-reset` | Resetear pipeline (borra jobs, runs, métricas, health metrics) |

### Orchestrator — Monitoreo y control

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/v1/orchestrator/status` | Estado general del orquestador |
| GET | `/api/v1/orchestrator/providers` | Estado de todos los proveedores |
| POST | `/api/v1/orchestrator/providers/{provider}/toggle` | Habilitar/deshabilitar proveedor |
| GET | `/api/v1/orchestrator/queue` | Estado de la cola de ejecución |
| POST | `/api/v1/orchestrator/pause` | Pausar cola |
| POST | `/api/v1/orchestrator/resume` | Reanudar cola |
| POST | `/api/v1/orchestrator/cancel/{job_id}` | Cancelar job |
| POST | `/api/v1/orchestrator/retry/{job_id}` | Reintentar job fallido |
| POST | `/api/v1/orchestrator/clear` | Limpiar cola |
| WebSocket | `/api/v1/orchestrator/ws` | Notificaciones real-time de la cola |

### Users — Administración

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/v1/users/` | Listar usuarios (admin) |
| PATCH | `/api/v1/users/{user_id}` | Actualizar usuario (role, tier) |
| DELETE | `/api/v1/users/{user_id}` | Eliminar usuario |

### Configuración

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/v1/add-portal/` | Registrar portal de scraping |
| GET | `/api/v1/add-portal/` | Listar portales |
| POST | `/api/v1/add-template/` | Registrar template LaTeX |
| POST | `/api/v1/add-template/switch` | Cambiar template activo |
| GET | `/api/v1/add-template/` | Listar templates |
| POST | `/api/v1/reset/` | Resetear datos del usuario (requiere confirmación) |

---

## LLM Orchestrator

El orchestrator es el corazón del sistema de ejecución. Reemplaza las llamadas directas a LiteLLM. Se compone de 9 módulos en `app/services/orchestrator/`.

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

Configurable (2, 4, 8 workers). Semaphore-based. Respeta rate limits por proveedor.

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
│  letter en LaTeX  │
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
│    LaTeX          │
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
- LaTeX balanceado (mismo número de `{` y `}`)
- Sin placeholders `[YOUR_NAME]` sin reemplazar
- Sin `(cid:*)` markers en PDF
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
| `JWT_SECRET_KEY` | ✅ | `change-me` | Firma JWT (≥64 chars en prod) |
| `JWT_ALGORITHM` | — | `HS256` | Algoritmo JWT |
| `JWT_EXPIRE_MINUTES` | — | `1440` | Expiración JWT (24h) |
| `APP_ENV` | — | `development` | `development` / `production` |
| `LOG_LEVEL` | — | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `CORS_ORIGINS` | — | `["http://localhost:3000"]` | Orígenes CORS permitidos |
| `LATEX_BIN_DIR` | — | `None` | Directorio de binarios LaTeX |
| `SCRAPE_INTERVAL_HOURS` | — | `6` | Intervalo del scheduler |
| `ORCHESTRATOR_MAX_WORKERS` | — | `4` | Workers concurrentes del orquestador |
| `RESEND_API_KEY` | — | — | API key para email transaccional |
| `SENTRY_DSN` | — | — | DSN de Sentry para errores en producción |

> Las API keys de proveedores LLM se registran vía API (`POST /api/v1/providers/`) y se almacenan **cifradas con Fernet** en DB por usuario. No van en `.env`.

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

---

## Deployment

La app deploya en **Fly.io** con Docker multi-stage (instala MiKTeX vía apt del repositorio de Debian bookworm).

El `fly.toml` define dos procesos:
- **`app`** — API HTTP (uvicorn)
- **`worker`** — worker de ranking (entrypoint con `DOCKER_PROCESS=worker`)

```bash
# Desplegar API
flyctl deploy --process-groups app

# Desplegar worker
flyctl deploy --process-groups worker

# O ambos a la vez
flyctl deploy

# Variables de entorno
flyctl secrets set DATABASE_URL="postgresql+asyncpg://..." \
    JWT_SECRET_KEY="$(openssl rand -hex 32)" \
    CORS_ORIGINS='["https://tu-frontend.com"]'

# Escalar workers
flyctl scale count worker=2
```

Ver `fly.toml` para configuración de máquina (1GB RAM mínimo por MiKTeX + LaTeX).

### MiKTeX Portable (auto-instalación)

El endpoint `/apply` compila CV y cover letter con `lualatex` (CV) y `xelatex` (cover). En **Windows**, el setup se hace automáticamente al ejecutar `dev.ps1` (que corre `python scripts/setup_latex.py`).

`setup_latex.py` descarga el instalador portable desde **GitHub Releases** (tag `latex-v1`) o desde `MIKTEX_DOWNLOAD_URL` si está definida en `.env`, lo extrae en `app/external/latex/miktex-portable/`, y escribe `LATEX_BIN_DIR` en `.env`.

**Requisito:** Antes del primer uso hay que crear un Release `latex-v1` en GitHub con `miktex-portable.exe` como adjunto, o definir `MIKTEX_DOWNLOAD_URL` en `.env`.

**Verificación manual:**
```bash
python scripts/setup_latex.py
# → OK: MiKTeX Portable listo para usar
```

En **Docker** (Linux), MiKTeX se instala vía `apt` (ver `Dockerfile`). En **Linux/Mac** nativo, instalar TeX Live manualmente.

Los binarios de MiKTeX Portable **no se commitean** (~150 MB, en `.gitignore`).

---

## Convenciones de diseño

- **`main.py` es app factory** (`create_app()`), no `app = FastAPI()` global.
- **Schemas Pydantic separados** de modelos SQLAlchemy. Cada recurso tiene `XCreate`, `XUpdate`, `XOut`.
- **Dependencias centralizadas** en `api/deps.py` (`get_db`, `get_current_user`, `get_llm_provider`, `get_locale`).
- **Excepciones de negocio** en `exceptions.py` con handlers registrados. No `HTTPException` en servicios.
- **LLM siempre vía LLMOrchestrator**, nunca SDK directo de proveedor.
- **Sanitización antes de validación**: truncar arrays, reparar JSON, llenar defaults antes de Pydantic.
- **Determinista primero**: si se puede resolver sin LLM, se resuelve sin LLM.
- **Logging estructurado** con `structlog` (JSON en prod, consola en dev).
- **Rate limiting** en login (por IP+email).
- **i18n** con detección vía cookie `NEXT_LOCALE` o `Accept-Language`.
