"""Email service using the Resend API (no external SDK needed — uses httpx).

Sends transactional emails for payment requests, donations, and admin contact.
"""

from __future__ import annotations


from app.core.settings import get_settings

from app.core.logging import get_logger, bind_context
logger = get_logger(__name__)

settings = get_settings()


async def send_resend_email(
    to: str,
    subject: str,
    html_body: str,
    *,
    from_name: str = "Open Ai Jobs Search",
) -> dict:
    """Send an email via the Resend API."""
    with bind_context(stage="email"):
        logger.info("Sending email | to=%s subject=%s", to, subject)
        if not settings.resend_api_key:
            logger.warning("RESEND_API_KEY is not configured — email not sent")
            return {"status": "skipped", "reason": "RESEND_API_KEY not configured"}

        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": f"{from_name} <{settings.resend_from_email}>",
                    "to": [to],
                    "subject": subject,
                    "html": html_body,
                },
            )
            try:
                resp.raise_for_status()
            except Exception:
                logger.warning("Resend API returned error (status=%s): %s", resp.status_code, resp.text[:500])
                return {"status": "error", "reason": f"Resend API error: {resp.status_code}"}
            return resp.json()


async def send_upgrade_request(
    admin_email: str,
    user_email: str,
    user_name: str,
    method: str,
    phone: str | None = None,
) -> dict:
    """Notify the admin that a user wants to upgrade.

    Args:
        admin_email: Admin email to notify.
        user_email: User's email address.
        user_name: User's full name.
        method: "sinpe" or "email".
        phone: Costa Rican phone number (required for SINPE).
    """
    method_label = "SINPE Móvil" if method == "sinpe" else "Correo electrónico"
    phone_line = f"<p><strong>Teléfono (SINPE):</strong> {phone}</p>" if phone else ""
    html = f"""<h2>Solicitud de actualización de plan</h2>
<p><strong>Usuario:</strong> {user_name}</p>
<p><strong>Email:</strong> {user_email}</p>
<p><strong>Método de pago:</strong> {method_label}</p>
{phone_line}
<p>Por favor contactar al usuario para gestionar el pago.</p>"""
    return await send_resend_email(
        to=admin_email,
        subject=f"Solicitud de upgrade — {user_name}",
        html_body=html,
    )


async def send_purchase_request(
    admin_email: str,
    user_email: str,
    user_name: str,
    plan_key: str,
    billing_cycle: str,
    method: str,
    phone: str | None = None,
    note: str | None = None,
    correlation_id: str = "",
) -> dict:
    """Notify the admin that a user wants to purchase a plan (manual flow)."""
    method_label = {"sinpe": "SINPE Móvil", "whatsapp": "WhatsApp", "email": "Correo electrónico"}.get(
        method, method
    )
    phone_line = f"<p><strong>Teléfono (SINPE):</strong> {phone}</p>" if phone else ""
    note_line = f"<p><strong>Nota:</strong> {note}</p>" if note else ""
    html = f"""<h2>Solicitud de compra de plan</h2>
<p><strong>Usuario:</strong> {user_name}</p>
<p><strong>Email:</strong> {user_email}</p>
<p><strong>Plan:</strong> {plan_key}</p>
<p><strong>Ciclo:</strong> {billing_cycle}</p>
<p><strong>Método de pago:</strong> {method_label}</p>
{phone_line}
{note_line}
<p><strong>Correlation ID:</strong> {correlation_id}</p>
<p>Contactar al usuario para gestionar el pago (SINPE / WhatsApp) y activar el plan desde el panel de administración.</p>"""
    return await send_resend_email(
        to=admin_email,
        subject=f"Solicitud de compra — {user_name} ({plan_key})",
        html_body=html,
    )


async def send_topup_request(
    admin_email: str,
    user_email: str,
    user_name: str,
    pack_credits: int,
    price_usd: float,
    method: str,
    phone: str | None = None,
    note: str | None = None,
    correlation_id: str = "",
) -> dict:
    """Notify the admin that a user wants to top up credits (manual flow)."""
    method_label = {
        "sinpe": "SINPE Móvil",
        "whatsapp": "WhatsApp",
        "email": "Correo electrónico",
    }.get(method, method)
    phone_line = f"<p><strong>Teléfono (SINPE):</strong> {phone}</p>" if phone else ""
    note_line = f"<p><strong>Nota:</strong> {note}</p>" if note else ""
    contact_line = (
        "<p>Contactar al usuario para gestionar el pago (SINPE / WhatsApp) "
        "y aprobar el top-up desde el panel de administración.</p>"
    )
    html = f"""<h2>Solicitud de top-up de créditos</h2>
<p><strong>Usuario:</strong> {user_name}</p>
<p><strong>Email:</strong> {user_email}</p>
<p><strong>Paquete:</strong> {pack_credits} créditos (${price_usd:.2f})</p>
<p><strong>Método de pago:</strong> {method_label}</p>
{phone_line}
{note_line}
<p><strong>Correlation ID:</strong> {correlation_id}</p>
{contact_line}"""
    return await send_resend_email(
        to=admin_email,
        subject=f"Solicitud de top-up — {user_name}",
        html_body=html,
    )


