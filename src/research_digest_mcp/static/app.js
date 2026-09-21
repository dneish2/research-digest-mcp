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
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

// "today 10:14" beats "today", and "17 Sep 10:14" beats "6 days ago". The old
// header could say "last fetch today" from one minute past midnight until the
// next midnight, which is the entire question it was there to answer.
function humanStamp(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const time = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  const now = new Date();
  const days = Math.round(
    (new Date(now.getFullYear(), now.getMonth(), now.getDate()) -
     new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
  if (days === 0) return { short: `today ${time}`, days, full: d.toLocaleString() };
  if (days === 1) return { short: `yesterday ${time}`, days, full: d.toLocaleString() };
  return {
    short: `${d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })} ${time}`,
    days,
    full: d.toLocaleString(),
  };
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

async function post(path, body, opts) {
  const timeoutMs = (opts && opts.timeoutMs) || 15000;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
      signal: controller.signal,
    });
    const data = await res.json().catch(() => ({
      status: 'error', message: `Server returned ${res.status}.`,
    }));
    return data;
  } catch (err) {
    return { status: 'error', message: 'Could not reach the server.' };
  } finally {
    clearTimeout(timer);
  }
}

const state = {
  view: 'grid',
  when: 'all',
  sort: 'score',
  query: '',
  mode: 'search',   // 'search' (keywords) or 'ask' (a question)
  papers: [],
  index: -1,
  topics: [],       // whatever produced the CURRENT list's ranking
  settingsTopics: [],
  hasMore: false,
  corrections: [],   // query words in no paper, with the nearest word that is
  correctedFrom: '', // what was typed, when it is not what was searched for
};

// Detail-panel data, keyed by paper id AND the query that ranked the list,
// because the panel scores against whatever the reader came from.
const paperCache = new Map();
const cacheKey = (id) => (state.query ? id + ' :: ' + state.query : id);

function prefetchPaper(id) {
  if (!id) return null;
  const key = cacheKey(id);
  if (!paperCache.has(key)) {
    const params = state.query ? { id, q: state.query } : { id };
    paperCache.set(key, get('/api/paper', params));
  }
  return paperCache.get(key);
}

// Every cached copy of one paper, whichever query ranked it. Deleting only
// paperCache[id] left the search-ranked copy behind, so starring a paper from
// a search result showed it unstarred again the moment you reopened it.
function forgetPaper(id) {
  Array.from(paperCache.keys())
    .filter((k) => k === id || k.startsWith(id + ' :: '))
    .forEach((k) => paperCache.delete(k));
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

function notice(title, body, onRetry, retryLabel) {
  const box = el('div', 'note');
  box.appendChild(el('b', null, title));
  const p = el('div');
  p.innerHTML = esc(body).replace(/'([^']+)'/g, '<code>$1</code>');
  box.appendChild(p);
  if (onRetry) {
    const retry = el('button', 'retry', retryLabel || 'Retry');
    retry.type = 'button';
    retry.addEventListener('click', onRetry);
    box.appendChild(retry);
  }
  return box;
}

let toastTimer = null;
function toast(message, kind) {
  const box = $('#toast');
  box.textContent = message;
  box.className = 'toast' + (kind ? ' toast-' + kind : '');
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 4200);
}

function chipRow(items, onPick, cls) {
  const row = el('div', 'chips');
  items.forEach((text) => {
    const b = el('button', cls || 'suggestion', text);
    b.type = 'button';
    b.addEventListener('click', () => onPick(text));
    row.appendChild(b);
  });
  return row;
}

/* ---------------- the grid ---------------- */

const TIER_TITLE = {
  core: 'Core: the subject you work in',
  complementary: 'Complementary: an adjacent lane',
  stretch: 'Stretch: another field, fetched for its methods',
};

