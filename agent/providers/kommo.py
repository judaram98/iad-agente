# agent/providers/kommo.py — Provider que recibe y envía mensajes vía Kommo Talk API
#
# Cuando AGENT_MODE=kommo:
#   - Los mensajes llegan por POST /webhooks/kommo/chat (form-encoded de Kommo)
#   - La clave conversacional es lead_id, NO el número de teléfono
#   - Las respuestas se envían via services/kommo.sendChatMessage(lead_id, texto)

import logging
from urllib.parse import parse_qsl
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("kommo_provider")


# ── Parser del formato form-encoded de Kommo ─────────────────────────────────
# (Se exporta para que main.py lo use directamente en el endpoint)

def _to_int(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except (ValueError, TypeError):
        return None


def parsear_form_kommo(body: bytes) -> dict:
    """
    Parsea el payload URL-encoded de Kommo (application/x-www-form-urlencoded).

    Kommo envía notación de brackets:
        message[add][0][text]=Hola
        leads[status][0][status_id]=105360847

    Retorna un dict normalizado con:
        account_id, subdomain, messages[], leads[]
    """
    flat = dict(parse_qsl(body.decode("utf-8", errors="replace"), keep_blank_values=True))

    messages = []
    i = 0
    while f"message[add][{i}][id]" in flat:
        messages.append({
            "id":          flat.get(f"message[add][{i}][id]"),
            "entity_id":   _to_int(flat.get(f"message[add][{i}][entity_id]")),   # lead_id
            "talk_id":     _to_int(flat.get(f"message[add][{i}][talk_id]")),
            "contact_id":  _to_int(flat.get(f"message[add][{i}][contact_id]")),
            "text":        flat.get(f"message[add][{i}][text]", ""),
            "type":        flat.get(f"message[add][{i}][type]", ""),   # incoming | outgoing
            "origin":      flat.get(f"message[add][{i}][origin]", ""),
            "author_name": flat.get(f"message[add][{i}][author][name]", ""),
            "author_type": flat.get(f"message[add][{i}][author][type]", ""),
            "created_at":  _to_int(flat.get(f"message[add][{i}][created_at]")),
        })
        i += 1

    lead_events = []
    for prefix in ("status", "add", "update"):
        i = 0
        while f"leads[{prefix}][{i}][id]" in flat:
            lead_events.append({
                "event":               prefix,
                "id":                  _to_int(flat.get(f"leads[{prefix}][{i}][id]")),
                "status_id":           _to_int(flat.get(f"leads[{prefix}][{i}][status_id]")),
                "old_status_id":       _to_int(flat.get(f"leads[{prefix}][{i}][old_status_id]")),
                "pipeline_id":         _to_int(flat.get(f"leads[{prefix}][{i}][pipeline_id]")),
                "responsible_user_id": _to_int(flat.get(f"leads[{prefix}][{i}][responsible_user_id]")),
            })
            i += 1

    return {
        "account_id": _to_int(flat.get("account[id]")),
        "subdomain":  flat.get("account[subdomain]", ""),
        "messages":   messages,
        "leads":      lead_events,
    }


# ── Provider ──────────────────────────────────────────────────────────────────

class ProveedorKommo(ProveedorWhatsApp):
    """
    Provider para el canal Kommo Talk API.

    Interfaz pública:
        parsear_webhook(request)   → lee body, llama normalizar_mensajes()
        normalizar_mensajes(dict)  → list[MensajeEntrante]  (para uso desde endpoints)
        enviar_mensaje(telefono, texto) → sendChatMessage(int(telefono), texto)
    """

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """
        Implementación de la interfaz base.
        Lee el body form-encoded y devuelve mensajes normalizados.
        """
        body = await request.body()
        payload = parsear_form_kommo(body)
        return self.normalizar_mensajes(payload)

    def normalizar_mensajes(self, payload: dict) -> list[MensajeEntrante]:
        """
        Convierte el payload ya parseado en objetos MensajeEntrante.

        - telefono = str(lead_id)  ← clave conversacional maestra
        - es_propio = True si type == "outgoing" (el agente ya respondió, ignorar)
        - Omite mensajes sin entity_id (lead_id).
        """
        result = []
        for msg in payload.get("messages", []):
            lead_id = msg.get("entity_id")
            if lead_id is None:
                logger.warning(f"Mensaje Kommo sin entity_id ignorado: id={msg.get('id')}")
                continue

            result.append(MensajeEntrante(
                telefono=str(lead_id),          # lead_id como clave conversacional
                texto=msg.get("text", ""),
                mensaje_id=msg.get("id", ""),
                es_propio=msg.get("type") == "outgoing",
                lead_id=lead_id,
                contact_id=msg.get("contact_id"),
            ))
        return result

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """
        Envía respuesta al lead via Kommo Talk API.
        telefono contiene str(lead_id) en modo Kommo.
        """
        from services.kommo import sendChatMessage, KommoError
        try:
            lead_id = int(telefono)
        except ValueError:
            logger.error(f"enviar_mensaje: telefono no es lead_id válido: '{telefono}'")
            return False
        try:
            await sendChatMessage(lead_id, mensaje)
            logger.debug(f"Mensaje enviado a lead {lead_id} via Kommo Talk")
            return True
        except KommoError as e:
            logger.error(f"Error Kommo Talk al enviar a lead {lead_id}: {e}")
            return False
