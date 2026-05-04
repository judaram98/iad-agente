# agent/tools.py — Herramientas específicas de IAD México

import json
import logging
import os
import unicodedata
from datetime import datetime

logger = logging.getLogger("agentkit")

# ── Catálogo de archivos compartibles ────────────────────────────────────────

CATALOGO_ARCHIVOS = {
    "brochure": {
        "nombre": "Brochure Acuario Vallarta.pdf",
        "ruta_media": "brochure.pdf",
        "tipo": "documento",
        "caption": "📋 Brochure oficial del Acuario Vallarta — IAD México",
    },
    "comparativo": {
        "nombre": "Comparativo de Inversión.pdf",
        "ruta_media": "comparativo.pdf",
        "tipo": "documento",
        "caption": "📊 Comparativo: Acuario Vallarta vs Inversión Inmobiliaria Tradicional",
    },
    "requisitos": {
        "nombre": "Requisitos para Invertir.pdf",
        "ruta_media": "requisitos.pdf",
        "tipo": "documento",
        "caption": "✅ Requisitos para convertirte en accionista del Acuario Vallarta",
    },
    "imagenes": {
        "tipo": "imagenes",
        "archivos": [
            {"ruta_media": "imagen1.jpg", "caption": "🏗️ Acuario Vallarta — Vista del proyecto (1/5)"},
            {"ruta_media": "imagen2.jpg", "caption": "🐧 Acuario Vallarta — Pingüinario (2/5)"},
            {"ruta_media": "imagen3.jpg", "caption": "🌊 Acuario Vallarta — Experiencias inmersivas (3/5)"},
            {"ruta_media": "imagen4.jpg", "caption": "📍 Acuario Vallarta — Plaza La Isla (4/5)"},
            {"ruta_media": "imagen5.jpg", "caption": "✨ Acuario Vallarta — Avance de obra (5/5)"},
        ],
    },
}


def obtener_url_archivo(ruta_media: str, base_url: str) -> str:
    """Construye la URL pública de un archivo en /media."""
    return f"{base_url.rstrip('/')}/media/{ruta_media}"


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

def _cargar_seguimientos_yaml() -> list[str] | None:
    """Lee mensajes_seguimiento desde prompts.yaml si existen."""
    try:
        import yaml as _yaml
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            cfg = _yaml.safe_load(f) or {}
        msgs = cfg.get("mensajes_seguimiento")
        if isinstance(msgs, list) and msgs:
            return msgs
    except Exception:
        pass
    return None


def _cargar_nombre_agente() -> str:
    """Lee el nombre del agente desde business.yaml."""
    try:
        import yaml as _yaml
        with open("config/business.yaml", "r", encoding="utf-8") as f:
            biz = _yaml.safe_load(f) or {}
        return biz.get("agente", {}).get("nombre", "Sofía")
    except Exception:
        return "Sofía"


# Mensajes por defecto — se pueden sobreescribir añadiendo
# una clave 'mensajes_seguimiento' (lista de strings) en config/prompts.yaml.
# Placeholders disponibles: {nombre} (cliente), {agente} (nombre del agente).
_MENSAJES_SEGUIMIENTO_DEFAULT = [
    "Hola {nombre}, soy {agente} 👋 Hace unos días platicamos sobre algunas opciones de propiedades. ¿Pudiste revisar la información que te mandé? Con gusto te resuelvo cualquier duda.",
    "Hola {nombre}, te escribo de nuevo. Quería ver si encontraste algo que te llamara la atención o si quieres que te muestre más opciones según tu presupuesto y zona. ¿Cómo ves?",
    "Hola {nombre}, {agente} de nuevo por aquí. Tenemos propiedades disponibles que podrían ajustarse muy bien a lo que buscas. ¿Tienes 10 minutos esta semana para que te cuente más?",
    "Hola {nombre}, entiendo que estás ocupado/a. Solo quiero que sepas que aquí estoy cuando quieras retomar la búsqueda. Sin presión, a tu ritmo.",
    "Hola {nombre}, este es mi último mensaje para no interrumpirte. Si en algún momento quieres buscar propiedades, escríbeme y con gusto te ayudo. ¡Mucho éxito!",
]


