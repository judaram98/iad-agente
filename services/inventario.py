# services/inventario.py — Inventario de propiedades desde Google Sheets CSV
#
# Diseño:
#   - Descarga el CSV público una vez y lo cachea 5 minutos en memoria.
#   - Detecta columnas por alias (no depende de nombres exactos de headers).
#   - Filtra y puntúa cada fila según los criterios recibidos.
#   - Devuelve hasta 5 matches ordenados por relevancia.
#   - Cuando no hay matches exactos, devuelve "casi-matches" con la diferencia explicada.

import asyncio
import csv
import io
import logging
import time
import unicodedata

import httpx

logger = logging.getLogger("inventario")

# ── Caché en memoria ──────────────────────────────────────────────────────────

_CACHE_TTL = 300  # segundos

_cache_filas: list[dict] = []   # filas normalizadas
_cache_ts: float = 0.0          # timestamp de la última carga
_cache_lock = asyncio.Lock()


# ── Mapeo de columnas por alias ───────────────────────────────────────────────
# Para cada columna lógica, lista de palabras clave que pueden aparecer
# en el nombre real del header (normalizado: sin tildes, lowercase).

_COL_ALIASES: dict[str, tuple[str, ...]] = {
    "nombre":      ("nombre", "proyecto", "unidad", "inmueble", "propiedad", "desarrollo"),
    "zona":        ("zona", "ciudad", "ubicacion", "colonia", "municipio", "localidad", "delegacion"),
    "precio":      ("precio", "costo", "valor", "monto", "importe", "inversion", "venta"),
    "tipo":        ("tipo", "categoria", "clase", "tipologia", "producto"),
    "recamaras":   ("recamara", "bedroom", "cuarto", "habitacion", "rec"),
    "disponible":  ("disponib", "status", "estado", "activo", "disponibilidad"),
    "m2":          ("m2", "metros", "superficie", "area", "tamano"),
    "descripcion": ("descripcion", "descripcion", "detalle", "notas", "observacion", "comentario"),
}

_ESTADOS_NO_DISPONIBLE = frozenset({
    "no", "false", "0", "vendido", "ocupado", "reservado", "inactivo", "no disponible",
})


# ── Helpers de texto ──────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    """Minúsculas, sin tildes, sin espacios extra."""
    s = (s or "").lower().strip()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _to_float(s: str) -> float | None:
    """Parsea un string de precio a float. Tolera $, comas, MXN, espacios."""
    clean = (s or "").replace(",", "").replace("$", "").replace("MXN", "").replace(" ", "").strip()
    try:
        return float(clean) if clean else None
    except ValueError:
        return None


def _to_int(s: str) -> int | None:
    """Parsea un string numérico a int."""
    try:
        return int(float(s)) if s and s.strip() else None
    except (ValueError, TypeError):
        return None


# ── Detección de columnas ─────────────────────────────────────────────────────

def _limpiar_header(s: str) -> str:
    """
    Normaliza un header para comparación:
    - Minúsculas, sin tildes (_norm)
    - Sin puntuación ("Rec." → "rec", "M²" → "m2")
    - "²"/"³" → "2"/"3" para manejar notación de metros cuadrados
    """
    s = _norm(s)
    chars = []
    for c in s:
        if c.isalpha():
            chars.append(c)
        elif c.isdigit() or c == "²":
            chars.append("2" if c == "²" else "3" if c == "³" else c)
        elif c == " ":
            chars.append(c)
    return "".join(chars).strip()


def _detectar_columnas(headers: list[str]) -> dict[str, str]:
    """
    Dado los nombres de columnas del CSV, retorna un mapa
    {nombre_logico: nombre_real_en_csv} usando los aliases definidos.

    Matching en tres pasadas (primera que aplique gana):
    1. Palabra exacta: alias == alguna palabra del header limpio  ("precio" in {"precio","mxn"})
    2. Substring largo: alias es substring del header Y len(alias)>=5  ("disponib" in "disponible")
    3. Prefijo inverso: alguna palabra del header (len>=3) es prefijo de algún alias
                        ("rec" is prefix of "recamara", "m2" is prefix of "m2")
    """
    col: dict[str, str] = {}
    for h in headers:
        hn = _limpiar_header(h)
        hn_words = set(hn.split()) if hn else set()
        for logico, aliases in _COL_ALIASES.items():
            if logico in col:
                continue
            matched = False
            for alias in aliases:
                if alias in hn_words:                            # pasada 1
                    matched = True; break
                if len(alias) >= 5 and alias in hn:             # pasada 2
                    matched = True; break
                if any(len(w) >= 3 and alias.startswith(w)      # pasada 3
                       for w in hn_words):
                    matched = True; break
            if matched:
                col[logico] = h
    return col


