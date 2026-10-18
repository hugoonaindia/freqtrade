/* Operator controls. Saved selection and revision guard every trading action. */
(()=>{
const el=id=>document.getElementById(id);
const money=v=>v==null?'—':new Intl.NumberFormat('es-ES',{style:'currency',currency:'USD'}).format(Number(v));
let state=null,busy=false,dirty=false,refreshing=false;
function table(id,rows,columns){const root=el(id);root.replaceChildren();if(!rows.length){root.textContent='Sin registros desde inicio.';return}const t=document.createElement('table'),head=t.createTHead().insertRow();for(const [label] of columns){const th=document.createElement('th');th.textContent=label;head.append(th)}const body=t.createTBody();for(const row of rows){const tr=body.insertRow();for(const [,key,format] of columns){tr.insertCell().textContent=format?format(row[key]):String(row[key]??'—')}}root.append(t)}
async function api(path,payload){const r=await fetch(path,payload===undefined?{}:{method:'POST',headers:{'X-Session-Token':token,'Content-Type':'application/json'},body:JSON.stringify(payload)});const d=await r.json();if(!r.ok||d.error)throw Error(d.error||'No se pudo completar acción.');return d}
function selection(){return {strategy:state.strategy,revision:state.revision}}
function controls(){const running=!!state?.automation.running,connected=!!state?.configured;for(const id of ['strategy','symbols','budget','save'])el(id).disabled=busy||running||!state;el('go').disabled=busy||running||!connected||dirty||(state?.risk?state.risk.tripped:!state?.entries_enabled);if(state?.risk)el('budget').disabled=true;el('stop').disabled=busy||!running;el('reset-campaign').disabled=busy||running||!connected||!state?.risk?.baseline||!!state?.positions?.length||!!state?.orders?.some(o=>['new','accepted','pending_new','partially_filled','held','pending_cancel'].includes(o.status));el('cycle').disabled=busy||running||!connected||dirty||!state?.entries_enabled;el('preview').disabled=busy||running||!connected||dirty;el('refresh').disabled=busy||refreshing;el('clear-history').disabled=busy;el('close-positions').disabled=busy||running||!connected||!state?.clock?.is_open||!state?.positions?.some(p=>p.asset_class==='us_equity'&&Number(p.qty)>0);}
function description(){el('strategy-description').textContent=state?.strategies[el('strategy').value]?.description||''}
function render(d){state=d;const running=d.automation.running;if(!dirty){el('strategy').replaceChildren();for(const [key,spec] of Object.entries(d.strategies)){const o=document.createElement('option');o.value=key;o.textContent=spec.name;el('strategy').append(o)}el('strategy').value=d.strategy;el('symbols').value=d.symbols.join(',');el('budget').value=d.budget;description()}
el('config').textContent=dirty?'Ajustes sin guardar. Guarda antes de operar.':'Guardado: '+d.symbols.join(', ')+(d.risk?' · hasta el 100% del capital disponible según señales.':' · '+money(d.budget)+' por entrada.');
el('automation-status').textContent=!d.entries_enabled?'Entradas bloqueadas':({starting:'Iniciando…',running:'Paper activo',stopping:'Deteniendo…',error:'Error · detenido',stopped:'En pausa',manual:'En pausa'})[d.automation.state]||'En pausa';
if(d.risk){
const risk=d.risk;
el('budget-label').hidden=true;el('cycle').hidden=true;el('settings-form').querySelector('.fields').style.gridTemplateColumns='1fr 1fr';
el('automation-status').textContent=risk.tripped?'Límite global · bloqueado':running?'Paper autónomo activo':'Entradas en pausa';
el('run-detail').textContent='GO PAPER usa la estrategia seleccionada, con stops del 2%. Parar pausa entradas; el supervisor sigue vigilando la cuenta mientras la app esté abierta. Cerrar la app detiene el supervisor; conserva los stops en Alpaca.';
el('risk-baseline').textContent=risk.baseline?'Capital al activar: '+money(risk.baseline)+' · umbral de cierre: '+money(risk.account_floor)+'.':'GO PAPER registrará el patrimonio de cuenta como referencia.';
el('risk-status').textContent=risk.tripped?'Umbral global alcanzado. No se permiten nuevas entradas; revisa la liquidación en Alpaca.':risk.monitor_error||(risk.baseline?'Campaña registrada. Pérdida desde activación: '+(risk.loss_pct==null?'—':risk.loss_pct.toFixed(2)+'%')+'.':'Sin activar. Puedes cambiar estrategia y símbolos antes de iniciar.');
el('risk-check').textContent=risk.last_check?'Última comprobación: '+new Date(risk.last_check).toLocaleString('es-ES'):'Sin comprobación de campaña todavía.';
table('journal',[...(risk.events||[])].reverse(),[['Fecha','time',v=>new Date(v).toLocaleString('es-ES')],['Evento','message']]);
}else{el('run-detail').textContent=d.automation.error||'Guarda ajustes y pulsa GO PAPER.';}

if(d.configured){el('market').textContent=d.clock.is_open?'● Mercado abierto':'○ Mercado cerrado';el('equity').textContent=money(d.account.equity);el('cash').textContent=money(d.account.cash);el('change').textContent=money(Number(d.account.equity)-Number(d.account.last_equity));el('order-count').textContent=d.orders.length+(d.orders.length>=100?' +':'');
table('positions',d.positions,[['Símbolo','symbol'],['Cantidad','qty'],['Valor','market_value',money],['P/G abierta','unrealized_pl',money]]);
table('orders',d.orders,[['Símbolo','symbol'],['Lado','side',v=>v==='buy'?'Compra':'Venta'],['Cantidad','qty'],['Ejecutada','filled_qty'],['Estado','status'],['Creada','created_at',v=>new Date(v).toLocaleString('es-ES')]]);
el('history-info').textContent=d.history_since?'Inicio: '+new Date(d.history_since).toLocaleString('es-ES')+'. Solo órdenes creadas desde esta fecha. Máximo 100 órdenes recientes de cuenta.':'Últimas 100 órdenes de cuenta. «Nuevo historial» oculta anteriores.';
if(d.last?.length)table('signals',d.last,[['Símbolo','symbol'],['Señal','signal',v=>({buy:'Comprar',sell:'Vender',hold:'Esperar'})[v]||'—'],['Motivo','reason'],['Cantidad','qty'],['Estado','status']]);
}else{el('market').textContent='Sin conexión';el('message').textContent=d.setup_message||'Revisa conexión Alpaca.'}
el('updated').textContent='Actualizado '+new Date().toLocaleTimeString('es-ES');controls();}
async function refresh(){if(refreshing)return;refreshing=true;controls();try{render(await api('/api/status'))}catch(e){el('market').textContent='Sin conexión';el('message').textContent=e.message;if(state)state.configured=false;for(const id of ['equity','cash','change','order-count'])el(id).textContent='—'}finally{refreshing=false;controls()}}
async function action(path,payload,success){if(busy)return;busy=true;controls();el('message').textContent='Procesando…';try{const result=await api(path,payload);if(path==='/api/settings'){dirty=false;el('signals').textContent='Ajustes guardados. Revisa señales.'}if(result.results)table('signals',result.results,[['Símbolo','symbol'],['Señal','signal'],['Motivo','reason'],['Cantidad','qty'],['Estado','status']]);el('message').textContent=success;await refresh()}catch(e){el('message').textContent=e.message}finally{busy=false;controls()}}
el('settings-form').onsubmit=e=>{e.preventDefault();action('/api/settings',{strategy:el('strategy').value,symbols:el('symbols').value.split(',').map(s=>s.trim().toUpperCase()),budget:Number(el('budget').value)},'Ajustes guardados. Ninguna orden enviada.')};
for(const id of ['strategy','symbols','budget'])el(id).addEventListener('input',()=>{dirty=true;description();el('config').textContent='Ajustes sin guardar. Guarda antes de operar.';controls()});
el('close-positions').onclick=()=>action('/api/positions/close',{},'Cierre procesado. Revisa señales y operaciones: solicitudes enviadas pueden seguir pendientes.');
el('go').onclick=()=>action('/api/paper/start',selection(),'Campaña Paper activada. Capital de referencia registrado; entradas protegidas habilitadas.');
el('reset-campaign').onclick=()=>{if(confirm('¿Has revisado los resultados? Se archivará la campaña. La próxima activación registrará un nuevo capital de referencia. Solo se permite con cuenta y órdenes vacías.'))action('/api/paper/reset',{confirm:true},'Campaña archivada. Revisa estrategia antes de volver a activar.')};
el('stop').onclick=()=>action('/api/paper/stop',{},'Entradas en pausa. Supervisor y stops de protección siguen activos mientras la app esté abierta.');
el('preview').onclick=()=>action('/api/preview',selection(),'Señales consultadas. Ninguna orden enviada.');
el('cycle').onclick=()=>action('/api/cycle',selection(),'Ciclo Paper completado. Revisa operaciones.');
el('clear-history').onclick=()=>action('/api/history/clear',{},'Historial nuevo. Registros anteriores ocultos; cuenta Alpaca y posiciones conservadas.');
el('refresh').onclick=refresh;
for(const tab of document.querySelectorAll('[data-panel]'))tab.onclick=()=>{for(const item of document.querySelectorAll('[data-panel]'))item.setAttribute('aria-selected',String(item===tab));for(const panel of document.querySelectorAll('.tab-panel'))panel.hidden=panel.id!==tab.dataset.panel};
controls();refresh();setInterval(()=>{if(!busy&&!refreshing)refresh()},5000);
})();
