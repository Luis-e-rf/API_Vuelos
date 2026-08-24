from __future__ import annotations

import datetime
import logging
import re
from typing import Optional, Protocol

from app import llm_router
from app.destinos import normalizar_destino
from app.flight_client import FlightClient, OpcionVuelo
from app.formatter import formatear_opciones
from app.fotos import foto_destino
from app.intents import Interpretador, Intencion, ResultadoInterpretacion
from app.links import link_google_flights
from app.models import MensajeEntrada, MensajeSalida, Perfil
from app.profile_store import ProfileStore

log = logging.getLogger(__name__)

_EXPIRA_HORAS = 48  # horas antes de limpiar el historial de conversación

_SYSTEM_PERSONA = (
    "Eres 'Asistente Vuelos', un asistente amigable de búsqueda de vuelos para una persona común, "
    "quizás mayor de 60 años. Responde corto, cálido, en español, sin tablas complicadas y sin "
    "datos inventados sobre precios exactos (puedes hablar de rangos aproximados). "
    "Si el usuario menciona un destino, presupuesto o fecha en el perfil, úsalos para responder."
)


class Sender:
    async def enviar(self, chat_id: str, salida: MensajeSalida) -> None: ...


class Orquestador:
    """Centro de la conversación. Canal-agnóstico.

    Arquitectura:
      - LLM extrae intención (con historial y perfil)
      - Orquestador despacha por acción
      - Sesión expira tras 48 horas (historial se limpia, perfil se mantiene)
      - Soporta múltiples intenciones por mensaje
    """

    def __init__(self, store: Optional[ProfileStore] = None) -> None:
        self.store = store or ProfileStore()
        self.flight = FlightClient()
        self.interprete = Interpretador()

    async def procesar(self, mensaje: MensajeEntrada, sender: Sender) -> None:
        perfil = await self.store.leer(mensaje.chat_id, mensaje.canal)

        # Verificar si la sesión sigue activa
        sesion_activa = _verificar_sesion(perfil.ultima_conexion)
        if not sesion_activa and perfil.historial:
            log.info("Sesión expirada para %s, historial limpiado", mensaje.chat_id)
            perfil.historial = []

        # Actualizar timestamp de última conexión
        perfil.ultima_conexion = _ahora()

        # Si el usuario estaba "esperando" una respuesta (cambiar_presupuesto),
        # procesar el mensaje directamente como actualización de ese campo.
        if perfil.esperando:
            respuesta = await self._procesar_esperando(mensaje, perfil)
            if respuesta:
                perfil.historial.append({"role": "user", "content": mensaje.texto})
                perfil.historial.append({"role": "assistant", "content": respuesta.texto})
                if len(perfil.historial) > 20:
                    perfil.historial = perfil.historial[-20:]
                perfil.esperando = None
                await self.store.guardar(perfil, mensaje.canal)
                await sender.enviar(mensaje.chat_id, respuesta)
                return

        # LLM extrae intención con contexto completo
        perfil_dict = perfil.to_dict()
        resultado = await self.interprete.interpretar(
            mensaje.texto,
            opciones_recientes=perfil.opciones_recientes,
            presupuesto_actual=perfil.presupuesto,
            historial=perfil.historial if sesion_activa else [],
            perfil_actual=perfil_dict,
        )

        # Mostrar mensaje de clarificación si el LLM lo pidió
        if resultado.mensaje_clarificacion:
            await sender.enviar(mensaje.chat_id, MensajeSalida(resultado.mensaje_clarificacion))
            return

        # Procesar cada intención
        respuestas = []
        for intent in resultado.intenciones:
            resp = await self._dispatch(intent, mensaje, perfil)
            if resp:
                respuestas.append(resp)

        # Guardar historial
        perfil.historial.append({"role": "user", "content": mensaje.texto})
        for r in respuestas:
            perfil.historial.append({"role": "assistant", "content": r.texto})
        if len(perfil.historial) > 20:
            perfil.historial = perfil.historial[-20:]

        await self.store.guardar(perfil, mensaje.canal)

        for r in respuestas:
            await sender.enviar(mensaje.chat_id, r)

    async def _dispatch(
        self, intent: Intencion, m: MensajeEntrada, p: Perfil
    ) -> Optional[MensajeSalida]:
        """Despacha una intención a la respuesta correspondiente."""

        if intent.accion == "olvidar_todo":
            return await self._respuesta_olvidar_todo(m, p)

        if intent.accion == "saludo":
            if p.presupuesto and p.destino:
                return MensajeSalida(
                    f"¡Hola! Veo que estabas buscando vuelos a *{p.destino}* "
                    f"con {_moneda(p.presupuesto)}. ¿Quieres que siga buscando o algo nuevo?"
                )
            return MensajeSalida(
                "¡Hola! Soy tu asistente de vuelos ✈️. Cuéntame con cuánto cuentas "
                "y hacia dónde quieres ir."
            )

        if intent.accion == "ayuda":
            return MensajeSalida(
                "Te ayudo a encontrar vuelos. Puedes decirme:\n"
                "• 'busca barato para 600 mil desde Bogotá'\n"
                "• 'la opción 2' o el nombre de una ciudad\n"
                "• 'lo más económico en 3 meses'\n"
                "• 'somos 2 personas'\n"
                "• 'olvida todo' para empezar de cero\n\n"
                "Sin formularios, voy entendiendo poco a poco."
            )

        if intent.accion == "actualizar_perfil":
            return await self._respuesta_actualizar_perfil(intent, m, p)

        if intent.accion == "cambiar_presupuesto":
            p.esperando = "presupuesto"
            await self.store.guardar(p, m.canal)
            return MensajeSalida("¡Claro! ¿Con cuánto presupuesto cuentas ahora? (ej: 250 dólares)")

        if intent.accion == "pasajeros" and intent.pasajeros:
            p.pasajeros = max(1, intent.pasajeros)
            await self.store.guardar(p, m.canal)
            return MensajeSalida(
                f"¡Anotado, {p.pasajeros} pasajeros! Los precios que te muestre serán "
                "por todo el grupo. ¿Busco opciones?",
                opciones=["Busca para este presupuesto"],
            )

        if intent.accion == "guardar_viaje" and p.opciones_recientes:
            guardado = p.opciones_recientes[0]
            p.viajes_guardados.append(guardado)
            await self.store.guardar(p, m.canal)
            return MensajeSalida(
                f"Guardé tu vuelo a *{guardado.get('destino')}* por "
                f"{_moneda(guardado.get('precio_cop', 0))}. Escribe 'ver guardados' cuando quieras verlo."
            )

        if intent.accion == "ver_guardados":
            if not p.viajes_guardados:
                return MensajeSalida(
                    "Todavía no has guardado viajes. Cuando elijas uno, dime 'guarda este viaje'."
                )
            lineas = "Tus viajes guardados:\n" + "\n".join(
                f"• {v.get('destino')} · {_moneda(v.get('precio_cop', 0))} · {v.get('fecha', '')}"
                for v in p.viajes_guardados
            )
            return MensajeSalida(lineas)

        if intent.accion == "comprar":
            return await self._respuesta_comprar(p, m)

        if intent.accion == "elegir_opcion" and intent.numero:
            return await self._respuesta_opcion(p, m, intent.numero)

        if intent.accion == "elegir_destino" and intent.destino:
            # Actualizar perfil con datos del LLM
            if intent.origen:
                p.origen = intent.origen
            if intent.presupuesto and intent.moneda:
                p.presupuesto = intent.presupuesto
                p.moneda = intent.moneda
            if intent.pasajeros:
                p.pasajeros = max(1, intent.pasajeros)
            return await self._respuesta_destino(
                p, m, intent.destino, fecha=intent.fecha, aerolinea=intent.aerolinea,
            )

        if intent.accion == "rango" and intent.rango_meses:
            if intent.origen:
                p.origen = intent.origen
            if intent.presupuesto and intent.moneda:
                p.presupuesto = intent.presupuesto
                p.moneda = intent.moneda
            return await self._respuesta_rango(p, m, intent.rango_meses, aerolinea=intent.aerolinea)

        if intent.accion == "buscar":
            if intent.origen:
                p.origen = intent.origen
            if intent.presupuesto and intent.moneda:
                p.presupuesto = intent.presupuesto
                p.moneda = intent.moneda
            if intent.pasajeros:
                p.pasajeros = max(1, intent.pasajeros)
            if intent.destino:
                return await self._respuesta_destino(
                    p, m, intent.destino, fecha=intent.fecha, aerolinea=intent.aerolinea,
                )
            return await self._respuesta_buscar(p, m, fecha=intent.fecha, aerolinea=intent.aerolinea)

        return await self._respuesta_conversacion(m, p)

    # --- respuestas concretas -------------------------------------------

    async def _respuesta_olvidar_todo(self, m: MensajeEntrada, p: Perfil) -> MensajeSalida:
        """Resetea todo el perfil y historial del usuario."""
        p.origen = None
        p.destino = None
        p.presupuesto = None
        p.moneda = None
        p.pasajeros = 1
        p.aerolinea = None
        p.opciones_recientes = []
        p.viajes_guardados = []
        p.historial = []
        p.esperando = None
        p.ultimo_destino_sugerido = None
        await self.store.guardar(p, m.canal)
        return MensajeSalida(
            "Listo, empezamos de cero. Cuéntame: ¿hacia dónde quieres ir y con cuánto presupuesto?"
        )

    async def _respuesta_actualizar_perfil(
        self, intent: Intencion, m: MensajeEntrada, p: Perfil
    ) -> MensajeSalida:
        """Actualiza un campo del perfil (cambio de parecer)."""
        campo = intent.campo_actualizado
        if campo == "destino" and intent.destino:
            p.destino = intent.destino
            await self.store.guardar(p, m.canal)
            if p.presupuesto:
                return await self._respuesta_destino(p, m, intent.destino)
            return MensajeSalida(
                f"Perfecto, cambiado a *{intent.destino}*. ¿Con cuánto presupuesto cuentas?"
            )
        if campo == "presupuesto" and intent.presupuesto:
            p.presupuesto = intent.presupuesto
            p.moneda = intent.moneda or "COP"
            await self.store.guardar(p, m.canal)
            return MensajeSalida(
                f"Presupuesto actualizado a {_moneda(p.presupuesto)}. ¿Hacia dónde quieres ir?"
            )
        if campo == "pasajeros" and intent.pasajeros:
            p.pasajeros = max(1, intent.pasajeros)
            await self.store.guardar(p, m.canal)
            return MensajeSalida(
                f"Anotado, {p.pasajeros} pasajeros. ¿Busco opciones?"
            )
        return MensajeSalida("¿Qué te gustaría cambiar? Puedo actualizar destino, presupuesto o pasajeros.")

    async def _procesar_esperando(self, m: MensajeEntrada, p: Perfil) -> Optional[MensajeSalida]:
        """Procesa un mensaje cuando el usuario está 'esperando' una respuesta."""
        esperando = p.esperando
        if esperando == "presupuesto":
            # Intentar extraer presupuesto del mensaje
            resultado = await self.interprete.interpretar(
                m.texto, perfil_actual=p.to_dict(),
            )
            if resultado.intenciones:
                intent = resultado.intenciones[0]
                if intent.presupuesto:
                    p.presupuesto = intent.presupuesto
                    p.moneda = intent.moneda or "COP"
                    return MensajeSalida(
                        f"Presupuesto actualizado a {_moneda(p.presupuesto)}. "
                        "¿Hacia dónde quieres ir o busco opciones?"
                    )
            return MensajeSalida(
                "No entendí el presupuesto. Dime algo como '500 dólares' o 'un millón'."
            )
        return None

    async def _respuesta_buscar(
        self, p: Perfil, m: MensajeEntrada, fecha: Optional[str] = None,
        aerolinea: Optional[str] = None,
    ) -> MensajeSalida:
        if p.presupuesto is None:
            return MensajeSalida("Primero cuéntame tu presupuesto (ej: '300 dólares').")
        origen = p.origen or "Bogota"
        cop = _a_cop(p.presupuesto, p.moneda)
        opciones = await self.flight.buscar(
            origen, cop, p.moneda, fecha=fecha, pasajeros=p.pasajeros, aerolinea=aerolinea
        )
        if not opciones:
            if aerolinea:
                return MensajeSalida(
                    f"No encontré vuelos de *{aerolinea}* en esas fechas. "
                    "Dime si quieres probar otra aerolínea o dejar que busque todas."
                )
            return MensajeSalida(
                "En esas fechas no encontré opciones. Dime cuánto presupuesto "
                "manejas o prueba con 'desde Bogotá' para refrescar."
            )
        titulo = "Aquí tienes opciones que se ajustan a tu presupuesto:"
        if fecha:
            titulo = f"Aquí tienes opciones para el *{_fecha_legible(fecha)}*:"
        if aerolinea:
            titulo = f"Solo te muestro vuelos de *{aerolinea}*. Aquí van:"
        return await self._mostrar(p, m, opciones, cop, titulo, aerolinea=aerolinea)

    async def _respuesta_destino(
        self, p: Perfil, m: MensajeEntrada, ciudad: str, fecha: Optional[str] = None,
        aerolinea: Optional[str] = None,
    ) -> MensajeSalida:
        if p.presupuesto is None:
            return MensajeSalida("Primero cuéntame tu presupuesto (ej. '300 dólares').")
        origen = p.origen or "Bogota"
        conduz = _a_cop(p.presupuesto, p.moneda)
        p.destino = ciudad
        await self.store.guardar(p, m.canal)
        opciones = await self.flight.buscar(
            origen, conduz, p.moneda, fecha=fecha, destino=ciudad, pasajeros=p.pasajeros,
            aerolinea=aerolinea,
        )
        if not opciones:
            if aerolinea:
                return MensajeSalida(
                    f"No hay vuelos de *{aerolinea}* a *{ciudad}* ahora. "
                    "¿Pruebo con otra aerolínea o busco todas?",
                    opciones=["Busca todas", "Cambiar presupuesto"],
                )
            return MensajeSalida(
                f"Para ir a *{ciudad}* con {_moneda(p.presupuesto)} no encontré opciones baratas ahora. "
                "¿Probamos otra ciudad o ajustamos el presupuesto?",
                opciones=["Cambiar presupuesto", "Busca para este presupuesto"],
            )
        extra = f" para el *{_fecha_legible(fecha)}*" if fecha else ""
        if aerolinea:
            extra += f" en *{aerolinea}*"
        return await self._mostrar(p, m, opciones, conduz, f"¡Excelente elección! Vuelos a *{ciudad}* {extra}✈️", aerolinea=aerolinea)

    async def _respuesta_opcion(self, p: Perfil, m: MensajeEntrada, numero: int) -> MensajeSalida:
        recientes = p.opciones_recientes
        if numero < 1 or numero > len(recientes):
            return MensajeSalida("No reconozco esa opción. Escribe el nombre del destino o di 'busca'.")
        destino = recientes[numero - 1].get("destino")
        if not destino:
            return MensajeSalida("Ups, no tengo los datos de esa opción. Dime 'busca' para refrescar.")
        fecha_guardada = recientes[numero - 1].get("fecha")
        return await self._respuesta_destino(p, m, destino, fecha=fecha_guardada)

    async def _respuesta_comprar(self, p: Perfil, m: MensajeEntrada) -> MensajeSalida:
        """'lo quiero' -> link de Google Flights para el vuelo que se mostró."""
        if not p.opciones_recientes:
            return MensajeSalida(
                "Aún no te he mostrado ningún vuelo. Pídeme que busque opciones "
                "(ej. 'busca a cartagena') y luego me dices 'lo quiero'."
            )
        mejor = p.opciones_recientes[0]
        destino = mejor.get("destino")
        fecha = mejor.get("fecha")
        origen = p.origen or "Bogota"
        link = link_google_flights(origen, destino, fecha)
        if not link:
            return MensajeSalida(
                f"Para *{destino}* no tengo un enlace de compra aún, pero puedes buscar "
                "en Google Flights por 'Bogota → {destino}'. ¿Buscamos otra cosa?"
            )
        return MensajeSalida(
            f"¡Claro! ✈️ Este es el vuelo a *{destino}* que te mostré:\n\n"
            f"📅 {_fecha_legible(fecha) if fecha else ''}\n"
            f"💰 {_moneda(mejor.get('precio_cop', 0))} COP\n\n"
            f"🔗 {link}\n\n"
            "Ahí eliges aerolínea, horario y pagas directo en Google Flights. 😉",
            opciones=["Busca más opciones", "Cambiar presupuesto"],
        )

    async def _respuesta_rango(
        self, p: Perfil, m: MensajeEntrada, meses: int,
        aerolinea: Optional[str] = None,
    ) -> MensajeSalida:
        if p.presupuesto is None:
            return MensajeSalida("Primero cuéntame tu presupuesto.")
        origen = p.origen or "Bogota"
        conduz = _a_cop(p.presupuesto, p.moneda)
        opciones = await self.flight.buscar_rango(
            origen, conduz, meses, destino=p.destino, pasajeros=p.pasajeros, aerolinea=aerolinea
        )
        if not opciones:
            return MensajeSalida(f"Por ahora no encontré vuelos en los próximos {meses} meses. Intenta otro rango.")
        return await self._mostrar(p, m, opciones, conduz, f"Lo más económico en los próximos {meses} meses:", aerolinea=aerolinea)

    async def _respuesta_conversacion(self, m: MensajeEntrada, p: Perfil) -> MensajeSalida:
        contexto = (
            f"Perfil: presupuesto={p.presupuesto}, moneda={p.moneda}, origen={p.origen or 'desconocido'}, "
            f"pasajeros={p.pasajeros}, destino={p.destino or 'ninguno'}. "
            f"Mensaje del usuario: {m.texto}"
        )
        texto, proveedor = await llm_router.generar(
            _SYSTEM_PERSONA, contexto, historial=p.historial,
        )
        if texto:
            log.info("LLM (%s) respondió para chat %s", proveedor, m.chat_id)
            return MensajeSalida(texto, opciones=["Busca para este presupuesto", "Ayuda"])
        if p.presupuesto is not None:
            return MensajeSalida(
                f"Con {_moneda(p.presupuesto)} desde {p.origen or 'tu ciudad'} ya te puedo buscar destinos.",
                opciones=["Busca para este presupuesto", "Cambiar presupuesto"],
            )
        return MensajeSalida("Te leo 😊. Escribe 'ayuda' para ver cómo puedo ayudarte.")

    # --- cómo mostrar opciones -----------------------------------------

    async def _mostrar(
        self, p: Perfil, m: MensajeEntrada, opciones: list[OpcionVuelo], cop: int, titulo: str,
        aerolinea: Optional[str] = None,
    ) -> MensajeSalida:
        p.opciones_recientes = [_bruto(o) for o in opciones]
        p.ultimo_destino_sugerido = opciones[0].destino if opciones else None
        if aerolinea:
            p.aerolinea = aerolinea
        await self.store.guardar(p, m.canal)
        texto = f"{titulo}\n\n{formatear_opciones(opciones, cop, pasajeros=p.pasajeros or 1)}"
        foto = await foto_destino(opciones[0].destino) if opciones else None
        return MensajeSalida(texto, opciones=["Cambiar presupuesto", "Más fechas"], foto_url=foto)


