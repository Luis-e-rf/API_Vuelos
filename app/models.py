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
    timestamps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Perfil":
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in known}
        return cls(**clean)