function paperCard(paper, i) {
  const card = el('article', 'pcard' + (paper.read ? ' is-read' : ''));
  card.tabIndex = 0;
  card.setAttribute('role', 'button');
  card.dataset.i = String(i);

  const star = el('button', 'star' + (paper.saved ? ' on' : ''), paper.saved ? '★' : '☆');
  star.type = 'button';
  star.title = paper.saved ? 'Remove from saved' : 'Save';
  star.setAttribute('aria-label', star.title);
  star.addEventListener('click', async (e) => {
    e.stopPropagation();
    const r = await get('/api/save', { id: paper.id });
    if (r.status !== 'ok') return toast(r.message || 'Could not save that.', 'bad');
    paper.saved = r.saved;
    star.className = 'star' + (r.saved ? ' on' : '');
    star.textContent = r.saved ? '★' : '☆';
    star.title = r.saved ? 'Remove from saved' : 'Save';
    forgetPaper(paper.id);
    toast(r.saved ? 'Saved to your shelf.' : 'Removed from saved.');
  });
  card.appendChild(star);

  const meta = el('div', 'meta');
  meta.appendChild(el('span', null, paper.id));
  if (paper.published) meta.appendChild(el('span', null, humanDate(paper.published)));
  if (paper.category) meta.appendChild(el('span', null, paper.category));
  // Which tier of your profile brought this in. The tiers decide how deep the
  // fetch goes and how the paper is matched, and nothing on screen said so.
  if (paper.tier) {
    const badge = el('span', 'tier tier-' + paper.tier, paper.tier);
    badge.title = TIER_TITLE[paper.tier] || paper.tier;
    meta.appendChild(badge);
  }
  card.appendChild(meta);

  const title = el('h3');
  if (state.query) title.innerHTML = highlight(paper.title, state.topics);
  else title.textContent = paper.title;
  card.appendChild(title);

  if (paper.about) {
    const about = el('p', 'about');
    if (state.query) about.innerHTML = highlight(paper.about, state.topics);
    else about.textContent = paper.about;
    card.appendChild(about);
  }

  if (paper.why_text) card.appendChild(el('div', 'why', paper.why_text));

  const tags = el('div', 'tags');
  (paper.concepts || []).slice(0, 6).forEach((c) => {
    // Concepts were decoration. They are the best index this library has into
    // itself, so they are now the fastest way to move sideways through it.
    const tag = el('button', 'tag tag-live', c);
    tag.type = 'button';
    tag.title = `Search for "${c}"`;
    tag.addEventListener('click', (e) => {
      e.stopPropagation();
      $('#q').value = c;
      runSearch('search');
    });
    tags.appendChild(tag);
  });
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

function renderCards(container, rows, offset) {
  rows.forEach((p, i) => container.appendChild(paperCard(p, (offset || 0) + i)));
}

function renderGrid() {
  const grid = $('#grid');
  const empty = $('#grid-empty');
  grid.textContent = '';
  if (!state.papers.length) {
    empty.hidden = false;
    empty.textContent = '';
    empty.appendChild(el('p', null, state.query
      ? `None of your ${state.searched || 0} papers mention “${state.query}”.`
      : 'No papers in this view yet.'));
    if (state.query) {
      // Say what was actually checked. "Nothing matched" over a library of
      // 1,443 reads as a broken search; "none of your 1,443 papers mention
      // this" reads as an answer, and it is the same fact.
      empty.appendChild(el('p', 'dim',
        'Every paper you hold was checked, against the title, the abstract and the '
        + 'concept tags. This is your shelf being thin on the subject, not the '
        + 'search giving up early.'));
      // A library search that finds nothing is not the end of the road, and
      // pretending it was is the single biggest thing this tool got wrong: it
      // could not reach past what it already held.
      const go = el('button', 'btn', 'Search arXiv for it instead');
      go.type = 'button';
      go.addEventListener('click', () => searchArxiv(state.query));
      empty.appendChild(go);
      empty.appendChild(renderGapNote());
    }
    return;
  }
  empty.hidden = true;

  // Honest about a thin answer. Nine papers each matching a third of the query
  // used to look exactly like nine good hits.
  if (state.weakNote) {
    const warn = el('div', 'scopebar');
    warn.appendChild(el('b', null, 'These are the closest things you hold, not matches.'));
    warn.appendChild(el('span', null, state.weakNote));
    const go = el('button', 'btn', `Ask arXiv for “${state.query}”`);
    go.type = 'button';
    go.style.alignSelf = 'flex-start';
    go.addEventListener('click', () => searchArxiv(state.query));
    warn.appendChild(go);
    const gap = renderGapNote();
    if (gap) warn.appendChild(gap);
    grid.appendChild(warn);
  }
  renderCards(grid, state.papers, 0);

  // A second answer to a different question. The list above is what contains
  // your words; this is what sits next to it. Kept visibly separate, because
  // quietly blending them would mean a result you cannot explain.
  const rel = state.related;
  if (rel && rel.status === 'ok' && rel.results.length) {
    const band = el('div', 'relatedband');
    const head = el('div', 'relatedband-head');
    head.appendChild(el('h3', null, 'Close to these, without using your words'));
    head.appendChild(el('span', 'pill', `${rel.results.length} found by meaning`));
    band.appendChild(head);
    band.appendChild(el('p', 'sub', rel.how || ''));
    grid.appendChild(band);
    const offset = state.papers.length;
    state.papers = state.papers.concat(rel.results);
    renderCards(grid, rel.results, offset);
  } else if (rel && rel.status === 'unavailable' && state.query) {
    const band = el('div', 'relatedband');
    band.appendChild(el('h3', null, 'Searching by meaning is switched off'));
    band.appendChild(el('p', 'sub',
      'With vectors built, a thin keyword result can be widened to the papers '
      + 'sitting next to it, which is how a search for a word your library barely '
      + 'uses still finds the shelf it belongs to. ' + (rel.message || '')));
    grid.appendChild(band);
  }

  if (state.hasMore) {
    const more = el('button', 'load-more', 'Load more papers');
    more.type = 'button';
    more.addEventListener('click', () => {
      more.disabled = true;
      more.textContent = 'Loading…';
      loadGrid(true);
    });
    grid.appendChild(more);
  }
}

// A month-shaped hole in the library explains a failed search better than any
// amount of ranking work, and nothing on any screen used to mention it. A
// July paper could not be found in a library holding 402 papers from May, 369
// from June and none at all from July.
function renderGapNote() {
  const cov = state.coverage;
  if (!cov || !(cov.gaps || []).length) return null;
  const box = el('div', 'gapnote');
  const months = cov.gaps.map(prettyMonth);
  box.appendChild(el('b', null,
    months.length === 1
      ? `You hold nothing published in ${months[0]}.`
      : `You hold nothing published in ${months.length} months: ${months.join(', ')}.`));
  box.appendChild(el('span', null,
    'A fetch always starts from the newest paper, so a month you missed stays '
    + 'missed. Searching cannot find what was never fetched.'));
  const fill = el('button', 'btn-ghost', `Fetch ${months[0]} now`);
  fill.type = 'button';
  fill.addEventListener('click', () => backfill(cov.gaps[0], fill));
  box.appendChild(fill);
  return box;
}

function prettyMonth(key) {
  const d = new Date(key + '-02T00:00:00');
  return Number.isNaN(d.getTime()) ? key
    : d.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
}

function lastDayOf(key) {
  const [y, m] = key.split('-').map(Number);
  return new Date(y, m, 0).toISOString().slice(0, 10);
}

/* A month is the longest thing this tool does: one real month of cs is tens of
   thousands of records. It used to be a held-open request behind a disabled
   button, which is the same picture whether it is working or hung. It now runs
   as the same background job as any other fetch, and reports itself in the
   connection panel, where a fetch belongs. */
async function backfill(month, button) {
  button.disabled = true;
  button.textContent = `Fetching ${prettyMonth(month)}…`;
  setView('grid');
  await renderConn('#conn-grid');
  const box = $('#conn-grid').querySelector('.conn-box');
  const started = await get('/api/fetch/start',
    { since: month + '-01', until: lastDayOf(month) }, { timeoutMs: 20000 });
  button.disabled = false;
  button.textContent = `Fetch ${prettyMonth(month)} now`;
  if (started.status !== 'ok') {
    return toast(started.message || 'Could not start the backfill.', 'bad');
  }
  if (box) box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  watchFetch(box);
}

const PAGE_SIZE = 120;

async function loadGrid(append, extra) {
  const offset = append ? state.papers.length : 0;
  $('#status').textContent = append ? 'Loading more…' : 'Loading…';
  const data = state.query
    ? await get('/api/search',
      Object.assign({ q: state.query, limit: PAGE_SIZE }, extra || {}))
    : await get('/api/papers',
      { when: state.when, sort: state.sort, limit: PAGE_SIZE, offset });

  if (data.status !== 'ok') {
    if (!append) {
      $('#grid').textContent = '';
      $('#grid').appendChild(notice('Could not load your library',
        data.message || data.status, () => loadGrid(false)));
    }
    $('#status').textContent = '';
    return;
  }
  state.papers = append ? state.papers.concat(data.results) : data.results;
  state.topics = data.terms || data.topics || [];
  state.hasMore = Boolean(data.has_more);
  state.weakNote = data.weak ? data.weak_note : '';
  state.related = append ? state.related : (data.related_papers || null);
  if (data.coverage) state.coverage = data.coverage;
  state.searched = data.searched || data.total || 0;
  state.corrections = data.corrections || [];
  state.correctedFrom = data.corrected_from || '';
  // The server may have searched for something other than what was typed, and
  // the box has to agree with the results or the reader is reading one query
  // and looking at another.
  if (state.correctedFrom && data.query) {
    state.query = data.query;
    $('#q').value = data.query;
  }
  $('#status').textContent = state.query
    ? `${data.matched} of your ${data.searched} papers mention this`
    : `Showing ${state.papers.length} of ${data.total} papers`;
  $('#status').className = 'search-status on';
  renderHeroText();
  renderGrid();
}

/* ---------------- the connection to arXiv ---------------- */

/* Written after a report that read, in full: "how am I getting rate limited
   for 1 search, don't we have two APIs available here?"

   We do, and the button was wired to the one that was refusing. Everything in
   here exists so that question is answerable from the screen: which service is
   being used, what state each is in, what window is about to be asked for, and
   what came back. A fetch that cannot say what it did cannot be trusted when
   it says nothing came back. */

function humanWait(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.round(s / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

function endpointPill(ep, inUse) {
  const pill = el('div', `epill epill-${ep.state}${inUse ? ' epill-live' : ''}`);
  const top = el('div', 'epill-top');
  top.appendChild(el('b', null, ep.label));
  top.appendChild(el('span', 'epill-state',
    ep.state === 'cooling' ? `refusing, ${humanWait(ep.remaining)} left` : 'ready'));
  pill.appendChild(top);
  pill.appendChild(el('div', 'epill-host', ep.host));
  pill.appendChild(el('div', 'epill-note', ep.note));
  if (inUse) pill.appendChild(el('div', 'epill-using', 'in use for this fetch'));
  return pill;
}

/* One line saying whether anything needs attention, for the top of a screen
   whose job is papers rather than plumbing. */
function connSummary(plan) {
  const lib = plan.library || {};
  const search = plan.endpoints.search;
  const bits = [];
  if (search.state === 'cooling') {
    bits.push(`search API refusing for ${humanWait(search.remaining)}, `
      + 'harvest feed answering');
  }
  if ((lib.holes || []).length) {
    bits.push(`${lib.holes.length} days missing in the last ${lib.lookback}`);
  }
  if (!bits.length) {
    bits.push(`both arXiv services ready, newest paper ${humanDate(lib.newest_published)}`);
  }
  return bits.join(' · ');
}

/* The panel. Same component on Papers and on Digest, because a reader who
   pressed Fetch on one of them and got nothing needs the same four facts.
   Collapsed on Papers, where the job of the screen is papers. */
function connectionPanel(plan, collapsed) {
  const box = el('section', 'conn-box');
  const lib = plan.library || {};

  // Collapsed renders the same body inside a details, so there is exactly one
  // description of the connection and one place to fix it.
  let host = box;
  if (collapsed) {
    const wrap = el('details', 'conn-details');
    const sum = el('summary', 'conn-summary');
    sum.appendChild(el('span', 'conn-dot conn-dot-'
      + (plan.endpoints.search.state === 'cooling' ? 'warn' : 'ok')));
    sum.appendChild(el('span', null, connSummary(plan)));
    wrap.appendChild(sum);
    box.appendChild(wrap);
    host = wrap;
  }

  const head = el('div', 'conn-head');
  head.appendChild(el('h3', null, 'arXiv connection'));
  if (lib.behind_days != null) {
    head.appendChild(el('span', 'conn-edge',
      `newest paper ${humanDate(lib.newest_published)}, ${lib.behind_days} days back`));
  }
  host.appendChild(head);

  // Two services, side by side, each with its own state. Collapsing them into
  // one "arXiv" status is what let a block on the one we were not obliged to
  // use read as arXiv being down.
  const pills = el('div', 'epills');
  ['harvest', 'search'].forEach((name) => {
    pills.appendChild(endpointPill(plan.endpoints[name], plan.route === name));
  });
  host.appendChild(pills);

  // The announcement delay, said once, where the confusion happens. A library
  // whose newest paper is three days old looks broken and is not.
  host.appendChild(el('p', 'conn-lag', plan.lag_note));

  /* Two kinds of empty day, kept apart on purpose. A hole is ours to fix; a
     pending day is arXiv's announcement delay and nothing can be done about
     it. Showing them as one list would have flagged an ordinary Friday as
     missing data, and gap warnings you learn to ignore are worse than none. */
  const holes = lib.holes || [];
  if (holes.length) {
    const note = el('div', 'conn-gaps');
    note.appendChild(el('b', null,
      `You hold nothing published on ${holes.length} of the last ${lib.lookback} days, `
      + `starting ${humanDate(holes[0])}.`));
    note.appendChild(el('span', null,
      'These are real holes: you hold papers published after them, so they were '
      + 'reachable. A fetch anchored to your newest paper would step over them '
      + 'every time, so this one starts at the oldest instead.'));
    host.appendChild(note);
  }
  const pending = lib.pending || [];
  if (pending.length) {
    host.appendChild(el('p', 'conn-pending',
      `${pending.length} more recent day${pending.length > 1 ? 's' : ''} `
      + `(${pending.map(humanDate).join(', ')}) `
      + `${pending.length > 1 ? 'are' : 'is'} empty because arXiv has not `
      + 'announced them yet. Not a hole, and not something a fetch can fix today.'));
  }

  const w = plan.window || {};
  const plans = el('div', 'conn-plan');
  plans.appendChild(el('span', null,
    `Fetch asks the ${plan.endpoints[plan.route].label} for papers published `
    + `${humanDate(w.since)} to ${humanDate(w.until)}, ${w.days} days, walked in `
    + `pages of about ${w.page_size.toLocaleString()} records. Progress appears `
    + 'here as it goes.'));
  if (plan.capped) plans.appendChild(el('span', 'conn-cap', plan.cap_note));
  host.appendChild(plans);

  const run = el('button', 'btn conn-run', 'Fetch now');
  run.type = 'button';
  run.addEventListener('click', () => startFetch({}, box));
  host.appendChild(run);

  const live = el('div', 'conn-live');
  live.hidden = true;
  host.appendChild(live);
  return box;
}

async function renderConn(targetId) {
  const target = $(targetId);
  if (!target) return;
  target.textContent = '';
  const plan = await get('/api/fetch/status', {}, { timeoutMs: 20000 });
  if (plan.status !== 'ok') return;
  state.fetchPlan = plan;
  target.appendChild(connectionPanel(plan, true));
  if (plan.running) watchFetch(target.querySelector('.conn-box'));
}

/* Start the fetch on the server and watch it, rather than holding a request
   open for the three minutes a 22 day catch-up takes. The harvest reports
   every page it reads and nothing was listening to it. */
async function startFetch(params, box) {
  const started = await get('/api/fetch/start', params, { timeoutMs: 20000 });
  if (started.status !== 'ok') return toast(started.message || 'Could not start.', 'bad');
  watchFetch(box);
}

function watchFetch(box) {
  if (!box) return;
  const live = box.querySelector('.conn-live');
  const run = box.querySelector('.conn-run');
  if (!live) return;
  // A fetch running behind a closed disclosure is a spinner with extra steps.
  const details = box.querySelector('.conn-details');
  if (details) details.open = true;
  live.hidden = false;
  run.disabled = true;
  run.textContent = 'Fetching…';

  const tick = async () => {
    const out = await get('/api/fetch/progress', {}, { timeoutMs: 15000 });
    const p = (out && out.progress) || {};
    live.textContent = '';

    if (p.state === 'running') {
      const bar = el('div', 'conn-running');
      bar.appendChild(el('b', null, p.message || 'Working…'));
      bar.appendChild(el('span', 'dim', `${humanWait(p.elapsed || 0)} elapsed`));
      live.appendChild(bar);
      const log = el('div', 'conn-log');
      (p.lines || []).slice(-8).reverse().forEach((line) => {
        const row = el('div', 'conn-log-row');
        row.appendChild(el('span', 'conn-log-at', line.at));
        row.appendChild(el('span', null,
          `${line.set} request ${line.page}: ${line.seen.toLocaleString()} records read, `
          + `${line.kept.toLocaleString()} in your categories`));
        log.appendChild(row);
      });
      live.appendChild(log);
      setTimeout(tick, 1200);
      return;
    }

    run.disabled = false;
    run.textContent = 'Fetch now';
    const r = p.result || {};
    live.appendChild(el('div', r.ok ? 'conn-done' : 'conn-failed',
      r.message || 'Finished.'));
    if (r.ok && r.added) {
      paperCache.clear();
      await boot();
    }
    if (!r.ok && r.blocked) showBlocked(r);
  };
  tick();
}

/* ---------------- asking a question ---------------- */

function renderReading(data) {
  const box = $('#reading');
  box.textContent = '';
  if (!data) { box.hidden = true; return; }
  box.hidden = false;

  const line = el('div', 'reading-line');
  line.appendChild(el('span', 'reading-tag', 'read as'));
  line.appendChild(el('span', 'reading-text', data.reading || data.message || ''));
  box.appendChild(line);

  const terms = data.terms || [];
  if (terms.length) {
    const row = el('div', 'reading-terms');
    terms.forEach((t) => row.appendChild(el('span', 'tag', t)));
    box.appendChild(row);
  }

  if ((data.ignored_terms || []).length) {
    box.appendChild(el('div', 'reading-drop',
      `Ignored ${data.ignored_terms.join(', ')}. No paper you hold contains `
      + `${data.ignored_terms.length > 1 ? 'them' : 'it'}, so they could only dilute the ranking.`));
  }

  if ((data.related || []).length) {
    const wrap = el('div', 'reading-related');
    wrap.appendChild(el('span', 'reading-tag', 'try next'));
    wrap.appendChild(chipRow(data.related, (t) => {
      $('#q').value = t;
      runSearch('search');
    }));
    box.appendChild(wrap);
  }

  if (data.scope === 'arxiv') {
    const row = el('div', 'reading-related');
    row.appendChild(el('span', 'reading-tag', 'asked for new'));
    const go = el('button', 'suggestion', 'Search arXiv and add what is missing');
    go.type = 'button';
    go.addEventListener('click', () => searchArxiv((data.terms || []).join(' ')));
    row.appendChild(go);
    box.appendChild(row);
  }
}

async function runAsk() {
  const question = $('#q').value.trim();
  if (!question) return;
  state.mode = 'ask';
  state.query = question;
  setView('grid');
  $('#status').textContent = 'Reading your question…';
  renderReading(null);

  // A local model can be slow on a cold start; the rule parser behind it is
  // instant, so the wait is the upper bound, not the norm.
  const data = await get('/api/ask', { q: question, limit: PAGE_SIZE },
    { timeoutMs: 40000 });

  if (data.status === 'error') {
    $('#grid').textContent = '';
    $('#grid').appendChild(notice('Could not answer that', data.message, runAsk));
    $('#status').textContent = '';
    return;
  }

  if (data.status === 'needs_workspace') {
    $('#status').textContent = '';
    state.papers = [];
    renderGrid();
    $('#grid').textContent = '';
    $('#grid').appendChild(notice('No workspace folder set yet', data.message,
      () => setView('profile'), 'Set one in Profile'));
    renderReading(null);
    return;
  }

  if (data.status === 'no_subject' || data.status === 'empty_library') {
    $('#status').textContent = '';
    state.papers = [];
    $('#grid').textContent = '';
    $('#grid').appendChild(notice('Nothing to search for', data.message));
    renderReading(data);
    return;
  }

  state.papers = data.results || [];
  state.topics = data.terms || [];
  state.hasMore = false;
  $('#status').textContent = `${data.matched} of ${data.searched} papers matched`;
  $('#status').className = 'search-status on';
  renderHeroText();
  renderReading(data);
  renderGrid();
}

async function searchArxiv(query) {
  if (!query) return;
  $('#status').textContent = `Asking arXiv for “${query}”…`;
  const data = await get('/api/arxiv', { q: query, limit: 25 }, { timeoutMs: 90000 });
  if (data.status !== 'ok') {
    $('#status').textContent = '';
    // A toast that vanishes in four seconds is the wrong surface for "you are
    // rate limited for the next 20 minutes". It reads as a glitch, and the
    // reader's next move is to try again, which is exactly what deepens it.
    if (data.blocked) return showBlocked(data);
    toast(data.message || 'arXiv did not answer.', 'bad');
    return;
  }
  toast(data.message);
  paperCache.clear();
  $('#q').value = query;
  await runSearch('search');
  await refreshHeaderMeta();
}

// A refusal, stated plainly and left on screen. Distinguishing "arXiv has
// nothing" from "arXiv would not talk to us" matters more than almost anything
// else here: they look identical and mean opposite things.
function showBlocked(data) {
  const cool = data.cooldown || {};
  const mins = Math.ceil((cool.remaining || 0) / 60);
  const box = el('div', 'blockbar');
  box.appendChild(el('b', null, 'arXiv is not taking requests from this machine.'));
  box.appendChild(el('span', null, data.message || ''));
  if (data.what_now) box.appendChild(el('span', null, data.what_now));
  if ((cool.remaining || 0) > 0) {
    // A live countdown, because "wait 20 minutes" with no clock attached is
    // the kind of instruction people ignore and then retry into. Watching the
    // number go down is the whole reason this is on screen instead of a toast.
    const when = el('div', 'blockbar-when');
    const bar = el('div', 'waitbar');
    const fill = el('i');
    bar.appendChild(fill);
    const label = el('span', 'waitlabel');
    when.appendChild(label);
    when.appendChild(bar);
    box.appendChild(when);

    const total = cool.remaining;
    const started = Date.now();
    const tick = () => {
      const left = Math.max(0, total - (Date.now() - started) / 1000);
      fill.style.width = (100 - (left / total) * 100).toFixed(1) + '%';
      if (left <= 0) {
        clearInterval(timer);
        label.textContent = 'arXiv can be asked again now.';
        const retry = el('button', 'btn-ghost', 'Try again');
        retry.type = 'button';
        retry.addEventListener('click', () => {
          box.remove();
          if (state.query) searchArxiv(state.query);
        });
        when.appendChild(retry);
        return;
      }
      const m = Math.floor(left / 60);
      const s = Math.floor(left % 60);
      label.textContent =
        `Asking again in ${m}:${String(s).padStart(2, '0')}`
        + `${cool.strikes > 1 ? ` (refusal ${cool.strikes} in a row, so the wait `
          + `was lengthened)` : ''}. Waiting is the fix; retrying sooner extends it.`;
    };
    tick();
    const timer = setInterval(tick, 1000);
    // Stop the clock if the notice is taken off screen, so a hidden interval
    // is not left running for the life of the tab.
    const watch = new MutationObserver(() => {
      if (!box.isConnected) { clearInterval(timer); watch.disconnect(); }
    });
    watch.observe(document.body, { childList: true, subtree: true });
  }
  const grid = $('#grid');
  const existing = grid.querySelector('.blockbar');
  if (existing) existing.remove();
  grid.insertBefore(box, grid.firstChild);
  const empty = $('#grid-empty');
  if (!empty.hidden) {
    const dup = empty.querySelector('.blockbar');
    if (dup) dup.remove();
    empty.appendChild(box.cloneNode(true));
  }
  setView('grid');
}

function runSearch(mode, extra) {
  state.mode = mode || 'search';
  if (state.mode === 'ask') return runAsk();
  state.query = $('#q').value.trim();
  renderReading(null);
  setView('grid');
  return loadGrid(false, extra);
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
  if (state.index !== i) return; // the reader moved on meanwhile
  body.textContent = '';

  if (data.status !== 'ok') {
    body.appendChild(notice('Could not load this paper', data.message || data.status, () => {
      forgetPaper(row.id);
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

  const save = el('button', null, data.saved ? 'Saved ★' : 'Save');
  save.type = 'button';
  save.addEventListener('click', async () => {
    const r = await get('/api/save', { id: data.id });
    if (r.status !== 'ok') return toast(r.message || 'Could not save.', 'bad');
    data.saved = r.saved;
    row.saved = r.saved;
    save.textContent = r.saved ? 'Saved ★' : 'Save';
    forgetPaper(data.id);
    paperCache.set(cacheKey(data.id), Promise.resolve(data));
    renderGrid();
    toast(r.saved ? 'Saved to your shelf.' : 'Removed from saved.');
  });
  btns.appendChild(save);

  const read = el('button', null, data.read ? 'Read' : 'Mark read');
  read.type = 'button';
  read.addEventListener('click', async () => {
    await get('/api/read', { id: data.id });
    data.read = true;
    row.read = true;
    read.textContent = 'Read';
    forgetPaper(data.id);
    paperCache.set(cacheKey(data.id), Promise.resolve(data));
    renderGrid();
  });
  btns.appendChild(read);
  body.appendChild(btns);

  if (data.saved) {
    body.appendChild(el('div', 'sec', 'Your note'));
    const note = el('textarea', 'notefield');
    note.placeholder = 'Why you kept this one.';
    note.value = data.note || '';
    const saveState = el('div', 'dim notestate');
    let noteTimer = null;
    note.addEventListener('input', () => {
      saveState.textContent = 'unsaved…';
      clearTimeout(noteTimer);
      noteTimer = setTimeout(async () => {
        const r = await get('/api/note', { id: data.id, note: note.value });
        // A note box that silently fails to save is a note you think you wrote.
        saveState.textContent = r.status === 'ok'
          ? 'saved' : (r.message || 'could not save');
        if (r.status === 'ok') data.note = note.value;
      }, 500);
    });
    body.appendChild(note);
    body.appendChild(saveState);
  }

  const foot = el('div', 'scorefoot');
  const label = data.score_basis === 'query' ? 'match' : 'topic match';
  const why = el('button', 'whylink', `${label} ${data.score.toFixed(2)} · why? →`);
  why.type = 'button';
  why.addEventListener('click', () => openScoringFor(data));
  foot.appendChild(why);
  body.appendChild(foot);
}

/* ---------------- the map ---------------- */

const SVG = 'http://www.w3.org/2000/svg';
const svgEl = (tag, attrs) => {
  const n = document.createElementNS(SVG, tag);
  Object.entries(attrs || {}).forEach(([k, v]) => n.setAttribute(k, String(v)));
  return n;
};

async function loadMap() {
  const out = $('#view-map');
  out.textContent = '';
  out.appendChild(el('p', 'dim', 'Reading your library…'));
  const data = await get('/api/map', {}, { timeoutMs: 30000 });
  out.textContent = '';

  if (data.status === 'empty') {
    out.appendChild(notice('Not enough to map yet', data.message || ''));
    return;
  }
  if (data.status !== 'ok') {
    out.appendChild(notice('Could not build the map', data.message || data.status, loadMap));
    return;
  }

  const head = el('div', 'view-head');
  head.appendChild(el('h2', null, 'How your library fits together'));
  head.appendChild(el('p', 'sub', data.how));
  out.appendChild(head);

  out.appendChild(mapSvg(data));

  // Where a click lands. Built empty and kept between clicks so the panel does
  // not jump the page around as the reader moves from one concept to the next.
  const detail = el('div', 'map-detail');
  detail.id = 'map-detail';
  detail.hidden = true;
  out.appendChild(detail);

  // The part worth having. A search only finds what you already knew to ask
  // for, and the ranked list puts the most typical papers on top, so the tool
  // is weakest exactly where reading pays best: a paper joining two things you
  // care about separately and had never connected.
  if ((data.bridges || []).length) {
    const card = el('section', 'profile-card');
    card.appendChild(el('h3', null, 'Papers joining two things you keep apart'));
    card.appendChild(el('p', 'sub',
      'Ranked by how unusual the pairing is in your own library, not by how good '
      + 'the paper is. A paper covering two subjects that two hundred other papers '
      + 'also cover together is ordinary. One covering a pairing you hold almost '
      + 'nothing on is worth a look even if it is otherwise unremarkable. This is '
      + 'the one list here a search could never have shown you, because you would '
      + 'have had to already know to ask.'));
    data.bridges.forEach((row) => {
      const item = el('div', 'bridge');
      const link = el('button', 'linkish bridge-title', row.title);
      link.type = 'button';
      link.addEventListener('click', () => openById(row.id, row.url));
      item.appendChild(link);
      const pair = el('div', 'bridge-pair');
      row.pair.forEach((concept, i) => {
        if (i) pair.appendChild(el('span', 'bridge-plus', '+'));
        const chip = el('button', 'tag tag-live', concept);
        chip.type = 'button';
        chip.addEventListener('click', () => { $('#q').value = concept; runSearch('search'); });
        pair.appendChild(chip);
      });
      item.appendChild(pair);
      item.appendChild(el('div', 'bridge-note', row.note));
      card.appendChild(item);
    });
    out.appendChild(card);
  }
}

// Open a paper whether or not it is in the list currently on screen.
async function openById(id, url) {
  const at = state.papers.findIndex((p) => p.id === id);
  if (at >= 0) { setView('grid'); return openPanel(at); }
  const data = await get('/api/paper', { id });
  if (data.status !== 'ok') {
    return window.open(url || `https://arxiv.org/abs/${id}`, '_blank', 'noopener');
  }
  // Put it at the front of the current list so the panel's next and previous
  // keys still mean something, rather than opening a paper in a list of none.
  state.papers.unshift({ id: data.id, title: data.title, saved: data.saved,
    read: data.read, concepts: data.concepts, published: data.published,
    category: data.category, about: data.about, why_text: data.why_text });
  paperCache.set(cacheKey(id), Promise.resolve(data));
  setView('grid');
  renderGrid();
  openPanel(0);
}

/* The map, and the screen a click lands on.

   Three reports, all about the same gap. It is "too overlapping". Clicking a
   node "does show me some arxiv or maybe the section of what I clicked, but that
   interaction is not intuitive or transparent enough, so it's hard to know
   what's going on". And it is "missing like another type of screen that appears
   when I click a node or edge".

   So: hovering focuses one concept and dims the rest, which is what makes an
   overlapping graph readable without moving anything; lines are clickable, since
   the interesting question about two concepts is which papers do both; and a
   click opens a panel under the map rather than navigating away, so the map and
   the answer are on screen together and the selection stays visible. */

const mapState = { selected: null, pair: null };

function mapSvg(data) {
  const wrap = el('div', 'mapwrap');
  const size = 620;
  const pad = 74;
  const svg = svgEl('svg', {
    viewBox: `0 0 ${size} ${size}`, class: 'conceptmap',
    role: 'img', 'aria-label': 'Concept map of your library',
  });

  const at = (v) => pad + ((v + 1) / 2) * (size - pad * 2);
  const byName = {};
  data.nodes.forEach((n) => { byName[n.concept] = n; });
  const biggest = Math.max(...data.nodes.map((n) => n.papers), 1);
  const radiusOf = (n) => 11 + Math.sqrt(n.papers / biggest) * 26;

  // Who touches whom, so a hover can dim everything else in one pass instead of
  // re-deriving the neighbourhood per mark.
  const neighbours = {};
  data.links.forEach((link) => {
    (neighbours[link.source] = neighbours[link.source] || new Set()).add(link.target);
    (neighbours[link.target] = neighbours[link.target] || new Set()).add(link.source);
  });

  const linkLayer = svgEl('g', { class: 'maplinks' });
  data.links.forEach((link) => {
    const a = byName[link.source];
    const b = byName[link.target];
    if (!a || !b) return;
    const g = svgEl('g', {
      class: 'maplink', tabindex: '0', role: 'button',
      'data-a': link.source, 'data-b': link.target,
    });
    const coords = { x1: at(a.x), y1: at(a.y), x2: at(b.x), y2: at(b.y) };
    // A wide invisible line under the visible one, because a 1px stroke is not
    // a hit target and a line you cannot hit is not clickable.
    g.appendChild(svgEl('line', Object.assign({ class: 'maplink-hit' }, coords)));
    g.appendChild(svgEl('line', Object.assign({
      class: 'maplink-ink',
      'stroke-width': (0.6 + link.strength * 4.5).toFixed(2),
      opacity: (0.13 + link.strength * 0.5).toFixed(2),
    }, coords)));
    const t = svgEl('title');
    t.textContent = `${link.papers} papers mention both ${link.source} and `
      + `${link.target}. Click for the list.`;
    g.appendChild(t);
    const open = () => openMapDetail(link.source, link.target);
    g.addEventListener('click', open);
    g.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
    });
    linkLayer.appendChild(g);
  });
  svg.appendChild(linkLayer);

  const nodeLayer = svgEl('g', { class: 'mapnodes' });
  data.nodes.forEach((n) => {
    const g = svgEl('g', { class: 'mapnode', tabindex: '0', role: 'button',
      'data-concept': n.concept });
    const r = radiusOf(n);
    g.appendChild(svgEl('circle', { cx: at(n.x), cy: at(n.y), r }));
    if (n.saved) {
      // A ring showing how much of this you actually kept. The gap between
      // what you fetch and what you save is the useful signal.
      const frac = Math.min(1, n.saved / Math.max(1, n.papers) * 6);
      const c = 2 * Math.PI * (r + 3.5);
      g.appendChild(svgEl('circle', {
        cx: at(n.x), cy: at(n.y), r: r + 3.5, class: 'mapsaved',
        'stroke-dasharray': `${(c * frac).toFixed(1)} ${c.toFixed(1)}`,
        transform: `rotate(-90 ${at(n.x)} ${at(n.y)})`,
      }));
    }

    /* Labels outside the small circles. Inside, a long concept in a small circle
       was clipped to "interpretab" with an ellipsis and ran over its neighbours,
       which is most of what "too overlapping" was about. Only circles with room
       keep the label inside. */
    const roomy = r >= 22 && n.concept.length <= 13;
    const label = svgEl('text', {
      x: at(n.x), y: roomy ? at(n.y) + 3.5 : at(n.y) + r + 12,
      'text-anchor': 'middle',
      class: roomy ? 'maplabel maplabel-in' : 'maplabel maplabel-out',
      'font-size': roomy ? Math.max(9, Math.min(12, r * 0.42)) : 10.5,
    });
    label.textContent = n.concept;
    g.appendChild(label);

    const title = svgEl('title');
    title.textContent = `${n.concept}: ${n.papers} papers, ${n.share}% of your library`
      + (n.saved ? `, ${n.saved} saved` : '')
      + (n.with.length ? `\nMost often alongside ${n.with.map((w) => w.concept).join(', ')}` : '')
      + '\nClick to see what is in it.';
    g.appendChild(title);

    const open = () => openMapDetail(n.concept, '');
    g.addEventListener('click', open);
    g.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
    });
    // Focus on hover. Nothing moves: the unrelated marks just recede, which is
    // what makes a crowded graph readable without making it unstable.
    g.addEventListener('mouseenter', () => focusConcept(svg, n.concept, neighbours));
    g.addEventListener('mouseleave', () => focusConcept(svg, null, neighbours));
    g.addEventListener('focus', () => focusConcept(svg, n.concept, neighbours));
    g.addEventListener('blur', () => focusConcept(svg, null, neighbours));
    nodeLayer.appendChild(g);
  });
  svg.appendChild(nodeLayer);
  wrap.appendChild(svg);

  const key = el('div', 'mapkey');
  [['Bigger circle', 'more of your papers mention it'],
   ['Thicker line', 'the two turn up together more often'],
   ['Gold arc', 'how much of it you have saved'],
   ['Hover a circle', 'everything unrelated fades back'],
   ['Click a circle', 'what is in it, and the papers'],
   ['Click a line', 'the papers that mention both']]
    .forEach(([term, meaning]) => {
      const row = el('div', 'mapkey-row');
      row.appendChild(el('b', null, term));
      row.appendChild(el('span', null, meaning));
      key.appendChild(row);
    });
  wrap.appendChild(key);
  return wrap;
}

function focusConcept(svg, concept, neighbours) {
  if (!concept) {
    svg.classList.remove('focusing');
    svg.querySelectorAll('.dimmed, .lit').forEach((n) => {
      n.classList.remove('dimmed');
      n.classList.remove('lit');
    });
    return;
  }
  const near = neighbours[concept] || new Set();
  svg.classList.add('focusing');
  svg.querySelectorAll('.mapnode').forEach((g) => {
    const name = g.getAttribute('data-concept');
    const related = name === concept || near.has(name);
    g.classList.toggle('dimmed', !related);
    g.classList.toggle('lit', name === concept);
  });
  svg.querySelectorAll('.maplink').forEach((g) => {
    const touches = g.getAttribute('data-a') === concept
      || g.getAttribute('data-b') === concept;
    g.classList.toggle('dimmed', !touches);
    g.classList.toggle('lit', touches);
  });
}

/* The screen a click lands on. Under the map rather than over it, so the thing
   that was clicked is still visible beside the answer. */
async function openMapDetail(concept, other) {
  mapState.selected = concept;
  mapState.pair = other || null;
  const host = $('#map-detail');
  if (!host) return;
  host.hidden = false;
  host.textContent = '';
  host.appendChild(el('p', 'dim', 'Reading your library...'));
  host.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  const data = await get('/api/map/detail',
    other ? { concept, with: other } : { concept }, { timeoutMs: 30000 });
  host.textContent = '';
  if (data.status !== 'ok') {
    host.appendChild(notice('Could not open that', data.message || data.status));
    return;
  }

  const card = el('section', 'mapdetail');
  const head = el('div', 'mapdetail-head');
  const title = el('h3');
  title.appendChild(el('span', 'mapdetail-term', concept));
  if (other) {
    title.appendChild(el('span', 'mapdetail-plus', 'together with'));
    title.appendChild(el('span', 'mapdetail-term', other));
  }
  head.appendChild(title);
  const close = el('button', 'panel-x', '×');
  close.type = 'button';
  close.setAttribute('aria-label', 'Close');
  close.addEventListener('click', () => {
    host.hidden = true;
    host.textContent = '';
    mapState.selected = null;
    mapState.pair = null;
  });
  head.appendChild(close);
  card.appendChild(head);

  // The numbers first, each one a count you can get back to.
  const stats = el('div', 'mapstats');
  const stat = (n, label) => {
    const box = el('div', 'mapstat');
    box.appendChild(el('b', null, n));
    box.appendChild(el('span', null, label));
    return box;
  };
  stats.appendChild(stat(data.papers.toLocaleString(), 'papers'));
  stats.appendChild(stat(`${data.share}%`, 'of your library'));
  stats.appendChild(stat(data.saved.toLocaleString(), 'you saved'));
  if (other) {
    stats.appendChild(stat(data.concept_papers.toLocaleString(),
      `mention ${concept} at all`));
  }
  card.appendChild(stats);
  card.appendChild(el('p', 'mapdetail-how', data.how));

  if (!other && (data.with || []).length) {
    const near = el('div', 'mapnear');
    near.appendChild(el('span', 'filter-label', 'Most often alongside'));
    const row = el('div', 'mapnear-row');
    data.with.forEach((w) => {
      const chip = el('button', 'tag tag-live');
      chip.type = 'button';
      chip.appendChild(el('span', null, w.concept));
      chip.appendChild(el('b', null, String(w.papers)));
      chip.title = `${w.papers} of your ${concept} papers also mention `
        + `${w.concept}. Click for those papers.`;
      chip.addEventListener('click', () => openMapDetail(concept, w.concept));
      row.appendChild(chip);
    });
    near.appendChild(row);
    card.appendChild(near);
  }

  const list = el('div', 'mappapers');
  data.results.forEach((p) => {
    const item = el('article', 'mappaper');
    item.appendChild(el('div', 'meta',
      [p.published && humanDate(p.published), p.category].filter(Boolean).join('  ·  ')));
    const link = el('button', 'linkish mappaper-title', p.title);
    link.type = 'button';
    link.addEventListener('click', () => openById(p.id, p.url));
    item.appendChild(link);
    if (p.about) item.appendChild(el('p', 'about', p.about));
    if (p.saved) item.appendChild(el('span', 'mappaper-saved', 'on your shelf'));
    list.appendChild(item);
  });
  card.appendChild(list);

  if (data.more) {
    const more = el('button', 'btn-ghost',
      `Search your library for all ${data.papers.toLocaleString()}`);
    more.type = 'button';
    more.addEventListener('click', () => {
      $('#q').value = other ? `${concept} ${other}` : concept;
      runSearch('search');
    });
    card.appendChild(more);
  }
  host.appendChild(card);
}

/* ---------------- trends: the field, and your feed ---------------- */

/* Reported as: "it's based on my local library or download history, but frankly
   I'm more interested in industry trends. For example from 1 week or 1 month
   ago, how many papers are being reported on rag or evaluation or memory."

   Those are two different measurements, so they are two views rather than one
   with a caveat. The field is the default, because it is the question that was
   being asked. */

const FIELD_WINDOWS = [
  { id: 'week', label: 'This week vs last week',
    q: { window: 7, offset: 0, against: 7, against_offset: 7 } },
  { id: 'month-ago', label: 'This week vs a month ago',
    q: { window: 7, offset: 0, against: 7, against_offset: 30 } },
  { id: 'month', label: 'Last 30 days vs the 30 before',
    q: { window: 30, offset: 0, against: 30, against_offset: 30 } },
  { id: 'quarter', label: 'Last 30 days vs 60 days ago',
    q: { window: 30, offset: 0, against: 30, against_offset: 60 } },
];

const trendState = { scope: 'field', window: 'week' };

async function loadTrends() {
  const out = $('#view-trends');
  out.textContent = '';
  const head = el('div', 'view-head');
  head.appendChild(el('h2', null, trendState.scope === 'field'
    ? 'What the field is publishing' : 'What moved in your feed'));
  head.appendChild(el('p', 'sub', trendState.scope === 'field'
    ? 'Counted across every cs paper arXiv published in each window, not across '
      + 'your library. Built by streaming arXiv and keeping only the daily counts, '
      + 'so asking about the whole field costs a few kilobytes.'
    : 'Counted across the papers your own profile fetched. Useful for seeing what '
      + 'you are accumulating, and not a measurement of the literature.'));
  out.appendChild(head);

  const tabs = el('div', 'scopetabs');
  [['field', 'The field'], ['feed', 'Your feed']].forEach(([id, label]) => {
    const b = el('button', `scopetab${trendState.scope === id ? ' active' : ''}`, label);
    b.type = 'button';
    b.addEventListener('click', () => { trendState.scope = id; loadTrends(); });
    tabs.appendChild(b);
  });
  out.appendChild(tabs);

  const body = el('div');
  out.appendChild(body);
  if (trendState.scope === 'field') return renderField(body);
  return renderFeedTrends(body);
}

async function renderField(out) {
  const picker = el('div', 'winpick');
  picker.appendChild(el('span', 'filter-label', 'Compare'));
  FIELD_WINDOWS.forEach((w) => {
    const b = el('button', `chip${trendState.window === w.id ? ' active' : ''}`, w.label);
    b.type = 'button';
    b.addEventListener('click', () => { trendState.window = w.id; loadTrends(); });
    picker.appendChild(b);
  });
  out.appendChild(picker);

  const loading = el('p', 'dim', 'Reading the census…');
  out.appendChild(loading);
  const chosen = FIELD_WINDOWS.find((w) => w.id === trendState.window) || FIELD_WINDOWS[0];
  const data = await get('/api/field', chosen.q, { timeoutMs: 30000 });
  loading.remove();

  if (data.status !== 'ok') {
    const box = el('div', 'note');
    box.appendChild(el('b', null, 'The field census is not built yet'));
    box.appendChild(el('span', null, data.reason || ''));
    box.appendChild(el('span', null,
      'It streams arXiv’s harvest feed, counts every paper it sees against a '
      + 'list of subjects, and keeps only the per-day totals. Your library is not '
      + 'touched and nothing is downloaded twice.'));
    const build = el('button', 'btn', 'Count the last 90 days');
    build.type = 'button';
    build.addEventListener('click', async () => {
      build.disabled = true;
      const started = await get('/api/field/build', { days: 90 }, { timeoutMs: 20000 });
      if (started.status !== 'ok') {
        build.disabled = false;
        return toast(started.message || 'Could not start.', 'bad');
      }
      setView('grid');
      await renderConn('#conn-grid');
      watchFetch($('#conn-grid').querySelector('.conn-box'));
      toast('Counting arXiv. Progress is on the Papers tab.');
    });
    box.appendChild(build);
    out.appendChild(box);
    return;
  }

  out.appendChild(fieldChart(data));

  const scope = el('div', 'scopebar');
  scope.appendChild(el('b', null, 'This is arXiv, not your library.'));
  scope.appendChild(el('span', null, data.scope));
  out.appendChild(scope);

  const how = el('details', 'method');
  how.appendChild(el('summary', null, 'How this is counted'));
  how.appendChild(el('p', null, data.method));
  const cov = data.coverage || {};
  how.appendChild(el('p', null,
    `The census holds ${cov.days} days, ${humanDate(cov.first)} to ${humanDate(cov.last)}, `
    + `covering ${(cov.papers || 0).toLocaleString()} papers. Those papers were read `
    + 'and counted, not saved: what is on disk is one row of numbers per day.'));
  out.appendChild(how);
}

/* A diverging bar of change in share, sorted.

   The sign is carried three ways on purpose: which side of the zero line the bar
   sits, the signed number beside it, and the colour. The app's rise and fall
   tokens are 5.9 apart in OKLab under protanopia, which is below the threshold
   where colour alone may distinguish two things, so colour here is the last of
   the three rather than the only one. */
function fieldChart(data) {
  const rows = (data.rows || []).filter((r) => r.now || r.before);
  const wrap = el('section', 'fieldchart');
  const most = Math.max(0.01, ...rows.map((r) => Math.abs(r.change_pts)));

  const legend = el('div', 'fieldlegend');
  legend.appendChild(el('span', null,
    `${humanDate(data.recent.start)} to ${humanDate(data.recent.end)} `
    + `(${data.recent.papers.toLocaleString()} papers)`));
  legend.appendChild(el('span', 'dim', 'against'));
  legend.appendChild(el('span', null,
    `${humanDate(data.prior.start)} to ${humanDate(data.prior.end)} `
    + `(${data.prior.papers.toLocaleString()} papers)`));
  wrap.appendChild(legend);

  rows.forEach((r) => {
    const row = el('div', 'fieldrow');
    row.appendChild(el('div', 'fieldterm', r.term));

    const track = el('div', 'fieldtrack');
    const bar = el('i', r.change_pts >= 0 ? 'up' : 'down');
    const width = Math.abs(r.change_pts) / most * 50;
    bar.style.width = `${width}%`;
    bar.style[r.change_pts >= 0 ? 'left' : 'right'] = '50%';
    track.appendChild(el('span', 'fieldzero'));
    track.appendChild(bar);
    row.appendChild(track);

    const pts = el('div', `fieldpts ${r.change_pts >= 0 ? 'up' : 'down'}`,
      `${r.change_pts >= 0 ? '+' : ''}${r.change_pts.toFixed(2)} pts`);
    row.appendChild(pts);
    row.appendChild(el('div', 'fieldshare',
      `${r.share_before.toFixed(1)}% to ${r.share_now.toFixed(1)}%`));
    row.appendChild(el('div', 'fieldcount',
      `${r.before.toLocaleString()} to ${r.now.toLocaleString()} papers`));
    row.title = `${r.term}: ${r.now.toLocaleString()} of `
      + `${r.of_now.toLocaleString()} papers in the recent window `
      + `(${r.share_now.toFixed(2)}%, ${r.per_day_now} a day), against `
      + `${r.before.toLocaleString()} of ${r.of_before.toLocaleString()} before `
      + `(${r.share_before.toFixed(2)}%, ${r.per_day_before} a day).`;
    wrap.appendChild(row);
  });

  if (!rows.length) {
    wrap.appendChild(el('p', 'dim', 'No subject reached either window.'));
  }
  return wrap;
}

async function renderFeedTrends(out) {
  const data = await get('/api/trends');
  return renderFeedTrendsWith(out, data);
}

async function renderFeedTrendsWith(out, data) {

  if (data.status !== 'ok') {
    const reason = data.status === 'error'
      ? (data.message || 'Could not reach the server.')
      : 'Trends compares this week’s concepts against last week’s. Come back '
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
    renderCrossing(out, data.crossing);
    return;
  }

  const basis = data.basis || {};
  const thisW = basis.this_week || {};
  const prevW = basis.previous_week || {};
  out.appendChild(el('p', 'sub',
    `${thisW.papers} papers from ${humanDate(thisW.start)} to ${humanDate(thisW.end)}, `
    + `compared against the ${prevW.papers} from ${humanDate(prevW.start)} to `
    + `${humanDate(prevW.end)}.`));

  // The sentence that decides whether any of this is believable. Without it a
  // reader reasonably assumes these counts are arXiv, and "chain-of-thought:
  // 2 papers" is absurd about arXiv and ordinary about a 70-paper sample that
  // your own keyword list selected.
  if (data.scope_warning) {
    const scope = el('div', 'scopebar');
    scope.appendChild(el('b', null, 'Read this as your feed, not the field.'));
    scope.appendChild(el('span', null, data.scope_warning));
    out.appendChild(scope);
  }

  const box = el('div', 'tcols');
  [['Rising', data.rising, 'up'], ['Falling', data.falling, 'down'],
   ['Holding steady', data.steady, '']].forEach(([label, rows, cls]) => {
    const col = el('div', 'tcol');
    col.appendChild(el('h3', null, label));
    if (!rows.length) col.appendChild(el('div', 'dim', 'nothing cleared the bar'));
    rows.forEach((r) => col.appendChild(trendRow(r, cls)));
    box.appendChild(col);
  });
  out.appendChild(box);

  // Shown, not hidden. Dropping these would trade one false impression for
  // another — "calibration is not moving" instead of "calibration is up 200%".
  if ((data.too_few || []).length) {
    const col = el('div', 'tcol');
    col.style.marginTop = '18px';
    col.appendChild(el('h3', null, 'Too thin to say anything about'));
    col.appendChild(el('p', 'sub',
      `These turned up in fewer than ${basis.min_evidence} of your papers in both `
      + 'weeks. At those numbers one research group posting twice looks identical to '
      + 'a real shift, so calling a direction would be making it up. The counts are '
      + 'here anyway, because hiding them would leave you thinking nothing was '
      + 'happening rather than that too little was measured.'));
    const strip = el('div', 'thin-row');
    (data.too_few || []).forEach((r) => {
      const b = el('button', 'thin-chip');
      b.type = 'button';
      b.appendChild(el('span', null, r.concept));
      b.appendChild(el('b', null, `${r.previous}→${r.current}`));
      b.title = `${r.previous} of ${r.of_previous} papers last week, `
        + `${r.current} of ${r.of_current} this week. Click to search.`;
      b.addEventListener('click', () => { $('#q').value = r.concept; runSearch('search'); });
      strip.appendChild(b);
    });
    col.appendChild(strip);
    out.appendChild(col);
  }

  if (data.note) {
    const how = el('details', 'method');
    how.appendChild(el('summary', null, 'How this is measured'));
    how.appendChild(el('p', null, data.note));
    out.appendChild(how);
  }
  renderCrossing(out, data.crossing);
}

// One trend row, showing its own evidence: the share of each week's papers,
// the raw counts behind that share, and the move in percentage points. The old
// row said "1 to 2  +100%", which is three claims and no denominator.
function trendRow(r, cls) {
  const row = el('div', 'trow trow-rich');
  const name = el('button', 'linkish', r.concept);
  name.type = 'button';
  name.title = `Search your library for "${r.concept}"`;
  name.addEventListener('click', () => { $('#q').value = r.concept; runSearch('search'); });
  row.appendChild(name);

  const right = el('div', 'trow-right');
  const move = el('span', 'd ' + cls,
    (r.change_pts > 0 ? '+' : '') + r.change_pts + ' pts');
  move.title = r.verdict || '';
  right.appendChild(move);
  right.appendChild(el('span', 'trow-counts',
    `${r.previous}/${r.of_previous} → ${r.current}/${r.of_current}`));
  row.appendChild(right);

  // Two bars, same scale: last week's share and this week's. The comparison is
  // the point, so it should be visible without reading four numbers.
  const bars = el('div', 'trow-bars');
  const scale = Math.max(r.share_now, r.share_previous, 1);
  [[r.share_previous, 'was'], [r.share_now, 'now']].forEach(([share, label]) => {
    const line = el('div', 'tbar');
    line.appendChild(el('span', 'tbar-label', label));
    const track = el('span', 'tbar-track');
    const fill = el('i', label === 'now' ? cls : null);
    fill.style.width = Math.max(1, (share / scale) * 100).toFixed(0) + '%';
    track.appendChild(fill);
    line.appendChild(track);
    line.appendChild(el('span', 'tbar-pct', share + '%'));
    bars.appendChild(line);
  });
  row.appendChild(bars);
  return row;
}

function renderCrossing(out, crossing) {
  if (!crossing || !crossing.length) return;
  const col = el('div', 'tcol');
  col.style.marginTop = '18px';
  col.appendChild(el('h3', null, 'Crossing over'));
  col.appendChild(el('p', 'sub',
    'A concept appearing in a category your library has never held it in before. '
    + 'usually worth more attention than the same idea appearing where it always does. '
    + 'The claim is about your library over the months you have been fetching it, not '
    + 'about the literature, and only categories where you already hold 20+ tagged '
    + 'papers are eligible, because in a thin category everything is a first.'));
  crossing.forEach((c) => {
    const row = el('div', 'crossrow');
    const b = el('button', 'linkish', c.title);
    b.type = 'button';
    b.addEventListener('click', () => {
      const at = state.papers.findIndex((p) => p.id === c.id);
      if (at >= 0) { setView('grid'); openPanel(at); }
      else window.open(c.url || `https://arxiv.org/abs/${c.id}`, '_blank', 'noopener');
    });
    row.appendChild(b);
    row.appendChild(el('span', 'n', c.note));
    col.appendChild(row);
  });
  out.appendChild(col);
}

/* ---------------- digest ---------------- */

/* Fourteen days of "did the fetch run, and did it work".

   The empty days are the point. A history that only draws the runs that
   happened cannot show you the morning the scheduled job stopped firing, which
   is the single failure this is meant to catch: everything looks normal, the
   digest still renders, and it is quietly built on a library that stopped
   growing a week ago. */
function fetchHistory(days) {
  const card = el('section', 'profile-card');
  card.appendChild(el('h3', null, 'Fetch history'));
  const ran = days.filter((d) => d.runs).length;
  const failed = days.filter((d) => d.failed).length;
  card.appendChild(el('p', 'sub',
    `A fetch ran on ${ran} of the last ${days.length} days`
    + (failed ? `, and was refused on ${failed} of them. ` : '. ')
    + 'A blank column is a day with no run recorded at all, which is what a '
    + 'stopped scheduled job looks like from here.'));

  const strip = el('div', 'histstrip');
  days.forEach((d) => {
    const kind = !d.runs ? 'none' : (d.failed && !d.ok ? 'bad' : 'ok');
    const cell = el('div', `histcell hist-${kind}`);
    cell.appendChild(el('span', 'histday', d.day.slice(8)));
    cell.title = !d.runs
      ? `${d.day}: no fetch recorded`
      : `${d.day}: ${d.runs} run${d.runs > 1 ? 's' : ''}, ${d.added} papers added`
        + (d.failed ? `, ${d.failed} refused` : '')
        + ` (${d.sources.join(', ')})`;
    if (d.added) cell.appendChild(el('b', null, String(d.added)));
    strip.appendChild(cell);
  });
  card.appendChild(strip);
  return card;
}

async function loadDigest() {
  const out = $('#view-digest');
  out.textContent = 'Loading…';
  const data = await get('/api/digest', {}, { timeoutMs: 20000 });
  out.textContent = '';

  if (data.status !== 'ok') {
    out.appendChild(notice('Could not load the digest', data.message || data.status, loadDigest));
    return;
  }

  out.appendChild(el('h2', null, `Today’s digest, ${humanDate(data.date)}`));
  /* This line used to read "the top 5 of 10125 papers in your library" while
     the header said 13,478. Both numbers were right and one was mislabelled:
     10,125 was how many papers matched a topic at all. Two surfaces claiming
     two library sizes is worse than either number being wrong. */
  out.appendChild(el('p', 'sub',
    `The top ${data.picks.length} of the ${data.considered.toLocaleString()} papers `
    + `in your library of ${data.library.toLocaleString()} that match at least one of `
    + 'your topics, one per category where possible. Deterministic: the same library '
    + 'and topics produce the same picks, so this is worth skimming once a day rather '
    + 'than re-running.'));

  if (data.fetch) {
    const panel = connectionPanel(data.fetch);
    out.appendChild(panel);
    if (data.fetch.running) watchFetch(panel);
  }
  if (data.history) out.appendChild(fetchHistory(data.history));

  if (!data.picks.length) {
    out.appendChild(el('div', 'empty',
      'Nothing to pick from yet. Fetch some papers first.'));
    return;
  }

  const grid = el('div', 'grid');
  data.picks.forEach((p) => {
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
  const [data, saved] = await Promise.all([
    get('/api/papers', { when: 'saved', sort: 'score', limit: 200 }),
    get('/api/saved', {}, { timeoutMs: 15000 }),
  ]);
  out.textContent = '';
  if (data.status !== 'ok') {
    out.appendChild(notice('Could not load your shelf', data.message || data.status, loadQueue));
    return;
  }

  const notes = {};
  (saved.results || []).forEach((row) => {
    notes[row.id] = { note: row.note || '', saved_at: row.saved_at || '' };
  });

  const head = el('div', 'view-head');
  head.appendChild(el('h2', null, 'Your shelf'));
  head.appendChild(el('p', 'sub',
    'Everything you starred, newest first, with your notes and what each paper says '
    + 'it does. Built for skimming a stack you already chose rather than searching '
    + 'one you have not.'));
  out.appendChild(head);

  if (!data.results.length) {
    out.appendChild(el('div', 'empty',
      'Nothing saved yet. Star a paper anywhere and it lands here.'));
    return;
  }

  state.papers = data.results;
  state.query = '';

  const unread = data.results.filter((p) => !p.read);
  const withNotes = data.results.filter((p) => (notes[p.id] || {}).note);
  const stats = el('div', 'statrow');
  [[data.results.length, 'saved'], [unread.length, 'still unread'],
   [withNotes.length, 'with a note']].forEach(([n, label]) => {
    const cell = el('div', 'stat');
    cell.appendChild(el('b', null, String(n)));
    cell.appendChild(el('span', null, label));
    stats.appendChild(cell);
  });
  out.appendChild(stats);

  const bar = el('div', 'exportbar');
  const filters = el('div', 'filter-bar');
  let mode = 'unread';
  const rowsFor = (which) => which === 'all' ? data.results
    : which === 'notes' ? withNotes : unread;
  [['unread', 'Unread'], ['notes', 'With notes'], ['all', 'Everything']]
    .forEach(([key, label]) => {
      const chip = el('button', 'chip' + (key === mode ? ' active' : ''), label);
      chip.type = 'button';
      chip.addEventListener('click', () => {
        mode = key;
        filters.querySelectorAll('.chip').forEach((c) =>
          c.classList.toggle('active', c === chip));
        draw();
      });
      filters.appendChild(chip);
    });
  bar.appendChild(filters);
  ['markdown', 'bibtex', 'json'].forEach((fmt) => {
    const b = el('button', 'btn-ghost',
      fmt === 'bibtex' ? 'BibTeX' : fmt === 'json' ? 'JSON' : 'Markdown');
    b.type = 'button';
    b.title = `Download everything saved as ${fmt}`;
    b.addEventListener('click', () => downloadExport('saved', fmt));
    bar.appendChild(b);
  });
  out.appendChild(bar);

  const list = el('div', 'shelf');
  out.appendChild(list);

  // A row you can read, rather than a card you have to open. The old queue was
  // the same grid as everywhere else, so the one screen where you have already
  // decided these matter was the screen showing you the least about them.
  function draw() {
    list.textContent = '';
    const rows = rowsFor(mode);
    if (!rows.length) {
      list.appendChild(el('div', 'empty',
        mode === 'unread' ? 'Nothing unread. The whole shelf is read.'
          : 'No notes yet. Open a paper and write why you kept it.'));
      return;
    }
    rows.forEach((paper) => {
      const item = el('article', 'shelfrow' + (paper.read ? ' is-read' : ''));
      const meta = el('div', 'meta');
      const when = (notes[paper.id] || {}).saved_at;
      meta.appendChild(el('span', null,
        [paper.category, humanDate(paper.published)].filter(Boolean).join('  ·  ')));
      if (when) {
        const stamp = humanStamp(when);
        if (stamp) {
          const s = el('span', 'shelf-when', `saved ${stamp.short}`);
          s.title = stamp.full;
          meta.appendChild(s);
        }
      }
      if (paper.read) meta.appendChild(el('span', 'shelf-read', 'read'));
      item.appendChild(meta);

      const title = el('button', 'linkish shelf-title', paper.title);
      title.type = 'button';
      title.addEventListener('click', () => {
        const at = state.papers.findIndex((p) => p.id === paper.id);
        setView('grid');
        openPanel(at >= 0 ? at : 0);
      });
      item.appendChild(title);

      if (paper.about) item.appendChild(el('p', 'shelf-about', paper.about));

      const note = (notes[paper.id] || {}).note;
      if (note) {
        const quote = el('blockquote', 'shelf-note');
        quote.appendChild(el('span', 'reading-tag', 'your note'));
        quote.appendChild(el('p', null, note));
        item.appendChild(quote);
      }

      const tags = el('div', 'tags');
      (paper.concepts || []).slice(0, 6).forEach((c) => {
        const tag = el('button', 'tag tag-live', c);
        tag.type = 'button';
        tag.addEventListener('click', () => { $('#q').value = c; runSearch('search'); });
        tags.appendChild(tag);
      });
      item.appendChild(tags);

      const acts = el('div', 'shelf-acts');
      const open = el('a', null, 'Open on arXiv');
      open.href = paper.url || `https://arxiv.org/abs/${paper.id}`;
      open.target = '_blank';
      open.rel = 'noopener';
      acts.appendChild(open);
      const done = el('button', null, paper.read ? 'Read' : 'Mark read');
      done.type = 'button';
      done.addEventListener('click', async () => {
        await get('/api/read', { id: paper.id });
        paper.read = true;
        forgetPaper(paper.id);
        done.textContent = 'Read';
        item.classList.add('is-read');
      });
      acts.appendChild(done);
      const drop = el('button', null, 'Remove');
      drop.type = 'button';
      drop.addEventListener('click', async () => {
        const r = await get('/api/save', { id: paper.id });
        if (r.status !== 'ok' || r.saved) return;
        forgetPaper(paper.id);
        toast('Removed from saved.');
        loadQueue();
      });
      acts.appendChild(drop);
      item.appendChild(acts);
      list.appendChild(item);
    });
  }
  draw();
}

// The browser's own download path — a Blob and an object URL. A library you
// cannot get out of is a library you are renting.
async function downloadExport(what, format) {
  const data = await get('/api/export', { what, format }, { timeoutMs: 30000 });
  if (data.status !== 'ok') return toast(data.message || 'Export failed.', 'bad');
  const blob = new Blob([data.body], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = data.filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast(`Exported ${data.count} papers as ${data.filename}.`);
}

/* ---------------- scoring lab ---------------- */

let labTimer = null;

/* The controls on the scoring page.

   Reported as: the topics box should "be able to kind of like fill in the blank
   and populate it instead of potentially having user errors there typing
   something"; and for the title, "who knows the full title all the time, it
   goes back to my first point about error handling or mistyping".

   So the two free-text boxes get the same treatment the date row already had:
   something to click. The boxes stay, because the page is a playground and
   typing your own nonsense into it is the point. They are just no longer the
   only way in. */

const LAB_SUGGESTED = [
  'agent', 'evaluation', 'multi-agent', 'reasoning', 'memory', 'retrieval',
  'benchmark', 'alignment', 'interpretability', 'reliability', 'learning',
  'robustness', 'planning', 'tool use', 'safety',
];

function labTopics() {
  return ($('#lab-topics').value || '').split(',')
    .map((t) => t.trim().toLowerCase()).filter(Boolean);
}

function setLabTopics(list) {
  $('#lab-topics').value = list.join(', ');
  drawTopicChips();
  runLab();
}

function drawTopicChips() {
  const box = $('#lab-topic-chips');
  if (!box) return;
  const chosen = labTopics();
  // Your own profile first, then the common ones, so the list is about you
  // before it is about the field.
  const offer = [];
  (state.settingsTopics || []).concat(LAB_SUGGESTED).forEach((t) => {
    const term = String(t).toLowerCase();
    if (term && !offer.includes(term)) offer.push(term);
  });
  chosen.forEach((t) => { if (!offer.includes(t)) offer.unshift(t); });

  box.textContent = '';
  offer.slice(0, 18).forEach((term) => {
    const on = chosen.includes(term);
    const chip = el('button', `pickchip${on ? ' on' : ''}`, term);
    chip.type = 'button';
    chip.setAttribute('aria-pressed', on ? 'true' : 'false');
    chip.title = on ? `Drop ${term} and watch the score fall`
      : `Add ${term} and watch the score move`;
    chip.addEventListener('click', () => {
      const next = on ? chosen.filter((t) => t !== term) : chosen.concat([term]);
      setLabTopics(next);
    });
    box.appendChild(chip);
  });
}

async function drawLabExamples() {
  const box = $('#lab-examples');
  if (!box || box.childElementCount) return;
  const data = await get('/api/explain/examples', {}, { timeoutMs: 20000 });
  if (data.status !== 'ok' || !data.results.length) return;
  box.textContent = '';
  box.appendChild(el('span', 'filter-label', 'Load a real paper'));
  data.results.slice(0, 6).forEach((p) => {
    const short = p.title.length > 38 ? `${p.title.slice(0, 37)}…` : p.title;
    const b = el('button', 'quickdate', short);
    b.type = 'button';
    b.title = `${p.title}\n${p.category} · ${p.published}`
      + (p.saved ? '\nOn your shelf.' : '');
    b.addEventListener('click', () => {
      $('#lab-title').value = p.title;
      $('#lab-abstract').value = p.abstract;
      $('#lab-published').value = p.published;
      runLab();
    });
    box.appendChild(b);
  });
}

/* What 0.1 and 0.9 actually mean.

   Reported as: "we have to explain what's the difference between 0.1 and 0.9
   and make it intuitive." The honest answer turned out to be surprising enough
   to be worth drawing: against a real library the median matching paper scores
   about 0.12, a 0.50 is already above 97.6% of it, and this page's own worked
   example scores 0.70, which beats 99.5%. The scale is not a percentage. It is
   a position in a distribution squashed against the bottom, and the only way to
   say that is to show it. */
function drawScale(score, scale, percentile) {
  const box = $('#lab-scale');
  if (!box) return;
  box.textContent = '';
  if (!scale) {
    box.appendChild(el('p', 'sub',
      'Add a topic to see where this score sits among your own papers.'));
    return;
  }

  box.appendChild(el('div', 'sec', 'What that number is worth'));
  box.appendChild(el('p', 'scale-lead',
    `${score.toFixed(2)} is higher than ${percentile}% of the `
    + `${scale.library.toLocaleString()} papers you hold, scored against these same `
    + `topics. The middle paper that matches anything at all scores `
    + `${scale.median.toFixed(2)}, and the best paper in your library scores `
    + `${scale.top_score.toFixed(2)}.`));

  const bar = el('div', 'scalebar');
  scale.landmarks.forEach((lm) => {
    const tick = el('i', 'scaletick');
    tick.style.left = `${lm.score * 100}%`;
    tick.title = `${lm.score.toFixed(1)} beats ${lm.above}% of your library`;
    bar.appendChild(tick);
  });
  const fill = el('i', 'scalefill');
  fill.style.width = `${Math.min(100, score * 100)}%`;
  bar.appendChild(fill);
  const pin = el('i', 'scalepin');
  pin.style.left = `${Math.min(100, score * 100)}%`;
  bar.appendChild(pin);
  box.appendChild(bar);

  const ticks = el('div', 'scalerow');
  scale.landmarks.forEach((lm) => {
    const cell = el('div', 'scalecell');
    cell.appendChild(el('b', null, lm.score.toFixed(1)));
    cell.appendChild(el('span', null, `beats ${lm.above}%`));
    ticks.appendChild(cell);
  });
  box.appendChild(ticks);

  box.appendChild(el('p', 'scale-note',
    'Which is why the numbers look small. A paper has to match several of your '
    + 'topics, repeatedly, and be new, to reach even half way. Nothing is wrong '
    + 'with a 0.3.'));
}

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

  const matched = data.why.matched || [];
  const nTopics = data.why.topics_considered;
  const nMatched = data.why.topics_matched;
  const points = matched.reduce((sum, m) => sum + m.credit, 0);

  // Three questions, answered in order, each with its own number. The old
  // panel was a correct column of arithmetic that never said what it was
  // working out, so "sum / 4 topics, weighted 0.8 = 0.400" told you the
  // formula and nothing else.
  const step = (n, question, answer, value) => {
    const wrap = el('div', 'step');
    const head = el('div', 'step-head');
    head.appendChild(el('span', 'step-n', String(n)));
    head.appendChild(el('span', 'step-q', question));
    head.appendChild(el('span', 'step-v', value));
    wrap.appendChild(head);
    wrap.appendChild(el('div', 'step-a', answer));
    return wrap;
  };

  const one = step(1, 'How many of your topics does it mention?',
    matched.length
      ? `${nMatched} of your ${nTopics}. Not every word counts the same, so those `
        + `${nMatched} are worth ${points.toFixed(2)} points out of a possible `
        + `${nTopics.toFixed ? nTopics : nTopics}.00. That works out to `
        + `${(points / nTopics * 100).toFixed(0)}% of your list, and this part of the `
        + `score is worth up to 0.80, so it earns ${c.base.toFixed(2)}.`
      : 'None of them. Nothing you listed appears in this paper.',
    c.base.toFixed(2));

  if (matched.length) {
    const words = el('div', 'step-words');
    matched.forEach((m) => {
      const chip = el('span', 'wordchip');
      chip.appendChild(el('b', null, m.topic));
      chip.appendChild(el('span', null, '+' + m.credit.toFixed(2)));
      chip.title = {
        'common word': 'Shows up in most papers here, so matching it barely narrows '
          + 'anything down. Worth 0.20.',
        phrase: 'The whole phrase, word for word. The strongest thing a paper can '
          + 'match. Worth 1.00.',
      }[m.kind] || 'A real subject word, not filler. Worth 0.60.';
      chip.className += m.kind === 'common word' ? ' wordchip-weak' : '';
      words.appendChild(chip);
    });
    one.appendChild(words);
    one.appendChild(el('p', 'step-note',
      'Faded words are ones like "learning" or "model" that show up in almost every '
      + 'paper here. Matching one of those barely narrows anything down, so it counts '
      + 'for less.'));
  }
  box.appendChild(one);

  box.appendChild(step(2, 'Does it touch several of your interests at once?',
    nMatched >= 3
      ? `Yes, ${nMatched} of them. A paper sitting where three or more of your `
        + `interests overlap is usually more use to you than one that nails a single `
        + `topic, so it gets a flat 0.30.`
      : nMatched === 2
        ? 'Two of them, which earns a smaller flat 0.15.'
        : 'No. This needs at least two of your topics to earn anything.',
    '+' + (c.breadth_bonus || 0).toFixed(2)));

  box.appendChild(step(3, 'Is it new?',
    data.why.age_days == null
      ? 'No date set, so nothing is added here. Pick a date above and watch this move.'
      : data.why.age_days > 30
        ? `It is ${data.why.age_days} days old. Anything past 30 days gets nothing `
          + `from this. It can still score well on the two questions above.`
        : `It is ${data.why.age_days} days old. Fresh papers get up to 0.20, fading `
          + `to nothing by day 30, because this is built for a daily read.`,
    '+' + (c.recency || 0).toFixed(2)));

  const total = el('div', 'step step-total');
  const th = el('div', 'step-head');
  th.appendChild(el('span', 'step-q', 'Score'));
  th.appendChild(el('span', 'step-v', data.score.toFixed(2)));
  total.appendChild(th);
  total.appendChild(el('div', 'step-a',
    data.why.capped
      ? 'The three parts added up to more than 1.00, so it is capped there.'
      : `${c.base.toFixed(2)} plus ${(c.breadth_bonus || 0).toFixed(2)} plus `
        + `${(c.recency || 0).toFixed(2)}. The highest any paper can score is 1.00.`));
  box.appendChild(total);

  $('#lab-why').textContent = data.why_text;
  drawScale(data.score, data.scale, data.percentile);

  const chips = $('#lab-boiler');
  if (!chips.childElementCount) {
    (data.boilerplate || []).forEach((w) => chips.appendChild(el('span', 'tag', w)));
  }
}

/* What was typed, when it was not what was searched for.

   Reported as: a typo "doesn't correct me or show me things which probably I
   was looking for". It used to name the dead word and stop there, which is
   honest and still leaves you to find your own slip. Every suggestion here is a
   word that appears in papers you hold, so it carries the count, and the
   original spelling stays one click away because sometimes the word is right
   and the library is what is missing. */
function spellNotice() {
  const from = state.correctedFrom;
  const fixes = state.corrections || [];
  if (!from && !fixes.length) return null;
  const box = el('div', 'spellfix');

  if (from) {
    const line = el('div', 'spellfix-main');
    line.appendChild(el('span', null, 'Nothing in your library matched '));
    line.appendChild(el('i', null, `“${from}”`));
    line.appendChild(el('span', null, `, so this is showing results for `));
    line.appendChild(el('b', null, state.query));
    line.appendChild(el('span', null, '.'));
    box.appendChild(line);
    const keep = el('button', 'linkish', `Search for “${from}” exactly`);
    keep.type = 'button';
    keep.addEventListener('click', () => {
      $('#q').value = from;
      runSearch('search', { exact: '1' });
    });
    box.appendChild(keep);
    return box;
  }

  // Some words landed and some did not, so the results on screen are real and
  // the offer sits beside them rather than replacing them.
  const line = el('div', 'spellfix-main');
  line.appendChild(el('span', null,
    fixes.length > 1 ? 'These words are in no paper you hold: ' : 'This word is in no paper you hold: '));
  box.appendChild(line);
  const row = el('div', 'spellfix-row');
  fixes.forEach((fix) => {
    const chip = el('button', 'spellchip');
    chip.type = 'button';
    chip.appendChild(el('i', null, fix.term));
    chip.appendChild(el('span', 'spellchip-arrow', '→'));
    chip.appendChild(el('b', null, fix.suggestion));
    chip.appendChild(el('span', 'spellchip-n', `${fix.papers.toLocaleString()} papers`));
    chip.title = `Replace ${fix.term} with ${fix.suggestion} and search again`;
    chip.addEventListener('click', () => {
      $('#q').value = ($('#q').value || '').split(/\s+/)
        .map((w) => (w.toLowerCase() === fix.term ? fix.suggestion : w)).join(' ');
      runSearch('search');
    });
    row.appendChild(chip);
  });
  box.appendChild(row);
  return box;
}

function renderHeroText() {
  const hero = $('#hero');
  const old = hero.querySelector('.hero-text');
  if (old) old.remove();
  const wrap = el('div', 'hero-text');
  if (state.query) {
    wrap.appendChild(el('h1', null,
      state.mode === 'ask' ? `“${state.query}”` : `Results for “${state.query}”`));
    wrap.appendChild(el('p', null,
      'Ranked by how much of your query each paper covers, best first. Open any '
      + 'paper for what it’s about and its nearest neighbours. Esc clears the search.'));
    const fixed = spellNotice();
    if (fixed) wrap.appendChild(fixed);
  } else {
    wrap.appendChild(el('h1', null, 'Your library'));
    wrap.appendChild(el('p', null,
      'Everything you have fetched, best match first. Type keywords and press Enter, '
      + 'or ask a question and press Ask. Press ? for every shortcut.'));
  }
  hero.insertBefore(wrap, hero.firstChild);
}

/* ---------------- profile (editable) ---------------- */

const TIER_BLURB = {
  core: 'The subject you work in. Fetched deepest.',
  complementary: 'Adjacent lanes. Meant to season the feed, not flood it.',
  stretch: 'Fields you do not work in, read for method transfer. Matched on '
         + 'structural keywords like identification and confounding rather '
         + 'than on subject matter.',
};

const profileState = { data: null, dirty: false, draft: null };

function markDirty() {
  profileState.dirty = true;
  const save = $('#profile-save');
  if (save) {
    save.disabled = false;
    save.textContent = 'Save changes';
    save.classList.add('is-dirty');
  }
}

// An editable list of chips. Each chip removes itself; the input adds. This is
// the whole interaction — the previous screen rendered the same information as
// dead text and left editing to a JSON file the page never mentioned.
function editableChips(values, opts) {
  const wrap = el('div', 'chipedit');
  const list = el('div', 'chipedit-list');
  const model = values.slice();

  function draw() {
    list.textContent = '';
    if (!model.length) list.appendChild(el('span', 'dim', opts.empty || 'none yet'));
    model.forEach((value, i) => {
      const chip = el('span', 'chip-edit' + (opts.liveSet && opts.liveSet.has(value)
        ? ' chip-live' : ''));
      if (opts.liveSet && opts.liveSet.has(value)) {
        chip.title = 'In the next fetch. Only some of your words fit in one arXiv '
          + 'request, so the window moves each run until the whole list is covered.';
      }
      if (opts.describe) chip.title = opts.describe(value);
      chip.appendChild(el('span', null, value));
      if (opts.count) {
        const n = opts.count(value);
        if (n != null) {
          const badge = el('b', n ? null : 'chip-zero', String(n));
          badge.title = n
            ? `${n} papers held here`
            : 'Configured, holding nothing. It is asking and not delivering.';
          chip.appendChild(badge);
        }
      }
      const x = el('button', 'chip-x', '×');
      x.type = 'button';
      x.title = `Remove ${value}`;
      x.setAttribute('aria-label', `Remove ${value}`);
      x.addEventListener('click', () => {
        model.splice(i, 1);
        draw();
        opts.onChange(model.slice());
        markDirty();
      });
      chip.appendChild(x);
      list.appendChild(chip);
    });
  }

  const add = el('input', 'chipedit-input');
  add.type = 'text';
  add.placeholder = opts.placeholder || 'add and press Enter';
  add.setAttribute('aria-label', opts.placeholder || 'add');
  const commit = () => {
    const raw = add.value.trim();
    if (!raw) return;
    raw.split(',').map((s) => s.trim()).filter(Boolean).forEach((value) => {
      if (!model.some((m) => m.toLowerCase() === value.toLowerCase())) model.push(value);
    });
    add.value = '';
    draw();
    opts.onChange(model.slice());
    markDirty();
  };
  add.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    // Backspace on an empty box removes the last chip, as every tag input does.
    if (e.key === 'Backspace' && !add.value && model.length) {
      model.pop();
      draw();
      opts.onChange(model.slice());
      markDirty();
    }
  });
  add.addEventListener('blur', commit);

  draw();
  wrap.appendChild(list);
  const row = el('div', 'chipedit-row');
  row.appendChild(add);
  // "Add and press Enter" is only useful if you already know what to type.
  if (opts.suggest) {
    const more = el('button', 'tiny-btn', 'Suggest some');
    more.type = 'button';
    more.addEventListener('click', () => {
      const old = wrap.querySelector('.chip-suggests');
      if (old) return old.remove();
      const ideas = (opts.suggest() || []).filter(
        (s) => !model.some((m) => m.toLowerCase() === String(s).toLowerCase()));
      const box = el('div', 'chip-suggests');
      if (!ideas.length) {
        box.appendChild(el('span', 'dim', 'nothing to suggest that you do not already have'));
      } else {
        box.appendChild(chipRow(ideas.slice(0, 18), (text) => {
          model.push(text);
          draw();
          opts.onChange(model.slice());
          markDirty();
        }));
      }
      wrap.appendChild(box);
    });
    row.appendChild(more);
  }
  wrap.appendChild(row);
  return wrap;
}

// Populated from /api/categories on the first profile load, so a chip can say
// what cs.RO is without another round trip.
let CATEGORY_NAMES = {};

function suggestTopicsFor(tierName) {
  const cat = profileState.catalogue || {};
  const starter = (cat.starters || []).find((s) => s.key === profileState.starterKey)
    || (cat.starters || [])[0];
  const block = starter && (starter[tierName === 'stretch' ? 'core' : tierName]
    || starter.core);
  return (block && block.topics) || [];
}

async function openCategoryPicker(tierName) {
  const cat = profileState.catalogue;
  if (!cat) return toast('Still loading the category list.', 'bad');
  const modal = $('#picker');
  const body = $('#picker-body');
  body.textContent = '';
  $('#picker-title').textContent = `Add categories to ${tierName}`;
  modal.hidden = false;

  const current = new Set(profileState.draft.tiers[tierName].categories);

  const search = el('input', 'text-input');
  search.type = 'search';
  search.placeholder = 'filter by name or code. try "vision", "causal", "finance"';
  body.appendChild(search);

  const listing = el('div', 'picker-groups');
  body.appendChild(listing);

  function draw(filter) {
    listing.textContent = '';
    const needle = (filter || '').trim().toLowerCase();
    let shown = 0;
    cat.groups.forEach((group) => {
      const rows = group.categories.filter((row) =>
        !needle || row.code.toLowerCase().includes(needle)
        || row.name.toLowerCase().includes(needle)
        || row.blurb.toLowerCase().includes(needle));
      if (!rows.length) return;
      const section = el('div', 'picker-group');
      section.appendChild(el('h4', null, group.label));
      rows.forEach((row) => {
        shown += 1;
        const on = current.has(row.code);
        const item = el('button', 'picker-row' + (on ? ' is-on' : ''));
        item.type = 'button';
        const left = el('div', 'picker-left');
        const head = el('div', 'picker-head');
        head.appendChild(el('code', null, row.code));
        head.appendChild(el('b', null, row.name));
        if (row.held) head.appendChild(el('span', 'picker-held', `${row.held} held`));
        left.appendChild(head);
        left.appendChild(el('span', 'picker-blurb', row.blurb));
        item.appendChild(left);
        item.appendChild(el('span', 'picker-mark', on ? 'added' : '+'));
        item.addEventListener('click', () => {
          const list = profileState.draft.tiers[tierName].categories;
          if (current.has(row.code)) {
            current.delete(row.code);
            list.splice(list.indexOf(row.code), 1);
          } else {
            current.add(row.code);
            list.push(row.code);
          }
          markDirty();
          draw(search.value);
        });
        section.appendChild(item);
      });
      listing.appendChild(section);
    });
    if (!shown) listing.appendChild(el('p', 'dim', `Nothing matches “${filter}”.`));
  }

  search.addEventListener('input', () => draw(search.value));
  draw('');
  setTimeout(() => search.focus(), 30);
}

function closePicker() {
  $('#picker').hidden = true;
  // Reflect whatever the picker changed, without losing other unsaved edits.
  if (state.view === 'profile') renderProfile();
}

async function loadProfile() {
  const out = $('#view-profile');
  out.textContent = '';
  out.appendChild(el('p', 'dim', 'Loading your profile…'));

  const [data, cat, llm] = await Promise.all([
    get('/api/profile', {}, { timeoutMs: 20000 }),
    get('/api/categories', {}, { timeoutMs: 20000 }),
    // Fetched here too, not just at boot, so the model card is correct when
    // Profile is the first screen someone opens.
    get('/api/llm', {}, { timeoutMs: 8000 }),
  ]);
  if (llm.status === 'ok') {
    state.llm = llm.llm;
    state.llmProviders = llm.providers || [];
  }
  if (data.status !== 'ok') {
    out.textContent = '';
    out.appendChild(notice('Could not load the profile', data.message || data.status, loadProfile));
    return;
  }
  if (cat.status === 'ok') {
    profileState.catalogue = cat;
    CATEGORY_NAMES = {};
    cat.groups.forEach((g) => g.categories.forEach((c) => { CATEGORY_NAMES[c.code] = c; }));
  }
  profileState.data = data;
  profileState.dirty = false;
  profileState.draft = {
    work_context: data.work_context || '',
    workspace_root: data.workspace_root || '',
    tiers: {},
  };
  data.tiers.forEach((tier) => {
    profileState.draft.tiers[tier.name] = {
      categories: tier.categories.map((c) => c.name),
      topics: tier.topics.slice(),
      structural_keywords: tier.structural_keywords.slice(),
      per_category: tier.per_category,
    };
  });
  renderProfile();
}

function renderProfile() {
  const data = profileState.data;
  const draft = profileState.draft;
  const out = $('#view-profile');
  out.textContent = '';

  const held = {};
  (data.held_categories || []).forEach((row) => { held[row.name] = row.held; });

  const head = el('div', 'view-head');
  head.appendChild(el('h2', null, 'Your profile'));
  head.appendChild(el('p', 'sub',
    'What this tool asks arXiv for on your behalf, and what that has actually delivered. '
    + 'Everything here is editable, and the next fetch uses it.'));

  const stats = el('div', 'statrow');
  [[data.total_categories, 'categories'], [data.total_topics, 'topics'],
   [data.library_total, 'papers held'], [data.keywords_per_query, 'topics per request']]
    .forEach(([n, label]) => {
      const cell = el('div', 'stat');
      cell.appendChild(el('b', null, String(n)));
      cell.appendChild(el('span', null, label));
      stats.appendChild(cell);
    });
  head.appendChild(stats);
  out.appendChild(head);

  // What a fetch costs and why it is slow, said once, near the controls that
  // change it. arXiv's rate limit is the real constraint on this whole screen.
  const cost = data.fetch_cost || {};
  const mins = Math.floor((cost.estimated_seconds || 0) / 60);
  const secs = (cost.estimated_seconds || 0) % 60;
  const cats = data.total_categories;

  // The shape of a fetch, said as what you get rather than as protocol.
  const costBox = el('div', 'costbar');
  costBox.appendChild(el('b', null,
    `Each fetch asks ${cats} categories for up to ${cost.max_papers} papers, `
    + `and takes about ${mins ? mins + 'm ' : ''}${secs}s.`));
  costBox.appendChild(el('span', null,
    'Every category gets its own trip to arXiv, so the time comes from how many '
    + 'categories you have, not how many papers you ask for. Adding a category makes '
    + 'a fetch longer. Moving a papers-per-category slider does not.'));

  const how = el('details', 'method');
  how.appendChild(el('summary', null, 'What actually happens when you press Fetch'));
  const steps = el('ol', 'howlist');
  [
    `It goes through your ${cats} categories one at a time. Each one is a separate `
    + 'request, so no category can crowd out another.',
    'For each, it asks arXiv for papers in that category whose title or abstract '
    + 'contains at least one of your words. Not all of your words. Any one of them.',
    'Only some of your words fit in a single request, because the request is a web '
    + 'address and those have a length limit. So each run takes the next few words '
    + 'off your list, and the next run picks up where it left off. Over a few days '
    + 'your whole list gets used. The gold-highlighted words below are the ones '
    + 'going out next.',
    'It waits 5 seconds between requests. arXiv asks for that, and asking faster '
    + 'gets you turned away for 5 minutes, which is slower than waiting.',
    'Anything new gets added to your library. Anything you already have is left '
    + 'alone, so running this twice in a day costs you nothing but time.',
  ].forEach((line) => steps.appendChild(el('li', null, line)));
  how.appendChild(steps);
  costBox.appendChild(how);

  if (data.cooldown_remaining > 0) {
    costBox.className = 'costbar costbar-warn';
    costBox.appendChild(el('b', null,
      `arXiv is not taking requests from you right now. `
      + `${Math.ceil(data.cooldown_remaining)} seconds left. A fetch will wait for `
      + `that rather than keep asking, which is what makes it worse.`));
  }
  out.appendChild(costBox);

  // Starting points, for the reader who has just met the word "cs.MA". The
  // profile screen assumes you already know which corner of arXiv you want,
  // and a new library ships with whatever the defaults were, forever, because
  // nothing ever suggests otherwise.
  const starters = (profileState.catalogue || {}).starters || [];
  if (starters.length) {
    const card = el('section', 'profile-card');
    const title = el('div', 'profile-title');
    title.appendChild(el('h3', null, 'Start from a shape'));
    card.appendChild(title);
    card.appendChild(el('p', 'sub',
      'Each of these replaces the core and complementary tiers with a set that is '
      + 'known to return papers. Nothing is saved until you press Save, so you can '
      + 'try one, look at what it would ask for, and reload from disk to undo.'));
    const row = el('div', 'starter-row');
    starters.forEach((s) => {
      const b = el('button', 'starter');
      b.type = 'button';
      b.appendChild(el('b', null, s.label));
      b.appendChild(el('span', null, s.blurb));
      b.appendChild(el('span', 'starter-cats',
        (s.core.categories || []).join('  ')));
      b.addEventListener('click', () => {
        if (!window.confirm(
          `Replace the core and complementary tiers with "${s.label}"?\n\n`
          + 'Your stretch tier, work context and workspace folder are left alone, '
          + 'and nothing is written until you press Save.')) return;
        profileState.starterKey = s.key;
        ['core', 'complementary'].forEach((tier) => {
          if (!s[tier]) return;
          profileState.draft.tiers[tier].categories = (s[tier].categories || []).slice();
          profileState.draft.tiers[tier].topics = (s[tier].topics || []).slice();
        });
        markDirty();
        renderProfile();
        toast(`Loaded "${s.label}". Review it, then Save.`);
      });
      row.appendChild(b);
    });
    card.appendChild(row);
    out.appendChild(card);
  }

  const bar = el('div', 'savebar');
  const save = el('button', 'btn', 'Saved');
  save.id = 'profile-save';
  save.type = 'button';
  save.disabled = true;
  save.addEventListener('click', saveProfile);
  bar.appendChild(save);
  const revert = el('button', 'btn-ghost', 'Reload from disk');
  revert.type = 'button';
  revert.addEventListener('click', loadProfile);
  bar.appendChild(revert);
  const suggest = el('button', 'btn-ghost', 'Suggest topics from what I saved');
  suggest.type = 'button';
  suggest.addEventListener('click', () => loadSuggestions(suggest));
  bar.appendChild(suggest);
  const exportBtn = el('button', 'btn-ghost', 'Export library');
  exportBtn.type = 'button';
  exportBtn.addEventListener('click', () => downloadExport('all', 'json'));
  bar.appendChild(exportBtn);
  out.appendChild(bar);
  out.appendChild(el('div', 'suggestions', ''));

  // Work context — the sentence a model and the digest both read.
  const ctx = el('section', 'profile-card');
  ctx.appendChild(el('h3', null, 'What you work on'));
  ctx.appendChild(el('p', 'sub',
    'Plain English, for your own reference and for a local model reading your questions. '
    + 'It does not affect ranking.'));
  const ctxBox = el('textarea', 'notefield');
  ctxBox.value = draft.work_context;
  ctxBox.rows = 4;
  ctxBox.placeholder = 'e.g. building agent systems, mostly interested in evaluation and reliability.';
  ctxBox.addEventListener('input', () => { draft.work_context = ctxBox.value; markDirty(); });
  ctx.appendChild(ctxBox);
  out.appendChild(ctx);

  // Workspace folder — the thing that makes the workspace question possible.
  const ws = el('section', 'profile-card');
  ws.appendChild(el('h3', null, 'Workspace folder'));
  ws.appendChild(el('p', 'sub',
    'Optional. Name a folder and you can ask “what relates to what I am building?”. '
    + 'It reads README and manifest files and source file headers to work out your '
    + 'terms. Nothing is read until you set this, and nothing read here leaves the machine.'));
  const wsRow = el('div', 'inline-row');
  const wsBox = el('input', 'text-input');
  wsBox.type = 'text';
  wsBox.value = draft.workspace_root;
  wsBox.placeholder = 'C:\\Users\\you\\Code   or   /home/you/code';
  wsBox.setAttribute('aria-label', 'Workspace folder');
  wsBox.addEventListener('input', () => { draft.workspace_root = wsBox.value; markDirty(); });
  wsRow.appendChild(wsBox);
  const wsTest = el('button', 'btn-ghost', 'Preview what it reads');
  wsTest.type = 'button';
  wsTest.addEventListener('click', () => previewWorkspace(wsBox.value, ws));
  wsRow.appendChild(wsTest);
  ws.appendChild(wsRow);
  out.appendChild(ws);

  // What the library is missing, as a picture. A row of bars says more about
  // whether this thing is actually running than any number can.
  const cov = state.coverage;
  if (cov && (cov.months || []).length > 1) {
    const card = el('section', 'profile-card');
    const title = el('div', 'profile-title');
    title.appendChild(el('h3', null, 'What you have, month by month'));
    if ((cov.gaps || []).length) {
      const flag = el('span', 'pill pill-warn',
        `${cov.gaps.length} month${cov.gaps.length > 1 ? 's' : ''} missing`);
      title.appendChild(flag);
    }
    card.appendChild(title);
    card.appendChild(el('p', 'sub',
      'Counted by when each paper was published. A short bar is a month you barely '
      + 'fetched, and an empty one is a month you cannot search at all, because a '
      + 'fetch always starts from the newest paper and never goes back on its own.'));

    const chart = el('div', 'monthchart');
    const peak = Math.max(...cov.months.map((m) => m.papers), 1);
    cov.months.forEach((m) => {
      const col = el('div', 'monthcol' + (m.papers ? '' : ' monthcol-gap'));
      col.title = m.papers
        ? `${m.papers} papers published in ${prettyMonth(m.month)}`
        : `Nothing from ${prettyMonth(m.month)}. Click to fetch it.`;
      const bar = el('div', 'monthbar');
      const fill = el('i');
      fill.style.height = Math.max(2, (m.papers / peak) * 100) + '%';
      bar.appendChild(fill);
      col.appendChild(bar);
      col.appendChild(el('span', 'monthn', String(m.papers)));
      col.appendChild(el('span', 'monthlabel', m.month.slice(2)));
      if (!m.papers) {
        col.tabIndex = 0;
        col.setAttribute('role', 'button');
        const go = () => backfill(m.month, col);
        col.addEventListener('click', go);
        col.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); }
        });
      }
      chart.appendChild(col);
    });
    card.appendChild(chart);
    if (cov.outside_window) {
      card.appendChild(el('p', 'step-note',
        `${cov.outside_window} older papers sit outside this window, picked up one at `
        + `a time by searches and cross-listings rather than by a fetch. They are in `
        + `your library and searchable; they are just not a month you ever pulled.`));
    }
    out.appendChild(card);
  }

  // Where the library came from. Every score in this tool explains itself;
  // this is the same idea applied to the papers, and it goes in before more
  // sources arrive rather than after, because a library assembled from four
  // services where you cannot tell which is which produces exactly the
  // confusion the scores were fixed to avoid.
  const sources = data.sources || [];
  if (sources.length) {
    const card = el('section', 'profile-card');
    card.appendChild(el('h3', null, 'Where your papers came from'));
    card.appendChild(el('p', 'sub',
      'Different routes into the library reach different things. A keyword fetch '
      + 'only ever finds papers matching words you already chose. A bulk harvest '
      + 'takes everything in your categories for a stretch of time, which is why '
      + 'it is the one that fills holes.'));
    const bar = el('div', 'sourcebar');
    sources.forEach((s, i) => {
      const seg = el('div', 'sourceseg sourceseg-' + (i % 4));
      seg.style.flexGrow = String(Math.max(1, s.papers));
      seg.title = `${s.papers} papers (${s.share}%), ${s.label}`;
      bar.appendChild(seg);
    });
    card.appendChild(bar);
    const legend = el('div', 'sourcelegend');
    sources.forEach((s, i) => {
      const row = el('div', 'sourcerow');
      row.appendChild(el('span', 'sourcedot sourceseg-' + (i % 4)));
      row.appendChild(el('b', null, String(s.papers)));
      row.appendChild(el('span', null, s.label));
      row.appendChild(el('span', 'dim', `${s.share}%`));
      legend.appendChild(row);
    });
    card.appendChild(legend);
    out.appendChild(card);
  }

  out.appendChild(modelCard());

  // The tiers.
  data.tiers.forEach((tier) => {
    const t = draft.tiers[tier.name];
    const card = el('section', 'profile-card');
    const title = el('div', 'profile-title');
    title.appendChild(el('h3', null, tier.name));
    const live = el('span', 'pill', `${tier.keywords_this_run.length} of ${tier.keywords_total} topics next run`);
    live.title = 'A search_query is a URL parameter, so the topic list is asked in '
      + 'windows. The window moves each run, so the whole list gets covered.';
    title.appendChild(live);
    card.appendChild(title);
    card.appendChild(el('p', 'sub', TIER_BLURB[tier.name] || ''));

    const catHead = el('div', 'fieldlabel-row');
    catHead.appendChild(el('span', 'fieldlabel', 'arXiv categories'));
    const browse = el('button', 'tiny-btn', 'Browse categories');
    browse.type = 'button';
    browse.addEventListener('click', () => openCategoryPicker(tier.name));
    catHead.appendChild(browse);
    card.appendChild(catHead);
    card.appendChild(el('p', 'sub',
      'The sections of arXiv this tier looks in. The number on each is how many '
      + 'papers you already hold from it. A zero means you have asked for that '
      + 'section and got nothing back, which is worth knowing: either it is a slow '
      + 'section, or your words never match anything in it.'));
    const liveSet = new Set(tier.keywords_this_run);
    card.appendChild(editableChips(t.categories, {
      placeholder: 'cs.AI, stat.ME…',
      empty: 'no categories, so this tier fetches nothing',
      count: (name) => held[name] || 0,
      describe: (name) => {
        const d = CATEGORY_NAMES[name];
        return d ? `${name}: ${d.name}. ${d.blurb}` : `${name} is not a category this tool knows.`;
      },
      onChange: (v) => { t.categories = v; },
    }));

    // Stretch matches on method words, not subject words, so calling its
    // keyword box "Topics" and then reporting "6 of 24 topics next run" beside
    // an empty topics list was the screen contradicting itself.
    const isStretch = tier.name === 'stretch';
    if (!isStretch || t.topics.length) {
      card.appendChild(el('div', 'fieldlabel', 'Topics'));
      const topicHelp = el('p', 'sub');
      topicHelp.appendChild(document.createTextNode(
        'Words a paper has to contain in its title or abstract. A paper only needs '
        + 'one of them, not all of them. Leave this empty and the tier just takes '
        + 'whatever is newest, which in a busy section is the last hour of posts '
        + 'rather than the day\'s best. '));
      const gold = el('span', 'chip-edit chip-live inline-demo', 'like this');
      topicHelp.appendChild(gold);
      topicHelp.appendChild(document.createTextNode(
        ` means the word is in the next fetch. Only ${data.keywords_per_query} of your `
        + 'words fit in one request, so it works through the list a few at a time and '
        + 'the highlight moves along with it.'));
      card.appendChild(topicHelp);
      card.appendChild(editableChips(t.topics, {
        placeholder: 'add a topic and press Enter',
        empty: 'no topics, so this tier takes whatever is newest',
        liveSet,
        suggest: () => suggestTopicsFor(tier.name),
        onChange: (v) => { t.topics = v; },
      }));
    }

    if (isStretch || t.structural_keywords.length) {
      card.appendChild(el('div', 'fieldlabel', 'Structural keywords'));
      card.appendChild(el('p', 'sub',
        'Method words rather than subject words. How a paper was done, not what it '
        + 'is about. These are this tier\'s search terms: you are not reading '
        + 'econometrics for the economics, you are reading it for how they establish '
        + 'a claim.'));
      card.appendChild(editableChips(t.structural_keywords, {
        placeholder: 'identification, ablation…',
        liveSet,
        suggest: () => (profileState.catalogue || {}).structural_suggestions || [],
        onChange: (v) => { t.structural_keywords = v; },
      }));
    }

    card.appendChild(el('div', 'fieldlabel', 'How deep to go'));
    const depth = el('div', 'inline-row');
    const range = el('input', 'range');
    range.type = 'range';
    range.min = '5';
    range.max = '100';
    range.step = '5';
    range.value = String(t.per_category);
    range.setAttribute('aria-label', 'Papers per category per run');
    const out2 = el('b', 'rangeval', String(t.per_category));
    const depthNote = el('p', 'profile-meta');
    const redraw = () => {
      const n = Number(range.value);
      depthNote.textContent =
        `Each of these ${tier.requests} categories hands back its ${n} most recent `
        + `matching papers, so this tier brings home up to ${n * tier.requests} per run. `
        + `Turning this up costs no extra time, because it is the same number of trips `
        + `to arXiv either way. It only means each trip comes back fuller. Turn it up `
        + `if a category is busy and you suspect you are seeing the last hour instead `
        + `of the day. Turn it down if the feed is drowning you.`;
    };
    range.addEventListener('input', () => {
      t.per_category = Number(range.value);
      out2.textContent = range.value;
      redraw();
      markDirty();
    });
    redraw();
    depth.appendChild(range);
    depth.appendChild(out2);
    card.appendChild(depth);
    card.appendChild(depthNote);

    if (tier.example_query) {
      const det = el('details', 'profile-query');
      det.appendChild(el('summary', null, 'The exact query this sends to arXiv'));
      det.appendChild(el('code', null, tier.example_query));
      // The OR question, answered where it gets asked.
      det.appendChild(el('p', 'sub',
        'In plain English: find papers in this section that mention any one of these '
        + 'words.'));
      det.appendChild(el('p', 'sub',
        'The section is joined with AND because a paper has to be in it to count. '
        + 'The words are joined with OR because they are separate things you are '
        + 'interested in, not a checklist a paper has to satisfy. Joining them with '
        + 'AND would be asking for the single paper that is about every topic you '
        + 'have at once, and no such paper exists, so you would get nothing back. '
        + 'Each word appears twice because it is checked against the title and '
        + 'against the abstract.'));
      det.appendChild(el('p', 'sub',
        `This particular query carries ${tier.keywords_this_run.length} of your `
        + `${tier.keywords_total} words. The next run carries the next few.`));
      card.appendChild(det);
    }
    out.appendChild(card);
  });

  // Categories the library holds that nothing asks for.
  const unasked = (data.held_categories || []).filter((r) => !r.configured && r.held >= 3);
  if (unasked.length) {
    const card = el('section', 'profile-card');
    card.appendChild(el('h3', null, 'Arriving without being asked for'));
    card.appendChild(el('p', 'sub',
      'Papers land in these because a paper can be cross-listed. They are not in your '
      + 'profile, so nothing is fetched for them deliberately. Click one to add it to core.'));
    card.appendChild(chipRow(unasked.slice(0, 14).map((r) => `${r.name} (${r.held})`), (label) => {
      const name = label.split(' ')[0];
      const core = profileState.draft.tiers.core;
      if (core && !core.categories.includes(name)) {
        core.categories.push(name);
        markDirty();
        renderProfile();
        toast(`${name} added to core. Save to keep it.`);
      }
    }));
    out.appendChild(card);
  }

  const foot = el('p', 'sub');
  foot.style.marginTop = '18px';
  foot.textContent = `Stored in ${data.home}. Everything on this page is that file.`;
  out.appendChild(foot);
}

