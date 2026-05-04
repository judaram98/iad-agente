# agent/db.py — Motor de base de datos: engine, sesión y dependency para FastAPI
#
# Único punto de verdad para el engine async y Base declarativa.
# Todos los modelos deben importar Base desde aquí.
# inicializar_db() vive en agent/memory.py, que importa todos los modelos.

import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from dotenv import load_dotenv

load_dotenv()


def _resolver_database_url() -> str:
    """Ajusta el dialecto de DATABASE_URL para SQLAlchemy async."""
    url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./agentkit.db")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("sqlite://") and not url.startswith("sqlite+aiosqlite://"):
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


DATABASE_URL = _resolver_database_url()
ES_POSTGRES = DATABASE_URL.startswith("postgresql")

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    **({"pool_size": 5, "max_overflow": 10} if ES_POSTGRES else {}),
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session():
    """
    FastAPI dependency: provee una AsyncSession con commit/rollback automático.

    Uso:
        @app.get("/...")
        async def handler(db: AsyncSession = Depends(get_session)):
            ...
    """
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
