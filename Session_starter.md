# 🧠 Session Starter — FastAPI-backend

---

## 👤 Tareas Manuales (antes del front)

- [ ] Configurar `.env` (DATABASE_URL, API keys, JWT_SECRET_KEY)
- [ ] Crear proyecto Supabase (Session Pooler)
- [ ] Instalar Bun
- [ ] `bun install` en los 6 scrapers (`app/external/scrapers/*/cli/`)
- [ ] Instalar LaTeX (lualatex + xelatex)
- [ ] Registrar API keys en `/providers` (Anthropic, OpenAI, NVIDIA NIM, LM Studio, Ollama)
- [ ] Probar flujo end-to-end completo
- [ ] Verificar migraciones en Supabase (`alembic upgrade head`)

---

## 🗺️ Roadmap — Ideas

### FASE 6 — Async & Persistencia
- BackgroundTasks para apply/interview/add-portal
- APScheduler job persistence en DB
- Webhooks/callbacks para jobs async
- Cola de tareas (Celery / RQ / Dramatiq)

### FASE 7 — Producción
- Rate limiting & quotas
- OpenTelemetry tracing
- Prometheus metrics
- OpenAPI docs mejoradas
- Health checks detallados
- Graceful shutdown
- Docker + docker-compose
- CI/CD (GitHub Actions)

### FASE 8 — Calidad
- Integration tests E2E
- Load testing (k6 / Locust)
- Property-based testing (Hypothesis)
- Contract testing (Pact)
- Mutation testing (mutmut)

### FASE 9 — Features
- Multi-tenant / workspaces
- Roles & permisos (RBAC)
- Audit log
- Exportación de datos (PDF/CSV)
- Notificaciones (email / push)
- Compartir resultados (links públicos)
- Versionado de CVs
- Templates de CV personalizables
- Integración con calendario (entrevistas)
- Browser extension para scrape
- Mobile app (React Native / Expo)

### FASE 10 — IA
- RAG sobre perfil del candidato
- Embeddings + vector store (pgvector)
- Re-ranking semántico de jobs
- Fine-tuning de prompts
- A/B testing de prompts
- Streaming de respuestas (SSE)
- Multi-agente (orchestration)
- Feedback loop (RLHF)
- Detección de sesgo en ranking
- Explicabilidad de decisiones ATS
