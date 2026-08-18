# CVMeld — Backend API

API principal de CVMeld construida con FastAPI. Gestiona autenticación, perfiles, generación y adaptación de CV, ofertas, compatibilidad, postulaciones, entrevistas, habilidades, créditos y administración.

## Stack

Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy async, PostgreSQL/Supabase, Alembic, LiteLLM, `LLMOrchestrator`, Typst, pytest y Ruff.

La ingesta de ofertas y el worker de ranking son servicios separados. Este backend consume sus datos y servicios mediante configuración; no duplica su lógica.

## Desarrollo local

Requisitos: Python 3.11+ y PostgreSQL.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
```

Configura como mínimo:

```env
DATABASE_URL=postgresql+asyncpg://usuario:password@localhost:5432/cvmeld
JWT_SECRET_KEY=cambia-esta-clave-en-desarrollo
CORS_ORIGINS=http://localhost:3000
APP_ENV=development
```

```powershell
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Documentación: `http://localhost:8000/docs`, `http://localhost:8000/redoc` y `http://localhost:8000/api/v1/health`.

## Variables importantes

Consulta `.env.example` como referencia. Las principales son:

| Variable | Uso |
|---|---|
| `DATABASE_URL` | PostgreSQL async |
| `JWT_SECRET_KEY` | Firma JWT y cifrado |
| `CORS_ORIGINS` | Orígenes del frontend |
| `INGEST_SERVICE_URL` | Servicio de ingesta |
| `LLM_DEFAULT_PROVIDER` | Proveedor LLM por defecto |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `NVIDIA_NIM_API_KEY` | Fallback de proveedores |
| `RESEND_API_KEY`, `RESEND_FROM_EMAIL` | Email transaccional |
| `ADMIN_EMAIL` | Notificaciones administrativas |
| `SENTRY_DSN` | Observabilidad opcional |
| `ORCHESTRATOR_MAX_CONCURRENCY` | Concurrencia LLM |
| `DOCUMENTS_DIR` | Documentos locales |

Las credenciales LLM de usuarios se gestionan por API y se almacenan cifradas. Nunca guardes secretos en el repositorio ni en logs.

## API

Todas las rutas están bajo `/api/v1`. Módulos principales:

- `auth`: registro, login y cuenta.
- `setup`: perfil candidato, perfil conductual y ejemplos STAR.
- `cv`: CV base, CV adaptado, documentos y descargas.
- `jobs` y `rank`: búsqueda y compatibilidad.
- `apply`: documentos por oferta y verificación.
- `interview`, `outcome`, `expand` y `upskill`.
- `billing`: catálogo, créditos, suscripciones y compras.
- `providers`: proveedores y modelos LLM.
- `admin`: usuarios, planes, créditos y configuración.

La especificación actualizada está en `/docs`; no se mantiene aquí una lista manual de endpoints.

## Migraciones

```powershell
alembic upgrade head
alembic revision --autogenerate -m "descripcion"
alembic history
```

Las tablas propias del servicio de ingesta se migran desde su repositorio, no desde este backend.

## Pruebas y calidad

```powershell
pytest -v
pytest --cov=app --cov-report=term-missing
ruff check .
ruff format --check .
```

## Estructura

```text
app/main.py                 aplicación FastAPI
app/api/v1/                 routers HTTP
app/core/                   configuración, seguridad y logging
app/db/                     modelos y sesiones
app/schemas/                contratos Pydantic
app/services/               lógica de negocio
app/services/orchestrator/  cola y ejecución LLM
app/external/typst/         templates PDF
alembic/                    migraciones
tests/                      pruebas
scripts/                    utilidades y E2E
```

## Despliegue

### Render (recomendado, plan free)

El repo incluye `render.yaml` (blueprint) y `Dockerfile`:

1. Subir el repo a GitHub.
2. En [Render](https://render.com) → **New Blueprint** → conectar el repo.
3. Llenar en el dashboard los secrets marcados `sync: false`:
   `DATABASE_URL`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
   `NVIDIA_NIM_API_KEY`, `RESEND_API_KEY`, `SENTRY_DSN`, `INGEST_SERVICE_URL`
   (URL del microservicio de ingesta en Render), y ajustar `CORS_ORIGINS` a la
   URL real del frontend.
4. Deploy. Queda en `https://open-ai-jobs-search-api.onrender.com`.

**Limitaciones del plan free**: se duerme tras 15 min sin tráfico (cold start
~1 min); no hay disco persistente, así que `DOCUMENTS_DIR`/`TRACKER_PATH` se
reinician en cada redeploy; 750 h/mes.

### Fly.io (alternativa)

El servicio se despliega en Fly.io con `Dockerfile` y `fly.toml`:

```bash
flyctl deploy
```

Configura secretos con `flyctl secrets set`. El frontend debe apuntar a la URL pública del backend mediante `NEXT_PUBLIC_API_URL`.
