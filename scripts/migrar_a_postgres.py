#!/usr/bin/env python3
# scripts/migrar_a_postgres.py — Migra datos de SQLite a PostgreSQL
#
# Uso:
#   SQLITE_PATH=./agentkit.db python scripts/migrar_a_postgres.py
#
# El script es idempotente: no duplica registros si se corre dos veces.

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

SQLITE_PATH = os.getenv("SQLITE_PATH", "./agentkit.db")


async def main():
    # ── Verificar que existe la base SQLite ──────────────────────────────────
    if not os.path.exists(SQLITE_PATH):
        print(f"No se encontró {SQLITE_PATH} — nada que migrar.")
        return

    # ── Conectar a SQLite (origen) ───────────────────────────────────────────
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from sqlalchemy import select, text

    sqlite_engine = create_async_engine(
        f"sqlite+aiosqlite:///{SQLITE_PATH}", echo=False
    )
    sqlite_session = async_sessionmaker(sqlite_engine, class_=AsyncSession, expire_on_commit=False)

    # ── Conectar a PostgreSQL (destino) via memory.py ────────────────────────
    # Importar DESPUÉS de load_dotenv para que tome la DATABASE_URL de postgres
    from agent.memory import (
        inicializar_db, async_session as pg_session,
        Mensaje, Lead, DATABASE_URL,
    )

    if not DATABASE_URL.startswith("postgresql"):
        print(f"ERROR: DATABASE_URL no es PostgreSQL: {DATABASE_URL}")
        print("Configura DATABASE_URL con una URL de PostgreSQL antes de migrar.")
        sys.exit(1)

    print(f"Origen:  {SQLITE_PATH}")
    print(f"Destino: {DATABASE_URL[:40]}...")
    print()

    await inicializar_db()

    # ── Leer mensajes de SQLite ──────────────────────────────────────────────
    async with sqlite_session() as session:
        try:
            result = await session.execute(
                text("SELECT telefono, role, content, timestamp FROM mensajes ORDER BY id")
            )
            mensajes_sqlite = result.fetchall()
        except Exception:
            mensajes_sqlite = []

    print(f"Mensajes encontrados en SQLite: {len(mensajes_sqlite)}")

    # ── Insertar mensajes en PostgreSQL (idempotente por contenido+timestamp) ─
    mensajes_migrados = 0
    async with pg_session() as session:
        for row in mensajes_sqlite:
            telefono, role, content, ts_raw = row

            # Normalizar timestamp
            if isinstance(ts_raw, str):
                try:
                    ts = datetime.fromisoformat(ts_raw).replace(tzinfo=timezone.utc)
                except ValueError:
                    ts = datetime.now(timezone.utc)
            elif isinstance(ts_raw, datetime):
                ts = ts_raw.replace(tzinfo=timezone.utc) if ts_raw.tzinfo is None else ts_raw
            else:
                ts = datetime.now(timezone.utc)

            # Verificar si ya existe (idempotente)
            existe = await session.execute(
                select(Mensaje).where(
                    Mensaje.telefono == telefono,
                    Mensaje.role == role,
                    Mensaje.content == content,
                    Mensaje.timestamp == ts,
                )
            )
            if existe.scalar_one_or_none():
                continue

            session.add(Mensaje(telefono=telefono, role=role, content=content, timestamp=ts))
            mensajes_migrados += 1

        await session.commit()

    print(f"Mensajes migrados: {mensajes_migrados} (ignorados duplicados: {len(mensajes_sqlite) - mensajes_migrados})")

    # ── Leer leads de SQLite ─────────────────────────────────────────────────
    async with sqlite_session() as session:
        try:
            result = await session.execute(
                text("SELECT telefono, nombre, estado, ultimo_contacto, seguimientos_enviados, notas, creado FROM leads")
            )
            leads_sqlite = result.fetchall()
        except Exception:
            leads_sqlite = []

    print(f"Leads encontrados en SQLite: {len(leads_sqlite)}")

    def _parse_ts(val):
        if val is None:
            return datetime.now(timezone.utc)
        if isinstance(val, str):
            try:
                return datetime.fromisoformat(val).replace(tzinfo=timezone.utc)
            except ValueError:
                return datetime.now(timezone.utc)
        if isinstance(val, datetime):
            return val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val
        return datetime.now(timezone.utc)

    leads_migrados = 0
    async with pg_session() as session:
        for row in leads_sqlite:
            telefono, nombre, estado, ultimo_contacto, seguimientos, notas, creado = row

            existe = await session.execute(select(Lead).where(Lead.telefono == telefono))
            if existe.scalar_one_or_none():
                continue

            session.add(Lead(
                telefono=telefono,
                nombre=nombre,
                estado=estado or "nuevo",
                ultimo_contacto=_parse_ts(ultimo_contacto),
                seguimientos_enviados=seguimientos or 0,
                notas=notas,
                creado=_parse_ts(creado),
            ))
            leads_migrados += 1

        await session.commit()

    print(f"Leads migrados: {leads_migrados} (ignorados duplicados: {len(leads_sqlite) - leads_migrados})")
    print()
    print("Migración completada.")

    await sqlite_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
