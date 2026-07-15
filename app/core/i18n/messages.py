"""Backend i18n message catalogs.

Usage::

    from app.core.i18n.locale import t

    msg = t("errors.not_found", locale="es")
    msg = t("rank.completed", locale="en", count=42)

Add a new language by adding a new key to the CATALOG dict.
"""

from typing import Any

CATALOG: dict[str, dict[str, str]] = {
    "en": {
        # ── Common ────────────────────────────────────────────
        "common.loading": "Loading...",
        "common.saved": "Saved successfully",
        "common.deleted": "Deleted successfully",
        "common.error": "An error occurred",
        # ── Auth ──────────────────────────────────────────────
        "auth.login_success": "Logged in successfully",
        "auth.register_success": "Account created successfully",
        "auth.logout_success": "Logged out successfully",
        "auth.invalid_credentials": "Invalid email or password",
        "auth.email_exists": "An account with this email already exists",
        "auth.token_expired": "Session expired. Please log in again.",
        # ── Profile ────────────────────────────────────────────
        "profile.not_found": "Candidate profile not found. Run /setup first.",
        "profile.incomplete": "Profile is incomplete. Please fill in at least name and experience.",
        "profile.saved": "Profile saved successfully",
        "profile.behavioral_saved": "Behavioral profile saved successfully",
        "profile.behavioral_not_found": "No behavioral profile found",
        # ── Providers ──────────────────────────────────────────
        "providers.not_found": "No AI providers configured. Please add at least one provider.",
        "providers.added": "Provider added successfully",
        "providers.removed": "Provider removed successfully",
        "providers.invalid_key": "Invalid API key for {provider}",
        # ── Scrape ──────────────────────────────────────────────
        "scrape.started": "Scraping started on {count} portals",
        "scrape.completed": "Scraping completed — {found} jobs found, {new} new",
        "scrape.no_portals": "Please select at least one portal or leave empty for all",
        # ── Rank ────────────────────────────────────────────────
        "rank.started": "Ranking started for {count} jobs",
        "rank.completed": "Ranking completed — {count} jobs evaluated",
        "rank.failed": "Ranking failed: {error}",
        "rank.no_jobs": "No new jobs to rank",
        "rank.job_evaluating": "Evaluating job {index}/{total}: {title}",
        "rank.job_completed": "Finished job {index}/{total}: {title}",
        # ── Apply ────────────────────────────────────────────────
        "apply.started": "Generating CV and cover letter for job {job_id}",
        "apply.completed": "Application generated successfully",
        "apply.failed": "Application generation failed: {error}",
        "apply.stage.draft": "Generating tailored experience and cover letter...",
        "apply.stage.reviewed": "Reviewing draft documents for issues...",
        "apply.stage.revised": "Applying reviewer feedback and refining...",
        "apply.stage.compiled": "Compiling LaTeX and verifying PDF...",
        "apply.stage.verified": "Application complete — ATS verified.",
        "apply.stage.initializing": "Initializing...",
        # ── Interview ───────────────────────────────────────────
        "interview.prep_generated": "Interview prep pack generated for {stage} stage",
        "interview.mock_started": "Mock interview started",
        "interview.mock_completed": "Mock interview completed — {count} questions answered",
        # ── Expand ──────────────────────────────────────────────
        "expand.started": "Scanning sources for skills...",
        "expand.completed": "Expansion complete — {count} skills discovered",
        "expand.failed": "Expansion failed: {error}",
        # ── Upskill ──────────────────────────────────────────────
        "upskill.started": "Running 4-pass analysis...",
        "upskill.completed": "Upskill analysis complete — {gaps} gaps identified",
        "upskill.failed": "Upskill analysis failed: {error}",
        # ── Errors ──────────────────────────────────────────────
        "errors.not_found": "Resource not found",
        "errors.unauthorized": "Authentication required",
        "errors.forbidden": "You don't have permission to perform this action",
        "errors.validation": "Validation error: {detail}",
        "errors.server_error": "Internal server error",
        "errors.internal": "An unexpected error occurred",
        "errors.provider_not_configured": "No API credentials configured for this provider",
        "errors.profile_incomplete": "Candidate profile is incomplete. Please fill in required fields.",
        "errors.scraper_failed": "Scraper failed to execute",
        "errors.latex_compile_error": "LaTeX compilation failed",
        "errors.duplicate": "This resource already exists",
        "errors.confirmation_required": "Confirmation required for this destructive action",
        "errors.llm_error": "LLM returned an error: {error}",
        "errors.rate_limited": "Rate limited by {provider}. Cooling down.",
        "errors.db_error": "Database error. Please try again.",
        # ── Orchestrator ──────────────────────────────────────
        "orchestrator.queue_paused": "Queue paused",
        "orchestrator.queue_resumed": "Queue resumed",
        "orchestrator.job_cancelled": "Job cancelled: {description}",
        "orchestrator.job_retried": "Retrying job: {description}",
        "orchestrator.all_cancelled": "Cancelled {count} active jobs",
        "orchestrator.all_retried": "Retried {count} failed jobs",
        # ── Pipeline Reset ──────────────────────────────────────
        "pipeline.reset": "Pipeline reset — {count} records deleted",
    },
    "es": {
        # ── Common ────────────────────────────────────────────
        "common.loading": "Cargando...",
        "common.saved": "Guardado correctamente",
        "common.deleted": "Eliminado correctamente",
        "common.error": "Ocurrió un error",
        # ── Auth ──────────────────────────────────────────────
        "auth.login_success": "Sesión iniciada correctamente",
        "auth.register_success": "Cuenta creada correctamente",
        "auth.logout_success": "Sesión cerrada correctamente",
        "auth.invalid_credentials": "Correo o contraseña inválidos",
        "auth.email_exists": "Ya existe una cuenta con este correo",
        "auth.token_expired": "Sesión expirada. Por favor inicia sesión de nuevo.",
        # ── Profile ────────────────────────────────────────────
        "profile.not_found": "Perfil de candidato no encontrado. Ejecuta /setup primero.",
        "profile.incomplete": "El perfil está incompleto. Completa al menos nombre y experiencia.",
        "profile.saved": "Perfil guardado correctamente",
        "profile.behavioral_saved": "Perfil conductual guardado correctamente",
        "profile.behavioral_not_found": "No se encontró perfil conductual",
        # ── Providers ──────────────────────────────────────────
        "providers.not_found": "No hay proveedores IA configurados. Agrega al menos uno.",
        "providers.added": "Proveedor agregado correctamente",
        "providers.removed": "Proveedor eliminado correctamente",
        "providers.invalid_key": "API Key inválida para {provider}",
        # ── Scrape ──────────────────────────────────────────────
        "scrape.started": "Scraping iniciado en {count} portales",
        "scrape.completed": "Scraping completado — {found} empleos encontrados, {new} nuevos",
        "scrape.no_portals": "Selecciona al menos un portal o deja vacío para todos",
        # ── Rank ────────────────────────────────────────────────
        "rank.started": "Ranking iniciado para {count} empleos",
        "rank.completed": "Ranking completado — {count} empleos evaluados",
        "rank.failed": "Ranking fallido: {error}",
        "rank.no_jobs": "No hay nuevos empleos para rankear",
        "rank.job_evaluating": "Evaluando empleo {index}/{total}: {title}",
        "rank.job_completed": "Empleo {index}/{total} completado: {title}",
        # ── Apply ────────────────────────────────────────────────
        "apply.started": "Generando CV y carta para el empleo {job_id}",
        "apply.completed": "Postulación generada correctamente",
        "apply.failed": "Error al generar postulación: {error}",
        "apply.stage.draft": "Generando experiencia y carta de presentación personalizadas...",
        "apply.stage.reviewed": "Revisando borradores en busca de problemas...",
        "apply.stage.revised": "Aplicando retroalimentación y refinando...",
        "apply.stage.compiled": "Compilando LaTeX y verificando PDF...",
        "apply.stage.verified": "Postulación completa — ATS verificado.",
        "apply.stage.initializing": "Inicializando...",
        # ── Interview ───────────────────────────────────────────
        "interview.prep_generated": "Paquete de preparación generado para etapa {stage}",
        "interview.mock_started": "Entrevista simulada iniciada",
        "interview.mock_completed": "Entrevista simulada completada — {count} preguntas respondidas",
        # ── Expand ──────────────────────────────────────────────
        "expand.started": "Escaneando fuentes en busca de habilidades...",
        "expand.completed": "Expansión completada — {count} habilidades descubiertas",
        "expand.failed": "Expansión fallida: {error}",
        # ── Upskill ──────────────────────────────────────────────
        "upskill.started": "Ejecutando análisis de 4 pasos...",
        "upskill.completed": "Análisis completado — {gaps} brechas identificadas",
        "upskill.failed": "Análisis fallido: {error}",
        # ── Errors ──────────────────────────────────────────────
        "errors.not_found": "Recurso no encontrado",
        "errors.unauthorized": "Autenticación requerida",
        "errors.forbidden": "No tienes permiso para realizar esta acción",
        "errors.validation": "Error de validación: {detail}",
        "errors.server_error": "Error interno del servidor",
        "errors.internal": "Ocurrió un error inesperado",
        "errors.provider_not_configured": "No hay credenciales API configuradas para este proveedor",
        "errors.profile_incomplete": "El perfil del candidato está incompleto. Completa los campos requeridos.",
        "errors.scraper_failed": "El scraper falló al ejecutarse",
        "errors.latex_compile_error": "La compilación de LaTeX falló",
        "errors.duplicate": "Este recurso ya existe",
        "errors.confirmation_required": "Se requiere confirmación para esta acción destructiva",
        "errors.llm_error": "El LLM devolvió un error: {error}",
        "errors.rate_limited": "Límite de velocidad alcanzado por {provider}. Enfriando.",
        "errors.db_error": "Error de base de datos. Intenta de nuevo.",
        # ── Orchestrator ──────────────────────────────────────
        "orchestrator.queue_paused": "Cola pausada",
        "orchestrator.queue_resumed": "Cola reanudada",
        "orchestrator.job_cancelled": "Trabajo cancelado: {description}",
        "orchestrator.job_retried": "Reintentando trabajo: {description}",
        "orchestrator.all_cancelled": "{count} trabajos activos cancelados",
        "orchestrator.all_retried": "{count} trabajos fallidos reintentados",
        # ── Pipeline Reset ──────────────────────────────────────
        "pipeline.reset": "Pipeline reiniciado — {count} registros eliminados",
    },
}

_EMPTY: dict[str, str] = {}


def get_messages(locale: str) -> dict[str, str]:
    """Return the message dictionary for the given locale.

    Falls back to English if the locale is not found.
    """
    return CATALOG.get(locale, CATALOG.get("en", _EMPTY))
