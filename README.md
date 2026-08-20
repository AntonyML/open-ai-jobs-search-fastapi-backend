# CVMeld — Backend API

> El cerebro de CVMeld. Una API, múltiples proveedores de IA.

API principal de CVMeld construida con FastAPI: autenticación, perfiles, CV base y adaptado, búsqueda y ranking de ofertas, postulaciones, entrevistas, habilidades, créditos, facturación y administración.

**Stack:** Python 3.11+ · FastAPI · Pydantic v2 · SQLAlchemy async · PostgreSQL/Supabase · Alembic · LiteLLM + LLMOrchestrator · Typst (PDF) · Cloudflare R2 (S3) · pypdf · pytest · Ruff.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Documentación interactiva en `http://localhost:8000/docs`.

## Variables clave

| Variable | Uso |
|---|---|
| `DATABASE_URL` | PostgreSQL async |
| `JWT_SECRET_KEY` | Firma JWT y cifrado |
| `CORS_ORIGINS` | Orígenes del frontend |
| `INGEST_SERVICE_URL` | Servicio de ingesta de ofertas |
| `ANTHROPIC_API_KEY` · `OPENAI_API_KEY` · `NVIDIA_NIM_API_KEY` | Proveedores LLM |
| `RESEND_API_KEY` · `RESEND_FROM_EMAIL` | Email transaccional |
| `R2_ACCOUNT_ID` · `R2_ACCESS_KEY_ID` · `R2_SECRET_ACCESS_KEY` · `R2_BUCKET_NAME` | Almacenamiento de PDFs en Cloudflare R2 (S3-compatible) |
| `R2_SIGNED_URL_TTL` | TTL (segundos) de las signed URLs de descarga de PDFs |

## Almacenamiento de PDFs (R2)

Los PDFs generados se suben a Cloudflare R2 tras compilarse (backend de almacenamiento `local|r2`). Los PDFs en disco local son efímeros en Render/Railway, por eso R2 es el almacén de producción.

```powershell
# Verificar la conexión R2 (configuración + signed URLs)
python scripts/verify_r2.py
```

Sin R2 configurado, el pipeline sigue funcionando con almacenamiento local (default).

## Calidad

```powershell
pytest -v
ruff check .
ruff format --check .
```

## Despliegue

- **Render** (recomendado): blueprint en `render.yaml` → `https://api.cvmeld.tonyml.com`
- **Fly.io**: `flyctl deploy` con `Dockerfile` + `fly.toml`

Los servicios de ingesta y ranking se despliegan desde sus propios repositorios.

## Estructura

```
app/main.py                 aplicación FastAPI
app/api/v1/                 routers HTTP
app/services/orchestrator/  cola y ejecución LLM
app/external/typst/         templates PDF
app/services/r2_storage.py  cliente Cloudflare R2 (S3)
scripts/verify_r2.py        verificación de conexión R2
alembic/                    migraciones
tests/                      pruebas
```