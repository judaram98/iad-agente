# agent/kommo_sync.py — Sincronización de conversaciones WhatsApp con Kommo CRM
#
# RESTRICCIÓN CRÍTICA: solo opera sobre leads del pipeline configurado en
# KOMMO_PIPELINE_ID. Si el lead está en otro pipeline → no-op silencioso.
# Nunca toca, mueve, etiqueta ni modifica leads de otros pipelines.

import logging
from agent.config import settings
from config.etapas import (
    LEADS_ENTRANTES,
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


def _en_nuestro_pipeline(lead: dict, pipeline_id: int) -> bool:
    """True si el lead pertenece al pipeline que configuramos."""
    return lead.get("pipeline_id") == pipeline_id


async def _buscar_lead_en_pipeline(contact_id: int, pipeline_id: int) -> int | None:
    """
    Retorna el id del primer lead activo del contacto dentro de nuestro pipeline.
    Leads en otros pipelines son ignorados completamente.
    """
    from services.kommo import getContact
    try:
        contact = await getContact(contact_id)
    except KommoError:
        return None

    leads_ref = contact.get("_embedded", {}).get("leads", [])
    logger.debug(
        f"_buscar_lead: contact_id={contact_id} pipeline_id={pipeline_id} "
        f"total_leads={len(leads_ref)}"
    )

    for lead_ref in leads_ref:
        try:
            lead = await getLead(lead_ref["id"])
            lp = lead.get("pipeline_id")
            ls = lead.get("status_id")
            en_nuestro = lp == pipeline_id
            logger.debug(
                f"_buscar_lead: lead_id={lead_ref['id']} pipeline={lp} "
                f"status={ls} nuestro={'SÍ' if en_nuestro else 'NO — ignorado'}"
            )
            if en_nuestro and ls not in (142, 143):
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

    RESTRICCIÓN: solo actúa sobre leads del pipeline `KOMMO_PIPELINE_ID`.
    Si el lead está en otro pipeline, retorna None sin modificar nada.

    Si `lead_id` se provee (modo Kommo), verifica que pertenezca al pipeline
    correcto antes de tocar cualquier cosa.

    Returns: lead_id si tuvo éxito, None en caso contrario.
    """
    if not settings.KOMMO_PIPELINE_ID:
        return None

    pipeline_id = settings.KOMMO_PIPELINE_ID

    try:
        if lead_id is not None:
            # ── Modo Kommo: lead_id conocido (viene del webhook de Kommo) ──────
            # Verificar que el lead esté en NUESTRO pipeline antes de tocar algo.
            try:
                lead_data = await getLead(lead_id)
            except KommoError as e:
                logger.warning(f"No se pudo obtener lead {lead_id}: {e}")
                return None

            if not _en_nuestro_pipeline(lead_data, pipeline_id):
                logger.info(
                    f"[sync] lead_id={lead_id} pertenece a pipeline "
                    f"{lead_data.get('pipeline_id')} ≠ {pipeline_id} — ignorado"
                )
                return None

            etapa_actual = lead_data.get("status_id")

            if interes in _TAG_INTERES:
                await setLeadTags(lead_id, [_TAG_INTERES[interes]])

            if es_etapa_congelada(etapa_actual):
                logger.debug(f"Lead {lead_id} en etapa congelada ({etapa_actual}) — sin cambios")
                return lead_id

            etapa_destino = etapa_siguiente_por_interes(interes, etapa_actual)
            if etapa_destino is not None and etapa_destino != etapa_actual:
                await moveLeadToStage(lead_id, etapa_destino)
                logger.info(f"Lead {lead_id}: {etapa_actual} → {etapa_destino} (interés={interes})")

            return lead_id

        else:
            # ── Modo Whapi: buscar o crear contacto + lead por teléfono ────────
            contactos = await searchContactsByPhone(telefono)
            if contactos:
                contact_id = contactos[0]["id"]
                logger.debug(f"Contacto Kommo: id={contact_id} ({telefono})")
            else:
                contacto = await createContact(nombre or _limpiar_telefono(telefono), telefono)
                contact_id = contacto["id"]
                logger.info(f"Contacto Kommo creado: id={contact_id} ({telefono})")

            # Buscar lead SOLO dentro de nuestro pipeline
            lead_id = await _buscar_lead_en_pipeline(contact_id, pipeline_id)

            if lead_id is None:
                # No existe un lead nuestro — crear uno nuevo en nuestro pipeline
                try:
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
                except KommoError as e:
                    # 400 = Kommo rechaza crear lead (posiblemente ya existe en otro pipeline).
                    # NO tocamos el lead del otro pipeline — simplemente no sincronizamos.
                    logger.warning(
                        f"[sync] createLead rechazado ({telefono}): {e} — "
                        "el contacto puede tener un lead en otro pipeline. "
                        "Configura KOMMO_PIPELINE_ID correctamente para reutilizarlo."
                    )
                    return None
            else:
                logger.debug(f"Lead Kommo existente: id={lead_id} ({telefono})")
                if interes in _TAG_INTERES:
                    await setLeadTags(lead_id, [_TAG_INTERES[interes]])

            # Avanzar etapa si corresponde
            lead_actual = await getLead(lead_id)
            etapa_actual = lead_actual.get("status_id")

            # Doble verificación: garantizar que el lead que vamos a mover
            # sigue siendo de nuestro pipeline (por si algo cambió entre llamadas)
            if not _en_nuestro_pipeline(lead_actual, pipeline_id):
                logger.warning(
                    f"[sync] lead_id={lead_id} ya no está en pipeline {pipeline_id} — abortando"
                )
                return None

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
