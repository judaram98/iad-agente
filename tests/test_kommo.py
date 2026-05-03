# tests/test_kommo.py — Pruebas para services/kommo.py
#
# Verifica tres comportamientos críticos del cliente HTTP:
#   1. Rate limiter: espacía las requests para no superar el límite de Kommo.
#   2. 429 → retry con backoff exponencial hasta _MAX_RETRIES intentos.
#   3. 403 → KommoForbiddenError inmediato, sin retry.

import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.kommo as kommo_mod
from services.kommo import (
    _req,
    _MAX_RETRIES,
    KommoRateLimitError,
    KommoForbiddenError,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resp(status: int, data: dict | None = None):
    """Crea un mock de respuesta httpx con el status dado."""
    r = MagicMock()
    r.status_code = status
    r.content     = b'{"ok":1}' if data is not None else b''
    r.json.return_value = data or {}
    r.raise_for_status   = MagicMock()
    return r


@contextmanager
def _mock_httpx(responses: list):
    """
    Context manager que parchea httpx.AsyncClient para devolver
    las respuestas de la lista en orden, sin hacer llamadas reales.
    """
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__  = AsyncMock(return_value=False)
    mock_client.request    = AsyncMock(side_effect=responses)
    with patch("httpx.AsyncClient", return_value=mock_client):
        yield mock_client


@pytest.fixture(autouse=True)
def reset_throttle():
    """
    Resetea el estado del rate limiter antes de cada test para que el primer
    request no tenga que esperar el intervalo del test anterior.
    """
    kommo_mod._throttle._last = 0.0


# ── Tests de HTTP básico ──────────────────────────────────────────────────────

async def test_200_devuelve_datos():
    """Happy path: una respuesta 200 retorna el JSON directamente."""
    with _mock_httpx([_resp(200, {"id": 99})]) as mc:
        with patch("services.kommo.asyncio.sleep", AsyncMock()):
            result = await _req("GET", "leads/99")

    assert result == {"id": 99}
    assert mc.request.call_count == 1


async def test_429_reintenta_y_recupera():
    """
    Dos respuestas 429 seguidas de un 200 → éxito en el tercer intento.
    El cliente debe reintentar exactamente _MAX_RETRIES veces antes de rendirse,
    pero si recupera antes, retorna con éxito.
    """
    responses = [_resp(429), _resp(429), _resp(200, {"id": 1})]
    with _mock_httpx(responses) as mc:
        with patch("services.kommo.asyncio.sleep", AsyncMock()):
            result = await _req("GET", "leads/1")

    assert result == {"id": 1}
    assert mc.request.call_count == 3


async def test_429_agota_reintentos_lanza_error():
    """
    Tres respuestas 429 consecutivas deben agotar los reintentos
    y lanzar KommoRateLimitError.
    """
    with _mock_httpx([_resp(429)] * _MAX_RETRIES) as mc:
        with patch("services.kommo.asyncio.sleep", AsyncMock()):
            with pytest.raises(KommoRateLimitError):
                await _req("GET", "leads/1")

    assert mc.request.call_count == _MAX_RETRIES


async def test_429_backoff_crece_exponencialmente():
    """
    Los sleeps de retry deben ser 2^0=1s, 2^1=2s, … (backoff exponencial).
    """
    sleep_calls = []

    async def mock_sleep(seconds):
        sleep_calls.append(seconds)

    with _mock_httpx([_resp(429)] * _MAX_RETRIES):
        with patch("services.kommo.asyncio.sleep", side_effect=mock_sleep):
            with pytest.raises(KommoRateLimitError):
                await _req("GET", "leads/1")

    # Filtra solo los sleeps de retry (≥ 1s) para ignorar posibles waits del rate limiter
    retry_sleeps = [s for s in sleep_calls if s >= 1]
    assert len(retry_sleeps) >= 2, f"Se esperaban ≥2 sleeps de retry, hubo: {sleep_calls}"
    # Cada sleep debe ser mayor o igual al anterior (backoff creciente)
    for i in range(1, len(retry_sleeps)):
        assert retry_sleeps[i] >= retry_sleeps[i - 1], \
            f"Backoff no crece: {retry_sleeps}"


async def test_403_no_reintenta():
    """
    403 Forbidden debe lanzar KommoForbiddenError en el primer intento,
    sin reintentar (los tokens revocados no se curan solos).
    """
    with _mock_httpx([_resp(403)]) as mc:
        with patch("services.kommo.asyncio.sleep", AsyncMock()):
            with pytest.raises(KommoForbiddenError):
                await _req("GET", "leads/1")

    assert mc.request.call_count == 1, \
        f"403 no debe reintentar — se hicieron {mc.request.call_count} llamadas"


# ── Test del rate limiter ─────────────────────────────────────────────────────

async def test_rate_limiter_espaciado_entre_requests():
    """
    Cuatro requests concurrentes deben quedar espaciadas por el rate limiter.
    Cada par de requests consecutivas debe separarse al menos 0.13 s
    (el intervalo teórico es 1/6 ≈ 0.167 s).
    """
    timestamps = []

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__  = AsyncMock(return_value=False)

    async def mock_request(*args, **kwargs):
        timestamps.append(asyncio.get_event_loop().time())
        return _resp(200, {})

    mock_client.request = mock_request  # función async, no AsyncMock (necesita timestamp real)

    with patch("httpx.AsyncClient", return_value=mock_client):
        # Las 4 coroutines compiten por el rate limiter
        await asyncio.gather(*[_req("GET", f"leads/{i}") for i in range(4)])

    assert len(timestamps) == 4, "Se esperaban 4 requests completadas"

    intervals = [timestamps[i + 1] - timestamps[i] for i in range(3)]
    for idx, gap in enumerate(intervals):
        assert gap >= 0.13, (
            f"Intervalo [{idx}] = {gap:.3f}s — debería ser ≥ 0.13s "
            f"(rate limiter: 6 req/s = 0.167 s/req)"
        )
