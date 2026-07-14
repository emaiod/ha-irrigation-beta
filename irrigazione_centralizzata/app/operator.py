from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path('/data/irrigation.db')
ADMIN_API = 'http://127.0.0.1:8099'
SESSION_DAYS = 7

env = Environment(loader=FileSystemLoader(APP_DIR / 'templates'), autoescape=select_autoescape(['html']))
app = FastAPI(title='Portale Operatori Irrigazione')


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables() -> None:
    with db() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS operator_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            last_login TEXT
        );
        CREATE TABLE IF NOT EXISTS operator_sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES operator_users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS operator_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            user_id INTEGER,
            username TEXT,
            action TEXT NOT NULL,
            detail TEXT
        );
        ''')
        conn.commit()


@app.on_event('startup')
def startup() -> None:
    ensure_tables()


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, digest_hex = stored.split('$', 5)
        if algorithm != 'scrypt':
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=32)
        return secrets.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def session_user(request: Request) -> sqlite3.Row | None:
    token = request.cookies.get('irrigation_operator_session')
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now().isoformat(timespec='seconds')
    with db() as conn:
        return conn.execute('''SELECT u.id,u.username,u.display_name,u.enabled,s.expires_at
          FROM operator_sessions s JOIN operator_users u ON u.id=s.user_id
          WHERE s.token_hash=? AND s.expires_at>? AND u.enabled=1''', (token_hash, now)).fetchone()


def require_user(request: Request) -> sqlite3.Row:
    user = session_user(request)
    if not user:
        raise HTTPException(401, 'Sessione non valida o scaduta')
    return user


def audit(user: sqlite3.Row, action: str, detail: str = '') -> None:
    with db() as conn:
        conn.execute('INSERT INTO operator_audit(created_at,user_id,username,action,detail) VALUES(?,?,?,?,?)',
                     (datetime.now().isoformat(timespec='seconds'), user['id'], user['username'], action, detail))
        conn.commit()


async def admin_request(method: str, path: str) -> Any:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.request(method, f'{ADMIN_API}{path}')
        if response.status_code >= 400:
            raise HTTPException(response.status_code, response.text)
        return response.json() if response.content else None


@app.get('/', include_in_schema=False)
async def root():
    return RedirectResponse('/operator')


@app.get('/operator', response_class=HTMLResponse)
async def operator_page(request: Request):
    user = session_user(request)
    template = env.get_template('operator.html')
    return HTMLResponse(template.render(user=dict(user) if user else None))


@app.post('/operator/login')
async def login(username: str = Form(...), password: str = Form(...)):
    with db() as conn:
        user = conn.execute('SELECT * FROM operator_users WHERE username=? COLLATE NOCASE', (username.strip(),)).fetchone()
        if not user or not user['enabled'] or not verify_password(password, user['password_hash']):
            return RedirectResponse('/operator?error=1', status_code=303)
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now()
        expires = now + timedelta(days=SESSION_DAYS)
        conn.execute('DELETE FROM operator_sessions WHERE expires_at<=?', (now.isoformat(timespec='seconds'),))
        conn.execute('INSERT INTO operator_sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)',
                     (token_hash, user['id'], now.isoformat(timespec='seconds'), expires.isoformat(timespec='seconds')))
        conn.execute('UPDATE operator_users SET last_login=? WHERE id=?', (now.isoformat(timespec='seconds'), user['id']))
        conn.commit()
    audit(user, 'login', 'Accesso al portale operatori')
    response = RedirectResponse('/operator', status_code=303)
    response.set_cookie('irrigation_operator_session', token, httponly=True, samesite='lax', secure=False, max_age=SESSION_DAYS*86400, path='/')
    return response


@app.post('/operator/logout')
async def logout(request: Request):
    token = request.cookies.get('irrigation_operator_session')
    if token:
        with db() as conn:
            conn.execute('DELETE FROM operator_sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
            conn.commit()
    response = RedirectResponse('/operator', status_code=303)
    response.delete_cookie('irrigation_operator_session', path='/')
    return response


@app.get('/operator/api/state')
async def state(request: Request):
    require_user(request)
    return await admin_request('GET', '/api/state')


@app.get('/operator/api/programs')
async def programs(request: Request):
    require_user(request)
    return await admin_request('GET', '/api/programs')


@app.post('/operator/api/programs/{program_id}/start')
async def start(program_id: int, request: Request):
    user = require_user(request)
    result = await admin_request('POST', f'/api/programs/{program_id}/start')
    audit(user, 'avvio_programma', f'Programma ID {program_id}')
    return result


@app.post('/operator/api/stop')
async def stop(request: Request):
    user = require_user(request)
    result = await admin_request('POST', '/api/stop')
    audit(user, 'arresto_programma', 'Arresto totale richiesto')
    return result


@app.post('/operator/api/skip-zone/{step_index}')
async def skip(step_index: int, request: Request):
    user = require_user(request)
    result = await admin_request('POST', f'/api/skip-zone/{step_index}')
    audit(user, 'salta_zona', f'Indice zona {step_index}')
    return result


@app.middleware('http')
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Cache-Control'] = 'no-store'
    return response
