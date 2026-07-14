from __future__ import annotations
import hashlib, hmac, os, secrets, sqlite3, time
from datetime import datetime
from pathlib import Path
from typing import Any
import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

DB_PATH=Path('/data/irrigation.db')
ADMIN_API='http://127.0.0.1:8099/api'
SESSION_SECRET_PATH=Path('/data/operator_session_secret')
if not SESSION_SECRET_PATH.exists(): SESSION_SECRET_PATH.write_text(secrets.token_hex(32))
SECRET=SESSION_SECRET_PATH.read_text().strip().encode()
app=FastAPI(title='Portale operatori irrigazione')

def db():
    c=sqlite3.connect(DB_PATH); c.row_factory=sqlite3.Row; return c

def verify(password:str,row:sqlite3.Row)->bool:
    digest=hashlib.scrypt(password.encode(),salt=bytes.fromhex(row['salt']),n=2**14,r=8,p=1,dklen=32).hex()
    return hmac.compare_digest(digest,row['password_hash'])

def token_for(user_id:int,username:str)->str:
    exp=int(time.time())+8*3600; payload=f'{user_id}:{username}:{exp}'; sig=hmac.new(SECRET,payload.encode(),hashlib.sha256).hexdigest(); return f'{payload}:{sig}'

def current_user(request:Request):
    token=request.cookies.get('irrigation_operator');
    if not token:return None
    try:
        uid_s,username,exp_s,sig=token.rsplit(':',3); payload=f'{uid_s}:{username}:{exp_s}'
        if int(exp_s)<time.time() or not hmac.compare_digest(sig,hmac.new(SECRET,payload.encode(),hashlib.sha256).hexdigest()):return None
        with db() as c: row=c.execute('SELECT id,display_name,username,enabled FROM operator_users WHERE id=? AND username=?',(int(uid_s),username)).fetchone()
        return dict(row) if row and row['enabled'] else None
    except Exception:return None

def audit(username,action,detail=''):
    with db() as c:c.execute('INSERT INTO operator_audit(created_at,username,action,detail) VALUES(?,?,?,?)',(datetime.now().isoformat(timespec='seconds'),username,action,detail));c.commit()

async def admin(method,path):
    async with httpx.AsyncClient(timeout=15) as client:
        r=await client.request(method,f'{ADMIN_API}/{path.lstrip("/")}');
        if r.status_code>=400: raise HTTPException(r.status_code,r.text)
        return r.json()

LOGIN='''<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Accesso irrigazione</title><style>body{font-family:system-ui;background:#eef5f6;margin:0;display:grid;place-items:center;min-height:100vh}.box{background:white;padding:28px;border-radius:16px;box-shadow:0 8px 30px #0002;width:min(90vw,380px)}input,button{width:100%;padding:12px;margin-top:10px;box-sizing:border-box;border-radius:9px;border:1px solid #ccd}button{background:#168aad;color:#fff;border:0;font-weight:700}.err{color:#b3261e}</style></head><body><form class="box" method="post" action="/operator/login"><h1>💧 Irrigazione</h1><p>Portale operatore</p>{error}<input name="username" placeholder="Nome utente" required><input type="password" name="password" placeholder="Password" required><button>Accedi</button></form></body></html>'''

