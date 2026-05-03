#!/usr/bin/env python3
# scripts/simular_webhook_kommo.py — Simula los webhooks REALES de Kommo
#
# Kommo envía application/x-www-form-urlencoded con notación de brackets,
# NO JSON. Este script replica exactamente ese formato.
#
# Uso:
#   .venv/bin/python scripts/simular_webhook_kommo.py            # ambos
#   .venv/bin/python scripts/simular_webhook_kommo.py chat       # mensaje entrante
#   .venv/bin/python scripts/simular_webhook_kommo.py lead       # cambio de etapa
#   .venv/bin/python scripts/simular_webhook_kommo.py chat_saliente
#   .venv/bin/python scripts/simular_webhook_kommo.py lead_nuevo
#
# Requiere servidor corriendo:
#   .venv/bin/python -m uvicorn agent.main:app --reload --port 8000

import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from urllib.parse import urlencode
from agent.config import settings

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
SECRET   = settings.KOMMO_WEBHOOK_SECRET
NOW      = int(time.time())

# ── Payloads en el formato real de Kommo (form-encoded) ───────────────────────

PAYLOADS = {

    "chat": {
        "_desc": "Mensaje ENTRANTE del cliente (replica payload real de Kommo)",
        "endpoint": "/webhooks/kommo/chat",
        "form": {
            "account[subdomain]":               "lurarealty",
            "account[id]":                      "35347992",
            "account[_links][self]":            "https://lurarealty.amocrm.com",
            "message[add][0][id]":              f"sim-{NOW}",
            "message[add][0][chat_id]":         "844cb9e6-3dd4-4068-abe4-c569090048c1",
            "message[add][0][talk_id]":         "6718",
            "message[add][0][contact_id]":      "56751866",
            "message[add][0][text]":            "Hola, me interesa el Acuario Vallarta. ¿Cuánto cuesta una acción?",
            "message[add][0][created_at]":      str(NOW),
            "message[add][0][element_type]":    "2",
            "message[add][0][entity_type]":     "lead",
            "message[add][0][element_id]":      "42015036",
            "message[add][0][entity_id]":       "42015036",
            "message[add][0][type]":            "incoming",
            "message[add][0][author][id]":      "b51c97d6-a385-4b32-aebb-482c40b93c98",
            "message[add][0][author][type]":    "external",
            "message[add][0][author][name]":    "Juan Test",
            "message[add][0][origin]":          "com.amocrm.amocrmwa",
        },
    },

    "chat_saliente": {
        "_desc": "Mensaje SALIENTE del agente (type=outgoing — debe ignorarse)",
        "endpoint": "/webhooks/kommo/chat",
        "form": {
            "account[subdomain]":               "lurarealty",
            "account[id]":                      "35347992",
            "message[add][0][id]":              f"sim-out-{NOW}",
            "message[add][0][entity_id]":       "42015036",
            "message[add][0][type]":            "outgoing",
            "message[add][0][text]":            "¡Hola! Soy Juan Ramirez de IAD México.",
            "message[add][0][author][type]":    "user",
            "message[add][0][author][name]":    "Juan Ramirez",
            "message[add][0][created_at]":      str(NOW),
        },
    },

    "lead": {
        "_desc": "Lead cambió de etapa: TOQUE_1 → TOQUE_2 (replica payload real)",
        "endpoint": "/webhooks/kommo/lead",
        "form": {
            "account[subdomain]":                       "lurarealty",
            "account[id]":                              "35347992",
            "account[_links][self]":                    "https://lurarealty.amocrm.com",
            "leads[status][0][id]":                     "42015036",
            "leads[status][0][name]":                   "",
            "leads[status][0][status_id]":              "105360847",  # TOQUE_2
            "leads[status][0][old_status_id]":          "105360771",  # TOQUE_1
            "leads[status][0][price]":                  "0",
            "leads[status][0][responsible_user_id]":    "14271535",
            "leads[status][0][last_modified]":          str(NOW),
            "leads[status][0][pipeline_id]":            "13652595",
            "leads[status][0][account_id]":             "35347992",
            "leads[status][0][created_at]":             str(NOW - 3600),
            "leads[status][0][updated_at]":             str(NOW),
        },
    },

    "lead_nuevo": {
        "_desc": "Lead nuevo creado en Leads Entrantes",
        "endpoint": "/webhooks/kommo/lead",
        "form": {
            "account[subdomain]":                   "lurarealty",
            "account[id]":                          "35347992",
            "leads[add][0][id]":                    "99001",
            "leads[add][0][status_id]":             "105360767",  # LEADS_ENTRANTES
            "leads[add][0][pipeline_id]":           "13652595",
            "leads[add][0][responsible_user_id]":   "14271535",
            "leads[add][0][created_at]":            str(NOW),
        },
    },
}


# ── Runner ────────────────────────────────────────────────────────────────────

async def simular(clave: str) -> None:
    caso     = PAYLOADS[clave]
    url      = f"{BASE_URL}{caso['endpoint']}?secret={SECRET}"
    form_raw = caso["form"]
    body     = urlencode(form_raw).encode()

    print(f"\n{'='*60}")
    print(f"  [{clave}] {caso['_desc']}")
    print(f"  URL:  {url}")
    print(f"  Body (primeras 3 claves):")
    for k, v in list(form_raw.items())[:3]:
        print(f"    {k} = {v}")
    print(f"    ... ({len(form_raw)} campos en total)")
    print()

    async with httpx.AsyncClient(timeout=5) as client:
        try:
            t0 = time.monotonic()
            r  = await client.post(
                url,
                content=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            ms = round((time.monotonic() - t0) * 1000)
            icono = "✓" if r.status_code == 200 else "✗"
            print(f"  {icono} HTTP {r.status_code}  ({ms} ms)  →  {r.text}")
        except httpx.ConnectError:
            print(f"  ✗ No se pudo conectar a {BASE_URL}")
            print("    .venv/bin/python -m uvicorn agent.main:app --reload")
    print()


async def main() -> None:
    print()
    print("=" * 60)
    print("  Simulador de Webhooks Kommo — formato real (form-encoded)")
    print("=" * 60)

    claves_validas = list(PAYLOADS.keys())
    arg = sys.argv[1] if len(sys.argv) > 1 else "ambos"

    if arg == "ambos":
        claves = ["chat", "lead"]
    elif arg in claves_validas:
        claves = [arg]
    else:
        print(f"\n  Uso: script [{'|'.join(claves_validas)}|ambos]\n")
        return

    for clave in claves:
        await simular(clave)


if __name__ == "__main__":
    asyncio.run(main())
