"""ActionExecutor: ejecuta las decisiones sobre FlightClient y NLG.

Valida ANTES de llamar a Google Flights (análisis §2.6): si falta un slot
crítico no se llama a la API, se pregunta. El LLM solo participa en
chitchat (tono), nunca en datos de vuelos ni precios.
"""
from __future__ import annotations

import logging
from typing import Optional

from app import llm_router
from app.destinos import DESTINOS
from app.dialogue.slot_manager import PREGUNTAS
from app.flight_client import FlightClient, OpcionVuelo
from app.fotos import foto_destino
from app.formatter import formatear_opciones
from app.models import MensajeSalida, UserState

log = logging.getLogger(__name__)

_PERSONA_CHITCHAT = (
    "Eres 'Asistente Vuelos', cálido y breve, en español. Solo charla; "
    "no inventes precios ni datos de vuelos. Si preguntan por vuelos, "
    "invita amablemente a decir destino y presupuesto."
)

_PLANTILLAS_OFFLINE = (
    "¡Hola! Cuéntame: ¿a dónde te gustaría viajar y con cuánto presupuesto? 😊",
    "Te leo ✈️. Dime destino y presupuesto y te busco opciones baratas.",
    "Aquí estoy. Por ejemplo: 'de Bogotá a San Andrés, 2 personas, 1 millón por persona'.",
)


def _bruto(o: OpcionVuelo) -> dict:
    return {
        "destino": o.destino,
        "fecha": o.fecha,
        "precio_cop": o.precio_cop,
        "aerolinea": o.aerolinea,
    }


class ActionExecutor:

    def __init__(self, flight: Optional[FlightClient] = None) -> None:
        self.flight = flight or FlightClient()

    async def buscar(self, slots, estado: UserState) -> MensajeSalida:
        """SEARCH / SEARCH_RANGO con slots ya validados por SlotManager.

        Guardas defensivas ANTES de llamar a Google Flights (análisis §2.6):
        sin presupuesto o con origen sin IATA no se consulta la API.
        """
        if not slots.presupuesto_cop:
            return MensajeSalida(PREGUNTAS["presupuesto"])
        origen = slots.origen or "Bogota"
        if not DESTINOS.get(origen):
            return MensajeSalida(
                f"No conozco aeropuerto para salir desde *{origen}* 🛫. "
                "¿Desde qué otra ciudad sales? (ej: Bogotá, Medellín, Cali)"
            )
        cop = slots.presupuesto_cop
        if slots.rango_meses:
            opciones = await self.flight.buscar_rango(
                origen, cop, slots.rango_meses, destino=slots.destino,
                pasajeros=slots.pasajeros,
            )
        else:
            opciones = await self.flight.buscar(
                origen, cop, "COP", fecha=slots.fecha_iso, destino=slots.destino,
                pasajeros=slots.pasajeros,
            )

        if not opciones:
            return MensajeSalida(
                "No encontré opciones ahora para esos datos 🤔. ¿Probamos otra "
                "fecha, otro presupuesto o decí 'olvida todo' para empezar de cero?"
            )

        estado.opciones_recientes = [_bruto(o) for o in opciones]
        titulo = f"✈️ {origen} → {slots.destino}" if slots.destino else \
            f"✈️ Lo más económico desde {origen}"
        texto = f"{titulo}\n\n{formatear_opciones(opciones, cop, pasajeros=slots.pasajeros)}"
        foto = await foto_destino(opciones[0].destino)
        return MensajeSalida(
            texto, opciones=["Cambiar presupuesto", "Otra fecha"], foto_url=foto,
        )

    async def seleccionar(self, numero: int, estado: UserState) -> MensajeSalida:
        """'la 2' -> re-busca el vuelo guardado en esa posición."""
        recientes = estado.opciones_recientes
        if not recientes or not 1 <= numero <= len(recientes):
            return MensajeSalida(
                "No tengo esa opción en la lista. Pídeme que busque vuelos primero."
            )
        eleccion = recientes[numero - 1]
        slots = estado.slots.model_copy(update={
            "destino": eleccion.get("destino"),
            "fecha_iso": eleccion.get("fecha") or None,
        })
        if not slots.presupuesto_cop:
            return MensajeSalida(PREGUNTAS["presupuesto"])
        return await self.buscar(slots, estado)

    async def chitchat(self, texto: str, estado: UserState) -> MensajeSalida:
        """NLG de charla: LLM solo para tono; plantillas si no hay proveedor."""
        respuesta, _ = await llm_router.generar(
            _PERSONA_CHITCHAT, texto, historial=estado.history_summary,
        )
        if respuesta:
            log.info("Chitchat vía LLM")
            return MensajeSalida(respuesta)
        return MensajeSalida(_PLANTILLAS_OFFLINE[len(texto) % len(_PLANTILLAS_OFFLINE)])
