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
    """
    Lógica de procesamiento de cada evento.

    Por ahora loggea el contenido estructurado.
    Aquí se conectará la respuesta automática vía Kommo (Etapa 4).
    """
    tipo = item["tipo"]
    payload = item["payload"]
    lag_ms = round((time.monotonic() - item["enqueued_at"]) * 1000)

    if tipo == "kommo_chat":
        for msg in payload.get("messages", []):
            logger.info(
                "[QUEUE] kommo_chat | "
                f"lead={msg.get('entity_id')} "
                f"tipo={msg.get('type')} "
                f"autor='{msg.get('author_name', '')}' "
                f"texto='{msg.get('text', '')[:80]}' "
                f"lag={lag_ms}ms"
            )

    elif tipo == "kommo_lead":
        for ev in payload.get("leads", []):
            logger.info(
                "[QUEUE] kommo_lead | "
                f"lead={ev.get('id')} "
                f"pipeline={ev.get('pipeline_id')} "
                f"{ev.get('old_status_id')} → {ev.get('status_id')} "
                f"lag={lag_ms}ms"
            )

    else:
        logger.warning(f"[QUEUE] tipo desconocido={tipo} | {item}")
