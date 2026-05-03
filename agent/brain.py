# agent/brain.py — Cerebro del agente: conexión con Groq (LLaMA 3.3 70B)
#
# Modos de operación:
#   AGENT_MODE=whapi  → generar_respuesta()         (clave: teléfono)
#   AGENT_MODE=kommo  → procesar_mensaje_kommo()    (clave: lead_id)

import os
import yaml
import logging
from groq import AsyncGroq
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
MODELO = "llama-3.3-70b-versatile"


def cargar_config_prompts() -> dict:
    """Lee toda la configuración desde config/prompts.yaml."""
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/prompts.yaml no encontrado")
        return {}


def cargar_system_prompt() -> str:
    config = cargar_config_prompts()
    return config.get("system_prompt", "Eres un asesor de inversiones útil. Responde en español.")


def obtener_mensaje_error() -> str:
    config = cargar_config_prompts()
    return config.get("error_message", "Lo siento, estoy teniendo problemas técnicos. Por favor intenta de nuevo.")


def obtener_mensaje_fallback() -> str:
    config = cargar_config_prompts()
    return config.get("fallback_message", "Disculpa, no entendí tu mensaje. ¿Podrías reformularlo?")


def construir_contexto_lead(lead_data: dict) -> str:
    """
    Convierte los datos del lead de Kommo en un bloque de contexto
    que se inyecta al inicio del system prompt.
    """
    from config.etapas import NOMBRE_ETAPA
    status_id = lead_data.get("status_id", 0)
    etapa_nombre = NOMBRE_ETAPA.get(status_id, f"Etapa desconocida ({status_id})")

    nombre = lead_data.get("name", "Sin nombre")
    lead_id = lead_data.get("id", "?")

    tags = [t.get("name", "") for t in lead_data.get("_embedded", {}).get("tags", [])]
    tags_str = ", ".join(tags) if tags else "ninguna"

    campos = lead_data.get("custom_fields_values") or []
    campos_str = ""
    for campo in campos:
        nombre_campo = campo.get("field_name", "")
        valores = [str(v.get("value", "")) for v in campo.get("values", [])]
        if nombre_campo and valores:
            campos_str += f"\n  - {nombre_campo}: {', '.join(valores)}"

    return (
        f"## Contexto del lead actual\n"
        f"- Lead ID: {lead_id}\n"
        f"- Nombre: {nombre}\n"
        f"- Etapa actual: {etapa_nombre}\n"
        f"- Etiquetas: {tags_str}"
        + (f"\n- Campos personalizados:{campos_str}" if campos_str else "")
        + "\n"
    )


async def procesar_mensaje_kommo(
    lead_id: int,
    texto: str,
    historial: list[dict],
) -> str | None:
    """
    Pipeline completo para mensajes entrantes en modo Kommo.

    1. Obtiene el lead de Kommo para contexto e inyecta su info al system prompt.
    2. GUARDIA CRÍTICA: si la etapa está congelada, retorna None (no responder).
    3. Genera la respuesta con Groq usando el contexto enriquecido.

    Retorna None si el lead está congelado o si hay error al obtenerlo.
    """
    from services.kommo import getLead, KommoError
    from config.etapas import es_etapa_congelada

    if not texto or len(texto.strip()) < 2:
        return obtener_mensaje_fallback()

    # ── Obtener contexto del lead ────────────────────────────────────────────
    try:
        lead_data = await getLead(lead_id)
    except KommoError as e:
        logger.warning(f"[BRAIN] No se pudo obtener lead {lead_id}: {e} — respondiendo sin contexto")
        lead_data = {}

    # ── GUARDIA CRÍTICA: etapa congelada ─────────────────────────────────────
    status_id = lead_data.get("status_id", 0)
    if status_id and es_etapa_congelada(status_id):
        from config.etapas import NOMBRE_ETAPA
        etapa = NOMBRE_ETAPA.get(status_id, str(status_id))
        logger.info(f"[BRAIN] Lead {lead_id} en etapa congelada ({etapa}) — silenciado")
        return None

    # ── Construir system prompt enriquecido ──────────────────────────────────
    base_prompt = cargar_system_prompt()
    if lead_data:
        contexto_lead = construir_contexto_lead(lead_data)
        system_prompt = f"{base_prompt}\n\n{contexto_lead}"
    else:
        system_prompt = base_prompt

    # ── Llamar a Groq ─────────────────────────────────────────────────────────
    mensajes = [{"role": "system", "content": system_prompt}]
    for msg in historial:
        mensajes.append({"role": msg["role"], "content": msg["content"]})
    mensajes.append({"role": "user", "content": texto})

    try:
        response = await client.chat.completions.create(
            model=MODELO,
            messages=mensajes,
            max_tokens=1024,
            temperature=0.7,
        )
        respuesta = response.choices[0].message.content
        logger.info(
            f"[BRAIN] Lead {lead_id} → {len(respuesta)} chars | {response.usage.total_tokens} tokens"
        )
        return respuesta
    except Exception as e:
        logger.error(f"[BRAIN] Error Groq (lead {lead_id}): {e}")
        return obtener_mensaje_error()


async def generar_respuesta(mensaje: str, historial: list[dict]) -> str:
    """
    Genera una respuesta usando Groq (LLaMA 3.3 70B).

    Args:
        mensaje: El mensaje nuevo del usuario
        historial: Lista de mensajes anteriores [{"role": "user/assistant", "content": "..."}]

    Returns:
        La respuesta generada por el modelo
    """
    if not mensaje or len(mensaje.strip()) < 2:
        return obtener_mensaje_fallback()

    system_prompt = cargar_system_prompt()

    # Groq usa el formato OpenAI: system + historial + mensaje actual
    mensajes = [{"role": "system", "content": system_prompt}]

    for msg in historial:
        mensajes.append({"role": msg["role"], "content": msg["content"]})

    mensajes.append({"role": "user", "content": mensaje})

    try:
        response = await client.chat.completions.create(
            model=MODELO,
            messages=mensajes,
            max_tokens=1024,
            temperature=0.7,
        )

        respuesta = response.choices[0].message.content
        logger.info(f"Respuesta generada ({len(respuesta)} chars | {response.usage.total_tokens} tokens)")
        return respuesta

    except Exception as e:
        logger.error(f"Error Groq API: {e}")
        return obtener_mensaje_error()
