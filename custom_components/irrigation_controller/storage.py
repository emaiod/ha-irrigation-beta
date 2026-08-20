from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION

DEFAULT_DATA: dict[str, Any] = {
    "master_enabled": True,
    "zones": [],
    "programs": [],
    "logs": [],
}


class IrrigationStore:
    """Persistent irrigation configuration backed by Home Assistant storage."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.data: dict[str, Any] = deepcopy(DEFAULT_DATA)

    async def async_load(self) -> None:
        loaded = await self._store.async_load()
        if loaded:
            self.data = {**deepcopy(DEFAULT_DATA), **loaded}

    async def async_save(self) -> None:
        await self._store.async_save(self.data)

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self.data)

    async def set_master(self, enabled: bool) -> None:
        self.data["master_enabled"] = enabled
        await self.async_save()

    async def upsert_zone(self, payload: dict[str, Any]) -> dict[str, Any]:
        zone = dict(payload)
        zone.setdefault("id", uuid4().hex)
        existing = next((i for i, item in enumerate(self.data["zones"]) if item["id"] == zone["id"]), None)
        if existing is None:
            self.data["zones"].append(zone)
        else:
            self.data["zones"][existing] = zone
        await self.async_save()
        return deepcopy(zone)

    async def delete_zone(self, zone_id: str) -> None:
        self.data["zones"] = [z for z in self.data["zones"] if z["id"] != zone_id]
        for program in self.data["programs"]:
            program["steps"] = [s for s in program.get("steps", []) if s["zone_id"] != zone_id]
        await self.async_save()

    async def upsert_program(self, payload: dict[str, Any]) -> dict[str, Any]:
        program = dict(payload)
        program.setdefault("id", uuid4().hex)
        existing = next((i for i, item in enumerate(self.data["programs"]) if item["id"] == program["id"]), None)
        if existing is None:
            self.data["programs"].append(program)
        else:
            self.data["programs"][existing] = program
        await self.async_save()
        return deepcopy(program)

    async def delete_program(self, program_id: str) -> None:
        self.data["programs"] = [p for p in self.data["programs"] if p["id"] != program_id]
        await self.async_save()

    async def append_log(self, item: dict[str, Any]) -> None:
        self.data["logs"].insert(0, item)
        self.data["logs"] = self.data["logs"][:300]
        await self.async_save()
