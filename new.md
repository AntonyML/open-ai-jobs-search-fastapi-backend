# 🛠️ Código Completo de Integración: Pases + Créditos + Lemon Squeezy

---

## PARTE 1: BACKEND (FastAPI — Fly.io)

---

### 1.1 Variables de Entorno

```bash
# .env — Agregar estas variables

# ── Lemon Squeezy ──
LEMONSQUEEZY_API_KEY=ls_1a2b3c4d5e6f7g8h9i
LEMONSQUEEZY_STORE_ID=123456
LEMONSQUEEZY_WEBHOOK_SECRET=whsec_abcdef1234567890

# Variant IDs (los obtienes del dashboard de LS)
LS_VARIANT_STARTER=111111
LS_VARIANT_PRO=222222
LS_VARIANT_ULTIMATE=333333
LS_VARIANT_TOPUP_10=333334
LS_VARIANT_TOPUP_25=333335
LS_VARIANT_TOPUP_50=333336
LS_VARIANT_DONATION=444444

# ── IA Interna (TU API key) ──
INTERNAL_ANTHROPIC_API_KEY=sk-ant-api03-...

# ── Frontend URL (para redirect) ──
FRONTEND_URL=https://tu-app.pages.dev
```

```python
# app/core/config.py — Agregar a Settings

class Settings(BaseSettings):
    # ... existentes ...

    # Lemon Squeezy
    LEMONSQUEEZY_API_KEY: str = ""
    LEMONSQUEEZY_STORE_ID: str = ""
    LEMONSQUEEZY_WEBHOOK_SECRET: str = ""

    LS_VARIANT_STARTER: str = ""
    LS_VARIANT_PRO: str = ""
    LS_VARIANT_ULTIMATE: str = ""
    LS_VARIANT_TOPUP_10: str = ""
    LS_VARIANT_TOPUP_25: str = ""
    LS_VARIANT_TOPUP_50: str = ""
    LS_VARIANT_DONATION: str = ""

    # IA Interna
    INTERNAL_ANTHROPIC_API_KEY: str = ""

    # Frontend
    FRONTEND_URL: str = "http://localhost:3000"
```

---

### 1.2 Modelos SQLAlchemy (Nuevas Tablas)

```python
# app/db/models.py — Agregar al final del archivo existente

import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, Text,
    ForeignKey, Index, CheckConstraint
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db.session import Base


class UserPass(Base):
    """Pase de acceso con créditos de IA."""
    __tablename__ = "user_passes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Lemon Squeezy
    ls_order_id = Column(String, unique=True, nullable=True, index=True)  # ID de orden en LS
    ls_variant_id = Column(String, nullable=True)
    
    # Tipo de pase
    pass_type = Column(String, nullable=False)  # 'starter' | 'pro' | 'ultimate' | 'topup'
    
    # Créditos
    credits_total = Column(Integer, nullable=False)
    credits_used = Column(Integer, nullable=False, default=0)
    credits_remaining = Column(Integer, nullable=False)
    
    # Vigencia
    activated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    
    # Metadata
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relaciones
    user = relationship("User", back_populates="passes")
    credit_transactions = relationship("CreditTransaction", back_populates="user_pass")

    __table_args__ = (
        CheckConstraint("credits_remaining >= 0", name="ck_credits_non_negative"),
        CheckConstraint("credits_used >= 0", name="ck_credits_used_non_negative"),
        Index("ix_user_passes_active", "user_id", "is_active", "expires_at"),
    )

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def days_remaining(self) -> int:
        delta = self.expires_at - datetime.now(timezone.utc)
        return max(0, delta.days)


class CreditTransaction(Base):
    """Ledger de transacciones de créditos (inmutable)."""
    __tablename__ = "credit_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    pass_id = Column(UUID(as_uuid=True), ForeignKey("user_passes.id", ondelete="SET NULL"), nullable=True)
    
    # Tipo de transacción
    action = Column(String, nullable=False)  # 'rank', 'apply', 'interview', 'purchase', 'topup', 'refund', etc.
    credits_cost = Column(Integer, nullable=False)  # Negativo = consumo, Positivo = adición
    
    # Metadata
    description = Column(Text, nullable=True)
    model_used = Column(String, nullable=True)  # 'claude-haiku-4-5', 'claude-sonnet-4-6', etc.
    tokens_input = Column(Integer, nullable=True)
    tokens_output = Column(Integer, nullable=True)
    cost_usd = Column(Integer, nullable=True)  # Costo real en centavos de USD
    
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relaciones
    user_pass = relationship("UserPass", back_populates="credit_transactions")

    __table_args__ = (
        Index("ix_credit_txn_user_date", "user_id", "created_at"),
    )


# ── Modificar modelo User existente ──
# En la clase User, agregar:

# user_passes = relationship("UserPass", back_populates="user", cascade="all, delete-orphan")
# active_pass_id = Column(UUID(as_uuid=True), ForeignKey("user_passes.id"), nullable=True)
```

---

### 1.3 Migración Alembic

```python
# alembic/versions/xxxx_add_billing_tables.py

"""add billing tables (user_passes, credit_transactions)

Revision ID: a1b2c3d4e5f6
Revises: previous_revision
Create Date: 2026-07-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'a1b2c3d4e5f6'
down_revision = None  # ← cambiar al último revision ID
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── user_passes ──
    op.create_table(
        'user_passes',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('ls_order_id', sa.String(), nullable=True),
        sa.Column('ls_variant_id', sa.String(), nullable=True),
        sa.Column('pass_type', sa.String(), nullable=False),
        sa.Column('credits_total', sa.Integer(), nullable=False),
        sa.Column('credits_used', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('credits_remaining', sa.Integer(), nullable=False),
        sa.Column('activated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()')),
        sa.CheckConstraint('credits_remaining >= 0', name='ck_credits_non_negative'),
        sa.CheckConstraint('credits_used >= 0', name='ck_credits_used_non_negative'),
    )
    op.create_index('ix_user_passes_user_id', 'user_passes', ['user_id'])
    op.create_index('ix_user_passes_ls_order_id', 'user_passes', ['ls_order_id'], unique=True)
    op.create_index('ix_user_passes_active', 'user_passes', ['user_id', 'is_active', 'expires_at'])

    # ── credit_transactions ──
    op.create_table(
        'credit_transactions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('pass_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('user_passes.id', ondelete='SET NULL'), nullable=True),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('credits_cost', sa.Integer(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('model_used', sa.String(), nullable=True),
        sa.Column('tokens_input', sa.Integer(), nullable=True),
        sa.Column('tokens_output', sa.Integer(), nullable=True),
        sa.Column('cost_usd', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()')),
    )
    op.create_index('ix_credit_txn_user_id', 'credit_transactions', ['user_id'])
    op.create_index('ix_credit_txn_user_date', 'credit_transactions', ['user_id', 'created_at'])

    # ── Modificar users ──
    op.add_column('users', sa.Column('active_pass_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('fk_users_active_pass', 'users', 'user_passes', ['active_pass_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint('fk_users_active_pass', 'users', type_='foreignkey')
    op.drop_column('users', 'active_pass_id')
    op.drop_table('credit_transactions')
    op.drop_table('user_passes')
```

---

### 1.4 Schemas Pydantic

```python
# app/schemas/billing.py

from __future__ import annotations
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field
from uuid import UUID


# ── Enums ──

class PassType(str, Enum):
    STARTER = "starter"
    PRO = "pro"
    ULTIMATE = "ultimate"
    TOPUP = "topup"


class CreditAction(str, Enum):
    RANK_SINGLE = "rank_single"
    RANK_BATCH = "rank_batch"
    APPLY = "apply"
    MOCK_INTERVIEW = "mock_interview"
    UPSKILL = "upskill"
    EXPAND = "expand"
    FIT_CALIBRATION = "fit_calibration"
    VERIFICATION = "verification"
    PURCHASE = "purchase"
    TOPUP = "topup"
    REFUND = "refund"
    BONUS = "bonus"


# ── Passes ──

class PassConfig(BaseModel):
    """Configuración de un tipo de pase."""
    pass_type: PassType
    variant_id: str
    price_usd: float
    credits: int
    duration_days: int
    name: str
    description: str


class UserPassOut(BaseModel):
    """Respuesta: pase del usuario."""
    id: UUID
    pass_type: PassType
    credits_total: int
    credits_used: int
    credits_remaining: int
    activated_at: datetime
    expires_at: datetime
    is_active: bool
    is_expired: bool
    days_remaining: int

    class Config:
        from_attributes = True


class UserPassStatus(BaseModel):
    """Estado completo de billing del usuario."""
    has_active_pass: bool
    active_pass: UserPassOut | None = None
    tier: str  # 'free' | 'premium'
    credits_remaining: int
    uses_own_api_key: bool
    total_purchases: int
    total_credits_earned: int
    total_credits_used: int


# ── Credit Transactions ──

class CreditTransactionOut(BaseModel):
    """Respuesta: transacción de créditos."""
    id: UUID
    action: str
    credits_cost: int
    description: str | None
    model_used: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class CreditBalance(BaseModel):
    """Balance de créditos."""
    credits_remaining: int
    credits_used: int
    credits_total: int
    pass_type: PassType | None
    expires_at: datetime | None
    days_remaining: int


# ── Checkout ──

class CreateCheckoutRequest(BaseModel):
    """Request: crear checkout de Lemon Squeezy."""
    variant_id: str = Field(..., description="ID del variant en Lemon Squeezy")


class CreateCheckoutResponse(BaseModel):
    """Respuesta: URL del checkout."""
    checkout_url: str
    variant_id: str


# ── Webhook ──

class WebhookMeta(BaseModel):
    event_name: str
    custom_data: dict | None = None


class WebhookOrderAttributes(BaseModel):
    identifier: str | None = None
    order_number: int | None = None
    total: float | None = None
    currency: str | None = None
    status: str | None = None


class WebhookOrderData(BaseModel):
    id: str
    type: str
    attributes: WebhookOrderAttributes


class WebhookPayload(BaseModel):
    meta: WebhookMeta
    data: WebhookOrderData


# ── Portal ──

class PortalUrlResponse(BaseModel):
    portal_url: str | None = None
    has_orders: bool = False


# ── Catálogo de productos (para frontend) ──

class ProductCatalogItem(BaseModel):
    variant_id: str
    pass_type: PassType
    name: str
    price_usd: float
    credits: int
    duration_days: int
    description: str
    popular: bool = False


class ProductCatalog(BaseModel):
    passes: list[ProductCatalogItem]
    topups: list[ProductCatalogItem]
    donation_variant_id: str
```

