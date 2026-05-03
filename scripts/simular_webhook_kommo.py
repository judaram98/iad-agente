#!/usr/bin/env python3
# scripts/simular_webhook_kommo.py — Simula webhooks de Kommo para pruebas locales
#
# Uso:
#   .venv/bin/python scripts/simular_webhook_kommo.py          # ambos
#   .venv/bin/python scripts/simular_webhook_kommo.py chat     # solo chat
#   .venv/bin/python scripts/simular_webhook_kommo.py lead     # solo lead
#   .venv/bin/python scripts/simular_webhook_kommo.py chat_saliente
#
# Requiere que el servidor esté corriendo:
#   .venv/bin/python -m uvicorn agent.main:app --reload --port 8000

import asyncio
import json
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from agent.config import settings

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
SECRET   = settings.KOMMO_WEBHOOK_SECRET
NOW      = int(time.time())

# ── Payloads de ejemplo ───────────────────────────────────────────────────────

PAYLOADS = {
    "chat": {
        "_desc": "Mensaje ENTRANTE del cliente (lead = 99999)",
        "endpoint": "/webhooks/kommo/chat",
        "body": {
            "account_id": 35347992,
            "time": NOW,
            "message": {
                "id": f"sim-msg-{NOW}",
                "entity_id": 99999,
                "entity_type": "lead",
                "type": "incoming",
                "origin": "whatsapp",
                "text": "Hola, me interesa el Acuario Vallarta. ¿Cuánto cuesta una acción?",
                "author": {"id": 11111, "type": "contact"},
                "created_at": NOW,
            },
        },
    },

    "chat_saliente": {
        "_desc": "Mensaje SALIENTE del agente (debe ignorarse)",
        "endpoint": "/webhooks/kommo/chat",
        "body": {
            "account_id": 35347992,
            "time": NOW,
            "message": {
                "id": f"sim-out-{NOW}",
                "entity_id": 99999,
                "entity_type": "lead",
                "type": "outgoing",
                "origin": "whatsapp",
                "text": "¡Hola! Soy Juan Ramirez de IAD México.",
                "author": {"id": 14271535, "type": "user"},
                "created_at": NOW,
            },
        },
    },

    "lead": {
        "_desc": "Lead cambió de etapa: SIN_PERFILAR → CITA_PRE (lead = 99999)",
        "endpoint": "/webhooks/kommo/lead",
        "body": {
            "account_id": 35347992,
            "time": NOW,
            "leads": {
                "status": [
                    {
                        "id": 99999,
                        "status_id": 105360867,       # CITA_PRE
                        "pipeline_id": 13652595,
                        "responsible_user_id": 14271535,
                        "old_status_id": 105360863,   # SIN_PERFILAR_CONTESTO
                        "old_pipeline_id": 13652595,
                        "last_modified_at": NOW,
                    }
                ]
            },
        },
    },

    "lead_nuevo": {
        "_desc": "Lead recién creado en Leads Entrantes",
        "endpoint": "/webhooks/kommo/lead",
        "body": {
            "account_id": 35347992,
            "time": NOW,
            "leads": {
                "add": [
                    {
                        "id": 88888,
                        "status_id": 105360767,   # LEADS_ENTRANTES
                        "pipeline_id": 13652595,
                        "responsible_user_id": 14271535,
                        "created_at": NOW,
                    }
                ]
            },
        },
    },
}


# ── Runner ────────────────────────────────────────────────────────────────────

async def simular(clave: str) -> None:
    caso = PAYLOADS[clave]
    url  = f"{BASE_URL}{caso['endpoint']}?secret={SECRET}"

    print(f"\n{'='*58}")
    print(f"  [{clave}] {caso['_desc']}")
    print(f"  URL: {url}")
    print(f"  Payload:\n{json.dumps(caso['body'], indent=4, ensure_ascii=False)}")
    print()

    async with httpx.AsyncClient(timeout=5) as client:
        try:
            t0 = time.monotonic()
            r  = await client.post(url, json=caso["body"])
            ms = round((time.monotonic() - t0) * 1000)
            icono = "✓" if r.status_code == 200 else "✗"
            print(f"  {icono} HTTP {r.status_code}  ({ms} ms)  →  {r.text}")
        except httpx.ConnectError:
            print(f"  ✗ No se pudo conectar a {BASE_URL}")
            print("    ¿Está corriendo el servidor?")
            print("    .venv/bin/python -m uvicorn agent.main:app --reload")
    print()


async def main() -> None:
    print()
    print("=" * 58)
    print("  Simulador de Webhooks Kommo — IAD México")
    print("=" * 58)

    claves_validas = list(PAYLOADS.keys())
    arg = sys.argv[1] if len(sys.argv) > 1 else "ambos"

    if arg == "ambos":
        claves = ["chat", "lead"]
    elif arg in claves_validas:
        claves = [arg]
    else:
        print(f"\n  Uso: python scripts/simular_webhook_kommo.py [{'|'.join(claves_validas)}|ambos]\n")
        return

    for clave in claves:
        await simular(clave)


if __name__ == "__main__":
    asyncio.run(main())
