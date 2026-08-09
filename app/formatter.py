from __future__ import annotations

from app.flight_client import OpcionVuelo


def formatear_opciones(opciones: list[OpcionVuelo], presupuesto_cop: int) -> str:
    """Convierte las ofertas en un mensaje cálido para el usuario."""
    if not opciones:
        return (
            f"Con tu presupuesto de *{presupuesto_cop:,.0f} COP* ({_usd(presupuesto_cop)}) "
            "no encontré opciones por ahora. ¿Quieres ajustar el presupuesto o probar otra fecha?"
        )

    lineas = "Aquí tienes algunas opciones que se ajustan a tu presupuesto:\n\n"
    lineas += "\n".join(o.cabecera() for o in opciones)
    lineas += (
        f"\n\n💡 Todas quedan dentro de tus *{presupuesto_cop:,.0f} COP*. "
        "¿Te interesa alguna para ampliar los detalles?"
    )
    return lineas


def _usd(cop: int) -> str:
    return f"≈ ${round(cop / 4000):,} USD"