---

### 1.5 Servicio de Créditos

```python
# app/services/credits.py

"""
Servicio de créditos: consumo, balance, validación.
Cada acción del pipeline consume créditos según la tabla de costos.
Las acciones deterministas (scrape, ATS, salary) son GRATIS (0 créditos).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserPass, CreditTransaction, User
from app.schemas.billing import CreditAction, PassType

logger = logging.getLogger(__name__)

# ── Tabla de costos de créditos por acción ──
# Las acciones deterministas NO están aquí (son gratis).

CREDIT_COSTS: dict[str, int] = {
    "rank_single":       1,   # Rank de 1 job
    "rank_batch_10":     7,   # Rank de hasta 10 jobs (batch)
    "rank_batch_30":    18,   # Rank de hasta 30 jobs (batch)
    "apply":             8,   # CV + Cover Letter (pipeline drafter-reviewer-revise)
    "mock_interview":    5,   # Sesión de mock interview (10 preguntas)
    "upskill":           3,   # Análisis de gaps (4-pass)
    "expand":            2,   # Enriquecimiento de perfil
    "fit_calibration":   2,   # Calibración de fit
    "verification":      1,   # Verification checklist (LLM check)
}

# Acciones gratuitas (deterministas, sin LLM)
FREE_ACTIONS: set[str] = {
    "scrape",
    "ats_check",
    "salary_lookup",
    "keyword_extraction",
    "skill_lint",
    "pdf_verify",
    "content_guard",
}

# ── Configuración de pases ──

PASS_CONFIGS: dict[str, dict] = {
    "starter": {
        "credits": 10,
        "duration_days": 7,
        "price_usd": 14.99,
        "name": "Starter Pass",
        "description": "7 days + 10 AI credits. Ideal for 1-2 applications.",
    },
    "pro": {
        "credits": 30,
        "duration_days": 14,
        "price_usd": 34.99,
        "name": "Pro Pass",
        "description": "14 days + 30 AI credits. For active job search, 3-5 applications.",
    },
    "ultimate": {
        "credits": 60,
        "duration_days": 30,
        "price_usd": 59.99,
        "name": "Ultimate Pass",
        "description": "30 days + 60 AI credits. Intensive search, 7-10 applications.",
    },
}

TOPUP_CONFIGS: dict[str, dict] = {
    "topup_10":  {"credits": 10, "price_usd": 9.99},
    "topup_25":  {"credits": 25, "price_usd": 19.99},
    "topup_50":  {"credits": 50, "price_usd": 34.99},
}


class InsufficientCreditsError(Exception):
    """El usuario no tiene créditos suficientes."""
    def __init__(self, action: str, required: int, available: int):
        self.action = action
        self.required = required
        self.available = available
        super().__init__(
            f"Créditos insuficientes para '{action}': "
            f"necesitas {required}, tienes {available}. "
            f"Compra más créditos o configura tu propia API key."
        )


class NoActivePassError(Exception):
    """El usuario no tiene un pase activo."""
    def __init__(self):
        super().__init__(
            "No tienes un pase activo. "
            "Compra un pase o configura tu propia API key en Settings > Providers."
        )


class PassExpiredError(Exception):
    """El pase del usuario expiró."""
    def __init__(self, expired_days: int):
        super().__init__(
            f"Tu pase expiró hace {expired_days} día(s). "
            f"Compra un nuevo pase para seguir usando la IA incluida."
        )


async def get_active_pass(user_id: str, db: AsyncSession) -> UserPass | None:
    """Obtiene el pase activo del usuario (no expirado, con créditos)."""
    now = datetime.now(timezone.utc)
    stmt = (
        select(UserPass)
        .where(
            UserPass.user_id == user_id,
            UserPass.is_active == True,
            UserPass.expires_at > now,
            UserPass.credits_remaining > 0,
        )
        .order_by(UserPass.expires_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_any_pass(user_id: str, db: AsyncSession) -> UserPass | None:
    """Obtiene cualquier pase del usuario (incluso expirado o sin créditos)."""
    stmt = (
        select(UserPass)
        .where(UserPass.user_id == user_id)
        .order_by(UserPass.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def check_credits(
    user_id: str,
    action: str,
    db: AsyncSession,
) -> tuple[bool, UserPass | None]:
    """
    Verifica si el usuario puede ejecutar una acción.
    
    Retorna:
        (True, pass)  → Puede ejecutar con IA interna (consume créditos)
        (False, None) → No tiene créditos → debe usar su propia API key
    """
    # 1. ¿Es una acción gratuita (determinista)?
    if action in FREE_ACTIONS:
        return True, None  # Gratis, no necesita pase

    # 2. ¿Cuántos créditos cuesta?
    cost = CREDIT_COSTS.get(action)
    if cost is None:
        logger.warning(f"Acción '{action}' no tiene costo definido. Tratando como gratis.")
        return True, None

    # 3. ¿Tiene pase activo con créditos suficientes?
    active_pass = await get_active_pass(user_id, db)
    if active_pass and active_pass.credits_remaining >= cost:
        return True, active_pass

    # 4. No tiene créditos suficientes
    return False, active_pass


async def consume_credits(
    user_id: str,
    action: str,
    db: AsyncSession,
    model_used: str | None = None,
    tokens_input: int | None = None,
    tokens_output: int | None = None,
    cost_usd_cents: int | None = None,
    description: str | None = None,
) -> UserPass:
    """
    Consume créditos del pase activo del usuario.
    Lanza InsufficientCreditsError si no hay suficientes.
    Registra la transacción en el ledger.
    """
    cost = CREDIT_COSTS.get(action, 0)
    if cost == 0:
        raise ValueError(f"Action '{action}' is free, should not call consume_credits.")

    active_pass = await get_active_pass(user_id, db)

    if not active_pass:
        # ¿Tiene un pase expirado?
        any_pass = await get_any_pass(user_id, db)
        if any_pass and any_pass.is_expired:
            days_expired = (datetime.now(timezone.utc) - any_pass.expires_at).days
            raise PassExpiredError(days_expired)
        raise NoActivePassError()

    if active_pass.credits_remaining < cost:
        raise InsufficientCreditsError(action, cost, active_pass.credits_remaining)

    # ── Consumir ──
    active_pass.credits_used += cost
    active_pass.credits_remaining -= cost
    active_pass.updated_at = datetime.now(timezone.utc)

    # ── Registrar en ledger ──
    txn = CreditTransaction(
        user_id=user_id,
        pass_id=active_pass.id,
        action=action,
        credits_cost=-cost,
        description=description or f"{action}: consumed {cost} credits",
        model_used=model_used,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        cost_usd=cost_usd_cents,
    )
    db.add(txn)
    await db.flush()

    logger.info(
        f"Credits consumed: user={user_id}, action={action}, "
        f"cost={cost}, remaining={active_pass.credits_remaining}"
    )

    return active_pass


async def add_credits(
    user_id: str,
    credits: int,
    action: str,
    db: AsyncSession,
    pass_id: UUID | None = None,
    description: str | None = None,
) -> CreditTransaction:
    """Agrega créditos (compra, top-up, bonus, reembolso)."""
    txn = CreditTransaction(
        user_id=user_id,
        pass_id=pass_id,
        action=action,
        credits_cost=credits,  # Positivo = adición
        description=description or f"{action}: added {credits} credits",
    )
    db.add(txn)
    await db.flush()

    logger.info(f"Credits added: user={user_id}, credits={credits}, action={action}")
    return txn


async def get_credit_balance(user_id: str, db: AsyncSession) -> dict:
    """Obtiene el balance de créditos del usuario."""
    active_pass = await get_active_pass(user_id, db)

    # Totales históricos
    stmt_earned = (
        select(func.coalesce(func.sum(CreditTransaction.credits_cost), 0))
        .where(
            CreditTransaction.user_id == user_id,
            CreditTransaction.credits_cost > 0,
        )
    )
    stmt_used = (
        select(func.coalesce(func.sum(func.abs(CreditTransaction.credits_cost)), 0))
        .where(
            CreditTransaction.user_id == user_id,
            CreditTransaction.credits_cost < 0,
        )
    )
    total_earned = (await db.execute(stmt_earned)).scalar() or 0
    total_used = (await db.execute(stmt_used)).scalar() or 0

    return {
        "has_active_pass": active_pass is not None,
        "credits_remaining": active_pass.credits_remaining if active_pass else 0,
        "credits_used": active_pass.credits_used if active_pass else 0,
        "credits_total": active_pass.credits_total if active_pass else 0,
        "pass_type": active_pass.pass_type if active_pass else None,
        "expires_at": active_pass.expires_at if active_pass else None,
        "days_remaining": active_pass.days_remaining if active_pass else 0,
        "is_expired": active_pass.is_expired if active_pass else True,
        "total_credits_earned": total_earned,
        "total_credits_used": total_used,
    }


async def get_credit_history(
    user_id: str,
    db: AsyncSession,
    limit: int = 50,
    offset: int = 0,
) -> list[CreditTransaction]:
    """Historial de transacciones de créditos."""
    stmt = (
        select(CreditTransaction)
        .where(CreditTransaction.user_id == user_id)
        .order_by(CreditTransaction.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())
```

---

### 1.6 Servicio de Billing (Lemon Squeezy)

