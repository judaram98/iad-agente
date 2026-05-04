# agent/kommo_sync.py — Sincronización de conversaciones WhatsApp con Kommo CRM
#
# Se llama desde main.py después de cada mensaje entrante.
# Si KOMMO_PIPELINE_ID no está configurado, no hace nada.
# Si Kommo falla, registra warning y continúa — nunca bloquea el flujo principal.

import logging
from agent.config import settings
from config.etapas import (
    LEADS_ENTRANTES, ETAPAS_CONGELADAS,
    es_etapa_congelada, etapa_siguiente_por_interes,
)
from services.kommo import (
    getLead, moveLeadToStage, setLeadTags,
    searchContactsByPhone, createContact, createLead,
    KommoError,
)

logger = logging.getLogger("kommo_sync")

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
            # Reutilizar solo leads que no estén cerrados
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
    lead_id: int | None = None,
) -> int | None:
    """
    Busca o crea el lead del prospecto en Kommo y avanza su etapa según el interés.

    Si `lead_id` se provee (modo Kommo), omite la búsqueda por teléfono y trabaja
    directamente sobre ese lead — evita el 400 de Kommo que ocurre cuando
    str(lead_id) se trata como número de teléfono y genera un lead duplicado.

    Garantías:
    - Si KOMMO_PIPELINE_ID no está configurado: no-op.
    - Si el lead está en etapa congelada: no mueve ni retrocede.
    - Si Kommo falla: warning silencioso, retorna None.

    Returns: lead_id de Kommo si tuvo éxito, None en caso contrario.
    """
    if not settings.KOMMO_PIPELINE_ID:
        return None

    pipeline_id = settings.KOMMO_PIPELINE_ID

    try:
        if lead_id is not None:
            # Modo Kommo: ya conocemos el lead — solo actualizar tags y etapa.
            logger.debug(f"Kommo sync directo: lead_id={lead_id} interes={interes}")

            if interes in _TAG_INTERES:
                await setLeadTags(lead_id, [_TAG_INTERES[interes]])

        else:
            # Modo Whapi: buscar o crear contacto + lead por teléfono.
            contactos = await searchContactsByPhone(telefono)
            if contactos:
                contact_id = contactos[0]["id"]
                logger.debug(f"Contacto Kommo: id={contact_id} ({telefono})")
            else:
                contacto = await createContact(nombre or _limpiar_telefono(telefono), telefono)
                contact_id = contacto["id"]
                logger.info(f"Contacto Kommo creado: id={contact_id} ({telefono})")

            lead_id = await _buscar_lead_en_pipeline(contact_id, pipeline_id)
            es_nuevo = lead_id is None

            if es_nuevo:
                lead = await createLead(
                    name=f"WhatsApp {_limpiar_telefono(telefono)}",
                    pipeline_id=pipeline_id,
                    status_id=LEADS_ENTRANTES,
                    contact_id=contact_id,
                )
                lead_id = lead["id"]
                logger.info(f"Lead Kommo creado: id={lead_id} ({telefono})")

                tags = ["IA", "WhatsApp"]
                if interes in _TAG_INTERES:
                    tags.append(_TAG_INTERES[interes])
                await setLeadTags(lead_id, tags)
            else:
                logger.debug(f"Lead Kommo existente: id={lead_id} ({telefono})")
                if interes in _TAG_INTERES:
                    await setLeadTags(lead_id, [_TAG_INTERES[interes]])

        # Avanzar etapa si corresponde (aplica a ambos modos)
        lead_actual = await getLead(lead_id)
        etapa_actual = lead_actual.get("status_id")

        if es_etapa_congelada(etapa_actual):
            logger.debug(f"Lead {lead_id} en etapa congelada ({etapa_actual}) — sin cambios")
            return lead_id

        etapa_destino = etapa_siguiente_por_interes(interes, etapa_actual)
        if etapa_destino is not None and etapa_destino != etapa_actual:
            await moveLeadToStage(lead_id, etapa_destino)
            logger.info(f"Lead {lead_id}: {etapa_actual} → {etapa_destino} (interés={interes})")

        return lead_id

    except KommoError as e:
        logger.warning(f"Kommo sync fallido ({telefono}): {e}")
        return None
    except Exception as e:
        logger.error(f"Error inesperado en Kommo sync ({telefono}): {e}")
        return None


def _limpiar_telefono(phone: str) -> str:
    """Elimina sufijo de WhatsApp y deja solo dígitos."""
    phone = phone.split("@")[0]
    return "".join(filter(str.isdigit, phone))
