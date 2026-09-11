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

function humanDate(iso) {
  if (!iso) return '';
  const d = new Date(String(iso).slice(0, 10) + 'T00:00:00');
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { day: 'numeric', month: 'short', year: 'numeric' });
}

// get() never throws. A network failure, a timeout, or a non-2xx response all
// resolve to {status: 'error', message}, exactly like a tool reporting its own
// failure — so every call site's existing `if (data.status !== 'ok')` branch
// handles "the server is down" the same way it handles "nothing matched",
// instead of leaving a "Looking..." placeholder that never resolves.
async function get(path, params, opts) {
  const url = new URL(path, location.origin);
  Object.entries(params || {}).forEach(([k, v]) => url.searchParams.set(k, v));
  const timeoutMs = (opts && opts.timeoutMs) || 8000;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: controller.signal });
    if (!res.ok) return { status: 'error', message: `Server returned ${res.status}.` };
    return await res.json();
  } catch (err) {
    const message = err && err.name === 'AbortError'
      ? 'The server did not respond in time.'
      : 'Could not reach the server. Is research-digest web still running?';
    return { status: 'error', message };
  } finally {
    clearTimeout(timer);
  }
}

const state = {
  view: 'grid',
  when: 'all',
  sort: 'score',
  query: '',
  papers: [],
  index: -1,
  topics: [],       // whatever produced the CURRENT list's ranking (query terms, or profile topics)
  settingsTopics: [],
};

// Detail-panel data, keyed by paper id. Populated by prefetchPaper() on hover
// or focus, so by the time a card is clicked the panel usually renders from
// cache instead of waiting on a round trip.
const paperCache = new Map();

function prefetchPaper(id) {
  if (!id) return null;
  // Keyed by id *and* query: the panel scores against whatever ranked the list
  // the reader came from, so the same paper legitimately has a different score
  // under a search than under the standing profile.
  const key = state.query ? id + " :: " + state.query : id;
  if (!paperCache.has(key)) {
    const params = state.query ? { id, q: state.query } : { id };
    paperCache.set(key, get('/api/paper', params));
  }
  return paperCache.get(key);
}

/* ---------------- shared bits ---------------- */

function highlight(text, terms) {
  let html = esc(text);
  (terms || []).forEach((t) => {
    if (!t || t.length < 2) return;
    const safe = t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    html = html.replace(new RegExp(`\\b(${safe})\\b`, 'gi'), '<mark>$1</mark>');
  });
  return html;
}

function notice(title, body, onRetry) {
  const box = el('div', 'note');
  box.appendChild(el('b', null, title));
  const p = el('div');
  p.innerHTML = esc(body).replace(/'([^']+)'/g, '<code>$1</code>');
  box.appendChild(p);
  if (onRetry) {
    const retry = el('button', 'retry', 'Retry');
    retry.type = 'button';
    retry.addEventListener('click', onRetry);
    box.appendChild(retry);
  }
  return box;
}

/* ---------------- the grid ---------------- */

function paperCard(paper, i) {
  // An <article>, not a <button>: the star inside it is itself a button, and a
  // button may not contain interactive content. role="button" + a keydown
  // handler keeps it operable from the keyboard without that HTML violation.
  const card = el('article', 'pcard' + (paper.read ? ' is-read' : ''));
  card.tabIndex = 0;
  card.setAttribute('role', 'button');
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
    paperCache.delete(paper.id);
  });
  card.appendChild(star);

  card.appendChild(el('div', 'meta',
    [paper.id, humanDate(paper.published), paper.category].filter(Boolean).join('  ·  ')));
  card.appendChild(el('h3', null, paper.title));
  if (paper.about) card.appendChild(el('p', 'about', paper.about));

  const tags = el('div', 'tags');
  (paper.concepts || []).slice(0, 3).forEach((c) => tags.appendChild(el('span', 'tag', c)));
  card.appendChild(tags);

  const open = () => openPanel(i);
  card.addEventListener('click', open);
  card.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
  });
  card.addEventListener('mouseenter', () => prefetchPaper(paper.id));
  card.addEventListener('focus', () => prefetchPaper(paper.id));
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
  $('#status').textContent = 'Loading…';
  const data = state.query
    ? await get('/api/search', { q: state.query, limit: 120 })
    : await get('/api/papers', { when: state.when, sort: state.sort, limit: 120 });

  if (data.status !== 'ok') {
    $('#grid').textContent = '';
    $('#grid').appendChild(notice('Could not load your library', data.message || data.status,
      loadGrid));
    $('#status').textContent = '';
    return;
  }
  state.papers = data.results;
  state.topics = data.terms || data.topics || [];
  $('#status').textContent = state.query
    ? `${data.matched} of ${data.searched} papers matched`
    : `${data.total} papers`;
  $('#status').className = 'search-status on';
  renderHeroText();
  renderGrid();
}

