// Core: helpers, API access, shared state, navigation and polling.
// The API is unchanged; every control or configuration request goes through api().
const $ = id => document.getElementById(id);
const SVG = 'http://www.w3.org/2000/svg';

const state = {
  settings: null, entities: [], connected: false, connectionMessage: '', pvSources: [], pvMessage: '',
  status: null, telemetry: null, telemetryFailed: false, trends: null, timeZone: 'Europe/Stockholm',
  updatedAt: null, view: 'home', back: 24
};

async function api(path, body, raw = false) {
  const response = await fetch(`api/${path}`, body === undefined ? {} : {method: 'POST',
    headers: {'X-MPC-Request': '1', 'Content-Type': raw ? 'text/csv' : 'application/json'}, body: raw ? body : JSON.stringify(body)});
  const value = await response.json();
  if (!response.ok) throw Error(value.error || 'Kunde inte läsa data');
  return value;
}

// DOM helpers. Attributes only: the CSP forbids inline style attributes, so styling is via classes,
// data-attributes or the CSSOM (element.style), which the CSP allows.
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'text') el.textContent = value;
    else if (key === 'class') el.className = value;
    else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else if (key === 'data') Object.assign(el.dataset, value);
    else el.setAttribute(key, value === true ? '' : value);
  }
  el.append(...children.flat().filter(c => c !== null && c !== undefined && c !== false));
  return el;
}
function s(tag, attrs = {}, text) {
  const el = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) if (value !== undefined && value !== null) el.setAttribute(key, value);
  if (text !== undefined) el.textContent = text;
  return el;
}

// Icons: simple stroke paths, 24×24.
const ICONS = {
  logo: 'M4 11.5 12 5l8 6.5V20a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z M9 17c1-1.2 1-2.3 0-3.5s-1-2.3 0-3.5 M13 17c1-1.2 1-2.3 0-3.5s-1-2.3 0-3.5',
  home: 'M4 11.5 12 5l8 6.5V20a1 1 0 0 1-1 1h-4v-6h-6v6H5a1 1 0 0 1-1-1z',
  weather: 'M8 3v1.5M3 8h1.5M4.6 4.6l1 1M12 5.6l-1 1M5.5 9.5a3 3 0 1 1 5.6-1.7 M8 20h9.5a3.5 3.5 0 1 0-.9-6.9 5 5 0 0 0-9.6 1.4A2.8 2.8 0 0 0 8 20z',
  learning: 'M3 20h18 M6 16v-4 M10 16V8 M14 16v-6 M18 16V5',
  settings: 'M4 7h10 M18 7h2 M4 17h4 M12 17h8 M16 5v4 M10 15v4',
  refresh: 'M20 11a8 8 0 1 0-2.3 5.7 M20 4v7h-7',
  sensor: 'M10 13.5V5a2 2 0 1 1 4 0v8.5a4 4 0 1 1-4 0z M12 9v8',
  cloud: 'M7 19h10a4 4 0 1 0-1-7.9A5.5 5.5 0 0 0 5.4 13 3 3 0 0 0 7 19z',
  sun: 'M12 3v2 M12 19v2 M3 12h2 M19 12h2 M5.6 5.6 7 7 M17 17l1.4 1.4 M5.6 18.4 7 17 M17 7l1.4-1.4 M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z',
  moon: 'M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z',
  model: 'M12 3a4 4 0 0 0-4 4 4 4 0 0 0-3 6.5A4 4 0 0 0 9 20h1V3.5 M12 3a4 4 0 0 1 4 4 4 4 0 0 1 3 6.5A4 4 0 0 1 15 20h-1V3.5',
  link: 'M9 15l6-6 M10 6l1-1a4 4 0 0 1 6 6l-1 1 M14 18l-1 1a4 4 0 0 1-6-6l1-1',
  thermostat: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M12 12l3-3',
  log: 'M6 3h9l3 3v15H6z M9 10h6 M9 14h6 M9 18h4',
  outdoor: 'M3 21h18 M5 21V11l7-5 7 5v10 M12 2v2 M19 4l-1 1 M5 4l1 1',
  ohmigo: 'M5 7h14v10H5z M9 7V4 M15 7V4 M9 12h2 M14 11v2 M8 20h8',
  pump: 'M3 6h18v12H3z M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z M12 9v6 M9 12h6 M6 9h1',
  house: 'M4 11.5 12 5l8 6.5V20a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z M10 14h4v7h-4z',
  up: 'M12 19V5 M6 11l6-6 6 6', down: 'M12 5v14 M6 13l6 6 6-6', flat: 'M5 12h14 M14 7l5 5-5 5'
};
function icon(name, cls = '') {
  const svg = s('svg', {viewBox: '0 0 24 24', class: ('icon ' + cls).trim(), 'aria-hidden': 'true'});
  svg.append(s('path', {d: ICONS[name] || ''}));
  return svg;
}
function hydrateIcons(root = document) { root.querySelectorAll('[data-icon]').forEach(el => { if (!el.firstChild) el.append(icon(el.dataset.icon)); }); }

