# agent/brain.py — Cerebro del agente: conexión con Groq (LLaMA 3.3 70B)
#
# Modos de operación:
#   AGENT_MODE=whapi  → generar_respuesta()         (clave: teléfono)
#   AGENT_MODE=kommo  → procesar_mensaje_kommo()    (clave: lead_id)

import json
import os
import re
import yaml
import logging
from groq import AsyncGroq
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
MODELO = "llama-3.3-70b-versatile"


def cargar_config_prompts() -> dict:
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/prompts.yaml no encontrado")
        return {}


def _cargar_business() -> dict:
    try:
        with open("config/business.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def cargar_system_prompt() -> str:
    """
    Carga el system prompt de prompts.yaml y sustituye los placeholders
    estáticos [NOMBRE_INMOBILIARIA] y [CIUDAD/ZONA] desde business.yaml.
    El placeholder {{contexto_del_lead}} NO se sustituye aquí — se inyecta
    en tiempo de ejecución dentro de procesar_mensaje_kommo/generar_respuesta.
    """
    cfg = cargar_config_prompts()
    biz = _cargar_business()

    base = cfg.get("system_prompt", "Eres una asesora inmobiliaria. Responde en español.")
    tools_ctx = cfg.get("tools_instrucciones", "")

    # Sustituir variables de negocio
    tvars = biz.get("template_vars", {})
    nombre_inmo = tvars.get("nombre_inmobiliaria") or biz.get("negocio", {}).get("nombre", "la inmobiliaria")
    ciudad = tvars.get("ciudad", "la zona")

    base = base.replace("[NOMBRE_INMOBILIARIA]", nombre_inmo)
    base = base.replace("[CIUDAD/ZONA]", ciudad)

    if tools_ctx:
        base = f"{base}\n\n{tools_ctx}"

    return base


def obtener_mensaje_error() -> str:
    config = cargar_config_prompts()
    return config.get("error_message", "Lo siento, estoy teniendo problemas técnicos. Por favor intenta de nuevo.")


def obtener_mensaje_fallback() -> str:
    config = cargar_config_prompts()
    return config.get("fallback_message", "Disculpa, no entendí tu mensaje. ¿Podrías reformularlo?")


_MAX_TOOL_CALLS = 5

# Regex para eliminar tags <function=...>...</function> que LLaMA 3 a veces
# incluye como texto plano cuando recibe un 429 y reintenta sin tool_calls.
_RE_LEAKED_TOOL = re.compile(r"<function=\w+>.*?</function>", re.DOTALL)

# Detecta cuando el modelo anuncia que va a llamar un tool pero no lo llama
# (finish_reason=stop con texto prometiendo una acción). Forzamos tool_choice=required.
_RE_TOOL_INTENT = re.compile(
    r"\b(voy\s+a|vamos\s+a|d[eé]jame|procedo\s+a)\b.{0,80}"
    r"\b(consultar|revisar|verificar|buscar|checar|obtener)\b"
    r"|\b(consultar[eé]|revisar[eé]|verificar[eé]|buscar[eé]|obtendr[eé])\b",
    re.IGNORECASE | re.DOTALL,
)

# Campos cuyo valor "0" o "0.0" equivale a "no especificado" (placeholder del modelo).
_CAMPOS_NUMERICOS = frozenset({"presupuesto", "presupuesto_min", "presupuesto_max"})
_CAMPOS_ENTEROS   = frozenset({"recamaras"})


def _limpiar_args(raw_arguments: str) -> tuple[dict, str]:
    """
    Parsea y limpia los argumentos de un tool call.

    Groq rechaza historial con valores `null` o tipos incorrectos en campos
    opcionales — esto causa un HTTP 400 en la llamada SIGUIENTE al tool call.

    Eliminamos:
    - null / None
    - strings vacíos
    - "0" / "0.0" como placeholder numérico del modelo

    Convertimos:
    - strings numéricas ("550000") → float/int según el campo
    """
    try:
        raw = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return {}, "{}"

    clean: dict = {}
    for k, v in raw.items():
        if v is None:
            continue
        if isinstance(v, str):
            v = v.strip()
            if not v or v.lower() in ("0", "0.0", "null", "none"):
                continue
            if k in _CAMPOS_NUMERICOS:
                try:
                    v = float(v.replace(",", "").replace("$", "").replace(" ", ""))
                    if v == 0.0:
                        continue
                except (ValueError, TypeError):
                    pass
            elif k in _CAMPOS_ENTEROS:
                try:
                    v = int(float(v))
                    if v == 0:
                        continue
                except (ValueError, TypeError):
                    pass
        elif isinstance(v, (int, float)) and v == 0 and k in (_CAMPOS_NUMERICOS | _CAMPOS_ENTEROS):
            continue
        clean[k] = v

    return clean, json.dumps(clean, ensure_ascii=False)


def _limpiar_texto_respuesta(texto: str) -> str:
    """
    Elimina fragmentos <function=...>...</function> que el modelo incluye
    como texto plano cuando Groq reintenta tras un 429 y el finish_reason
    es 'stop' en vez de 'tool_calls'.
    """
    limpio = _RE_LEAKED_TOOL.sub("", texto).strip()
    return re.sub(r"\n{3,}", "\n\n", limpio)


async def _loop_tools(mensajes: list[dict], lead_id: int | None) -> str:
    """
    Ciclo de tool calling con Groq (OpenAI-compatible).

    Pasa TOOLS al modelo y maneja la secuencia:
      modelo decide llamar tool → ejecutar → devolver resultado → modelo continúa.
    Limita a _MAX_TOOL_CALLS iteraciones para evitar loops infinitos de LLaMA.
    """
    from agent.tools import TOOLS, ejecutar_tool

    tool_calls_total = 0

    while True:
        # Ofrecer tools solo si no se ha alcanzado el límite
        usar_tools = tool_calls_total < _MAX_TOOL_CALLS
        kwargs: dict = {
            "model": MODELO,
            "messages": mensajes,
            "max_tokens": 1024,
            "temperature": 0.7,
        }
        if usar_tools:
            kwargs["tools"] = TOOLS
            kwargs["tool_choice"] = "auto"
            logger.info(f"[brain] llamando a Groq con {len(TOOLS)} tools disponibles (llamada #{tool_calls_total + 1})")

        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as e:
            err_str = str(e)
            if "429" in err_str:
                if "tokens per day" in err_str.lower() or "TPD" in err_str:
                    logger.error(
                        f"[BRAIN] Groq 429 TPD — cuota diaria agotada, sin recuperación: {e}"
                    )
                else:
                    logger.warning(
                        f"[BRAIN] Groq 429 TPM — límite por minuto, reintento posible: {e}"
                    )
            else:
                logger.error(f"[BRAIN] Error Groq API: {e}")
            return obtener_mensaje_error()

        choice = response.choices[0]
        tool_calls = getattr(choice.message, "tool_calls", None)

        # Sin tool calls → verificar si el modelo anunció intención sin llamar (bug de LLaMA)
        if choice.finish_reason != "tool_calls" or not tool_calls:
            texto = (choice.message.content or "").strip()

            # Detectar "voy a consultar…" / "déjame revisar…" sin llamada real.
            # Solo en la primera iteración y cuando hay tools disponibles.
            if usar_tools and tool_calls_total == 0 and texto and _RE_TOOL_INTENT.search(texto):
                logger.warning(
                    f"[BRAIN] Modelo anunció herramienta sin llamarla "
                    f"(finish={choice.finish_reason!r}) — forzando tool_choice=required"
                )
                try:
                    resp2 = await client.chat.completions.create(
                        model=MODELO,
                        messages=mensajes,
                        max_tokens=512,
                        temperature=0.3,
                        tools=TOOLS,
                        tool_choice="required",
                    )
                    c2 = resp2.choices[0]
                    tc2 = getattr(c2.message, "tool_calls", None)
                    if c2.finish_reason == "tool_calls" and tc2:
                        logger.info("[BRAIN] Tool call forzado exitoso")
                        choice, tool_calls = c2, tc2
                        # Continuar: no hacer return, caer al bloque de procesamiento
                    else:
                        return _limpiar_texto_respuesta(texto or obtener_mensaje_error())
                except Exception as e:
                    logger.error(f"[BRAIN] Error en tool call forzado: {e}")
                    return _limpiar_texto_respuesta(texto or obtener_mensaje_error())
            else:
                return _limpiar_texto_respuesta(texto or obtener_mensaje_error())

        # Si ya alcanzamos el límite y el modelo aún quiere llamar tools,
        # hacer una última llamada sin tools para forzar respuesta de texto.
        if not usar_tools:
            logger.warning(f"[BRAIN] Límite de {_MAX_TOOL_CALLS} tool calls alcanzado — forzando respuesta")
            try:
                final = await client.chat.completions.create(
                    model=MODELO, messages=mensajes, max_tokens=1024, temperature=0.7,
                )
                texto = final.choices[0].message.content or obtener_mensaje_error()
                return _limpiar_texto_respuesta(texto)
            except Exception as e:
                logger.error(f"[BRAIN] Error en llamada final sin tools: {e}")
                return obtener_mensaje_error()

        # Agregar mensaje del asistente al historial con args LIMPIOS.
        # Groq valida el historial completo en cada llamada — args con null/tipos
        # incorrectos en mensajes anteriores causan HTTP 400 en la siguiente llamada.
        tool_calls_limpios = []
        args_por_id: dict[str, dict] = {}

        for tc in tool_calls:
            args_clean, args_str = _limpiar_args(tc.function.arguments)
            args_por_id[tc.id] = args_clean
            tool_calls_limpios.append({
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": args_str,   # sin nulls
                },
            })

        mensajes.append({
            "role": "assistant",
            "content": choice.message.content,
            "tool_calls": tool_calls_limpios,
        })

        # Ejecutar cada tool call con los args ya limpios
        for tc in tool_calls:
            tool_calls_total += 1
            args = args_por_id[tc.id]

            logger.info(f"[BRAIN] Tool call #{tool_calls_total}: {tc.function.name}({list(args.keys())})")
            result_str = await ejecutar_tool(tc.function.name, args, lead_id)
            logger.debug(f"[BRAIN] Tool result: {result_str[:200]}")

            mensajes.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_str,
            })


