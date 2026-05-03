# agent/providers/__init__.py — Factory de proveedores de mensajería
#
# AGENT_MODE (en .env) decide qué canal se usa:
#   kommo  → mensajes llegan/salen por Kommo Talk API  (webhook: /webhooks/kommo/chat)
#   whapi  → mensajes llegan/salen por Whapi.cloud     (webhook: /webhook)

from agent.config import settings
from agent.providers.base import ProveedorWhatsApp


def obtener_proveedor() -> ProveedorWhatsApp:
    """Retorna el proveedor configurado según AGENT_MODE."""
    modo = settings.AGENT_MODE

    if modo == "kommo":
        from agent.providers.kommo import ProveedorKommo
        return ProveedorKommo()

    if modo == "whapi":
        from agent.providers.whapi import ProveedorWhapi
        return ProveedorWhapi()

    raise ValueError(
        f"AGENT_MODE inválido: '{modo}'. Valores permitidos: kommo | whapi"
    )
