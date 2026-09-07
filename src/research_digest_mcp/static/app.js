'use strict';

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
};
const esc = (s) => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

async function get(path, params) {
  const url = new URL(path, location.origin);
  Object.entries(params || {}).forEach(([k, v]) => url.searchParams.set(k, v));
  const res = await fetch(url);
  return res.json();
}

/* ---------- navigation ---------- */

document.querySelectorAll('nav button').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('nav button').forEach((b) =>
      b.setAttribute('aria-current', String(b === btn)));
    document.querySelectorAll('.view').forEach((v) =>
      v.classList.toggle('on', v.id === 'view-' + btn.dataset.view));
    if (btn.dataset.view === 'trends') loadTrends();
    if (btn.dataset.view === 'saved') loadSaved();
    if (btn.dataset.view === 'lab') runLab();
  });
});

/* ---------- shared pieces ---------- */

function scoreBar(why, score) {
  const c = why.components || {};
  const wrap = el('div', 'score');
  wrap.appendChild(el('span', 'num', score.toFixed(3)));
  const bar = el('div', 'bar');
  const total = Math.max(score, 0.0001);
  [['seg-base', c.base], ['seg-breadth', c.breadth_bonus], ['seg-recency', c.recency]]
    .forEach(([cls, value]) => {
      if (!value) return;
      const seg = el('i', cls);
      seg.style.width = (Math.min(value / total, 1) * 100).toFixed(1) + '%';
      bar.appendChild(seg);
    });
  wrap.appendChild(bar);
  return wrap;
}

function legend(why) {
  const c = why.components || {};
  const box = el('div', 'legend');
  const parts = [
    ['seg-base', 'term matches', c.base],
    ['seg-breadth', 'breadth bonus', c.breadth_bonus],
    ['seg-recency', 'recency', c.recency],
  ];
  parts.forEach(([cls, label, value]) => {
    if (!value) return;
    const span = el('span');
    const swatch = el('i', cls);
    span.appendChild(swatch);
    span.appendChild(document.createTextNode(`${label} ${value.toFixed(2)}`));
    box.appendChild(span);
  });
  return box;
}

function highlight(text, terms) {
  let html = esc(text);
  (terms || []).forEach((t) => {
    if (!t || t.length < 2) return;
    const safe = t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    html = html.replace(new RegExp(`(${safe})`, 'gi'), '<mark>$1</mark>');
  });
  return html;
}

function notice(title, body) {
  const box = el('div', 'note');
  box.appendChild(el('b', null, title));
  const p = el('div');
  p.innerHTML = esc(body).replace(/'([^']+)'/g, "<code>$1</code>");
  box.appendChild(p);
  return box;
}

/* ---------- search ---------- */

function paperCard(paper, terms) {
  const card = el('div', 'paper');

  const h = el('h3');
  const link = el('a', null, paper.title);
  link.href = paper.url || '#';
  link.target = '_blank';
  link.rel = 'noopener';
  h.appendChild(link);
  card.appendChild(h);

  card.appendChild(el('div', 'line',
    [paper.id, paper.published, paper.category].filter(Boolean).join('  ·  ')));

  card.appendChild(scoreBar(paper.why, paper.score));
  card.appendChild(legend(paper.why));

  const chips = el('div', 'chips');
  (paper.why.matched || []).forEach((m) => {
    chips.appendChild(el('span', 'chip hit' + (m.credit <= 0.2 ? ' weak' : ''),
      `${m.topic} · ${m.kind} · ${m.credit.toFixed(2)}`));
  });
  (paper.concepts || []).forEach((c) => chips.appendChild(el('span', 'chip', c)));
  card.appendChild(chips);

  card.appendChild(el('div', 'why', paper.why_text));

  if (paper.abstract) {
    const abs = el('p', 'abs');
    abs.innerHTML = highlight(paper.abstract, terms);
    card.appendChild(abs);
  }

  const acts = el('div', 'acts');
  const simBtn = el('button', null, 'Find similar');
  const holder = el('div', 'neighbours');
  holder.style.display = 'none';
  simBtn.addEventListener('click', async () => {
    if (holder.style.display === 'block') {
      holder.style.display = 'none';
      simBtn.textContent = 'Find similar';
      return;
    }
    holder.style.display = 'block';
    holder.textContent = 'Looking...';
    simBtn.textContent = 'Hide similar';
    const data = await get('/api/similar', { id: paper.id, limit: 6 });
    holder.textContent = '';
    if (data.status !== 'ok') {
      holder.appendChild(notice(
        data.status === 'needs_rebuild' ? 'Similarity is turned off' : 'Not available yet',
        data.message || ''));
      return;
    }
    data.results.forEach((n) => {
      const row = el('div', 'nb');
      row.appendChild(el('span', 's', n.similarity.toFixed(3)));
      const gauge = el('span', 'g');
      const fill = el('i');
      fill.style.width = Math.max(2, Math.min(n.similarity, 1) * 100).toFixed(0) + '%';
      gauge.appendChild(fill);
      row.appendChild(gauge);
      const a = el('a', null, n.title);
      a.href = n.url || '#';
      a.target = '_blank';
      a.rel = 'noopener';
      row.appendChild(a);
      holder.appendChild(row);
    });
  });
  acts.appendChild(simBtn);
  card.appendChild(acts);
  card.appendChild(holder);
  return card;
}