def obtener_mensaje_seguimiento(nombre: str, numero_seguimiento: int) -> str:
    """
    Genera el mensaje de seguimiento N para el lead.
    Primero intenta cargar la lista desde config/prompts.yaml (mensajes_seguimiento).
    Si no existe, usa los mensajes por defecto.
    """
    mensajes = _cargar_seguimientos_yaml() or _MENSAJES_SEGUIMIENTO_DEFAULT
    nombre_display = nombre or "estimado cliente"
    agente_display = _cargar_nombre_agente()
    idx = min(numero_seguimiento, len(mensajes) - 1)
    return mensajes[idx].format(nombre=nombre_display, agente=agente_display)


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


# ══════════════════════════════════════════════════════════════════════════════
# Function calling — schemas + implementaciones
# ══════════════════════════════════════════════════════════════════════════════

# ── IDs de campos personalizados en Kommo ────────────────────────────────────
# Configura en .env: KOMMO_FIELD_PRESUPUESTO=12345, etc.
# Si un ID no está configurado, el valor se guarda como tag "dato_campo:valor".

def _parse_field_id(env_var: str) -> int | None:
    try:
        v = int(os.getenv(env_var, "0"))
        return v if v > 0 else None
    except (ValueError, TypeError):
        return None


CUSTOM_FIELD_IDS: dict[str, int | None] = {
    "presupuesto": _parse_field_id("KOMMO_FIELD_PRESUPUESTO"),
    "zona":        _parse_field_id("KOMMO_FIELD_ZONA"),
    "tipo":        _parse_field_id("KOMMO_FIELD_TIPO"),
    "recamaras":   _parse_field_id("KOMMO_FIELD_RECAMARAS"),
    "motivo":      _parse_field_id("KOMMO_FIELD_MOTIVO"),
    "urgencia":    _parse_field_id("KOMMO_FIELD_URGENCIA"),
    "forma_pago":  _parse_field_id("KOMMO_FIELD_FORMA_PAGO"),
}

# ── Palabras clave para opt-out explícito ────────────────────────────────────

_OPT_OUT_KEYWORDS = frozenset({
    "déjenme", "dejenme", "no escriban", "no me escriban",
    "no quiero", "no me contacten", "remover", "baja mis datos",
    "quitar de la lista", "no me molesten",
})


def _es_opt_out(texto: str) -> bool:
    t = texto.lower()
    return any(kw in t for kw in _OPT_OUT_KEYWORDS)


