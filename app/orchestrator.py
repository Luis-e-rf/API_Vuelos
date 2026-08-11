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
from app.intents import Interpretador
from app.links import link_google_flights
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
    """Centro de la conversación. Canal-agnóstico.

    El entendimiento del texto libre lo hace el Interpretador (LLM, con
    heurística local de respaldo). El orquestador solo despacha la Intencion.
    """

    def __init__(self, store: Optional[ProfileStore] = None) -> None:
        self.store = store or ProfileStore()
        self.flight = FlightClient()
        self.interprete = Interpretador()

    async def procesar(self, mensaje: MensajeEntrada, sender: Sender) -> None:
        perfil = await self.store.leer(mensaje.chat_id, mensaje.canal)
        perfil.timestamps.append(_ahora())
        if self._inferir_perfil(mensaje, perfil):
            await self.store.guardar(perfil, mensaje.canal)
        salida = await self._construir_respuesta(mensaje, perfil)
        await sender.enviar(mensaje.chat_id, salida)

    # --- inferencia implícita del perfil --------------------------------

    def _inferir_perfil(self, m: MensajeEntrada, p: Perfil) -> bool:
        """Guarda datos que el usuario 'suelta' sin preguntar."""
        cambio = False

        monto = _extraer_monto(m.texto)
        if monto and monto != p.presupuesto:
            p.presupuesto = monto
            p.moneda = _detectar_moneda(m.texto)
            cambio = True

        if not monto:
            millon = re.search(
                r"(?:(\d[\d.,]*)\s*|un\s*|una\s*)?millon(?:es)?", m.texto.lower()
            )
            if millon:
                cantidad = millon.group(1)
                p.presupuesto = (int(cantidad.replace(".", "").replace(",", "")) if cantidad else 1) * 1_000_000
                p.moneda = "COP"
                cambio = True
            else:
                mil = re.search(r"(\d[\d.,]*)\s*mil", m.texto.lower())
                if mil:
                    p.presupuesto = int(mil.group(1).replace(".", "").replace(",", "")) * 1000
                    p.moneda = "COP"
                    cambio = True

        # origen marcado explícitamente ("desde Bogotá", "salgo de X")
        if _marcador_origen(m.texto):
            ciudad = normalizar_destino(m.texto)
            if ciudad and ciudad != p.origen:
                p.origen = ciudad
                cambio = True

        if p.origen is None and m.ubicacion:
            pass  # TODO: reverse-geocode lat/long
        return cambio

    # --- construcción de la respuesta -----------------------------------

    async def _construir_respuesta(self, m: MensajeEntrada, p: Perfil) -> MensajeSalida:
        t = m.texto.strip()

        if p.esperando == "presupuesto" and p.presupuesto is not None:
            p.esperando = None
            await self.store.guardar(p, m.canal)
            return MensajeSalida(
                f"Perfecto, quedó en {_moneda(p.presupuesto)}. ¿Hacia dónde quieres ir?",
                opciones=["Busca para este presupuesto"],
            )

        intent = await self.interprete.interpretar(
            t, opciones_recientes=p.opciones_recientes, presupuesto_actual=p.presupuesto
        )

        # "si/ok/dale" después de "¿Busco opciones?" -> ejecuta la búsqueda
        if _es_afirmacion(t):
            ultima_fecha = p.opciones_recientes[0].get("fecha") if p.opciones_recientes else None
            if p.destino:
                return await self._respuesta_destino(p, m, p.destino, fecha=ultima_fecha)
            return await self._respuesta_buscar(p, m, fecha=ultima_fecha)

        # "somos 2" siempre se aplica, incluso dentro de otra petición
        if intent.pasajeros and intent.accion not in ("pasajeros", "saludo", "ayuda", "guardar_viaje"):
            if intent.pasajeros != p.pasajeros:
                p.pasajeros = max(1, intent.pasajeros)
                await self.store.guardar(p, m.canal)

        if intent.accion == "saludo":
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
                "• 'somos 2 personas'\n\n"
                "Sin formularios, voy entendiendo poco a poco."
            )
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
            return await self._respuesta_destino(p, m, intent.destino, fecha=intent.fecha, aerolinea=intent.aerolinea)

        if intent.accion == "rango" and intent.rango_meses:
            return await self._respuesta_rango(p, m, intent.rango_meses, aerolinea=intent.aerolinea)

        if intent.accion == "buscar":
            if intent.destino:
                return await self._respuesta_destino(p, m, intent.destino, fecha=intent.fecha, aerolinea=intent.aerolinea)
            return await self._respuesta_buscar(p, m, fecha=intent.fecha, aerolinea=intent.aerolinea)

        return await self._respuesta_conversacion(m, p)

    # --- respuestas concretas -------------------------------------------

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
            f"pasajeros={p.pasajeros}. Mensaje del usuario: {m.texto}"
        )
        texto, proveedor = await llm_router.generar(_SYSTEM_PERSONA, contexto)
        if texto:
            log.info("LLM (%s) respondió para chat %s", proveedor, m.chat_id)
            return MensajeSalida(texto, opciones=["Busca para este presupuesto", "Ayuda"])
        if p.presupuesto is not None:
            return MensajeSalida(
                f"Con {_moneda(p.presupuesto)} desde {p.origen or 'tu ciudad'} ya te puedo buscar destinos.",
                opciones=["Busca para este presupuesto", "Cambiar presupuesto"],
            )
        if re.search(r"vuelo|viajar|viaje|barato", m.texto.lower()):
            return MensajeSalida("Genial, dime tu presupuesto (ej. '200 dólares') y te doy opciones.")
        return MensajeSalida("Te leo 😊. Escribe /help para ver cómo puedo ayudarte.")

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


_AFIRMACIONES = {
    "si", "sí", "ok", "okey", "dale", "sale", "bueno", "vamos", "claro",
    "sip", "si claro", "sí claro", "afirma", "obvio", "siguiente", "prosigo",
}


def _es_afirmacion(t: str) -> bool:
    return t.strip().lower() in _AFIRMACIONES


def _marcador_origen(texto: str) -> bool:
    t = texto.lower()
    return any(m in t for m in ("desde", "salgo de", "me voy de", "parto de", "saliendo de"))


def _extraer_monto(texto: str) -> Optional[int]:
    m = PRESUPUESTO_RE.search(texto)
    return _limpio(m.group("cant")) if m else None


def _limpio(s: str) -> Optional[int]:
    try:
        return int(s.replace(".", "").replace(",", ""))
    except (ValueError, AttributeError):
        return None


def _detectar_moneda(texto: str) -> str:
    t = texto.lower()
    if "dolar" in t or "usd" in t or "$" in t:
        return "USD"
    if "euro" in t:
        return "EUR"
    if "peso" in t or "mil" in t:
        return "COP"
    return "COP"


def _moneda(numero: int) -> str:
    return f"${numero:,.0f}".replace(",", ".")


def _describir_presupuesto(p: Perfil) -> str:
    moneda = p.moneda or "COP"
    if moneda == "COP":
        usd = round(p.presupuesto / 4000) if p.presupuesto else 0
        return f"{p.presupuesto:,} pesos colombianos (COP), aproximadamente ${usd} dólares"
    return f"{p.presupuesto:,} {moneda}"


def _a_cop(monto: int, moneda: Optional[str]) -> int:
    if moneda == "USD":
        return round(monto * 4000)
    if moneda == "EUR":
        return round(monto * 4400)
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