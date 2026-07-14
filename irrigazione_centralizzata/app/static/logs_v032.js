function irrigationStatusLabel(status){
  return ({running:'In corso',completed:'Completata',error:'Errore',stopped:'Interrotta',skipped:'Saltata',disabled:'Disabilitata'})[status]||status||'—';
}

function irrigationLogMessage(row){
  const message=String(row.message||'').trim();
  if(message)return message;
  if(row.status==='error')return 'Errore registrato senza dettaglio tecnico. Controlla anche il Registro dell’app subito dopo il tentativo.';
  if(row.status==='running')return 'Irrigazione avviata. La riga conclusiva riporterà l’esito e l’eventuale errore.';
  return 'Nessuna nota.';
}

function irrigationDuration(row){
  if(row.actual_seconds!=null){
    const seconds=Math.max(0,Number(row.actual_seconds)||0);
    if(seconds<60)return `${seconds} sec`;
    const minutes=Math.floor(seconds/60),rest=seconds%60;
    return rest?`${minutes} min ${rest} sec`:`${minutes} min`;
  }
  if(row.planned_minutes!=null)return `${Number(row.planned_minutes)} min previsti`;
  return '—';
}

async function loadLogs(){
  const target=document.querySelector('#logCards');
  const legacy=document.querySelector('#logRows');
  try{
    const logs=await api('api/logs');
    if(legacy)legacy.innerHTML='';
    if(!target)return;
    if(!logs.length){
      target.innerHTML='<p class="muted">Nessuna irrigazione registrata.</p>';
      return;
    }
    target.innerHTML=logs.map(row=>{
      const status=String(row.status||'').toLowerCase();
      const isError=status==='error';
      const message=irrigationLogMessage(row);
      const date=String(row.started_at||'').replace('T',' ');
      return `<article class="irrigation-log-card ${isError?'has-error':''}">
        <div class="irrigation-log-head">
          <div><small>${esc(date||'Data non disponibile')}</small><h3>${esc(row.program_name||row.zone_name||'Irrigazione manuale')}</h3></div>
          <span class="log-status ${esc(status)}">${esc(irrigationStatusLabel(status))}</span>
        </div>
        <div class="irrigation-log-meta">
          <span><b>Zona</b>${esc(row.zone_name||'—')}</span>
          <span><b>Durata</b>${esc(irrigationDuration(row))}</span>
          <span><b>Avvio</b>${esc(row.source||'—')}</span>
        </div>
        <div class="irrigation-log-message ${isError?'error-message':''}">
          <b>${isError?'Motivo dell’errore':'Nota'}</b>
          <p>${esc(message)}</p>
        </div>
      </article>`;
    }).join('');
  }catch(error){
    if(target)target.innerHTML=`<div class="irrigation-log-message error-message"><b>Impossibile caricare il registro</b><p>${esc(error.message)}</p></div>`;
  }
}

window.loadLogs=loadLogs;
document.querySelector('#refreshLogs')?.addEventListener('click',loadLogs);
