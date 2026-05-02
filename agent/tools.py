# agent/tools.py — Herramientas específicas de IAD México

import logging
from datetime import datetime

logger = logging.getLogger("agentkit")

# ── Información del proyecto ──────────────────────────────────────────────────

INFO_PROYECTO = {
    "precio_accion": 550000,
    "total_acciones": 125,
    "roi_anual": "30-35%",
    "recuperacion": "~3 años",
    "ubicacion": "Plaza La Isla, Puerto Vallarta",
    "apertura": "Diciembre 2025",
    "avance": "60%+",
}

MENSAJES_SEGUIMIENTO = [
    "Hola {nombre}, soy Juan Ramirez de IAD México. 👋 Hace unos días platicamos sobre el Acuario Vallarta. ¿Pudiste revisar la información? Quería ver si tienes alguna pregunta o si te gustaría agendar una llamada para contarte más detalles del proyecto.",
    "Hola {nombre}, te escribo nuevamente de IAD México. El Acuario Vallarta avanza muy bien — ya llevamos más del 60% de construcción con apertura en diciembre. Es una de las últimas oportunidades de entrar antes del lanzamiento. ¿Tienes 15 minutos esta semana para una llamada rápida?",
    "Hola {nombre}, soy Juan de IAD México. Solo quería compartirte que las acciones del Acuario Vallarta se están colocando rápidamente. Con un ROI proyectado del 30-35% anual y apertura en diciembre, muchos inversionistas ya han tomado su lugar. ¿Te puedo enviar la presentación completa?",
    "Hola {nombre}, Juan Ramirez de IAD México. Entiendo que tienes muchas cosas en mente. Si en algún momento quieres explorar esta oportunidad con más calma, aquí estoy para ayudarte. La inversión mínima es de $550,000 MXN con rendimientos semestrales desde el primer año de operación.",
    "Hola {nombre}, último mensaje de mi parte para no saturarte. Si en el futuro te interesa conocer más sobre el Acuario Vallarta u otras oportunidades de IAD México, con gusto te atiendo. ¡Éxito en tus inversiones!",
]


def obtener_mensaje_seguimiento(nombre: str, numero_seguimiento: int) -> str:
    """Genera el mensaje de seguimiento correspondiente según cuántos ya se enviaron."""
    nombre_display = nombre or "estimado inversionista"
    idx = min(numero_seguimiento, len(MENSAJES_SEGUIMIENTO) - 1)
    return MENSAJES_SEGUIMIENTO[idx].format(nombre=nombre_display)


def calificar_interes(texto: str) -> str:
    """
    Analiza el texto de un mensaje para determinar el nivel de interés del prospecto.
    Retorna: 'alto' | 'medio' | 'bajo' | 'ninguno'
    """
    texto = texto.lower()

    keywords_alto = [
        "quiero invertir", "me interesa", "cuándo podemos", "cómo invierto",
        "quiero participar", "me anoto", "precio", "cuánto", "tengo el dinero",
        "cuándo abren", "agenda", "reunión", "llamada", "presentación"
    ]
    keywords_medio = [
        "interesante", "cuéntame más", "más información", "rendimiento",
        "cómo funciona", "qué incluye", "seguridad", "riesgo", "plazo"
    ]
    keywords_bajo = [
        "quizás", "tal vez", "lo pensaré", "después", "no sé",
        "no tengo", "poco dinero", "muy caro"
    ]
    keywords_ninguno = [
        "no me interesa", "gracias no", "ya tengo", "baja mis datos",
        "no quiero", "spam", "cancelar"
    ]

    for kw in keywords_ninguno:
        if kw in texto:
            return "ninguno"
    for kw in keywords_alto:
        if kw in texto:
            return "alto"
    for kw in keywords_medio:
        if kw in texto:
            return "medio"
    for kw in keywords_bajo:
        if kw in texto:
            return "bajo"

    return "medio"


def estado_desde_interes(interes: str) -> str:
    """Convierte nivel de interés en estado del lead."""
    mapa = {
        "alto": "calificado",
        "medio": "interesado",
        "bajo": "contactado",
        "ninguno": "descartado",
    }
    return mapa.get(interes, "contactado")


def esta_en_horario_atencion() -> bool:
    """IAD México atiende 24/7."""
    return True


def obtener_info_cita() -> str:
    """Retorna el mensaje para agendar una cita con el equipo."""
    return (
        "Para agendar una reunión con nuestro equipo de asesores, "
        "puedo coordinar una llamada o videollamada a tu conveniencia. "
        "¿Qué día y horario te funciona mejor?"
    )


def obtener_resumen_proyecto() -> str:
    """Retorna un resumen rápido del proyecto para compartir."""
    return (
        f"*Acuario Vallarta — Oportunidad de Inversión*\n\n"
        f"📍 Ubicación: {INFO_PROYECTO['ubicacion']}\n"
        f"💰 Precio por acción: ${INFO_PROYECTO['precio_accion']:,} MXN\n"
        f"📈 ROI proyectado: {INFO_PROYECTO['roi_anual']} anual\n"
        f"⏱ Recuperación: {INFO_PROYECTO['recuperacion']}\n"
        f"🏗 Avance: {INFO_PROYECTO['avance']} completado\n"
        f"🎯 Apertura: {INFO_PROYECTO['apertura']}\n\n"
        f"¿Te gustaría agendar una llamada para conocer todos los detalles?"
    )