async function doSearch() {
  const query = $('#q').value.trim();
  const out = $('#results');
  const meta = $('#search-meta');
  if (!query) { out.textContent = ''; meta.textContent = ''; return; }
  meta.textContent = 'Searching...';
  const data = await get('/api/search', { q: query, limit: 25 });
  out.textContent = '';
  if (data.status !== 'ok') {
    out.appendChild(notice('Could not search', data.message || ''));
    meta.textContent = '';
    return;
  }
  meta.textContent =
    `${data.matched} of ${data.searched} papers matched · showing ${data.results.length}`;
  if (!data.results.length) {
    out.appendChild(el('div', 'empty', 'Nothing matched those words.'));
    return;
  }
  data.results.forEach((p) => out.appendChild(paperCard(p, data.terms)));
}

$('#go').addEventListener('click', doSearch);
$('#q').addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });

/* ---------- saved ---------- */

async function loadSaved() {
  const out = $('#saved');
  out.textContent = 'Loading...';
  const data = await get('/api/saved');
  out.textContent = '';
  if (data.status !== 'ok' || !(data.results || []).length) {
    out.appendChild(el('div', 'empty', data.message || 'Nothing saved yet.'));
    return;
  }
  data.results.forEach((p) => {
    const card = el('div', 'paper');
    const h = el('h3');
    const a = el('a', null, p.title || p.id);
    a.href = p.url || '#';
    a.target = '_blank';
    a.rel = 'noopener';
    h.appendChild(a);
    card.appendChild(h);
    card.appendChild(el('div', 'line', `${p.id}  ·  saved ${(p.saved_at || '').slice(0, 10)}`));
    if (p.note) card.appendChild(el('div', 'why', p.note));
    const chips = el('div', 'chips');
    (p.concepts || []).forEach((c) => chips.appendChild(el('span', 'chip', c)));
    card.appendChild(chips);
    out.appendChild(card);
  });
}

/* ---------- trends ---------- */

async function loadTrends() {
  const out = $('#trends');
  out.textContent = 'Loading...';
  const data = await get('/api/trends');
  out.textContent = '';

  if (data.status !== 'ok') {
    out.appendChild(notice('Not enough data to compare', data.reason || data.message || ''));
    const w = data.window || {};
    if (w.this_week) {
      const box = el('div', 'tcols');
      [['This week', w.this_week], ['Previous week', w.previous_week]].forEach(([label, win]) => {
        const col = el('div', 'tcol');
        col.appendChild(el('h3', null, label));
        col.appendChild(el('div', 'trow')).appendChild(
          el('span', null, `${win.papers} papers`));
        col.appendChild(el('div', 'line', `${win.start} to ${win.end}`));
        box.appendChild(col);
      });
      out.appendChild(box);
    }
    return;
  }

  const box = el('div', 'tcols');
  [['Rising', data.rising, 'up'], ['Falling', data.falling, 'down'],
   ['Steady', data.steady, '']].forEach(([label, rows, cls]) => {
    const col = el('div', 'tcol');
    col.appendChild(el('h3', null, label));
    if (!rows.length) col.appendChild(el('div', 'dim', 'nothing here'));
    rows.forEach((r) => {
      const row = el('div', 'trow');
      row.appendChild(el('span', null, r.concept));
      const d = r.change === 'new' ? 'new' :
        (r.change_pct > 0 ? '+' : '') + r.change_pct + '%';
      row.appendChild(el('span', 'd ' + cls, `${r.previous} to ${r.current}  ${d}`));
      col.appendChild(row);
    });
    box.appendChild(col);
  });
  out.appendChild(box);
  if (data.note) out.appendChild(el('p', 'sub', data.note));
}