# ── Schemas OpenAI-compatible para Groq ──────────────────────────────────────

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "consultar_inventario",
            "description": (
                "Consulta el inventario de proyectos disponibles en el Google Sheet. "
                "Usar cuando el lead pregunta qué opciones hay, o para verificar si "
                "existe algo que coincida con sus criterios antes de clasificarlo como "
                "BUSCANDO_DIFERENTE."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "zona": {
                        "type": "string",
                        "description": "Ciudad o zona de interés. Ej: 'Puerto Vallarta', 'Tijuana'.",
                    },
                    "presupuesto_min": {
                        "type": "number",
                        "description": "Presupuesto mínimo en MXN.",
                    },
                    "presupuesto_max": {
                        "type": "number",
                        "description": "Presupuesto máximo en MXN.",
                    },
                    "tipo": {
                        "type": "string",
                        "description": "Tipo de inversión buscada. Ej: 'accion', 'departamento'.",
                    },
                    "recamaras": {
                        "type": "integer",
                        "description": "Número de recámaras (para propiedades residenciales).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clasificar_lead",
            "description": (
                "Clasifica al lead y lo mueve a la etapa correcta del pipeline. "
                "Llamar cuando tengas al menos presupuesto + urgencia. "
                "NOTA: cita y baja no se mueven automáticamente — se agregan tags "
                "para revisión humana (excepto opt-out explícito, que sí mueve a BAJA)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "presupuesto": {
                        "type": "number",
                        "description": "Presupuesto disponible en MXN. None si no se conoce.",
                    },
                    "zona": {
                        "type": "string",
                        "description": "Ciudad o zona de interés del lead.",
                    },
                    "tipo": {
                        "type": "string",
                        "description": "Tipo de inversión buscada.",
                    },
                    "urgencia": {
                        "type": "string",
                        "enum": [
                            "ya quiero ver",
                            "interesado pero indeciso",
                            "más adelante",
                            "no califica",
                            "opt-out",
                        ],
                        "description": "Nivel de urgencia/intención detectado.",
                    },
                    "motivo": {
                        "type": "string",
                        "description": (
                            "Contexto adicional: razón del estado, horizonte temporal, "
                            "qué busca que no está en el inventario, etc."
                        ),
                    },
                },
                "required": ["urgencia", "motivo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "agendar_cita",
            "description": (
                "Registra una propuesta de cita con el asesor. No agenda en calendario real. "
                "Agrega tags 'cita_propuesta' y notifica al equipo. Usar cuando el lead "
                "quiere ver el proyecto o hablar con alguien del equipo."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fecha_iso": {
                        "type": "string",
                        "description": "Fecha propuesta en formato YYYY-MM-DD. Ej: '2025-06-15'.",
                    },
                    "hora_iso": {
                        "type": "string",
                        "description": "Hora propuesta en formato HH:MM. Ej: '10:00'.",
                    },
                    "asesor_id": {
                        "type": "integer",
                        "description": "ID del asesor en Kommo. Omitir para dejar sin asignar.",
                    },
                },
                "required": ["fecha_iso", "hora_iso"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalar_a_humano",
            "description": (
                "Escala la conversación a un asesor humano sin mover la etapa. "
                "Usar cuando el lead pide hablar con alguien, tiene preguntas legales "
                "complejas o la situación requiere atención personalizada."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "razon": {
                        "type": "string",
                        "description": (
                            "Razón específica para escalar. "
                            "Ej: 'Lead solicita hablar con asesor', 'Pregunta sobre cláusulas NDA'."
                        ),
                    },
                },
                "required": ["razon"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "registrar_dato_calificador",
            "description": (
                "Guarda un dato calificador del lead en el CRM. "
                "Llamar INMEDIATAMENTE cuando el lead revele cualquier dato: presupuesto, "
                "zona, urgencia, forma de pago, etc. No esperar a tener todos los datos."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "campo": {
                        "type": "string",
                        "enum": [
                            "presupuesto", "zona", "tipo", "recamaras",
                            "motivo", "urgencia", "forma_pago",
                        ],
                        "description": "El campo calificador a guardar.",
                    },
                    "valor": {
                        "type": "string",
                        "description": "El valor a guardar. Para presupuesto usar solo el número.",
                    },
                },
                "required": ["campo", "valor"],
            },
        },
    },
]


# ── Helpers internos ──────────────────────────────────────────────────────────

async def _guardar_campo(lead_id: int, campo: str, valor: str) -> None:
    """
    Guarda un dato en campo personalizado de Kommo.
    Si el field_id no está configurado, usa tag 'dato_campo:valor' como fallback.
    """
    from services.kommo import setLeadCustomField, addTagToLead

    field_id = CUSTOM_FIELD_IDS.get(campo)
    if field_id:
        await setLeadCustomField(lead_id, field_id, valor)
    else:
        tag = f"dato_{campo}:{str(valor)[:50]}"
        await addTagToLead(lead_id, tag)


def _norm_texto(s: str) -> str:
    """Normaliza texto: minúsculas, sin tildes."""
    s = s.lower().strip()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


# ── Implementaciones de herramientas ──────────────────────────────────────────

async def _tool_consultar_inventario(
    zona: str = None,
    presupuesto_min: float = None,
    presupuesto_max: float = None,
    tipo: str = None,
    recamaras: int = None,
) -> dict:
    from services.inventario import consultar_inventario
    return await consultar_inventario(
        zona=zona,
        presupuesto_min=presupuesto_min,
        presupuesto_max=presupuesto_max,
        tipo=tipo,
        recamaras=recamaras,
    )


async def _verificar_lead_en_pipeline(lead_id: int) -> bool:
    """Verifica que el lead pertenezca al pipeline configurado antes de modificarlo."""
    from agent.config import settings as _s
    from services.kommo import getLead, KommoError
    if not _s.KOMMO_PIPELINE_ID:
        return True  # sin restricción configurada
    try:
        lead = await getLead(lead_id)
        if lead.get("pipeline_id") != _s.KOMMO_PIPELINE_ID:
            import logging
            logging.getLogger("agentkit").warning(
                f"[tools] lead_id={lead_id} pipeline={lead.get('pipeline_id')} "
                f"≠ {_s.KOMMO_PIPELINE_ID} — operación bloqueada"
            )
            return False
        return True
    except KommoError:
        return False