// Which model reads your questions, and where it lives. Local or hosted, your
// call. The one thing the page will not do is keep saying "stays on your
// machine" once you have pointed it somewhere else.
function modelCard() {
  const card = el('section', 'profile-card');
  const title = el('div', 'profile-title');
  title.appendChild(el('h3', null, 'Who reads your questions'));
  const llm = state.llm || {};
  const pill = el('span', 'pill', {
    ready: 'connected', absent: 'not connected', off: 'turned off',
    no_models: 'no models', model_missing: 'model missing',
  }[llm.status] || 'unknown');
  if (llm.status === 'ready') pill.className = 'pill pill-good';
  title.appendChild(pill);
  card.appendChild(title);
  card.appendChild(el('p', 'sub',
    'Optional. Typing a question works without any of this: there is a built-in '
    + 'reader that strips the question words and keeps the subject. A model does '
    + 'that job better on messier sentences. It never decides the order of your '
    + 'results, and it never decides to go and search arXiv. Those are both yours.'));

  const body = el('div', 'modelgrid');
  const providers = (state.llmProviders || []);

  const pick = el('select', 'text-input');
  pick.setAttribute('aria-label', 'Where the model runs');
  providers.forEach((p) => {
    const opt = el('option', null, p.label);
    opt.value = p.key;
    if (p.key === llm.provider) opt.selected = true;
    pick.appendChild(opt);
  });
  body.appendChild(labelled('Where it runs', pick));

  const url = el('input', 'text-input');
  url.type = 'text';
  url.value = llm.base_url || '';
  url.placeholder = 'http://127.0.0.1:11434';
  body.appendChild(labelled('Address', url));

  const model = el('input', 'text-input');
  model.type = 'text';
  model.value = llm.model || '';
  model.placeholder = (llm.models || [])[0] || 'qwen2.5:3b';
  body.appendChild(labelled('Model', model));

  const key = el('input', 'text-input');
  key.type = 'password';
  key.value = '';
  key.placeholder = llm.has_key ? 'saved, leave blank to keep' : 'only for a paid service';
  key.autocomplete = 'off';
  body.appendChild(labelled('Key, if it needs one', key));
  card.appendChild(body);

  const chosen = () => providers.find((p) => p.key === pick.value) || {};
  const blurb = el('p', 'sub', (chosen().blurb || ''));
  const setup = el('p', 'step-note', chosen().setup || '');
  pick.addEventListener('change', () => {
    blurb.textContent = chosen().blurb || '';
    setup.textContent = chosen().setup || '';
    if (!url.value || providers.some((p) => p.default_url === url.value)) {
      url.value = chosen().default_url || '';
    }
  });
  card.appendChild(blurb);
  card.appendChild(setup);

  if ((llm.models || []).length) {
    const found = el('div', 'chips');
    found.appendChild(el('span', 'reading-tag', 'available'));
    llm.models.slice(0, 12).forEach((name) => {
      const b = el('button', 'suggestion', name);
      b.type = 'button';
      b.addEventListener('click', () => { model.value = name; });
      found.appendChild(b);
    });
    card.appendChild(found);
  }

  const status = el('p', llm.local === false ? 'privacy privacy-remote' : 'privacy',
    llm.privacy || '');
  card.appendChild(status);
  if (llm.message) card.appendChild(el('p', 'step-note', llm.message));

  const row = el('div', 'inline-row');
  const apply = el('button', 'btn', 'Connect');
  apply.type = 'button';
  apply.addEventListener('click', async () => {
    apply.disabled = true;
    apply.textContent = 'Checking…';
    const payload = {
      provider: pick.value, base_url: url.value.trim(),
      model: model.value.trim(), enabled: true,
    };
    if (key.value.trim()) payload.api_key = key.value.trim();
    const result = await post('/api/llm', payload, { timeoutMs: 20000 });
    apply.disabled = false;
    apply.textContent = 'Connect';
    if (result.status !== 'ok') return toast(result.message || 'Could not save.', 'bad');
    state.llm = result.llm;
    state.llmProviders = providers;
    toast(result.llm.message || 'Saved.');
    renderProfile();
    loadLlmStrip();
  });
  row.appendChild(apply);

  const off = el('button', 'btn-ghost', llm.enabled === false ? 'Turn on' : 'Turn off');
  off.type = 'button';
  off.addEventListener('click', async () => {
    const result = await post('/api/llm', { enabled: llm.enabled === false });
    if (result.status !== 'ok') return toast(result.message || 'Could not save.', 'bad');
    state.llm = result.llm;
    toast(llm.enabled === false ? 'Model back on.'
      : 'Turned off. Questions are read by the built-in reader.');
    renderProfile();
    loadLlmStrip();
  });
  row.appendChild(off);
  card.appendChild(row);
  return card;
}