```python
# app/services/billing.py

"""
Servicio de billing: integración con Lemon Squeezy API.
Crea checkouts, procesa webhooks, gestiona pases.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import User, UserPass, CreditTransaction
from app.schemas.billing import (
    PassType,
    PASS_CONFIGS,
    TOPUP_CONFIGS,
)
from app.services.credits import add_credits, get_active_pass

logger = logging.getLogger(__name__)

LS_API_BASE = "https://api.lemonsqueezy.com/v1"
LS_HEADERS = {
    "Authorization": f"Bearer {settings.LEMONSQUEEZY_API_KEY}",
    "Accept": "application/vnd.api+json",
    "Content-Type": "application/vnd.api+json",
}


# ── Verificación de Webhook ──

def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """Verifica la firma HMAC-SHA256 del webhook de Lemon Squeezy."""
    expected = hmac.new(
        settings.LEMONSQUEEZY_WEBHOOK_SECRET.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


# ── Crear Checkout ──

async def create_checkout(
    user: User,
    variant_id: str,
    db: AsyncSession,
) -> str:
    """
    Crea un checkout en Lemon Squeezy y retorna la URL.
    Pasa el user_id como custom_data para vincular con el webhook.
    """
    payload = {
        "data": {
            "type": "checkouts",
            "attributes": {
                "checkout_data": {
                    "email": user.email,
                    "custom": {
                        "user_id": user.id,
                    },
                },
                "checkout_options": {
                    "embed": True,
                    "media": False,
                    "dark": False,
                },
                "product_options": {
                    "redirect_url": f"{settings.FRONTEND_URL}/dashboard?upgraded=true",
                    "receipt_button_text": "Go to Dashboard",
                    "receipt_thank_you_note": "Thank you for supporting Open AI Jobs Search!",
                    "receipt_link_url": f"{settings.FRONTEND_URL}/settings?tab=billing",
                },
            },
            "relationships": {
                "store": {
                    "data": {
                        "type": "stores",
                        "id": settings.LEMONSQUEEZY_STORE_ID,
                    }
                },
                "variant": {
                    "data": {
                        "type": "variants",
                        "id": variant_id,
                    }
                },
            },
        }
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{LS_API_BASE}/checkouts",
            headers=LS_HEADERS,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        checkout_url = data["data"]["attributes"]["url"]

    logger.info(f"Checkout created: user={user.id}, variant={variant_id}")
    return checkout_url


# ── Procesar Webhook ──

async def process_webhook(
    event_name: str,
    data: dict,
    meta: dict,
    db: AsyncSession,
) -> None:
    """
    Procesa un evento de webhook de Lemon Squeezy.
    Solo manejamos order_created y order_refunded (one-time payments).
    """
    custom_data = meta.get("custom_data", {})
    user_id = custom_data.get("user_id")

    if not user_id:
        logger.error(f"Webhook sin user_id en custom_data. Event: {event_name}")
        return

    # Verificar que el usuario existe
    user = await db.get(User, user_id)
    if not user:
        logger.error(f"Webhook para usuario inexistente: {user_id}")
        return

    if event_name == "order_created":
        await _handle_order_created(user, data, db)
    elif event_name == "order_refunded":
        await _handle_order_refunded(user, data, db)
    else:
        logger.info(f"Webhook ignorado: {event_name}")


async def _handle_order_created(user: User, data: dict, db: AsyncSession) -> None:
    """
    order_created: El usuario compró un pase o top-up.
    Extrae el variant_id, determina el tipo, crea el pase y agrega créditos.
    """
    order_id = data.get("id", "")
    attributes = data.get("attributes", {})
    order_number = attributes.get("order_number")
    total = attributes.get("total")
    currency = attributes.get("currency", "USD")

    # Obtener el variant_id del primer order_item
    # (LS incluye order_items en el payload del webhook)
    order_items = data.get("relationships", {}).get("order-items", {}).get("data", [])
    variant_id = None
    if order_items:
        # El variant_id viene en el order_item
        variant_id = order_items[0].get("id")  # Simplificado

    # Mapear variant_id → tipo de pase
    pass_info = _resolve_variant(variant_id, total)
    if not pass_info:
        logger.error(f"Variant desconocido: {variant_id}, total: {total}")
        return

    pass_type = pass_info["pass_type"]
    credits = pass_info["credits"]
    duration_days = pass_info.get("duration_days", 0)

    # Verificar si ya existe esta orden (idempotencia)
    existing = await db.execute(
        select(UserPass).where(UserPass.ls_order_id == str(order_id))
    )
    if existing.scalar_one_or_none():
        logger.warning(f"Orden duplicada ignorada: {order_id}")
        return

    now = datetime.now(timezone.utc)

    if pass_type == "topup":
        # Top-up: agregar créditos al pase activo existente
        active_pass = await get_active_pass(user.id, db)
        if active_pass:
            active_pass.credits_total += credits
            active_pass.credits_remaining += credits
            active_pass.updated_at = now

            await add_credits(
                user_id=user.id,
                credits=credits,
                action="topup",
                db=db,
                pass_id=active_pass.id,
                description=f"Top-up: +{credits} credits (order #{order_number})",
            )
            logger.info(f"Top-up applied: user={user.id}, +{credits} credits")
        else:
            # No tiene pase activo: crear uno con duración extendida
            logger.warning(f"Top-up sin pase activo para user={user.id}. Creando pase de 14 días.")
            new_pass = UserPass(
                user_id=user.id,
                ls_order_id=str(order_id),
                ls_variant_id=variant_id,
                pass_type="topup",
                credits_total=credits,
                credits_remaining=credits,
                activated_at=now,
                expires_at=now + timedelta(days=14),
                is_active=True,
            )
            db.add(new_pass)
            await db.flush()

            await add_credits(
                user_id=user.id,
                credits=credits,
                action="topup",
                db=db,
                pass_id=new_pass.id,
                description=f"Top-up (new pass): +{credits} credits",
            )

    else:
        # Pase nuevo (starter, pro, ultimate)
        new_pass = UserPass(
            user_id=user.id,
            ls_order_id=str(order_id),
            ls_variant_id=variant_id,
            pass_type=pass_type,
            credits_total=credits,
            credits_remaining=credits,
            activated_at=now,
            expires_at=now + timedelta(days=duration_days),
            is_active=True,
        )
        db.add(new_pass)
        await db.flush()

        # Actualizar usuario
        user.tier = "premium"
        user.active_pass_id = new_pass.id
        user.updated_at = now

        await add_credits(
            user_id=user.id,
            credits=credits,
            action="purchase",
            db=db,
            pass_id=new_pass.id,
            description=f"{pass_info['name']}: +{credits} credits, {duration_days} days",
        )

        logger.info(
            f"Pass activated: user={user.id}, type={pass_type}, "
            f"credits={credits}, expires={new_pass.expires_at}"
        )

    await db.commit()


async def _handle_order_refunded(user: User, data: dict, db: AsyncSession) -> None:
    """
    order_refunded: Reembolso. Desactivar pase y revertir tier.
    """
    order_id = data.get("id", "")

    # Buscar el pase asociado
    result = await db.execute(
        select(UserPass).where(UserPass.ls_order_id == str(order_id))
    )
    user_pass = result.scalar_one_or_none()

    if user_pass:
        user_pass.is_active = False
        user_pass.credits_remaining = 0
        user_pass.updated_at = datetime.now(timezone.utc)

        # Registrar reembolso en ledger
        await add_credits(
            user_id=user.id,
            credits=-user_pass.credits_total,  # Negativo = reversa
            action="refund",
            db=db,
            pass_id=user_pass.id,
            description=f"Refund: order #{order_id}",
        )

    # Verificar si tiene otro pase activo
    active = await get_active_pass(user.id, db)
    if not active:
        user.tier = "free"
        user.active_pass_id = None

    user.updated_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info(f"Refund processed: user={user.id}, order={order_id}")


def _resolve_variant(variant_id: str | None, total: float | None) -> dict | None:
    """Mapea un variant_id a la configuración del pase."""
    if not variant_id:
        # Fallback: resolver por precio
        if total and total <= 15:
            return {**PASS_CONFIGS["starter"], "pass_type": "starter"}
        elif total and total <= 35:
            return {**PASS_CONFIGS["pro"], "pass_type": "pro"}
        elif total and total <= 60:
            return {**PASS_CONFIGS["ultimate"], "pass_type": "ultimate"}
        return None

    # Mapeo directo por variant_id
    variant_map = {
        settings.LS_VARIANT_STARTER: {**PASS_CONFIGS["starter"], "pass_type": "starter"},
        settings.LS_VARIANT_PRO: {**PASS_CONFIGS["pro"], "pass_type": "pro"},
        settings.LS_VARIANT_ULTIMATE: {**PASS_CONFIGS["ultimate"], "pass_type": "ultimate"},
        settings.LS_VARIANT_TOPUP_10: {**TOPUP_CONFIGS["topup_10"], "pass_type": "topup", "duration_days": 0, "name": "Top-up 10"},
        settings.LS_VARIANT_TOPUP_25: {**TOPUP_CONFIGS["topup_25"], "pass_type": "topup", "duration_days": 0, "name": "Top-up 25"},
        settings.LS_VARIANT_TOPUP_50: {**TOPUP_CONFIGS["topup_50"], "pass_type": "topup", "duration_days": 0, "name": "Top-up 50"},
    }
    return variant_map.get(variant_id)


# ── Obtener Customer Portal URL ──

async def get_customer_portal_url(user: User) -> str | None:
    """
    Obtiene la URL del Customer Portal de Lemon Squeezy para el usuario.
    Busca la orden más reciente y extrae la URL del portal.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{LS_API_BASE}/orders",
            headers=LS_HEADERS,
            params={
                "filter[store_id]": settings.LEMONSQUEEZY_STORE_ID,
                "filter[user_email]": user.email,
                "sort": "-created_at",
                "page[size]": 1,
            },
        )
        if response.status_code != 200:
            return None

        data = response.json()
        orders = data.get("data", [])
        if not orders:
            return None

        # La URL del customer portal viene en los attributes de la orden
        order = orders[0]
        return order.get("attributes", {}).get("urls", {}).get("receipt")
```

---

### 1.7 Modelo Tiered de IA