async def _tool_clasificar_lead(
    lead_id: int | None,
    urgencia: str,
    motivo: str,
    presupuesto: float = None,
    zona: str = None,
    tipo: str = None,
) -> dict:
    if lead_id is None:
        return {"success": False, "error": "clasificar_lead solo está disponible en modo Kommo (lead_id requerido)"}
    if not await _verificar_lead_en_pipeline(lead_id):
        return {"success": False, "error": "Lead fuera del pipeline configurado — operación ignorada"}

    from services.kommo import moveLeadToStage, setLeadTags, KommoError
    from config.etapas import (
        CITA_PRE, SIN_PERFILAR_CONTESTO, BUSCANDO_DIFERENTE,
        MAS_ADELANTE, BAJA,
    )

    PRECIO_MIN = INFO_PROYECTO["precio_accion"]
    motivo_norm = _norm_texto(motivo or "")
    urgencia_norm = _norm_texto(urgencia or "")

    # ── Validación: cita requiere datos mínimos ───────────────────────────────
    if urgencia == "ya quiero ver":
        faltantes = [c for c, v in [("presupuesto", presupuesto), ("zona", zona)] if not v]
        if faltantes:
            return {
                "success": False,
                "error": (
                    f"Para sugerir cita necesito: {', '.join(faltantes)}. "
                    "Recópilalos con registrar_dato_calificador primero."
                ),
            }

    # ── Determinar acción ─────────────────────────────────────────────────────
    etapa_destino: int | None = None
    tags_agregar: list[str] = []
    accion: str

    opt_out = urgencia == "opt-out" or _es_opt_out(motivo or "")

    if opt_out:
        etapa_destino = BAJA
        accion = "movido_a_baja_opt_out"

    elif urgencia == "ya quiero ver":
        # GUARDIA: sugerir en lugar de mover
        tags_agregar = ["ia_sugiere_cita"]
        accion = "tag_ia_sugiere_cita"

    elif urgencia == "no califica" or (
        presupuesto is not None and presupuesto < PRECIO_MIN
    ):
        # GUARDIA: sugerir baja en lugar de mover
        tags_agregar = ["ia_sugiere_baja"]
        accion = "tag_ia_sugiere_baja"

    elif "mas adelante" in urgencia_norm or urgencia == "más adelante":
        etapa_destino = MAS_ADELANTE
        accion = "movido_a_mas_adelante"

    elif any(k in motivo_norm for k in ("diferente", "no hay", "otro proyecto", "no encontr")):
        etapa_destino = BUSCANDO_DIFERENTE
        accion = "movido_a_buscando_diferente"

    elif presupuesto and zona:
        etapa_destino = SIN_PERFILAR_CONTESTO
        accion = "movido_a_sin_perfilar"

    else:
        tags_agregar = ["ia_requiere_mas_datos"]
        accion = "tag_requiere_mas_datos"

    # ── Ejecutar cambios en Kommo ─────────────────────────────────────────────
    errores: list[str] = []

    if etapa_destino is not None:
        try:
            await moveLeadToStage(lead_id, etapa_destino)
        except KommoError as e:
            errores.append(f"Error al mover etapa: {e}")

    if tags_agregar:
        try:
            await setLeadTags(lead_id, tags_agregar)
        except KommoError as e:
            errores.append(f"Error al agregar tags: {e}")

    # Guardar datos calificadores recibidos
    datos_a_guardar = {
        k: str(v) for k, v in {
            "presupuesto": presupuesto,
            "zona": zona,
            "tipo": tipo,
            "urgencia": urgencia,
            "motivo": motivo,
        }.items() if v is not None
    }
    for campo, valor in datos_a_guardar.items():
        try:
            await _guardar_campo(lead_id, campo, valor)
        except Exception as e:
            errores.append(f"Error guardando {campo}: {e}")

    return {
        "success": not errores,
        "accion": accion,
        "etapa_destino": etapa_destino,
        "tags_agregados": tags_agregar,
        "errores": errores or None,
        "datos_guardados": list(datos_a_guardar.keys()),
    }


