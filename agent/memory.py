# agent/memory.py — Memoria de conversaciones y leads (SQLite local / PostgreSQL prod)

import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text, DateTime, select, Integer, func

from agent.db import Base, engine, async_session

logger = logging.getLogger("agentkit")


class Mensaje(Base):
    """Historial de conversaciones por número de teléfono."""
    __tablename__ = "mensajes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # server_default garantiza que Postgres ponga la fecha, no Python
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class Lead(Base):
    """Registro de prospectos para seguimiento."""
    __tablename__ = "leads"

    telefono: Mapped[str] = mapped_column(String(50), primary_key=True, nullable=False)
    nombre: Mapped[str] = mapped_column(String(100), nullable=True)
    # Estados: nuevo | contactado | interesado | calificado | en_proceso | cerrado | descartado
    estado: Mapped[str] = mapped_column(String(30), nullable=False, server_default="nuevo")
    ultimo_contacto: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    seguimientos_enviados: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    notas: Mapped[str] = mapped_column(Text, nullable=True)
    creado: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


# ── API pública — mismos nombres y firmas que antes ──────────────────────────

async def inicializar_db():
    """
    Crea las tablas si no existen (idempotente).

    IMPORTANTE: importa todos los módulos con modelos ANTES de create_all
    para que SQLAlchemy los tenga registrados en Base.metadata.
    Si añades modelos en nuevos archivos, agrégalos aquí.
    """
    import agent.memory  # noqa: Mensaje, Lead → registrados en Base.metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    tablas = sorted(Base.metadata.tables.keys())
    logger.info(f"[DB] {len(tablas)} tabla(s) listas: {tablas}")


async def guardar_mensaje(telefono: str, role: str, content: str):
    """Guarda un mensaje en el historial de conversación."""
    async with async_session() as session:
        session.add(Mensaje(
            telefono=telefono,
            role=role,
            content=content,
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


async def registrar_o_actualizar_lead(
    telefono: str,
    nombre: str = None,
    estado: str = None,
    notas: str = None,
):
    """Crea un lead nuevo o actualiza el existente."""
    async with async_session() as session:
        result = await session.execute(select(Lead).where(Lead.telefono == telefono))
        lead = result.scalar_one_or_none()

        now = datetime.now(timezone.utc)

        if lead is None:
            lead = Lead(
                telefono=telefono,
                nombre=nombre,
                estado=estado or "nuevo",
                notas=notas,
                ultimo_contacto=now,
            )
            session.add(lead)
        else:
            lead.ultimo_contacto = now
            if nombre:
                lead.nombre = nombre
            if estado:
                lead.estado = estado
            if notas:
                lead.notas = (lead.notas or "") + f"\n{notas}"

        await session.commit()


async def obtener_leads_para_seguimiento(dias_sin_contacto: int = 3) -> list[Lead]:
    """Retorna leads sin contacto reciente que no están cerrados ni descartados."""
    from datetime import timedelta
    limite = datetime.now(timezone.utc) - timedelta(days=dias_sin_contacto)

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
    """Incrementa el contador de seguimientos y actualiza la fecha de último contacto."""
    async with async_session() as session:
        result = await session.execute(select(Lead).where(Lead.telefono == telefono))
        lead = result.scalar_one_or_none()
        if lead:
            lead.seguimientos_enviados += 1
            lead.ultimo_contacto = datetime.now(timezone.utc)
            await session.commit()
