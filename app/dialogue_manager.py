"""DialogueManager: reemplaza a orchestrator.Orquestador (FASE 3).

Flujo por mensaje:
    1. early-reset determinista ("olvida todo") ANTES del NLU -> store.borrar()
    2. NLU (Extractor: Gemini JSON o determinista) -> RawSlots
    3. SlotManager.decidir() -> Decision (invariantes + completitud)
    4. ActionExecutor ejecuta la decisión
    5. historial SOLO para NLG (máx 5), estado v2 persistido con TTL

Mantiene el contrato del Orquestador legacy: `procesar(mensaje, sender)`.
"""
from __future__ import annotations

import logging

from app.dialogue.executor import ActionExecutor
from app.dialogue.slot_manager import PREGUNTAS, SlotManager
from app.models import MensajeEntrada, MensajeSalida, Sender, UserState
from app.normalizers.text import quitar_tildes
from app.nlu.extractor import VOCAB_RESET, Extractor
from app.profile_store import ProfileStore

log = logging.getLogger(__name__)

_MSG_RESET = (
    "¡Listo! Empezamos de cero. Cuéntame hacia dónde quieres ir y con cuánto presupuesto 😊"
)


class DialogueManager:
    """Centro de la conversación, canal-agnóstico, sin Dios-objeto."""

    def __init__(self, store: ProfileStore | None = None,
                 flight=None) -> None:
        self.store = store or ProfileStore()
        self.nlu = Extractor()
        self.slots = SlotManager()
        self.executor = ActionExecutor(flight)

    async def procesar(self, mensaje: MensajeEntrada, sender: Sender) -> None:
        chat_id, canal = mensaje.chat_id, mensaje.canal
        texto = mensaje.texto.strip()

        # 1) reset temprano determinista: ni pasa por el LLM
        if any(v in quitar_tildes(texto.lower()) for v in VOCAB_RESET):
            await self.store.borrar(chat_id, canal)
            await sender.enviar(chat_id, MensajeSalida(_MSG_RESET))
            return

        estado = await self.store.leer_estado(chat_id, canal)

        # 2-3) NLU + política de slots
        raw = await self.nlu.extract(texto)
        decision = self.slots.decidir(estado.slots, raw)
        estado.slots = decision.slots  # fusión confirmada: persiste y sirve de base
        log.info(
            "chat=%s nlu_raw=%s normalized=%s action=%s",
            chat_id, raw.model_dump(), decision.slots.model_dump(), decision.accion,
        )

        # 4) ejecutar la decisión
        salida: MensajeSalida | None = None
        if decision.accion == "reset":
            await self.store.borrar(chat_id, canal)
            await sender.enviar(chat_id, MensajeSalida(_MSG_RESET))
            return
        if decision.accion == "ask_slot":
            estado.pending_question = decision.slot_faltante
            salida = MensajeSalida(PREGUNTAS[decision.slot_faltante])
        elif decision.accion == "chitchat":
            salida = await self.executor.chitchat(texto, estado)
        elif decision.accion == "select_option":
            salida = await self.executor.seleccionar(decision.numero_opcion, estado)
        else:  # search / search_rango
            estado.pending_question = None
            salida = await self.executor.buscar(decision.slots, estado)

        # 5) historial solo para NLG + persistencia v2
        estado.agregar_historial("user", texto)
        if salida:
            estado.agregar_historial("assistant", salida.texto)
            await sender.enviar(chat_id, salida)
        await self.store.guardar_estado(estado, chat_id, canal)