/* ---------------- detail panel ---------------- */

function closePanel() {
  $('#panel').classList.remove('open');
  $('#panel').setAttribute('aria-hidden', 'true');
  $('#scrim').hidden = true;
  state.index = -1;
}

function renderSimilar(container, sim) {
  const holder = el('div', 'simlist');
  container.appendChild(holder);
  if (!sim || sim.status !== 'ok') {
    const heading = {
      needs_rebuild: 'Similarity needs rebuilding',
      no_embeddings: 'No vectors built yet',
      not_found: 'Not embedded yet',
      unavailable: 'Similarity is an optional extra',
    }[sim && sim.status] || 'Similarity unavailable';
    holder.appendChild(notice(heading, (sim && sim.message) || ''));
    return;
  }
  if (!sim.results.length) {
    holder.appendChild(el('div', 'dim', 'No close neighbours yet.'));
    return;
  }
  sim.results.forEach((n) => {
    const row = el('div', 'nb');
    row.appendChild(el('span', 's', n.similarity.toFixed(2)));
    const g = el('span', 'g');
    const fill = el('i');
    fill.style.width = Math.max(2, Math.min(n.similarity, 1) * 100).toFixed(0) + '%';
    g.appendChild(fill);
    row.appendChild(g);
    const jump = el('button', null, n.title);
    jump.type = 'button';
    jump.addEventListener('click', () => {
      const at = state.papers.findIndex((p) => p.id === n.id);
      if (at >= 0) openPanel(at);
      else window.open(n.url || '#', '_blank', 'noopener');
    });
    row.appendChild(jump);
    holder.appendChild(row);
  });
}

function openScoringFor(data) {
  closePanel();
  setView('scoring');
  $('#lab-title').value = data.title || '';
  $('#lab-abstract').value = data.abstract || '';
  $('#lab-published').value = data.published || '';
  if (state.settingsTopics.length) $('#lab-topics').value = state.settingsTopics.join(', ');
  runLab();
}

