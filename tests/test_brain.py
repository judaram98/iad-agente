# tests/test_brain.py — Pruebas para agent/brain.py
#
# La guardia más importante del sistema: cuando el lead está en una etapa
# congelada, el cerebro debe devolver None y NO generar ninguna respuesta.
# Esto evita que el bot "pisotee" conversaciones manejadas por humanos.

from unittest.mock import AsyncMock, patch

import pytest

from agent.brain import procesar_mensaje_kommo
from config.etapas import (
    CITA_PRE,
    GANADOS,
    BAJA,
    APARTADO,
    CITA_DURANTE_POST,
    BUSCANDO_DIFERENTE,
    LEADS_ENTRANTES,
    TOQUE_1,
    SIN_PERFILAR_CONTESTO,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lead(status_id: int, lead_id: int = 42, nombre: str = "Test Lead") -> dict:
    """Construye un dict mínimo que simula la respuesta de getLead."""
    return {
        "id":       lead_id,
        "name":     nombre,
        "status_id": status_id,
        "_embedded": {"tags": [], "contacts": []},
        "custom_fields_values": [],
    }


# ── Tests: guardia de etapa congelada ─────────────────────────────────────────

@pytest.mark.parametrize("status_id,nombre_etapa", [
    (CITA_PRE,          "Cita (pre)"),
    (CITA_DURANTE_POST, "Cita (durante y post)"),
    (APARTADO,          "Apartado"),
    (BUSCANDO_DIFERENTE,"Buscando diferente"),
    (GANADOS,           "Ganado"),
    (BAJA,              "Baja"),
])
async def test_etapa_congelada_retorna_none(status_id, nombre_etapa):
    """
    GUARDIA CRÍTICA: para cualquier etapa congelada, procesar_mensaje_kommo
    debe devolver None — el bot no interfiere con leads en manos de humanos.
    """
    with patch("services.kommo.getLead", AsyncMock(return_value=_lead(status_id))):
        result = await procesar_mensaje_kommo(42, "hola", [])

    assert result is None, (
        f"Etapa '{nombre_etapa}' ({status_id}) debería silenciar al bot, "
        f"pero se devolvió: {result!r}"
    )


# ── Tests: etapas activas generan respuesta ───────────────────────────────────

@pytest.mark.parametrize("status_id", [LEADS_ENTRANTES, TOQUE_1, SIN_PERFILAR_CONTESTO])
async def test_etapa_activa_genera_respuesta(status_id):
    """
    En etapas activas el bot SÍ debe responder (retorno no-None y no vacío).
    Mockeamos _loop_tools para no llamar a la API de Groq.
    """
    with patch("services.kommo.getLead", AsyncMock(return_value=_lead(status_id))):
        with patch("agent.brain._loop_tools", AsyncMock(return_value="Respuesta de prueba")):
            result = await procesar_mensaje_kommo(42, "¿qué opciones tienen?", [])

    assert result is not None, f"Etapa {status_id}: el bot debería haber respondido"
    assert len(result.strip()) > 0, "La respuesta no debe estar vacía"


async def test_mensaje_muy_corto_devuelve_fallback():
    """
    Mensajes de menos de 2 caracteres deben devolver el fallback inmediatamente
    sin consultar Kommo ni llamar al modelo.
    """
    mock_get_lead = AsyncMock()
    with patch("services.kommo.getLead", mock_get_lead):
        result = await procesar_mensaje_kommo(42, "k", [])

    # El fallback se devuelve antes de consultar el lead
    mock_get_lead.assert_not_called()
    assert result is not None
    assert len(result) > 0


async def test_kommo_error_responde_sin_contexto():
    """
    Si getLead falla (ej: token expirado), el bot no debe bloquearse —
    debe responder sin contexto de CRM en lugar de lanzar una excepción.
    """
    from services.kommo import KommoError

    with patch("services.kommo.getLead", AsyncMock(side_effect=KommoError("timeout"))):
        with patch("agent.brain._loop_tools", AsyncMock(return_value="Respuesta sin contexto")):
            result = await procesar_mensaje_kommo(42, "¿tienen departamentos?", [])

    # No debe propagarse la excepción — el bot responde de todas formas
    assert result is not None
    assert len(result.strip()) > 0


async def test_contexto_lead_se_inyecta_en_system_prompt():
    """
    El contexto del lead (etapa, datos) debe aparecer reflejado en los mensajes
    que se pasan a _loop_tools (y por ende al modelo).
    """
    lead = _lead(LEADS_ENTRANTES)
    lead["name"] = "Juan Pérez"
    captura = {}

    async def capturar_mensajes(mensajes, lead_id):
        captura["mensajes"] = mensajes
        return "ok"

    with patch("services.kommo.getLead", AsyncMock(return_value=lead)):
        with patch("agent.brain._loop_tools", side_effect=capturar_mensajes):
            await procesar_mensaje_kommo(99, "hola", [])

    assert "mensajes" in captura, "_loop_tools no fue llamado"
    system_content = captura["mensajes"][0]["content"]

    # El system prompt debe contener el nombre del lead y su etapa
    assert "Juan Pérez" in system_content, "El nombre del lead no está en el system prompt"
    assert "Leads Entrantes" in system_content, "La etapa no está en el system prompt"
