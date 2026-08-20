"""Cloudflare R2 (S3-compatible) storage para archivos PDF.

Si R2 no esta configurado (credenciales vacias), todas las funciones
retornan False/None y el caller debe fallback a disco local.

Uso tipico:
    from app.services import r2_storage

    r2_storage.upload_pdf("generated_cvs/user-1/cv-id.pdf", pdf_bytes)
    url = r2_storage.generate_signed_url("generated_cvs/user-1/cv-id.pdf")
    r2_storage.delete_pdf("generated_cvs/user-1/cv-id.pdf")
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.logging import get_logger
from app.core.settings import get_settings

logger = get_logger(__name__)

# Reintento automatico de boto3 para errores transitorios de red
_BOTOCORE_CONFIG = Config(
    retries={"max_attempts": 3, "mode": "adaptive"},
    signature_version="s3v4",
    s3={"addressing_style": "path"},
)


def _r2_configured() -> bool:
    """True si las credenciales R2 estan configuradas."""
    s = get_settings()
    return bool(s.r2_account_id and s.r2_access_key_id and s.r2_secret_access_key)


def _get_client() -> Any:
    """Crear cliente boto3 S3 para R2. Se crea por llamada (no cacheado)."""
    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=f"https://{s.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=s.r2_access_key_id,
        aws_secret_access_key=s.r2_secret_access_key,
        region_name=s.r2_region,
        config=_BOTOCORE_CONFIG,
    )


def upload_pdf(key: str, data: bytes) -> None:
    """Subir un PDF al bucket R2.

    Args:
        key: Object key (ej: "generated_cvs/user-1/cv-id.pdf")
        data: Bytes del PDF

    Raises:
        RuntimeError: Si R2 no esta configurado
        ClientError: Si la subida falla despues de reintentos
    """
    if not _r2_configured():
        raise RuntimeError("R2 is not configured — cannot upload PDF")

    s = get_settings()
    client = _get_client()
    client.put_object(
        Bucket=s.r2_bucket_name,
        Key=key,
        Body=data,
        ContentType="application/pdf",
        CacheControl="private, max-age=0",  # No cache en CDN (signed URLs)
    )
    logger.info("R2 upload OK | key=%s | size=%d", key, len(data))


def generate_signed_url(key: str, expires_in: int | None = None) -> str:
    """Generar URL firmada (v4) para descargar un PDF.

    Args:
        key: Object key
        expires_in: TTL en segundos (default: r2_signed_url_ttl de settings)

    Returns:
        URL firmada que expira despues de expires_in segundos

    Raises:
        RuntimeError: Si R2 no esta configurado
        ClientError: Si la generacion falla
    """
    if not _r2_configured():
        raise RuntimeError("R2 is not configured — cannot generate signed URL")

    s = get_settings()
    ttl = expires_in or s.r2_signed_url_ttl
    client = _get_client()
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": s.r2_bucket_name, "Key": key},
        ExpiresIn=ttl,
    )
    return url


def delete_pdf(key: str) -> bool:
    """Eliminar un PDF del bucket R2. Idempotente (no falla si no existe).

    Args:
        key: Object key

    Returns:
        True si se elimino, False si no existia o hubo error
    """
    if not _r2_configured():
        return False

    s = get_settings()
    try:
        client = _get_client()
        client.delete_object(Bucket=s.r2_bucket_name, Key=key)
        logger.info("R2 delete OK | key=%s", key)
        return True
    except (ClientError, BotoCoreError):
        logger.warning("R2 delete failed | key=%s", key, exc_info=True)
        return False


def delete_user_prefix(user_id: str) -> int:
    """Eliminar todos los PDFs de un usuario (ambos scopes: cv + apply).

    Args:
        user_id: ID del usuario

    Returns:
        Numero de objetos eliminados
    """
    if not _r2_configured():
        return 0

    s = get_settings()
    client = _get_client()
    deleted = 0

    for prefix in [f"generated_cvs/{user_id}/", f"generated/{user_id}/"]:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=s.r2_bucket_name, Prefix=prefix):
            objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if objects:
                client.delete_objects(
                    Bucket=s.r2_bucket_name,
                    Delete={"Objects": objects, "Quiet": True},
                )
                deleted += len(objects)

    logger.info("R2 delete_user_prefix OK | user=%s | deleted=%d", user_id, deleted)
    return deleted


def download_to_temp(key: str) -> Path | None:
    """Descargar un PDF a un archivo temporal. El caller debe eliminar el temp.

    Args:
        key: Object key

    Returns:
        Path al archivo temporal, o None si no existe / error
    """
    if not _r2_configured():
        return None

    s = get_settings()
    client = _get_client()

    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            client.download_file(s.r2_bucket_name, key, tmp.name)
            return Path(tmp.name)
    except (ClientError, BotoCoreError):
        logger.warning("R2 download failed | key=%s", key, exc_info=True)
        return None


def object_exists(key: str) -> bool:
    """Verificar si un objeto existe en R2 (sin descargar)."""
    if not _r2_configured():
        return False

    s = get_settings()
    client = _get_client()
    try:
        client.head_object(Bucket=s.r2_bucket_name, Key=key)
        return True
    except ClientError:
        return False
