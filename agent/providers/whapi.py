# agent/providers/whapi.py — Adaptador para Whapi.cloud

import os
import logging
import httpx
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

    async def enviar_documento(self, telefono: str, url: str, nombre: str, caption: str = "") -> bool:
        """Envía un documento (PDF) via Whapi.cloud usando URL pública."""
        if not self.token:
            return False
        payload = {"to": telefono, "media": url, "filename": nombre}
        if caption:
            payload["caption"] = caption
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://gate.whapi.cloud/messages/document",
                json=payload,
                headers=self._headers(),
            )
            if r.status_code != 200:
                logger.error(f"Error Whapi documento: {r.status_code} — {r.text}")
            return r.status_code == 200

    async def enviar_imagen(self, telefono: str, url: str, caption: str = "") -> bool:
        """Envía una imagen via Whapi.cloud usando URL pública."""
        if not self.token:
            return False
        payload = {"to": telefono, "media": url}
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