// Formatting: Swedish decimal comma and the Home Assistant time zone.
const finite = v => typeof v === 'number' && Number.isFinite(v);
const num = (v, d = 1) => finite(v) ? v.toLocaleString('sv-SE', {minimumFractionDigits: d, maximumFractionDigits: d}) : '—';
const deg = (v, d = 1) => finite(v) ? num(v, d) + ' °C' : '—';
const degShort = (v, d = 1) => finite(v) ? num(v, d) + '°' : '—';
const watts = v => !finite(v) ? '—' : Math.abs(v) >= 1000 ? num(v / 1000, 2) + ' kW' : num(v, 0) + ' W';
function localTime(value, options = {timeStyle: 'short'}) {
  if (value === null || value === undefined || value === '') return '—';
  try { return new Date(value).toLocaleString('sv-SE', {...options, timeZone: state.timeZone}); }
  catch { return new Date(value).toLocaleString('sv-SE', options); }
}
const clock = t => localTime(t, {hour: '2-digit', minute: '2-digit'});
const dayTime = t => localTime(t, {weekday: 'short', hour: '2-digit', minute: '2-digit'});
const fullTime = t => localTime(t, {dateStyle: 'medium', timeStyle: 'short'});
function ago(value) {
  if (!value) return '';
  const minutes = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 60000));
  if (minutes < 1) return 'nyss';
  if (minutes < 60) return minutes + ' min sedan';
  if (minutes < 48 * 60) return Math.round(minutes / 60) + ' h sedan';
  return Math.round(minutes / 1440) + ' dygn sedan';
}
function zoneHour(t) { return Number(localTime(t, {hour: '2-digit', hourCycle: 'h23'})); }

function entityName(id) {
  if (!id) return '';
  const e = state.entities.find(x => x.entity_id === id);
  return e?.attributes?.friendly_name || id;
}

// Toasts
function toast(message, {tone = 'ok', action, timeout = 5000} = {}) {
  const box = h('div', {class: 'toast', data: {tone}, role: tone === 'err' ? 'alert' : 'status'}, h('span', {text: message}));
  if (action) box.append(h('button', {type: 'button', text: action.label, onclick: () => { action.run(); box.remove(); }}));
  $('toasts').append(box);
  setTimeout(() => box.remove(), tone === 'err' ? Math.max(timeout, 9000) : timeout);
}

// Navigation via hash so views can be linked and survive reloads inside the HA ingress panel.
const ROUTES = {hem: 'home', vader: 'weather', lararande: 'learning', installningar: 'settings'};
function route() {
  const [page, anchor] = location.hash.slice(1).split('/');
  const view = ROUTES[page] || 'home';
  state.view = view;
  document.querySelectorAll('.view').forEach(v => v.hidden = v.id !== view);
  document.querySelectorAll('[data-view]').forEach(a => a.setAttribute('aria-current', a.dataset.view === view ? 'page' : 'false'));
  if (anchor) requestAnimationFrame(() => $('s-' + anchor)?.scrollIntoView({behavior: 'smooth', block: 'start'}));
  else window.scrollTo({top: 0});
  window.dispatchEvent(new CustomEvent('viewchange', {detail: view}));
}
if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
window.addEventListener('hashchange', route);

// Data loading
async function loadEntities() {
  try {
    const result = await api('entities');
    state.entities = result.entities || [];
    state.connected = !!result.connected;
    state.connectionMessage = result.connected ? 'Ansluten till Home Assistant · ' + state.entities.length + ' entiteter tillgängliga.' : result.message;
  } catch (e) { state.connectionMessage = e.message; }
}
async function loadPvSources() {
  try { const pv = await api('pv-sources'); state.pvSources = pv.sources || []; state.pvMessage = pv.message; }
  catch { state.pvMessage = 'Kunde inte läsa Forecast.Solar-källor.'; }
}
async function refresh() {
  const button = $('refresh');
  button.classList.add('spinning');
  const [status, telemetry, trends] = await Promise.allSettled([api('status'), api('telemetry'), api('trends')]);
  if (status.status === 'fulfilled') state.status = status.value;
  state.telemetryFailed = telemetry.status !== 'fulfilled';
  state.telemetry = telemetry.status === 'fulfilled' ? telemetry.value : null;
  if (trends.status === 'fulfilled') state.trends = trends.value;
  state.updatedAt = Date.now();
  button.classList.remove('spinning');
  if (status.status !== 'fulfilled') toast(status.reason.message, {tone: 'err'});
  window.dispatchEvent(new Event('datachange'));
}