function labelled(text, field) {
  const wrap = el('div', 'modelfield');
  const id = 'f-' + text.toLowerCase().replace(/[^a-z]+/g, '-');
  field.id = id;
  const label = el('label', 'fieldlabel', text);
  label.setAttribute('for', id);
  wrap.appendChild(label);
  wrap.appendChild(field);
  return wrap;
}

async function previewWorkspace(root, container) {
  const old = container.querySelector('.ws-preview');
  if (old) old.remove();
  const box = el('div', 'ws-preview');
  box.appendChild(el('p', 'dim', 'Reading…'));
  container.appendChild(box);
  const data = await get('/api/workspace', { root: root || '', limit: 18 },
    { timeoutMs: 60000 });
  box.textContent = '';
  if (data.status !== 'ok') {
    box.appendChild(notice('Could not read that folder', data.message || data.status));
    return;
  }
  box.appendChild(el('p', 'sub', data.how));
  box.appendChild(el('p', 'dim',
    `Projects seen: ${(data.projects || []).slice(0, 8).join(', ') || 'none'}`));
  const row = el('div', 'chips');
  (data.terms || []).forEach((t) => {
    const chip = el('button', 'suggestion', `${t.term} · ${t.spread} projects`);
    chip.type = 'button';
    chip.title = `In ${t.files} files across ${(t.projects || []).join(', ')}`;
    chip.addEventListener('click', () => { $('#q').value = t.term; runSearch('search'); });
    row.appendChild(chip);
  });
  box.appendChild(row);
}

