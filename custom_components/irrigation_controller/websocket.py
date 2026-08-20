from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from .const import DOMAIN


def _controller(hass: HomeAssistant):
    return hass.data[DOMAIN]["controller"]


def _store(hass: HomeAssistant):
    return hass.data[DOMAIN]["store"]


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/state"})
@websocket_api.async_response
async def ws_state(hass, connection, msg):
    connection.send_result(msg["id"], _controller(hass).snapshot())


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/set_master", vol.Required("enabled"): bool})
@websocket_api.async_response
async def ws_set_master(hass, connection, msg):
    await _store(hass).set_master(msg["enabled"])
    connection.send_result(msg["id"], _controller(hass).snapshot())


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/save_zone", vol.Required("zone"): dict})
@websocket_api.async_response
async def ws_save_zone(hass, connection, msg):
    zone = dict(msg["zone"])
    if not zone.get("name") or not zone.get("valve_entity"):
        connection.send_error(msg["id"], "invalid_zone", "Nome e valvola sono obbligatori")
        return
    zone.setdefault("enabled", True)
    zone.setdefault("moisture_enabled", False)
    zone.setdefault("max_minutes", 720)
    await _store(hass).upsert_zone(zone)
    connection.send_result(msg["id"], _controller(hass).snapshot())


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/delete_zone", vol.Required("zone_id"): str})
@websocket_api.async_response
async def ws_delete_zone(hass, connection, msg):
    await _store(hass).delete_zone(msg["zone_id"])
    connection.send_result(msg["id"], _controller(hass).snapshot())


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/save_program", vol.Required("program"): dict})
@websocket_api.async_response
async def ws_save_program(hass, connection, msg):
    program: dict[str, Any] = dict(msg["program"])
    if not program.get("name"):
        connection.send_error(msg["id"], "invalid_program", "Il nome del programma è obbligatorio")
        return
    program.setdefault("enabled", True)
    program.setdefault("weekdays", [])
    program.setdefault("start_times", [])
    program.setdefault("sun_event", "none")
    program.setdefault("sun_offset_minutes", 0)
    program.setdefault("steps", [])
    await _store(hass).upsert_program(program)
    connection.send_result(msg["id"], _controller(hass).snapshot())


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/delete_program", vol.Required("program_id"): str})
@websocket_api.async_response
async def ws_delete_program(hass, connection, msg):
    await _store(hass).delete_program(msg["program_id"])
    connection.send_result(msg["id"], _controller(hass).snapshot())


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/run", vol.Required("program_id"): str})
@websocket_api.async_response
async def ws_run(hass, connection, msg):
    try:
        await _controller(hass).async_run_program(msg["program_id"], "manuale")
        connection.send_result(msg["id"], True)
    except RuntimeError as exc:
        connection.send_error(msg["id"], "run_error", str(exc))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/stop"})
@websocket_api.async_response
async def ws_stop(hass, connection, msg):
    await _controller(hass).async_stop()
    connection.send_result(msg["id"], True)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/skip"})
@websocket_api.async_response
async def ws_skip(hass, connection, msg):
    await _controller(hass).async_skip_zone()
    connection.send_result(msg["id"], True)


def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_state)
    websocket_api.async_register_command(hass, ws_set_master)
    websocket_api.async_register_command(hass, ws_save_zone)
    websocket_api.async_register_command(hass, ws_delete_zone)
    websocket_api.async_register_command(hass, ws_save_program)
    websocket_api.async_register_command(hass, ws_delete_program)
    websocket_api.async_register_command(hass, ws_run)
    websocket_api.async_register_command(hass, ws_stop)
    websocket_api.async_register_command(hass, ws_skip)
