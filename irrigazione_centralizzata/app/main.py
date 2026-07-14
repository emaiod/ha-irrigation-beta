from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import hashlib
import secrets
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


def hash_operator_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("La password deve contenere almeno 8 caratteri")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_operator_password(password: str, stored: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, digest_hex = stored.split("$", 5)
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=32)
        return secrets.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


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
            CREATE TABLE IF NOT EXISTS operator_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_login TEXT
            );
            CREATE TABLE IF NOT EXISTS operator_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                user_id INTEGER,
                username TEXT,
                action TEXT NOT NULL,
                detail TEXT,
                FOREIGN KEY(user_id) REFERENCES operator_users(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS weather_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                program_id INTEGER,
                program_name TEXT,
                weather_entity TEXT NOT NULL,
                phase TEXT NOT NULL DEFAULT 'periodic',
                condition TEXT,
                temperature REAL,
                humidity REAL,
                pressure REAL,
                wind_speed REAL,
                precipitation REAL,
                raw_attributes TEXT
            );
            """
        )
        ensure_column(conn, "programs", "sun_event", "TEXT NOT NULL DEFAULT 'none'")
        ensure_column(conn, "programs", "sun_offset_minutes", "INTEGER NOT NULL DEFAULT 0")
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


def parse_ha_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


async def get_sun_event_time(event: str, offset_minutes: int = 0) -> datetime | None:
    if event not in {"sunrise", "sunset"}:
        return None
    sun = await get_entity_state("sun.sun")
    attr = "next_rising" if event == "sunrise" else "next_setting"
    moment = parse_ha_datetime(sun.get("attributes", {}).get(attr))
    return moment + timedelta(minutes=offset_minutes) if moment else None


def weather_value(attributes: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = attributes.get(key)
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            pass
    return None


async def capture_weather_snapshot(program: dict[str, Any], phase: str) -> None:
    entity_id = program.get("weather_entity") if program.get("weather_enabled") else None
    if not entity_id:
        return
    try:
        item = await get_entity_state(entity_id)
        attrs = item.get("attributes", {})
        with db() as conn:
            conn.execute(
                """INSERT INTO weather_history(captured_at,program_id,program_name,weather_entity,phase,condition,
                   temperature,humidity,pressure,wind_speed,precipitation,raw_attributes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (datetime.now().isoformat(timespec="seconds"), program.get("id"), program.get("name"), entity_id, phase,
                 item.get("state"), weather_value(attrs, "temperature"), weather_value(attrs, "humidity"),
                 weather_value(attrs, "pressure"), weather_value(attrs, "wind_speed", "wind_speed_km_h"),
                 weather_value(attrs, "precipitation", "precipitation_probability"), json.dumps(attrs, ensure_ascii=False)),
            )
            conn.commit()
    except Exception:
        return


def validate_schedule(payload: "ProgramIn") -> None:
    if payload.sun_event not in {"none", "sunrise", "sunset"}:
        raise HTTPException(400, "Tipo di partenza solare non valido")
    for weekday in payload.weekdays:
        if weekday < 0 or weekday > 6:
            raise HTTPException(400, "Giorno della settimana non valido")
    for value in payload.start_times:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise HTTPException(400, f"Orario non valido: {value}")
    has_time = bool(payload.start_times) or payload.sun_event != "none"
    if has_time and not payload.weekdays:
        raise HTTPException(400, "Se imposti una partenza automatica devi selezionare almeno un giorno")


