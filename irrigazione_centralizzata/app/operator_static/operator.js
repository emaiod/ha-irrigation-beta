const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

async function api(path,options={}){
  const response=await fetch(`/operator/api/${path}`,{headers:{'Content-Type':'application/json'},...options});
  if(response.status===401){location.href='/operator';throw new Error('Sessione scaduta');}
  if(!response.ok)throw new Error(await response.text());
  return response.json();
}

function statusLabel(status){return({pending:'In attesa',running:'In corso',completed:'Completata',skipped:'Saltata',disabled:'Disabilitata',stopped:'Interrotta',error:'Errore'})[status]||status;}

async function loadPrograms(){
  const programs=await api('programs');
  document.querySelector('#programs').innerHTML=programs.length?programs.map(program=>`<article class="card program-card"><h3>${esc(program.name)}</h3><p>${(program.steps||[]).map(step=>`${esc(step.zone_name)} · ${Number(step.duration_minutes)} min`).join('<br>')||'Nessuna zona configurata'}</p><button onclick="startProgram(${program.id},'${esc(program.name).replace(/'/g,'&#39;')}')">Avvia</button></article>`).join(''):'<p class="muted">Nessun programma configurato.</p>';
}

async function loadState(){
  const state=await api('state');
  document.querySelector('#systemStatus').textContent=state.running?'Irrigazione attiva':'Sistema pronto';
  document.querySelector('#programName').textContent=state.program_name||'Nessuno';
  document.querySelector('#zoneName').textContent=state.zone_name||'—';
  const total=Math.max(0,Number(state.remaining_seconds||0));
  document.querySelector('#remaining').textContent=`${String(Math.floor(total/60)).padStart(2,'0')}:${String(total%60).padStart(2,'0')}`;
  const card=document.querySelector('#runtimeCard');
  card.classList.toggle('hidden',!state.running);
  if(!state.running)return;
  document.querySelector('#runtimeTitle').textContent=state.program_name||'Programma in corso';
  document.querySelector('#runtimeSteps').innerHTML=(state.steps||[]).map((step,index)=>{const canSkip=['running','pending'].includes(step.status);return `<div class="step ${esc(step.status)}"><span class="step-number">${index+1}</span><div><strong>${esc(step.zone_name)}</strong><small>${Number(step.duration_minutes)} min · ${statusLabel(step.status)}</small></div>${canSkip?`<button class="warning" onclick="skipZone(${index},'${esc(step.zone_name).replace(/'/g,'&#39;')}')">Salta zona</button>`:''}</div>`;}).join('');
}

window.startProgram=async(id,name)=>{if(!confirm(`Avviare ${name}?`))return;try{await api(`programs/${id}/start`,{method:'POST'});await loadState();}catch(error){showError(error.message);}};
window.skipZone=async(index,name)=>{if(!confirm(`Saltare la zona ${name}?`))return;try{await api(`skip-zone/${index}`,{method:'POST'});await loadState();}catch(error){showError(error.message);}};
document.querySelector('#stopButton')?.addEventListener('click',async()=>{if(!confirm('Arrestare completamente il programma?'))return;try{await api('stop',{method:'POST'});await loadState();}catch(error){showError(error.message);}});

function showError(message){const box=document.querySelector('#pageError');box.textContent=message;box.classList.remove('hidden');}

(async()=>{try{await Promise.all([loadPrograms(),loadState()]);setInterval(()=>loadState().catch(error=>showError(error.message)),2000);}catch(error){showError(error.message);}})();
