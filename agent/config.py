from typing import Literal, Optional
from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import sys


class Settings(BaseSettings):
    # ── IA (Groq) ──────────────────────────────────────────────────────────────
    GROQ_API_KEY: str
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # ── Kommo CRM ──────────────────────────────────────────────────────────────
    KOMMO_SUBDOMAIN: str          # ej: "miempresa.kommo.com"
    KOMMO_ACCESS_TOKEN: str       # JWT long-lived de Kommo
    KOMMO_PIPELINE_ID: Optional[int] = None   # se configura en Etapa 2
    KOMMO_WEBHOOK_SECRET: str     # generado con secrets.token_urlsafe(32)

    @field_validator("KOMMO_PIPELINE_ID", mode="before")
    @classmethod
    def pipeline_id_vacio_es_none(cls, v):
        """Trata string vacío como None para KOMMO_PIPELINE_ID."""
        if v == "" or v is None:
            return None
        return v

    # ── Inventario ─────────────────────────────────────────────────────────────
    INVENTORY_SHEET_CSV_URL: str  # Google Sheet publicado como CSV

    # ── Modo del agente ────────────────────────────────────────────────────────
    AGENT_MODE: Literal["kommo", "whapi"] = "kommo"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


_AYUDA = {
    "GROQ_API_KEY":            "Obtener en console.groq.com → API Keys (empieza con gsk_...)",
    "GROQ_MODEL":              "Modelo Groq a usar. Default: llama-3.3-70b-versatile",
    "KOMMO_SUBDOMAIN":         "Tu subdominio sin https://, ej: miempresa.kommo.com",
    "KOMMO_ACCESS_TOKEN":      "Token JWT long-lived: Kommo → Settings → Integrations → API",
    "KOMMO_PIPELINE_ID":       "ID del pipeline (opcional, se configura en Etapa 2)",
    "KOMMO_WEBHOOK_SECRET":    'Genera con: python -c "import secrets; print(secrets.token_urlsafe(32))"',
    "INVENTORY_SHEET_CSV_URL": "Google Sheet → Archivo → Publicar en web → CSV → copiar URL",
    "AGENT_MODE":              "Modo del agente. Valores permitidos: kommo | whapi",
}


try:
    settings = Settings()
except ValidationError as e:
    print("\n" + "─" * 65)
    print("  ERROR FATAL — Variables de entorno faltantes o inválidas")
    print("─" * 65)

    for err in e.errors():
        campo = str(err["loc"][0]) if err["loc"] else "desconocido"
        tipo = err["type"]

        if tipo == "missing":
            print(f"\n  FALTA:    {campo}")
            print(f"  Qué es:   {_AYUDA.get(campo, 'Revisa .env.example')}")
        elif tipo == "literal_error":
            esperado = err.get("ctx", {}).get("expected", "")
            print(f"\n  INVÁLIDO: {campo} = ???")
            print(f"  Esperado: {esperado}")
        else:
            print(f"\n  ERROR en {campo}: {err['msg']}")

    print("\n" + "─" * 65)
    print("  → Copia .env.example → .env y llena los valores reales.")
    print("  → En Railway: agrega las variables en Settings → Variables.")
    print("─" * 65 + "\n")
    sys.exit(1)
except Exception as exc:
    print(f"\n❌ ERROR FATAL DE CONFIGURACIÓN: {exc}\n")
    sys.exit(1)
