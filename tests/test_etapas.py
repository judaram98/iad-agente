# tests/test_etapas.py — Pruebas para config/etapas.py
#
# Verifica que es_etapa_congelada() distinga correctamente entre
# etapas donde el bot debe actuar y etapas donde debe guardar silencio.

from config.etapas import (
    es_etapa_congelada,
    ETAPAS_CONGELADAS,
    NOMBRE_ETAPA,
    LEADS_ENTRANTES,
    TOQUE_1,
    TOQUE_2,
    TOQUE_3,
    TOQUE_4,
    TOQUE_5,
    SIN_PERFILAR_CONTESTO,
    CITA_PRE,
    CITA_DURANTE_POST,
    APARTADO,
    FRIOS,
    MAS_ADELANTE,
    BUSCANDO_DIFERENTE,
    GANADOS,
    BAJA,
)

_ETAPAS_ACTIVAS = [
    LEADS_ENTRANTES,
    TOQUE_1,
    TOQUE_2,
    TOQUE_3,
    TOQUE_4,
    TOQUE_5,
    SIN_PERFILAR_CONTESTO,
    FRIOS,
    MAS_ADELANTE,
]

_ETAPAS_CONGELADAS_LISTA = list(ETAPAS_CONGELADAS)


def test_todas_las_etapas_congeladas_retornan_true():
    """Cada etapa del frozenset ETAPAS_CONGELADAS debe devolver True."""
    for etapa in _ETAPAS_CONGELADAS_LISTA:
        nombre = NOMBRE_ETAPA.get(etapa, str(etapa))
        assert es_etapa_congelada(etapa) is True, \
            f"Etapa '{nombre}' ({etapa}) debería estar congelada"


def test_todas_las_etapas_activas_retornan_false():
    """Las etapas donde el bot sí actúa deben devolver False."""
    for etapa in _ETAPAS_ACTIVAS:
        nombre = NOMBRE_ETAPA.get(etapa, str(etapa))
        assert es_etapa_congelada(etapa) is False, \
            f"Etapa '{nombre}' ({etapa}) NO debería estar congelada"


def test_id_desconocido_no_esta_congelado():
    """Un status_id inexistente (ej: 0, 9999) no debe bloquear al bot."""
    assert es_etapa_congelada(0) is False
    assert es_etapa_congelada(9999999) is False


def test_cierre_y_perdido_estan_congelados():
    """
    GANADOS y BAJA son los IDs especiales de Kommo (142/143).
    El bot nunca debe reactivar un lead cerrado.
    """
    assert es_etapa_congelada(GANADOS) is True, "Lead ganado debe estar congelado"
    assert es_etapa_congelada(BAJA)   is True, "Lead de baja debe estar congelado"


def test_cita_pre_esta_congelada():
    """
    Una vez que el lead pidió cita, ventas se hace cargo.
    El bot puede contestar pero NO debe mover ni hacer seguimiento.
    """
    assert es_etapa_congelada(CITA_PRE) is True


def test_buscando_diferente_esta_congelado():
    """Lead descartado educadamente — el bot no debe insistir."""
    assert es_etapa_congelada(BUSCANDO_DIFERENTE) is True


def test_todas_las_etapas_tienen_nombre_legible():
    """Cada ID de etapa conocido debe tener un nombre en NOMBRE_ETAPA."""
    todos_los_ids = _ETAPAS_ACTIVAS + _ETAPAS_CONGELADAS_LISTA
    for etapa_id in todos_los_ids:
        assert etapa_id in NOMBRE_ETAPA, \
            f"ID {etapa_id} no tiene nombre legible en NOMBRE_ETAPA"