```python
# app/services/orchestrator/model_tiers.py

"""
Modelo tiered de IA: cada acción del pipeline usa el modelo óptimo
en relación costo/calidad. Invisible para el usuario.

- Haiku 4.5  → Tareas estructuradas (ranking, upskill, expand, verification)
- Sonnet 4.6 → Tareas de generación (drafter, revise, mock interview)
- Opus 4.7   → Solo reviewer (la acción de mayor valor y calidad)

Batch API (50% descuento) para ranking (asíncrono, no tiempo real).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelTier:
    """Configuración de un modelo para una acción específica."""
    provider: str
    model: str
    cost_input_per_mtok: float   # $/1M input tokens
    cost_output_per_mtok: float  # $/1M output tokens
    batch_eligible: bool = False  # ¿Puede usar Batch API (50% descuento)?
    max_output_tokens: int = 4096


# ── Registro de modelos tiered ──

MODEL_TIERS: dict[str, ModelTier] = {
    # ── RANKING (Haiku: barato, rápido, suficiente para extracción) ──
    "rank_extraction": ModelTier(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        cost_input_per_mtok=1.0,
        cost_output_per_mtok=5.0,
        batch_eligible=True,  # El ranking es asíncrono → Batch API
        max_output_tokens=2048,
    ),
    "rank_scoring": ModelTier(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        cost_input_per_mtok=1.0,
        cost_output_per_mtok=5.0,
        batch_eligible=True,
        max_output_tokens=2048,
    ),
    "rank_qualitative": ModelTier(
        provider="anthropic",
        model="claude-sonnet-4-6",
        cost_input_per_mtok=3.0,
        cost_output_per_mtok=15.0,
        batch_eligible=True,
        max_output_tokens=2048,
    ),

    # ── APPLY: DRAFTER (Sonnet: buena generación) ──
    "apply_drafter": ModelTier(
        provider="anthropic",
        model="claude-sonnet-4-6",
        cost_input_per_mtok=3.0,
        cost_output_per_mtok=15.0,
        max_output_tokens=8192,  # CV + Cover en LaTeX
    ),

    # ── APPLY: REVIEWER (Opus: máxima calidad para detectar problemas) ──
    "apply_reviewer": ModelTier(
        provider="anthropic",
        model="claude-opus-4-7",
        cost_input_per_mtok=5.0,
        cost_output_per_mtok=25.0,
        max_output_tokens=4096,
    ),

    # ── APPLY: REVISE (Sonnet: corrección guiada) ──
    "apply_revise": ModelTier(
        provider="anthropic",
        model="claude-sonnet-4-6",
        cost_input_per_mtok=3.0,
        cost_output_per_mtok=15.0,
        max_output_tokens=8192,
    ),

    # ── MOCK INTERVIEW (Sonnet: conversacional) ──
    "mock_interview": ModelTier(
        provider="anthropic",
        model="claude-sonnet-4-6",
        cost_input_per_mtok=3.0,
        cost_output_per_mtok=15.0,
        max_output_tokens=4096,
    ),

    # ── UPSKILL / EXPAND / CALIBRATION (Haiku: análisis estructurado) ──
    "upskill_analysis": ModelTier(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        cost_input_per_mtok=1.0,
        cost_output_per_mtok=5.0,
        max_output_tokens=4096,
    ),
    "expand_profile": ModelTier(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        cost_input_per_mtok=1.0,
        cost_output_per_mtok=5.0,
        max_output_tokens=2048,
    ),
    "fit_calibration": ModelTier(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        cost_input_per_mtok=1.0,
        cost_output_per_mtok=5.0,
        max_output_tokens=2048,
    ),

    # ── VERIFICATION (Haiku: check simple) ──
    "verification_llm": ModelTier(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        cost_input_per_mtok=1.0,
        cost_output_per_mtok=5.0,
        max_output_tokens=1024,
    ),
}


def get_model_tier(action: str) -> ModelTier:
    """Obtiene la configuración de modelo para una acción."""
    tier = MODEL_TIERS.get(action)
    if not tier:
        # Fallback: Sonnet para acciones no definidas
        return ModelTier(
            provider="anthropic",
            model="claude-sonnet-4-6",
            cost_input_per_mtok=3.0,
            cost_output_per_mtok=15.0,
        )
    return tier


def estimate_cost_cents(action: str, input_tokens: int, output_tokens: int) -> int:
    """Estima el costo en centavos de USD para una acción."""
    tier = get_model_tier(action)
    cost = (
        (input_tokens / 1_000_000 * tier.cost_input_per_mtok)
        + (output_tokens / 1_000_000 * tier.cost_output_per_mtok)
    )
    return int(cost * 100)  # Centavos
```

---

### 1.8 Router de Billing

```python
# app/api/v1/billing.py

"""
Router de billing: checkout, webhooks, estado de suscripción, portal.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user
from app.db.models import User
from app.schemas.billing import (
    CreateCheckoutRequest,
    CreateCheckoutResponse,
    UserPassOut,
    UserPassStatus,
    CreditTransactionOut,
    CreditBalance,
    PortalUrlResponse,
    ProductCatalog,
    ProductCatalogItem,
    PassType,
)
from app.services import billing as billing_service
from app.services import credits as credits_service
from app.services.credits import PASS_CONFIGS, TOPUP_CONFIGS, CREDIT_COSTS, FREE_ACTIONS
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


# ── Catálogo de productos (para el frontend) ──

@router.get("/catalog", response_model=ProductCatalog)
async def get_catalog():
    """Retorna el catálogo de productos para mostrar en el frontend."""
    passes = [
        ProductCatalogItem(
            variant_id=getattr(settings, f"LS_VARIANT_{pt.upper()}"),
            pass_type=PassType(pt),
            name=cfg["name"],
            price_usd=cfg["price_usd"],
            credits=cfg["credits"],
            duration_days=cfg["duration_days"],
            description=cfg["description"],
            popular=(pt == "pro"),
        )
        for pt, cfg in PASS_CONFIGS.items()
    ]

    topups = [
        ProductCatalogItem(
            variant_id=getattr(settings, f"LS_VARIANT_TOPUP_{credits}"),
            pass_type=PassType.TOPUP,
            name=f"+{credits} Credits",
            price_usd=cfg["price_usd"],
            credits=cfg["credits"],
            duration_days=0,
            description=f"Add {credits} AI credits to your active pass.",
        )
        for credits, cfg in [
            ("10", TOPUP_CONFIGS["topup_10"]),
            ("25", TOPUP_CONFIGS["topup_25"]),
            ("50", TOPUP_CONFIGS["topup_50"]),
        ]
    ]

    return ProductCatalog(
        passes=passes,
        topups=topups,
        donation_variant_id=settings.LS_VARIANT_DONATION,
    )


# ── Crear Checkout ──

@router.post("/create-checkout", response_model=CreateCheckoutResponse)
async def create_checkout(
    body: CreateCheckoutRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Crea un checkout de Lemon Squeezy.
    El frontend abre esta URL como overlay (lemon.js) o redirect.
    """
    try:
        checkout_url = await billing_service.create_checkout(
            user=current_user,
            variant_id=body.variant_id,
            db=db,
        )
        return CreateCheckoutResponse(
            checkout_url=checkout_url,
            variant_id=body.variant_id,
        )
    except Exception as e:
        logger.error(f"Error creating checkout: {e}")
        raise HTTPException(502, "Error creating checkout. Please try again.")


# ── Webhook de Lemon Squeezy ──

@router.post("/webhook")
async def billing_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Recibe webhooks de Lemon Squeezy.
    Solo acepta order_created y order_refunded.
    Verifica firma HMAC-SHA256.
    """
    raw_body = await request.body()

    # 1. Verificar firma
    signature = request.headers.get("X-Signature", "")
    if not billing_service.verify_webhook_signature(raw_body, signature):
        logger.warning("Webhook con firma inválida")
        raise HTTPException(401, "Invalid webhook signature")

    # 2. Parsear payload
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON payload")

    meta = payload.get("meta", {})
    data = payload.get("data", {})
    event_name = meta.get("event_name", "")

    logger.info(f"Webhook received: {event_name}")

    # 3. Procesar
    try:
        await billing_service.process_webhook(
            event_name=event_name,
            data=data,
            meta=meta,
            db=db,
        )
    except Exception as e:
        logger.error(f"Error processing webhook {event_name}: {e}", exc_info=True)
        # Retornar 200 igual para que LS no reintente infinitamente
        # (el error se loguea y se puede investigar)

    return Response(status_code=200, content="OK")


# ── Estado de billing del usuario ──

@router.get("/status", response_model=UserPassStatus)
async def get_billing_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retorna el estado completo de billing del usuario."""
    balance = await credits_service.get_credit_balance(current_user.id, db)

    active_pass = await credits_service.get_active_pass(current_user.id, db)
    pass_out = None
    if active_pass:
        pass_out = UserPassOut(
            id=active_pass.id,
            pass_type=PassType(active_pass.pass_type),
            credits_total=active_pass.credits_total,
            credits_used=active_pass.credits_used,
            credits_remaining=active_pass.credits_remaining,
            activated_at=active_pass.activated_at,
            expires_at=active_pass.expires_at,
            is_active=active_pass.is_active,
            is_expired=active_pass.is_expired,
            days_remaining=active_pass.days_remaining,
        )

    # ¿El usuario tiene su propia API key configurada?
    from app.services.provider_credentials import get_user_providers
    providers = await get_user_providers(current_user.id, db)
    uses_own_key = len(providers) > 0

    # Total de compras
    from sqlalchemy import select, func
    from app.db.models import UserPass
    total_purchases = (await db.execute(
        select(func.count()).select_from(UserPass).where(
            UserPass.user_id == current_user.id,
            UserPass.pass_type != "topup",
        )
    )).scalar() or 0

    return UserPassStatus(
        has_active_pass=balance["has_active_pass"],
        active_pass=pass_out,
        tier=current_user.tier,
        credits_remaining=balance["credits_remaining"],
        uses_own_api_key=uses_own_key,
        total_purchases=total_purchases,
        total_credits_earned=balance["total_credits_earned"],
        total_credits_used=balance["total_credits_used"],
    )


# ── Balance de créditos ──

@router.get("/credits", response_model=CreditBalance)
async def get_credits(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Balance de créditos del usuario."""
    balance = await credits_service.get_credit_balance(current_user.id, db)
    return CreditBalance(**balance)


# ── Historial de créditos ──

@router.get("/credits/history", response_model=list[CreditTransactionOut])
async def get_credit_history(
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Historial de transacciones de créditos."""
    transactions = await credits_service.get_credit_history(
        current_user.id, db, limit=limit, offset=offset
    )
    return [CreditTransactionOut.model_validate(t) for t in transactions]


# ── Tabla de costos (para mostrar en UI) ──

@router.get("/credit-costs")
async def get_credit_costs():
    """Retorna la tabla de costos de créditos por acción."""
    return {
        "costs": CREDIT_COSTS,
        "free_actions": list(FREE_ACTIONS),
    }


# ── Customer Portal ──

@router.get("/portal", response_model=PortalUrlResponse)
async def get_portal(
    current_user: User = Depends(get_current_user),
):
    """URL del Customer Portal de Lemon Squeezy."""
    portal_url = await billing_service.get_customer_portal_url(current_user)
    return PortalUrlResponse(
        portal_url=portal_url,
        has_orders=portal_url is not None,
    )
```

