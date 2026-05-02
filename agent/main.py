# agent/main.py — Servidor FastAPI + Webhook + Scheduler de seguimientos

import os
import re
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from agent.brain import generar_respuesta
from agent.memory import (
    inicializar_db, guardar_mensaje, obtener_historial,
    registrar_o_actualizar_lead, obtener_leads_para_seguimiento, incrementar_seguimiento,
)
from agent.providers import obtener_proveedor
from agent.tools import (
    calificar_interes, estado_desde_interes, obtener_mensaje_seguimiento,
    CATALOGO_ARCHIVOS, obtener_url_archivo,
)

load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("agentkit")

proveedor = obtener_proveedor()
PORT = int(os.getenv("PORT", 8000))
FOLLOWUP_DIAS = int(os.getenv("FOLLOWUP_DIAS", 3))
BASE_URL = os.getenv("BASE_URL", f"http://localhost:{PORT}")

scheduler = AsyncIOScheduler()

# Regex para detectar etiquetas de archivo en la respuesta del agente
REGEX_ARCHIVO = re.compile(r"\[ARCHIVO:(\w+)\]")


def extraer_archivos(texto: str) -> tuple[str, list[str]]:
    """
    Extrae las etiquetas [ARCHIVO:xxx] del texto y las retorna por separado.
    Retorna (texto_limpio, lista_de_claves).
    """
    claves = REGEX_ARCHIVO.findall(texto)
    texto_limpio = REGEX_ARCHIVO.sub("", texto).strip()
    return texto_limpio, claves


async def enviar_archivos(telefono: str, claves: list[str]):
    """Envía los archivos correspondientes a las claves detectadas."""
    for clave in claves:
        archivo = CATALOGO_ARCHIVOS.get(clave)
        if not archivo:
            logger.warning(f"Clave de archivo no encontrada en catálogo: {clave}")
            continue

        if archivo["tipo"] == "documento":
            ruta = f"media/{archivo['ruta_media']}"
            ok = await proveedor.enviar_documento(
                telefono, ruta, archivo["nombre"], archivo.get("caption", "")
            )
            logger.info(f"Documento '{clave}' enviado a {telefono}: {ok}")

        elif archivo["tipo"] == "imagenes":
            for img in archivo["archivos"]:
                ruta = f"media/{img['ruta_media']}"
                ok = await proveedor.enviar_imagen(telefono, ruta, img.get("caption", ""))
                logger.info(f"Imagen '{img['ruta_media']}' enviada a {telefono}: {ok}")


async def enviar_seguimientos_programados():
    """Tarea automática: envía seguimientos a leads sin contacto reciente."""
    logger.info("Ejecutando seguimientos automáticos...")
    leads = await obtener_leads_para_seguimiento(dias_sin_contacto=FOLLOWUP_DIAS)

    if not leads:
        logger.info("No hay leads pendientes de seguimiento.")
        return

    for lead in leads:
        try:
            mensaje = obtener_mensaje_seguimiento(lead.nombre, lead.seguimientos_enviados)
            enviado = await proveedor.enviar_mensaje(lead.telefono, mensaje)

            if enviado:
                await incrementar_seguimiento(lead.telefono)
                await guardar_mensaje(lead.telefono, "assistant", mensaje)
                logger.info(f"Seguimiento enviado a {lead.telefono} (#{lead.seguimientos_enviados + 1})")

        except Exception as e:
            logger.error(f"Error enviando seguimiento a {lead.telefono}: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await inicializar_db()
    logger.info("Base de datos inicializada")

    scheduler.add_job(
        enviar_seguimientos_programados,
        trigger="interval",
        hours=24,
        id="seguimientos",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(f"Scheduler iniciado — seguimientos cada 24h")
    logger.info(f"Servidor corriendo en puerto {PORT} | BASE_URL: {BASE_URL}")

    yield
    scheduler.shutdown()


app = FastAPI(
    title="AgentKit — IAD México WhatsApp Agent",
    version="1.0.0",
    lifespan=lifespan,
)

# Servir archivos de media públicamente
if os.path.exists("media"):
    app.mount("/media", StaticFiles(directory="media"), name="media")


@app.get("/")
async def health_check():
    return {"status": "ok", "service": "agentkit-iad-mexico"}


@app.get("/webhook")
async def webhook_verificacion(request: Request):
    resultado = await proveedor.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}


@app.post("/webhook")
async def webhook_handler(request: Request):
    """
    Recibe mensajes de WhatsApp, genera respuesta con IA y la envía.
    Si la respuesta incluye [ARCHIVO:xxx], envía el archivo correspondiente.
    """
    try:
        mensajes = await proveedor.parsear_webhook(request)

        for msg in mensajes:
            if msg.es_propio or not msg.texto:
                continue

            logger.info(f"Mensaje de {msg.telefono}: {msg.texto[:80]}")

            historial = await obtener_historial(msg.telefono)
            respuesta_cruda = await generar_respuesta(msg.texto, historial)

            # Extraer etiquetas de archivo y limpiar el texto
            respuesta_texto, archivos_a_enviar = extraer_archivos(respuesta_cruda)

            # Guardar conversación
            await guardar_mensaje(msg.telefono, "user", msg.texto)
            await guardar_mensaje(msg.telefono, "assistant", respuesta_texto)

            # Registrar/actualizar lead
            interes = calificar_interes(msg.texto)
            estado = estado_desde_interes(interes)
            await registrar_o_actualizar_lead(telefono=msg.telefono, estado=estado)

            # Enviar respuesta de texto
            await proveedor.enviar_mensaje(msg.telefono, respuesta_texto)

            # Enviar archivos si el agente los solicitó
            if archivos_a_enviar:
                await enviar_archivos(msg.telefono, archivos_a_enviar)

            logger.info(f"Respuesta enviada a {msg.telefono} | interés: {interes} | archivos: {archivos_a_enviar}")

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
