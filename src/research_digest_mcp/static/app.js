'use strict';

const $ = (s) => document.querySelector(s);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};
const esc = (s) => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

async function get(path, params) {
  const url = new URL(path, location.origin);
  Object.entries(params || {}).forEach(([k, v]) => url.searchParams.set(k, v));
  return (await fetch(url)).json();
}

const state = {
  view: 'grid',
  when: 'all',
  sort: 'score',
  query: '',
  papers: [],
  index: -1,
  topics: [],
};

/* ---------------- shared bits ---------------- */

function scoreBar(why, score) {
  const c = (why && why.components) || {};
  const wrap = el('div', 'score');
  wrap.appendChild(el('span', 'num', score.toFixed(3)));
  const bar = el('div', 'bar');
  const total = Math.max(score, 0.0001);
  [['seg-base', c.base], ['seg-breadth', c.breadth_bonus], ['seg-recency', c.recency]]
    .forEach(([cls, v]) => {
      if (!v) return;
      const seg = el('i', cls);
      seg.style.width = (Math.min(v / total, 1) * 100).toFixed(1) + '%';
      bar.appendChild(seg);
    });
  wrap.appendChild(bar);
  return wrap;
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
  p.innerHTML = esc(body).replace(/'([^']+)'/g, '<code>$1</code>');
  box.appendChild(p);
  return box;
}

/* ---------------- the grid ---------------- */

function paperCard(paper, i) {
  const card = el('button', 'pcard' + (paper.read ? ' is-read' : ''));
  card.type = 'button';
  card.dataset.i = String(i);

  const star = el('button', 'star' + (paper.saved ? ' on' : ''), paper.saved ? '★' : '☆');
  star.type = 'button';
  star.title = paper.saved ? 'Remove from saved' : 'Save';
  star.addEventListener('click', async (e) => {
    e.stopPropagation();
    const r = await get('/api/save', { id: paper.id });
    if (r.status !== 'ok') return;
    paper.saved = r.saved;
    star.className = 'star' + (r.saved ? ' on' : '');
    star.textContent = r.saved ? '★' : '☆';
  });
  card.appendChild(star);

  card.appendChild(el('div', 'meta',
    [paper.id, paper.published, paper.category].filter(Boolean).join('  ·  ')));
  card.appendChild(el('h3', null, paper.title));
  card.appendChild(scoreBar(paper.why, paper.score));
  if (paper.why_text) card.appendChild(el('div', 'why', paper.why_text));

  const tags = el('div', 'tags');
  (paper.concepts || []).slice(0, 4).forEach((c) => tags.appendChild(el('span', 'tag', c)));
  card.appendChild(tags);

  card.addEventListener('click', () => openPanel(i));
  return card;
}

function renderGrid() {
  const grid = $('#grid');
  const empty = $('#grid-empty');
  grid.textContent = '';
  if (!state.papers.length) {
    empty.hidden = false;
    empty.textContent = state.query
      ? `Nothing matched ${state.query}.`
      : 'No papers in this view yet.';
    return;
  }
  empty.hidden = true;
  state.papers.forEach((p, i) => grid.appendChild(paperCard(p, i)));
}

async function loadGrid() {
  $('#status').textContent = 'Loading...';
  const data = state.query
    ? await get('/api/search', { q: state.query, limit: 120 })
    : await get('/api/papers', { when: state.when, sort: state.sort, limit: 120 });

  if (data.status !== 'ok') {
    $('#grid').textContent = '';
    $('#grid').appendChild(notice('Could not load', data.message || data.status));
    $('#status').textContent = '';
    return;
  }
  state.papers = data.results;
  state.topics = data.terms || data.topics || [];
  $('#status').textContent = state.query
    ? `${data.matched} of ${data.searched} papers matched`
    : `${data.total} papers`;
  $('#status').className = 'search-status on';
  renderGrid();
}

/* ---------------- detail panel ---------------- */

function closePanel() {
  $('#panel').classList.remove('open');
  $('#panel').setAttribute('aria-hidden', 'true');
  $('#scrim').hidden = true;
  state.index = -1;
}