# ── Normalización de filas ────────────────────────────────────────────────────

def _normalizar_fila(raw: dict, col: dict[str, str]) -> dict:
    """
    Convierte una fila del CSV en un dict normalizado con claves consistentes.
    Siempre incluye _raw con la fila original para depuración.
    """
    precio_raw  = (raw.get(col.get("precio", ""))      or "")
    rec_raw     = (raw.get(col.get("recamaras", ""))   or "")
    disp_raw    = _norm(raw.get(col.get("disponible", "")) or "")

    return {
        "nombre":       ((raw.get(col.get("nombre", "")) or "") or "Sin nombre").strip(),
        "zona":         _norm(raw.get(col.get("zona", "")) or ""),
        "zona_display": (raw.get(col.get("zona", "")) or "").strip(),
        "precio":       _to_float(precio_raw),
        "precio_display": precio_raw.strip(),
        "tipo":         _norm(raw.get(col.get("tipo", "")) or ""),
        "tipo_display": (raw.get(col.get("tipo", "")) or "").strip(),
        "recamaras":    _to_int(rec_raw),
        "m2":           (raw.get(col.get("m2", "")) or "").strip(),
        "descripcion":  (raw.get(col.get("descripcion", "")) or "").strip(),
        "disponible":   disp_raw not in _ESTADOS_NO_DISPONIBLE if disp_raw else True,
        "_raw":         raw,
    }


# ── Fetch y parseo ────────────────────────────────────────────────────────────

async def _fetch_y_normalizar() -> list[dict]:
    """Descarga el CSV, lo parsea y retorna filas normalizadas."""
    from agent.config import settings

    url = settings.INVENTORY_SHEET_CSV_URL
    logger.info(f"[INVENTARIO] Descargando CSV desde Google Sheets…")

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url)
        r.raise_for_status()

    reader = csv.DictReader(io.StringIO(r.text))
    raw_filas = list(reader)

    if not raw_filas:
        logger.warning("[INVENTARIO] CSV vacío")
        return []

    col = _detectar_columnas(list(raw_filas[0].keys()))
    logger.info(f"[INVENTARIO] {len(raw_filas)} filas cargadas | columnas detectadas: {list(col.keys())}")

    return [_normalizar_fila(r, col) for r in raw_filas]


# ── Caché ─────────────────────────────────────────────────────────────────────

async def _obtener_cache() -> tuple[list[dict], float]:
    """
    Retorna (filas_normalizadas, edad_en_segundos).
    Usa double-checked locking para evitar fetches concurrentes.
    """
    global _cache_filas, _cache_ts

    ahora = time.monotonic()

    # Fast path: caché válido
    if _cache_filas and ahora - _cache_ts < _CACHE_TTL:
        return _cache_filas, ahora - _cache_ts

    # Slow path: necesita actualización
    async with _cache_lock:
        ahora = time.monotonic()
        if _cache_filas and ahora - _cache_ts < _CACHE_TTL:
            return _cache_filas, ahora - _cache_ts

        filas = await _fetch_y_normalizar()
        _cache_filas = filas
        _cache_ts = time.monotonic()
        return filas, 0.0


