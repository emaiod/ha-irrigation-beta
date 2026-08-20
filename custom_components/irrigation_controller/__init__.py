from __future__ import annotations

from pathlib import Path

import voluptuous as vol
from homeassistant.components.frontend import async_register_built_in_panel, async_remove_panel
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN, PANEL_ELEMENT, PANEL_URL, STATIC_URL
from .controller import IrrigationController
from .storage import IrrigationStore
from .websocket import async_register as async_register_websocket

PLATFORMS: list[Platform] = []


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up integration-wide websocket commands once."""
    hass.data.setdefault(DOMAIN, {})
    if not hass.data[DOMAIN].get("websocket_registered"):
        async_register_websocket(hass)
        hass.data[DOMAIN]["websocket_registered"] = True
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Irrigation Controller from a config entry."""
    data = hass.data.setdefault(DOMAIN, {})
    store = IrrigationStore(hass)
    await store.async_load()
    controller = IrrigationController(hass, store)
    await controller.async_start()
    data["store"] = store
    data["controller"] = controller

    frontend_dir = Path(__file__).parent / "frontend"
    if not data.get("static_registered"):
        await hass.http.async_register_static_paths([
            StaticPathConfig(STATIC_URL, str(frontend_dir), cache_headers=False)
        ])
        data["static_registered"] = True

    async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title="Irrigation Controller",
        sidebar_icon="mdi:sprinkler-variant",
        frontend_url_path=PANEL_URL,
        config={
            "_panel_custom": {
                "name": PANEL_ELEMENT,
                "module_url": f"{STATIC_URL}/panel.js",
                "embed_iframe": False,
                "trust_external": False,
            }
        },
        require_admin=False,
        update=True,
    )

    async def run_service(call: ServiceCall) -> None:
        await controller.async_run_program(call.data["program_id"], "servizio")

    async def stop_service(call: ServiceCall) -> None:
        await controller.async_stop()

    async def skip_service(call: ServiceCall) -> None:
        await controller.async_skip_zone()

    hass.services.async_register(
        DOMAIN,
        "run_program",
        run_service,
        schema=vol.Schema({vol.Required("program_id"): cv.string}),
    )
    hass.services.async_register(DOMAIN, "stop", stop_service)
    hass.services.async_register(DOMAIN, "skip_zone", skip_service)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Irrigation Controller."""
    data = hass.data.get(DOMAIN, {})
    controller = data.get("controller")
    if controller:
        await controller.async_shutdown()
    for service in ("run_program", "stop", "skip_zone"):
        hass.services.async_remove(DOMAIN, service)
    async_remove_panel(hass, PANEL_URL)
    data.pop("controller", None)
    data.pop("store", None)
    return True