async function openPanel(i) {
  const paper = state.papers[i];
  if (!paper) return;
  state.index = i;
  $('#panel').classList.add('open');
  $('#panel').setAttribute('aria-hidden', 'false');
  $('#scrim').hidden = false;
  $('#d-kicker').textContent = `${i + 1} of ${state.papers.length}`;

  const body = $('#d-body');
  body.textContent = '';
  body.appendChild(el('h2', null, paper.title));
  body.appendChild(el('div', 'meta',
    [paper.id, paper.published, paper.category].filter(Boolean).join('  ·  ')));
  body.appendChild(scoreBar(paper.why, paper.score));
  if (paper.why_text) body.appendChild(el('div', 'why', paper.why_text));

  if (paper.abstract) {
    const abs = el('p', 'abs');
    abs.innerHTML = highlight(paper.abstract, state.topics);
    body.appendChild(abs);
  }

  const btns = el('div', 'rowbtns');
  const link = el('a', null, 'Open on arXiv');
  link.href = paper.url || '#';
  link.target = '_blank';
  link.rel = 'noopener';
  btns.appendChild(link);

  const save = el('button', null, paper.saved ? 'Saved' : 'Save');
  save.addEventListener('click', async () => {
    const r = await get('/api/save', { id: paper.id });
    if (r.status !== 'ok') return;
    paper.saved = r.saved;
    save.textContent = r.saved ? 'Saved' : 'Save';
    renderGrid();
  });
  btns.appendChild(save);

  const read = el('button', null, paper.read ? 'Read' : 'Mark read');
  read.addEventListener('click', async () => {
    await get('/api/read', { id: paper.id });
    paper.read = true;
    read.textContent = 'Read';
    renderGrid();
  });
  btns.appendChild(read);
  body.appendChild(btns);

  if (paper.saved) {
    body.appendChild(el('div', 'sec', 'Your note'));
    const note = el('textarea', 'notefield');
    note.placeholder = 'Why you kept this one.';
    note.value = paper.note || '';
    let noteTimer = null;
    note.addEventListener('input', () => {
      clearTimeout(noteTimer);
      noteTimer = setTimeout(async () => {
        const r = await get('/api/note', { id: paper.id, note: note.value });
        if (r.status === 'ok') paper.note = note.value;
      }, 500);
    });
    body.appendChild(note);
  }

  body.appendChild(el('div', 'sec', 'Similar papers'));
  const holder = el('div');
  holder.textContent = 'Looking...';
  body.appendChild(holder);

  const data = await get('/api/similar', { id: paper.id, limit: 6 });
  holder.textContent = '';
  if (data.status !== 'ok') {
    const heading = {
      needs_rebuild: 'Similarity needs rebuilding',
      no_embeddings: 'No vectors built yet',
      unavailable: 'Similarity is an optional extra',
    }[data.status] || 'Similarity unavailable';
    holder.appendChild(notice(heading, data.message || ''));
    return;
  }
  data.results.forEach((n) => {
    const row = el('div', 'nb');
    row.appendChild(el('span', 's', n.similarity.toFixed(3)));
    const g = el('span', 'g');
    const fill = el('i');
    fill.style.width = Math.max(2, Math.min(n.similarity, 1) * 100).toFixed(0) + '%';
    g.appendChild(fill);
    row.appendChild(g);
    const jump = el('button', null, n.title);
    jump.addEventListener('click', () => {
      const at = state.papers.findIndex((p) => p.id === n.id);
      if (at >= 0) openPanel(at);
      else window.open(n.url || '#', '_blank', 'noopener');
    });
    row.appendChild(jump);
    holder.appendChild(row);
  });
}

/* ---------------- trends ---------------- */

