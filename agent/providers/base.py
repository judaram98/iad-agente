# agent/providers/base.py — Interfaz común para proveedores de WhatsApp

from abc import ABC, abstractmethod
from dataclasses import dataclass
from fastapi import Request


@dataclass
class MensajeEntrante:
    """
    Mensaje normalizado — mismo formato sin importar el proveedor.

    En modo Whapi:  telefono = número E.164 del remitente.
    En modo Kommo:  telefono = str(lead_id) — el lead_id es la clave
                    conversacional maestra; el número real se ignora porque
                    Kommo ya sabe a qué canal enviar.
    """
    telefono: str        # Clave conversacional (número o str(lead_id))
    texto: str
    mensaje_id: str
    es_propio: bool
    lead_id: int | None = None      # Solo en modo Kommo
    contact_id: int | None = None   # Solo en modo Kommo


class ProveedorWhatsApp(ABC):
    """Interfaz que cada proveedor de WhatsApp debe implementar."""

    @abstractmethod
    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Extrae y normaliza mensajes del payload del webhook."""
        ...

    @abstractmethod
    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía un mensaje de texto. Retorna True si fue exitoso."""
        ...

    async def validar_webhook(self, request: Request) -> dict | int | None:
        """Verificación GET del webhook (solo Meta la requiere). Retorna respuesta o None."""
        return None

    async def enviar_documento(self, telefono: str, ruta: str, nombre: str, caption: str = "") -> bool:
        """Envía un documento (PDF, etc.) desde una ruta local."""
        return False

    async def enviar_imagen(self, telefono: str, ruta: str, caption: str = "") -> bool:
        """Envía una imagen desde una ruta local."""
        return False
