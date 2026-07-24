"""Email service using the Resend API (no external SDK needed — uses httpx).

Sends transactional emails for payment requests, donations, and admin contact.
"""

from __future__ import annotations


from app.core.settings import get_settings

from app.core.logging import get_logger
logger = get_logger(__name__)

settings = get_settings()


async def send_resend_email(
    to: str,
    subject: str,
    html_body: str,
    *,
    from_name: str = "Open Ai Jobs Search",
) -> dict:
    """Send an email via the Resend API.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        html_body: HTML body content.
        from_name: Sender display name.

    Returns:
        The JSON response from Resend.

    Raises:
        RuntimeError: If RESEND_API_KEY is not configured.
    """
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