async function loadSuggestions(button) {
  const box = $('#view-profile .suggestions');
  if (!box) return;
  button.disabled = true;
  button.textContent = 'Reading your saved papers…';
  const data = await get('/api/suggest-terms', { limit: 15 }, { timeoutMs: 20000 });
  button.disabled = false;
  button.textContent = 'Suggest topics from what I saved';
  box.textContent = '';
  if (data.status === 'error') {
    box.appendChild(notice('Could not read your saved papers', data.message));
    return;
  }
  const card = el('section', 'profile-card');
  card.appendChild(el('h3', null, 'What you save but never ask for'));
  card.appendChild(el('p', 'sub', data.message + ' ' + (data.how || '')));
  if ((data.results || []).length) {
    card.appendChild(chipRow(
      data.results.map((r) => `${r.term} (${r.saved_papers})`),
      (label) => {
        const term = label.replace(/\s*\(\d+\)$/, '');
        const core = profileState.draft.tiers.core;
        if (core && !core.topics.some((t) => t.toLowerCase() === term)) {
          core.topics.push(term);
          markDirty();
          renderProfile();
          toast(`“${term}” added to core topics. Save to keep it.`);
        }
      }));
  }
  box.appendChild(card);
}

async function saveProfile() {
  const save = $('#profile-save');
  save.disabled = true;
  save.textContent = 'Saving…';
  const result = await post('/api/profile', profileState.draft);
  if (result.status !== 'ok') {
    save.disabled = false;
    save.textContent = 'Save changes';
    toast(result.message || 'Could not save.', 'bad');
    return;
  }
  (result.rejected || []).forEach((line) => toast(line, 'bad'));
  toast(result.message);
  await loadProfile();
  await refreshHeaderMeta();
}

