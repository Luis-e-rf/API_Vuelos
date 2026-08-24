from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class MensajeEntrada:
    """Mensaje normalizado, sin importar el canal de origen (Telegram, WhatsApp, ...)."""

    chat_id: str
    texto: str = ""
    canal: str = "unknown"
    ubicacion: Optional[tuple[float, float]] = None


@dataclass
class MensajeSalida:
    """Respuesta del bot, agnóstica al canal."""

    texto: str
    opciones: list[str] = field(default_factory=list)
    foto_url: Optional[str] = None  # si se set, se manda sendPhoto (Telegram)


@dataclass
class Perfil:
    """Contexto persistido por usuario. Clave = chat_id, valor = este JSON."""

    chat_id: str
    origen: Optional[str] = None
    destino: Optional[str] = None
    presupuesto: Optional[int] = None
    moneda: Optional[str] = None
    pasaporte: Optional[bool] = None
    nombre: Optional[str] = None
    ultimo_destino_sugerido: Optional[str] = None
    esperando: Optional[str] = None
    # Últimas opciones mostradas (para referencias como "la 3")
    opciones_recientes: list[dict] = field(default_factory=list)
    # Número de pasajeros (p. ej. "somos 2")
    pasajeros: int = 1
    # Aerolínea preferida si el usuario la pidió ("con wingo")
    aerolinea: Optional[str] = None
    viajes_guardados: list[dict] = field(default_factory=list)
    timestamps: list[str] = field(default_factory=list)
    historial: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Perfil":
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in known}
        return cls(**clean)