async function loadTrends() {
  const out = $('#view-digest');
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
        col.appendChild(el('div', 'trow')).appendChild(el('span', null, `${win.papers} papers`));
        col.appendChild(el('div', 'dim', `${win.start} to ${win.end}`));
        box.appendChild(col);
      });
      out.appendChild(box);
    }
    // Crossing does not depend on the week-over-week comparison, so it still
    // has something to say when that comparison cannot be made.
    renderCrossing(out, data.crossing);
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
      const d = r.change === 'new' ? 'new'
        : (r.change_pct > 0 ? '+' : '') + r.change_pct + '%';
      row.appendChild(el('span', 'd ' + cls, `${r.previous} to ${r.current}  ${d}`));
      col.appendChild(row);
    });
    box.appendChild(col);
  });
  out.appendChild(box);
  if (data.note) out.appendChild(el('p', 'sub', data.note));
  renderCrossing(out, data.crossing);
}

/* Concepts turning up somewhere they never have before. */
function renderCrossing(out, crossing) {
  if (!crossing || !crossing.length) return;
  const col = el('div', 'tcol');
  col.style.marginTop = '18px';
  col.appendChild(el('h3', null, 'Crossing over'));
  col.appendChild(el('p', 'sub',
    'An idea showing up in a field it has not appeared in before, which is usually '
    + 'worth more attention than the same idea appearing where it always does.'));
  crossing.forEach((c) => {
    const row = el('div', 'crossrow');
    const b = el('button', null, c.title);
    b.style.cssText = 'border:0;background:none;text-align:left;cursor:pointer;font:inherit;color:inherit;padding:0';
    b.addEventListener('click', () => {
      const at = state.papers.findIndex((p) => p.id === c.id);
      if (at >= 0) { setView('grid'); openPanel(at); }
    });
    row.appendChild(b);
    row.appendChild(el('span', 'n', c.note));
    col.appendChild(row);
  });
  out.appendChild(col);
}

/* ---------------- queue ---------------- */

async function loadQueue() {
  const out = $('#view-queue');
  out.textContent = 'Loading...';
  const data = await get('/api/papers', { when: 'queue', sort: 'score', limit: 120 });
  out.textContent = '';
  if (!data.results || !data.results.length) {
    out.appendChild(el('div', 'empty',
      'Nothing queued. Star a paper in the grid and it lands here until you mark it read.'));
    return;
  }
  state.papers = data.results;
  const grid = el('div', 'grid');
  data.results.forEach((p, i) => grid.appendChild(paperCard(p, i)));
  out.appendChild(grid);
}

/* ---------------- scoring lab ---------------- */

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
  (data.why.matched || []).forEach((m) =>
    box.appendChild(row(`${m.topic}  (${m.kind})`, '+' + m.credit.toFixed(2))));
  if (!(data.why.matched || []).length) box.appendChild(row('no topic matched', '0.00', true));
  box.appendChild(row(`sum / ${data.why.topics_considered} topics, weighted 0.8`,
    c.base.toFixed(3)));
  box.appendChild(row(`breadth (${data.why.topics_matched} matched)`,
    '+' + (c.breadth_bonus || 0).toFixed(2)));
  box.appendChild(row(
    data.why.age_days == null ? 'recency (no date)' : `recency (${data.why.age_days} days old)`,
    '+' + (c.recency || 0).toFixed(2)));
  const total = row('score', data.score.toFixed(3));
  total.className = 'r total';
  box.appendChild(total);
  $('#lab-why').textContent = data.why_text;

  const chips = $('#lab-boiler');
  if (!chips.childElementCount) {
    (data.boilerplate || []).forEach((w) => chips.appendChild(el('span', 'tag', w)));
  }
}

/* ---------------- views ---------------- */

function setView(name) {
  state.view = name;
  document.querySelectorAll('.view-tab').forEach((b) =>
    b.classList.toggle('active', b.dataset.view === name));
  ['grid', 'digest', 'queue', 'scoring'].forEach((v) => {
    $('#view-' + v).hidden = v !== name;
  });
  $('#filters').style.visibility = name === 'grid' ? '' : 'hidden';
  if (name === 'digest') loadTrends();
  if (name === 'queue') loadQueue();
  if (name === 'scoring') runLab();
}

/* ---------------- wiring ---------------- */

document.querySelectorAll('.view-tab').forEach((btn) =>
  btn.addEventListener('click', () => setView(btn.dataset.view)));

