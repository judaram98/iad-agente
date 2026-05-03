# config/etapas.py — Mapa central de etapas del pipeline "IA" en Kommo
#
# IDs obtenidos con: .venv/bin/python scripts/probar_kommo.py
# Pipeline: "IA"  |  KOMMO_PIPELINE_ID = 13652595
#
# REGLA: antes de mover un lead o enviarle un mensaje automático,
# verifica con es_etapa_congelada(status_id). Si retorna True, el agente
# NO debe intervenir — un humano ya está manejando esa conversación.

from typing import Final

# ── IDs de etapas ────────────────────────────────────────────────────────────

LEADS_ENTRANTES:       Final[int] = 105360767
TOQUE_1:               Final[int] = 105360771
TOQUE_2:               Final[int] = 105360847
TOQUE_3:               Final[int] = 105360851
TOQUE_4:               Final[int] = 105360855
TOQUE_5:               Final[int] = 105360859
SIN_PERFILAR_CONTESTO: Final[int] = 105360863
CITA_PRE:              Final[int] = 105360867
CITA_DURANTE_POST:     Final[int] = 105360871
APARTADO:              Final[int] = 105360875
FRIOS:                 Final[int] = 105360879
MAS_ADELANTE:          Final[int] = 105360883
BUSCANDO_DIFERENTE:    Final[int] = 105360887
GANADOS:               Final[int] = 142   # "Logrado con éxito"  (tipo especial Kommo)
BAJA:                  Final[int] = 143   # "Ventas Perdidos"     (tipo especial Kommo)

# ── Etapas congeladas ─────────────────────────────────────────────────────────
#
# Un lead congelado está siendo manejado por un humano o ya cerró su ciclo.
# El agente de IA NO debe:
#   - Mover el lead a otra etapa
#   - Enviarle seguimientos automáticos
#   - Sobrescribir su stage con lógica de interés
#
# Incluye Cita (pre) deliberadamente: una vez que el lead pidió reunión,
# el equipo de ventas se hace cargo. El bot puede responder preguntas si
# escribe, pero no debe moverlo ni hacer seguimiento automático.

ETAPAS_CONGELADAS: Final[frozenset[int]] = frozenset({
    CITA_PRE,           # 105360867 — agendando con ventas
    CITA_DURANTE_POST,  # 105360871 — reunión activa / post-cita
    APARTADO,           # 105360875 — ya reservó su lugar
    BUSCANDO_DIFERENTE, # 105360887 — descartado pero educadamente
    GANADOS,            # 142        — cerrado ganado
    BAJA,               # 143        — cerrado perdido
})


# ── Guardia de seguridad ──────────────────────────────────────────────────────

def es_etapa_congelada(status_id: int) -> bool:
    """
    Retorna True si el lead está en una etapa donde el agente NO debe actuar.
    Úsala antes de cualquier moveLeadToStage(), addTagToLead() automático,
    o envío de seguimiento programado.

    Ejemplo:
        lead = await getLead(lead_id)
        if es_etapa_congelada(lead["status_id"]):
            return  # humano al mando, no tocar
    """
    return status_id in ETAPAS_CONGELADAS


# ── Nombre legible de cada etapa ─────────────────────────────────────────────

NOMBRE_ETAPA: Final[dict[int, str]] = {
    LEADS_ENTRANTES:       "Leads Entrantes",
    TOQUE_1:               "IA - Toque 1",
    TOQUE_2:               "IA - Toque 2",
    TOQUE_3:               "IA - Toque 3",
    TOQUE_4:               "IA - Toque 4",
    TOQUE_5:               "IA - Toque 5",
    SIN_PERFILAR_CONTESTO: "IA - Sin perfilar / Contestó",
    CITA_PRE:              "IA - Cita (pre)",
    CITA_DURANTE_POST:     "Cita (durante y post)",
    APARTADO:              "Apartado",
    FRIOS:                 "IA - Fríos",
    MAS_ADELANTE:          "IA - Más adelante",
    BUSCANDO_DIFERENTE:    "IA - Buscando algo diferente",
    GANADOS:               "Logrado con éxito",
    BAJA:                  "Ventas Perdidos",
}


# ── Helpers de clasificación ──────────────────────────────────────────────────

def etapa_siguiente_por_interes(interes: str, etapa_actual: int) -> int | None:
    """
    Dado el nivel de interés detectado y la etapa actual del lead,
    retorna la etapa destino o None si no debe moverse.

    Reglas:
    - "ninguno"  → FRIOS (siempre, aunque el lead esté adelantado)
    - "alto"     → CITA_PRE (solo si la etapa actual tiene prioridad menor)
    - cualquier  → SIN_PERFILAR_CONTESTO (si el lead aún está en las etapas iniciales)
    - None       → no mover
    """
    if es_etapa_congelada(etapa_actual):
        return None

    _PRIORIDAD = {
        LEADS_ENTRANTES:       1,
        TOQUE_1:               2,
        TOQUE_2:               3,
        TOQUE_3:               4,
        TOQUE_4:               5,
        TOQUE_5:               6,
        SIN_PERFILAR_CONTESTO: 7,
        CITA_PRE:              8,
        CITA_DURANTE_POST:     9,
        # Frios / Mas adelante / Buscando diferente no tienen prioridad ordinal
    }

    prioridad_actual = _PRIORIDAD.get(etapa_actual, 0)

    if interes == "ninguno":
        return FRIOS

    if interes == "alto":
        if prioridad_actual < _PRIORIDAD[CITA_PRE]:
            return CITA_PRE
        return None  # ya está en CITA_PRE o más adelante

    # interes medio/bajo: avanzar solo si aún está en las 2 primeras etapas
    if prioridad_actual <= _PRIORIDAD[TOQUE_1]:
        return SIN_PERFILAR_CONTESTO

    return None  # no mover