async def send_prorated_upgrade_request(
    admin_email: str,
    user_email: str,
    user_name: str,
    plan_from: str,
    plan_to: str,
    amount_due: float,
    method: str,
    phone: str | None = None,
    note: str | None = None,
    correlation_id: str = "",
) -> dict:
    """Notify the admin that a user wants a prorated upgrade (manual flow)."""
    method_label = {
        "sinpe": "SINPE Móvil",
        "whatsapp": "WhatsApp",
        "email": "Correo electrónico",
    }.get(method, method)
    phone_line = f"<p><strong>Teléfono (SINPE):</strong> {phone}</p>" if phone else ""
    note_line = f"<p><strong>Nota:</strong> {note}</p>" if note else ""
    html = f"""<h2>Solicitud de upgrade prorrateado</h2>
<p><strong>Usuario:</strong> {user_name}</p>
<p><strong>Email:</strong> {user_email}</p>
<p><strong>De:</strong> {plan_from} → <strong>A:</strong> {plan_to}</p>
<p><strong>Monto prorrateado:</strong> ${amount_due:.2f}</p>
<p><strong>Método de pago:</strong> {method_label}</p>
{phone_line}
{note_line}
<p><strong>Correlation ID:</strong> {correlation_id}</p>
<p>Contactar al usuario y activar el nuevo plan desde el panel (price_paid = monto prorrateado).</p>"""
    return await send_resend_email(
        to=admin_email,
        subject=f"Solicitud de upgrade — {user_name}",
        html_body=html,
    )


async def send_refund_request(
    admin_email: str,
    user_email: str,
    user_name: str,
    plan_key: str,
    usage_in_period: int,
    correlation_id: str = "",
) -> dict:
    """Notify the admin that a user wants a refund (manual flow)."""
    html = f"""<h2>Solicitud de reembolso</h2>
<p><strong>Usuario:</strong> {user_name}</p>
<p><strong>Email:</strong> {user_email}</p>
<p><strong>Plan:</strong> {plan_key}</p>
<p><strong>Uso del periodo:</strong> {usage_in_period} créditos</p>
<p><strong>Correlation ID:</strong> {correlation_id}</p>
<p>Contactar al usuario para gestionar el reembolso (SINPE / WhatsApp) y aprobarlo desde el panel de administración.</p>"""
    return await send_resend_email(
        to=admin_email,
        subject=f"Solicitud de reembolso — {user_name}",
        html_body=html,
    )


async def send_no_provider_notification(
    admin_email: str,
) -> dict:
    """Notify the admin that no AI provider key has been configured.

    This is a system-level alert: the orchestrator has no stored API key
    and is falling back to the .env configuration (or has no provider at all).
    """
    html = """<h2>⚠️ Alerta: Sin proveedor de IA configurado</h2>
<p><strong>El sistema no tiene una clave API de proveedor de IA configurada.</strong></p>
<p>Las funcionalidades de inteligencia artificial (ranking, generación de CV,
adaptación, entrevistas) pueden no estar disponibles para los usuarios.</p>
<p><strong>Acción requerida:</strong> Configura un proveedor de IA desde el panel
 de administración en <code>/admin/providers</code>.</p>
<hr>
<p><em>Este es un correo automático del sistema. Si ya configuraste el
 proveedor, puedes ignorar este mensaje.</em></p>"""
    return await send_resend_email(
        to=admin_email,
        subject="⚠️ Alerta: Sin proveedor de IA configurado — Acción requerida",
        html_body=html,
    )


async def send_donation_notification(
    admin_email: str,
    user_email: str,
    user_name: str,
    amount: str,
    method: str,
    phone: str | None = None,
) -> dict:
    """Notify the admin about a donation.

    Args:
        admin_email: Admin email to notify.
        user_email: User's email address.
        user_name: User's full name.
        amount: Donation amount description.
        method: "sinpe" or "email".
        phone: Costa Rican phone number (required for SINPE).
    """
    method_label = "SINPE Móvil" if method == "sinpe" else "Correo electrónico"
    phone_line = f"<p><strong>Teléfono (SINPE):</strong> {phone}</p>" if phone else ""
    html = f"""<h2>Notificación de donación</h2>
<p><strong>Usuario:</strong> {user_name}</p>
<p><strong>Email:</strong> {user_email}</p>
<p><strong>Monto:</strong> {amount}</p>
<p><strong>Método:</strong> {method_label}</p>
{phone_line}
<p>Gracias por tu apoyo.</p>"""
    return await send_resend_email(
        to=admin_email,
        subject=f"Donación recibida — {user_name}",
        html_body=html,
    )
