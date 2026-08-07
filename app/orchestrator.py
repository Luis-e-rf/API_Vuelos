from __future__ import annotations

import datetime
import logging
import re
from typing import Optional, Protocol

from app import llm_router
from app.models import MensajeEntrada, MensajeSalida, Perfil
from app.profile_store import ProfileStore

log = logging.getLogger(__name__)

PRESUPUESTO_RE = re.compile(
    r"(?P<cant>[0-9][0-9.,]*)\s*(?:usd|dolares|dólares|euros|pesos|\$)", re.I
)

_SYSTEM_PERSONA = (
    "Eres 'Asistente Vuelos', un asistente amigable de búsqueda de vuelos para una persona común, "
    "quizás mayor de 60 años. Responde corto, cálido, en español, sin tablas complicadas y sin "
    "datos inventados sobre precios exactos (puedes hablar de rangos aproximados)."
)


class Sender:
    async def enviar(self, chat_id: str, salida: MensajeSalida) -> None: ...


class Orquestador:
    """Centro de la conversación. Canal-agnóstico: recibe MensajeEntrada y
    produce respuestas vía sender. Usa el LLM Router si hay proveedor, con
    fallback a reglas locales.
    """

    def __init__(self, store: Optional[ProfileStore] = None) -> None:
        self.store = store or ProfileStore()

    async def procesar(self, mensaje: MensajeEntrada, sender: Sender) -> None:
        perfil = await self.store.leer(mensaje.chat_id, mensaje.canal)
        perfil.timestamps.append(_ahora())
        if self._inferir_perfil(mensaje, perfil):
            await self.store.guardar(perfil, mensaje.canal)
        salida = await self._construir_respuesta(mensaje, perfil)
        await sender.enviar(mensaje.chat_id, salida)

    # --- inferencia implícita del perfil ------------------------------

    def _inferir_perfil(self, m: MensajeEntrada, p: Perfil) -> bool:
        """Guarda datos que el usuario 'suelta' en cualquier mensaje, sin preguntar."""
        t = m.texto.upper()
        cambio = False

        monto = _extraer_monto(m.texto)
        if monto and monto != p.presupuesto:
            p.presupuesto = monto
            cambio = True

        # "300 mil" -> 300.000 (presupuestos en pesos)
        mil = re.search(r"(\d[\d.,]*)\s*mil", m.texto.lower())
        if mil and p.presupuesto == (monto or None):
            p.presupuesto = int(mil.group(1).replace(".", "").replace(",", "")) * 1000
            cambio = True

        if any(c in t for c in ("desde bogota", "salgo de bogota", "desde bogot")):
            p.origen = "Bogota"
            cambio = True
        elif any(c in t for c in ("desde medellin", "salgo de medellin")):
            p.origen = "Medellin"
            cambio = True

        if p.origen is None and m.ubicacion:
            # TODO: reverse-geocode lat/long
            pass
        return cambio

    # --- construcción de la respuesta ---------------------------------

    async def _construir_respuesta(self, m: MensajeEntrada, p: Perfil) -> MensajeSalida:
        t = m.texto.strip().lower()

        # 1) Respuestas fijas de sistema (sin LLM)
        if t in ("/start", "hola", "buenas", "hi"):
            return MensajeSalida(
                "¡Hola! Soy tu asistente de vuelos ✈️. Cuéntame con cuánto cuentas "
                "y hacia dónde quieres ir."
            )
        if t in ("/help", "ayuda", "help", "que haces"):
            return MensajeSalida(
                "Te ayudo a encontrar vuelos. Dime cosas como:\n"
                "• 'Quiero irme, tengo 300 dólares'\n"
                "• '¿Qué hay barato para el 20?'\n"
                "• 'Busco de Bogotá a Madrid'\n\n"
                "Sin formularios, voy infiriendo."
            )

        # 2) Flujo: cambiando presupuesto
        if "cambiar" in t:
            p.esperando = "presupuesto"
            await self.store.guardar(p, m.canal)
            return MensajeSalida("¡Claro! ¿Con cuánto presupuesto cuentas ahora? (ej: 250 dólares)")

        if p.esperando == "presupuesto" and p.presupuesto is not None:
            p.esperando = None
            await self.store.guardar(p, m.canal)
            return MensajeSalida(
                f"Perfecto, quedó en {_moneda(p.presupuesto)}. Ahora dime hacia dónde quieres ir "
                "o pídeme sugerencias.",
                opciones=["Busca para este presupuesto"],
            )

        # 3) Acciones de búsqueda (el usuario escribe libre o toca el botón)
        if any(marca in t for marca in ("busca", "busco", "buscar", "presupuesto")):
            return await self._respuesta_buscar(p, m)

        # 4) Conversación libre: usa LLM si hay proveedor, si no, fallback local
        return await self._respuesta_conversacion(m, p)

    async def _respuesta_buscar(self, p: Perfil, m: MensajeEntrada) -> MensajeSalida:
        if p.presupuesto is None:
            return MensajeSalida("Primero cuéntame tu presupuesto (ej: '300 dólares').")

        prompt = (
            f"El usuario quiere viajar con un presupuesto de {p.presupuesto} dólares. "
            f"Origen: {p.origen or 'no especificado (pregúntalo o asume su país por contexto)'}.\n"
            "Sugiere 3 destinos realistas para ese presupuesto con una idea aproximada de rango de precio "
            "y una frase cálida. Termina preguntando cuál le gusta."
        )
        texto, proveedor = await llm_router.generar(_SYSTEM_PERSONA, prompt)
        if texto:
            p.ultimo_destino_sugerido = "sugerido"
            await self.store.guardar(p, m.canal)
            return MensajeSalida(texto, opciones=["Buscar vuelos", "Cambiar presupuesto"])
        # fallback sin LLM
        return MensajeSalida(
            f"Con {_moneda(p.presupuesto)} desde {p.origen or 'tu ciudad'} te sugeriré opciones pronto. "
            "Estoy en fase de prueba (aún sin motor de vuelos).",
            opciones=["Cambiar presupuesto"],
        )

    async def _respuesta_conversacion(self, m: MensajeEntrada, p: Perfil) -> MensajeSalida:
        contexto = (
            f"Perfil: presupuesto={p.presupuesto}, origen={p.origen or 'desconocido'}. "
            f"Mensaje: {m.texto}"
        )
        texto, _ = await llm_router.generar(_SYSTEM_PERSONA, contexto)
        if texto:
            return MensajeSalida(texto, opciones=["Busca para este presupuesto", "Ayuda"])
        # fallback local (sin LLM configurado)
        if p.presupuesto is not None:
            desde = p.origen or "tu ciudad"
            return MensajeSalida(
                f"Con {_moneda(p.presupuesto)} desde {desde} ya te puedo buscar destinos.",
                opciones=["Busca para este presupuesto", "Cambiar presupuesto"],
            )
        if any(k in m.texto.lower() for k in ("vuelo", "viajar", "viaje", "barato")):
            return MensajeSalida("Genial, dime tu presupuesto (ej: '200 dólares') y te doy opciones.")
        return MensajeSalida(
            "Te leo, pero aún estoy en fase de prueba. Escribe /help para ver qué entiendo."
        )


# --- helpers ---------------------------------------------------------


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