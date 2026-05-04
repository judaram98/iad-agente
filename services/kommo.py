# services/kommo.py — Cliente async para la API v4 de Kommo CRM
#
# Rate limit interno: 6 req/s (margen sobre el límite oficial de 7/s).
# Retry con exponential backoff en 429. Error crítico en 403, sin retry.

import asyncio
import logging
import time
from typing import Any

import httpx
from dotenv import load_dotenv

from agent.config import settings

load_dotenv()

logger = logging.getLogger("kommo")

# ── Configuración ─────────────────────────────────────────────────────────────

_BASE_URL = f"https://{settings.KOMMO_SUBDOMAIN}/api/v4"
_HEADERS = {
    "Authorization": f"Bearer {settings.KOMMO_ACCESS_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}
_MAX_RETRIES = 3


# ── Excepciones ───────────────────────────────────────────────────────────────

class KommoError(Exception):
    pass

class KommoRateLimitError(KommoError):
    pass

class KommoForbiddenError(KommoError):
    pass

class KommoNotFoundError(KommoError):
    pass


# ── Rate limiter: token bucket simple, 6 req/s ────────────────────────────────

class _RateLimiter:
    """
    Garantiza como máximo `rate` requests por segundo.
    Usa un lock para serializar el acceso — los callers se encolan
    y se liberan a razón de 1 cada (1/rate) segundos.
    """

    def __init__(self, rate: int = 6):
        self._interval = 1.0 / rate
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        async with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._last + self._interval - now)
            if wait:
                await asyncio.sleep(wait)
            self._last = time.monotonic()

    async def __aexit__(self, *_):
        pass


_throttle = _RateLimiter(rate=6)


# ── Función de request central ────────────────────────────────────────────────

async def _req(
    method: str,
    endpoint: str,
    *,
    params: dict = None,
    json: Any = None,
) -> Any:
    """
    Ejecuta una llamada HTTP a la API de Kommo con:
    - throttling (≤6 req/s)
    - retry en 429 con backoff exponencial (máx 3 intentos)
    - error crítico en 403 sin retry
    - log estructurado por llamada
    """
    url = f"{_BASE_URL}/{endpoint.lstrip('/')}"

    for attempt in range(_MAX_RETRIES):
        async with _throttle:
            t0 = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.request(
                        method,
                        url,
                        headers=_HEADERS,
                        params=params,
                        json=json,
                    )
            except httpx.RequestError as exc:
                latency_ms = round((time.monotonic() - t0) * 1000)
                logger.error(
                    "kommo_request_error",
                    extra={
                        "method": method,
                        "endpoint": endpoint,
                        "attempt": attempt + 1,
                        "error": str(exc),
                        "latency_ms": latency_ms,
                    },
                )
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise KommoError(f"Error de conexión con Kommo: {exc}") from exc

        latency_ms = round((time.monotonic() - t0) * 1000)

        logger.info(
            "kommo_request",
            extra={
                "method": method,
                "endpoint": endpoint,
                "status": r.status_code,
                "latency_ms": latency_ms,
                "attempt": attempt + 1,
            },
        )

        # 429 → retry con backoff
        if r.status_code == 429:
            wait = 2 ** attempt  # 1s, 2s, 4s
            logger.warning(
                f"Kommo 429 en {method} {endpoint} — reintentando en {wait}s "
                f"(intento {attempt + 1}/{_MAX_RETRIES})"
            )
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(wait)
                continue
            raise KommoRateLimitError(
                f"Rate limit de Kommo agotado tras {_MAX_RETRIES} intentos en {endpoint}"
            )

        # 403 → error crítico, sin retry
        if r.status_code == 403:
            logger.error(
                f"ERROR CRÍTICO — Kommo 403 Forbidden en {method} {endpoint}. "
                "Verifica que KOMMO_ACCESS_TOKEN sea válido y no haya expirado."
            )
            raise KommoForbiddenError(
                f"403 Forbidden: acceso denegado en {endpoint}. "
                "Renueva KOMMO_ACCESS_TOKEN."
            )

        if r.status_code == 404:
            raise KommoNotFoundError(f"404: recurso no encontrado — {endpoint}")

        if not (200 <= r.status_code < 300):
            logger.debug(f"Kommo {r.status_code} body: {r.text[:500]}")
        r.raise_for_status()

        # 204 No Content (ej: DELETE exitoso)
        if r.status_code == 204 or not r.content:
            return {}

        return r.json()

    raise KommoError(f"No se pudo completar la request a {endpoint} tras {_MAX_RETRIES} intentos")


# ── Funciones públicas ────────────────────────────────────────────────────────

async def getLead(lead_id: int) -> dict:
    """Retorna el lead completo con sus embedded (contactos, tags, etc.)."""
    return await _req("GET", f"leads/{lead_id}", params={"with": "contacts,tags,talks"})


async def updateLead(lead_id: int, payload: dict) -> dict:
    """
    Actualiza campos arbitrarios del lead.
    payload puede incluir: name, status_id, pipeline_id, price,
    responsible_user_id, custom_fields_values, _embedded.tags, etc.
    """
    return await _req("PATCH", f"leads/{lead_id}", json=payload)


async def moveLeadToStage(lead_id: int, status_id: int) -> dict:
    """Mueve el lead a una etapa (columna) específica del pipeline."""
    return await updateLead(lead_id, {"status_id": status_id})