/* ---------------- the optional local model ---------------- */

async function loadLlmStrip() {
  const data = await get('/api/llm', {}, { timeoutMs: 6000 });
  const strip = $('#llm-strip');
  strip.textContent = '';
  if (data.status !== 'ok') { strip.hidden = true; return; }
  const llm = data.llm || {};
  state.llm = llm;
  state.llmProviders = data.providers || [];

  // Quiet by design. A tool that nags about an optional dependency every time
  // you open it has made the optional dependency mandatory in practice.
  if (llm.dismissed || llm.status === 'ready') { strip.hidden = true; return; }

  strip.hidden = false;
  strip.className = 'strip';
  const text = el('span', null, llm.message || '');
  strip.appendChild(text);

  if (llm.status === 'absent' || llm.status === 'no_models') {
    const how = el('button', 'strip-link', 'How to add one');
    how.type = 'button';
    how.addEventListener('click', () => {
      strip.textContent = '';
      strip.appendChild(el('span', null, llm.hint));
      const ok = el('button', 'strip-link', 'Got it');
      ok.type = 'button';
      ok.addEventListener('click', () => dismissLlm());
      strip.appendChild(ok);
    });
    strip.appendChild(how);
  }
  const x = el('button', 'strip-x', '×');
  x.type = 'button';
  x.title = 'Hide this. Questions keep working.';
  x.setAttribute('aria-label', 'Hide');
  x.addEventListener('click', () => dismissLlm());
  strip.appendChild(x);
}