---

### 1.9 Registrar el Router

```python
# app/api/v1/__init__.py — Agregar

from app.api.v1.billing import router as billing_router

# En la función que registra routers:
api_router.include_router(billing_router)
```

---

### 1.10 Modificar Servicios Existentes

```python
# app/services/tiers.py — Modificar para integrar con créditos

"""
Límites de uso por tier.
- Free: límites bajos + requiere API key propia
- Premium (pase activo): límites altos + IA incluida (créditos)
"""

from __future__ import annotations

from app.services.credits import get_active_pass, CREDIT_COSTS, FREE_ACTIONS
from sqlalchemy.ext.asyncio import AsyncSession


# Límites del FREE tier (sin pase activo)
FREE_LIMITS = {
    "scrape_runs_per_day": 3,
    "rank_jobs_per_run": 10,
    "apply_applications_total": 1,
    "mock_interview_sessions": 1,
    "upskill_analyses": 1,
    "expand_runs": 0,
}

# Límites del PREMIUM tier (con pase activo)
PREMIUM_LIMITS = {
    "scrape_runs_per_day": 20,
    "rank_jobs_per_run": 50,
    "apply_applications_total": 100,  # Limitado por créditos
    "mock_interview_sessions": 20,    # Limitado por créditos
    "upskill_analyses": 10,           # Limitado por créditos
    "expand_runs": 5,                 # Limitado por créditos
}


async def get_user_limits(user_id: str, tier: str, db: AsyncSession) -> dict:
    """Retorna los límites del usuario según su tier y pase activo."""
    if tier == "premium":
        active_pass = await get_active_pass(user_id, db)
        if active_pass:
            return {**PREMIUM_LIMITS, "has_credits": True, "credits_remaining": active_pass.credits_remaining}
    
    return {**FREE_LIMITS, "has_credits": False, "credits_remaining": 0}


async def can_perform_action(
    user_id: str,
    action: str,
    tier: str,
    db: AsyncSession,
) -> tuple[bool, str]:
    """
    Verifica si el usuario puede realizar una acción.
    
    Retorna:
        (True, "") → Puede realizarla
        (False, reason) → No puede, con razón
    """
    # Acciones deterministas: siempre permitidas
    if action in FREE_ACTIONS:
        return True, ""

    # Verificar créditos (para usuarios premium)
    from app.services.credits import check_credits
    has_credits, active_pass = await check_credits(user_id, action, db)

    if has_credits and active_pass:
        return True, ""  # Tiene créditos → puede usar IA interna

    # No tiene créditos → ¿tiene su propia API key?
    from app.services.provider_credentials import get_active_provider
    provider = await get_active_provider(user_id, db)
    if provider:
        return True, ""  # Puede usar su propia API key (free tier)

    # No tiene créditos NI API key
    return False, (
        "No tienes créditos de IA ni una API key configurada. "
        "Compra un pase para usar la IA incluida, o configura tu propio "
        "proveedor de IA en Settings > Providers."
    )
```

---

### 1.11 Modificar el LLMOrchestrator

```python
# app/services/orchestrator/executor.py — Modificar la función de ejecución

"""
Ejecutor del orquestador: decide qué modelo usar según el tier del usuario.
- Pase activo con créditos → modelo tiered interno (TU API key)
- Sin créditos + API key propia → proveedor del usuario (free tier)
- Sin nada → error
"""

from __future__ import annotations

import logging
from app.core.config import settings
from app.services.credits import consume_credits, check_credits, CREDIT_COSTS
from app.services.orchestrator.model_tiers import get_model_tier, estimate_cost_cents
from app.services.tiers import can_perform_action

logger = logging.getLogger(__name__)


async def execute_llm_action(
    user_id: str,
    action: str,
    prompt: str,
    system_prompt: str,
    db,  # AsyncSession
    llm_call_fn,  # Función que hace la llamada LLM
    **kwargs,
) -> dict:
    """
    Ejecuta una acción LLM con la lógica de créditos y modelo tiered.
    
    Flujo:
    1. ¿El usuario puede realizar esta acción? (créditos o API key propia)
    2. Si tiene créditos → usar modelo tiered interno + consumir créditos
    3. Si no tiene créditos pero tiene API key → usar su proveedor
    4. Ejecutar la llamada LLM
    5. Registrar consumo en ledger
    """
    
    # 1. Verificar permisos
    can_do, reason = await can_perform_action(user_id, action, "premium", db)
    if not can_do:
        raise PermissionError(reason)

    # 2. Decidir modelo
    has_credits, active_pass = await check_credits(user_id, action, db)

    if has_credits and active_pass:
        # ── USUARIO DE PAGO: modelo tiered interno ──
        tier = get_model_tier(action)
        
        provider_config = {
            "provider": "anthropic",
            "model": tier.model,
            "api_key": settings.INTERNAL_ANTHROPIC_API_KEY,
            "max_tokens": tier.max_output_tokens,
        }
        
        # ¿Usar Batch API?
        use_batch = tier.batch_eligible and kwargs.get("allow_batch", False)
        if use_batch:
            provider_config["use_batch"] = True

    else:
        # ── FREE TIER: API key del usuario ──
        from app.services.provider_credentials import get_active_provider
        user_provider = await get_active_provider(user_id, db)
        if not user_provider:
            raise PermissionError(
                "No credits or API key. Buy a pass or configure your provider."
            )
        
        from app.services.provider_credentials import decrypt_key
        provider_config = {
            "provider": user_provider.provider,
            "model": user_provider.model,
            "api_key": decrypt_key(user_provider.encrypted_key),
            "max_tokens": kwargs.get("max_tokens", 4096),
        }

    # 3. Ejecutar llamada LLM
    result = await llm_call_fn(
        prompt=prompt,
        system_prompt=system_prompt,
        **provider_config,
        **kwargs,
    )

    # 4. Si usó créditos, consumir y registrar
    if has_credits and active_pass:
        input_tokens = result.get("usage", {}).get("input_tokens", 0)
        output_tokens = result.get("usage", {}).get("output_tokens", 0)
        cost_cents = estimate_cost_cents(action, input_tokens, output_tokens)

        await consume_credits(
            user_id=user_id,
            action=action,
            db=db,
            model_used=provider_config["model"],
            tokens_input=input_tokens,
            tokens_output=output_tokens,
            cost_usd_cents=cost_cents,
            description=f"{action} via {provider_config['model']}",
        )
        await db.commit()

    return result
```

---

### 1.12 Modificar Endpoints Existentes (Ejemplo: Rank)

```python
# app/api/v1/rank.py — Modificar el endpoint POST /rank/

@router.post("/")
async def execute_ranking(
    body: RankRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ejecuta el ranking de jobs."""
    
    # ── NUEVO: Verificar créditos/permisos ──
    from app.services.tiers import can_perform_action
    action = "rank_batch_10" if body.top_n and body.top_n > 1 else "rank_single"
    
    can_do, reason = await can_perform_action(
        current_user.id, action, current_user.tier, db
    )
    if not can_do:
        raise HTTPException(
            status_code=402,  # Payment Required
            detail=reason,
        )
    
    # ── Lógica existente de ranking (sin cambios) ──
    # ...
```

---

### 1.13 Modificar Email Service (Resend)

```python
# app/services/email.py — Agregar templates de billing

BILLING_TEMPLATES = {
    "pass_activated": {
        "subject": "🎉 Your {pass_name} is active!",
        "body": """
Hi {name},

Your **{pass_name}** is now active!

📋 **What you got:**
• {credits} AI credits
• {duration_days} days of access
• AI-powered CV, cover letters, interview prep & more

⏰ **Expires:** {expires_at}

💡 **Tip:** You can also use your own API key anytime in Settings > Providers.

Good luck with your job search! 🚀
— Open AI Jobs Search
""",
    },
    "credits_low": {
        "subject": "⚡ Running low on credits ({remaining} left)",
        "body": """
Hi {name},

You have **{remaining} AI credits** remaining on your {pass_name}.

When they run out, you can:
• **Buy more credits** (Top-up from $9.99)
• **Use your own API key** (Settings > Providers)

Don't let your job search stall! 💪
— Open AI Jobs Search
""",
    },
    "pass_expired": {
        "subject": "⏰ Your {pass_name} has expired",
        "body": """
Hi {name},

Your **{pass_name}** expired on {expired_at}.

Your data is safe — you can still:
• **Buy a new pass** to continue with AI included
• **Use your own API key** (free tier)

We hope you found your dream job! 🌟
— Open AI Jobs Search
""",
    },
    "refund_processed": {
        "subject": "💳 Refund processed",
        "body": """
Hi {name},

Your refund for order #{order_number} has been processed.
Your access has been reverted to the free tier.

If you have questions, reply to this email.
— Open AI Jobs Search
""",
    },
}


async def send_billing_email(
    to_email: str,
    template_name: str,
    variables: dict,
) -> None:
    """Envía un email de billing usando Resend."""
    template = BILLING_TEMPLATES.get(template_name)
    if not template:
        return

    subject = template["subject"].format(**variables)
    body = template["body"].format(**variables)

    # Usar la integración existente de Resend
    await send_email(to=to_email, subject=subject, html_body=body)
```

