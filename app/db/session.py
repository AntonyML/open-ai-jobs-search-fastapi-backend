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
    pool_size=5,        # Base pool connections — Postgres caps at 15
    max_overflow=3,     # Max overflow so total never exceeds Postgres limit (5+3=8)
    pool_pre_ping=True,
    pool_recycle=300,   # Recycle connections every 5 min to prevent stale conns
    # If using Transaction Pooler, uncomment:
    # connect_args={"statement_cache_size": 0},
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:
    """Yield an async database session (FastAPI dependency).

    The session is guaranteed to be closed and returned to the pool
    on every code path — normal completion, endpoint exception,
    or generator cleanup (``GeneratorExit``).

    We manage the session lifecycle **without** ``async with`` so that
    ``BaseException`` (which includes ``GeneratorExit`` fired when
    FastAPI calls ``aclose()`` on this generator) is always caught,
    ensuring rollback + close happen in all cases.
    """
    session = async_session_factory()
    try:
        yield session
        await session.commit()
    except (Exception, GeneratorExit):
        await session.rollback()
        raise
    finally:
        await session.close()