async def recargar_cache() -> None:
    """Invalida el caché. La próxima consulta descargará el CSV de nuevo."""
    global _cache_filas, _cache_ts
    async with _cache_lock:
        _cache_filas = []
        _cache_ts = 0.0
    logger.info("[INVENTARIO] Caché invalidado")


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score(
    item: dict,
    zona: str | None,
    presupuesto_min: float | None,
    presupuesto_max: float | None,
    tipo: str | None,
    recamaras: int | None,
) -> float:
    """
    Puntúa una fila según qué tan bien coincide con los criterios.
    Máximo teórico: 14 puntos.
    """
    score = 0.0

    # ── Precio (max 5 pts) ────────────────────────────────────────────────────
    precio = item.get("precio")
    if precio is not None:
        target: float | None = None
        if presupuesto_min and presupuesto_max:
            target = (presupuesto_min + presupuesto_max) / 2
        elif presupuesto_max:
            target = presupuesto_max * 0.85
        elif presupuesto_min:
            target = presupuesto_min * 1.15

        if target:
            diff = abs(precio - target) / target
            if diff <= 0.05:   score += 5.0
            elif diff <= 0.10: score += 4.0
            elif diff <= 0.20: score += 3.0
            elif diff <= 0.30: score += 1.5

    # ── Zona (max 4 pts) ──────────────────────────────────────────────────────
    if zona:
        nz = _norm(zona)
        rz = item.get("zona", "")
        if rz == nz:            score += 4.0
        elif nz in rz or rz in nz: score += 2.0

    # ── Tipo (max 3 pts) ──────────────────────────────────────────────────────
    if tipo:
        nt = _norm(tipo)
        rt = item.get("tipo", "")
        if nt in rt or rt in nt: score += 3.0

    # ── Recámaras (max 2 pts) ─────────────────────────────────────────────────
    if recamaras is not None:
        rec = item.get("recamaras")
        if rec is not None:
            if rec == recamaras:                   score += 2.0
            elif abs(rec - recamaras) == 1:        score += 0.5

    return score


# ── Filtros exactos ───────────────────────────────────────────────────────────

def _pasa_filtros(
    item: dict,
    zona: str | None,
    presupuesto_min: float | None,
    presupuesto_max: float | None,
    tipo: str | None,
    recamaras: int | None,
) -> bool:
    """Retorna True si la fila pasa TODOS los filtros activos."""

    if not item.get("disponible", True):
        return False

    precio = item.get("precio")
    if presupuesto_min is not None and precio is not None and precio < presupuesto_min:
        return False
    if presupuesto_max is not None and precio is not None and precio > presupuesto_max:
        return False

    if zona:
        nz = _norm(zona)
        rz = item.get("zona", "")
        if rz and nz not in rz and rz not in nz:
            return False

    if tipo:
        nt = _norm(tipo)
        rt = item.get("tipo", "")
        if rt and nt not in rt and rt not in nt:
            return False

    if recamaras is not None:
        rec = item.get("recamaras")
        if rec is not None and rec != recamaras:
            return False

    return True


# ── Serialización para el modelo ──────────────────────────────────────────────

def _serializar(item: dict, diferencia: str | None = None) -> dict:
    """Convierte un item normalizado en el dict que ve el modelo."""
    out: dict = {
        "nombre":     item["nombre"],
        "zona":       item["zona_display"] or item["zona"],
        "tipo":       item["tipo_display"] or item["tipo"],
        "recamaras":  item["recamaras"],
        "m2":         item["m2"] or None,
        "descripcion": item["descripcion"] or None,
    }
    if item["precio"] is not None:
        out["precio_mxn"] = int(item["precio"])
    elif item["precio_display"]:
        out["precio_mxn"] = item["precio_display"]

    if diferencia:
        out["diferencia"] = diferencia

    # Limpiar Nones para reducir ruido en el contexto del modelo
    return {k: v for k, v in out.items() if v is not None}


# ── Función pública principal ─────────────────────────────────────────────────

