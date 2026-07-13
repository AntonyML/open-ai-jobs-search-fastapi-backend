# Open AI Jobs Search — FastAPI Backend

Backend multi-proveedor de IA para búsqueda de empleos. Reimplementa como servicio Python el framework de `ai-job-search` (que originalmente vive como comandos de Claude Code).

## Stack

- **FastAPI** (async) + **Pydantic v2** + **SQLAlchemy 2.0** (async, asyncpg)
- **LiteLLM** como capa adaptadora multi-proveedor (Anthropic, OpenAI, NVIDIA NIM, LM Studio)
- **Supabase** (PostgreSQL) — Session Pooler o Direct Connection
- **APScheduler** para scraping periódico
- **Bun/TypeScript** scrapers heredados del repo fuente (invocados vía subprocess)

## Estructura

```
FastAPI-backend/
├── app/
│   ├── main.py              # app factory: create_app()
│   ├── core/                 # config, settings, seguridad
│   ├── llm/                   # adaptador LiteLLM
│   ├── db/                     # SQLAlchemy models, session, alembic
│   ├── schemas/                 # Pydantic request/response
│   ├── services/                  # lógica de negocio (un módulo por skill)
│   ├── external/                   # scrapers Bun/TS + LaTeX heredados
│   ├── api/
│   │   ├── deps.py                    # get_db, get_current_user, get_llm_provider
│   │   └── v1/                         # routers versionados
│   └── exceptions.py                    # excepciones de negocio + handlers
├── tests/
│   ├── unit/
│   └── integration/
├── pyproject.toml
├── alembic.ini
└── .env.example
```

## Setup

```bash
# 1. Crear entorno virtual
python -m venv .venv
source .venv/bin/activate  # o .venv\Scripts\activate en Windows

# 2. Instalar dependencias
pip install -e ".[dev]"

# 3. Configurar variables de entorno
cp .env.example .env
# Editar .env con tu DATABASE_URL de Supabase y API keys

# 4. Ejecutar migraciones
alembic upgrade head

# 5. Arrancar el servidor de desarrollo
uvicorn app.main:create_app --factory --reload
```

## Endpoints

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/v1/health` | Liveness probe |

(Más endpoints en FASE 3 cuando se implementen los servicios.)

## Convenciones de diseño

- **`main.py` es una app factory** (`create_app()`), no `app = FastAPI()` a nivel de módulo.
- **`schemas/` vs `db/models.py`**: nunca exponer ORM directo. Cada recurso tiene `XCreate`, `XUpdate`, `XOut`.
- **`api/deps.py`**: dependencias centralizadas (`get_db`, `get_current_user`, `get_llm_provider`).
- **`api/v1/`**: versionado desde el arranque.
- **`exceptions.py`**: excepciones de negocio con handlers registrados. No `HTTPException` sueltas en servicios.
- **LLM siempre vía `app/llm/adapter.py`** — nunca SDK directo de proveedor.
- **Scrapers Bun/TS** se invocan por subprocess desde `app/services/scrape/`, no se reescriben.

## Constraints

- **Repo fuente `ai-job-search` es SOLO LECTURA** — nunca modificar.
- **No commits sin "commit autorizado" explícito.**
- **Hard stop al final de cada fase** — esperar aprobación.
- **Secrets en `.env`**, nunca hardcodeados ni commiteados.
- **NO usar Transaction Pooler** de PgBouncer con asyncpg (rompe prepared statements). Usar Session Pooler o Direct Connection.