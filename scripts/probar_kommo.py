#!/usr/bin/env python3
# scripts/probar_kommo.py — Imprime la estructura de pipelines de Kommo
#
# Uso:
#   .venv/bin/python scripts/probar_kommo.py
#
# Muestra todos los pipelines y sus etapas con sus IDs.
# Úsalo para descubrir el KOMMO_PIPELINE_ID y los status_id de cada columna.

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.kommo import listPipelines, KommoError


def _fmt_id(n: int, width: int = 10) -> str:
    return f"[id: {n}]".ljust(width + 8)


async def main():
    print()
    print("=" * 60)
    print("  Kommo — Estructura de Pipelines y Etapas")
    print("=" * 60)

    try:
        pipelines = await listPipelines()
    except KommoError as e:
        print(f"\n❌ Error al conectar con Kommo: {e}")
        print("Verifica KOMMO_SUBDOMAIN y KOMMO_ACCESS_TOKEN en tu .env\n")
        sys.exit(1)

    if not pipelines:
        print("\n⚠️  No se encontraron pipelines en esta cuenta.\n")
        return

    for pipeline in pipelines:
        pid = pipeline.get("id")
        nombre = pipeline.get("name", "Sin nombre")
        activo = "" if pipeline.get("is_archive") else " ✓"
        print(f"\n📊 PIPELINE{activo}: {nombre}")
        print(f"   {_fmt_id(pid)}")
        print(f"   KOMMO_PIPELINE_ID={pid}")
        print()

        statuses = pipeline.get("_embedded", {}).get("statuses", [])
        statuses_ordenados = sorted(statuses, key=lambda s: s.get("sort", 0))

        for status in statuses_ordenados:
            sid = status.get("id")
            snombre = status.get("name", "Sin nombre")
            tipo = status.get("type", 0)

            # Tipos especiales de Kommo
            if tipo == 142:
                etiqueta = "🏆 GANADO"
            elif tipo == 143:
                etiqueta = "❌ PERDIDO"
            else:
                etiqueta = "  ○"

            print(f"   {etiqueta}  {snombre}")
            print(f"         status_id = {sid}")

    print()
    print("=" * 60)
    print("  Copia el KOMMO_PIPELINE_ID y los status_id que necesites")
    print("  en tu .env y en las variables de Railway.")
    print("=" * 60)
    print()


if __name__ == "__main__":
    asyncio.run(main())
