# tests/test_local.py — Simulador de chat en terminal para IAD México

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.brain import generar_respuesta
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial, limpiar_historial
from agent.tools import calificar_interes, estado_desde_interes

TELEFONO_TEST = "test-local-001"


async def main():
    await inicializar_db()

    print()
    print("=" * 60)
    print("   AgentKit — IAD México — Test Local")
    print("   Agente: Juan Ramirez de IAD México")
    print("=" * 60)
    print()
    print("  Escribe mensajes como si fueras un prospecto inversionista.")
    print("  Comandos especiales:")
    print("    'limpiar'  — borra el historial de la conversación")
    print("    'salir'    — termina el test")
    print()
    print("-" * 60)
    print()

    while True:
        try:
            mensaje = input("Tú: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nTest finalizado.")
            break

        if not mensaje:
            continue

        if mensaje.lower() == "salir":
            print("\nTest finalizado.")
            break

        if mensaje.lower() == "limpiar":
            await limpiar_historial(TELEFONO_TEST)
            print("[Historial borrado]\n")
            continue

        # Mostrar nivel de interés detectado (útil para ajustar el prompt)
        interes = calificar_interes(mensaje)
        estado = estado_desde_interes(interes)

        historial = await obtener_historial(TELEFONO_TEST)

        print("\nJuan Ramirez (IAD México): ", end="", flush=True)
        respuesta = await generar_respuesta(mensaje, historial)
        print(respuesta)
        print(f"\n[Interés detectado: {interes} → estado: {estado}]")
        print()

        await guardar_mensaje(TELEFONO_TEST, "user", mensaje)
        await guardar_mensaje(TELEFONO_TEST, "assistant", respuesta)


if __name__ == "__main__":
    asyncio.run(main())
