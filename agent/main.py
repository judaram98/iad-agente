# agent/main.py — Servidor FastAPI + Webhook + Scheduler de seguimientos

import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from agent.brain import generar_respuesta
from agent.memory import (
    inicializar_db, guardar_mensaje, obtener_historial,
    registrar_o_actualizar_lead, obtener_leads_para_seguimiento, incrementar_seguimiento,
)
from agent.providers import obtener_proveedor
from agent.tools import calificar_interes, estado_desde_interes, obtener_mensaje_seguimiento

load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("agentkit")

proveedor = obtener_proveedor()
PORT = int(os.getenv("PORT", 8000))
FOLLOWUP_DIAS = int(os.getenv("FOLLOWUP_DIAS", 3))

scheduler = AsyncIOScheduler()


async def enviar_seguimientos_programados():
    """
    Tarea automática: busca leads sin contacto reciente y les envía un mensaje de seguimiento.
    Se ejecuta cada 24 horas.
    """
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
            else:
                logger.warning(f"No se pudo enviar seguimiento a {lead.telefono}")

        except Exception as e:
            logger.error(f"Error enviando seguimiento a {lead.telefono}: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa la base de datos y el scheduler al arrancar."""
    await inicializar_db()
    logger.info("Base de datos inicializada")

    # Scheduler de seguimientos: corre cada 24 horas
    scheduler.add_job(
        enviar_seguimientos_programados,
        trigger="interval",
        hours=24,
        id="seguimientos",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(f"Scheduler iniciado — seguimientos cada 24h (leads sin contacto >{FOLLOWUP_DIAS} días)")
    logger.info(f"Servidor AgentKit corriendo en puerto {PORT}")
    logger.info(f"Proveedor de WhatsApp: {proveedor.__class__.__name__}")

    yield

    scheduler.shutdown()


app = FastAPI(
    title="AgentKit — IAD México WhatsApp Agent",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
async def health_check():
    return {"status": "ok", "service": "agentkit-iad-mexico"}


@app.get("/webhook")
async def webhook_verificacion(request: Request):
    """Verificación GET del webhook (requerido por Meta, no-op para Whapi)."""
    resultado = await proveedor.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}


@app.post("/webhook")
async def webhook_handler(request: Request):
    """
    Recibe mensajes de WhatsApp, genera respuesta con IA y la envía de vuelta.
    También registra y califica el lead automáticamente.
    """
    try:
        mensajes = await proveedor.parsear_webhook(request)

        for msg in mensajes:
            if msg.es_propio or not msg.texto:
                continue

            logger.info(f"Mensaje de {msg.telefono}: {msg.texto[:80]}")

            # Obtener historial antes de guardar (evita duplicados en el contexto)
            historial = await obtener_historial(msg.telefono)

            # Generar respuesta con IA
            respuesta = await generar_respuesta(msg.texto, historial)

            # Guardar conversación
            await guardar_mensaje(msg.telefono, "user", msg.texto)
            await guardar_mensaje(msg.telefono, "assistant", respuesta)

            # Registrar/actualizar lead con su nivel de interés
            interes = calificar_interes(msg.texto)
            estado = estado_desde_interes(interes)
            await registrar_o_actualizar_lead(
                telefono=msg.telefono,
                estado=estado,
            )

            # Enviar respuesta por WhatsApp
            await proveedor.enviar_mensaje(msg.telefono, respuesta)
            logger.info(f"Respuesta enviada a {msg.telefono} | interés: {interes}")

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
