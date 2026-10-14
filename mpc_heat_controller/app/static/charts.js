// Charts: the comfort dial and a multi-lane timeline shared by Home, Weather and Learning.

// Comfort dial: a 240° arc with cold / comfort / warm zones, the target as a tick and the
// current temperature as a marker.
function drawDial(svg, {temp, target, min, max, tone}) {
  svg.replaceChildren();
  const cx = 120, cy = 112, r = 92, from = 150, sweep = 240;
  const lo = Math.min(min - 2, finite(temp) ? temp - .5 : Infinity), hi = Math.max(max + 2, finite(temp) ? temp + .5 : -Infinity);
  const angle = v => from + (Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo) * sweep;
  const point = (a, radius = r) => [cx + radius * Math.cos(a * Math.PI / 180), cy + radius * Math.sin(a * Math.PI / 180)];
  const arc = (a0, a1, cls, width = 16) => {
    const [x0, y0] = point(a0), [x1, y1] = point(a1);
    svg.append(s('path', {d: `M${x0} ${y0} A${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1} ${y1}`, class: cls, fill: 'none', 'stroke-width': width, 'stroke-linecap': 'round'}));
  };
  arc(from, from + sweep, 'dial-track', 18);
  arc(from, angle(min) - 1.5, 'dial-cold');
  arc(angle(max) + 1.5, from + sweep, 'dial-warm');
  arc(angle(min), angle(max), 'dial-band');
  for (const [value, anchor] of [[min, 'end'], [max, 'start']]) {
    const [x, y] = point(angle(value), r + 22);
    svg.append(s('text', {x, y: y + 4, class: 'dial-label', 'text-anchor': y > cy ? 'middle' : anchor}, num(value)));
  }
  if (finite(target)) {
    const [x0, y0] = point(angle(target), r - 14), [x1, y1] = point(angle(target), r + 14);
    svg.append(s('line', {x1: x0, y1: y0, x2: x1, y2: y1, class: 'dial-target', 'stroke-width': 3, 'stroke-linecap': 'round'}));
  }
  if (finite(temp)) {
    const [x, y] = point(angle(temp));
    svg.append(s('circle', {cx: x, cy: y, r: 11, class: 'dial-marker', 'data-tone': tone}));
  }
}

function niceTicks(lo, hi, count = 4) {
  const raw = (hi - lo) / count || 1, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(v => v >= raw);
  const ticks = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) ticks.push(+v.toFixed(6));
  return {ticks, step};
}

// Timeline. config = {start, end, now, lanes: [{title, height, unit, min, max, band:[lo,hi], format, series:[
//   {key, label, color, dash, area, bars, maxGap, points:[{t, v}]}]}], empty}
// Series visibility is remembered per host while the page is open.
function timeline(host, config) {
  host._timeline = config;
  host._hidden ||= new Set();
  if (!host._observer) {
    let width = 0;
    host._observer = new ResizeObserver(() => { if (host.clientWidth !== width) { width = host.clientWidth; renderTimeline(host); } });
    host._observer.observe(host);
  }
  renderTimeline(host);
}

