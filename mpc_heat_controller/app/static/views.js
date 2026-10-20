// Views: Home, Weather, Learning and Settings. Rendering reads `state`; actions go through api().
const HOUR = 3600000;
const COMFORT_KEYS = ['target', 'comfort_min', 'comfort_max', 'target_climates'];
const MIN_HOURS = 250;

const readings = () => state.telemetry?.readings || [];
const reading = id => id ? readings().find(r => r.entity === id) : undefined;
const okValue = id => { const r = reading(id); return r?.quality === 'OK' ? r.value : null; };
const isDemo = () => state.settings?.mode !== 'shadow';
function comfort() { return pendingComfort || {target: state.settings.target, comfort_min: state.settings.comfort_min, comfort_max: state.settings.comfort_max}; }
function toneFor(v, c = comfort()) { return !finite(v) ? 'none' : v < c.comfort_min ? 'cold' : v > c.comfort_max ? 'warm' : 'ok'; }

function controlState() {
  const c = state.telemetry?.control;
  if (state.telemetryFailed) return 'unknown';
  if (c?.active) return 'active';
  if (c?.auto_restart_pending) return 'pending';
  if (c?.fault) return 'fault';
  return isDemo() ? 'demo' : 'shadow';
}

function readinessChecks(c = state.settings) {
  const automation = state.entities.find(e => e.entity_id === c.old_automation);
  const automationText = !c.old_automation ? 'Den gamla automationen är angiven'
    : state.connected && !automation ? 'Den gamla automationen hittas inte i Home Assistant'
    : automation && automation.state !== 'off' ? 'Den gamla automationen är fortfarande på – stäng av den'
    : 'Den gamla automationen är angiven och avstängd';
  return [
    {ok: c.mode === 'shadow', text: 'Skuggläge är valt'},
    {ok: c.indoor.length > 0 && !!c.outdoor, text: 'Rumsgivare och verklig utegivare är valda'},
    {ok: c.applied_signal.startsWith('number.'), text: 'Ohmigos number-entitet är vald som utgång'},
    {ok: !!c.old_automation && (!state.connected || automation?.state === 'off'), text: automationText},
    {ok: c.watchdog_verified && c.watchdog_seconds >= 600, text: 'Watchdog verifierad, minst 600 s timeout'},
    {ok: c.exclusive_writer_confirmed, text: 'Inga andra skrivare till utgången'}
  ];
}
function checklist(list, checks) { list.replaceChildren(...checks.map(c => h('li', {'data-ok': String(c.ok), text: c.text}))); }

/* HOME ===================================================================== */
function renderHome() {
  if (!state.settings) return;
  renderMode(); renderWelcome(); renderHero(); renderControl(); renderHealth(); renderHomeTimeline(); renderChain(); renderPI(); renderRooms();
}

function renderMode() {
  const cs = controlState();
  const [text, tone] = {active: ['Aktiv styrning', 'ok'], pending: ['Väntar på återstart', 'warn'], fault: ['Styrning stoppad', 'err'],
    unknown: ['Status okänd', 'err'], shadow: ['Skuggläge', 'info'], demo: ['Demo', '']}[cs];
  for (const id of ['mode-pill', 'rail-mode']) { $(id).textContent = text; $(id).dataset.tone = tone; }
  const updated = state.updatedAt ? 'Uppdaterad ' + clock(state.updatedAt) : '';
  $('updated').textContent = updated; $('rail-updated').textContent = updated;
}

function renderWelcome() {
  const c = state.settings, box = $('welcome');
  box.hidden = c.mode === 'shadow';
  if (box.hidden) return;
  const steps = [[c.indoor.length > 0, 'Välj rummen som ska styra värmen', 'rum'], [!!c.outdoor, 'Välj verklig utetemperatur', 'system'],
    [!!c.applied_signal, 'Välj Ohmigos inställda temperatur', 'system'], [false, 'Byt till skuggläge och spara', 'lage']];
  const next = steps.find(s => !s[0]);
  box.replaceChildren(
    h('span', {class: 'brand-mark'}, icon('logo')),
    h('h2', {text: 'Välkommen! Det du ser nu är demodata.'}),
    h('p', {text: 'Koppla appen till ditt hus i fyra steg. Inget skickas till värmepumpen förrän du själv startar aktiv styrning.'}),
    h('div', {}, h('ol', {}, steps.map(([done, text]) => h('li', {class: done ? 'done' : '', text}))),
      h('a', {class: 'link', href: '#installningar/' + next[2], text: 'Fortsätt: ' + next[1].toLowerCase() + ' →'})));
}

function indoorTrend() {
  const pts = (state.trends?.points || []).filter(p => finite(p.indoor)).map(p => ({t: Date.parse(p.datetime), v: p.indoor}));
  const last = pts.at(-1);
  if (!last || Date.now() - last.t > 30 * 60000) return null;
  const before = pts.filter(p => last.t - p.t >= 50 * 60000 && last.t - p.t <= 75 * 60000).at(-1);
  return before ? last.v - before.v : null;
}

function renderHero() {
  const st = state.status, c = comfort(), temp = st?.temperature, demo = isDemo();
  const tone = toneFor(temp, c), rooms = state.settings.indoor.length;
  drawDial($('dial'), {temp, target: c.target, min: c.comfort_min, max: c.comfort_max, tone});
  $('hero-temp').textContent = finite(temp) ? num(temp) + '°' : '—';
  $('hero-temp-label').textContent = demo ? 'Simulerat' : rooms === 1 ? 'Inne' : `Medel av ${rooms} rum`;
  $('hero-eyebrow').textContent = demo ? 'Demo · simulerat hus' : 'Just nu';
  const title = $('hero-title'), diff = finite(temp) ? temp - c.target : null;
  if (!st) { title.textContent = 'Hämtar temperatur…'; title.dataset.tone = ''; $('hero-detail').textContent = ''; }
  else if (!finite(temp)) { title.textContent = 'Ingen giltig innetemperatur'; title.dataset.tone = 'err'; $('hero-detail').textContent = st.temperature_message || st.message || ''; }
  else {
    title.dataset.tone = tone;
    title.textContent = tone === 'ok' ? 'Inom ditt komfortområde' : tone === 'cold' ? `${num(c.comfort_min - temp)} °C kallare än komfortområdet` : `${num(temp - c.comfort_max)} °C varmare än komfortområdet`;
    const detail = Math.abs(diff) < .05 ? 'Precis på önskad temperatur.' : `${num(Math.abs(diff))} °C ${diff < 0 ? 'under' : 'över'} önskad temperatur.`;
    $('hero-detail').textContent = st.temperature_valid === false ? st.temperature_message : detail;
  }
  const trend = indoorTrend(), trendEl = $('hero-trend');
  trendEl.replaceChildren();
  if (finite(trend)) trendEl.append(icon(Math.abs(trend) < .05 ? 'flat' : trend > 0 ? 'up' : 'down'),
    Math.abs(trend) < .05 ? 'Stabil senaste timmen' : `${trend > 0 ? '+' : '−'}${num(Math.abs(trend))} °C senaste timmen`);
  $('target-value').textContent = deg(c.target);
  $('target-value').classList.toggle('pending', !!pendingComfort);
  $('target-band').textContent = `Komfortområde ${num(c.comfort_min)}–${num(c.comfort_max)} °C` + (pendingComfort ? ' · sparas strax…' : '');
}