class Runtime:
    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.stop_event = asyncio.Event()
        self.skip_event = asyncio.Event()
        self.future_skips: set[int] = set()
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
    sun_event: str = "none"
    sun_offset_minutes: int = Field(default=0, ge=-240, le=240)
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
    runtime.future_skips.clear()
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

    await capture_weather_snapshot(program, "program_start")
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
            if index in runtime.future_skips:
                runtime.future_skips.discard(index)
                runtime.state["steps"][index]["status"] = "skipped"
                with db() as conn:
                    conn.execute(
                        """INSERT INTO logs(started_at,program_id,program_name,zone_id,zone_name,source,planned_minutes,status,message)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                        (datetime.now().isoformat(timespec="seconds"), program_id, program["name"], step["zone_id"],
                         step["zone_name"], source, step["duration_minutes"], "skipped", "Saltata manualmente prima dell'avvio"),
                    )
                    conn.commit()
                continue
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
        await capture_weather_snapshot(program, "program_end")
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
                programs = conn.execute("SELECT * FROM programs WHERE enabled=1").fetchall() if master and master["value"] == "true" else []
            for row in programs:
                p = rowdict(row)
                weekdays = json.loads(p["weekdays"])
                if weekday not in weekdays:
                    continue
                fire_key = f"{minute_key}:{p['id']}"
                should_fire = hhmm in json.loads(p["start_times"])
                if not should_fire and p.get("sun_event", "none") != "none":
                    try:
                        event_time = await get_sun_event_time(p["sun_event"], int(p.get("sun_offset_minutes", 0)))
                        should_fire = bool(event_time and 0 <= (event_time - now).total_seconds() <= 35)
                    except Exception:
                        should_fire = False
                if should_fire and fire_key not in fired:
                    fired.add(fire_key)
                    runtime.task = asyncio.create_task(run_program(p["id"], "automatico"))
                    break
        fired = {key for key in fired if key.startswith(now.strftime("%Y-%m-%d"))}
        await asyncio.sleep(15)


async def weather_history_loop() -> None:
    while True:
        with db() as conn:
            rows = conn.execute("SELECT * FROM programs WHERE weather_enabled=1 AND weather_entity IS NOT NULL").fetchall()
        seen: set[str] = set()
        for row in rows:
            program = rowdict(row)
            entity = program.get("weather_entity")
            if entity and entity not in seen:
                seen.add(entity)
                await capture_weather_snapshot(program, "periodic")
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    scheduler = asyncio.create_task(scheduler_loop())
    weather_task = asyncio.create_task(weather_history_loop())
    yield
    scheduler.cancel()
    weather_task.cancel()
    await runtime.stop("Chiusura add-on")


app = FastAPI(title="Irrigazione Centralizzata", lifespan=lifespan)


@app.middleware("http")
async def disable_frontend_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response
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
    validate_schedule(payload)
    with db() as conn:
        cur = conn.execute(
            """INSERT INTO programs(name,enabled,weekdays,start_times,sun_event,sun_offset_minutes,weather_enabled,weather_entity,rain_skip_enabled,
               pump_enabled,pump_entity,pump_lead_seconds,pump_lag_seconds,inter_zone_seconds)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (payload.name, payload.enabled, json.dumps(payload.weekdays), json.dumps(payload.start_times), payload.sun_event, payload.sun_offset_minutes,
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
    validate_schedule(payload)
    with db() as conn:
        exists = conn.execute("SELECT id FROM programs WHERE id=?", (program_id,)).fetchone()
        if not exists:
            raise HTTPException(404, "Programma non trovato")
        conn.execute(
            """UPDATE programs SET name=?,enabled=?,weekdays=?,start_times=?,sun_event=?,sun_offset_minutes=?,weather_enabled=?,weather_entity=?,
               rain_skip_enabled=?,pump_enabled=?,pump_entity=?,pump_lead_seconds=?,pump_lag_seconds=?,
               inter_zone_seconds=? WHERE id=?""",
            (payload.name, payload.enabled, json.dumps(payload.weekdays), json.dumps(payload.start_times), payload.sun_event, payload.sun_offset_minutes,
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


@app.post("/api/skip-zone/{step_index}")
async def skip_program_step(step_index: int):
    if not runtime.state["running"]:
        raise HTTPException(409, "Nessun programma in esecuzione")
    steps = runtime.state.get("steps", [])
    if step_index < 0 or step_index >= len(steps):
        raise HTTPException(404, "Zona del programma non trovata")
    status = steps[step_index].get("status")
    current_index = runtime.state.get("current_step_index")
    if status == "running" or step_index == current_index:
        runtime.skip_event.set()
        return {"ok": True, "mode": "current"}
    if status != "pending":
        raise HTTPException(409, "La zona non è più in attesa")
    runtime.future_skips.add(step_index)
    steps[step_index]["status"] = "skipped"
    return {"ok": True, "mode": "future"}


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


class OperatorUserIn(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=200)
    enabled: bool = True


class OperatorUserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    password: str | None = Field(default=None, min_length=8, max_length=200)
    enabled: bool | None = None


@app.get("/api/operator-users")
async def list_operator_users():
    with db() as conn:
        return [rowdict(r) for r in conn.execute(
            "SELECT id,username,display_name,enabled,created_at,last_login FROM operator_users ORDER BY username"
        )]


@app.post("/api/operator-users")
async def create_operator_user(payload: OperatorUserIn):
    try:
        password_hash = hash_operator_password(payload.password)
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO operator_users(username,password_hash,display_name,enabled,created_at) VALUES(?,?,?,?,?)",
                (payload.username.strip(), password_hash, payload.display_name.strip(), int(payload.enabled), datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()
            return {"id": cur.lastrowid}
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Nome utente già esistente") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.put("/api/operator-users/{user_id}")
async def update_operator_user(user_id: int, payload: OperatorUserUpdate):
    fields=[]; values=[]
    if payload.display_name is not None:
        fields.append("display_name=?"); values.append(payload.display_name.strip())
    if payload.enabled is not None:
        fields.append("enabled=?"); values.append(int(payload.enabled))
    if payload.password is not None:
        try:
            fields.append("password_hash=?"); values.append(hash_operator_password(payload.password))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if not fields:
        return {"updated": False}
    values.append(user_id)
    with db() as conn:
        cur=conn.execute(f"UPDATE operator_users SET {', '.join(fields)} WHERE id=?", values)
        conn.commit()
    if not cur.rowcount:
        raise HTTPException(404, "Utente non trovato")
    return {"updated": True}


@app.delete("/api/operator-users/{user_id}")
async def delete_operator_user(user_id: int):
    with db() as conn:
        cur=conn.execute("DELETE FROM operator_users WHERE id=?", (user_id,))
        conn.commit()
    if not cur.rowcount:
        raise HTTPException(404, "Utente non trovato")
    return {"deleted": True}


@app.get("/api/operator-audit")
async def operator_audit(limit: int = 200):
    with db() as conn:
        return [rowdict(r) for r in conn.execute(
            "SELECT * FROM operator_audit ORDER BY id DESC LIMIT ?", (min(max(limit,1),1000),)
        )]


@app.get("/api/calendar")
async def calendar(days: int = 30):
    days = min(max(days, 1), 90)
    now = datetime.now()
    events: list[dict[str, Any]] = []
    with db() as conn:
        rows = conn.execute("SELECT * FROM programs WHERE enabled=1 ORDER BY name").fetchall()
    for row in rows:
        p = rowdict(row)
        weekdays = json.loads(p["weekdays"])
        for offset in range(days + 1):
            day = (now + timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
            if day.weekday() not in weekdays:
                continue
            for time_value in json.loads(p["start_times"]):
                hour, minute = map(int, time_value.split(":"))
                moment = day.replace(hour=hour, minute=minute)
                if moment > now:
                    events.append({"program_id": p["id"], "program_name": p["name"], "datetime": moment.isoformat(), "type": "fixed", "label": time_value})
        if p.get("sun_event", "none") != "none":
            try:
                moment = await get_sun_event_time(p["sun_event"], int(p.get("sun_offset_minutes", 0)))
                if moment and moment > now and moment.weekday() in weekdays:
                    label = "Alba" if p["sun_event"] == "sunrise" else "Tramonto"
                    events.append({"program_id": p["id"], "program_name": p["name"], "datetime": moment.isoformat(), "type": p["sun_event"], "label": f"{label} {int(p.get('sun_offset_minutes',0)):+d} min"})
            except Exception:
                pass
    return sorted(events, key=lambda x: x["datetime"])[:500]


@app.get("/api/weather-history")
async def weather_history(limit: int = 300):
    with db() as conn:
        return [rowdict(r) for r in conn.execute(
            "SELECT * FROM weather_history ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 2000),)
        )]


@app.get("/api/logs")
async def logs(limit: int = 200):
    with db() as conn:
        return [rowdict(r) for r in conn.execute(
            "SELECT * FROM logs ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 1000),)
        )]