async function dismissLlm() {
  $('#llm-strip').hidden = true;
  await post('/api/llm', { dismissed: true });
}

/* ---------------- views ---------------- */

const VIEWS = ['grid', 'digest', 'map', 'trends', 'queue', 'profile', 'scoring'];

function setView(name) {
  if (state.view === 'profile' && name !== 'profile' && profileState.dirty) {
    if (!window.confirm('You have unsaved profile changes. Leave without saving?')) return;
    profileState.dirty = false;
  }
  state.view = name;
  document.querySelectorAll('.view-tab').forEach((b) =>
    b.classList.toggle('active', b.dataset.view === name));
  VIEWS.forEach((v) => { $('#view-' + v).hidden = v !== name; });
  $('#filters').style.visibility = name === 'grid' ? '' : 'hidden';
  $('#sort').style.visibility = name === 'grid' ? '' : 'hidden';
  $('#status').hidden = name !== 'grid';
  if (name === 'digest') loadDigest();
  if (name === 'map') loadMap();
  if (name === 'trends') loadTrends();
  if (name === 'queue') loadQueue();
  if (name === 'scoring') {
    drawTopicChips();
    drawLabExamples();
    runLab();
  }
  if (name === 'profile') loadProfile();
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
    renderReading(null);
    loadGrid(false);
  });
});