function renderControl() {
  const cs = controlState(), t = state.telemetry, c = t?.control, card = $('control-card');
  card.dataset.state = cs;
  const [headline, lead] = {
    active: ['Aktiv styrning', 'Appen skickar temperaturkommandon till Ohmigo var femte minut.'],
    pending: ['Väntar på återstart', 'Styrningen återupptas när givare och utgång är redo. Inget skickas under väntan.'],
    fault: ['Stoppad – kräver åtgärd', 'Inga kommandon skickas. Åtgärda orsaken nedan och starta igen.'],
    shadow: ['Skuggläge', 'Appen räknar och loggar, men skickar ingenting till värmepumpen.'],
    demo: ['Demoläge', 'Simulerade data. Inget läses från eller skickas till värmepumpen.'],
    unknown: ['Status okänd', 'Kan inte läsa styrstatus. Använd Stoppa eller stoppa appen i Home Assistant.']}[cs];
  $('control-headline').textContent = headline;
  $('control-lead').textContent = lead;
  $('control-message').textContent = c?.message || '';
  const facts = [];
  if (c?.last_command) facts.push(['Senast skickat', deg(c.last_command.value), clock(c.last_command.sent_at * 1000)]);
  const pi = t?.pi, plan = t?.mpc?.plan || [];
  if (finite(pi?.signal)) facts.push(['PI föreslår', deg(pi.signal), cs === 'active' ? 'skickas' : 'beräknat, skickas ej']);
  if (plan.length) facts.push(['MPC föreslår', deg(plan[0].signal), 'skuggförslag']);
  else if (isDemo() && state.status?.plan?.length) facts.push(['MPC-förslag', deg(state.status.plan[0].signal), 'simulerat']);
  const out = okValue(state.settings.outdoor);
  if (finite(out) && facts.length < 4) facts.push(['Verklig ute', deg(out), '']);
  $('control-facts').replaceChildren(...facts.map(([k, v, note]) => h('div', {}, h('dt', {text: k}), h('dd', {}, v, note ? h('small', {text: note}) : null))));
  $('control-start').hidden = cs === 'active';
  $('control-start').disabled = cs === 'demo' || cs === 'unknown';
  $('control-stop').hidden = !['active', 'pending', 'unknown'].includes(cs);
  const checks = readinessChecks(), done = checks.filter(x => x.ok).length;
  $('readiness').hidden = cs === 'active' || cs === 'demo';
  $('readiness-summary').textContent = done === checks.length ? 'Allt är förberett för aktiv styrning' : `Förberedelser för aktiv styrning · ${done} av ${checks.length} klara`;
  checklist($('readiness-list'), checks);
}

let openChip = null;
function healthItems() {
  const t = state.telemetry, c = state.settings, items = [];
  if (!t) return [{key: 'conn', icon: 'link', label: 'Anslutning', value: 'Ingen kontakt', tone: 'err', detail: ['Kan inte läsa appens mätdata. Kontrollera att appen körs i Home Assistant.']}];
  const rs = readings(), bad = rs.filter(r => r.quality !== 'OK');
  const critical = bad.filter(r => r.roles.some(x => x === 'Reglering' || x === 'Utomhus'));
  items.push({key: 'sensors', icon: 'sensor', label: 'Givare', value: rs.length ? `${rs.length - bad.length} av ${rs.length} OK` : isDemo() ? 'Demo' : 'Inga valda',
    tone: !rs.length ? (isDemo() ? '' : 'warn') : critical.length ? 'err' : bad.length ? 'warn' : 'ok',
    detail: bad.length ? bad.map(r => `${r.name}: ${r.quality}${r.reported_at ? ' · senast ' + ago(r.reported_at) : ''}`) : [rs.length ? 'Alla valda givare rapporterar giltiga värden.' : 'Välj givare under Inställningar.']});
  const f = t.forecast || {}, n = (f.points || []).filter(p => Date.parse(p.datetime) > Date.now() - HOUR).length;
  items.push({key: 'weather', icon: 'cloud', label: 'Väder', value: !c.weather ? 'Ej vald' : n ? `${Math.min(n, 48)} h prognos` : 'Saknas',
    tone: !c.weather ? '' : n >= 22 ? 'ok' : 'warn',
    detail: [f.message || 'Väntar på data.', f.fields?.length ? 'Tillgängliga fält: ' + f.fields.join(', ') + '.' : '', f.fetched_at ? 'Hämtad ' + fullTime(f.fetched_at) + '.' : '']});
  if (c.pv_forecast) {
    const pv = t.pv_forecast || {}, count = (pv.points || []).length;
    items.push({key: 'pv', icon: 'sun', label: 'Solprognos', value: count ? `${count} timvärden` : 'Saknas', tone: count ? 'ok' : 'warn', detail: [pv.message || '']});
  }
  const m = t.mpc || {};
  const modelValue = {ready: 'Redo', collecting: `${m.complete_hours ?? 0} av ${MIN_HOURS} h`, needs_data: 'Behöver mer data', waiting: 'Väntar', off: 'Pausad', error: 'Fel'}[m.state] || '—';
  items.push({key: 'model', icon: 'model', label: 'Husmodell', value: modelValue, progress: m.state === 'collecting' ? (m.complete_hours || 0) / MIN_HOURS : null,
    tone: {ready: 'ok', collecting: 'info', needs_data: 'warn', error: 'err'}[m.state] || '', detail: [m.message || '', ...(m.plan_blockers || [])], link: ['#lararande', 'Mer om modellen']});
  const sync = t.target_sync || {};
  items.push({key: 'sync', icon: 'thermostat', label: 'Termostater', value: {ok: 'Synkade', warning: 'Behöver åtgärd', off: 'Av'}[sync.state] || '—',
    tone: {ok: 'ok', warning: 'warn'}[sync.state] || '', detail: [sync.message || '']});
  const e = t.entities || {};
  items.push({key: 'mqtt', icon: 'link', label: 'HA-sensorer', value: e.ok ? 'Publicerade' : 'Väntar', tone: e.ok ? 'ok' : isDemo() ? '' : 'warn', detail: [e.message || '']});
  items.push({key: 'log', icon: 'log', label: 'Loggning', value: t.error ? 'Fel' : t.logging ? 'Senast ' + clock(t.sampled_at) : 'Av i demo',
    tone: t.error ? 'err' : t.logging ? 'ok' : '',
    detail: [t.error || (t.logging ? `Mätloggning ${t.control?.active ? 'varje minut' : 'var femte minut'}, sparas lokalt i 90 dagar. Tidszon ${state.timeZone}.` : 'Demoläge: verkliga mätvärden loggas inte.')]});
  return items;
}

function renderHealth() {
  const items = healthItems(), strip = $('health');
  strip.replaceChildren(...items.map(item => {
    const chip = h('button', {type: 'button', class: 'chip', role: 'listitem', data: {tone: item.tone}, 'aria-expanded': String(openChip === item.key), 'aria-controls': 'health-detail',
      onclick: () => { openChip = openChip === item.key ? null : item.key; renderHealth(); }},
      h('span', {class: 'badge'}, icon(item.icon)),
      h('span', {}, h('b', {text: item.label}), h('small', {text: item.value}), finite(item.progress) ? h('span', {class: 'progress'}, h('i')) : null));
    const bar = chip.querySelector('.progress i'); if (bar) bar.style.width = Math.min(100, item.progress * 100) + '%';
    return chip;
  }));
  const open = items.find(i => i.key === openChip), detail = $('health-detail');
  detail.hidden = !open;
  if (open) detail.replaceChildren(h('h3', {text: open.label + ' · ' + open.value}), ...open.detail.filter(Boolean).map(text => h('p', {text})),
    open.link ? h('a', {class: 'link', href: open.link[0], text: open.link[1] + ' →'}) : null);
}

function trendSeries(key) { return (state.trends?.points || []).map(p => ({t: Date.parse(p.datetime), v: p[key]})); }