# --- helpers ---------------------------------------------------------


def _bruto(o: OpcionVuelo) -> dict:
    return {
        "destino": o.destino,
        "fecha": o.fecha,
        "precio_cop": o.precio_cop,
        "aerolinea": o.aerolinea,
    }


def _verificar_sesion(ultima_conexion: Optional[str]) -> bool:
    """Retorna True si la sesión sigue activa (< 48 horas)."""
    if not ultima_conexion:
        return False
    try:
        ultima = datetime.datetime.fromisoformat(ultima_conexion)
        ahora = datetime.datetime.now(datetime.timezone.utc)
        horas = (ahora - ultima).total_seconds() / 3600
        return horas < _EXPIRA_HORAS
    except (ValueError, TypeError):
        return False


def _moneda(numero: int) -> str:
    return f"${numero:,.0f}".replace(",", ".")


def _a_cop(monto: int, moneda: Optional[str]) -> int:
    # Tasas de cambio aproximadas (actualizar periódicamente)
    # En producción, usar una API como exchangerate-api.com
    _USD_COP = 4000
    _EUR_COP = 4400
    if moneda == "USD":
        return round(monto * _USD_COP)
    if moneda == "EUR":
        return round(monto * _EUR_COP)
    return monto


def _ahora() -> str:
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")


_MESES_ESP = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _fecha_legible(iso: str) -> str:
    """'2027-01-05' -> '5 de enero de 2027'."""
    try:
        d = datetime.date.fromisoformat(iso)
        return f"{d.day} de {_MESES_ESP[d.month - 1]} de {d.year}"
    except ValueError:
        return iso
