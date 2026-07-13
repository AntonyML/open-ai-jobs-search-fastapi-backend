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
| 2026-07-12 | ✅ FASE 3.5 — Outcome (registro resultados) completado |
| 2026-07-12 | ✅ FASE 3.5 — Expand (expansión competencias) completado |
| 2026-07-12 | ✅ FASE 3.5 — Upskill (plan aprendizaje) completado |

---

# 📊 FASE 3.5 — Upskill (Plan de Aprendizaje)
**Estado:** ✅ Completada — esperando aprobación para siguiente skill

---

## 📦 Archivos creados/modificados
| Archivo | Líneas | Descripción |
|---------|--------|-------------|
| models.py | +80 | Modificado — añadido modelo Upskill con desglose completo |
| upskill.py (schemas) | ~150 | Schemas: UpskillRequest, UpskillOut, UpskillSummaryOut, HardSkillGapOut, SynthesizedGapOut, GapHeatmapOut, LearningResourceOut, LearningPlanItemOut + LLM output schemas |
| upskill.py (servicio) | ~500 | Servicio: Pass 1 (hard skill diff), Pass 2 (LLM synthesis), Pass 3 (heatmap), Pass 4 (learning plan con recursos web) |
| upskill.py (router) | ~60 | Router: 3 endpoints bajo /api/v1/upskill/ |
| router.py | ~15 | Modificado — montado router de upskill |
| test_upskill.py | ~300 | 10 tests: aggregate, targeted, perfil no encontrado, sin jobs, job no encontrado, LLM error, get by id, get not found, list, summary gaps count |

---

## 🗄️ Modelo ORM añadido
**`upskills`** — registro de una corrida de análisis upskill:
- user_id, candidate_id (FKs)
- Mode: aggregate (todos los jobs trackeados) | targeted (job único)
- Target job: target_job_posting_id, target_job_url
- Hard skill gaps (Pass 1): hard_skill_gaps (JSONB) — skill, type, priority, source_jobs[], frequency, fit_weight
- Synthesized gaps (Pass 2): synthesized_gaps (JSONB) — skill, type (domain/soft/tooling/credential), priority, evidence
- Gap heatmap: gap_heatmap (JSONB) — skill, type, priority, gap_source
- Learning plan: learning_plan (JSONB) — skill, type, priority, resources[] (title, url, format, duration_hours, cost, quality_score), study_order, prerequisites[], estimated_weeks
- Status: pending → completed | failed, error_message
- Raw LLM response: raw_response (JSONB)

---

## 🔌 Endpoints creados
| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | /api/v1/upskill/ | Ejecutar análisis upskill (síncrono, con mode, target_job_url, target_job_posting_id) |
| GET | /api/v1/upskill/{upskill_id} | Obtener análisis por ID |
| GET | /api/v1/upskill/ | Listar análisis del usuario |

---

## ⚙️ Lógica del servicio (upskill.py)
1. **Guardrails obligatorios** (constantes en código, no configurables)
2. **Flujo completo (4 pasos):**
   - Obtiene perfil candidato — valida que existe
   - Selecciona jobs a analizar: aggregate (todos los jobs con status="ranked") o targeted (job específico)
   - **Pass 1:** Hard skill diff — frequency map + fit-weighting, elimina skills del candidato
   - **Pass 2:** LLM synthesis — domain knowledge, soft skills, tooling/process, credentials
   - **Pass 3:** Heatmap — combina Pass 1 + Pass 2 en tabla unificada
   - **Pass 4:** Learning plan — 2-3 recursos por gap, prioriza gratis, ordena por prioridad y prerequisitos
   - Persiste Upskill + actualiza status

---

## 🛡️ Decisiones de diseño aplicadas
1. Schemas separados de ORM
2. Dependencias centralizadas desde deps.py
3. Excepciones de negocio: LLMError, NotFoundError, ProfileIncompleteError
4. LiteLLM structured output con schemas Pydantic
5. Tests con SQLite in-memory + mocks (no requieren LLM real)
6. Síncrono por ahora — para producción mover a background task

---

## 🚦 Hard Stop — FASE 3.5 Upskill completada
**Listo:**
- ✅ Modelo ORM Upskill con desglose completo (4 passes)
- ✅ Schemas Pydantic validados (incluyendo LLM output schemas)
- ✅ Servicio completo: Pass 1, Pass 2, Pass 3, Pass 4
- ✅ Router con 3 endpoints funcionales
- ✅ 10 tests unitarios (aggregate, targeted, errores, get/list, summary)
- ✅ Session_starter.md actualizado

**Pendiente para iteración futura:**
- ⏸️ Mover upskill a background task
- ⏸️ Cache de enriquecimientos
- ⏸️ Integración real con APIs de búsqueda web
- ⏸️ UI para aprobar/rechazar learning plan

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
- [x] ✅ FASE 3.5 — Outcome (registro resultados)
- [x] ✅ FASE 3.5 — Expand (expansión competencias)
- [x] ✅ FASE 3.5 — Upskill (plan aprendizaje)
- [ ] ⏸️ FASE 3.5 — Add-portal / Add-template / Reset
- [ ] ⏸️ FASE 4 — Esquema de base de datos Supabase
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