function renderHomeTimeline() {
  const now = Date.now(), c = comfort(), t = state.telemetry, demo = isDemo();
  const start = now - state.back * HOUR, end = now + 24 * HOUR, temp = state.status?.temperature;
  let plan = (demo ? state.status?.plan : t?.mpc?.plan) || [];
  plan = plan.map((p, i) => ({...p, t: p.datetime ? Date.parse(p.datetime) : now + (i + 1) * HOUR}));
  const anchor = finite(temp) ? [{t: now, v: temp}] : [];
  const forecast = (t?.forecast?.points || []).map(p => ({t: Date.parse(p.datetime), v: p.temperature}));
  const targetHistory = trendSeries('target');
  const lanes = [
    {title: 'Inne °C', height: 150, band: [c.comfort_min, c.comfort_max], minRange: 1.5, series: [
      {key: 'indoor', label: 'Inne, uppmätt', color: 'var(--c-indoor)', maxGap: 20 * 60000, points: trendSeries('indoor')},
      {key: 'mpc-indoor', label: demo ? 'Inne, simulerad prognos' : 'Inne, MPC-prognos', color: 'var(--c-indoor)', dash: true, points: plan.length ? [...anchor, ...plan.map(p => ({t: p.t, v: p.indoor}))] : []},
      {key: 'target', label: 'Önskad', color: 'var(--c-target)', width: 1.5, dash: true, maxGap: 30 * HOUR, points: [...targetHistory, {t: now, v: c.target}, {t: end, v: c.target}]}]},
    {title: 'Ute och styrsignal °C', height: 130, series: [
      {key: 'outdoor', label: 'Ute, uppmätt', color: 'var(--c-outdoor)', maxGap: 20 * 60000, points: trendSeries('outdoor')},
      {key: 'outdoor-fc', label: demo ? 'Ute, simulerad' : 'Ute, prognos', color: 'var(--c-outdoor)', dash: true, points: demo ? plan.map(p => ({t: p.t, v: p.outdoor})) : forecast},
      {key: 'applied', label: 'Ohmigo inställt', color: 'var(--c-applied)', maxGap: 20 * 60000, points: trendSeries('applied')},
      {key: 'pump', label: 'Pumpen läser', color: 'var(--c-pump)', hidden: true, maxGap: 20 * 60000, points: trendSeries('pump_outdoor')},
      {key: 'pi', label: 'PI-förslag', color: 'var(--c-pi)', dash: true, hidden: true, maxGap: 20 * 60000, points: trendSeries('proposal')},
      {key: 'mpc', label: demo ? 'Simulerad signal' : 'MPC-förslag', color: 'var(--c-mpc)', dash: true, points: plan.map(p => ({t: p.t, v: p.signal}))}]}];
  const pvForecast = (t?.pv_forecast?.points || []).map(p => ({t: Date.parse(p.datetime), v: p.pv_power}));
  const pvMeasured = trendSeries('pv_power');
  if (pvForecast.length || pvMeasured.some(p => finite(p.v))) {
    lanes.push({title: 'Solceller', height: 64, min: 0, minRange: 500, format: watts, tick: v => v >= 1000 ? num(v / 1000, 0) + 'k' : num(v, 0), series: [
      {key: 'pv', label: 'Sol, uppmätt', color: 'var(--c-sun)', area: true, maxGap: 30 * 60000, points: pvMeasured},
      {key: 'pv-fc', label: 'Sol, prognos', color: 'var(--c-sun)', bars: true, opacity: .45, points: pvForecast.filter(p => p.t > now - HOUR / 2)}]});
  } else {
    const clouds = (t?.forecast?.points || []).map(p => ({t: Date.parse(p.datetime), v: p.cloud_coverage}));
    lanes.push({title: 'Moln %', height: 56, min: 0, max: 100, format: v => num(v, 0) + ' %', tick: v => num(v, 0), series: [
      {key: 'cloud', label: 'Moln, prognos', color: 'var(--c-cloud)', bars: true, opacity: .5, points: clouds}]});
  }
  $('timeline-sub').textContent = demo ? 'Demo: simulerad prognos för ett exempelhus. Historik loggas i skuggläge.' :
    plan.length ? 'Uppmätt historik till vänster, prognos till höger. Streckat = prognos eller förslag som inte skickas.' :
    'Uppmätt historik till vänster, väderprognos till höger. Inneprognos visas när husmodellen är redo.';
  const host = $('timeline');
  if (!host._hidden) host._hidden = new Set(lanes.flatMap(l => l.series.filter(se => se.hidden).map(se => se.key)));
  timeline(host, {start, end, now, lanes, label: 'Tidslinje för inne, ute och styrning', empty: 'Ingen historik eller prognos ännu. Historiken byggs upp i skuggläge.'});
}

function renderChain() {
  const c = state.settings, demo = isDemo(), plan0 = state.status?.plan?.[0];
  const out = demo ? plan0?.outdoor : okValue(c.outdoor), ohmigo = demo ? plan0?.signal : okValue(c.applied_signal);
  const pump = okValue(c.pump_outdoor), supply = okValue(c.supply), ret = okValue(c.return), inside = state.status?.temperature;
  const node = (k, ic, value, label, extra) => h('div', {class: 'node', data: {k}}, h('div', {class: 'bubble'}, icon(ic)), h('b', {text: value}), h('small', {text: label}), extra ? h('small', {text: extra}) : null);
  const host = $('chain');
  host.replaceChildren(
    node('out', 'outdoor', degShort(out), demo ? 'Ute (simulerat)' : 'Verklig ute'),
    node('ohmigo', 'ohmigo', degShort(ohmigo), demo ? 'Signal (simulerad)' : 'Ohmigo säger'),
    node('pump', 'pump', finite(supply) ? degShort(supply, 0) : degShort(pump), finite(supply) ? 'Framledning' : 'Pumpen läser',
      [finite(ret) ? 'retur ' + degShort(ret, 0) : '', finite(supply) && finite(pump) ? 'läser ' + degShort(pump) : ''].filter(Boolean).join(' · ')),
    node('house', 'house', degShort(inside), 'Inne'));
  host.parentElement.querySelector('.chain-note')?.remove();
  if (finite(out) && finite(ohmigo)) {
    const diff = ohmigo - out, tone = diff < -.25 ? 'more' : diff > .25 ? 'less' : 'even';
    const text = tone === 'more' ? ['Ohmigo visar ', num(-diff) + ' °C kallare', ' än verkligt → värmepumpen ger mer värme.']
      : tone === 'less' ? ['Ohmigo visar ', num(diff) + ' °C varmare', ' än verkligt → värmepumpen ger mindre värme.']
      : ['Ohmigo följer verklig utetemperatur – ', 'ingen kompensation', ' just nu.'];
    host.after(h('p', {class: 'chain-note', data: {tone}}, text[0], h('strong', {text: text[1]}), text[2]));
  }
}

function renderPI() {
  const pi = state.telemetry?.pi, host = $('pi-math'), c = state.settings;
  if (!pi || !finite(pi.signal)) { host.replaceChildren(h('p', {text: pi?.message || 'PI väntar på giltiga mätdata. Inget förslag skickas.'})); return; }
  const term = (value, label, cls = '') => h('span', {class: 'term ' + cls}, h('b', {text: value}), h('small', {text: label}));
  const op = text => h('span', {class: 'op', 'aria-hidden': 'true', text});
  const out = okValue(c.outdoor), signed = v => (v > 0 ? '+' : v < 0 ? '−' : '') + num(Math.abs(v), 2);
  host.replaceChildren(
    h('p', {class: 'sub', text: '1. Hur långt ifrån önskad temperatur är huset?'}),
    h('div', {class: 'equation'}, term(degShort(c.target), 'önskad'), op('−'), term(degShort(c.target - pi.error, 2), 'inne'), op('='), term(signed(pi.error) + '°', 'fel', 'result')),
    h('p', {class: 'sub', text: '2. Felet blir en kompensation som dras från verklig utetemperatur.'}),
    h('div', {class: 'equation'}, term(degShort(out), 'verklig ute'), op('−'), term(num(pi.compensation, 2) + '°', `P ${num(pi.p, 2)} + I ${num(pi.i, 2)}`), op('='), term(degShort(pi.signal), 'förslag', 'result')),
    h('div', {class: 'flags'}, pi.limited ? h('span', {class: 'pill', data: {tone: 'warn'}, text: 'Begränsad av signalgränser'}) : null,
      pi.integrator_frozen ? h('span', {class: 'pill', data: {tone: 'warn'}, text: 'I-delen pausad av begränsning'}) : null),
    h('p', {class: 'fineprint', text: `${pi.message} Kp ${num(c.pi_kp, 1)}, Ki ${num(c.pi_ki, 2)}. Positivt fel ger mer värme genom lägre simulerad utetemperatur.`}));
}

function renderRooms() {
  const host = $('rooms'), rs = readings().filter(r => r.roles.includes('Reglering') || r.roles.includes('Uppföljning'))
    .sort((a, b) => b.roles.includes('Reglering') - a.roles.includes('Reglering') || a.name.localeCompare(b.name, 'sv'));
  if (!rs.length) {
    host.replaceChildren(h('p', {class: 'empty-note', text: isDemo() ? 'I demoläget visas inga riktiga rum. Välj dina rum under Inställningar.' : 'Inga rum valda ännu.'}));
    return;
  }
  host.replaceChildren(...rs.map(r => {
    const control = r.roles.includes('Reglering'), ok = r.quality === 'OK';
    return h('div', {class: 'room', data: {tone: ok ? (control ? toneFor(r.value) : 'none') : 'err', role: control ? 'control' : 'observe'}, title: r.entity},
      h('span', {class: 'tag', text: control ? 'Styr' : 'Följs'}), h('span', {class: 'name', text: r.name}),
      h('b', {text: deg(r.value)}), ok ? h('small', {text: ago(r.reported_at)}) : h('small', {class: 'problem', text: r.quality}));
  }));
}

