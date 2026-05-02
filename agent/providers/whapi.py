# agent/providers/whapi.py — Adaptador para Whapi.cloud

import os
import base64
import mimetypes
import logging
import httpx
import aiofiles
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")


class ProveedorWhapi(ProveedorWhatsApp):
    """Proveedor de WhatsApp usando Whapi.cloud."""

    def __init__(self):
        self.token = os.getenv("WHAPI_TOKEN")
        self.url_envio = "https://gate.whapi.cloud/messages/text"

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Parsea el payload de Whapi.cloud."""
        body = await request.json()
        mensajes = []
        for msg in body.get("messages", []):
            mensajes.append(MensajeEntrante(
                telefono=msg.get("chat_id", ""),
                texto=msg.get("text", {}).get("body", ""),
                mensaje_id=msg.get("id", ""),
                es_propio=msg.get("from_me", False),
            ))
        return mensajes

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía mensaje de texto via Whapi.cloud."""
        if not self.token:
            logger.warning("WHAPI_TOKEN no configurado — mensaje no enviado")
            return False
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                self.url_envio,
                json={"to": telefono, "body": mensaje},
                headers=self._headers(),
            )
            if r.status_code != 200:
                logger.error(f"Error Whapi texto: {r.status_code} — {r.text}")
            return r.status_code == 200

    async def _leer_base64(self, ruta: str) -> tuple[str, str]:
        """Lee un archivo y retorna (data_uri_base64, mime_type)."""
        mime, _ = mimetypes.guess_type(ruta)
        mime = mime or "application/octet-stream"
        async with aiofiles.open(ruta, "rb") as f:
            data = await f.read()
        b64 = base64.b64encode(data).decode()
        return f"data:{mime};base64,{b64}", mime

    async def enviar_documento(self, telefono: str, ruta: str, nombre: str, caption: str = "") -> bool:
        """Envía un documento (PDF) via Whapi.cloud como base64."""
        if not self.token:
            return False
        try:
            media, _ = await self._leer_base64(ruta)
        except FileNotFoundError:
            logger.error(f"Archivo no encontrado: {ruta}")
            return False
        payload = {"to": telefono, "media": media, "filename": nombre}
        if caption:
            payload["caption"] = caption
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                "https://gate.whapi.cloud/messages/document",
                json=payload,
                headers=self._headers(),
            )
            if r.status_code != 200:
                logger.error(f"Error Whapi documento: {r.status_code} — {r.text}")
            return r.status_code == 200

    async def enviar_imagen(self, telefono: str, ruta: str, caption: str = "") -> bool:
        """Envía una imagen via Whapi.cloud como base64."""
        if not self.token:
            return False
        try:
            media, _ = await self._leer_base64(ruta)
        except FileNotFoundError:
            logger.error(f"Imagen no encontrada: {ruta}")
            return False
        payload = {"to": telefono, "media": media}
        if caption:
            payload["caption"] = caption
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://gate.whapi.cloud/messages/image",
                json=payload,
                headers=self._headers(),
            )
            if r.status_code != 200:
                logger.error(f"Error Whapi imagen: {r.status_code} — {r.text}")
            return r.status_code == 200