async function openPanel(i) {
  const row = state.papers[i];
  if (!row) return;
  state.index = i;
  $('#panel').classList.add('open');
  $('#panel').setAttribute('aria-hidden', 'false');
  $('#scrim').hidden = false;
  $('#d-kicker').textContent = `${i + 1} of ${state.papers.length}`;

  const body = $('#d-body');
  body.textContent = '';
  body.appendChild(el('div', 'dload', 'Loading…'));

  const data = await prefetchPaper(row.id);
  if (state.index !== i) return; // the reader moved to a different paper meanwhile
  body.textContent = '';

  if (data.status !== 'ok') {
    body.appendChild(notice('Could not load this paper', data.message || data.status, () => {
      paperCache.delete(row.id);
      openPanel(i);
    }));
    return;
  }

  body.appendChild(el('h2', null, data.title));
  const authors = data.authors || [];
  const byline = authors.length > 3
    ? authors.slice(0, 3).join(', ') + ` +${authors.length - 3}`
    : authors.join(', ');
  body.appendChild(el('div', 'meta',
    [data.category, humanDate(data.published), byline].filter(Boolean).join('  ·  ')));

  if (data.about) {
    body.appendChild(el('div', 'sec', "What it's about"));
    body.appendChild(el('p', 'about-lead', data.about));
  }

  body.appendChild(el('div', 'sec', 'Abstract'));
  const abs = el('p', 'abs');
  abs.innerHTML = highlight(data.abstract || '', state.topics);
  body.appendChild(abs);

  body.appendChild(el('div', 'sec', 'Similar'));
  renderSimilar(body, data.similar);

  const btns = el('div', 'rowbtns');
  const link = el('a', null, 'Open on arXiv');
  link.href = data.url || '#';
  link.target = '_blank';
  link.rel = 'noopener';
  btns.appendChild(link);

  const save = el('button', null, data.saved ? 'Saved' : 'Save');
  save.type = 'button';
  save.addEventListener('click', async () => {
    const r = await get('/api/save', { id: data.id });
    if (r.status !== 'ok') return;
    data.saved = r.saved;
    row.saved = r.saved;
    save.textContent = r.saved ? 'Saved' : 'Save';
    paperCache.set(data.id, Promise.resolve(data));
    renderGrid();
  });
  btns.appendChild(save);

  const read = el('button', null, data.read ? 'Read' : 'Mark read');
  read.type = 'button';
  read.addEventListener('click', async () => {
    await get('/api/read', { id: data.id });
    data.read = true;
    row.read = true;
    read.textContent = 'Read';
    paperCache.set(data.id, Promise.resolve(data));
    renderGrid();
  });
  btns.appendChild(read);
  body.appendChild(btns);

  if (data.saved) {
    body.appendChild(el('div', 'sec', 'Your note'));
    const note = el('textarea', 'notefield');
    note.placeholder = 'Why you kept this one.';
    note.value = data.note || '';
    let noteTimer = null;
    note.addEventListener('input', () => {
      clearTimeout(noteTimer);
      noteTimer = setTimeout(async () => {
        const r = await get('/api/note', { id: data.id, note: note.value });
        if (r.status === 'ok') data.note = note.value;
      }, 500);
    });
    body.appendChild(note);
  }

  const foot = el('div', 'scorefoot');
  const label = data.score_basis === 'query' ? 'match' : 'topic match';
  const why = el('button', 'whylink', `${label} ${data.score.toFixed(2)} · why? →`);
  why.type = 'button';
  why.addEventListener('click', () => openScoringFor(data));
  foot.appendChild(why);
  body.appendChild(foot);
}

/* ---------------- trends ---------------- */