/* Quick comfort adjust: shifts the whole band, saves after a pause. Comfort changes never stop PI. */
let pendingComfort = null, commitTimer = null;
function adjustTarget(delta) {
  const base = comfort(), next = {target: +(base.target + delta).toFixed(1), comfort_min: +(base.comfort_min + delta).toFixed(1), comfort_max: +(base.comfort_max + delta).toFixed(1)};
  if (next.comfort_min < 7 || next.comfort_max > 35) { toast('Komfortområdet måste ligga inom 7–35 °C.', {tone: 'err'}); return; }
  pendingComfort = next; renderHero(); renderHomeTimeline();
  clearTimeout(commitTimer); commitTimer = setTimeout(commitComfort, 1300);
}
async function commitComfort() {
  const wanted = pendingComfort; if (!wanted) return;
  try {
    const fresh = await api('config');
    const saved = await api('config', {...fresh, ...wanted});
    const previous = {target: state.settings.target, comfort_min: state.settings.comfort_min, comfort_max: state.settings.comfort_max};
    state.settings = saved; syncDraftComfort();
    if (pendingComfort === wanted) pendingComfort = null;
    toast(`Önskad temperatur ${deg(saved.target)} sparad. Gäller direkt${controlState() === 'active' ? ' – aktiv styrning fortsätter' : ''}.`,
      {action: {label: 'Ångra', run: () => { pendingComfort = previous; commitComfort(); }}});
    refresh();
  } catch (e) { pendingComfort = null; toast('Kunde inte spara: ' + e.message, {tone: 'err'}); }
  renderHero();
}

/* Start / stop */
function openStartDialog() {
  const c = state.settings, checks = readinessChecks();
  $('start-facts').replaceChildren(
    h('li', {text: `Appen skriver till ${entityName(c.applied_signal) || 'vald utgång'} (${c.applied_signal || '—'}).`}),
    h('li', {text: `Kommandon i steg om 0,5 °C, högst var femte minut, inom ${num(c.signal_min)}–${num(c.signal_max)} °C och högst ${num(c.pi_rate)} °C ändring per timme.`}),
    h('li', {text: c.auto_restart ? 'Styrningen återupptas automatiskt efter omstart när givare och utgång är redo.' : 'Efter en omstart måste du starta styrningen igen här.'}));
  checklist($('start-checks'), checks);
  $('start-confirm').disabled = !checks.every(x => x.ok);
  $('start-blocked').hidden = checks.every(x => x.ok);
  $('start-dialog').showModal();
}
$('start-dialog').addEventListener('close', async () => {
  if ($('start-dialog').returnValue !== 'confirm') return;
  $('start-dialog').returnValue = '';
  try { await api('control/start', {confirm: true}); toast('Aktiv styrning startad.'); }
  catch (e) { toast(e.message, {tone: 'err'}); }
  refresh();
});
async function stopControl() {
  $('control-stop').disabled = true;
  try { await api('control/stop', {}); toast('Styrningen är stoppad. Inga fler kommandon skickas – Ohmigos watchdog tar över.'); }
  catch (e) { toast('Stopp kunde inte bekräftas. Stoppa appen i Home Assistant. ' + e.message, {tone: 'err', timeout: 15000}); }
  finally { $('control-stop').disabled = false; refresh(); }
}

/* WEATHER ================================================================== */
function renderWeather() {
  const t = state.telemetry, c = state.settings, now = Date.now();
  const f = t?.forecast || {}, pv = t?.pv_forecast || {};
  const points = (f.points || []).filter(p => Date.parse(p.datetime) > now - HOUR && Date.parse(p.datetime) <= now + 24 * HOUR);
  const pvAt = new Map((pv.points || []).map(p => [Math.floor(Date.parse(p.datetime) / HOUR), p.pv_power]));
  $('weather-status').replaceChildren(
    h('span', {class: 'pill', data: {tone: !c.weather ? '' : points.length ? 'ok' : 'warn'}, text: !c.weather ? 'Ingen väderentitet vald' : (f.message || 'Väntar på prognos') + (f.fetched_at ? ' · hämtad ' + clock(f.fetched_at) : '')}),
    c.pv_forecast ? h('span', {class: 'pill', data: {tone: (pv.points || []).length ? 'ok' : 'warn'}, text: pv.message || 'Solcellsprognos väntar'}) : null);
  const strip = $('hour-strip');
  strip.hidden = !points.length;
  strip.replaceChildren(...points.map((p, i) => {
    const time = Date.parse(p.datetime), hour = zoneHour(time), night = hour < 7 || hour > 19, cloudy = p.cloud_coverage >= 60;
    const power = p.pv_power ?? pvAt.get(Math.floor(time / HOUR));
    const current = Math.abs(time - now) < HOUR / 2;
    return h('div', {class: 'hour' + (current ? ' now' : '')}, h('time', {text: current ? 'Nu' : clock(time)}),
      icon(cloudy ? 'cloud' : night ? 'moon' : 'sun', cloudy || night ? 'cloudy' : ''), h('b', {text: degShort(p.temperature)}),
      finite(p.cloud_coverage) ? h('small', {text: num(p.cloud_coverage, 0) + ' % moln'}) : null,
      finite(power) && power > 0 ? h('small', {text: watts(power)}) : null);
  }));
  const fc = (f.points || []).map(p => ({t: Date.parse(p.datetime), ...p}));
  const lanes = [{title: 'Ute °C', height: 140, series: [{key: 'temp', label: 'Utetemperatur', color: 'var(--c-outdoor)', points: fc.map(p => ({t: p.t, v: p.temperature}))}]},
    {title: 'Moln %', height: 70, min: 0, max: 100, format: v => num(v, 0) + ' %', tick: v => num(v, 0), series: [{key: 'cloud', label: 'Molntäckning', color: 'var(--c-cloud)', bars: true, opacity: .55, points: fc.map(p => ({t: p.t, v: p.cloud_coverage}))}]},
    {title: 'Solinstrålning W/m²', height: 70, min: 0, format: v => num(v, 0) + ' W/m²', tick: v => num(v, 0), series: [{key: 'irr', label: 'Solinstrålning', color: 'var(--c-sun)', area: true, points: fc.map(p => ({t: p.t, v: p.solar_irradiance}))}]}];
  timeline($('weather-chart'), {start: now - HOUR, end: now + 24 * HOUR, now, lanes, label: 'Väderprognos kommande dygn',
    empty: !c.weather ? 'Välj en väderentitet med timprognos under Inställningar → Väder & sol.' : f.message || 'Ingen giltig timprognos.'});
  const actual = reading(c.pv_power);
  $('pv-now').replaceChildren(...(!c.pv_power ? ['Ingen produktionssensor vald. ', h('a', {class: 'link', href: '#installningar/vader', text: 'Välj under Väder & sol'})]
    : actual?.quality === 'OK' ? ['Just nu ', h('strong', {text: watts(actual.value)}), ` · ${clock(actual.reported_at)}`]
    : ['Uppmätt produktion saknas: ' + (actual?.quality || 'givaren har inte lämnat något värde') + '.']));
  timeline($('pv-chart'), {start: now - 24 * HOUR, end: now + 24 * HOUR, now, label: 'Solcellsproduktion', lanes: [{title: 'Solceller', height: 120, min: 0, minRange: 500, format: watts,
    tick: v => v >= 1000 ? num(v / 1000, 1) + 'k' : num(v, 0), series: [
      {key: 'pv', label: 'Uppmätt', color: 'var(--c-sun)', area: true, maxGap: 30 * 60000, points: trendSeries('pv_power')},
      {key: 'pv-fc', label: 'Prognos', color: 'var(--c-sun)', bars: true, opacity: .45, points: (pv.points || []).map(p => ({t: Date.parse(p.datetime), v: p.pv_power}))}]}],
    empty: !c.pv_forecast ? 'Välj växelriktare och Forecast.Solar-källa för att se produktion och prognos.' : pv.fetched_at ? 'Prognosen saknar timvärden för kommande dygn.' : pv.message || 'Ingen solcellsdata ännu.'});
  tableRows('weather-rows', points.map(p => [dayTime(p.datetime), deg(p.temperature), finite(p.cloud_coverage) ? num(p.cloud_coverage, 0) + ' %' : '—',
    finite(p.solar_irradiance) ? num(p.solar_irradiance, 0) + ' W/m²' : '—', watts(p.pv_power ?? pvAt.get(Math.floor(Date.parse(p.datetime) / HOUR)))]));
}

