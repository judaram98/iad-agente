# agent/kommo_sync.py — Sincronización de conversaciones WhatsApp con Kommo CRM
#
# Se llama desde main.py después de cada mensaje entrante.
# Si KOMMO_PIPELINE_ID no está configurado, no hace nada.
# Si Kommo falla, registra warning y continúa — nunca bloquea el flujo principal.

import logging
from agent.config import settings
from services.kommo import (
    getLead, moveLeadToStage, setLeadTags,
    searchContactsByPhone, createContact, createLead,
    KommoError,
)

logger = logging.getLogger("kommo_sync")

# ── IDs de etapas del pipeline "IA" (pipeline_id=13652595) ───────────────────

STAGE_ENTRANTE      = 105360767  # Leads Entrantes
STAGE_TOQUE1        = 105360771  # IA - Toque 1
STAGE_SIN_PERFILAR  = 105360863  # IA - Sin perfilar - Contestó
STAGE_CITA_PRE      = 105360867  # IA - Cita (pre)
STAGE_CITA_DURANTE  = 105360871  # IA - Cita (durante y post)
STAGE_FRIOS         = 105360879  # IA - Frios
STAGE_MAS_ADELANTE  = 105360883  # Más adelante

# Prioridad ordinal para evitar mover un lead hacia atrás en el pipeline.
# 0 = etapa especial (fríos / más adelante) — sin prioridad comparativa.
_PRIORIDAD = {
    STAGE_ENTRANTE:     1,
    STAGE_TOQUE1:       2,
    STAGE_SIN_PERFILAR: 3,
    STAGE_CITA_PRE:     4,
    STAGE_CITA_DURANTE: 5,
}

_TAG_INTERES = {
    "alto":    "Interés Alto",
    "medio":   "Interés Medio",
    "bajo":    "Interés Bajo",
    "ninguno": "Sin Interés",
}


async def _buscar_lead_en_pipeline(contact_id: int, pipeline_id: int) -> int | None:
    """Retorna el id del primer lead activo del contacto en este pipeline, o None."""
    from services.kommo import getContact
    try:
        contact = await getContact(contact_id)
    except KommoError:
        return None

    for lead_ref in contact.get("_embedded", {}).get("leads", []):
        try:
            lead = await getLead(lead_ref["id"])
            # Leads cerrados (142 = ganado, 143 = perdido) no se reutilizan
            if (
                lead.get("pipeline_id") == pipeline_id
                and lead.get("status_id") not in (142, 143)
            ):
                return lead["id"]
        except KommoError:
            continue
    return None


async def sincronizar_con_kommo(
    telefono: str,
    nombre: str | None,
    interes: str,
) -> int | None:
    """
    Busca o crea el lead del prospecto en Kommo y avanza su etapa según el interés.

    - Si KOMMO_PIPELINE_ID no está configurado: no-op.
    - Si Kommo falla: warning silencioso, retorna None.
    - Nunca retrocede un lead en el pipeline (excepto → Fríos si interes="ninguno").

    Returns: lead_id de Kommo si tuvo éxito, None en caso contrario.
    """
    if not settings.KOMMO_PIPELINE_ID:
        return None

    pipeline_id = settings.KOMMO_PIPELINE_ID

    try:
        # 1. Buscar o crear contacto
        contactos = await searchContactsByPhone(telefono)
        if contactos:
            contact_id = contactos[0]["id"]
            logger.debug(f"Contacto Kommo encontrado: id={contact_id} para {telefono}")
        else:
            contacto = await createContact(nombre or telefono, telefono)
            contact_id = contacto["id"]
            logger.info(f"Contacto Kommo creado: id={contact_id} para {telefono}")

        # 2. Buscar lead activo en el pipeline, o crear uno nuevo
        lead_id = await _buscar_lead_en_pipeline(contact_id, pipeline_id)
        es_nuevo = lead_id is None

        if es_nuevo:
            lead = await createLead(
                name=f"WhatsApp {_limpiar_telefono(telefono)}",
                pipeline_id=pipeline_id,
                status_id=STAGE_ENTRANTE,
                contact_id=contact_id,
            )
            lead_id = lead["id"]
            logger.info(f"Lead Kommo creado: id={lead_id} para {telefono}")

            # Tags iniciales en una sola llamada
            tags = ["IA", "WhatsApp"]
            if interes in _TAG_INTERES:
                tags.append(_TAG_INTERES[interes])
            await setLeadTags(lead_id, tags)
        else:
            logger.debug(f"Lead Kommo existente: id={lead_id} para {telefono}")
            if interes in _TAG_INTERES:
                await setLeadTags(lead_id, [_TAG_INTERES[interes]])

        # 3. Mover la etapa según el nivel de interés
        lead_actual = await getLead(lead_id)
        etapa_actual = lead_actual.get("status_id")
        prioridad_actual = _PRIORIDAD.get(etapa_actual, 0)

        if interes == "ninguno":
            if etapa_actual != STAGE_FRIOS:
                await moveLeadToStage(lead_id, STAGE_FRIOS)
                logger.info(f"Lead {lead_id} → Frios")

        elif interes == "alto":
            if prioridad_actual < _PRIORIDAD[STAGE_CITA_PRE]:
                await moveLeadToStage(lead_id, STAGE_CITA_PRE)
                logger.info(f"Lead {lead_id} → Cita (pre)")

        elif es_nuevo or prioridad_actual <= _PRIORIDAD[STAGE_TOQUE1]:
            # Lead recién creado o todavía en Toque 1 → mover a "Contestó"
            await moveLeadToStage(lead_id, STAGE_SIN_PERFILAR)
            logger.info(f"Lead {lead_id} → Sin perfilar / Contestó")

        return lead_id

    except KommoError as e:
        logger.warning(f"Kommo sync fallido para {telefono}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error inesperado en Kommo sync para {telefono}: {e}")
        return None


def _limpiar_telefono(phone: str) -> str:
    """Elimina sufijo de WhatsApp y deja solo dígitos."""
    phone = phone.split("@")[0]
    return "".join(filter(str.isdigit, phone))