async def consultar_inventario(
    zona: str | None = None,
    presupuesto_min: float | None = None,
    presupuesto_max: float | None = None,
    tipo: str | None = None,
    recamaras: int | None = None,
) -> dict:
    """
    Filtra el inventario del Google Sheet y devuelve hasta 5 matches.

    - Ordena por relevancia (score descendente, luego precio ascendente).
    - Cuando no hay matches exactos, devuelve hasta 5 "casi-matches" con
      una explicación de la diferencia para que el modelo pueda usarlos.

    Returns:
        {
            "success": bool,
            "total_matches": int,
            "matches": [...],           # hasta 5 items
            "casi_matches": [...],      # solo cuando total == 0
            "cache_age_s": float,
            "mensaje": str,
        }
    """
    try:
        filas, cache_age = await _obtener_cache()
    except Exception as e:
        logger.error(f"[INVENTARIO] Error obteniendo inventario: {e}")
        return {"success": False, "error": f"No se pudo obtener el inventario: {e}"}

    if not filas:
        return {
            "success": True,
            "total_matches": 0,
            "matches": [],
            "casi_matches": [],
            "cache_age_s": round(cache_age, 1),
            "mensaje": "El inventario está vacío en este momento.",
        }

    # ── Matches exactos ───────────────────────────────────────────────────────
    candidatos = [
        (item, _score(item, zona, presupuesto_min, presupuesto_max, tipo, recamaras))
        for item in filas
        if _pasa_filtros(item, zona, presupuesto_min, presupuesto_max, tipo, recamaras)
    ]
    candidatos.sort(key=lambda x: (-x[1], x[0].get("precio") or float("inf")))

    matches = [_serializar(item) for item, _ in candidatos[:5]]
    total = len(candidatos)

    if total > 0:
        return {
            "success": True,
            "total_matches": total,
            "matches": matches,
            "casi_matches": [],
            "cache_age_s": round(cache_age, 1),
            "mensaje": f"{total} propiedad(es) encontrada(s) con esos criterios.",
        }

    # ── Sin matches: buscar casi-matches ─────────────────────────────────────
    casi: list[dict] = []
    vistos: set[str] = set()

    def _agregar_casi(item: dict, diferencia: str) -> None:
        clave = item["nombre"] + item.get("zona_display", "")
        if clave not in vistos:
            vistos.add(clave)
            casi.append(_serializar(item, diferencia=diferencia))

    # Estrategia 1: sin filtro de zona (busca misma tipo y precio)
    if zona:
        alt = [
            (it, _score(it, None, presupuesto_min, presupuesto_max, tipo, recamaras))
            for it in filas
            if _pasa_filtros(it, None, presupuesto_min, presupuesto_max, tipo, recamaras)
        ]
        alt.sort(key=lambda x: -x[1])
        for it, _ in alt[:3]:
            dif = f"Zona diferente: {it.get('zona_display') or it.get('zona', '?')}"
            _agregar_casi(it, dif)

    # Estrategia 2: sin filtro de tipo (busca misma zona y precio)
    if tipo:
        alt = [
            (it, _score(it, zona, presupuesto_min, presupuesto_max, None, recamaras))
            for it in filas
            if _pasa_filtros(it, zona, presupuesto_min, presupuesto_max, None, recamaras)
        ]
        alt.sort(key=lambda x: -x[1])
        for it, _ in alt[:3]:
            dif = f"Tipo diferente: {it.get('tipo_display') or it.get('tipo', '?')}"
            _agregar_casi(it, dif)

    # Estrategia 3: precio ±30% (flexibiliza presupuesto)
    if presupuesto_min is not None or presupuesto_max is not None:
        pmin_rel = (presupuesto_min * 0.70) if presupuesto_min else None
        pmax_rel = (presupuesto_max * 1.30) if presupuesto_max else None
        alt = [
            (it, _score(it, zona, pmin_rel, pmax_rel, tipo, recamaras))
            for it in filas
            if _pasa_filtros(it, zona, pmin_rel, pmax_rel, tipo, recamaras)
        ]
        alt.sort(key=lambda x: -x[1])
        for it, _ in alt[:3]:
            precio = it.get("precio")
            if precio:
                dif = f"Precio fuera de rango (${int(precio):,} MXN)"
            else:
                dif = "Precio aproximado fuera de rango"
            _agregar_casi(it, dif)

    casi_top = casi[:5]

    if casi_top:
        msg = (
            "No encontré propiedades exactas con esos criterios, "
            "pero hay opciones similares que podrían interesarte."
        )
    else:
        msg = "No hay propiedades disponibles con esos criterios ni opciones similares en este momento."

    return {
        "success": True,
        "total_matches": 0,
        "matches": [],
        "casi_matches": casi_top,
        "cache_age_s": round(cache_age, 1),
        "mensaje": msg,
    }