---

## PARTE 2: FRONTEND (Next.js — Cloudflare Workers)

---

### 2.1 Cargar lemon.js

```tsx
// app/[locale]/layout.tsx — Agregar Script

import Script from 'next/script'

export default function LocaleLayout({ children, params }: Props) {
  return (
    <html lang={params.locale}>
      <head>
        {/* Lemon Squeezy checkout overlay */}
        <Script
          src="https://app.lemonsqueezy.com/js/lemon.js"
          strategy="afterInteractive"
        />
      </head>
      <body>
        <NextIntlClientProvider locale={params.locale}>
          <AccessibilityProvider>
            <SoundProvider>
              {children}
            </SoundProvider>
          </AccessibilityProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  )
}
```

---

### 2.2 Tipos de Billing

```typescript
// types/billing.ts

export type PassType = 'starter' | 'pro' | 'ultimate' | 'topup'

export interface ProductCatalogItem {
  variant_id: string
  pass_type: PassType
  name: string
  price_usd: number
  credits: number
  duration_days: number
  description: string
  popular: boolean
}

export interface ProductCatalog {
  passes: ProductCatalogItem[]
  topups: ProductCatalogItem[]
  donation_variant_id: string
}

export interface UserPassOut {
  id: string
  pass_type: PassType
  credits_total: number
  credits_used: number
  credits_remaining: number
  activated_at: string
  expires_at: string
  is_active: boolean
  is_expired: boolean
  days_remaining: number
}

export interface UserPassStatus {
  has_active_pass: boolean
  active_pass: UserPassOut | null
  tier: 'free' | 'premium'
  credits_remaining: number
  uses_own_api_key: boolean
  total_purchases: number
  total_credits_earned: number
  total_credits_used: number
}

export interface CreditTransaction {
  id: string
  action: string
  credits_cost: number
  description: string | null
  model_used: string | null
  created_at: string
}

export interface CreditCosts {
  costs: Record<string, number>
  free_actions: string[]
}
```

---

### 2.3 API Client para Billing

```typescript
// lib/billing.ts

import { apiFetch } from './api'
import type {
  ProductCatalog,
  UserPassStatus,
  CreditTransaction,
  CreditCosts,
} from '@/types/billing'

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

/** Obtener catálogo de productos */
export async function fetchCatalog(): Promise<ProductCatalog> {
  const res = await apiFetch('/api/v1/billing/catalog')
  if (!res.ok) throw new Error('Failed to fetch catalog')
  return res.json()
}

/** Crear checkout de Lemon Squeezy */
export async function createCheckout(variantId: string): Promise<string> {
  const res = await apiFetch('/api/v1/billing/create-checkout', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ variant_id: variantId }),
  })
  if (!res.ok) throw new Error('Failed to create checkout')
  const data = await res.json()
  return data.checkout_url
}

/** Obtener estado de billing del usuario */
export async function fetchBillingStatus(): Promise<UserPassStatus> {
  const res = await apiFetch('/api/v1/billing/status')
  if (!res.ok) throw new Error('Failed to fetch billing status')
  return res.json()
}

/** Obtener historial de créditos */
export async function fetchCreditHistory(
  limit = 50,
  offset = 0
): Promise<CreditTransaction[]> {
  const res = await apiFetch(
    `/api/v1/billing/credits/history?limit=${limit}&offset=${offset}`
  )
  if (!res.ok) throw new Error('Failed to fetch credit history')
  return res.json()
}

/** Obtener tabla de costos de créditos */
export async function fetchCreditCosts(): Promise<CreditCosts> {
  const res = await apiFetch('/api/v1/billing/credit-costs')
  if (!res.ok) throw new Error('Failed to fetch credit costs')
  return res.json()
}

/** Obtener URL del Customer Portal */
export async function fetchPortalUrl(): Promise<string | null> {
  const res = await apiFetch('/api/v1/billing/portal')
  if (!res.ok) return null
  const data = await res.json()
  return data.portal_url
}

/** Abrir checkout de Lemon Squeezy como overlay */
export function openLemonCheckout(url: string) {
  if (typeof window !== 'undefined' && window.LemonSqueezy) {
    window.LemonSqueezy.Url.Open(url)
  } else {
    // Fallback: abrir en nueva pestaña
    window.open(url, '_blank')
  }
}

// Type declaration para lemon.js
declare global {
  interface Window {
    LemonSqueezy: {
      Url: {
        Open: (url: string) => void
        Close: () => void
      }
      Setup: () => void
    }
    createLemonSqueezy: () => void
  }
}
```

---

### 2.4 Hook de Billing

```typescript
// hooks/useBilling.ts

'use client'

import { useState, useEffect, useCallback } from 'react'
import {
  fetchBillingStatus,
  fetchCatalog,
  createCheckout,
  openLemonCheckout,
} from '@/lib/billing'
import type { UserPassStatus, ProductCatalog } from '@/types/billing'

export function useBilling() {
  const [status, setStatus] = useState<UserPassStatus | null>(null)
  const [catalog, setCatalog] = useState<ProductCatalog | null>(null)
  const [loading, setLoading] = useState(true)
  const [checkoutLoading, setCheckoutLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Cargar estado y catálogo al montar
  useEffect(() => {
    async function load() {
      try {
        const [s, c] = await Promise.all([
          fetchBillingStatus(),
          fetchCatalog(),
        ])
        setStatus(s)
        setCatalog(c)
      } catch (e) {
        setError('Failed to load billing info')
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  // Inicializar lemon.js
  useEffect(() => {
    if (typeof window.createLemonSqueezy === 'function') {
      window.createLemonSqueezy()
    }
  }, [])

  // Comprar un pase o top-up
  const purchase = useCallback(async (variantId: string) => {
    setCheckoutLoading(true)
    setError(null)
    try {
      const checkoutUrl = await createCheckout(variantId)
      openLemonCheckout(checkoutUrl)
    } catch (e) {
      setError('Failed to create checkout. Please try again.')
    } finally {
      setCheckoutLoading(false)
    }
  }, [])

  // Refrescar estado (después de comprar)
  const refresh = useCallback(async () => {
    try {
      const s = await fetchBillingStatus()
      setStatus(s)
    } catch (e) {
      // Silent fail
    }
  }, [])

  // Escuchar evento de upgrade exitoso (redirect de LS)
  useEffect(() => {
    const url = new URL(window.location.href)
    if (url.searchParams.get('upgraded') === 'true') {
      refresh()
      // Limpiar URL
      url.searchParams.delete('upgraded')
      window.history.replaceState({}, '', url.toString())
    }
  }, [refresh])

  return {
    status,
    catalog,
    loading,
    checkoutLoading,
    error,
    purchase,
    refresh,
    hasActivePass: status?.has_active_pass ?? false,
    creditsRemaining: status?.credits_remaining ?? 0,
    isPremium: status?.tier === 'premium',
  }
}
```

---

### 2.5 UpgradeModal Rediseñado

```tsx
// components/UpgradeModal.tsx

'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { useBilling } from '@/hooks/useBilling'
import { cn } from '@/lib/utils'

interface UpgradeModalProps {
  isOpen: boolean
  onClose: () => void
  triggerAction?: string  // La acción que disparó el upgrade (ej: "apply")
}

export function UpgradeModal({ isOpen, onClose, triggerAction }: UpgradeModalProps) {
  const t = useTranslations('billing')
  const { catalog, purchase, checkoutLoading, status } = useBilling()
  const [selectedTab, setSelectedTab] = useState<'passes' | 'topups'>('passes')

  if (!isOpen || !catalog) return null

  const hasActivePass = status?.has_active_pass ?? false

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative z-10 w-full max-w-2xl mx-4 bg-white rounded-2xl p-8 shadow-xl">
        {/* Header */}
        <div className="text-center mb-8">
          <h2 className="text-2xl font-semibold text-[var(--color-carbon)]">
            {t('upgradeTitle')}
          </h2>
          <p className="mt-2 text-sm text-gray-500">
            {t('upgradeSubtitle')}
          </p>
          <p className="mt-1 text-xs text-gray-400">
            {t('noSubscription')} — {t('oneTimePayment')}
          </p>
        </div>

        {/* Tabs */}
        <div className="flex justify-center gap-2 mb-6">
          <button
            onClick={() => setSelectedTab('passes')}
            className={cn(
              'px-4 py-2 rounded-full text-sm font-medium transition-colors',
              selectedTab === 'passes'
                ? 'bg-[var(--color-apple-blue)] text-white'
                : 'bg-[var(--color-frost)] text-gray-600 hover:bg-gray-200'
            )}
          >
            {t('passes')}
          </button>
          {hasActivePass && (
            <button
              onClick={() => setSelectedTab('topups')}
              className={cn(
                'px-4 py-2 rounded-full text-sm font-medium transition-colors',
                selectedTab === 'topups'
                  ? 'bg-[var(--color-apple-blue)] text-white'
                  : 'bg-[var(--color-frost)] text-gray-600 hover:bg-gray-200'
              )}
            >
              {t('topups')}
            </button>
          )}
        </div>

        {/* Passes */}
        {selectedTab === 'passes' && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {catalog.passes.map((pass) => (
              <div
                key={pass.variant_id}
                className={cn(
                  'relative border rounded-xl p-5 flex flex-col',
                  pass.popular
                    ? 'border-[var(--color-apple-blue)] ring-1 ring-[var(--color-apple-blue)]'
                    : 'border-gray-200'
                )}
              >
                {pass.popular && (
                  <span className="absolute -top-3 left-1/2 -translate-x-1/2 bg-[var(--color-apple-blue)] text-white text-xs px-3 py-0.5 rounded-full">
                    {t('mostPopular')}
                  </span>
                )}

                <h3 className="font-semibold text-[var(--color-carbon)]">
                  {pass.name}
                </h3>
                <div className="mt-2">
                  <span className="text-3xl font-bold text-[var(--color-carbon)]">
                    ${pass.price_usd}
                  </span>
                  <span className="text-sm text-gray-400 ml-1">
                    {t('oneTime')}
                  </span>
                </div>

                <ul className="mt-4 space-y-2 text-sm text-gray-600 flex-1">
                  <li>⚡ {pass.credits} {t('aiCredits')}</li>
                  <li>📅 {pass.duration_days} {t('daysAccess')}</li>
                  <li>🤖 {t('aiIncluded')}</li>
                  <li>🔑 {t('ownKeyOptional')}</li>
                </ul>

                <p className="mt-3 text-xs text-gray-400">
                  {pass.description}
                </p>

                <button
                  onClick={() => purchase(pass.variant_id)}
                  disabled={checkoutLoading}
                  className={cn(
                    'mt-4 w-full py-2.5 rounded-full text-sm font-medium transition-colors',
                    pass.popular
                      ? 'bg-[var(--color-apple-blue)] text-white hover:bg-blue-700'
                      : 'bg-[var(--color-frost)] text-[var(--color-carbon)] hover:bg-gray-200'
                  )}
                >
                  {checkoutLoading ? t('processing') : t('getPass')}
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Top-ups */}
        {selectedTab === 'topups' && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {catalog.topups.map((topup) => (
              <div
                key={topup.variant_id}
                className="border border-gray-200 rounded-xl p-5 flex flex-col items-center"
              >
                <span className="text-2xl font-bold text-[var(--color-carbon)]">
                  +{topup.credits}
                </span>
                <span className="text-sm text-gray-500">{t('credits')}</span>
                <span className="mt-2 text-lg font-semibold">
                  ${topup.price_usd}
                </span>
                <button
                  onClick={() => purchase(topup.variant_id)}
                  disabled={checkoutLoading}
                  className="mt-4 w-full py-2 rounded-full text-sm font-medium bg-[var(--color-frost)] text-[var(--color-carbon)] hover:bg-gray-200 transition-colors"
                >
                  {checkoutLoading ? t('processing') : t('addCredits')}
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Free tier reminder */}
        <div className="mt-6 text-center">
          <p className="text-xs text-gray-400">
            {t('freeTierReminder')}
          </p>
        </div>

        {/* Close button */}
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-gray-400 hover:text-gray-600"
        >
          ✕
        </button>
      </div>
    </div>
  )
}
```

