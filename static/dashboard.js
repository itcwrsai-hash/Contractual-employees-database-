// Dashboard charts: hover/focus tooltips for bar segments and the 30-day line chart.
(function () {
  const tip = document.getElementById('chartTip');

  function showTip(x, y, title, lines) {
    tip.innerHTML = '';
    if (title) {
      const t = document.createElement('div');
      t.className = 't';
      t.textContent = title;
      tip.appendChild(t);
    }
    lines.forEach(line => {
      const d = document.createElement('div');
      if (line.color) {
        const sw = document.createElement('i');
        sw.className = 'sw';
        sw.style.background = line.color;
        sw.style.marginRight = '6px';
        d.appendChild(sw);
      }
      d.appendChild(document.createTextNode(line.text));
      tip.appendChild(d);
    });
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    let left = x + 14, top = y + 14;
    if (left + r.width > window.innerWidth - 8) left = x - r.width - 14;
    if (top + r.height > window.innerHeight - 8) top = y - r.height - 14;
    tip.style.left = left + 'px';
    tip.style.top = top + 'px';
  }
  function hideTip() { tip.hidden = true; }

  // Bar segments
  document.querySelectorAll('[data-tip]').forEach(el => {
    const lines = () => [{ text: el.dataset.tip }];
    el.addEventListener('mousemove', e => showTip(e.clientX, e.clientY, el.dataset.title, lines()));
    el.addEventListener('mouseleave', hideTip);
    el.addEventListener('focus', () => {
      const r = el.getBoundingClientRect();
      showTip(r.left + r.width / 2, r.bottom, el.dataset.title, lines());
    });
    el.addEventListener('blur', hideTip);
  });

  // 30-day line chart
  const box = document.getElementById('trendChart');
  const data = window.TREND || [];
  if (!box || !data.length) return;
  const NS = 'http://www.w3.org/2000/svg';
  const css = getComputedStyle(document.documentElement);
  const series = [
    { key: 'present', label: 'Present', color: css.getPropertyValue('--s-present').trim() },
    { key: 'absent', label: 'Absent', color: css.getPropertyValue('--s-absent').trim() },
  ];
  const fmt = iso => iso.slice(8, 10) + '-' + iso.slice(5, 7);

  function el(name, attrs, parent) {
    const n = document.createElementNS(NS, name);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }

  function niceMax(v) {
    if (v <= 5) return 5;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
    return 10 * p;
  }

  function draw() {
    box.innerHTML = '';
    const W = box.clientWidth, H = box.clientHeight;
    const m = { top: 12, right: 76, bottom: 24, left: 34 };
    const iw = W - m.left - m.right, ih = H - m.top - m.bottom;
    const vals = data.flatMap(d => series.map(s => d[s.key] || 0));
    const yMax = niceMax(Math.max(...vals, 1));
    const x = i => m.left + (data.length === 1 ? iw / 2 : i * iw / (data.length - 1));
    const y = v => m.top + ih - v / yMax * ih;
    const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img' }, box);

    // grid + y ticks
    for (let i = 0; i <= 4; i++) {
      const v = yMax * i / 4, yy = y(v);
      el('line', { x1: m.left, x2: m.left + iw, y1: yy, y2: yy, stroke: i ? 'var(--grid)' : 'var(--axis)', 'stroke-width': 1 }, svg);
      el('text', { x: m.left - 6, y: yy + 4, 'text-anchor': 'end' }, svg).textContent = Number.isInteger(v) ? v : v.toFixed(1);
    }
    // x labels: every 5th day + last
    data.forEach((d, i) => {
      if (i % 5 === 0 || i === data.length - 1) {
        if (i !== data.length - 1 && data.length - 1 - i < 3) return;
        el('text', { x: x(i), y: H - 6, 'text-anchor': 'middle' }, svg).textContent = fmt(d.date);
      }
    });

    // lines (broken at days with no attendance)
    series.forEach(s => {
      let path = '', pen = false, last = null;
      data.forEach((d, i) => {
        const v = d[s.key];
        if (v === null || v === undefined) { pen = false; return; }
        path += (pen ? 'L' : 'M') + x(i).toFixed(1) + ',' + y(v).toFixed(1);
        pen = true;
        last = i;
      });
      if (path) el('path', { d: path, fill: 'none', stroke: s.color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, svg);
      // isolated points need a dot to be visible
      data.forEach((d, i) => {
        const v = d[s.key];
        if (v === null || v === undefined) return;
        const prev = data[i - 1], next = data[i + 1];
        const alone = (!prev || prev[s.key] == null) && (!next || next[s.key] == null);
        if (alone) el('circle', { cx: x(i), cy: y(v), r: 4, fill: s.color, stroke: '#fff', 'stroke-width': 2 }, svg);
      });
      s.last = last;
    });
    // direct labels at the line ends, nudged apart if they collide
    const ends = series.filter(s => s.last !== null).map(s => ({ s, yy: y(data[s.last][s.key]) }));
    if (ends.length === 2 && Math.abs(ends[0].yy - ends[1].yy) < 14) {
      const mid = (ends[0].yy + ends[1].yy) / 2, up = ends[0].yy <= ends[1].yy ? 0 : 1;
      ends[up].yy = mid - 7; ends[1 - up].yy = mid + 7;
    }
    ends.forEach(({ s, yy }) => {
      el('text', { x: x(s.last) + 8, y: yy + 4, class: 'end-label' }, svg).textContent = `${data[s.last][s.key]} ${s.label.toLowerCase()}`;
    });

    // crosshair + hover layer
    const cross = el('line', { y1: m.top, y2: m.top + ih, stroke: 'var(--axis)', 'stroke-width': 1, visibility: 'hidden' }, svg);
    const dots = series.map(s => el('circle', { r: 4.5, fill: s.color, stroke: '#fff', 'stroke-width': 2, visibility: 'hidden' }, svg));
    const hit = el('rect', { x: m.left, y: m.top, width: iw, height: ih, fill: 'transparent', tabindex: 0 }, svg);
    let idx = data.length - 1;

    function focusIndex(i, cx, cy) {
      idx = Math.max(0, Math.min(data.length - 1, i));
      const d = data[idx];
      cross.setAttribute('x1', x(idx)); cross.setAttribute('x2', x(idx));
      cross.setAttribute('visibility', 'visible');
      series.forEach((s, k) => {
        const v = d[s.key];
        if (v === null || v === undefined) { dots[k].setAttribute('visibility', 'hidden'); return; }
        dots[k].setAttribute('cx', x(idx)); dots[k].setAttribute('cy', y(v));
        dots[k].setAttribute('visibility', 'visible');
      });
      const lines = d.present === null
        ? [{ text: 'No attendance marked' }]
        : series.map(s => ({ text: `${s.label}: ${d[s.key]}`, color: s.color }));
      const r = svg.getBoundingClientRect();
      showTip(cx !== undefined ? cx : r.left + x(idx), cy !== undefined ? cy : r.top + m.top, fmt(d.date) + '-' + d.date.slice(0, 4), lines);
    }
    function clear() {
      cross.setAttribute('visibility', 'hidden');
      dots.forEach(d => d.setAttribute('visibility', 'hidden'));
      hideTip();
    }
    hit.addEventListener('mousemove', e => {
      const r = svg.getBoundingClientRect();
      const px = (e.clientX - r.left) * (W / r.width);
      focusIndex(Math.round((px - m.left) / iw * (data.length - 1)), e.clientX, e.clientY);
    });
    hit.addEventListener('mouseleave', clear);
    hit.addEventListener('focus', () => focusIndex(idx));
    hit.addEventListener('blur', clear);
    hit.addEventListener('keydown', e => {
      if (e.key === 'ArrowLeft') { focusIndex(idx - 1); e.preventDefault(); }
      if (e.key === 'ArrowRight') { focusIndex(idx + 1); e.preventDefault(); }
    });
  }

  draw();
  let t;
  window.addEventListener('resize', () => { clearTimeout(t); t = setTimeout(draw, 150); });
})();
