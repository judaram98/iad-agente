# tests/test_local.py — Simulador de chat en terminal para IAD México
#
# Modos de uso:
#   python tests/test_local.py              → chat directo (sin contexto CRM)
#   python tests/test_local.py --lead XXXX  → chat simulando un lead de Kommo real
#
# Comandos especiales dentro del chat:
#   limpiar   — borra el historial de la sesión actual
#   contexto  — muestra qué contexto se inyectaría al system prompt en este momento
#   salir     — cierra el test

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mostrar INFO para ver los tool calls en tiempo real
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(name)s | %(message)s",
)
# Silenciar los loggers muy verbosos para que no tapen el chat
for modulo in ("httpx", "httpcore", "groq", "hpack"):
    logging.getLogger(modulo).setLevel(logging.WARNING)

from agent.brain import (
    generar_respuesta, procesar_mensaje_kommo,
    construir_contexto_lead, cargar_system_prompt,
)
from agent.memory import (
    inicializar_db, guardar_mensaje, obtener_historial, limpiar_historial,
)

TELEFONO_TEST = "test-local-001"


def _leer_args() -> int | None:
    """Parsea --lead XXXX de sys.argv. Retorna lead_id int o None."""
    args = sys.argv[1:]
    if "--lead" in args:
        idx = args.index("--lead")
        try:
            return int(args[idx + 1])
        except (IndexError, ValueError):
            print("Uso: python tests/test_local.py --lead <lead_id>")
            sys.exit(1)
    return None


async def _mostrar_contexto(lead_id: int | None, historial: list[dict]) -> None:
    """Imprime el contexto que se inyectaría al system prompt."""
    if lead_id is None:
        print("\n[Contexto]: Sin contexto CRM — conversación directa\n")
        return
    try:
        from services.kommo import getLead, KommoError
        lead_data = await getLead(lead_id)
        ctx = construir_contexto_lead(lead_data, historial)
        print(f"\n[Contexto del lead #{lead_id}]:\n{ctx}\n")
    except Exception as e:
        print(f"\n[No se pudo obtener contexto del lead {lead_id}: {e}]\n")


async def main():
    lead_id = _leer_args()
    modo = f"Kommo (lead_id={lead_id})" if lead_id else "Directo (sin CRM)"

    await inicializar_db()

    # Cargar nombre del agente desde business.yaml si existe
    try:
        import yaml
        with open("config/business.yaml", "r", encoding="utf-8") as f:
            biz = yaml.safe_load(f) or {}
        nombre_agente = biz.get("agente", {}).get("nombre", "Sofía")
        nombre_inmo = biz.get("template_vars", {}).get("nombre_inmobiliaria", "la inmobiliaria")
    except Exception:
        nombre_agente = "Sofía"
        nombre_inmo = "la inmobiliaria"

    print()
    print("=" * 62)
    print(f"   Test Local — {nombre_agente} ({nombre_inmo})")
    print(f"   Modo: {modo}")
    print("=" * 62)
    print()
    print("  Escribe mensajes como si fueras el cliente.")
    print("  Comandos:")
    print("    limpiar   — borra el historial")
    print("    contexto  — muestra el contexto CRM actual")
    print("    salir     — cierra el test")
    print()
    print("  Los tool calls que haga el modelo aparecen en los logs [INFO].")
    print("-" * 62)
    print()

    # Clave de conversación: lead_id como string si existe, sino TELEFONO_TEST
    clave = str(lead_id) if lead_id else TELEFONO_TEST

    while True:
        try:
            entrada = input("Tú: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nTest finalizado.")
            break

        if not entrada:
            continue

        cmd = entrada.lower()

        if cmd == "salir":
            print("\nTest finalizado.")
            break

        if cmd == "limpiar":
            await limpiar_historial(clave)
            print("[Historial borrado]\n")
            continue

        if cmd == "contexto":
            historial = await obtener_historial(clave)
            await _mostrar_contexto(lead_id, historial)
            continue

        historial = await obtener_historial(clave)

        print(f"\n{nombre_agente}: ", end="", flush=True)

        if lead_id:
            # Modo Kommo: usa el pipeline completo con contexto del lead
            respuesta = await procesar_mensaje_kommo(lead_id, entrada, historial)
            if respuesta is None:
                print("[Lead en etapa congelada — sin respuesta]")
                print()
                continue
        else:
            # Modo directo: generar_respuesta sin contexto CRM
            respuesta = await generar_respuesta(entrada, historial)

        print(respuesta)
        print()

        await guardar_mensaje(clave, "user", entrada)
        await guardar_mensaje(clave, "assistant", respuesta)


if __name__ == "__main__":
    asyncio.run(main())