_RE_DATO_TAG = re.compile(r"^dato_(\w+):(.+)$")


def construir_contexto_lead(
    lead_data: dict,
    historial: list[dict] | None = None,
) -> str:
    """
    Construye el bloque {{contexto_del_lead}} que se inyecta en el system prompt.

    Incluye:
    - Etapa actual humanizada (no el ID numérico)
    - Datos calificadores ya registrados (campos personalizados + tags dato_*)
    - Resumen de los últimos 5 mensajes del historial
    """
    from config.etapas import NOMBRE_ETAPA

    # ── Etapa humanizada ─────────────────────────────────────────────────────
    status_id = lead_data.get("status_id", 0)
    etapa = NOMBRE_ETAPA.get(status_id, f"Desconocida (id={status_id})")
    nombre = lead_data.get("name") or "Sin nombre"
    lead_id = lead_data.get("id", "?")

    # ── Datos calificadores ──────────────────────────────────────────────────
    datos: dict[str, str] = {}

    # Desde custom_fields_values (si KOMMO_FIELD_* está configurado)
    for campo in (lead_data.get("custom_fields_values") or []):
        nombre_campo = campo.get("field_name", "")
        valores = [str(v.get("value", "")) for v in campo.get("values", []) if v.get("value") is not None]
        if nombre_campo and valores:
            datos[nombre_campo] = ", ".join(valores)

    # Desde tags con formato dato_campo:valor (fallback cuando no hay field_id)
    tags_raw = [t.get("name", "") for t in lead_data.get("_embedded", {}).get("tags", [])]
    for tag in tags_raw:
        m = _RE_DATO_TAG.match(tag)
        if m:
            datos.setdefault(m.group(1), m.group(2))  # custom_field tiene prioridad

    # ── Últimos 5 mensajes ───────────────────────────────────────────────────
    lineas_historial: list[str] = []
    if historial:
        for msg in historial[-5:]:
            rol = "Cliente" if msg["role"] == "user" else "Sofía"
            texto = msg["content"]
            resumen = texto[:120] + ("…" if len(texto) > 120 else "")
            lineas_historial.append(f"  {rol}: {resumen}")

    # ── Armar bloque ─────────────────────────────────────────────────────────
    lineas = [
        f"Lead #{lead_id} — {nombre}",
        f"Etapa: {etapa}",
    ]

    if datos:
        lineas.append("Datos calificadores registrados:")
        for k, v in datos.items():
            lineas.append(f"  {k}: {v}")
    else:
        lineas.append("Datos calificadores: ninguno registrado aún")

    if lineas_historial:
        lineas.append("Últimos mensajes:")
        lineas.extend(lineas_historial)
    else:
        lineas.append("Últimos mensajes: inicio de conversación")

    return "\n".join(lineas)


