# agent/memory.py — Memoria de conversaciones y leads con SQLite

import os
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Text, DateTime, select, Integer
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./agentkit.db")

if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Mensaje(Base):
    """Historial de conversaciones por número de teléfono."""
    __tablename__ = "mensajes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Lead(Base):
    """Registro de prospectos para seguimiento."""
    __tablename__ = "leads"

    telefono: Mapped[str] = mapped_column(String(50), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(100), nullable=True)
    estado: Mapped[str] = mapped_column(String(30), default="nuevo")
    # Estados: nuevo | contactado | interesado | calificado | en_proceso | cerrado | descartado
    ultimo_contacto: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    seguimientos_enviados: Mapped[int] = mapped_column(Integer, default=0)
    notas: Mapped[str] = mapped_column(Text, nullable=True)
    creado: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


async def inicializar_db():
    """Crea las tablas si no existen."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def guardar_mensaje(telefono: str, role: str, content: str):
    """Guarda un mensaje en el historial de conversación."""
    async with async_session() as session:
        session.add(Mensaje(
            telefono=telefono,
            role=role,
            content=content,
            timestamp=datetime.utcnow(),
        ))
        await session.commit()


async def obtener_historial(telefono: str, limite: int = 20) -> list[dict]:
    """Recupera los últimos N mensajes de una conversación en orden cronológico."""
    async with async_session() as session:
        query = (
            select(Mensaje)
            .where(Mensaje.telefono == telefono)
            .order_by(Mensaje.timestamp.desc())
            .limit(limite)
        )
        result = await session.execute(query)
        mensajes = list(reversed(result.scalars().all()))
        return [{"role": m.role, "content": m.content} for m in mensajes]


async def limpiar_historial(telefono: str):
    """Borra todo el historial de una conversación."""
    async with async_session() as session:
        result = await session.execute(
            select(Mensaje).where(Mensaje.telefono == telefono)
        )
        for msg in result.scalars().all():
            await session.delete(msg)
        await session.commit()


# ── Gestión de leads ──────────────────────────────────────────────────────────

async def registrar_o_actualizar_lead(telefono: str, nombre: str = None, estado: str = None, notas: str = None):
    """Crea un lead nuevo o actualiza el existente."""
    async with async_session() as session:
        result = await session.execute(select(Lead).where(Lead.telefono == telefono))
        lead = result.scalar_one_or_none()

        if lead is None:
            lead = Lead(
                telefono=telefono,
                nombre=nombre,
                estado=estado or "nuevo",
                notas=notas,
                ultimo_contacto=datetime.utcnow(),
            )
            session.add(lead)
        else:
            lead.ultimo_contacto = datetime.utcnow()
            if nombre:
                lead.nombre = nombre
            if estado:
                lead.estado = estado
            if notas:
                lead.notas = (lead.notas or "") + f"\n{notas}"

        await session.commit()


async def obtener_leads_para_seguimiento(dias_sin_contacto: int = 3) -> list[Lead]:
    """Retorna leads que no han sido contactados en N días y no están cerrados/descartados."""
    from datetime import timedelta
    limite = datetime.utcnow() - timedelta(days=dias_sin_contacto)

    async with async_session() as session:
        query = (
            select(Lead)
            .where(Lead.ultimo_contacto < limite)
            .where(Lead.estado.in_(["contactado", "interesado", "calificado"]))
            .where(Lead.seguimientos_enviados < 5)
        )
        result = await session.execute(query)
        return result.scalars().all()


async def incrementar_seguimiento(telefono: str):
    """Incrementa el contador de seguimientos enviados y actualiza la fecha."""
    async with async_session() as session:
        result = await session.execute(select(Lead).where(Lead.telefono == telefono))
        lead = result.scalar_one_or_none()
        if lead:
            lead.seguimientos_enviados += 1
            lead.ultimo_contacto = datetime.utcnow()
            await session.commit()
