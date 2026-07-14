from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path('/data/irrigation.db')
ADMIN_API = 'http://127.0.0.1:8099/api'
SESSION_SECRET_PATH = Path('/data/operator_session_secret')
SESSION_SECONDS = 8 * 3600

SESSION_SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
if not SESSION_SECRET_PATH.exists():
    SESSION_SECRET_PATH.write_text(secrets.token_hex(32), encoding='utf-8')
SECRET = SESSION_SECRET_PATH.read_text(encoding='utf-8').strip().encode()

app = FastAPI(title='Portale operatori irrigazione', docs_url=None, redoc_url=None)
app.mount('/operator/static', StaticFiles(directory=APP_DIR / 'operator_static'), name='operator_static')
templates = Jinja2Templates(directory=APP_DIR / 'operator_templates')


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def verify_password(password: str, row: sqlite3.Row) -> bool:
    try:
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(row['salt']), n=2**14, r=8, p=1, dklen=32).hex()
        return hmac.compare_digest(digest, row['password_hash'])
    except (KeyError, ValueError, TypeError):
        return False


def make_token(user_id: int, username: str) -> str:
    expires = int(time.time()) + SESSION_SECONDS
    payload = f'{user_id}:{username}:{expires}'
    signature = hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return f'{payload}:{signature}'


def current_user(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get('irrigation_operator')
    if not token:
        return None
    try:
        user_id, username, expires, signature = token.rsplit(':', 3)
        payload = f'{user_id}:{username}:{expires}'
        expected = hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()
        if int(expires) < time.time() or not hmac.compare_digest(signature, expected):
            return None
        with db() as conn:
            row = conn.execute('SELECT id,display_name,username,enabled FROM operator_users WHERE id=? AND username=?', (int(user_id), username)).fetchone()
        return dict(row) if row and row['enabled'] else None
    except (ValueError, TypeError):
        return None


def require_user(request: Request) -> dict[str, Any]:
    user = current_user(request)
    if not user:
        raise HTTPException(401, 'Sessione scaduta')
    return user


def audit(username: str, action: str, detail: str = '') -> None:
    with db() as conn:
        conn.execute('INSERT INTO operator_audit(created_at,username,action,detail) VALUES(?,?,?,?)', (datetime.now().isoformat(timespec='seconds'), username, action, detail))
        conn.commit()


async def admin_request(method: str, path: str, json_data: Any | None = None) -> Any:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.request(method, f"{ADMIN_API}/{path.lstrip('/')}", json=json_data)
    if response.status_code >= 400:
        raise HTTPException(response.status_code, response.text)
    return response.json() if response.content else {'ok': True}


@app.get('/', include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse('/operator', status_code=307)


@app.get('/operator', include_in_schema=False)
async def operator_page(request: Request):
    return templates.TemplateResponse(request=request, name='operator.html', context={'user': current_user(request), 'login_error': request.query_params.get('error') == '1'})


@app.post('/operator/login', include_in_schema=False)
async def login(username: str = Form(...), password: str = Form(...)):
    with db() as conn:
        row = conn.execute('SELECT * FROM operator_users WHERE username=? COLLATE NOCASE', (username.strip(),)).fetchone()
        if not row or not row['enabled'] or not verify_password(password, row):
            return RedirectResponse('/operator?error=1', status_code=303)
        conn.execute('UPDATE operator_users SET last_login=? WHERE id=?', (datetime.now().isoformat(timespec='seconds'), row['id']))
        conn.commit()
    audit(row['username'], 'login', 'Accesso al portale')
    response = RedirectResponse('/operator', status_code=303)
    response.set_cookie('irrigation_operator', make_token(row['id'], row['username']), httponly=True, samesite='strict', secure=False, max_age=SESSION_SECONDS, path='/')
    return response


@app.post('/operator/logout', include_in_schema=False)
async def logout(request: Request):
    user = current_user(request)
    if user:
        audit(user['username'], 'logout', 'Uscita dal portale')
    response = RedirectResponse('/operator', status_code=303)
    response.delete_cookie('irrigation_operator', path='/')
    return response


@app.get('/operator/api/state')
async def state(request: Request):
    require_user(request)
    return await admin_request('GET', 'state')


@app.get('/operator/api/programs')
async def programs(request: Request):
    require_user(request)
    return await admin_request('GET', 'programs')


@app.put('/operator/api/programs/{program_id}')
async def update_program(program_id: int, request: Request):
    user = require_user(request)
    payload = await request.json()
    result = await admin_request('PUT', f'programs/{program_id}', payload)
    audit(user['username'], 'update_program', f"Programma ID {program_id}: {payload.get('name','')}")
    return result


@app.get('/operator/api/zones')
async def zones(request: Request):
    require_user(request)
    return await admin_request('GET', 'zones')


@app.post('/operator/api/zones/{zone_id}/start')
async def start_zone(zone_id: int, request: Request):
    user = require_user(request)
    payload = await request.json()
    duration = int(payload.get('duration_minutes', 10))
    zone_items = await admin_request('GET', 'zones')
    zone = next((item for item in zone_items if int(item['id']) == zone_id), None)
    if not zone:
        raise HTTPException(404, 'Zona non trovata')
    if duration < 1 or duration > int(zone.get('max_minutes') or 60):
        raise HTTPException(400, f"Durata consentita: 1-{zone.get('max_minutes',60)} minuti")
    temp_payload = {
        'name': f"Manuale - {zone['name']}", 'enabled': False, 'weekdays': [], 'start_times': [],
        'sun_event': 'none', 'sun_offset_minutes': 0, 'weather_enabled': False,
        'weather_entity': None, 'rain_skip_enabled': False, 'pump_enabled': False,
        'pump_entity': None, 'pump_lead_seconds': 0, 'pump_lag_seconds': 0,
        'inter_zone_seconds': 0, 'steps': [{'zone_id': zone_id, 'duration_minutes': duration}],
    }
    created = await admin_request('POST', 'programs', temp_payload)
    program_id = int(created['id'])
    await admin_request('POST', f'programs/{program_id}/start')
    await asyncio.sleep(1)
    try:
        await admin_request('DELETE', f'programs/{program_id}')
    except Exception:
        pass
    audit(user['username'], 'start_zone', f"{zone['name']} - {duration} min")
    return {'ok': True}


@app.post('/operator/api/programs/{program_id}/start')
async def start_program(program_id: int, request: Request):
    user = require_user(request)
    result = await admin_request('POST', f'programs/{program_id}/start')
    audit(user['username'], 'start_program', f'Programma ID {program_id}')
    return result


@app.post('/operator/api/stop')
async def stop_program(request: Request):
    user = require_user(request)
    result = await admin_request('POST', 'stop')
    audit(user['username'], 'stop', 'Arresto totale')
    return result


@app.post('/operator/api/skip-zone/{step_index}')
async def skip_zone(step_index: int, request: Request):
    user = require_user(request)
    result = await admin_request('POST', f'skip-zone/{step_index}')
    audit(user['username'], 'skip_zone', f'Indice zona {step_index}')
    return result


@app.middleware('http')
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Cache-Control'] = 'no-store'
    return response