async def procesar_mensaje_kommo(
    lead_id: int,
    texto: str,
    historial: list[dict],
) -> str | None:
    """
    Pipeline completo para mensajes entrantes en modo Kommo.

    1. Obtiene el lead de Kommo para contexto e inyecta su info al system prompt.
    2. GUARDIA CRÍTICA: si la etapa está congelada, retorna None (no responder).
    3. Genera la respuesta con Groq usando el contexto enriquecido.

    Retorna None si el lead está congelado o si hay error al obtenerlo.
    """
    from services.kommo import getLead, KommoError
    from config.etapas import es_etapa_congelada

    if not texto or len(texto.strip()) < 2:
        return obtener_mensaje_fallback()

    # ── Obtener contexto del lead ────────────────────────────────────────────
    try:
        lead_data = await getLead(lead_id)
    except KommoError as e:
        logger.warning(f"[BRAIN] No se pudo obtener lead {lead_id}: {e} — respondiendo sin contexto")
        lead_data = {}

    # ── GUARDIA CRÍTICA: etapa congelada ─────────────────────────────────────
    from config.etapas import NOMBRE_ETAPA
    status_id = lead_data.get("status_id", 0)
    etapa_nombre = NOMBRE_ETAPA.get(status_id, f"id={status_id}")
    logger.info(f"[brain] cargando contexto del lead={lead_id}, etapa={etapa_nombre}")

    if status_id and es_etapa_congelada(status_id):
        logger.info(f"[BRAIN] Lead {lead_id} en etapa congelada ({etapa_nombre}) — silenciado")
        return None

    # ── Inyectar contexto del lead en el system prompt ───────────────────────
    base_prompt = cargar_system_prompt()
    contexto = (
        construir_contexto_lead(lead_data, historial)
        if lead_data
        else "Lead nuevo — sin datos registrados aún"
    )
    system_prompt = base_prompt.replace("{{contexto_del_lead}}", contexto)

    # ── Construir mensajes y ejecutar con tool loop ───────────────────────────
    mensajes: list[dict] = [{"role": "system", "content": system_prompt}]
    for msg in historial:
        mensajes.append({"role": msg["role"], "content": msg["content"]})
    mensajes.append({"role": "user", "content": texto})

    respuesta = await _loop_tools(mensajes, lead_id)
    logger.info(f"[BRAIN] Lead {lead_id} → {len(respuesta)} chars")
    return respuesta


async def generar_respuesta(mensaje: str, historial: list[dict]) -> str:
    """
    Genera una respuesta usando Groq (LLaMA 3.3 70B).

    Args:
        mensaje: El mensaje nuevo del usuario
        historial: Lista de mensajes anteriores [{"role": "user/assistant", "content": "..."}]

    Returns:
        La respuesta generada por el modelo
    """
    if not mensaje or len(mensaje.strip()) < 2:
        return obtener_mensaje_fallback()

    system_prompt = cargar_system_prompt().replace(
        "{{contexto_del_lead}}",
        "Sin contexto CRM — conversación directa",
    )

    mensajes: list[dict] = [{"role": "system", "content": system_prompt}]
    for msg in historial:
        mensajes.append({"role": msg["role"], "content": msg["content"]})
    mensajes.append({"role": "user", "content": mensaje})

    respuesta = await _loop_tools(mensajes, lead_id=None)
    logger.info(f"[BRAIN] Whapi → {len(respuesta)} chars")
    return respuesta
