from __future__ import annotations

import datetime
import re
import time
from typing import Optional, Protocol

from app.models import MensajeEntrada, MensajeSalida, Perfil
from app.profile_store import ProfileStore

PRESUPUESTO_RE = re.compile(
    r"(?P<cant>[0-9][0-9.,]*)\s*(?:usd|dolares|dólares|euros|pesos|\$)", re.I
)


class Sender:
    async def enviar(self, chat_id: str, salida: MensajeSalida) -> None: ...


class Orquestador:
    """Centro de la conversación. No conoce Telegram ni WhatsApp:
    recibe un MensajeEntrada y produce respuestas vía el sender provisto.
    """

    def __init__(self, store: Optional[ProfileStore] = None) -> None:
        self.store = store or ProfileStore()

    async def procesar(self, mensaje: MensajeEntrada, sender: Sender) -> None:
        perfil = await self.store.leer(mensaje.chat_id, mensaje.canal)
        perfil.timestamps.append(_ahora())
        if self._inferir_perfil(mensaje, perfil):
            await self.store.guardar(perfil, mensaje.canal)
        salida = self._construir_respuesta(mensaje.texto, perfil)
        await sender.enviar(mensaje.chat_id, salida)

    # --- reglas de negocio (mientras no haya LLM) ----------------------

    def _inferir_perfil(self, m: MensajeEntrada, p: Perfil) -> bool:
        """Guarda datos que el usuario 'suelta' en cualquier mensaje, sin preguntar."""
        t = m.texto.upper()
        cambio = False

        monto = _extraer_monto(m.texto)
        if monto and monto != p.presupuesto:
            p.presupuesto = monto
            cambio = True

        if any(c in t for c in ("desde bogota", "salgo de bogota", "desde bogot")):
            p.origen = "Bogota"
            cambio = True
        elif any(c in t for c in ("desde medellin", "salgo de medel")):
            p.origen = "Medellin"
            cambio = True

        if p.origen is None and m.ubicacion:
            # TODO: reverse-geocode de lat/long
            pass
        return cambio

    def _construir_respuesta(self, texto: str, p: Perfil) -> MensajeSalida:
        t = texto.strip().lower()
        if t in ("/start", "hola", "buenas", "hi"):
            return MensajeSalida(
                "¡Hola! Soy tu asistente de vuelos ✈️. "
                "Cuéntame con cuánto cuentas y hacia dónde quieres ir."
            )
        if t in ("/help", "ayuda", "help", "que haces"):
            return MensajeSalida(
                "Te ayudo a encontrar vuelos. Puedes decirme cosas como:\n"
                "• 'Quiero irme, tengo 300 dólares'\n"
                "• '¿Qué hay barato para el 20?'\n"
                "• 'Busco de Bogotá a Madrid'\n\n"
                "Sin formularios, yo voy infiriendo."
            )
        if p.presupuesto is not None:
            desde = p.origen or "tu ciudad"
            return MensajeSalida(
                f"Con tu presupuesto {_moneda(p.presupuesto)} y desde {desde}, "
                "pronto te sugeriré destinos. Ahora estoy en fase de prueba.",
                opciones=["Busca para este presupuesto", "Cambiar presupuesto"],
            )
        if any(k in t for k in ("vuelo", "viajar", "viaje", "barato")):
            return MensajeSalida(
                "Genial, dime tu presupuesto (ej: '200 dólares') y te doy opciones."
            )
        return MensajeSalida(
            "Te leo, pero aún estoy en fase de prueba. Escribe /help para ver qué entiendo."
        )


def _extraer_monto(texto: str) -> Optional[int]:
    m = PRESUPUESTO_RE.search(texto)
    if not m:
        return None
    try:
        return int(m.group("cant").replace(".", "").replace(",", ""))
    except (ValueError, AttributeError):
        return None


def _moneda(numero: int) -> str:
    return f"${numero:,.0f}".replace(",", ".")


def _ahora() -> str:
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")