function renderTimeline(host) {
  const config = host._timeline;
  if (!config) return;
  host.replaceChildren();
  const lanes = config.lanes.filter(l => l.series.some(se => se.points.some(p => finite(p.v))));
  if (!lanes.length) { host.append(h('p', {class: 'empty', text: config.empty || 'Inga värden att visa ännu.'})); return; }
  const W = Math.max(300, host.clientWidth || 600), narrow = W < 520;
  const padL = 40, padR = 8, titleH = 20, gap = 16, axisH = 24;
  const plotW = W - padL - padR, span = config.end - config.start;
  const x = t => padL + (t - config.start) / span * plotW;
  let y0 = 4;
  const layout = lanes.map(lane => { const height = narrow ? Math.round(lane.height * .8) : lane.height; const top = y0 + titleH; y0 = top + height + gap; return {lane, top, height}; });
  const H = y0 - gap + axisH;
  const svg = s('svg', {viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img', 'aria-label': config.label || 'Tidslinje'});
  const clipId = 'clip-' + (host.id || Math.random().toString(36).slice(2));
  const clip = s('clipPath', {id: clipId}); clip.append(s('rect', {x: padL, y: 0, width: plotW, height: H}));
  svg.append(clip);
  const plot = s('g', {'clip-path': `url(#${clipId})`});

  // Future shading and day separators across the whole chart.
  if (config.now > config.start && config.now < config.end) svg.append(s('rect', {x: x(config.now), y: 4, width: x(config.end) - x(config.now), height: H - axisH - 4, class: 'tl-future', rx: 8}));
  const hourMs = 3600000, firstHour = Math.ceil(config.start / hourMs) * hourMs;
  const labelEvery = [1, 2, 3, 6, 12, 24].find(n => plotW / (span / hourMs / n) >= (narrow ? 52 : 64)) || 24;
  for (let t = firstHour; t <= config.end; t += hourMs) {
    const hour = zoneHour(t);
    if (hour === 0) svg.append(s('line', {x1: x(t), x2: x(t), y1: 4, y2: H - axisH, class: 'tl-day'}));
    if (hour % labelEvery === 0 && x(t) > padL + 14 && x(t) < W - 18) {
      svg.append(s('text', {x: x(t), y: H - 6, 'text-anchor': 'middle', class: 'tl-axis'},
        hour === 0 ? localTime(t, {weekday: 'short'}) : localTime(t, {hour: '2-digit', minute: '2-digit'})));
    }
  }

  const scales = [];
  for (const {lane, top, height} of layout) {
    const visible = lane.series.filter(se => !host._hidden.has(se.key));
    const values = visible.flatMap(se => se.points.map(p => p.v).filter(finite));
    if (lane.band) values.push(...lane.band);
    let lo = lane.min ?? Math.min(...values), hi = lane.max ?? Math.max(...values);
    if (!values.length) { lo = lane.min ?? 0; hi = lane.max ?? 1; }
    if (hi - lo < (lane.minRange || 1)) { const mid = (hi + lo) / 2, half = (lane.minRange || 1) / 2; lo = lane.min ?? mid - half; hi = lane.max ?? mid + half; }
    const pad = (hi - lo) * .08; if (lane.min === undefined) lo -= pad; if (lane.max === undefined) hi += pad;
    const y = v => top + height - (v - lo) / (hi - lo) * height;
    scales.push({lane, y, top, height, visible});
    svg.append(s('text', {x: 0, y: top - 7, class: 'tl-lane-title'}, lane.title));
    if (lane.band) svg.append(s('rect', {x: padL, width: plotW, y: y(lane.band[1]), height: Math.max(1, y(lane.band[0]) - y(lane.band[1])), class: 'tl-band'}));
    for (const tick of niceTicks(lo, hi, height < 90 ? 2 : 4).ticks) {
      if (tick < lo || tick > hi) continue;
      svg.append(s('line', {x1: padL, x2: W - padR, y1: y(tick), y2: y(tick), class: 'tl-grid'}));
      svg.append(s('text', {x: padL - 6, y: y(tick) + 4, 'text-anchor': 'end', class: 'tl-axis'}, (lane.tick || (v => num(v, Math.abs(v) < 10 && v % 1 ? 1 : 0)))(tick)));
    }
    for (const se of visible) {
      const pts = se.points.filter(p => finite(p.v) && p.t >= config.start - hourMs && p.t <= config.end + hourMs).sort((a, b) => a.t - b.t);
      const maxGap = se.maxGap || 2 * hourMs;
      if (se.bars) {
        const bw = Math.max(2, plotW / (span / hourMs) * .62);
        for (const p of pts) {
          const r = s('rect', {x: x(p.t) - bw / 2, width: bw, y: y(Math.max(p.v, lo)), height: Math.max(0, y(lo) - y(Math.max(p.v, lo))), rx: 2, opacity: se.opacity ?? .7});
          r.style.fill = se.color; plot.append(r);
        }
        continue;
      }
      let d = '', area = '', run = [];
      const flush = () => {
        if (!run.length) return;
        if (run.length === 1) { const c = s('circle', {cx: x(run[0].t), cy: y(run[0].v), r: 2.5}); c.style.fill = se.color; plot.append(c); }
        d += run.map((p, i) => (i ? 'L' : 'M') + x(p.t).toFixed(1) + ' ' + y(p.v).toFixed(1)).join('');
        if (se.area) area += `M${x(run[0].t).toFixed(1)} ${y(lo)}` + run.map(p => 'L' + x(p.t).toFixed(1) + ' ' + y(p.v).toFixed(1)).join('') + `L${x(run.at(-1).t).toFixed(1)} ${y(lo)}Z`;
        run = [];
      };
      for (const p of pts) { if (run.length && (p.t - run.at(-1).t > maxGap)) flush(); run.push(p); }
      flush();
      if (area) { const a = s('path', {d: area, opacity: .22}); a.style.fill = se.color; plot.append(a); }
      if (d) {
        const path = s('path', {d, fill: 'none', 'stroke-width': se.width || 2.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round', 'stroke-dasharray': se.dash ? '6 5' : null});
        path.style.stroke = se.color; plot.append(path);
      }
    }
  }
  svg.append(plot);
  if (config.now > config.start && config.now < config.end) {
    svg.append(s('line', {x1: x(config.now), x2: x(config.now), y1: 0, y2: H - axisH, class: 'tl-now'}));
    svg.append(s('text', {x: x(config.now) + 5, y: 12, class: 'tl-now-label'}, 'Nu'));
  }

  // Hover / touch read-out.
  const hover = s('g', {'pointer-events': 'none'}), overlay = s('rect', {x: padL, y: 0, width: plotW, height: H - axisH, fill: 'transparent'});
  svg.append(hover, overlay);
  const tip = h('div', {class: 'tooltip', hidden: true});
  const move = event => {
    const box = svg.getBoundingClientRect(), px = (event.clientX - box.left) * W / box.width;
    const t = config.start + (px - padL) / plotW * span;
    hover.replaceChildren(s('line', {x1: px, x2: px, y1: 0, y2: H - axisH, class: 'tl-hover'}));
    const rows = [];
    for (const {y, visible, lane} of scales) for (const se of visible) {
      let best = null;
      for (const p of se.points) if (finite(p.v) && (!best || Math.abs(p.t - t) < Math.abs(best.t - t))) best = p;
      if (!best || Math.abs(best.t - t) > (se.bars ? 40 : 25) * 60000) continue;
      const dot = s('circle', {cx: x(best.t), cy: y(best.v), r: 4.5, 'stroke-width': 2}); dot.style.fill = se.color; dot.style.stroke = 'var(--surface)'; hover.append(dot);
      const row = h('div', {}, h('span', {}, h('i'), se.label), h('strong', {text: (se.format || lane.format || (v => degShort(v)))(best.v)}));
      row.querySelector('i').style.color = se.color; rows.push(row);
    }
    if (!rows.length) { tip.hidden = true; return; }
    tip.replaceChildren(h('b', {text: (t > config.now ? 'Prognos · ' : '') + dayTime(t)}), ...rows);
    tip.hidden = false;
    const left = Math.min(W - 95, Math.max(95, px)) / W * box.width;
    tip.style.left = left + 'px';
    tip.style.top = Math.max(0, (event.clientY - box.top) - tip.offsetHeight - 18) + 'px';
  };
  overlay.addEventListener('pointermove', move);
  overlay.addEventListener('pointerdown', move);
  overlay.addEventListener('pointerleave', () => { hover.replaceChildren(); tip.hidden = true; });
  host.append(svg, tip);

  // Legend doubles as series toggles.
  const legend = h('div', {class: 'tl-legend', role: 'group', 'aria-label': 'Visa eller dölj kurvor'});
  for (const lane of lanes) for (const se of lane.series) {
    if (!se.points.some(p => finite(p.v))) continue;
    const button = h('button', {type: 'button', 'aria-pressed': String(!host._hidden.has(se.key)),
      onclick: () => { host._hidden.has(se.key) ? host._hidden.delete(se.key) : host._hidden.add(se.key); renderTimeline(host); }},
      h('span', {class: 'swatch' + (se.dash ? ' dash' : '') + (se.area || se.bars ? ' area' : '')}), se.label);
    button.querySelector('.swatch').style.color = se.color;
    legend.append(button);
  }
  host.append(legend);
}
