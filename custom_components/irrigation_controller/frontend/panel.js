class IrrigationControllerPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.data = null;
    this.tab = "dashboard";
    this.refreshTimer = null;
  }

  set hass(value) {
    this._hass = value;
    if (!this.data) this.load();
  }

  connectedCallback() {
    this.render();
    this.refreshTimer = setInterval(() => this.load(false), 3000);
  }

  disconnectedCallback() {
    if (this.refreshTimer) clearInterval(this.refreshTimer);
  }

  async ws(type, extra = {}) {
    return this._hass.callWS({ type: `irrigation_controller/${type}`, ...extra });
  }

  async load(render = true) {
    if (!this._hass) return;
    try {
      this.data = await this.ws("state");
      if (render) this.render();
      else this.renderRuntime();
    } catch (err) {
      this.toast(err.message || String(err));
    }
  }

  esc(value) {
    return String(value ?? "").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
  }

  entityOptions(domains) {
    if (!this._hass) return "";
    return Object.values(this._hass.states)
      .filter(s => domains.includes(s.entity_id.split(".")[0]))
      .sort((a,b) => a.entity_id.localeCompare(b.entity_id))
      .map(s => `<option value="${this.esc(s.entity_id)}"></option>`).join("");
  }

  styles() {
    return `
      :host{display:block;background:var(--primary-background-color);min-height:100vh;color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family,Arial,sans-serif)}
      *{box-sizing:border-box}.wrap{max-width:1180px;margin:auto;padding:24px}.head{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px}.head h1{margin:0;font-size:28px}.sub{color:var(--secondary-text-color);font-size:14px}
      .tabs{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}.tab{border:0;border-radius:20px;padding:9px 15px;background:var(--card-background-color);color:var(--primary-text-color);cursor:pointer}.tab.active{background:var(--primary-color);color:var(--text-primary-color)}
      .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:16px}.card{background:var(--card-background-color);border-radius:14px;padding:18px;box-shadow:var(--ha-card-box-shadow,0 2px 8px rgba(0,0,0,.12))}.card h2,.card h3{margin-top:0}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.between{justify-content:space-between}.muted{color:var(--secondary-text-color)}
      button{border:0;border-radius:8px;padding:9px 13px;background:var(--primary-color);color:var(--text-primary-color);cursor:pointer;font-weight:600}button.secondary{background:var(--secondary-background-color);color:var(--primary-text-color)}button.danger{background:var(--error-color,#db4437);color:#fff}button:disabled{opacity:.45;cursor:not-allowed}
      input,select{width:100%;padding:10px;border:1px solid var(--divider-color);border-radius:8px;background:var(--card-background-color);color:var(--primary-text-color)}label{display:block;font-size:13px;margin:10px 0 5px;color:var(--secondary-text-color)}input[type=checkbox]{width:auto}.check{display:flex;gap:8px;align-items:center;margin:10px 0}.check label{margin:0;color:var(--primary-text-color)}
      .item{padding:12px 0;border-bottom:1px solid var(--divider-color)}.item:last-child{border-bottom:0}.pill{display:inline-block;border-radius:12px;padding:3px 8px;background:var(--secondary-background-color);font-size:12px}.ok{color:var(--success-color,#43a047)}.bad{color:var(--error-color,#db4437)}
      .runtime{font-size:18px}.count{font-size:32px;font-weight:700}.days{display:flex;gap:7px;flex-wrap:wrap}.days label{margin:0;padding:7px 9px;border-radius:8px;background:var(--secondary-background-color);color:var(--primary-text-color)}.days input{width:auto}
      .step{display:grid;grid-template-columns:1fr 110px;gap:10px;align-items:center;margin:8px 0}.hidden{display:none}.empty{text-align:center;padding:30px;color:var(--secondary-text-color)}
      dialog{border:0;border-radius:14px;background:var(--card-background-color);color:var(--primary-text-color);width:min(680px,94vw);max-height:90vh;overflow:auto;padding:22px;box-shadow:0 12px 35px rgba(0,0,0,.35)}dialog::backdrop{background:rgba(0,0,0,.5)}.actions{display:flex;justify-content:flex-end;gap:8px;margin-top:18px}
      @media(max-width:600px){.wrap{padding:14px}.head{align-items:flex-start;flex-direction:column}.head h1{font-size:23px}}
    `;
  }

  render() {
    if (!this.shadowRoot) return;
    const cfg = this.data?.config || { zones: [], programs: [], logs: [], master_enabled: true };
    const rt = this.data?.runtime || {};
    this.shadowRoot.innerHTML = `
      <style>${this.styles()}</style>
      <datalist id="actuators">${this.entityOptions(["switch","valve","input_boolean"])}</datalist>
      <datalist id="sensors">${this.entityOptions(["sensor","binary_sensor"])}</datalist>
      <datalist id="weather">${this.entityOptions(["weather"])}</datalist>
      <div class="wrap">
        <div class="head"><div><h1>💧 Irrigation Controller</h1><div class="sub">Gestione nativa dell'irrigazione in Home Assistant</div></div><div class="row"><span>Automazione generale</span><input id="master" type="checkbox" ${cfg.master_enabled ? "checked" : ""}></div></div>
        <div class="tabs">
          ${[["dashboard","Stato"],["zones","Zone"],["programs","Programmi"],["logs","Registro"]].map(([id,n])=>`<button class="tab ${this.tab===id?"active":""}" data-tab="${id}">${n}</button>`).join("")}
        </div>
        <section id="view"></section>
      </div>
      <dialog id="zoneDialog"></dialog><dialog id="programDialog"></dialog>
    `;
    this.shadowRoot.querySelectorAll("[data-tab]").forEach(b => b.onclick = () => { this.tab = b.dataset.tab; this.render(); });
    this.shadowRoot.querySelector("#master").onchange = async e => { this.data = await this.ws("set_master", {enabled:e.target.checked}); this.render(); };
    this.renderView();
  }

  renderView() {
    const view = this.shadowRoot.querySelector("#view");
    if (!this.data) { view.innerHTML = `<div class="card">Caricamento…</div>`; return; }
    if (this.tab === "dashboard") this.renderDashboard(view);
    if (this.tab === "zones") this.renderZones(view);
    if (this.tab === "programs") this.renderPrograms(view);
    if (this.tab === "logs") this.renderLogs(view);
  }

  renderDashboard(view) {
    const cfg=this.data.config, rt=this.data.runtime;
    view.innerHTML=`<div class="grid">
      <div class="card"><h2>Stato</h2><div id="runtimeBox"></div></div>
      <div class="card"><h2>Configurazione</h2><div class="count">${cfg.zones.length}</div><div class="muted">zone configurate</div><br><div class="count">${cfg.programs.length}</div><div class="muted">programmi</div></div>
      <div class="card"><h2>Prossime azioni</h2>${cfg.programs.filter(p=>p.enabled).length ? cfg.programs.filter(p=>p.enabled).map(p=>`<div class="item"><b>${this.esc(p.name)}</b><br><span class="muted">${this.esc((p.start_times||[]).join(", ") || p.sun_event || "manuale")}</span></div>`).join("") : `<div class="empty">Nessun programma attivo</div>`}</div>
    </div>`;
    this.renderRuntime();
  }

  renderRuntime() {
    if (!this.data || this.tab!=="dashboard") return;
    const box=this.shadowRoot.querySelector("#runtimeBox"); if(!box)return;
    const rt=this.data.runtime;
    if(!rt.running){box.innerHTML=`<div class="runtime ok">● Impianto fermo</div>${rt.last_error?`<p class="bad">${this.esc(rt.last_error)}</p>`:""}`;return;}
    const min=Math.floor((rt.remaining_seconds||0)/60), sec=(rt.remaining_seconds||0)%60;
    box.innerHTML=`<div class="runtime ok">● Irrigazione in corso</div><h3>${this.esc(rt.program_name)}</h3><p>Zona: <b>${this.esc(rt.zone_name||"-")}</b></p><div class="count">${min}:${String(sec).padStart(2,"0")}</div><p class="muted">tempo rimanente</p><div class="row"><button id="skipNow" class="secondary">Salta zona</button><button id="stopNow" class="danger">Ferma</button></div>`;
    box.querySelector("#skipNow").onclick=async()=>{await this.ws("skip");await this.load();};
    box.querySelector("#stopNow").onclick=async()=>{await this.ws("stop");await this.load();};
  }

  renderZones(view) {
    const zones=this.data.config.zones;
    view.innerHTML=`<div class="row between"><h2>Zone</h2><button id="addZone">+ Nuova zona</button></div><div class="grid">${zones.length?zones.map(z=>`<div class="card"><div class="row between"><h3>${this.esc(z.name)}</h3><span class="pill">${z.enabled!==false?"attiva":"disattivata"}</span></div><p><b>Valvola:</b> ${this.esc(z.valve_entity)}</p>${z.moisture_enabled?`<p><b>Umidità:</b> ${this.esc(z.moisture_entity)} · soglia ${this.esc(z.moisture_min)}%</p>`:""}<p class="muted">Massimo ${this.esc(z.max_minutes||720)} min</p><div class="row"><button class="secondary editZone" data-id="${z.id}">Modifica</button><button class="danger delZone" data-id="${z.id}">Elimina</button></div></div>`).join(""):`<div class="card empty">Crea la prima zona e associa una valvola o uno switch di Home Assistant.</div>`}</div>`;
    view.querySelector("#addZone").onclick=()=>this.openZone();
    view.querySelectorAll(".editZone").forEach(b=>b.onclick=()=>this.openZone(b.dataset.id));
    view.querySelectorAll(".delZone").forEach(b=>b.onclick=async()=>{if(confirm("Eliminare questa zona?")){this.data=await this.ws("delete_zone",{zone_id:b.dataset.id});this.render();}});
  }

  openZone(id=null){
    const old=this.data.config.zones.find(z=>z.id===id)||{}; const d=this.shadowRoot.querySelector("#zoneDialog");
    d.innerHTML=`<h2>${id?"Modifica":"Nuova"} zona</h2><form id="zoneForm"><label>Nome</label><input name="name" required value="${this.esc(old.name||"")}"><label>Entità valvola/switch</label><input name="valve_entity" list="actuators" required value="${this.esc(old.valve_entity||"")}"><div class="check"><input name="enabled" type="checkbox" ${old.enabled!==false?"checked":""}><label>Zona attiva</label></div><label>Durata massima (minuti)</label><input name="max_minutes" type="number" min="1" max="720" value="${old.max_minutes||60}"><div class="check"><input id="moistEnable" name="moisture_enabled" type="checkbox" ${old.moisture_enabled?"checked":""}><label>Salta se il terreno è già umido</label></div><label>Sensore umidità</label><input name="moisture_entity" list="sensors" value="${this.esc(old.moisture_entity||"")}"><label>Soglia umidità (%)</label><input name="moisture_min" type="number" step="0.1" value="${old.moisture_min??60}"><div class="actions"><button type="button" class="secondary" id="cancelZone">Annulla</button><button type="submit">Salva zona</button></div></form>`;
    d.querySelector("#cancelZone").onclick=()=>d.close();
    d.querySelector("#zoneForm").onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target);const zone={...(id?{id}:{}),name:f.get("name").trim(),valve_entity:f.get("valve_entity").trim(),enabled:f.has("enabled"),max_minutes:Number(f.get("max_minutes")),moisture_enabled:f.has("moisture_enabled"),moisture_entity:f.get("moisture_entity").trim()||null,moisture_min:Number(f.get("moisture_min"))};this.data=await this.ws("save_zone",{zone});d.close();this.render();};
    d.showModal();
  }

  renderPrograms(view){
    const programs=this.data.config.programs,zones=this.data.config.zones;
    view.innerHTML=`<div class="row between"><h2>Programmi</h2><button id="addProgram" ${zones.length?"":"disabled"}>+ Nuovo programma</button></div>${zones.length?"":`<div class="card empty">Prima crea almeno una zona.</div>`}<div class="grid">${programs.map(p=>`<div class="card"><div class="row between"><h3>${this.esc(p.name)}</h3><span class="pill">${p.enabled!==false?"attivo":"spento"}</span></div><p><b>Giorni:</b> ${this.dayNames(p.weekdays)}</p><p><b>Avvio:</b> ${this.esc((p.start_times||[]).join(", ") || (p.sun_event!=="none"?`${p.sun_event} ${p.sun_offset_minutes||0} min`:"manuale"))}</p><p><b>Sequenza:</b> ${(p.steps||[]).map(s=>{const z=zones.find(x=>x.id===s.zone_id);return z?`${this.esc(z.name)} (${s.duration_minutes}m)`:""}).filter(Boolean).join(" → ")||"-"}</p><div class="row"><button class="runProgram" data-id="${p.id}">Avvia</button><button class="secondary editProgram" data-id="${p.id}">Modifica</button><button class="danger delProgram" data-id="${p.id}">Elimina</button></div></div>`).join("")}</div>`;
    view.querySelector("#addProgram").onclick=()=>this.openProgram();
    view.querySelectorAll(".editProgram").forEach(b=>b.onclick=()=>this.openProgram(b.dataset.id));
    view.querySelectorAll(".runProgram").forEach(b=>b.onclick=async()=>{await this.ws("run",{program_id:b.dataset.id});this.tab="dashboard";await this.load();});
    view.querySelectorAll(".delProgram").forEach(b=>b.onclick=async()=>{if(confirm("Eliminare questo programma?")){this.data=await this.ws("delete_program",{program_id:b.dataset.id});this.render();}});
  }

  dayNames(days=[]){const n=["Lun","Mar","Mer","Gio","Ven","Sab","Dom"];return days.length?days.map(i=>n[i]).join(", "):"-";}

  openProgram(id=null){
    const old=this.data.config.programs.find(p=>p.id===id)||{};const zones=this.data.config.zones;const d=this.shadowRoot.querySelector("#programDialog");const selected=new Map((old.steps||[]).map(s=>[s.zone_id,s.duration_minutes]));
    d.innerHTML=`<h2>${id?"Modifica":"Nuovo"} programma</h2><form id="programForm"><label>Nome</label><input name="name" required value="${this.esc(old.name||"")}"><div class="check"><input name="enabled" type="checkbox" ${old.enabled!==false?"checked":""}><label>Programma attivo</label></div><label>Giorni</label><div class="days">${["Lun","Mar","Mer","Gio","Ven","Sab","Dom"].map((n,i)=>`<label><input type="checkbox" name="day" value="${i}" ${(old.weekdays||[]).includes(i)?"checked":""}> ${n}</label>`).join("")}</div><label>Orari di avvio (HH:MM, separati da virgola)</label><input name="start_times" placeholder="06:30, 20:15" value="${this.esc((old.start_times||[]).join(", "))}"><label>Oppure evento solare</label><select name="sun_event"><option value="none">Nessuno</option><option value="sunrise" ${old.sun_event==="sunrise"?"selected":""}>Alba</option><option value="sunset" ${old.sun_event==="sunset"?"selected":""}>Tramonto</option></select><label>Offset evento solare (minuti, anche negativo)</label><input name="sun_offset_minutes" type="number" min="-240" max="240" value="${old.sun_offset_minutes||0}"><hr><h3>Sequenza zone</h3><p class="muted">Spunta le zone da irrigare e imposta la durata.</p>${zones.map(z=>`<div class="step"><label style="margin:0"><input type="checkbox" name="zone_${z.id}" ${selected.has(z.id)?"checked":""}> ${this.esc(z.name)}</label><input type="number" name="dur_${z.id}" min="1" max="720" value="${selected.get(z.id)||10}"></div>`).join("")}<hr><h3>Pompa e protezioni</h3><label>Entità pompa (opzionale)</label><input name="pump_entity" list="actuators" value="${this.esc(old.pump_entity||"")}"><div class="row"><div style="flex:1"><label>Anticipo pompa (s)</label><input name="pump_lead_seconds" type="number" min="0" max="300" value="${old.pump_lead_seconds??3}"></div><div style="flex:1"><label>Ritardo spegnimento (s)</label><input name="pump_lag_seconds" type="number" min="0" max="300" value="${old.pump_lag_seconds??3}"></div><div style="flex:1"><label>Pausa tra zone (s)</label><input name="inter_zone_seconds" type="number" min="0" max="300" value="${old.inter_zone_seconds??5}"></div></div><div class="check"><input name="rain_skip_enabled" type="checkbox" ${old.rain_skip_enabled?"checked":""}><label>Salta il programma se sta piovendo</label></div><label>Entità meteo</label><input name="weather_entity" list="weather" value="${this.esc(old.weather_entity||"")}"><div class="actions"><button type="button" class="secondary" id="cancelProgram">Annulla</button><button type="submit">Salva programma</button></div></form>`;
    d.querySelector("#cancelProgram").onclick=()=>d.close();
    d.querySelector("#programForm").onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target);const times=String(f.get("start_times")||"").split(",").map(x=>x.trim()).filter(Boolean);if(times.some(x=>!/^([01]\d|2[0-3]):[0-5]\d$/.test(x))){alert("Uno degli orari non è valido. Usa HH:MM.");return;}const steps=zones.filter(z=>f.has(`zone_${z.id}`)).map(z=>({zone_id:z.id,duration_minutes:Number(f.get(`dur_${z.id}`))||1}));const program={...(id?{id}:{}),name:f.get("name").trim(),enabled:f.has("enabled"),weekdays:f.getAll("day").map(Number),start_times:times,sun_event:f.get("sun_event"),sun_offset_minutes:Number(f.get("sun_offset_minutes"))||0,steps,pump_entity:f.get("pump_entity").trim()||null,pump_lead_seconds:Number(f.get("pump_lead_seconds"))||0,pump_lag_seconds:Number(f.get("pump_lag_seconds"))||0,inter_zone_seconds:Number(f.get("inter_zone_seconds"))||0,rain_skip_enabled:f.has("rain_skip_enabled"),weather_entity:f.get("weather_entity").trim()||null};this.data=await this.ws("save_program",{program});d.close();this.render();};
    d.showModal();
  }

  renderLogs(view){
    const logs=this.data.config.logs||[];view.innerHTML=`<div class="row between"><h2>Registro</h2><span class="muted">ultimi ${logs.length} eventi</span></div><div class="card">${logs.length?logs.map(l=>`<div class="item"><div class="row between"><b>${this.esc(l.program_name||"Irrigazione")}${l.zone_name?` · ${this.esc(l.zone_name)}`:""}</b><span class="pill ${l.status==="error"?"bad":""}">${this.esc(l.status)}</span></div><div class="muted">${this.esc(new Date(l.at).toLocaleString())} · ${this.esc(l.source||"")}</div>${l.message?`<div>${this.esc(l.message)}</div>`:""}</div>`).join(""):`<div class="empty">Nessun evento registrato.</div>`}</div>`;
  }

  toast(message){console.error("Irrigation Controller:",message);}
}

customElements.define("irrigation-controller-panel", IrrigationControllerPanel);