function tableRows(id, rows) { $(id).replaceChildren(...rows.map(row => h('tr', {}, row.map(v => h('td', {text: v ?? '—'}))))); }

/* LEARNING ================================================================= */
function renderLearning() {
  const m = state.telemetry?.mpc || {}, demo = isDemo(), plan = m.plan || [];
  const hours = m.complete_hours || 0, remaining = Math.max(0, MIN_HOURS - hours);
  let steps;
  if (m.state === 'ready') steps = [['done', `${hours} kompletta timmar`], ['done', (m.report?.selected_model === 'lag6' ? 'Fördröjningsmodell' : 'Enkel modell') + ' godkänd' + (m.trained_at ? ' · ' + fullTime(m.trained_at) : '')],
    plan.length ? ['done', 'Förslag visas på Hem'] : ['failed', 'Väntar: ' + ((m.plan_blockers || [])[0] || 'se orsak nedan')]];
  else if (m.state === 'needs_data') steps = [['done', `${hours} kompletta timmar`], ['failed', 'Klarade inte kraven ännu – fortsätter samla'], ['', 'Visas när modellen godkänts']];
  else if (m.state === 'collecting') steps = [['current', `${hours} av ${MIN_HOURS} timmar · ca ${Math.ceil(remaining / 24)} dygn kvar`], ['', 'Medelfel högst 0,35 °C vid 6, 12 och 24 h'], ['', 'Visas när modellen godkänts']];
  else if (m.state === 'error') steps = [['failed', 'Mätloggen kunde inte läsas'], ['', ''], ['', '']];
  else steps = [['', demo ? 'Pausad i demoläge – startar i skuggläge' : 'Väntar på första kontrollen'], ['', 'Medelfel högst 0,35 °C vid 6, 12 och 24 h'], ['', 'Visas när modellen godkänts']];
  $('journey').replaceChildren(...['Samlar data', 'Kvalitetskontroll', 'Skuggförslag'].map((title, i) => {
    const li = h('li', {data: {s: steps[i][0]}, 'aria-current': steps[i][0] === 'current' ? 'step' : null}, h('b', {text: title}), h('small', {text: steps[i][1]}));
    if (i === 0 && m.state === 'collecting') { const bar = h('span', {class: 'progress'}, h('i')); bar.firstChild.style.width = Math.min(100, hours / MIN_HOURS * 100) + '%'; li.append(bar); }
    return li;
  }));
  const extra = [m.report?.recovered_pv_zero_samples ? `Återanvända historiska nollvärden för solceller: ${m.report.recovered_pv_zero_samples}.` : ''];
  $('model-message').textContent = [m.message || 'Väntar på automatisk modellstatus.', ...extra].filter(Boolean).join(' ');
  const labels = {temperature: 'Temperaturmodell', pv: 'Solcellsmodell', cloud: 'Molnmodell'};
  const reasons = m.state === 'ready' ? [] : Object.entries(m.quality_reasons || {}).flatMap(([kind, models]) => Object.values(models || {}).flat().map(r => (labels[kind] || kind) + ': ' + r));
  $('model-reasons').replaceChildren(...[...reasons, ...(m.plan_blockers || [])].map(text => h('li', {text})));
  renderAccuracy($('accuracy'), m.report?.metrics || [], m.report?.selected_model, 'Visas när modellen har tränats – det kräver minst 250 kompletta timmar i skuggläge.');
}

function renderAccuracy(host, metrics, selected, empty) {
  if (!metrics.length) { host.replaceChildren(h('p', {class: 'empty-note', text: empty})); return; }
  const LIMIT = .35, values = metrics.flatMap(m => [m.mae, m.lag_mae, m.baseline_mae]).filter(finite);
  const scale = Math.max(LIMIT * 1.25, ...values) * 1.08;
  const rows = metrics.map(m => {
    const bars = [['mae', 'Enkel modell'], ['lag_mae', 'Med fördröjning'], ['baseline_mae', 'Oförändrad temp.']].filter(([k]) => finite(m[k])).map(([k, label]) => {
      const checked = k !== 'baseline_mae' && [6, 12, 24].includes(m.hours);
      const pass = checked ? m[k] <= LIMIT && m[k] <= m.baseline_mae : null;
      const chosen = (selected === 'lag6' && k === 'lag_mae') || (selected === 'linear' && k === 'mae');
      const fill = h('span', {class: 'fill'}), limit = checked ? h('span', {class: 'limit', title: 'Krav 0,35 °C'}) : null;
      fill.style.width = (m[k] / scale * 100) + '%'; if (limit) limit.style.left = (LIMIT / scale * 100) + '%';
      return h('div', {class: 'bar', data: {k, pass: pass === null ? '' : String(pass)}}, h('span', {text: label + (chosen ? ' · vald' : '')}), h('span', {class: 'track'}, fill, limit), h('span', {class: 'val', text: num(m[k], 2)}));
    });
    return h('div', {class: 'acc-row'}, h('b', {}, `${m.hours} h framåt`, h('small', {text: `${m.windows} testfönster`})), h('div', {class: 'bars'}, bars));
  });
  host.replaceChildren(h('div', {class: 'acc'}, rows));
}

async function shadowEvaluate() {
  const button = $('shadow-evaluate'); button.disabled = true; $('shadow-status').textContent = 'Hämtar…';
  try {
    const result = await api('mpc/evaluation'), latest = result.proposals?.[0];
    $('shadow-status').textContent = latest ? `Beslut ${fullTime(latest.time)} · väder från ${latest.weather_entity}, hämtat ${clock(latest.forecast_fetched_at)}. ${result.message}` : result.message;
    const stat = (label, value) => h('div', {class: 'stat'}, h('small', {text: label}), h('b', {text: value}));
    $('shadow-stats').replaceChildren(...(latest ? [stat('Fel i inneprognos', finite(latest.indoor_mae) ? num(latest.indoor_mae, 2) + ' °C' : '—'),
      stat('Fel i väderprognos', finite(latest.outdoor_mae) ? num(latest.outdoor_mae, 2) + ' °C' : '—'), stat('Fel i solcellsprognos', finite(latest.pv_power_mae) ? watts(latest.pv_power_mae) : '—')] : []));
    const pts = (latest?.points || []).map(p => ({...p, t: Date.parse(latest.time) + p.hours * HOUR}));
    if (pts.length) timeline($('shadow-chart'), {start: Date.parse(latest.time), end: pts.at(-1).t, now: Infinity, label: 'Prognos mot utfall', lanes: [
      {title: 'Inne °C', height: 130, minRange: 1, series: [{key: 'pred', label: 'Inne, prognos', color: 'var(--c-indoor)', dash: true, points: pts.map(p => ({t: p.t, v: p.predicted_indoor}))},
        {key: 'act', label: 'Inne, uppmätt', color: 'var(--c-indoor)', points: pts.map(p => ({t: p.t, v: p.actual_indoor}))}]},
      {title: 'Ute °C', height: 100, series: [{key: 'fo', label: 'Ute, prognos', color: 'var(--c-outdoor)', dash: true, points: pts.map(p => ({t: p.t, v: p.forecast_outdoor}))},
        {key: 'ao', label: 'Ute, uppmätt', color: 'var(--c-outdoor)', points: pts.map(p => ({t: p.t, v: p.actual_outdoor}))},
        {key: 'sig', label: 'Föreslagen signal', color: 'var(--c-mpc)', dash: true, points: pts.map(p => ({t: p.t, v: p.proposed_signal}))}]}]});
    else $('shadow-chart').replaceChildren();
    $('shadow-table-fold').hidden = !pts.length;
    tableRows('shadow-rows', pts.map(p => [p.hours + ' h', deg(p.forecast_outdoor), deg(p.actual_outdoor), watts(p.forecast_pv_power), watts(p.actual_pv_power), deg(p.predicted_indoor), deg(p.actual_indoor), deg(p.proposed_signal)]));
  } catch (e) { $('shadow-status').textContent = e.message; $('shadow-stats').replaceChildren(); }
  finally { button.disabled = false; }
}

