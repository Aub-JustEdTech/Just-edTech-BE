"""
Database connection and session management.
"""

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

_connect_args: dict = {}
if settings.POSTGRES_SSLMODE:
    # asyncpg requires ssl via connect_args, not a query-string parameter.
    _connect_args["ssl"] = True

async_engine = create_async_engine(
    settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://"),
    pool_pre_ping=True,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_recycle=300,
    connect_args=_connect_args,
)

AsyncSessionLocal = sessionmaker(
    async_engine, class_=AsyncSession, expire_on_commit=False
)

Base = declarative_base()


async def get_session() -> AsyncSession:
    """Dependency to get database session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def create_tables():
    """Create database tables using the ORM models' metadata"""
    from app.models.base import Base as ModelsBase  # noqa: WPS433

    async with async_engine.begin() as conn:
        await conn.run_sync(ModelsBase.metadata.create_all)
