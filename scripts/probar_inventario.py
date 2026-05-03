#!/usr/bin/env python
# scripts/probar_inventario.py — Prueba interactiva del módulo de inventario
#
# Uso:
#   python scripts/probar_inventario.py
#   python scripts/probar_inventario.py --zona "Puerto Vallarta" --max 1500000
#   python scripts/probar_inventario.py --tipo depa --rec 2
#   python scripts/probar_inventario.py --recargar   → limpia el caché y vuelve a bajar

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

from services.inventario import consultar_inventario, recargar_cache


def _fmt_precio(p) -> str:
    try:
        return f"${int(float(p)):,} MXN"
    except (TypeError, ValueError):
        return str(p) if p else "—"


def _imprimir_item(item: dict, idx: int, label: str = "") -> None:
    tag = f"[{label}] " if label else ""
    print(f"\n  {idx}. {tag}{item.get('nombre', 'Sin nombre')}")
    print(f"     Zona:      {item.get('zona', '—')}")
    print(f"     Tipo:      {item.get('tipo', '—')}")
    print(f"     Precio:    {_fmt_precio(item.get('precio_mxn'))}")
    if item.get("recamaras"):
        print(f"     Recámaras: {item['recamaras']}")
    if item.get("m2"):
        print(f"     M²:        {item['m2']}")
    if item.get("descripcion"):
        print(f"     Notas:     {item['descripcion'][:80]}")
    if item.get("diferencia"):
        print(f"     ⚠ Diff:    {item['diferencia']}")


async def main():
    parser = argparse.ArgumentParser(description="Probar consulta de inventario")
    parser.add_argument("--zona",       type=str,   help="Ciudad o zona de interés")
    parser.add_argument("--min",        type=float, help="Presupuesto mínimo en MXN")
    parser.add_argument("--max",        type=float, help="Presupuesto máximo en MXN")
    parser.add_argument("--tipo",       type=str,   help="Tipo de propiedad (depa, casa, lote…)")
    parser.add_argument("--rec",        type=int,   help="Número de recámaras")
    parser.add_argument("--recargar",   action="store_true", help="Fuerza recarga del caché")
    parser.add_argument("--json",       action="store_true", help="Imprime JSON crudo en lugar de formato legible")
    args = parser.parse_args()

    if args.recargar:
        await recargar_cache()
        print("Caché borrado. Recargando inventario…\n")

    # Si no se pasan filtros, usa una consulta de muestra
    sin_filtros = not any([args.zona, args.min, args.max, args.tipo, args.rec])

    resultado = await consultar_inventario(
        zona=args.zona,
        presupuesto_min=args.min,
        presupuesto_max=args.max,
        tipo=args.tipo,
        recamaras=args.rec,
    )

    if args.json:
        print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))
        return

    print()
    print("═" * 55)
    print("   Resultado de consulta de inventario")
    print("═" * 55)

    if not resultado.get("success"):
        print(f"\n  ERROR: {resultado.get('error', 'desconocido')}")
        return

    cache_age = resultado.get("cache_age_s", 0)
    cache_info = "recién descargado" if cache_age < 5 else f"caché de {int(cache_age)}s"
    print(f"\n  Inventario: {cache_info}")

    if sin_filtros:
        print("  Filtros:    ninguno (mostrando muestra del inventario)")
    else:
        filtros = []
        if args.zona:  filtros.append(f"zona={args.zona}")
        if args.min:   filtros.append(f"min=${args.min:,.0f}")
        if args.max:   filtros.append(f"max=${args.max:,.0f}")
        if args.tipo:  filtros.append(f"tipo={args.tipo}")
        if args.rec:   filtros.append(f"recámaras={args.rec}")
        print(f"  Filtros:    {' | '.join(filtros)}")

    total = resultado.get("total_matches", 0)
    matches = resultado.get("matches", [])
    casi = resultado.get("casi_matches", [])

    print(f"\n  {resultado.get('mensaje', '')}")

    if matches:
        print(f"\n  ── Matches exactos ({len(matches)} de {total}) ──")
        for i, item in enumerate(matches, 1):
            _imprimir_item(item, i)
    elif casi:
        print(f"\n  ── Casi-matches ({len(casi)}) ──")
        for i, item in enumerate(casi, 1):
            _imprimir_item(item, i, label="~")
    else:
        print("\n  No se encontraron propiedades.")

    print()


if __name__ == "__main__":
    asyncio.run(main())