$('#sort').addEventListener('click', () => {
  state.sort = state.sort === 'score' ? 'date' : 'score';
  $('#sort').textContent = 'Sort: ' + (state.sort === 'score' ? 'best match' : 'newest');
  loadGrid(false);
});

$('#go').addEventListener('click', () => runSearch('search'));
$('#ask').addEventListener('click', () => runSearch('ask'));

$('#q').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    // Ctrl/Cmd+Enter always asks. A plain Enter asks too when what you typed
    // reads as a question — pressing Enter after typing a sentence and getting
    // a bag-of-words search is the tool ignoring what you plainly meant.
    const asking = e.ctrlKey || e.metaKey || looksLikeQuestion($('#q').value);
    runSearch(asking ? 'ask' : 'search');
  }
  if (e.key === 'Escape') {
    $('#q').value = '';
    state.query = '';
    renderReading(null);
    loadGrid(false);
  }
});

// Mirrors askparse.looks_like_a_question, deliberately loosely: getting it
// wrong here costs one keystroke, never a result.
function looksLikeQuestion(text) {
  const low = String(text || '').trim().toLowerCase();
  if (low.endsWith('?')) return true;
  const words = low.split(/\s+/);
  if (words.length < 4) return false;
  return /^(can|could|would|what|which|who|how|where|when|why|is|are|do|does|did|show|find|give|tell|search|look|any|anything|i)\b/.test(low)
    || /\b(related to|papers about|papers on|looking for|anything about)\b/.test(low);
}

/* The header button now does what the panel's button does, and shows it in the
   same place. It used to hold a request open for up to four minutes behind the
   word "Fetching…", which is the same amount of information as a spinner: you
   could not tell a long harvest from a hung one, or find out which service it
   was even talking to. */
$('#refresh').addEventListener('click', async () => {
  setView('grid');
  await renderConn('#conn-grid');
  const box = $('#conn-grid').querySelector('.conn-box');
  if (!box) return;
  box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  startFetch({}, box);
});

$('#panel-x').addEventListener('click', closePanel);
$('#scrim').addEventListener('click', closePanel);

const KEYS = [
  ['/', 'jump to the search box'],
  ['Enter', 'search, or ask if what you typed reads as a question'],
  ['Ctrl/⌘ + Enter', 'always ask, never keyword-search'],
  ['1 to 7', 'papers, digest, map, trends, queue, profile, scoring'],
  ['j / k', 'next / previous paper, with one open'],
  ['s', 'star the open paper'],
  ['Esc', 'close the paper, or clear the search'],
  ['?', 'this list'],
];

function toggleKeymap(show) {
  const box = $('#keymap');
  if (show && !$('#keymap-list').childElementCount) {
    const list = $('#keymap-list');
    KEYS.forEach(([key, what]) => {
      list.appendChild(el('dt', null, key));
      list.appendChild(el('dd', null, what));
    });
  }
  box.hidden = !show;
}
$('#picker-close').addEventListener('click', closePicker);
$('#picker-done').addEventListener('click', closePicker);
$('#picker').addEventListener('click', (e) => {
  if (e.target === $('#picker')) closePicker();
});

// One-click dates for the scoring playground, because the point of that screen
// is watching recency move and typing a date by hand is friction in the way.
(function quickDates() {
  const box = $('#lab-quickdates');
  const iso = (days) => {
    const d = new Date();
    d.setDate(d.getDate() - days);
    return d.toISOString().slice(0, 10);
  };
  [['Today', 0], ['A week ago', 7], ['A month ago', 30], ['Six months ago', 182],
   ['No date', null]].forEach(([label, days]) => {
    const b = el('button', 'tiny-btn', label);
    b.type = 'button';
    b.addEventListener('click', () => {
      $('#lab-published').value = days == null ? '' : iso(days);
      runLab();
    });
    box.appendChild(b);
  });
})();

$('#keymap-open').addEventListener('click', () => toggleKeymap(true));
$('#keymap-close').addEventListener('click', () => toggleKeymap(false));
$('#keymap').addEventListener('click', (e) => {
  if (e.target === $('#keymap')) toggleKeymap(false);
});
$('#slash-hint').addEventListener('click', () => $('#q').focus());

document.addEventListener('keydown', (e) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
  if (e.key === '/' && !typing) { e.preventDefault(); $('#q').focus(); return; }
  if (e.key === '?' && !typing) { e.preventDefault(); toggleKeymap(true); return; }
  if (typing) return;
  if (e.key === 'Escape') {
    if (!$('#picker').hidden) return closePicker();
    if (!$('#keymap').hidden) return toggleKeymap(false);
    return closePanel();
  }
  if (!$('#picker').hidden) return;
  // One key per tab. The tabs used to advertise a "(6)" that was not bound.
  if (/^[1-9]$/.test(e.key) && Number(e.key) <= VIEWS.length) {
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
    if (e.key === 's') {
      e.preventDefault();
      const row = state.papers[state.index];
      if (row) get('/api/save', { id: row.id }).then((r) => {
        if (r.status !== 'ok') return;
        row.saved = r.saved;
        forgetPaper(row.id);
        renderGrid();
        openPanel(state.index);
        toast(r.saved ? 'Saved to your shelf.' : 'Removed from saved.');
      });
    }
  }
});

['#lab-topics', '#lab-title', '#lab-abstract', '#lab-published'].forEach((sel) => {
  $(sel).addEventListener('input', () => {
    clearTimeout(labTimer);
    labTimer = setTimeout(() => { drawTopicChips(); runLab(); }, 180);
  });
});

window.addEventListener('beforeunload', (e) => {
  if (profileState.dirty) { e.preventDefault(); e.returnValue = ''; }
});

/* ---------------- boot ---------------- */

async function refreshHeaderMeta() {
  const s = await get('/api/status');
  if (s.status !== 'ok' && s.status !== 'empty_library') return s;
  const meta = $('#header-meta');
  meta.textContent = '';
  const add = (label, value, cls, title) => {
    const span = el('span', cls);
    span.appendChild(document.createTextNode(label + ' '));
    span.appendChild(el('b', null, value));
    if (title) span.title = title;
    meta.appendChild(span);
  };
  add('papers', String(s.papers ?? 0));
  add('saved', String(s.saved ?? 0));
  const emb = s.embeddings || {};
  add('vectors', emb.available === false ? 'not installed'
    : emb.usable ? String(emb.vectors || 0) : 'rebuild',
    emb.usable === false ? 'meta-stale' : null,
    emb.warning || emb.hint || 'Vectors power the "Similar" list on each paper.');

  // To the minute, not to the day. `last_fetch_at` is only present once this
  // version has fetched at least once; before that the date is all there is,
  // and it says so rather than inventing a time.
  const stamp = humanStamp(s.last_fetch_at);
  const runs = s.runs || [];
  const last = runs.length ? runs[runs.length - 1] : null;
  const lastDate = typeof last === 'string' ? last : (last && last.date);
  if (stamp) {
    add('last fetch', stamp.short, stamp.days > 7 ? 'meta-stale' : null,
      `${stamp.full}${s.last_fetch_source ? ' · from the ' + s.last_fetch_source : ''}`
      + `${s.last_added != null ? ' · ' + s.last_added + ' new' : ''}`
      + ` · ${runs.length} runs recorded, library spans ${s.earliest} to ${s.latest}`);
  } else if (lastDate) {
    const days = Math.floor(
      (Date.now() - new Date(lastDate + 'T00:00:00').getTime()) / 86400000);
    add('last fetch', humanDate(lastDate), days > 7 ? 'meta-stale' : null,
      `${humanDate(lastDate)}, ${days === 0 ? 'today' : days === 1 ? 'yesterday'
        : days + ' days ago'}.\n\n`
      + 'The clock time was not recorded before this version, so only the date is '
      + 'known. Rather than show a time it would have to invent, it shows none. '
      + 'The next fetch records the exact minute.\n\n'
      + `${runs.length} runs recorded. Library spans ${s.earliest} to ${s.latest}.`);
  } else {
    add('last fetch', 'never');
  }

  state.coverage = s.coverage || null;
  if (s.coverage && (s.coverage.gaps || []).length) {
    const span = el('span', 'meta-stale');
    const cov = s.coverage;
    // "missing July 2026" on its own is a riddle. It reads as though the app
    // lost something. It means: no paper you hold was published that month.
    span.appendChild(document.createTextNode('no papers from '));
    span.appendChild(el('b', null, cov.gaps.length === 1
      ? prettyMonth(cov.gaps[0])
      : `${cov.gaps.length} months`));
    span.title =
      `No paper in your library was published in `
      + `${cov.gaps.map(prettyMonth).join(', ')}.\n\n`
      + `You hold papers published from ${prettyMonth(cov.window_start)} onwards, `
      + `about ${cov.typical_month} in a normal month. Nothing was deleted: those `
      + `months were never fetched, because a fetch always asks for the newest `
      + `papers and cannot reach backwards on its own.\n\n`
      + `Open Profile to see the whole run and fill a month in with one click.`;
    meta.appendChild(span);
  }
  return s;
}

async function boot() {
  const s = await refreshHeaderMeta();
  if (!s || (s.status !== 'ok' && s.status !== 'empty_library')) {
    const hero = $('#hero');
    hero.textContent = '';
    hero.appendChild(notice('Could not reach the server',
      (s && s.message) || 'Is "research-digest web" still running?', boot));
    return;
  }
  state.settingsTopics = s.topics || [];
  loadLlmStrip();
  renderConn('#conn-grid');

  const hero = $('#hero');
  hero.textContent = '';
  if (!s.papers) {
    hero.appendChild(notice('Your library is empty',
      "Run 'research-digest fetch' to pull today's papers from arXiv, or press Fetch "
      + "above. Then 'research-digest embed' if you want similarity search."));
    return;
  }
  renderHeroText();

  const sug = el('div', 'suggestion-grid');
  (s.topics || []).slice(0, 7).forEach((t) => {
    const b = el('button', 'suggestion', t);
    b.type = 'button';
    b.addEventListener('click', () => { $('#q').value = t; runSearch('search'); });
    sug.appendChild(b);
  });
  // One worked example of the thing the box can now do, because a search box
  // that accepts English is invisible until you have seen it accept some.
  const example = el('button', 'suggestion suggestion-ask',
    'Ask: what should I read about agent memory?');
  example.type = 'button';
  example.addEventListener('click', () => {
    $('#q').value = 'what should I read about agent memory?';
    runSearch('ask');
  });
  sug.appendChild(example);
  hero.appendChild(sug);

  loadGrid(false);
}

boot();
