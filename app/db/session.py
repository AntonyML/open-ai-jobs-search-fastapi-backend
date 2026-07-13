"""SQLAlchemy async engine and session factory.

Uses asyncpg under the hood.  Prepared-statement caching is enabled by
default (asyncpg does this automatically); if you must use a Transaction
Pooler (PgBouncer), set statement_cache_size=0 in the connect_args.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.app_env == "development",
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    # If using Transaction Pooler, uncomment:
    # connect_args={"statement_cache_size": 0},
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:
    """Yield an async database session (FastAPI dependency)."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()