/* ---------- scoring playground ---------- */

let labTimer = null;

async function runLab() {
  const data = await get('/api/explain', {
    topics: $('#lab-topics').value,
    title: $('#lab-title').value,
    abstract: $('#lab-abstract').value,
    published: $('#lab-published').value,
  });
  const c = data.why.components || {};
  const box = $('#lab-formula');
  box.textContent = '';

  const row = (label, value, dim) => {
    const r = el('div', 'r' + (dim ? ' dim' : ''));
    r.appendChild(el('span', null, label));
    r.appendChild(el('span', null, value));
    return r;
  };

  (data.why.matched || []).forEach((m) => {
    box.appendChild(row(`${m.topic}  (${m.kind})`, '+' + m.credit.toFixed(2)));
  });
  if (!(data.why.matched || []).length) {
    box.appendChild(row('no topic matched', '0.00', true));
  }
  box.appendChild(row(
    `sum / ${data.why.topics_considered} topics, weighted 0.8`, c.base.toFixed(3)));
  box.appendChild(row(
    `breadth (${data.why.topics_matched} matched)`, '+' + (c.breadth_bonus || 0).toFixed(2)));
  box.appendChild(row(
    data.why.age_days == null ? 'recency (no date)' : `recency (${data.why.age_days} days old)`,
    '+' + (c.recency || 0).toFixed(2)));
  const total = row('score', data.score.toFixed(3));
  total.className = 'r total';
  box.appendChild(total);
  if (data.why.capped) {
    box.appendChild(row('capped at 1.0', '', true));
  }

  $('#lab-why').textContent = data.why_text;

  const chips = $('#lab-boiler');
  if (!chips.childElementCount) {
    (data.boilerplate || []).forEach((w) =>
      chips.appendChild(el('span', 'chip weak', w)));
  }
}

['#lab-topics', '#lab-title', '#lab-abstract', '#lab-published'].forEach((sel) => {
  $(sel).addEventListener('input', () => {
    clearTimeout(labTimer);
    labTimer = setTimeout(runLab, 180);
  });
});

/* ---------- health strip ---------- */

async function loadHealth() {
  const s = await get('/api/status');
  const box = $('#health');
  box.textContent = '';
  const add = (label, value) => {
    const row = el('div', 'row');
    row.appendChild(el('span', null, label));
    row.appendChild(el('span', 'v', value));
    box.appendChild(row);
  };
  add('papers', String(s.papers ?? 0));
  if (s.earliest) add('range', `${s.earliest.slice(5)} to ${s.latest.slice(5)}`);
  add('saved', String(s.saved ?? 0));

  const emb = s.embeddings || {};
  const line = el('div', 'row');
  const left = el('span');
  if (emb.available === false) {
    left.innerHTML = '<i class="dot off"></i>embeddings';
    line.appendChild(left);
    line.appendChild(el('span', 'v', 'not installed'));
  } else if (!emb.vectors) {
    left.innerHTML = '<i class="dot off"></i>embeddings';
    line.appendChild(left);
    line.appendChild(el('span', 'v', 'none'));
  } else if (emb.usable) {
    left.innerHTML = '<i class="dot ok"></i>embeddings';
    line.appendChild(left);
    line.appendChild(el('span', 'v', `${emb.vectors}`));
  } else {
    left.innerHTML = '<i class="dot warn"></i>embeddings';
    line.appendChild(left);
    line.appendChild(el('span', 'v', 'rebuild'));
  }
  box.appendChild(line);

  if (!s.papers) {
    $('#results').appendChild(notice(
      'Your library is empty',
      "Run 'research-digest fetch' to pull today's papers from arXiv, then "
      + "'research-digest embed' if you want similarity search."));
  }
}

loadHealth();
runLab();
$('#q').focus();
