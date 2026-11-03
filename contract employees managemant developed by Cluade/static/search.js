// Top-bar search: live suggestions for employees and firms while typing.
// Arrow keys move, Enter opens, Esc closes, "/" focuses the box from anywhere.
(function () {
  const input = document.getElementById('globalSearch');
  const box = document.getElementById('searchResults');
  if (!input || !box) return;
  const form = input.form;
  let items = [], active = -1, timer = null, lastQuery = '', controller = null;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function close() {
    box.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    active = -1;
  }

  function highlight(i) {
    items.forEach((it, k) => it.classList.toggle('active', k === i));
    active = i;
    if (items[i]) items[i].scrollIntoView({ block: 'nearest' });
  }

  function row(r, kind) {
    const a = el('a', 'sr-item');
    a.href = r.url;
    a.setAttribute('role', 'option');
    const pic = el('span', 'sr-pic ' + kind);
    if (r.photo) {
      const img = el('img');
      img.src = r.photo;
      img.alt = '';
      pic.appendChild(img);
    } else {
      pic.textContent = (r.name || '?').replace(/^M\/s\.?\s*/i, '').trim().charAt(0).toUpperCase();
    }
    const body = el('span', 'sr-body');
    const top = el('span', 'sr-name', r.name);
    if (r.inactive) top.appendChild(el('span', 'sr-tag', 'Inactive'));
    body.appendChild(top);
    if (r.sub) body.appendChild(el('span', 'sr-sub', r.sub));
    a.appendChild(pic);
    a.appendChild(body);
    if (r.code) a.appendChild(el('span', 'sr-code', r.code));
    return a;
  }

  function render(data, q) {
    box.innerHTML = '';
    items = [];
    const groups = [['Employees', data.employees, 'emp'], ['Firms', data.firms, 'firm']];
    let any = false;
    groups.forEach(([label, list, kind]) => {
      if (!list.length) return;
      any = true;
      box.appendChild(el('div', 'sr-group', label));
      list.forEach(r => { const a = row(r, kind); box.appendChild(a); items.push(a); });
    });
    if (!any) box.appendChild(el('div', 'sr-empty', 'No employee or firm matches "' + q + '"'));
    const all = el('a', 'sr-all', 'See all results for "' + q + '"  ↵');
    all.href = form.action + '?q=' + encodeURIComponent(q);
    box.appendChild(all);
    items.push(all);
    box.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    active = -1;
  }

  async function lookup() {
    const q = input.value.trim();
    if (q.length < 2 && !/^\d$/.test(q)) { close(); lastQuery = ''; return; }
    if (q === lastQuery && !box.hidden) return;
    lastQuery = q;
    if (controller) controller.abort();
    controller = new AbortController();
    try {
      const res = await fetch('/search/suggest?q=' + encodeURIComponent(q), { signal: controller.signal });
      if (!res.ok) return;
      const data = await res.json();
      if (input.value.trim() === q) render(data, q);
    } catch (e) { /* aborted or offline – ignore */ }
  }

  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(lookup, 180); });
  input.addEventListener('focus', () => { if (input.value.trim()) lookup(); });
  input.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' && items.length) { e.preventDefault(); highlight(Math.min(active + 1, items.length - 1)); }
    else if (e.key === 'ArrowUp' && items.length) { e.preventDefault(); highlight(Math.max(active - 1, 0)); }
    else if (e.key === 'Enter' && active >= 0 && items[active]) { e.preventDefault(); window.location = items[active].href; }
    else if (e.key === 'Escape') { close(); input.blur(); }
  });
  form.addEventListener('submit', e => { if (!input.value.trim()) e.preventDefault(); });
  document.addEventListener('click', e => { if (!form.contains(e.target)) close(); });
  document.addEventListener('keydown', e => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) || document.activeElement.isContentEditable;
    if (e.key === '/' && !typing) { e.preventDefault(); input.focus(); input.select(); }
  });
})();
