# tests/test_inventario.py — Pruebas para services/inventario.py
#
# Inyecta filas normalizadas directamente en el caché para evitar
# llamadas de red y probar el filtrado de forma aislada.

import time
import pytest
import services.inventario as inv_mod
from services.inventario import consultar_inventario, _normalizar_fila, _detectar_columnas


# ── Fixture: inventario ficticio ──────────────────────────────────────────────

_CSV_HEADERS = ["Nombre", "Zona", "Precio MXN", "Tipo", "Rec.", "M²", "Disponible", "Descripcion"]
_COLUMN_MAP  = _detectar_columnas(_CSV_HEADERS)

def _fila(nombre, zona, precio, tipo, rec, disponible="Sí", m2=""):
    raw = {
        "Nombre":      nombre,
        "Zona":        zona,
        "Precio MXN":  str(precio),
        "Tipo":        tipo,
        "Rec.":        str(rec) if rec else "",
        "M²":          m2,
        "Disponible":  disponible,
        "Descripcion": "",
    }
    return _normalizar_fila(raw, _COLUMN_MAP)


_INVENTARIO = [
    _fila("Casa Vallarta A",    "Puerto Vallarta", 1_500_000, "casa",     3, m2="120"),
    _fila("Depa Vallarta B",    "Puerto Vallarta",   850_000, "depa",     2, m2="75"),
    _fila("Casa Vallarta C",    "Puerto Vallarta",   480_000, "casa",     2, m2="80"),
    _fila("Casa Guadalajara D", "Guadalajara",     2_200_000, "casa",     4, m2="200"),
    _fila("Depa CDMX E",        "Ciudad de México",  950_000, "depa",     1, m2="55"),
    _fila("Casa PV cara F",     "Puerto Vallarta", 3_000_000, "casa",     5, m2="300"),
    _fila("Terreno no disp",    "Puerto Vallarta",   350_000, "terreno",  0, disponible="No disponible"),
]


@pytest.fixture(autouse=True)
def inyectar_cache():
    """Carga el inventario ficticio en el caché antes de cada test."""
    inv_mod._cache_filas = list(_INVENTARIO)
    inv_mod._cache_ts    = time.monotonic()
    yield
    inv_mod._cache_filas = []
    inv_mod._cache_ts    = 0.0


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_sin_filtros_devuelve_resultados():
    r = await consultar_inventario()
    assert r["success"] is True
    assert len(r["matches"]) > 0


async def test_filtro_zona_exacto():
    r = await consultar_inventario(zona="Guadalajara")
    assert r["success"] is True
    nombres = [m["nombre"] for m in r["matches"]]
    assert "Casa Guadalajara D" in nombres
    for m in r["matches"]:
        # zona se devuelve como display (capitalizado); comparar case-insensitive
        assert "guadalajara" in m.get("zona", "").lower()


async def test_filtro_precio_max():
    r = await consultar_inventario(presupuesto_max=900_000)
    assert r["success"] is True
    assert len(r["matches"]) > 0
    for m in r["matches"]:
        # el campo serializado es precio_mxn (int o string display)
        precio = m.get("precio_mxn")
        assert precio is not None
        assert int(precio) <= 900_000


async def test_filtro_precio_min():
    r = await consultar_inventario(presupuesto_min=2_000_000)
    assert r["success"] is True
    assert len(r["matches"]) > 0
    for m in r["matches"]:
        precio = m.get("precio_mxn")
        assert precio is not None
        assert int(precio) >= 2_000_000


async def test_filtro_tipo_depa():
    r = await consultar_inventario(tipo="depa")
    assert r["success"] is True
    assert len(r["matches"]) >= 1
    for m in r["matches"]:
        assert "depa" in m.get("tipo", "").lower()


async def test_filtro_recamaras():
    r = await consultar_inventario(recamaras=2)
    assert r["success"] is True
    for m in r["matches"]:
        assert m.get("recamaras") == 2


async def test_no_disponibles_quedan_excluidos():
    """Filas con Disponible='No disponible' nunca deben aparecer en resultados."""
    r = await consultar_inventario(zona="Puerto Vallarta")
    nombres = [m["nombre"] for m in r["matches"]]
    assert "Terreno no disp" not in nombres


async def test_casi_matches_cuando_no_hay_exactos():
    """
    Zona sin inventario ("Monterrey") pero con presupuesto realista →
    no hay matches exactos pero la estrategia "sin filtro de zona" debe
    devolver casi-matches para que el modelo tenga opciones que ofrecer.
    """
    r = await consultar_inventario(zona="Monterrey", presupuesto_max=1_000_000)
    assert r["success"] is True
    assert len(r["matches"]) == 0, "No debe haber matches exactos para 'Monterrey'"
    assert len(r["casi_matches"]) > 0, (
        "Debe haber casi-matches (mismos precios pero en otras zonas)"
    )


async def test_matches_limitados_a_cinco():
    """Aunque haya más propiedades, el resultado no devuelve más de 5."""
    r = await consultar_inventario()
    assert len(r["matches"]) <= 5


async def test_zona_parcial_hace_match():
    """Búsqueda por 'Vallarta' debe encontrar entradas de 'Puerto Vallarta'."""
    r = await consultar_inventario(zona="Vallarta")
    assert r["success"] is True
    assert len(r["matches"]) > 0