async def _tool_agendar_cita(
    lead_id: int | None,
    fecha_iso: str,
    hora_iso: str,
    asesor_id: int = None,
) -> dict:
    if lead_id is None:
        return {"success": False, "error": "agendar_cita solo está disponible en modo Kommo"}
    if not await _verificar_lead_en_pipeline(lead_id):
        return {"success": False, "error": "Lead fuera del pipeline configurado — operación ignorada"}

    from services.kommo import setLeadTags, updateLead, KommoError

    tags = ["cita_propuesta", f"cita_{fecha_iso}_{hora_iso}"]
    errores: list[str] = []

    try:
        await setLeadTags(lead_id, tags)
    except KommoError as e:
        errores.append(f"Error agregando tags: {e}")

    if asesor_id:
        try:
            await updateLead(lead_id, {"responsible_user_id": asesor_id})
        except KommoError as e:
            errores.append(f"Error asignando asesor: {e}")

    return {
        "success": not errores,
        "mensaje": f"Cita propuesta registrada para {fecha_iso} a las {hora_iso}. El equipo la confirmará.",
        "tags_agregados": tags,
        "asesor_id": asesor_id,
        "errores": errores or None,
    }


async def _tool_escalar_a_humano(
    lead_id: int | None,
    razon: str,
) -> dict:
    if lead_id is None:
        return {"success": False, "error": "escalar_a_humano solo está disponible en modo Kommo"}
    if not await _verificar_lead_en_pipeline(lead_id):
        return {"success": False, "error": "Lead fuera del pipeline configurado — operación ignorada"}

    from services.kommo import setLeadTags, KommoError

    razon_tag = f"escalar_{_norm_texto(razon)[:40].replace(' ', '_')}"
    tags = ["escalar_humano", razon_tag]

    try:
        await setLeadTags(lead_id, tags)
    except KommoError as e:
        return {"success": False, "error": f"Error al escalar: {e}"}

    return {
        "success": True,
        "mensaje": "Lead escalado a asesor humano. El equipo se pondrá en contacto.",
        "tags_agregados": tags,
    }


async def _tool_registrar_dato_calificador(
    lead_id: int | None,
    campo: str,
    valor: str,
) -> dict:
    if lead_id is None:
        return {"success": False, "error": "registrar_dato_calificador solo está disponible en modo Kommo"}
    if not await _verificar_lead_en_pipeline(lead_id):
        return {"success": False, "error": "Lead fuera del pipeline configurado — operación ignorada"}

    CAMPOS_VALIDOS = {"presupuesto", "zona", "tipo", "recamaras", "motivo", "urgencia", "forma_pago"}
    if campo not in CAMPOS_VALIDOS:
        return {
            "success": False,
            "error": f"Campo inválido: '{campo}'. Válidos: {', '.join(sorted(CAMPOS_VALIDOS))}",
        }

    try:
        await _guardar_campo(lead_id, campo, valor)
    except Exception as e:
        return {"success": False, "error": f"Error guardando {campo}: {e}"}

    return {"success": True, "campo": campo, "valor": valor}


# ── Dispatcher ────────────────────────────────────────────────────────────────

async def ejecutar_tool(nombre: str, args: dict, lead_id: int | None) -> str:
    """
    Ejecuta la herramienta solicitada por el modelo y retorna el resultado
    serializado como JSON string (el formato que espera el rol 'tool').
    """
    try:
        if nombre == "consultar_inventario":
            result = await _tool_consultar_inventario(**args)
        elif nombre == "clasificar_lead":
            result = await _tool_clasificar_lead(lead_id=lead_id, **args)
        elif nombre == "agendar_cita":
            result = await _tool_agendar_cita(lead_id=lead_id, **args)
        elif nombre == "escalar_a_humano":
            result = await _tool_escalar_a_humano(lead_id=lead_id, **args)
        elif nombre == "registrar_dato_calificador":
            result = await _tool_registrar_dato_calificador(lead_id=lead_id, **args)
        else:
            result = {"success": False, "error": f"Herramienta desconocida: '{nombre}'"}
    except TypeError as e:
        result = {"success": False, "error": f"Argumentos inválidos para {nombre}: {e}"}
    except Exception as e:
        logger.error(f"[TOOLS] Error inesperado en {nombre}: {e}")
        result = {"success": False, "error": str(e)}

    return json.dumps(result, ensure_ascii=False, default=str)
