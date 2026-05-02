# agent/brain.py — Cerebro del agente: conexión con Groq (LLaMA 3.3 70B)

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
