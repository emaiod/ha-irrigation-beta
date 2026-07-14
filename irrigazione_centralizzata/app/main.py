from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path("/data")
DB_PATH = DATA_DIR / "irrigation.db"
HA_API = "http://supervisor/core/api"
TOKEN = os.getenv("SUPERVISOR_TOKEN", "")

DATA_DIR.mkdir(parents=True, exist_ok=True)

env = Environment(
    loader=FileSystemLoader(APP_DIR / "templates"),
    autoescape=select_autoescape(["html", "xml"]),
)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS zones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                valve_entity TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                moisture_enabled INTEGER NOT NULL DEFAULT 0,
                moisture_entity TEXT,
                moisture_min REAL,
                soil_enabled INTEGER NOT NULL DEFAULT 0,
                soil_type TEXT,
                max_minutes INTEGER NOT NULL DEFAULT 60
            );
            CREATE TABLE IF NOT EXISTS programs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                weekdays TEXT NOT NULL DEFAULT '[]',
                start_times TEXT NOT NULL DEFAULT '[]',
                weather_enabled INTEGER NOT NULL DEFAULT 0,
                weather_entity TEXT,
                rain_skip_enabled INTEGER NOT NULL DEFAULT 0,
                pump_enabled INTEGER NOT NULL DEFAULT 0,
                pump_entity TEXT,
                pump_lead_seconds INTEGER NOT NULL DEFAULT 3,
                pump_lag_seconds INTEGER NOT NULL DEFAULT 3,
                inter_zone_seconds INTEGER NOT NULL DEFAULT 5
            );
            CREATE TABLE IF NOT EXISTS program_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                program_id INTEGER NOT NULL,
                zone_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                duration_minutes INTEGER NOT NULL,
                FOREIGN KEY(program_id) REFERENCES programs(id) ON DELETE CASCADE,
                FOREIGN KEY(zone_id) REFERENCES zones(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                program_id INTEGER,
                program_name TEXT,
                zone_id INTEGER,
                zone_name TEXT,
                source TEXT NOT NULL,
                planned_minutes INTEGER,
                actual_seconds INTEGER,
                status TEXT NOT NULL,
                message TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('master_enabled','true')")
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('notifications_enabled','true')")
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('notification_service','persistent_notification.create')")
        conn.commit()


async def ha_request(method: str, path: str, json_data: dict[str, Any] | None = None) -> Any:
    if not TOKEN:
        raise RuntimeError("SUPERVISOR_TOKEN non disponibile")
    headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.request(method, f"{HA_API}{path}", headers=headers, json=json_data)
        response.raise_for_status()
        if response.content:
            return response.json()
        return None


async def entity_on(entity_id: str) -> None:
    domain = entity_id.split(".", 1)[0]
    service = "open_valve" if domain == "valve" else "turn_on"
    await ha_request("POST", f"/services/{domain}/{service}", {"entity_id": entity_id})


async def entity_off(entity_id: str) -> None:
    domain = entity_id.split(".", 1)[0]
    service = "close_valve" if domain == "valve" else "turn_off"
    await ha_request("POST", f"/services/{domain}/{service}", {"entity_id": entity_id})


async def get_entity_state(entity_id: str) -> dict[str, Any]:
    try:
        item = await ha_request("GET", f"/states/{entity_id}")
        if str(item.get("state", "")).lower() in {"unavailable", "unknown"}:
            raise RuntimeError(f"Entità non disponibile: {entity_id}")
        return item
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise RuntimeError(f"Entità non trovata: {entity_id}") from exc
        raise


def get_setting(key: str, default: str = "") -> str:
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


async def send_notification(title: str, message: str) -> None:
    if get_setting("notifications_enabled", "true") != "true":
        return
    service_id = get_setting("notification_service", "persistent_notification.create").strip()
    if not service_id or "." not in service_id:
        return
    domain, service = service_id.split(".", 1)
    payload = {"title": title, "message": message}
    await ha_request("POST", f"/services/{domain}/{service}", payload)


def numeric_state(item: dict[str, Any]) -> float | None:
    try:
        return float(item.get("state"))
    except (TypeError, ValueError):
        return None


async def program_skip_reason(program: dict[str, Any]) -> str | None:
    if not program.get("weather_enabled") or not program.get("rain_skip_enabled"):
        return None
    entity_id = program.get("weather_entity")
    if not entity_id:
        return "Controllo meteo attivo ma entità meteo non configurata"
    item = await get_entity_state(entity_id)
    rainy_states = {"rainy", "pouring", "lightning-rainy", "hail", "snowy-rainy"}
    if str(item.get("state", "")).lower() in rainy_states:
        return f"Programma saltato: meteo {item.get('state')}"
    return None


async def zone_skip_reason(step: dict[str, Any]) -> str | None:
    if not step.get("moisture_enabled"):
        return None
    entity_id = step.get("moisture_entity")
    threshold = step.get("moisture_min")
    if not entity_id or threshold is None:
        return "Zona saltata: controllo umidità incompleto"
    item = await get_entity_state(entity_id)
    value = numeric_state(item)
    if value is None:
        return f"Zona saltata: sensore {entity_id} non numerico o non disponibile"
    if value >= float(threshold):
        return f"Zona saltata: umidità {value:g}% ≥ soglia {float(threshold):g}%"
    return None


class Runtime:
    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.stop_event = asyncio.Event()
        self.skip_event = asyncio.Event()
        self.state: dict[str, Any] = {
            "running": False,
            "paused": False,
            "program_id": None,
            "program_name": None,
            "zone_id": None,
            "zone_name": None,
            "remaining_seconds": 0,
            "started_at": None,
            "source": None,
            "current_step_index": None,
            "steps": [],
            "last_error": None,
        }

    async def stop(self, reason: str = "Arresto manuale") -> None:
        self.stop_event.set()
        if self.task and not self.task.done():
            try:
                await asyncio.wait_for(self.task, timeout=15)
            except asyncio.TimeoutError:
                self.task.cancel()
        self.state.update({"running": False, "paused": False, "remaining_seconds": 0})


runtime = Runtime()


class ZoneIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    valve_entity: str
    enabled: bool = True
    moisture_enabled: bool = False
    moisture_entity: str | None = None
    moisture_min: float | None = None
    soil_enabled: bool = False
    soil_type: str | None = None
    max_minutes: int = Field(default=60, ge=1, le=720)


class StepIn(BaseModel):
    zone_id: int
    duration_minutes: int = Field(ge=1, le=720)


class ProgramIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    weekdays: list[int] = []
    start_times: list[str] = []
    weather_enabled: bool = False
    weather_entity: str | None = None
    rain_skip_enabled: bool = False
    pump_enabled: bool = False
    pump_entity: str | None = None
    pump_lead_seconds: int = Field(default=3, ge=0, le=300)
    pump_lag_seconds: int = Field(default=3, ge=0, le=300)
    inter_zone_seconds: int = Field(default=5, ge=0, le=300)
    steps: list[StepIn]


def rowdict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


async def run_program(program_id: int, source: str) -> None:
    runtime.stop_event.clear()
    runtime.skip_event.clear()
    with db() as conn:
        program_row = conn.execute("SELECT * FROM programs WHERE id=?", (program_id,)).fetchone()
        if not program_row:
            return
        program = rowdict(program_row)
        steps = conn.execute(
            """SELECT ps.*, z.name zone_name, z.valve_entity, z.max_minutes, z.enabled zone_enabled,
                      z.moisture_enabled, z.moisture_entity, z.moisture_min
               FROM program_steps ps JOIN zones z ON z.id=ps.zone_id
               WHERE ps.program_id=? ORDER BY ps.position""",
            (program_id,),
        ).fetchall()

    skip_reason = await program_skip_reason(program)
    if skip_reason:
        with db() as conn:
            conn.execute(
                "INSERT INTO logs(started_at,program_id,program_name,source,status,message) VALUES(?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), program_id, program["name"], source, "skipped", skip_reason),
            )
            conn.commit()
        return

    runtime_steps = [
        {"index": i, "zone_id": row["zone_id"], "zone_name": row["zone_name"],
         "duration_minutes": row["duration_minutes"], "status": "pending"}
        for i, row in enumerate(steps)
    ]
    runtime.state.update({
        "running": True, "program_id": program_id, "program_name": program["name"],
        "started_at": datetime.now().isoformat(timespec="seconds"), "source": source,
        "current_step_index": None, "steps": runtime_steps, "last_error": None,
    })
    try:
        await send_notification(
            "Irrigazione avviata",
            f"È iniziato il programma {program['name']} con {len(runtime_steps)} zone.",
        )
    except Exception:
        pass
    pump_entity = program["pump_entity"] if program["pump_enabled"] else None
    active_valve: str | None = None
    try:
        if pump_entity:
            await get_entity_state(pump_entity)
            await entity_on(pump_entity)
            await asyncio.sleep(program["pump_lead_seconds"])

        for index, step_row in enumerate(steps):
            if runtime.stop_event.is_set():
                break
            step = rowdict(step_row)
            runtime.state["current_step_index"] = index
            runtime.skip_event.clear()
            if not step["zone_enabled"]:
                runtime.state["steps"][index]["status"] = "disabled"
                continue
            zone_reason = await zone_skip_reason(step)
            if zone_reason:
                with db() as conn:
                    conn.execute(
                        """INSERT INTO logs(started_at,program_id,program_name,zone_id,zone_name,source,planned_minutes,status,message)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                        (datetime.now().isoformat(timespec="seconds"), program_id, program["name"], step["zone_id"],
                         step["zone_name"], source, step["duration_minutes"], "skipped", zone_reason),
                    )
                    conn.commit()
                runtime.state["steps"][index]["status"] = "skipped"
                continue
            duration = min(step["duration_minutes"], step["max_minutes"])
            active_valve = step["valve_entity"]
            started = datetime.now()
            log_id: int
            with db() as conn:
                cur = conn.execute(
                    """INSERT INTO logs(started_at,program_id,program_name,zone_id,zone_name,source,planned_minutes,status,message)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (started.isoformat(timespec="seconds"), program_id, program["name"], step["zone_id"],
                     step["zone_name"], source, duration, "running", "Irrigazione avviata"),
                )
                log_id = cur.lastrowid
                conn.commit()
            runtime.state.update({"zone_id": step["zone_id"], "zone_name": step["zone_name"]})
            runtime.state["steps"][index]["status"] = "running"
            await get_entity_state(active_valve)
            await entity_on(active_valve)
            total_seconds = duration * 60
            for remaining in range(total_seconds, 0, -1):
                if runtime.stop_event.is_set() or runtime.skip_event.is_set():
                    break
                runtime.state["remaining_seconds"] = remaining
                await asyncio.sleep(1)
            await entity_off(active_valve)
            active_valve = None
            ended = datetime.now()
            if runtime.stop_event.is_set():
                status = "stopped"
            elif runtime.skip_event.is_set():
                status = "skipped"
            else:
                status = "completed"
            runtime.state["steps"][index]["status"] = status
            with db() as conn:
                conn.execute(
                    "UPDATE logs SET ended_at=?,actual_seconds=?,status=?,message=? WHERE id=?",
                    (ended.isoformat(timespec="seconds"), int((ended-started).total_seconds()), status,
                     "Interrotta" if status == "stopped" else ("Saltata manualmente" if status == "skipped" else "Completata"), log_id),
                )
                conn.commit()
            if runtime.stop_event.is_set():
                break
            if index < len(steps) - 1:
                await asyncio.sleep(program["inter_zone_seconds"])
    except Exception as exc:
        runtime.state["last_error"] = str(exc)
        if runtime.state.get("current_step_index") is not None:
            runtime.state["steps"][runtime.state["current_step_index"]]["status"] = "error"
        try:
            await send_notification("Errore irrigazione", f"Programma {program['name']}: {exc}")
        except Exception:
            pass
        with db() as conn:
            conn.execute(
                "INSERT INTO logs(started_at,program_id,program_name,source,status,message) VALUES(?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), program_id, program["name"], source, "error", str(exc)),
            )
            conn.commit()
    finally:
        if active_valve:
            try:
                await entity_off(active_valve)
            except Exception:
                pass
        if pump_entity:
            try:
                await asyncio.sleep(program["pump_lag_seconds"])
                await entity_off(pump_entity)
            except Exception:
                pass
        stopped = runtime.stop_event.is_set()
        failed = bool(runtime.state.get("last_error"))
        if not failed:
            try:
                if stopped:
                    await send_notification("Irrigazione interrotta", f"Il programma {program['name']} è stato arrestato.")
                else:
                    await send_notification("Irrigazione completata", f"Il programma {program['name']} è terminato correttamente.")
            except Exception:
                pass
        runtime.state.update({
            "running": False, "paused": False, "program_id": None, "program_name": None,
            "zone_id": None, "zone_name": None, "remaining_seconds": 0, "source": None,
            "current_step_index": None,
        })


async def scheduler_loop() -> None:
    fired: set[str] = set()
    while True:
        now = datetime.now()
        minute_key = now.strftime("%Y-%m-%d %H:%M")
        weekday = now.weekday()
        hhmm = now.strftime("%H:%M")
        if not runtime.state["running"]:
            with db() as conn:
                master = conn.execute("SELECT value FROM settings WHERE key='master_enabled'").fetchone()
                if master and master["value"] == "true":
                    programs = conn.execute("SELECT * FROM programs WHERE enabled=1").fetchall()
                    for row in programs:
                        p = rowdict(row)
                        weekdays = json.loads(p["weekdays"])
                        times = json.loads(p["start_times"])
                        fire_key = f"{minute_key}:{p['id']}"
                        if weekday in weekdays and hhmm in times and fire_key not in fired:
                            fired.add(fire_key)
                            runtime.task = asyncio.create_task(run_program(p["id"], "automatico"))
                            break
        fired = {key for key in fired if key.startswith(now.strftime("%Y-%m-%d"))}
        await asyncio.sleep(15)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    scheduler = asyncio.create_task(scheduler_loop())
    yield
    scheduler.cancel()
    await runtime.stop("Chiusura add-on")


app = FastAPI(title="Irrigazione Centralizzata", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


def render(name: str, **context: Any) -> HTMLResponse:
    return HTMLResponse(env.get_template(name).render(**context))


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return render("index.html", request=request)


@app.get("/api/state")
async def state():
    return runtime.state


@app.get("/api/entities")
async def entities():
    states = await ha_request("GET", "/states")
    allowed = {"switch", "valve", "input_boolean", "sensor", "binary_sensor", "weather"}
    result = []
    for item in states:
        domain = item["entity_id"].split(".", 1)[0]
        if domain in allowed:
            result.append({
                "entity_id": item["entity_id"],
                "state": item.get("state"),
                "name": item.get("attributes", {}).get("friendly_name", item["entity_id"]),
                "domain": domain,
            })
    return sorted(result, key=lambda x: (x["domain"], x["name"].lower()))


@app.get("/api/zones")
async def zones():
    with db() as conn:
        return [rowdict(r) for r in conn.execute("SELECT * FROM zones ORDER BY name")]


@app.post("/api/zones")
async def create_zone(payload: ZoneIn):
    with db() as conn:
        cur = conn.execute(
            """INSERT INTO zones(name,valve_entity,enabled,moisture_enabled,moisture_entity,moisture_min,soil_enabled,soil_type,max_minutes)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (payload.name, payload.valve_entity, payload.enabled, payload.moisture_enabled,
             payload.moisture_entity, payload.moisture_min, payload.soil_enabled, payload.soil_type,
             payload.max_minutes),
        )
        conn.commit()
        return {"id": cur.lastrowid}


@app.delete("/api/zones/{zone_id}")
async def delete_zone(zone_id: int):
    with db() as conn:
        conn.execute("DELETE FROM zones WHERE id=?", (zone_id,))
        conn.commit()
    return {"ok": True}


@app.get("/api/programs")
async def programs():
    with db() as conn:
        rows = conn.execute("SELECT * FROM programs ORDER BY name").fetchall()
        result = []
        for row in rows:
            p = rowdict(row)
            p["weekdays"] = json.loads(p["weekdays"])
            p["start_times"] = json.loads(p["start_times"])
            p["steps"] = [rowdict(s) for s in conn.execute(
                """SELECT ps.*, z.name zone_name FROM program_steps ps
                   JOIN zones z ON z.id=ps.zone_id WHERE ps.program_id=? ORDER BY ps.position""", (p["id"],)
            )]
            result.append(p)
        return result


@app.post("/api/programs")
async def create_program(payload: ProgramIn):
    if payload.pump_enabled and not payload.pump_entity:
        raise HTTPException(400, "Se la pompa è abilitata devi selezionare la sua entità")
    with db() as conn:
        cur = conn.execute(
            """INSERT INTO programs(name,enabled,weekdays,start_times,weather_enabled,weather_entity,rain_skip_enabled,
               pump_enabled,pump_entity,pump_lead_seconds,pump_lag_seconds,inter_zone_seconds)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (payload.name, payload.enabled, json.dumps(payload.weekdays), json.dumps(payload.start_times),
             payload.weather_enabled, payload.weather_entity, payload.rain_skip_enabled, payload.pump_enabled,
             payload.pump_entity, payload.pump_lead_seconds, payload.pump_lag_seconds, payload.inter_zone_seconds),
        )
        program_id = cur.lastrowid
        for position, step in enumerate(payload.steps):
            conn.execute(
                "INSERT INTO program_steps(program_id,zone_id,position,duration_minutes) VALUES(?,?,?,?)",
                (program_id, step.zone_id, position, step.duration_minutes),
            )
        conn.commit()
    return {"id": program_id}


@app.put("/api/programs/{program_id}")
async def update_program(program_id: int, payload: ProgramIn):
    if payload.pump_enabled and not payload.pump_entity:
        raise HTTPException(400, "Se la pompa è abilitata devi selezionare la sua entità")
    with db() as conn:
        exists = conn.execute("SELECT id FROM programs WHERE id=?", (program_id,)).fetchone()
        if not exists:
            raise HTTPException(404, "Programma non trovato")
        conn.execute(
            """UPDATE programs SET name=?,enabled=?,weekdays=?,start_times=?,weather_enabled=?,weather_entity=?,
               rain_skip_enabled=?,pump_enabled=?,pump_entity=?,pump_lead_seconds=?,pump_lag_seconds=?,
               inter_zone_seconds=? WHERE id=?""",
            (payload.name, payload.enabled, json.dumps(payload.weekdays), json.dumps(payload.start_times),
             payload.weather_enabled, payload.weather_entity, payload.rain_skip_enabled, payload.pump_enabled,
             payload.pump_entity, payload.pump_lead_seconds, payload.pump_lag_seconds,
             payload.inter_zone_seconds, program_id),
        )
        conn.execute("DELETE FROM program_steps WHERE program_id=?", (program_id,))
        for position, step in enumerate(payload.steps):
            conn.execute(
                "INSERT INTO program_steps(program_id,zone_id,position,duration_minutes) VALUES(?,?,?,?)",
                (program_id, step.zone_id, position, step.duration_minutes),
            )
        conn.commit()
    return {"id": program_id, "updated": True}


@app.delete("/api/programs/{program_id}")
async def delete_program(program_id: int):
    with db() as conn:
        conn.execute("DELETE FROM program_steps WHERE program_id=?", (program_id,))
        conn.execute("DELETE FROM programs WHERE id=?", (program_id,))
        conn.commit()
    return {"ok": True}


@app.post("/api/programs/{program_id}/start")
async def start_program(program_id: int):
    if runtime.state["running"]:
        raise HTTPException(409, "È già in corso un'irrigazione")
    runtime.task = asyncio.create_task(run_program(program_id, "manuale"))
    return {"ok": True}


@app.post("/api/stop")
async def stop():
    await runtime.stop()
    return {"ok": True}


@app.post("/api/skip-zone")
async def skip_zone():
    if not runtime.state["running"] or runtime.state.get("zone_id") is None:
        raise HTTPException(409, "Nessuna zona attiva da saltare")
    runtime.skip_event.set()
    return {"ok": True}


@app.get("/api/notification-services")
async def notification_services():
    services = await ha_request("GET", "/services")
    result = [{"service_id": "persistent_notification.create", "name": "Notifica persistente Home Assistant"}]
    for domain in services:
        if domain.get("domain") != "notify":
            continue
        for service in domain.get("services", {}):
            result.append({"service_id": f"notify.{service}", "name": service.replace("_", " ")})
    return result


@app.get("/api/notification-settings")
async def notification_settings():
    return {
        "enabled": get_setting("notifications_enabled", "true") == "true",
        "service": get_setting("notification_service", "persistent_notification.create"),
    }


class NotificationSettingsIn(BaseModel):
    enabled: bool = True
    service: str = "persistent_notification.create"


@app.put("/api/notification-settings")
async def save_notification_settings(payload: NotificationSettingsIn):
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('notifications_enabled',?)", ("true" if payload.enabled else "false",))
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('notification_service',?)", (payload.service,))
        conn.commit()
    return {"updated": True}


@app.get("/api/logs")
async def logs(limit: int = 200):
    with db() as conn:
        return [rowdict(r) for r in conn.execute(
            "SELECT * FROM logs ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 1000),)
        )]