async function exportHistory() {
  const button = $('export-model-history'), status = $('export-status');
  button.disabled = true; status.textContent = 'Förbereder export…';
  try {
    const response = await fetch('api/model/export');
    if (!response.ok) throw Error('Kunde inte exportera modellhistoriken. Försök igen.');
    const url = URL.createObjectURL(await response.blob()), link = h('a', {href: url, download: 'mpc-model-history.zip'});
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    status.textContent = 'Exporten har hämtats.';
  } catch (e) { status.textContent = e.message; } finally { button.disabled = false; }
}

/* CSV import (offline evaluation) */
let historyCsv = null, importIndoor = new Set(), importEntities = [];
function renderImportPicker() {
  const host = $('model-indoor-picker');
  host.replaceChildren(h('div', {class: 'picker multi'}, h('div', {class: 'picker-panel'}, h('div', {class: 'picker-list'},
    importEntities.map(e => h('label', {class: 'picker-option'}, h('input', {type: 'checkbox', checked: importIndoor.has(e.entity),
      onchange: ev => { ev.target.checked ? importIndoor.add(e.entity) : importIndoor.delete(e.entity); }}), h('span', {class: 'ent'}, h('b', {text: e.entity}), h('small', {text: `${e.rows} rader`}))))))));
}
function prepareModel(report) {
  importEntities = report.entities; importIndoor = new Set();
  for (const id of ['model-outdoor', 'model-signal']) $(id).replaceChildren(new Option('Välj signal', ''), ...report.entities.map(e => new Option(e.entity, e.entity)));
  renderImportPicker();
  const valid = report.entities.filter(e => e.start && e.end);
  if (valid.length) { $('model-start').value = valid.map(e => e.start.slice(0, 10)).sort().at(-1); $('model-end').value = valid.map(e => e.end.slice(0, 10)).sort()[0]; }
  $('model-panel').hidden = false;
  $('model-status').textContent = 'Välj en period under värmesäsongen och de signaler som ska ingå. Framledning och retur krävs inte.';
  $('model-accuracy').replaceChildren();
}
function renderModel(result) {
  renderAccuracy($('model-accuracy'), result.metrics, null, '');
  $('model-status').textContent = `${result.complete_hours} kompletta timmar. Validering från ${fullTime(result.split_at)}. ${result.incomplete_hours} ofullständiga timmar uteslöts. ${result.comparison || ''} ${result.message} ${result.aggregation}`;
}
async function inspectFile() {
  const file = $('file').files[0], status = $('history-status');
  if (!file) { status.textContent = 'Välj en CSV-fil först.'; return; }
  if (file.size > 20000000) { status.textContent = 'Filen får vara högst 20 MB.'; return; }
  $('inspect').disabled = true; status.textContent = 'Granskar…';
  try {
    historyCsv = null; $('model-panel').hidden = true; $('model-saved').textContent = 'Nytt underlag sparas först när utvärderingen lyckas.';
    const csv = await file.text(), report = await api('history', csv, true);
    historyCsv = csv; prepareModel(report);
    $('history-table').querySelector('tbody').replaceChildren(...report.entities.map(e => h('tr', {}, [e.entity, e.rows, e.start ? fullTime(e.start) : '—', e.end ? fullTime(e.end) : '—', e.invalid,
      e.max_gap_hours === null ? '—' : num(e.max_gap_hours, 1) + ' h'].map(v => h('td', {text: v ?? '—'})))));
    $('history-table').hidden = false; status.textContent = report.message;
  } catch (e) { status.textContent = e.message; } finally { $('inspect').disabled = false; }
}
async function evaluateModel() {
  if (!historyCsv) return;
  $('evaluate-model').disabled = true; $('model-status').textContent = 'Anpassar modellen och testar den på senare historik…';
  try {
    const result = await api('model/evaluate', {csv: historyCsv, mapping: {indoor: [...importIndoor], outdoor: $('model-outdoor').value, signal: $('model-signal').value, start: $('model-start').value, end: $('model-end').value}});
    renderModel(result); $('model-saved').textContent = 'Underlag och resultat sparade i appen. Återställs vid nästa öppning.';
  } catch (e) { $('model-status').textContent = e.message; } finally { $('evaluate-model').disabled = false; }
}
async function restoreModel() {
  try {
    const saved = await api('model/saved'); if (!saved) return;
    historyCsv = saved.csv; prepareModel(await api('history', saved.csv, true));
    const m = saved.report.mapping;
    importIndoor = new Set(m.indoor); renderImportPicker();
    $('model-outdoor').value = m.outdoor; $('model-signal').value = m.signal; $('model-start').value = m.start; $('model-end').value = m.end;
    renderModel(saved.report);
    $('model-saved').textContent = 'Sparat underlag återställt från ' + fullTime(saved.saved_at) + '. Du kan ändra perioden och köra igen.';
    $('history-status').textContent = 'Tidigare CSV-underlag har återställts från appens lagring.';
  } catch (e) { $('history-status').textContent = 'Kunde inte återställa sparat modellunderlag: ' + e.message; }
}

/* SETTINGS ================================================================= */
let draft = null, outdoorChoice = 'restart', indoorChoice = 'keep';
const unit = e => e.attributes?.unit_of_measurement;
const isSensor = (e, units) => e.entity_id.startsWith('sensor.') && units.includes(unit(e));
const PICKERS = {
  indoor: {multi: true, filter: e => isSensor(e, ['°C']), search: 'Sök rum eller givare'},
  observe: {multi: true, filter: e => isSensor(e, ['°C']), search: 'Sök rum eller givare'},
  target_climates: {multi: true, filter: e => e.entity_id.startsWith('climate.'), search: 'Sök termostat'},
  outdoor: {filter: e => isSensor(e, ['°C'])}, supply: {filter: e => isSensor(e, ['°C'])}, return: {filter: e => isSensor(e, ['°C'])},
  pump_outdoor: {filter: e => isSensor(e, ['°C'])},
  applied_signal: {filter: e => (e.entity_id.startsWith('number.') || e.entity_id.startsWith('sensor.')) && unit(e) === '°C', sort: e => e.entity_id.startsWith('number.') ? 0 : 1},
  weather: {filter: e => e.entity_id.startsWith('weather.')},
  solar: {filter: e => isSensor(e, ['W/m²'])}, cloud: {filter: e => isSensor(e, ['%'])}, pv_power: {filter: e => isSensor(e, ['W', 'kW'])},
  old_automation: {filter: e => e.entity_id.startsWith('automation.'), search: 'Sök automation'},
  pv_forecast: {sources: true}
};
function entityValue(e) {
  if (!e) return '';
  if (e.missing) return 'saknas i HA';
  if (e.entity_id.startsWith('automation.')) return e.state === 'on' ? 'på' : e.state === 'off' ? 'av' : e.state;
  if (e.entity_id.startsWith('climate.') || e.entity_id.startsWith('weather.')) return finite(Number(e.attributes?.temperature)) && e.attributes?.temperature !== null ? num(Number(e.attributes.temperature)) + ' °C' : e.state;
  const n = Number(e.state);
  return Number.isFinite(n) && e.state !== '' ? num(n, Math.abs(n) >= 100 ? 0 : 1) + ' ' + (unit(e) || '') : e.state;
}
function pickerOptions(key) {
  const config = PICKERS[key], selected = [].concat(draft[key]).filter(Boolean);
  let options = config.sources ? state.pvSources.map(src => ({entity_id: src.id, state: src.has_forecast ? 'prognos finns' : src.linked ? 'saknar timprognos' : 'ej kopplad i Energipanelen', attributes: {friendly_name: src.label}}))
    : state.entities.filter(config.filter);
  for (const id of selected) if (!options.some(o => o.entity_id === id)) options.push({entity_id: id, missing: true, attributes: {friendly_name: id}});
  return options.sort((a, b) => (config.sort ? config.sort(a) - config.sort(b) : 0) || (a.attributes?.friendly_name || a.entity_id).localeCompare(b.attributes?.friendly_name || b.entity_id, 'sv'));
}
const entBlock = e => h('span', {class: 'ent'}, h('b', {text: e.attributes?.friendly_name || e.entity_id}), h('small', {text: e.entity_id}));
const valueBlock = e => h('span', {class: 'ent-value' + (e.missing ? ' missing' : ''), text: entityValue(e)});

