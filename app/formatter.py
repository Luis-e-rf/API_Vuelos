from __future__ import annotations

from app.flight_client import OpcionVuelo

# Tasas de cambio aproximadas (actualizar periódicamente)
# En producción, usar una API como exchangerate-api.com
_USD_COP = 4000
_EUR_COP = 4400


def formatear_opciones(opciones: list[OpcionVuelo], presupuesto_cop: int, pasajeros: int = 1) -> str:
    """Convierte las ofertas en un mensaje cálido, numeradas para escoger."""
    if not opciones:
        return (
            f"Con tu presupuesto de *{presupuesto_cop:,.0f} COP* ({_usd(presupuesto_cop)}) "
            "no encontré opciones por ahora. ¿Quieres ajustar el presupuesto o probar otra fecha?"
        )

    lineas = ""
    for i, o in enumerate(opciones, start=1):
        lineas += f"{i}. {o.cabecera()}\n"

    dentro = all(o.precio_cop <= presupuesto_cop for o in opciones)
    if dentro:
        lineas += f"\n💡 Todas quedan dentro de tus *{presupuesto_cop:,.0f} COP*."
    elif pasajeros > 1:
        lineas += f"\n💡 Precios por *{pasajeros} personas* (ya multiplicados)."
    else:
        lineas += f"\n💡 Algunas se pasan de tu presupuesto de *{presupuesto_cop:,.0f} COP*."
    lineas += " Dime el número que te gusta o el destino ('la 2', 'cartagena') y te doy detalles."
    return lineas


def _usd(cop: int) -> str:
    return f"≈ ${round(cop / _USD_COP):,} USD"