@app.get('/operator',response_class=HTMLResponse)
async def operator(request:Request):
    u=current_user(request)
    if not u:return HTMLResponse(LOGIN.format(error=''))
    return HTMLResponse(f'''<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Irrigazione</title><style>body{{font-family:system-ui;background:#f4f7f8;margin:0;color:#172126}}header{{padding:18px;background:white;display:flex;justify-content:space-between}}main{{max-width:950px;margin:auto;padding:16px}}.box,.card{{background:white;border:1px solid #dbe3e6;border-radius:14px;padding:16px;margin-bottom:14px}}button{{border:0;border-radius:8px;padding:10px 14px;background:#168aad;color:#fff;font-weight:700;margin:4px}}.danger{{background:#c0392b}}.warning{{background:#ef6c00}}.step{{display:flex;justify-content:space-between;align-items:center;border-top:1px solid #ddd;padding:10px 0}}small{{color:#66747c}}</style></head><body><header><strong>💧 Irrigazione</strong><span>{u['display_name']} · <a href="/operator/logout">Esci</a></span></header><main><div id="state" class="box">Caricamento…</div><h2>Programmi</h2><div id="programs"></div></main><script>
const api=async(p,o={{}})=>{{const r=await fetch('/operator/api/'+p,o);if(!r.ok)throw Error(await r.text());return r.json()}};
async function load(){{const [s,p]=await Promise.all([api('state'),api('programs')]);document.querySelector('#state').innerHTML=s.running?`<h2>${{s.program_name}}</h2><p>Zona: <b>${{s.zone_name||'—'}}</b> · ${{s.remaining_seconds}} sec</p><div>${{(s.steps||[]).map((x,i)=>`<div class="step"><span>${{i+1}}. ${{x.zone_name}}<br><small>${{x.status}}</small></span>${{['running','pending'].includes(x.status)?`<button class="warning" onclick="skip(${{i}})">Salta zona</button>`:''}}</div>`).join('')}}</div><button class="danger" onclick="stopAll()">Arresta tutto</button>`:'<h2>Sistema pronto</h2><p>Nessun programma in corso.</p>';document.querySelector('#programs').innerHTML=p.map(x=>`<div class="card"><h3>${{x.name}}</h3><p>${{x.steps.map(z=>z.zone_name+' · '+z.duration_minutes+' min').join('<br>')}}</p><button onclick="start(${{x.id}})">Avvia</button></div>`).join('')}}
async function start(id){{if(confirm('Avviare questo programma?')){{await api('programs/'+id+'/start',{{method:'POST'}});load()}}}}async function stopAll(){{if(confirm('Arrestare tutto?')){{await api('stop',{{method:'POST'}});load()}}}}async function skip(i){{if(confirm('Saltare questa zona?')){{await api('skip-zone/'+i,{{method:'POST'}});load()}}}}load();setInterval(load,2000);
</script></body></html>''')

@app.post('/operator/login')
async def login(username:str=Form(...),password:str=Form(...)):
    with db() as c: row=c.execute('SELECT * FROM operator_users WHERE username=? COLLATE NOCASE',(username.strip(),)).fetchone()
    if not row or not row['enabled'] or not verify(password,row): return HTMLResponse(LOGIN.format(error='<p class="err">Credenziali non valide</p>'),status_code=401)
    with db() as c:c.execute('UPDATE operator_users SET last_login=? WHERE id=?',(datetime.now().isoformat(timespec='seconds'),row['id']));c.commit()
    audit(row['username'],'login','Accesso al portale')
    r=RedirectResponse('/operator',303);r.set_cookie('irrigation_operator',token_for(row['id'],row['username']),httponly=True,samesite='strict',max_age=28800,path='/operator');return r

@app.get('/operator/logout')
async def logout(request:Request):
    u=current_user(request)
    if u:audit(u['username'],'logout','Uscita dal portale')
    r=RedirectResponse('/operator',303);r.delete_cookie('irrigation_operator',path='/operator');return r

def require(request):
    u=current_user(request)
    if not u:raise HTTPException(401,'Sessione scaduta')
    return u
@app.get('/operator/api/state')
async def state(request:Request):require(request);return await admin('GET','state')
@app.get('/operator/api/programs')
async def programs(request:Request):require(request);return await admin('GET','programs')
@app.post('/operator/api/programs/{pid}/start')
async def start(pid:int,request:Request):
    u=require(request);r=await admin('POST',f'programs/{pid}/start');audit(u['username'],'start_program',str(pid));return r
@app.post('/operator/api/stop')
async def stop(request:Request):
    u=require(request);r=await admin('POST','stop');audit(u['username'],'stop','Arresto totale');return r
@app.post('/operator/api/skip-zone/{idx}')
async def skip(idx:int,request:Request):
    u=require(request);r=await admin('POST',f'skip-zone/{idx}');audit(u['username'],'skip_zone',str(idx));return r
