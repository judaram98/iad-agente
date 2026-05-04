# services/queue.py — Cola FIFO in-memory para webhooks de Kommo
#
# Garantiza que el servidor responda 200 en < 1s a Kommo (que no reintenta),
# mientras el procesamiento real ocurre de forma asíncrona y sin presión.
#
# Migración futura: reemplazar _q por una cola Redis (rq/arq/dramatiq)
# sin tocar la interfaz pública — solo enqueue() y _procesar().

import asyncio
import logging
import time

logger = logging.getLogger("queue")

_q: asyncio.Queue[dict] = asyncio.Queue()
_worker_task: asyncio.Task | None = None


# ── Interfaz pública ──────────────────────────────────────────────────────────

async def enqueue(tipo: str, payload: dict) -> None:
    """Encola un evento para procesamiento asíncrono. No bloquea."""
    await _q.put({
        "tipo": tipo,
        "payload": payload,
        "enqueued_at": time.monotonic(),
    })
    logger.debug(f"[QUEUE] encolado tipo={tipo} qsize={_q.qsize()}")


async def iniciar_worker() -> None:
    """Arranca el worker en background. Llamar desde lifespan de FastAPI."""
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker(), name="kommo-queue-worker")
        logger.info("[QUEUE] Worker iniciado")


async def detener_worker() -> None:
    """Cancela el worker. Llamar al cerrar la app."""
    global _worker_task
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
    logger.info("[QUEUE] Worker detenido")


# ── Worker ────────────────────────────────────────────────────────────────────

async def _worker() -> None:
    """
    Consume la cola FIFO procesando un item a la vez.
    El procesamiento serial evita saturar la API de Kommo.
    """
    while True:
        item = await _q.get()
        try:
            await _procesar(item)
        except Exception as e:
            logger.error(f"[QUEUE] Error procesando item: {e} | item={item}")
        finally:
            _q.task_done()


# ── Procesador ────────────────────────────────────────────────────────────────

async def _procesar(item: dict) -> None:
    tipo = item["tipo"]
    payload = item["payload"]
    lag_ms = round((time.monotonic() - item["enqueued_at"]) * 1000)

    if tipo == "kommo_chat":
        await _procesar_kommo_chat(payload, lag_ms)

    elif tipo == "kommo_lead":
        for ev in payload.get("leads", []):
            logger.info(
                "[QUEUE] kommo_lead | "
                f"lead={ev.get('id')} pipeline={ev.get('pipeline_id')} "
                f"{ev.get('old_status_id')} → {ev.get('status_id')} "
                f"lag={lag_ms}ms"
            )

    else:
        logger.warning(f"[QUEUE] tipo desconocido={tipo} | {item}")


async def _procesar_kommo_chat(payload: dict, lag_ms: int) -> None:
    """Pipeline completo para mensajes de chat de Kommo."""
    from agent.brain import procesar_mensaje_kommo
    from agent.memory import guardar_mensaje, obtener_historial, registrar_o_actualizar_lead
    from agent.tools import calificar_interes, estado_desde_interes
    from agent.kommo_sync import sincronizar_con_kommo
    from services.kommo import sendChatMessage, KommoError

    for msg in payload.get("mensajes", []):
        lead_id = msg.get("lead_id")
        texto = msg.get("texto", "")
        es_propio = msg.get("es_propio", False)
        telefono = msg.get("telefono", str(lead_id))  # str(lead_id) en modo Kommo

        if es_propio or not texto:
            continue

        logger.info(f"[worker] procesando mensaje lead_id={lead_id} text='{texto[:80]}' lag={lag_ms}ms")

        try:
            historial = await obtener_historial(telefono)

            respuesta = await procesar_mensaje_kommo(lead_id, texto, historial)

            if respuesta is None:
                # Lead en etapa congelada — silenciar
                continue

            await guardar_mensaje(telefono, "user", texto)
            await guardar_mensaje(telefono, "assistant", respuesta)

            interes = calificar_interes(texto)
            estado = estado_desde_interes(interes)
            await registrar_o_actualizar_lead(telefono=telefono, estado=estado)

            try:
                await sendChatMessage(lead_id, respuesta)
                logger.info(f"[kommo] sendChatMessage lead_id={lead_id} → ok | interés: {interes}")
            except KommoError as e:
                logger.error(f"[kommo] sendChatMessage lead_id={lead_id} → ERROR: {e}")

            await sincronizar_con_kommo(
                telefono=telefono,
                nombre=None,
                interes=interes,
                lead_id=lead_id,
            )

        except Exception as e:
            logger.error(f"[QUEUE] Error procesando kommo_chat lead={lead_id}: {e}")
