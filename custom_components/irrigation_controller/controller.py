from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .storage import IrrigationStore


class IrrigationController:
    """Run irrigation programs using native Home Assistant entities/services."""

    def __init__(self, hass: HomeAssistant, store: IrrigationStore) -> None:
        self.hass = hass
        self.store = store
        self.task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._skip = asyncio.Event()
        self._fired: set[str] = set()
        self._remove_scheduler = None
        self.state: dict[str, Any] = {
            "running": False,
            "program_id": None,
            "program_name": None,
            "zone_id": None,
            "zone_name": None,
            "remaining_seconds": 0,
            "current_step": None,
            "steps": [],
            "source": None,
            "last_error": None,
        }

    async def async_start(self) -> None:
        self._remove_scheduler = async_track_time_interval(
            self.hass, self._async_scheduler_tick, timedelta(seconds=15)
        )

    async def async_shutdown(self) -> None:
        if self._remove_scheduler:
            self._remove_scheduler()
            self._remove_scheduler = None
        await self.async_stop()

    def snapshot(self) -> dict[str, Any]:
        return {"config": self.store.snapshot(), "runtime": dict(self.state)}

    async def _async_turn(self, entity_id: str, enabled: bool) -> None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in {"unknown", "unavailable"}:
            raise RuntimeError(f"Entità non disponibile: {entity_id}")
        domain = entity_id.split(".", 1)[0]
        if domain == "valve":
            service = "open_valve" if enabled else "close_valve"
        else:
            service = "turn_on" if enabled else "turn_off"
        await self.hass.services.async_call(domain, service, {"entity_id": entity_id}, blocking=True)

    def _zone_skip_reason(self, zone: dict[str, Any]) -> str | None:
        if not zone.get("moisture_enabled"):
            return None
        entity_id = zone.get("moisture_entity")
        threshold = zone.get("moisture_min")
        if not entity_id or threshold is None:
            return "Controllo umidità incompleto"
        state = self.hass.states.get(entity_id)
        if state is None or state.state in {"unknown", "unavailable"}:
            return f"Sensore non disponibile: {entity_id}"
        try:
            value = float(state.state)
        except ValueError:
            return f"Sensore non numerico: {entity_id}"
        if value >= float(threshold):
            return f"Umidità {value:g}% ≥ soglia {float(threshold):g}%"
        return None

    def _program_skip_reason(self, program: dict[str, Any]) -> str | None:
        if not program.get("rain_skip_enabled"):
            return None
        entity_id = program.get("weather_entity")
        if not entity_id:
            return "Controllo meteo attivo senza entità meteo"
        state = self.hass.states.get(entity_id)
        if state is None:
            return f"Entità meteo non trovata: {entity_id}"
        if state.state.lower() in {"rainy", "pouring", "lightning-rainy", "hail", "snowy-rainy"}:
            return f"Programma saltato per meteo: {state.state}"
        return None

    async def async_run_program(self, program_id: str, source: str = "manuale") -> None:
        if self.state["running"]:
            raise RuntimeError("Un programma è già in esecuzione")
        program = next((p for p in self.store.data["programs"] if p["id"] == program_id), None)
        if not program:
            raise RuntimeError("Programma non trovato")
        self.task = self.hass.async_create_task(self._async_execute(program, source))

    async def async_stop(self) -> None:
        if not self.state["running"]:
            return
        self._stop.set()
        if self.task and not self.task.done():
            try:
                await asyncio.wait_for(self.task, timeout=20)
            except asyncio.TimeoutError:
                self.task.cancel()

    async def async_skip_zone(self) -> None:
        if self.state["running"]:
            self._skip.set()

    async def _async_execute(self, program: dict[str, Any], source: str) -> None:
        self._stop.clear()
        self._skip.clear()
        reason = self._program_skip_reason(program)
        if reason:
            await self.store.append_log({
                "id": uuid4().hex,
                "at": dt_util.now().isoformat(),
                "program_name": program["name"],
                "source": source,
                "status": "skipped",
                "message": reason,
            })
            return

        zones = {z["id"]: z for z in self.store.data["zones"]}
        steps = [s for s in program.get("steps", []) if s.get("zone_id") in zones]
        self.state.update({
            "running": True,
            "program_id": program["id"],
            "program_name": program["name"],
            "source": source,
            "last_error": None,
            "steps": [
                {"zone_id": s["zone_id"], "zone_name": zones[s["zone_id"]]["name"], "duration_minutes": s["duration_minutes"], "status": "pending"}
                for s in steps
            ],
        })
        pump = program.get("pump_entity")
        active_valve: str | None = None
        try:
            if pump:
                await self._async_turn(pump, True)
                await asyncio.sleep(int(program.get("pump_lead_seconds", 3)))

            for index, step in enumerate(steps):
                if self._stop.is_set():
                    break
                zone = zones[step["zone_id"]]
                self.state["current_step"] = index
                self.state["zone_id"] = zone["id"]
                self.state["zone_name"] = zone["name"]
                self._skip.clear()

                if not zone.get("enabled", True):
                    self.state["steps"][index]["status"] = "disabled"
                    continue
                zone_reason = self._zone_skip_reason(zone)
                if zone_reason:
                    self.state["steps"][index]["status"] = "skipped"
                    await self.store.append_log({
                        "id": uuid4().hex,
                        "at": dt_util.now().isoformat(),
                        "program_name": program["name"],
                        "zone_name": zone["name"],
                        "source": source,
                        "status": "skipped",
                        "message": zone_reason,
                    })
                    continue

                duration = min(int(step["duration_minutes"]), int(zone.get("max_minutes", 720)))
                active_valve = zone["valve_entity"]
                self.state["steps"][index]["status"] = "running"
                await self._async_turn(active_valve, True)
                started = dt_util.now()
                for remaining in range(duration * 60, 0, -1):
                    if self._stop.is_set() or self._skip.is_set():
                        break
                    self.state["remaining_seconds"] = remaining
                    await asyncio.sleep(1)
                await self._async_turn(active_valve, False)
                active_valve = None
                status = "stopped" if self._stop.is_set() else "skipped" if self._skip.is_set() else "completed"
                self.state["steps"][index]["status"] = status
                await self.store.append_log({
                    "id": uuid4().hex,
                    "at": dt_util.now().isoformat(),
                    "program_name": program["name"],
                    "zone_name": zone["name"],
                    "source": source,
                    "status": status,
                    "actual_seconds": int((dt_util.now() - started).total_seconds()),
                    "message": "",
                })
                if self._stop.is_set():
                    break
                if index < len(steps) - 1:
                    await asyncio.sleep(int(program.get("inter_zone_seconds", 5)))
        except Exception as exc:
            self.state["last_error"] = str(exc)
            await self.store.append_log({
                "id": uuid4().hex,
                "at": dt_util.now().isoformat(),
                "program_name": program["name"],
                "source": source,
                "status": "error",
                "message": str(exc),
            })
        finally:
            if active_valve:
                try:
                    await self._async_turn(active_valve, False)
                except Exception:
                    pass
            if pump:
                try:
                    await asyncio.sleep(int(program.get("pump_lag_seconds", 3)))
                    await self._async_turn(pump, False)
                except Exception:
                    pass
            self.state.update({
                "running": False,
                "program_id": None,
                "program_name": None,
                "zone_id": None,
                "zone_name": None,
                "remaining_seconds": 0,
                "current_step": None,
                "source": None,
            })

    def _sun_matches(self, program: dict[str, Any], now: datetime) -> bool:
        event = program.get("sun_event", "none")
        if event not in {"sunrise", "sunset"}:
            return False
        sun = self.hass.states.get("sun.sun")
        if sun is None:
            return False
        key = "next_rising" if event == "sunrise" else "next_setting"
        value = sun.attributes.get(key)
        if not value:
            return False
        try:
            target = dt_util.parse_datetime(value)
            if target is None:
                return False
            target = dt_util.as_local(target) + timedelta(minutes=int(program.get("sun_offset_minutes", 0)))
            return abs((target - now).total_seconds()) <= 20
        except (TypeError, ValueError):
            return False

    async def _async_scheduler_tick(self, now: datetime) -> None:
        now = dt_util.as_local(now)
        day_key = now.strftime("%Y-%m-%d")
        self._fired = {key for key in self._fired if key.startswith(day_key)}
        if self.state["running"] or not self.store.data.get("master_enabled", True):
            return
        weekday = now.weekday()
        hhmm = now.strftime("%H:%M")
        for program in self.store.data["programs"]:
            if not program.get("enabled", True) or weekday not in program.get("weekdays", []):
                continue
            fire_key = f"{day_key}:{hhmm}:{program['id']}"
            matches = hhmm in program.get("start_times", []) or self._sun_matches(program, now)
            if matches and fire_key not in self._fired:
                self._fired.add(fire_key)
                await self.async_run_program(program["id"], "automatico")
                return
