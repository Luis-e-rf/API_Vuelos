"""Tests de ProfileStore v2 contra un Upstash simulado (MockTransport).

Verifican la sintaxis EXACTA del wire contra la doc oficial de Upstash:
- guardar: POST /set/{key}?EX={ttl} con el JSON como body (el body se
  agrega como ÚLTIMO parámetro del comando; los modifiers van en query).
- borrar: comando DEL -> /del/{key} (no existe /delete/).
- errores de Upstash ({"error": ...}) se registran y no rompen el flujo.
"""
from __future__ import annotations

import json
import logging

import httpx
import pytest

from app.models import UserState
from app.profile_store import ProfileStore

_TTL = 60 * 60 * 24 * 30


def _store(handler) -> ProfileStore:
    return ProfileStore(
        url="https://fake.upstash.io", token="tok",
        transport=httpx.MockTransport(handler),
    )


async def test_guardar_usa_set_con_ex_en_query_y_body_json():
    vistas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        return httpx.Response(200, json={"result": "OK"})

    await _store(handler).guardar_estado(UserState(), "c1", "whatsapp")
    req = vistas[0]
    assert req.method == "POST"
    assert req.url.path == "/set/estado:whatsapp:c1"
    assert req.url.params["EX"] == str(_TTL)
    body = json.loads(req.content)
    assert body["version"] == 2


async def test_guardar_registra_error_de_upstash(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "ERR syntax error"})

    with caplog.at_level(logging.ERROR, logger="app.profile_store"):
        await _store(handler).guardar_estado(UserState(), "c1", "whatsapp")
    assert any("ERR syntax error" in r.message for r in caplog.records)


async def test_borrar_usa_del():
    vistas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        return httpx.Response(200, json={"result": 1})

    assert await _store(handler).borrar("c1", "whatsapp") is True
    assert vistas[0].url.path == "/del/estado:whatsapp:c1"


async def test_borrar_error_devuelve_false(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "ERR unknown command"})

    with caplog.at_level(logging.ERROR, logger="app.profile_store"):
        assert await _store(handler).borrar("c1", "whatsapp") is False


async def test_leer_recupera_estado_guardado_roundtrip():
    datos: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        clave = request.url.path.split("/", 2)[2]  # quita /set o /get
        if request.url.path.startswith("/set"):
            datos[clave] = request.content
            return httpx.Response(200, json={"result": "OK"})
        crudo = datos.get(clave)
        return httpx.Response(200, json={"result": crudo.decode() if crudo else None})

    store = _store(handler)
    estado = UserState()
    estado.slots.destino = "Cartagena"
    estado.opciones_recientes = [{
        "destino": "San Andres", "fecha": "2027-01-15",
        "precio_cop": 864000, "aerolinea": "LATAM",
    }]
    await store.guardar_estado(estado, "c1", "whatsapp")
    leido = await store.leer_estado("c1", "whatsapp")
    assert leido.slots.destino == "Cartagena"
    assert leido.opciones_recientes[0]["destino"] == "San Andres"


async def test_leer_con_error_devuelve_estado_fresco():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "WRONGPASS invalid password"})

    leido = await _store(handler).leer_estado("c1", "whatsapp")
    assert leido.version == 2
    assert leido.slots.destino is None
