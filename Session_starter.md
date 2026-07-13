# 🧠 AI Session Starter: FastAPI-backend

*Project memory file for AI assistant session continuity. Auto-referenced by custom instructions.*

---

## 📘 Project Context
**Project:** FastAPI-backend — Open AI Jobs Search
**Type:** Backend API (FastAPI + Supabase + LiteLLM)
**Purpose:** Servicio multi-proveedor de IA para búsqueda de empleos: scraping, ranking ATS, generación de CV/carta tailored, y preparación de entrevistas.
**Status:** 🏗️ FASE 1 — Scaffolding en progreso

**Core Technologies:**
- FastAPI (async), Pydantic v2, SQLAlchemy 2.0 (async, asyncpg)
- LiteLLM como capa adaptadora multi-proveedor (Anthropic, OpenAI, NVIDIA NIM, LM Studio)
- Supabase (PostgreSQL) — Session Pooler o Direct Connection
- APScheduler para scraping periódico
- Bun/TypeScript scrapers heredados del repo fuente (invocados vía subprocess)

**Repo fuente (solo lectura):** `E:\Dev\PoryectosDeTerceros\ai-job-search`
**Repo destino:** `E:\Dev\PoryectosDeTerceros\open-ai-jobs-search\FastAPI-backend`

**Available AI Capabilities:**
- 🔧 MCP Servers: everything-re (test), secure-filesy (filesystem), sequential-thinking
- 📚 Documentation: Microsoft docs MCP available for Azure/Microsoft products

---

## 🎯 Current State
**Build Status:** 🔄 FASE 1 — Scaffolding en progreso
**Key Achievement:** FASE 0 completada — auditoría del repo fuente (6 scrapers, 7 archivos de perfil, 9 comandos, 4 tests)
**Active Issue:** Creando estructura base del proyecto FastAPI
**AI Enhancement:** MCP filesystem usado para auditoría del repo fuente

**Architecture Highlights:**
- App factory pattern (`create_app()`) — no `app = FastAPI()` suelto
- Schemas Pydantic separados de modelos ORM (nunca exponer SQLAlchemy directo)
- Dependencias centralizadas en `api/deps.py`
- API versionada desde el arranque (`/api/v1/`)
- Excepciones de negocio propias con handlers registrados
- LiteLLM como única interfaz al LLM (nunca SDK directo de proveedor)
- Scrapers Bun/TS heredados — se invocan por subprocess, no se reescriben
- Sin repository pattern, sin DDD completo, sin `utils/` genérico

---

## 🧠 Technical Memory

**Critical Discoveries:**
- Repo fuente tiene 6 scrapers Bun/TS con contrato uniforme (search + detail, JSON stdout, errores stderr)
- Plantillas LaTeX: moderncv/banking (lualatex) + cover.cls (xelatex) con fuentes Lato/Raleway
- `salary_lookup.py` usa fuzzy matching con normalización de caracteres daneses
- 7 archivos de perfil del candidato (~40 campos) → base para esquema Supabase
- 9 comandos Claude Code documentados como base para servicios Python
- `/scrape` es skill, no comando — usa CLIs instalados + WebSearch fallback

**Performance Insights:**
- asyncpg usa prepared statements por defecto → NO Transaction Pooler (PgBouncer)
- Si se usa Transaction Pooler, requiere `statement_cache_size=0`
- LiteLLM unifica todos los proveedores bajo una sola API
- Scrapers Bun/TS se invocan por subprocess desde `app/services/scrape/` — no se reescriben
- `salary_lookup.py` se importa directo como módulo Python (no subprocess)
- `convert_salary_excel.py` escribe `salary_data.json` en `app/services/salary/` (parent.parent del script)

**Known Constraints:**
- Repo fuente es SOLO LECTURA — nunca modificar
- No commits sin "commit autorizado" explícito de Tony
- Hard stop al final de cada fase — esperar aprobación
- Secrets en `.env`, nunca hardcodeados ni commiteados

---

## 🚀 Recent Achievements
| Date | Achievement |
|------|-------------|
| 2026-07-12 | ✅ Project initialized with session continuity infrastructure |
| 2026-07-12 | ✅ FASE 0 — Auditoría del repo fuente completada |
| 2026-07-12 | ✅ FASE 1 — Scaffolding del proyecto completado |
| 2026-07-12 | ✅ FASE 2 — Componentes reutilizables copiados (6 scrapers, LaTeX, salary) |
| 2026-07-12 | ✅ FASE 3.1 — Setup (perfil candidato) completado |
| 2026-07-12 | ✅ FASE 3.2 — Scrape completado |
| 2026-07-12 | ✅ FASE 3.3 — Rank (evaluación de fit + optimización ATS) completado |
| 2026-07-12 | ✅ FASE 3.4 — Apply (generación CV/carta tailored) completado |
| 2026-07-12 | ✅ FASE 3.5 — Interview (preparación entrevistas) completado |

---

## 📋 Active Priorities
- [x] ✅ FASE 0 — Auditoría del repo fuente
- [x] ✅ FASE 1 — Scaffolding del proyecto
- [x] ✅ FASE 2 — Copiar componentes reutilizables
- [x] ✅ FASE 3.1 — Setup (perfil candidato)
- [x] ✅ FASE 3.2 — Scrape
- [x] ✅ FASE 3.3 — Rank (evaluación de fit + optimización ATS)
- [x] ✅ FASE 3.4 — Apply (generación CV/carta tailored con LaTeX)
- [x] ✅ FASE 3.5 — Interview (preparación entrevistas)
- [ ] ⏸️ FASE 3.5 — Outcome (registro resultados)
- [ ] ⏸️ FASE 3.5 — Expand (expansión competencias)
- [ ] ⏸️ FASE 3.5 — Upskill (plan aprendizaje)
- [ ] ⏸️ FASE 3.5 — Add-portal / Add-template / Reset
- [ ] ⏸️ FASE 4 — Esquema de base de datos Supabase

---

## 🔧 Development Environment
**Common Commands:**
- `uvicorn app.main:create_app --factory --reload` — dev server
- `pytest` — tests
- `ruff check .` / `ruff format .` — lint + format
- `alembic upgrade head` — migraciones

**Key Files:**
- `app/main.py` — app factory
- `app/core/settings.py` — pydantic-settings
- `app/llm/adapter.py` — LiteLLM wrapper
- `app/db/session.py` — SQLAlchemy async engine
- `app/api/deps.py` — dependencias FastAPI centralizadas
- `app/exceptions.py` — excepciones de negocio + handlers

**Setup Requirements:**
- Python 3.11+
- Bun (para scrapers heredados)
- LaTeX (lualatex + xelatex) para CV/carta
- Supabase project (Session Pooler o Direct Connection)

**AI Tools:**
- MCP secure-filesy: filesystem seguro
- MCP everything-re: testing tools
- MCP sequential-thinking: razonamiento estructurado

---

*This file serves as persistent project memory for enhanced AI assistant session continuity with MCP server integration.*