async function loadTrends() {
  const out = $('#view-trends');
  out.textContent = 'Loading…';
  const data = await get('/api/trends');
  out.textContent = '';

  if (data.status !== 'ok') {
    const reason = data.status === 'error'
      ? (data.message || 'Could not reach the server.')
      : 'Trends compares this week’s concepts against last week’s — come back '
        + 'after a few daily fetches and there will be something to compare.';
    out.appendChild(notice(data.status === 'error' ? 'Could not load trends'
      : 'Not enough history yet', reason, loadTrends));
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
    b.type = 'button';
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

/* ---------------- digest ---------------- */

async function loadDigest() {
  const out = $('#view-digest');
  out.textContent = 'Loading…';
  const data = await get('/api/digest');
  out.textContent = '';

  if (data.status !== 'ok') {
    out.appendChild(notice('Could not load the digest', data.message || data.status, loadDigest));
    return;
  }

  out.appendChild(el('h2', null, `Today’s digest — ${data.date}`));
  out.appendChild(el('p', 'sub',
    `The top ${data.picks.length} of ${data.considered} papers in your library, one per `
    + 'category where possible. Deterministic: the same library and topics produce the same '
    + 'picks, so this is worth skimming once a day rather than re-running.'));

  if (!data.picks.length) {
    out.appendChild(el('div', 'empty',
      'Nothing to pick from yet. Fetch some papers first.'));
    return;
  }

  const grid = el('div', 'grid');
  data.picks.forEach((p, i) => {
    const card = el('article', 'pcard');
    card.tabIndex = 0;
    card.setAttribute('role', 'button');
    card.appendChild(el('div', 'meta',
      [p.id, humanDate(p.published), p.category].filter(Boolean).join('  ·  ')));
    card.appendChild(el('h3', null, p.title));
    if (p.about) card.appendChild(el('p', 'about', p.about));
    if (p.pick_reason) card.appendChild(el('div', 'why', p.pick_reason));
    const open = () => {
      const at = state.papers.findIndex((row) => row.id === p.id);
      if (at >= 0) { setView('grid'); openPanel(at); }
      else window.open(p.url || '#', '_blank', 'noopener');
    };
    card.addEventListener('click', open);
    card.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
    grid.appendChild(card);
  });
  out.appendChild(grid);

  const foot = el('p', 'sub');
  foot.style.marginTop = '18px';
  foot.textContent = `Also written to ${data.path}.`;
  out.appendChild(foot);
}

/* ---------------- queue ---------------- */

async function loadQueue() {
  const out = $('#view-queue');
  out.textContent = 'Loading…';
  const data = await get('/api/papers', { when: 'queue', sort: 'score', limit: 120 });
  out.textContent = '';
  if (data.status !== 'ok') {
    out.appendChild(notice('Could not load the queue', data.message || data.status, loadQueue));
    return;
  }
  if (!data.results.length) {
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
  if (data.status !== 'ok') {
    $('#lab-formula').textContent = '';
    $('#lab-formula').appendChild(notice('Could not score this', data.message || data.status));
    $('#lab-why').textContent = '';
    return;
  }
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

// The hero used to be written once at startup, so it went on saying "Everything
// you have fetched" while a search was showing 46 of 1,376 papers.
function renderHeroText() {
  const hero = $('#hero');
  const old = hero.querySelector('.hero-text');
  if (old) old.remove();
  const wrap = el('div', 'hero-text');
  if (state.query) {
    wrap.appendChild(el('h1', null, `Results for “${state.query}”`));
    wrap.appendChild(el('p', null,
      'Ranked by how much of your query each paper covers, best first. Open any '
      + 'paper for what it’s about and its nearest neighbours. Esc clears the search.'));
  } else {
    wrap.appendChild(el('h1', null, 'Your library'));
    wrap.appendChild(el('p', null,
      'Everything you have fetched, best match first. Open any paper for what it’s '
      + 'about and its nearest neighbours. Press / to search, 1 to 5 to switch views.'));
  }
  hero.insertBefore(wrap, hero.firstChild);
}

/* ---------------- views ---------------- */

const VIEWS = ['grid', 'digest', 'trends', 'queue', 'scoring'];

function setView(name) {
  state.view = name;
  document.querySelectorAll('.view-tab').forEach((b) =>
    b.classList.toggle('active', b.dataset.view === name));
  VIEWS.forEach((v) => { $('#view-' + v).hidden = v !== name; });
  $('#filters').style.visibility = name === 'grid' ? '' : 'hidden';
  // The "46 of 1376 papers matched" line describes the grid. It used to stay put
  // when you switched to Digest or Trends, captioning a list it had not counted.
  $('#status').hidden = name !== 'grid';
  if (name === 'digest') loadDigest();
  if (name === 'trends') loadTrends();
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
  $('#status').textContent = 'Asking arXiv for today’s papers… (can take a couple of minutes)';
  // arXiv is rate-limited on purpose (one request per category, 3s apart), so
  // this can run well past a default request timeout — give it room.
  const r = await get('/api/refresh', {}, { timeoutMs: 240000 });
  btn.disabled = false;
  btn.textContent = 'Fetch';
  $('#status').textContent = r.message || r.status;
  $('#status').className = 'search-status on';
  if (r.status === 'ok') { paperCache.clear(); await boot(); }
});

$('#panel-x').addEventListener('click', closePanel);
$('#scrim').addEventListener('click', closePanel);

document.addEventListener('keydown', (e) => {
  const typing = /^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName);
  if (e.key === '/' && !typing) { e.preventDefault(); $('#q').focus(); return; }
  if (typing) return;
  if (e.key === 'Escape') return closePanel();
  if (['1', '2', '3', '4', '5'].includes(e.key)) {
    return setView(VIEWS[Number(e.key) - 1]);
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
  if (s.status !== 'ok') {
    const hero = $('#hero');
    hero.textContent = '';
    hero.appendChild(notice('Could not reach the server', s.message || '', boot));
    return;
  }

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
  state.settingsTopics = s.topics || [];

  const hero = $('#hero');
  hero.textContent = '';
  if (!s.papers) {
    hero.appendChild(notice('Your library is empty',
      "Run 'research-digest fetch' to pull today's papers from arXiv, then "
      + "'research-digest embed' if you want similarity search."));
    return;
  }
  renderHeroText();
  const sug = el('div', 'suggestion-grid');
  (s.topics || []).slice(0, 7).forEach((t) => {
    const b = el('button', 'suggestion', t);
    b.type = 'button';
    b.addEventListener('click', () => { $('#q').value = t; doSearch(); });
    sug.appendChild(b);
  });
  hero.appendChild(sug);

  loadGrid();
}

boot();