document.querySelectorAll('#filters .chip').forEach((chip) => {
  chip.addEventListener('click', () => {
    document.querySelectorAll('#filters .chip').forEach((c) =>
      c.classList.toggle('active', c === chip));
    state.when = chip.dataset.when;
    state.query = '';
    $('#q').value = '';
    loadGrid();
  });
});

$('#sort').addEventListener('click', () => {
  state.sort = state.sort === 'score' ? 'date' : 'score';
  $('#sort').textContent = 'Sort: ' + (state.sort === 'score' ? 'best match' : 'newest');
  loadGrid();
});

function doSearch() {
  state.query = $('#q').value.trim();
  setView('grid');
  loadGrid();
}
$('#go').addEventListener('click', doSearch);
$('#q').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') doSearch();
  if (e.key === 'Escape') { $('#q').value = ''; state.query = ''; loadGrid(); }
});

$('#refresh').addEventListener('click', async () => {
  const btn = $('#refresh');
  btn.disabled = true;
  btn.textContent = 'Fetching...';
  $('#status').textContent = 'Asking arXiv for today’s papers...';
  const r = await get('/api/refresh');
  btn.disabled = false;
  btn.textContent = 'Fetch';
  $('#status').textContent = r.message || r.status;
  $('#status').className = 'search-status on';
  if (r.status === 'ok') { await boot(); }
});

$('#panel-x').addEventListener('click', closePanel);
$('#scrim').addEventListener('click', closePanel);

document.addEventListener('keydown', (e) => {
  const typing = /^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName);
  if (e.key === '/' && !typing) { e.preventDefault(); $('#q').focus(); return; }
  if (typing) return;
  if (e.key === 'Escape') return closePanel();
  if (['1', '2', '3', '4'].includes(e.key)) {
    return setView(['grid', 'digest', 'queue', 'scoring'][Number(e.key) - 1]);
  }
  if (state.index >= 0) {
    if (e.key === 'j' || e.key === 'ArrowDown') {
      e.preventDefault();
      openPanel(Math.min(state.index + 1, state.papers.length - 1));
    }
    if (e.key === 'k' || e.key === 'ArrowUp') {
      e.preventDefault();
      openPanel(Math.max(state.index - 1, 0));
    }
  }
});

['#lab-topics', '#lab-title', '#lab-abstract', '#lab-published'].forEach((sel) => {
  $(sel).addEventListener('input', () => {
    clearTimeout(labTimer);
    labTimer = setTimeout(runLab, 180);
  });
});

/* ---------------- boot ---------------- */

async function boot() {
  const s = await get('/api/status');
  const meta = $('#header-meta');
  meta.textContent = '';
  const add = (label, value) => {
    const span = el('span');
    span.appendChild(document.createTextNode(label + ' '));
    span.appendChild(el('b', null, value));
    meta.appendChild(span);
  };
  add('papers', String(s.papers ?? 0));
  add('saved', String(s.saved ?? 0));
  const emb = s.embeddings || {};
  add('vectors', emb.available === false ? 'not installed'
    : emb.usable ? String(emb.vectors || 0) : 'rebuild');

  const hero = $('#hero');
  hero.textContent = '';
  if (!s.papers) {
    hero.appendChild(notice('Your library is empty',
      "Run 'research-digest fetch' to pull today's papers from arXiv, then "
      + "'research-digest embed' if you want similarity search."));
    return;
  }
  hero.appendChild(el('h1', null, 'Your library'));
  hero.appendChild(el('p', null,
    'Everything you have fetched, best match first. Open any paper for its abstract and '
    + 'its nearest neighbours. Press / to search, 1 to 4 to switch views.'));
  const sug = el('div', 'suggestion-grid');
  (s.topics || []).slice(0, 7).forEach((t) => {
    const b = el('button', 'suggestion', t);
    b.addEventListener('click', () => { $('#q').value = t; doSearch(); });
    sug.appendChild(b);
  });
  hero.appendChild(sug);

  loadGrid();
}

boot();
