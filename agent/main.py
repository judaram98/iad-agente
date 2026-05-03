# agent/main.py — Servidor FastAPI + Webhook + Scheduler de seguimientos

import json as _json
import os
import re
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

# Forzar la carga y validación de variables de entorno al arranque
from agent.config import settings

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
from agent.kommo_sync import sincronizar_con_kommo
from config.etapas import es_etapa_congelada
from services.queue import enqueue, iniciar_worker, detener_worker

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


async def _lead_congelado_en_kommo(telefono: str) -> bool:
    """
    Retorna True si el lead del teléfono está en una etapa congelada en Kommo.
    Retorna False si Kommo no está configurado, el lead no existe, o la llamada falla.
    """
    if not settings.KOMMO_PIPELINE_ID:
        return False
    try:
        from services.kommo import searchContactsByPhone, getLead, KommoError
        contactos = await searchContactsByPhone(telefono)
        if not contactos:
            return False
        for ref in contactos[0].get("_embedded", {}).get("leads", []):
            try:
                ld = await getLead(ref["id"])
                if ld.get("pipeline_id") == settings.KOMMO_PIPELINE_ID:
                    return es_etapa_congelada(ld.get("status_id", 0))
            except KommoError:
                continue
    except Exception:
        pass
    return False


async def enviar_seguimientos_programados():
    """Tarea automática: envía seguimientos a leads sin contacto reciente."""
    logger.info("Ejecutando seguimientos automáticos...")
    leads = await obtener_leads_para_seguimiento(dias_sin_contacto=FOLLOWUP_DIAS)

    if not leads:
        logger.info("No hay leads pendientes de seguimiento.")
        return

    for lead in leads:
        try:
            if await _lead_congelado_en_kommo(lead.telefono):
                logger.info(f"Seguimiento omitido — {lead.telefono} en etapa congelada en Kommo")
                continue

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

    await iniciar_worker()

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
    await detener_worker()


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

            # Registrar/actualizar lead en BD local
            interes = calificar_interes(msg.texto)
            estado = estado_desde_interes(interes)
            await registrar_o_actualizar_lead(telefono=msg.telefono, estado=estado)

            # Enviar respuesta de texto
            await proveedor.enviar_mensaje(msg.telefono, respuesta_texto)

            # Sincronizar con Kommo CRM (no bloquea si falla)
            await sincronizar_con_kommo(
                telefono=msg.telefono,
                nombre=None,
                interes=interes,
            )

            # Enviar archivos si el agente los solicitó
            if archivos_a_enviar:
                await enviar_archivos(msg.telefono, archivos_a_enviar)

            logger.info(f"Respuesta enviada a {msg.telefono} | interés: {interes} | archivos: {archivos_a_enviar}")

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Webhooks de Kommo ─────────────────────────────────────────────────────────

def _to_int(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except (ValueError, TypeError):
        return None


def _parse_kommo_form(body: bytes) -> dict:
    """
    Parsea el payload URL-encoded de Kommo (application/x-www-form-urlencoded).

    Kommo NO envía JSON — envía form-data con notación de brackets:
      message[add][0][text]=Hola
      leads[status][0][status_id]=105360847

    Retorna un dict normalizado con 'messages' y 'leads'.
    """
    from urllib.parse import parse_qsl
    flat = dict(parse_qsl(body.decode("utf-8", errors="replace"), keep_blank_values=True))

    # ── Mensajes (message[add][N][...]) ──────────────────────────────────────
    messages = []
    i = 0
    while f"message[add][{i}][id]" in flat:
        messages.append({
            "id":          flat.get(f"message[add][{i}][id]"),
            "entity_id":   _to_int(flat.get(f"message[add][{i}][entity_id]")),   # lead_id
            "talk_id":     _to_int(flat.get(f"message[add][{i}][talk_id]")),
            "contact_id":  _to_int(flat.get(f"message[add][{i}][contact_id]")),
            "text":        flat.get(f"message[add][{i}][text]", ""),
            "type":        flat.get(f"message[add][{i}][type]", ""),  # incoming | outgoing
            "origin":      flat.get(f"message[add][{i}][origin]", ""),
            "author_name": flat.get(f"message[add][{i}][author][name]", ""),
            "author_type": flat.get(f"message[add][{i}][author][type]", ""),
            "created_at":  _to_int(flat.get(f"message[add][{i}][created_at]")),
        })
        i += 1

    # ── Cambios de etapa (leads[status][N][...]) ──────────────────────────────
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


def _validar_secret_kommo(request: Request, body_bytes: bytes) -> bool:
    """
    Acepta el webhook si KOMMO_WEBHOOK_SECRET aparece en:
      1. Query param  ?secret=...   ← el que usa Kommo (lo pones en la URL)
      2. Header       X-Kommo-Signature
    Sin secret configurado acepta todo (solo útil en dev local).
    """
    secret = settings.KOMMO_WEBHOOK_SECRET
    if not secret:
        return True

    if request.query_params.get("secret") == secret:
        return True
    if request.headers.get("X-Kommo-Signature") == secret:
        return True
    return False


@app.post("/webhooks/kommo/chat")
async def webhook_kommo_chat(request: Request):
    """
    Recibe mensajes de chat de Kommo (cliente escribió en el Talk).
    Kommo envía form-data URL-encoded, no JSON.
    Responde 200 inmediato — Kommo no reintenta.
    """
    body_bytes = await request.body()

    if not _validar_secret_kommo(request, body_bytes):
        logger.warning("Webhook Kommo /chat rechazado — secret inválido")
        raise HTTPException(status_code=403, detail="Forbidden")

    payload = _parse_kommo_form(body_bytes)

    for msg in payload["messages"]:
        logger.info(
            f"Kommo /chat | lead={msg['entity_id']} tipo={msg['type']} "
            f"autor='{msg['author_name']}' texto='{msg['text'][:60]}'"
        )

    await enqueue("kommo_chat", payload)
    return {"status": "ok"}


@app.post("/webhooks/kommo/lead")
async def webhook_kommo_lead(request: Request):
    """
    Recibe cambios de etapa / creación de leads en Kommo.
    Kommo envía form-data URL-encoded, no JSON.
    Responde 200 inmediato — Kommo no reintenta.
    """
    body_bytes = await request.body()

    if not _validar_secret_kommo(request, body_bytes):
        logger.warning("Webhook Kommo /lead rechazado — secret inválido")
        raise HTTPException(status_code=403, detail="Forbidden")

    payload = _parse_kommo_form(body_bytes)

    for ev in payload["leads"]:
        logger.info(
            f"Kommo /lead | lead={ev['id']} pipeline={ev['pipeline_id']} "
            f"{ev['old_status_id']} → {ev['status_id']}"
        )

    await enqueue("kommo_lead", payload)
    return {"status": "ok"}