function renderPicker(host) {
  const key = host.dataset.picker, config = PICKERS[key], options = pickerOptions(key), open = host._open, query = host._query || '';
  const matches = options.filter(o => !query || (o.attributes?.friendly_name + ' ' + o.entity_id).toLocaleLowerCase('sv').includes(query.toLocaleLowerCase('sv')));
  const search = h('input', {type: 'search', placeholder: config.search || 'Sök entitet', value: query, 'aria-label': 'Sök', oninput: ev => {
    host._query = ev.target.value; const pos = ev.target.selectionStart; renderPicker(host); const input = host.querySelector('input[type=search]'); input.focus(); input.setSelectionRange(pos, pos); }});
  const none = h('p', {class: 'picker-none', text: options.length ? 'Ingen träff.' : config.sources ? 'Inga Forecast.Solar-källor hittades.' : 'Inga passande entiteter hittades i Home Assistant.'});
  if (config.multi) {
    const chosen = draft[key];
    host.replaceChildren(h('div', {class: 'picker multi'},
      h('div', {class: 'picker-chips'}, chosen.map(id => h('button', {type: 'button', 'aria-label': 'Ta bort ' + entityName(id), text: entityName(id),
        onclick: () => { draft[key] = draft[key].filter(x => x !== id); renderPicker(host); changed(); }}))),
      h('div', {class: 'picker-panel'}, options.length > 6 ? search : null,
        h('div', {class: 'picker-list'}, matches.length ? matches.map(o => h('label', {class: 'picker-option'},
          h('input', {type: 'checkbox', checked: chosen.includes(o.entity_id), onchange: ev => {
            draft[key] = ev.target.checked ? [...draft[key], o.entity_id] : draft[key].filter(x => x !== o.entity_id); renderPicker(host); changed(); }}),
          entBlock(o), valueBlock(o))) : none),
        h('p', {class: 'picker-count', text: `${chosen.length} valda`}))));
    return;
  }
  const current = options.find(o => o.entity_id === draft[key]);
  const toggle = h('button', {type: 'button', class: 'picker-current' + (current ? '' : ' empty'), 'aria-expanded': String(!!open), onclick: () => { host._open = !open; host._query = ''; renderPicker(host); if (host._open) host.querySelector('input[type=search]')?.focus(); }},
    current ? entBlock(current) : h('span', {class: 'ent'}, h('b', {text: 'Inte vald'})), current ? valueBlock(current) : null, h('span', {class: 'chev', text: open ? 'Stäng' : current ? 'Ändra' : 'Välj'}));
  const choose = id => { draft[key] = id; host._open = false; renderPicker(host); changed(); host.querySelector('.picker-current').focus(); };
  host.replaceChildren(h('div', {class: 'picker'}, toggle, open ? h('div', {class: 'picker-panel'}, search,
    h('div', {class: 'picker-list', role: 'radiogroup'},
      h('button', {type: 'button', class: 'picker-option', role: 'radio', 'aria-checked': String(!draft[key]), onclick: () => choose('')}, h('span', {class: 'ent'}, h('b', {text: 'Ingen'}), h('small', {text: 'Välj senare'}))),
      matches.length ? matches.map(o => h('button', {type: 'button', class: 'picker-option', role: 'radio', 'aria-checked': String(o.entity_id === draft[key]), onclick: () => choose(o.entity_id)}, entBlock(o), valueBlock(o))) : none)) : null));
}

const FIELD_LABELS = {mode: 'läge', target: 'önskad temperatur', comfort_min: 'lägsta komfort', comfort_max: 'högsta komfort', target_climates: 'termostater',
  indoor: 'styrande rum', observe: 'uppföljda rum', outdoor: 'utegivare', supply: 'framledning', return: 'retur', applied_signal: 'Ohmigo-utgång',
  pump_outdoor: 'pumpens utetemperatur', weather: 'väder', solar: 'solinstrålning', cloud: 'molnighet', pv_power: 'solcellsproduktion', pv_forecast: 'solprognos',
  old_automation: 'gammal automation', watchdog_seconds: 'watchdog-timeout', watchdog_verified: 'watchdog verifierad', exclusive_writer_confirmed: 'ensam skrivare',
  auto_restart: 'automatisk återstart', pi_kp: 'Kp', pi_ki: 'Ki', pi_limit: 'max kompensation', pi_rate: 'max signaländring', signal_min: 'lägsta signal',
  signal_max: 'högsta signal', max_step: 'största ändring'};