---

### 2.6 Widget de Créditos para el LLM Control Center

```tsx
// components/CreditWidget.tsx

'use client'

import { useBilling } from '@/hooks/useBilling'
import { cn } from '@/lib/utils'
import { useTranslations } from 'next-intl'

interface CreditWidgetProps {
  onBuyCredits: () => void  // Abre el UpgradeModal en tab topups
}

export function CreditWidget({ onBuyCredits }: CreditWidgetProps) {
  const t = useTranslations('billing')
  const { status, loading } = useBilling()

  if (loading || !status) return null

  const { active_pass, credits_remaining, uses_own_api_key } = status

  // Sin pase activo
  if (!active_pass) {
    return (
      <div className="p-3 rounded-lg bg-[var(--color-frost)] border border-gray-200">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-gray-500">
            {t('noActivePass')}
          </span>
          {uses_own_api_key && (
            <span className="text-xs text-green-600">
              🔑 {t('ownKey')}
            </span>
          )}
        </div>
        {!uses_own_api_key && (
          <button
            onClick={onBuyCredits}
            className="mt-2 w-full py-1.5 rounded-full text-xs font-medium bg-[var(--color-apple-blue)] text-white hover:bg-blue-700 transition-colors"
          >
            {t('getPass')}
          </button>
        )}
      </div>
    )
  }

  // Con pase activo
  const percentage = (credits_remaining / active_pass.credits_total) * 100
  const isLow = percentage <= 20
  const isCritical = percentage <= 10

  return (
    <div className="p-3 rounded-lg bg-[var(--color-frost)] border border-gray-200">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-gray-700 capitalize">
          🎫 {active_pass.pass_type} Pass
        </span>
        <span className="text-xs text-gray-400">
          {active_pass.days_remaining}d {t('left')}
        </span>
      </div>

      {/* Progress bar */}
      <div className="w-full h-2 bg-gray-200 rounded-full overflow-hidden">
        <div
          className={cn(
            'h-full rounded-full transition-all duration-500',
            isCritical ? 'bg-red-500' : isLow ? 'bg-yellow-500' : 'bg-[var(--color-apple-blue)]'
          )}
          style={{ width: `${percentage}%` }}
        />
      </div>

      {/* Credits count */}
      <div className="flex items-center justify-between mt-1.5">
        <span className={cn(
          'text-xs font-medium',
          isCritical ? 'text-red-600' : isLow ? 'text-yellow-600' : 'text-gray-600'
        )}>
          {credits_remaining}/{active_pass.credits_total} {t('credits')}
        </span>
        {isLow && (
          <button
            onClick={onBuyCredits}
            className="text-xs text-[var(--color-apple-blue)] hover:underline"
          >
            {t('buyMore')}
          </button>
        )}
      </div>
    </div>
  )
}
```

---

### 2.7 Integrar en LLM Control Center

```tsx
// components/LLMControlCenter.tsx — Agregar CreditWidget

import { CreditWidget } from './CreditWidget'
import { UpgradeModal } from './UpgradeModal'

export function LLMControlCenter() {
  const [showUpgrade, setShowUpgrade] = useState(false)

  return (
    <aside className="sticky top-4 w-72 space-y-4">
      {/* ── NUEVO: Widget de créditos ── */}
      <CreditWidget onBuyCredits={() => setShowUpgrade(true)} />

      {/* ── Existente: Estado del proveedor ── */}
      <ProviderStatus />

      {/* ── Existente: Cola de ejecución ── */}
      <QueueStatus />

      {/* ── Existente: Métricas ── */}
      <Metrics />

      {/* ── NUEVO: UpgradeModal ── */}
      <UpgradeModal
        isOpen={showUpgrade}
        onClose={() => setShowUpgrade(false)}
      />
    </aside>
  )
}
```

---

### 2.8 UpgradeListener Modificado

```tsx
// components/UpgradeListener.tsx

'use client'

import { useEffect, useState } from 'react'
import { UpgradeModal } from './UpgradeModal'

/**
 * Escucha eventos HTTP 402 (Payment Required) del backend.
 * Cuando el backend retorna 402, muestra el UpgradeModal.
 */
export function UpgradeListener() {
  const [showModal, setShowModal] = useState(false)
  const [triggerAction, setTriggerAction] = useState<string>()

  useEffect(() => {
    function handleUpgradeRequired(e: CustomEvent) {
      setTriggerAction(e.detail?.action)
      setShowModal(true)
    }

    window.addEventListener('upgrade:required', handleUpgradeRequired as EventListener)
    return () => {
      window.removeEventListener('upgrade:required', handleUpgradeRequired as EventListener)
    }
  }, [])

  return (
    <UpgradeModal
      isOpen={showModal}
      onClose={() => setShowModal(false)}
      triggerAction={triggerAction}
    />
  )
}
```

```typescript
// lib/api.ts — Modificar apiFetch para disparar evento en 402

export async function apiFetch(path: string, options?: RequestInit) {
  const token = getAccessToken()
  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      ...options?.headers,
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  })

  // ── NUEVO: HTTP 402 → disparar evento de upgrade ──
  if (res.status === 402) {
    const detail = await res.json().catch(() => ({}))
    window.dispatchEvent(
      new CustomEvent('upgrade:required', {
        detail: { action: detail.action, message: detail.detail },
      })
    )
  }

  return res
}
```

---

### 2.9 Settings → Tab de Billing

```tsx
// app/[locale]/(app)/settings/page.tsx — Agregar tab "Billing"

// En el array de tabs, agregar:
{
  id: 'billing',
  label: t('billing'),
  icon: CreditCard,
}

// Componente del tab:
function BillingTab() {
  const t = useTranslations('settings.billing')
  const { status, loading } = useBilling()
  const [history, setHistory] = useState<CreditTransaction[]>([])
  const [portalUrl, setPortalUrl] = useState<string | null>(null)

  useEffect(() => {
    fetchCreditHistory().then(setHistory).catch(() => {})
    fetchPortalUrl().then(setPortalUrl).catch(() => {})
  }, [])

  if (loading) return <Skeleton />

  return (
    <div className="space-y-6">
      {/* Estado actual */}
      <Card>
        <CardHeader>
          <CardTitle>{t('currentPlan')}</CardTitle>
        </CardHeader>
        <CardContent>
          {status?.active_pass ? (
            <div className="space-y-2">
              <div className="flex justify-between">
                <span className="text-sm text-gray-500">{t('plan')}</span>
                <span className="text-sm font-medium capitalize">
                  {status.active_pass.pass_type} Pass
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-sm text-gray-500">{t('credits')}</span>
                <span className="text-sm font-medium">
                  {status.credits_remaining} / {status.active_pass.credits_total}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-sm text-gray-500">{t('expires')}</span>
                <span className="text-sm font-medium">
                  {new Date(status.active_pass.expires_at).toLocaleDateString()}
                  ({status.active_pass.days_remaining}d)
                </span>
              </div>
            </div>
          ) : (
            <p className="text-sm text-gray-500">
              {t('freeTier')}
              {status?.uses_own_api_key && ` — ${t('ownKeyConfigured')}`}
            </p>
          )}
        </CardContent>
      </Card>

      {/* Customer Portal */}
      {portalUrl && (
        <Card>
          <CardHeader>
            <CardTitle>{t('managePayments')}</CardTitle>
          </CardHeader>
          <CardContent>
            <a
              href={portalUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-[var(--color-apple-blue)] hover:underline"
            >
              {t('viewReceipts')} ↗
            </a>
          </CardContent>
        </Card>
      )}

      {/* Historial de créditos */}
      <Card>
        <CardHeader>
          <CardTitle>{t('creditHistory')}</CardTitle>
        </CardHeader>
        <CardContent>
          {history.length === 0 ? (
            <p className="text-sm text-gray-400">{t('noTransactions')}</p>
          ) : (
            <div className="space-y-2">
              {history.slice(0, 20).map((txn) => (
                <div key={txn.id} className="flex justify-between items-center py-1 border-b border-gray-100 last:border-0">
                  <div>
                    <span className="text-sm text-gray-700">{txn.action}</span>
                    {txn.model_used && (
                      <span className="text-xs text-gray-400 ml-2">({txn.model_used})</span>
                    )}
                  </div>
                  <span className={cn(
                    'text-sm font-medium',
                    txn.credits_cost > 0 ? 'text-green-600' : 'text-gray-600'
                  )}>
                    {txn.credits_cost > 0 ? '+' : ''}{txn.credits_cost}
                  </span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
```

