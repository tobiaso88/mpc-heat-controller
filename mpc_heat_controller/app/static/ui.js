// Presentation only. All configuration and control requests remain in app.js.
const piCard=$('control-status').closest('article');
piCard.classList.add('control-card');
document.querySelector('.metrics').after(piCard);
const actions=document.createElement('div');actions.className='control-actions';
$('control-start').before(actions);actions.append($('control-start'),$('control-stop'));
$('control-start').classList.add('primary');$('control-stop').classList.add('stop-button');
function fold(nodes,title,open=false){const details=document.createElement('details');details.className='fold';details.open=open;const summary=document.createElement('summary');summary.textContent=title;nodes[0].before(details);details.append(summary,...nodes);return details}
fold([$('pi-status'),$('pi-values')],'PI-beräkning och begränsningar');
fold([$('entities-status')],'Anslutning till Home Assistant');
const chartCard=$('chart').closest('article');
fold([chartCard],'Inomhusprognos · experimentell modell');
const weatherCard=$('weather-status').closest('article');
weatherCard.classList.add('weather-card');
const weatherGraph=document.createElement('div');weatherGraph.className='trend-graph';weatherCard.querySelector('.table-wrap').before(weatherGraph);
fold([weatherCard.querySelector('.table-wrap')],'Visa timprognos');
const historyCard=document.createElement('article');historyCard.className='chart-card';
historyCard.innerHTML='<h2>Temperatur och reglering</h2><p id="trends-status">Läser mätloggen…</p><h3>Inomhus och komfortmål</h3><div id="indoor-trend" class="trend-graph"></div><h3>Utetemperatur och styrning</h3><p>Ohmigo visar inställt värde. PI-förslaget är beräknat och behöver inte vara skickat.</p><div id="control-trend" class="trend-graph"></div>';
piCard.after(historyCard);
function plot(host,points,series,title,maxGap){
    host.replaceChildren();
    const available=points.flatMap(p=>series.map(s=>p[s.key]).filter(v=>typeof v==='number'&&Number.isFinite(v)));
    if(!available.length){const p=document.createElement('p');p.textContent='Inga mätvärden att visa ännu.';host.append(p);return}
    const times=points.map(p=>Date.parse(p.datetime));const first=Math.min(...times),last=Math.max(...times);
    const min=Math.floor(Math.min(...available)-.5),max=Math.ceil(Math.max(...available)+.5);
    const x=t=>58+((t-first)/Math.max(last-first,3600000))*790,y=v=>225-(v-min)/Math.max(1,max-min)*195;
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 880 280');svg.setAttribute('role','img');svg.setAttribute('aria-label',title);
    function node(tag,attrs,text){const e=document.createElementNS(svg.namespaceURI,tag);for(const [key,value] of Object.entries(attrs))e.setAttribute(key,value);if(text)e.textContent=text;svg.append(e);return e}
    for(let i=0;i<5;i++){const v=min+(max-min)*i/4;node('line',{x1:58,x2:848,y1:y(v),y2:y(v),stroke:'#d8e2e8'});node('text',{x:4,y:y(v)+5,fill:'#526974','font-size':14},v.toFixed(1)+'°')}
    for(let i=0;i<4;i++){const t=first+(last-first)*i/3;node('text',{x:x(t),y:259,'text-anchor':i===0?'start':i===3?'end':'middle',fill:'#526974','font-size':14},new Date(t).toLocaleString('sv-SE',{day:'numeric',month:'numeric',hour:'2-digit',minute:'2-digit'}))}
    for(const s of series){let previous=null;for(const p of points){const v=p[s.key],t=Date.parse(p.datetime);if(typeof v!=='number'||!Number.isFinite(v)){previous=null;continue}if(previous&&t-previous.t<=maxGap&&previous.group===p.group)node('line',{x1:x(previous.t),y1:y(previous.v),x2:x(t),y2:y(v),stroke:s.color,'stroke-width':2.5,...(s.dash?{'stroke-dasharray':'6 4'}:{})});const dot=node('circle',{cx:x(t),cy:y(v),r:2.5,fill:s.color});const tip=document.createElementNS(svg.namespaceURI,'title');tip.textContent=`${s.label}: ${v.toFixed(2)} °C · ${new Date(t).toLocaleString('sv-SE')}`;dot.append(tip);previous={t,v,group:p.group}}}
    const scroller=document.createElement('div');scroller.className='graph-scroll';scroller.tabIndex=0;scroller.setAttribute('aria-label',title+' – rulla i sidled på liten skärm');scroller.append(svg);host.append(scroller);
    const legend=document.createElement('div');legend.className='graph-legend';for(const s of series){const span=document.createElement('span');span.textContent=(s.dash?'┄ ':'● ')+s.label;span.style.color=s.color;legend.append(span)}host.append(legend);
}
const originalTelemetry=telemetry;
telemetry=async function(){await originalTelemetry();try{const t=await api('telemetry');plot(weatherGraph,(t.forecast?.points||[]).filter(p=>Date.parse(p.datetime)<=Date.now()+24*3600000),[{key:'temperature',label:'Prognos utomhus',color:'#146b88'}],'Väderprognos kommande 24 timmar',5400000)}catch(e){weatherGraph.textContent='Vädergrafen kunde inte hämtas.'}};
async function loadTrends(){try{const r=await api('trends');$('trends-status').textContent=r.message;plot($('indoor-trend'),r.points,[{key:'indoor',label:'Inomhus',color:'#146b88'},{key:'target',label:'Börvärde',color:'#956211',dash:true}],'Inomhustemperatur och börvärde',1200000);plot($('control-trend'),r.points,[{key:'outdoor',label:'Verklig utetemperatur',color:'#527482'},{key:'applied',label:'Ohmigo inställt',color:'#146b88'},{key:'proposal',label:'PI-förslag',color:'#97547e',dash:true}],'Utetemperatur och reglering',1200000)}catch(e){$('trends-status').textContent='Kunde inte läsa mätloggen.'}}
loadTrends();setInterval(loadTrends,60000);
const stepNames=['Givare','Komfort & styrning','Granska'];
const originalSetStep=setStep;
setStep=function(n){originalSetStep(n);document.querySelectorAll('.steps li').forEach((li,i)=>{li.textContent=`${i+1}. ${stepNames[i]}`;li.classList.toggle('current',i===n);if(i===n)li.setAttribute('aria-current','step');else li.removeAttribute('aria-current')});$('setup').scrollIntoView({block:'start',behavior:'instant'})};
document.querySelectorAll('nav button').forEach(b=>b.addEventListener('click',()=>{document.querySelectorAll('nav button').forEach(x=>x.setAttribute('aria-current',x===b?'page':'false'))}));
function picker(select){
    if(select.dataset.enhanced)return;
    select.dataset.enhanced='yes';
    const parent=select.parentElement,fieldset=document.createElement('fieldset'),legend=document.createElement('legend');
    legend.textContent=parent.firstChild.textContent.trim();fieldset.className='sensor-picker';parent.before(fieldset);fieldset.append(legend,select);parent.remove();select.hidden=true;
    const search=document.createElement('input');search.type='search';search.placeholder='Sök rum eller entitet';search.setAttribute('aria-label',legend.textContent+' – sök');
    const count=document.createElement('p');count.className='selection-count';count.setAttribute('role','status');
    const list=document.createElement('div');list.className='sensor-options';fieldset.append(search,count,list);
    function render(){const filter=search.value.toLocaleLowerCase('sv');list.replaceChildren();let found=0;for(const option of select.options){if(!option.text.toLocaleLowerCase('sv').includes(filter))continue;found++;const label=document.createElement('label'),check=document.createElement('input'),span=document.createElement('span');check.type='checkbox';check.checked=option.selected;span.textContent=option.text;check.onchange=()=>{option.selected=check.checked;updateCount()};label.append(check,span);list.append(label)}if(!found){const p=document.createElement('p');p.textContent=select.options.length?'Ingen träff. Prova ett annat namn.':'Inga givare tillgängliga ännu.';list.append(p)}updateCount()}
    function updateCount(){count.textContent=`${select.selectedOptions.length} valda`}
    search.oninput=render;
    new MutationObserver(render).observe(select,{childList:true,subtree:true,attributes:true});
    select.addEventListener('picker-refresh',render);render();
}
for(const id of ['indoor','observe','model-indoor'])picker($(id));
// Saved model restoration changes selected properties rather than attributes.
const originalRenderModel=renderModel;
renderModel=function(r){originalRenderModel(r);$('model-indoor').dispatchEvent(new Event('picker-refresh'))};
const originalRenderControl=renderControl;
renderControl=function(c){originalRenderControl(c);piCard.dataset.state=c?.active?'active':c?.auto_restart_pending?'waiting':'stopped';$('control-start').textContent=c?.active?'PI styr värmen':'Aktivera PI-styrning…'};
const originalTableRows=tableRows;
tableRows=function(id,rows){originalTableRows(id,rows);labelTable($(id).closest('table'))};
function labelTable(table){const labels=Array.from(table.querySelectorAll('thead th')).map(th=>th.textContent);table.querySelectorAll('tbody tr').forEach(tr=>Array.from(tr.children).forEach((td,i)=>td.dataset.label=labels[i]||''))}
new MutationObserver(()=>labelTable($('history-table'))).observe($('history-table').querySelector('tbody'),{childList:true});