const NUMBER_FIELDS = ['target', 'comfort_min', 'comfort_max', 'signal_min', 'signal_max', 'max_step', 'pi_kp', 'pi_ki', 'pi_limit', 'pi_rate', 'watchdog_seconds'];
const CHECK_FIELDS = ['watchdog_verified', 'exclusive_writer_confirmed', 'auto_restart'];
function fillForm() {
  for (const k of NUMBER_FIELDS) $(k).value = draft[k];
  for (const k of CHECK_FIELDS) $(k).checked = draft[k];
  for (const r of document.querySelectorAll('input[name=mode]')) r.checked = r.value === draft.mode;
  document.querySelectorAll('[data-picker]').forEach(renderPicker);
  $('ha-connection').textContent = state.connectionMessage;
  $('pv-source-status').textContent = state.pvMessage || '';
  changed();
}
function outdoorMigration() { return draft.mode === 'shadow' && !!state.settings.outdoor && !!draft.outdoor && draft.outdoor !== state.settings.outdoor; }
function renderOutdoorHistory() {
  const box = $('outdoor-history'), show = outdoorMigration();
  box.hidden = !show;
  if (!show) { outdoorChoice = 'restart'; return; }
  const option = (value, title, text) => h('label', {class: 'choice'},
    h('input', {type: 'radio', name: 'outdoor-history', value, checked: outdoorChoice === value, onchange: () => { outdoorChoice = value; changed(); }}),
    h('span', {}, h('strong', {text: title}), h('small', {text})));
  box.replaceChildren(h('strong', {text: 'Vad ska hända med historiken?'}),
    option('restart', 'Börja om insamlingen', `Husmodellen samlar en ny period på minst ${MIN_HOURS} timmar med den nya givaren. Bäst om givarna mäter olika, t.ex. olika placering i sol.`),
    option('migrate', 'Flytta historiken till den nya givaren', `Tidigare värden från ${entityName(state.settings.outdoor)} räknas som ${entityName(draft.outdoor)}. Modell och skuggförslag fortsätter direkt. Värdena ändras inte – mäter givarna olika blandas två givares mätningar. En säkerhetskopia tas först.`));
}
function indoorRemoval() {
  const old = state.settings.indoor || [], now = draft.indoor || [];
  return draft.mode === 'shadow' && !outdoorMigration() && now.length > 0 && now.length < old.length && now.every(e => old.includes(e)) && new Set(now).size === now.length;
}
function renderIndoorHistory() {
  const box = $('indoor-history'), show = indoorRemoval();
  box.hidden = !show;
  if (!show) { indoorChoice = 'keep'; return; }
  const removed = state.settings.indoor.filter(e => !draft.indoor.includes(e)).map(entityName).join(', ');
  const option = (value, title, text) => h('label', {class: 'choice'},
    h('input', {type: 'radio', name: 'indoor-history', value, checked: indoorChoice === value, onchange: () => { indoorChoice = value; changed(); }}),
    h('span', {}, h('strong', {text: title}), h('small', {text})));
  box.replaceChildren(h('strong', {text: 'Vad ska hända med historiken?'}),
    option('keep', 'Behåll historiken utan borttagna givare', `Tidigare mätningar räknas om till medelvärdet av de kvarvarande rummen; ${removed} räknas inte längre. Inga värden ändras och husmodellen tränas om direkt. En säkerhetskopia tas först.`),
    option('restart', 'Börja om insamlingen', `Husmodellen samlar en ny period på minst ${MIN_HOURS} timmar med det nya rumsurvalet.`));
}
function changedKeys() { return Object.keys(draft).filter(k => JSON.stringify(draft[k]) !== JSON.stringify(state.settings[k])); }
function validateDraft() {
  const errors = {};
  for (const k of NUMBER_FIELDS) if (!finite(draft[k])) errors[k] = 'Ange ett tal.';
  if (!errors.target && !errors.comfort_min && !errors.comfort_max && !(7 <= draft.comfort_min && draft.comfort_min <= draft.target && draft.target <= draft.comfort_max && draft.comfort_max <= 35))
    errors.target = 'Lägsta ≤ önskad ≤ högsta, inom 7–35 °C.';
  if (draft.signal_min >= draft.signal_max) errors.signal_max = 'Högsta signal måste vara större än lägsta.';
  if (!Number.isInteger(draft.watchdog_seconds) || draft.watchdog_seconds < 0 || draft.watchdog_seconds > 86400) errors.watchdog_seconds = 'Hela sekunder, 0–86 400.';
  if (draft.mode === 'shadow' && (!draft.indoor.length || !draft.outdoor)) errors.mode = 'Skuggläge kräver minst ett rum och en verklig utegivare.';
  if (!!draft.pv_power !== !!draft.pv_forecast) errors.pv_power = 'Välj både produktion och prognoskälla, eller ingen av dem.';
  if (['solar', 'cloud', 'pv_power'].filter(k => draft[k]).length > 1) errors.solar = 'Välj bara en historisk solsignal: solinstrålning, molnighet eller solceller.';
  return errors;
}
function changed() {
  const keys = changedKeys(), errors = validateDraft(), bar = $('savebar');
  for (const k of NUMBER_FIELDS) $(k).setAttribute('aria-invalid', String(!!errors[k]));
  bar.hidden = !keys.length;
  const comfortOnly = keys.every(k => COMFORT_KEYS.includes(k)), active = ['active', 'pending'].includes(controlState());
  $('savebar-title').textContent = keys.length === 1 ? '1 osparad ändring' : `${keys.length} osparade ändringar`;
  const firstError = Object.values(errors)[0];
  const names = keys.map(k => FIELD_LABELS[k] || k).join(', ');
  $('savebar-detail').textContent = firstError || [names.charAt(0).toUpperCase() + names.slice(1),
    !comfortOnly && active ? 'Om du sparar stoppas aktiv styrning och måste startas igen på Hem.' : comfortOnly ? 'Gäller direkt.' : ''].filter(Boolean).join(' · ');
  bar.dataset.tone = firstError || (!comfortOnly && active) ? 'warn' : '';
  $('save').disabled = !!firstError;
  renderComfortBar();
  renderOutdoorHistory();
  renderIndoorHistory();
  if (!firstError && outdoorMigration() && outdoorChoice === 'migrate') $('savebar-detail').textContent += ' · Historiken flyttas till den nya utegivaren.';
  if (!firstError && indoorRemoval() && indoorChoice === 'keep') $('savebar-detail').textContent += ' · Historiken behålls utan borttagna rumsgivare.';
}
function renderComfortBar() {
  const lo = Math.min(15, draft.comfort_min - 1), hi = Math.max(27, draft.comfort_max + 1), pct = v => Math.max(0, Math.min(100, (v - lo) / (hi - lo) * 100)) + '%';
  if (![draft.comfort_min, draft.comfort_max, draft.target].every(finite)) return;
  $('comfort-band').style.left = pct(draft.comfort_min);
  $('comfort-band').style.width = `calc(${pct(draft.comfort_max)} - ${pct(draft.comfort_min)})`;
  $('comfort-target').style.left = pct(draft.target);
}
function syncDraftComfort() {
  if (!draft) return;
  const keys = changedKeys();
  if (!keys.length) draft = structuredClone(state.settings);
  else for (const k of ['target', 'comfort_min', 'comfort_max']) if (!keys.includes(k)) draft[k] = state.settings[k];
  if (state.view === 'settings' || !keys.length) fillForm();
}
async function saveSettings() {
  const errors = validateDraft();
  if (Object.keys(errors).length) { toast(Object.values(errors)[0], {tone: 'err'}); return; }
  const keys = changedKeys(), comfortOnly = keys.every(k => COMFORT_KEYS.includes(k)), wasActive = ['active', 'pending'].includes(controlState());
  $('save').disabled = true;
  try {
    let moved = null, kept = null;
    if (outdoorMigration() && outdoorChoice === 'migrate') {
      const result = await api('outdoor/migrate', {config: draft, confirm: true});
      state.settings = result.config; moved = result.migration;
    } else if (indoorRemoval() && indoorChoice === 'keep') {
      const result = await api('indoor/migrate', {config: draft, confirm: true});
      state.settings = result.config; kept = result.migration;
    } else state.settings = await api('config', draft);
    draft = structuredClone(state.settings); outdoorChoice = 'restart'; indoorChoice = 'keep'; fillForm();
    toast((moved ? `Historiken är flyttad: ${num(moved.samples, 0)} mätningar och ${num(moved.forecasts, 0)} arkiverade förslag. Säkerhetskopia: ${moved.backup}. `
      : kept ? `Historiken är behållen: ${num(kept.samples, 0)} mätningar räknas utan borttagna rumsgivare. Säkerhetskopia: ${kept.backup}. ` : 'Inställningarna är sparade. ')
      + (!comfortOnly && wasActive ? 'Aktiv styrning stoppades – starta den igen på Hem.' : ''), {tone: !comfortOnly && wasActive ? 'err' : 'ok', timeout: moved || kept ? 12000 : 5000});
    refresh();
  } catch (e) { toast(e.message, {tone: 'err'}); } finally { $('save').disabled = false; }
}
function initSettings() {
  draft = structuredClone(state.settings);
  for (const k of NUMBER_FIELDS) $(k).addEventListener('input', ev => { const v = ev.target.value.replace(',', '.'); draft[k] = v === '' ? NaN : k === 'watchdog_seconds' ? Number(v) : Number(v); changed(); });
  for (const k of CHECK_FIELDS) $(k).addEventListener('change', ev => { draft[k] = ev.target.checked; changed(); });
  for (const r of document.querySelectorAll('input[name=mode]')) r.addEventListener('change', ev => { draft.mode = ev.target.value; changed(); });
  $('settings-form').addEventListener('submit', ev => { ev.preventDefault(); saveSettings(); });
  $('save').onclick = saveSettings;
  $('discard').onclick = () => { draft = structuredClone(state.settings); outdoorChoice = 'restart'; indoorChoice = 'keep'; fillForm(); };
  $('pv-refresh').onclick = async () => { $('pv-source-status').textContent = 'Söker installerade Forecast.Solar-källor…'; await loadPvSources(); $('pv-source-status').textContent = state.pvMessage; document.querySelectorAll('[data-picker=pv_forecast]').forEach(renderPicker); };
  window.addEventListener('beforeunload', ev => { if (changedKeys().length) { ev.preventDefault(); ev.returnValue = ''; } });
  const links = [...document.querySelectorAll('.section-nav a')];
  const observer = new IntersectionObserver(entries => { for (const e of entries) if (e.isIntersecting) links.forEach(a => a.classList.toggle('current', a.hash === '#installningar/' + e.target.id.slice(2))); }, {rootMargin: '-20% 0px -70% 0px'});
  document.querySelectorAll('.section').forEach(sec => observer.observe(sec));
  fillForm();
}

/* BOOT ===================================================================== */
function renderAll() { if (!state.settings) return; renderHome(); renderWeather(); renderLearning(); }
window.addEventListener('datachange', renderAll);
window.addEventListener('viewchange', async ev => {
  if (ev.detail === 'settings' && draft) { await loadEntities(); if (!document.activeElement?.matches('input[type=search]')) fillForm(); }
  if (ev.detail === 'home' || ev.detail === 'weather') renderAll();
});
document.querySelectorAll('[data-back]').forEach(b => b.addEventListener('click', () => {
  state.back = Number(b.dataset.back);
  document.querySelectorAll('[data-back]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
  renderHomeTimeline();
}));
$('target-up').onclick = () => adjustTarget(.5);
$('target-down').onclick = () => adjustTarget(-.5);
$('control-start').onclick = openStartDialog;
$('control-stop').onclick = stopControl;
$('start-fix').onclick = () => $('start-dialog').close();
$('refresh').onclick = refresh;
$('shadow-evaluate').onclick = shadowEvaluate;
$('export-model-history').onclick = exportHistory;
$('inspect').onclick = inspectFile;
$('evaluate-model').onclick = evaluateModel;

(async function boot() {
  hydrateIcons(); route();
  try {
    try { const tz = await api('timezone'); new Intl.DateTimeFormat('sv-SE', {timeZone: tz.time_zone}).format(); state.timeZone = tz.time_zone; } catch {}
    state.settings = await api('config');
    await Promise.all([loadEntities(), loadPvSources()]);
    initSettings();
    await refresh();
    restoreModel();
    setInterval(refresh, 60000);
  } catch (e) { toast(e.message, {tone: 'err', timeout: 20000}); }
})();
