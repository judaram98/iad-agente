# agent/providers/base.py — Interfaz común para proveedores de WhatsApp

from abc import ABC, abstractmethod
from dataclasses import dataclass
from fastapi import Request


@dataclass
class MensajeEntrante:
    """Mensaje normalizado — mismo formato sin importar el proveedor."""
    telefono: str
    texto: str
    mensaje_id: str
    es_propio: bool


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

    async def enviar_documento(self, telefono: str, url: str, nombre: str, caption: str = "") -> bool:
        """Envía un documento (PDF, etc.) por URL pública."""
        return False

    async def enviar_imagen(self, telefono: str, url: str, caption: str = "") -> bool:
        """Envía una imagen por URL pública."""
        return False