async def addTagToLead(lead_id: int, tag_name: str) -> dict:
    """
    Agrega un tag al lead conservando los existentes.
    Kommo reemplaza los tags en cada PATCH, así que leemos primero.
    """
    lead = await getLead(lead_id)
    tags_actuales = [
        {"name": t["name"]}
        for t in lead.get("_embedded", {}).get("tags", [])
    ]
    # Agregar solo si no existe ya
    nombres_actuales = {t["name"] for t in tags_actuales}
    if tag_name not in nombres_actuales:
        tags_actuales.append({"name": tag_name})

    return await updateLead(lead_id, {"_embedded": {"tags": tags_actuales}})


async def removeTagFromLead(lead_id: int, tag_name: str) -> dict:
    """Elimina un tag del lead conservando los demás."""
    lead = await getLead(lead_id)
    tags_sin_el = [
        {"name": t["name"]}
        for t in lead.get("_embedded", {}).get("tags", [])
        if t["name"] != tag_name
    ]
    return await updateLead(lead_id, {"_embedded": {"tags": tags_sin_el}})


async def setLeadCustomField(lead_id: int, field_id: int, value: Any) -> dict:
    """
    Establece el valor de un campo personalizado del lead.
    Para campos de tipo select/multiselect, value debe ser el enum_id (int).
    Para texto/número, value es el string o número directamente.
    """
    payload = {
        "custom_fields_values": [
            {
                "field_id": field_id,
                "values": [{"value": value}],
            }
        ]
    }
    return await updateLead(lead_id, payload)


async def getContact(contact_id: int) -> dict:
    """Retorna el contacto completo."""
    return await _req("GET", f"contacts/{contact_id}", params={"with": "leads,tags"})


def _limpiar_telefono(phone: str) -> str:
    """Normaliza un número de WhatsApp para búsqueda/almacenamiento en Kommo."""
    # Eliminar sufijo de WhatsApp (@c.us, @g.us)
    phone = phone.split("@")[0]
    # Dejar solo dígitos
    return "".join(filter(str.isdigit, phone))


async def searchContactsByPhone(phone: str) -> list[dict]:
    """Busca contactos en Kommo por número de teléfono. Retorna lista vacía si no hay."""
    phone_clean = _limpiar_telefono(phone)
    try:
        data = await _req("GET", "contacts", params={"query": phone_clean, "with": "leads"})
    except KommoNotFoundError:
        return []
    return data.get("_embedded", {}).get("contacts", [])


async def createContact(name: str, phone: str) -> dict:
    """Crea un nuevo contacto en Kommo con número de teléfono."""
    payload = [
        {
            "name": name,
            "custom_fields_values": [
                {
                    "field_code": "PHONE",
                    "values": [{"value": _limpiar_telefono(phone), "enum_code": "WORK"}],
                }
            ],
        }
    ]
    data = await _req("POST", "contacts", json=payload)
    contacts = data.get("_embedded", {}).get("contacts", [])
    if not contacts:
        raise KommoError("Kommo no devolvió contacto creado")
    return contacts[0]


async def createLead(
    name: str,
    pipeline_id: int,
    status_id: int,
    contact_id: int = None,
) -> dict:
    """Crea un nuevo lead en Kommo, opcionalmente vinculado a un contacto."""
    lead_data: dict[str, Any] = {
        "name": name,
        "pipeline_id": pipeline_id,
        "status_id": status_id,
    }
    if contact_id:
        lead_data["_embedded"] = {"contacts": [{"id": contact_id}]}

    data = await _req("POST", "leads", json=[lead_data])
    leads = data.get("_embedded", {}).get("leads", [])
    if not leads:
        raise KommoError("Kommo no devolvió lead creado")
    return leads[0]


async def setLeadTags(lead_id: int, tags_nuevos: list[str]) -> dict:
    """
    Agrega múltiples tags al lead en una sola llamada, conservando los existentes.
    Más eficiente que llamar addTagToLead() varias veces.
    """
    lead = await getLead(lead_id)
    existentes = {t["name"] for t in lead.get("_embedded", {}).get("tags", [])}
    merged = existentes | set(tags_nuevos)
    return await updateLead(lead_id, {"_embedded": {"tags": [{"name": t} for t in merged]}})


async def listPipelines() -> list[dict]:
    """
    Lista todos los pipelines con sus etapas (statuses).
    Útil para obtener los IDs de columnas del embudo.
    """
    data = await _req("GET", "leads/pipelines", params={"with": "statuses"})
    return data.get("_embedded", {}).get("pipelines", [])


async def sendChatMessage(lead_id: int, text: str) -> dict:
    """
    Inyecta un mensaje saliente en el chat del lead via la API de Talks de Kommo.

    Flujo:
    1. Obtiene el lead para leer su talks_id.
    2. Si existe talk, manda el mensaje via POST /talks/{id}/messages.
    3. Si no hay talk asociado, lanza excepción con instrucción clara.

    Nota: El talk_id se crea automáticamente cuando un canal (WhatsApp,
    Telegram, etc.) está conectado y el cliente escribió al menos una vez.
    """
    lead = await getLead(lead_id)

    # Buscar el talk (conversación) vinculado al lead
    talks = lead.get("_embedded", {}).get("talks", [])
    if not talks:
        raise KommoError(
            f"Lead {lead_id} no tiene conversación (talk) asociada. "
            "El cliente debe haber escrito primero desde el canal de WhatsApp/Telegram."
        )

    talk_id = talks[0]["id"]

    payload = {
        "text": text,
        "type": "text",
    }

    return await _req("POST", f"talks/{talk_id}/messages", json=payload)