---

### 2.10 Traducciones (i18n)

```json
// messages/en.json — Agregar sección "billing"

{
  "billing": {
    "upgradeTitle": "Unlock AI-Powered Job Search",
    "upgradeSubtitle": "Get AI credits for CV generation, interview prep, and more.",
    "noSubscription": "No subscription",
    "oneTimePayment": "One-time payment. Use it when you need it.",
    "passes": "Passes",
    "topups": "Top-up Credits",
    "mostPopular": "Most Popular",
    "oneTime": "one-time",
    "aiCredits": "AI credits",
    "daysAccess": "days access",
    "aiIncluded": "AI included (Claude + GPT)",
    "ownKeyOptional": "Or use your own API key",
    "processing": "Processing...",
    "getPass": "Get Pass",
    "addCredits": "Add Credits",
    "credits": "credits",
    "freeTierReminder": "💡 Free tier: bring your own API key and use the full pipeline at no cost.",
    "noActivePass": "No active pass",
    "ownKey": "Own API key",
    "left": "left",
    "buyMore": "Buy more",
    "currentPlan": "Current Plan",
    "plan": "Plan",
    "expires": "Expires",
    "freeTier": "Free Tier",
    "ownKeyConfigured": "Own API key configured",
    "managePayments": "Payment History",
    "viewReceipts": "View receipts on Lemon Squeezy",
    "creditHistory": "Credit History",
    "noTransactions": "No transactions yet."
  }
}
```

```json
// messages/es.json — Agregar sección "billing"

{
  "billing": {
    "upgradeTitle": "Desbloquea la Búsqueda de Empleo con IA",
    "upgradeSubtitle": "Obtén créditos de IA para generar CV, preparar entrevistas y más.",
    "noSubscription": "Sin suscripción",
    "oneTimePayment": "Pago único. Úsalo cuando lo necesites.",
    "passes": "Pases",
    "topups": "Recargar Créditos",
    "mostPopular": "Más Popular",
    "oneTime": "pago único",
    "aiCredits": "créditos de IA",
    "daysAccess": "días de acceso",
    "aiIncluded": "IA incluida (Claude + GPT)",
    "ownKeyOptional": "O usa tu propia API key",
    "processing": "Procesando...",
    "getPass": "Obtener Pase",
    "addCredits": "Agregar Créditos",
    "credits": "créditos",
    "freeTierReminder": "💡 Plan gratuito: trae tu propia API key y usa todo el pipeline sin costo.",
    "noActivePass": "Sin pase activo",
    "ownKey": "API key propia",
    "left": "restantes",
    "buyMore": "Comprar más",
    "currentPlan": "Plan Actual",
    "plan": "Plan",
    "expires": "Expira",
    "freeTier": "Plan Gratuito",
    "ownKeyConfigured": "API key propia configurada",
    "managePayments": "Historial de Pagos",
    "viewReceipts": "Ver recibos en Lemon Squeezy",
    "creditHistory": "Historial de Créditos",
    "noTransactions": "Sin transacciones aún."
  }
}
```

---

## PARTE 3: CONFIGURACIÓN EN LEMON SQUEEZY (Dashboard)

### 3.1 Crear Productos

```
Dashboard → Products → New Product

Product 1: "Starter Pass"
  Price: $14.99
  Type: One-time payment
  Variant: "Starter Pass" → $14.99

Product 2: "Pro Pass"
  Price: $34.99
  Type: One-time payment
  Variant: "Pro Pass" → $34.99

Product 3: "Ultimate Pass"
  Price: $59.99
  Type: One-time payment
  Variant: "Ultimate Pass" → $59.99

Product 4: "Credit Top-Up"
  Type: One-time payment
  Variants:
    "+10 Credits" → $9.99
    "+25 Credits" → $19.99
    "+50 Credits" → $34.99

Product 5: "Donation"
  Type: One-time payment
  Price: Pay what you want (minimum $1.00)
```

### 3.2 Configurar Webhook

```
Dashboard → Settings → Webhooks → New Webhook

URL: https://tu-api.fly.dev/api/v1/billing/webhook
Events:
  ☑ order_created
  ☑ order_refunded
  ☐ (todos los demás desmarcados)

Secret: (copiar y guardar como LEMONSQUEEZY_WEBHOOK_SECRET)
```

### 3.3 Configurar Payout

```
Dashboard → Settings → Payouts

Method: Bank transfer (Costa Rica)
  - Account holder: Tu nombre
  - Bank: Tu banco en CR
  - Account number: ...
  - SWIFT: ...

O alternativamente:
Method: PayPal
  - Email: tu@email.com
```

### 3.4 Obtener IDs

```
Dashboard → Settings → API → Generate API Key
  → Copiar como LEMONSQUEEZY_API_KEY

Dashboard → Products → (cada producto) → Variants
  → Copiar cada Variant ID como LS_VARIANT_STARTER, LS_VARIANT_PRO, etc.

Dashboard → Settings → General
  → Store ID → LEMONSQUEEZY_STORE_ID
```

---

## PARTE 4: CHECKLIST DE DEPLOY

```bash
# ── 1. Backend (Fly.io) ──

# Agregar secrets
flyctl secrets set \
  LEMONSQUEEZY_API_KEY="ls_..." \
  LEMONSQUEEZY_STORE_ID="123456" \
  LEMONSQUEEZY_WEBHOOK_SECRET="whsec_..." \
  LS_VARIANT_STARTER="111111" \
  LS_VARIANT_PRO="222222" \
  LS_VARIANT_ULTIMATE="333333" \
  LS_VARIANT_TOPUP_10="333334" \
  LS_VARIANT_TOPUP_25="333335" \
  LS_VARIANT_TOPUP_50="333336" \
  LS_VARIANT_DONATION="444444" \
  INTERNAL_ANTHROPIC_API_KEY="sk-ant-..." \
  FRONTEND_URL="https://tu-app.pages.dev"

# Migración
alembic upgrade head

# Deploy
flyctl deploy

# Verificar webhook
curl -X POST https://tu-api.fly.dev/api/v1/billing/webhook \
  -H "Content-Type: application/json" \
  -d '{"test": true}'
# → Debe retornar 401 (firma inválida), no 500


# ── 2. Frontend (Cloudflare) ──

# Agregar variables en wrangler.jsonc
# NEXT_PUBLIC_API_URL = "https://tu-api.fly.dev"

pnpm build
pnpm deploy


# ── 3. Testing ──

# 1. Registrar usuario → tier = 'free'
# 2. Configurar API key propia → funciona sin créditos
# 3. Comprar Starter Pass → webhook → tier = 'premium', 10 créditos
# 4. Usar Apply → consume 8 créditos → quedan 2
# 5. Intentar otro Apply → 402 → UpgradeModal
# 6. Comprar Top-up → +10 créditos
# 7. Esperar expiración → tier = 'free'
# 8. Verificar historial de créditos en Settings > Billing
```

---

## Resumen de Archivos Creados/Modificados

| Archivo | Acción | Descripción |
|---|---|---|
| `app/core/config.py` | ✏️ Modificar | Agregar vars de LS + IA interna |
| `app/db/models.py` | ✏️ Modificar | Agregar UserPass, CreditTransaction, modificar User |
| `alembic/versions/xxxx_billing.py` | 🆕 Crear | Migración de tablas |
| `app/schemas/billing.py` | 🆕 Crear | Schemas Pydantic de billing |
| `app/services/credits.py` | 🆕 Crear | Lógica de créditos |
| `app/services/billing.py` | 🆕 Crear | Integración Lemon Squeezy |
| `app/services/orchestrator/model_tiers.py` | 🆕 Crear | Modelo tiered de IA |
| `app/services/orchestrator/executor.py` | ✏️ Modificar | Lógica de selección de modelo |
| `app/services/tiers.py` | ✏️ Modificar | Integrar con créditos |
| `app/services/email.py` | ✏️ Modificar | Templates de billing |
| `app/api/v1/billing.py` | 🆕 Crear | Router de billing |
| `app/api/v1/__init__.py` | ✏️ Modificar | Registrar router |
| `app/api/v1/rank.py` | ✏️ Modificar | Verificar créditos |
| `app/api/v1/apply.py` | ✏️ Modificar | Verificar créditos |
| `types/billing.ts` | 🆕 Crear | Tipos TypeScript |
| `lib/billing.ts` | 🆕 Crear | API client de billing |
| `hooks/useBilling.ts` | 🆕 Crear | Hook de billing |
| `components/UpgradeModal.tsx` | ✏️ Modificar | Rediseñar con pases |
| `components/CreditWidget.tsx` | 🆕 Crear | Widget de créditos |
| `components/LLMControlCenter.tsx` | ✏️ Modificar | Agregar CreditWidget |
| `components/UpgradeListener.tsx` | ✏️ Modificar | Integrar 402 |
| `lib/api.ts` | ✏️ Modificar | Manejar 402 |
| `app/[locale]/layout.tsx` | ✏️ Modificar | Cargar lemon.js |
| `app/[locale]/(app)/settings/page.tsx` | ✏️ Modificar | Tab de Billing |
| `messages/en.json` | ✏️ Modificar | Traducciones billing |
| `messages/es.json` | ✏️ Modificar | Traducciones billing |

> **Total: 12 archivos nuevos + 14 modificados.** La arquitectura existente (pipeline, orchestrator, scrapers, WebSocket, i18n) queda **intacta**. Solo se agrega la capa de billing encima.
