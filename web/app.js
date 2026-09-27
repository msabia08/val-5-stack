/* 5-Stack Tracker front-end. Plain JS, no build step, no dependencies. */
(() => {
  'use strict';

  const VIEWS = ['overview', 'players', 'viz', 'odds', 'bettors', 'matches', 'setup'];
  const state = {
    view: 'overview',
    status: null, stats: null, matches: null, odds: null, content: null, insights: null,
    bets: [], bettors: [], rewards: [], slip: [], me: null,
    ctx: { map: '', agents: {} },
    bettor: localStorage.getItem('fs.bettor') || '',
    oddsFormat: localStorage.getItem('fs.oddsFormat') || 'american',
    stake: Number(localStorage.getItem('fs.stake') || 50) || 50,
    matchFilter: { map: '', result: '', mode: '' },
    expanded: new Set(),
  };

  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const isNum = (v) => v != null && !Number.isNaN(Number(v));

  const fmt = {
    n0: (v) => (isNum(v) ? Math.round(v).toLocaleString() : '–'),
    n1: (v) => (isNum(v) ? (Math.round(v * 10) / 10).toFixed(1) : '–'),
    n2: (v) => (isNum(v) ? (Math.round(v * 100) / 100).toFixed(2) : '–'),
    pct: (v) => (isNum(v) ? Math.round(v * 100) + '%' : '–'),
    pct1: (v) => (isNum(v) ? (Math.round(v * 10) / 10).toFixed(1) + '%' : '–'),
    credits: (v) => (isNum(v) ? Math.round(v).toLocaleString() : '–'),
    signed: (v, d = 1) => (isNum(v) ? (v > 0 ? '+' : '') + (d === 0 ? fmt.n0(v) : fmt.n1(v)) : '–'),
    date: (iso) => {
      if (!iso) return '–';
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return String(iso);
      return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' +
        d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    },
    ago: (ts) => {
      if (!ts) return 'never';
      const s = Math.max(0, Date.now() / 1000 - ts);
      if (s < 60) return 'just now';
      if (s < 3600) return Math.round(s / 60) + ' min ago';
      if (s < 86400) return Math.round(s / 3600) + ' h ago';
      return Math.round(s / 86400) + ' d ago';
    },
    american: (dec) => (dec >= 2 ? '+' + Math.round((dec - 1) * 100) : '-' + Math.round(100 / (dec - 1))),
    odds: (sel) => (state.oddsFormat === 'decimal' ? Number(sel.decimal).toFixed(2) : sel.american),
    res: (r) => (r === 'win' ? 'W' : r === 'loss' ? 'L' : 'D'),
  };

  const memberIndex = () => {
    const m = new Map();
    (state.status?.members || []).forEach((x, i) => m.set(x.puuid, { ...x, slot: (i % 8) + 1 }));
    return m;
  };

  // ---- data ---------------------------------------------------------------
  async function api(path, opts = {}) {
    const res = await fetch(path, { ...opts, headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) } });
    if (res.status === 401) {
      window.location.href = '/login';
      throw new Error('Login required');
    }
    let data = {};
    try { data = await res.json(); } catch (e) { /* no body */ }
    if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
    return data;
  }
  const loadStatus = async () => { state.status = await api('/api/status'); renderHeader(); };
  const loadStats = async () => { state.stats = await api('/api/stats'); };
  const loadInsights = async () => { state.insights = await api('/api/insights'); };
  const loadMatches = async () => { state.matches = (await api('/api/matches?limit=400')).matches; };
  const loadContent = async () => { state.content = await api('/api/content'); };
  const loadBets = async () => {
    const [b, l, m, r] = await Promise.all([api('/api/bets?limit=200'), api('/api/bettors'), api('/api/bettor/me'), api('/api/rewards?limit=60')]);
    state.bets = b.bets; state.bettors = l.bettors; state.me = m.bettor || null; state.rewards = r.rewards;
    if (state.me) { state.bettor = state.me.name; localStorage.setItem('fs.bettor', state.bettor); }
  };
  const isMine = (b) => !!state.me && b.bettor.toLowerCase() === state.me.name.toLowerCase();
  async function loadOdds() {
    const p = new URLSearchParams();
    if (state.ctx.map) p.set('map', state.ctx.map);
    if (Object.keys(state.ctx.agents).length) p.set('agents', JSON.stringify(state.ctx.agents));
    state.odds = await api('/api/odds?' + p.toString());
  }

  let toastTimer;
  function toast(msg, kind = 'info') {
    const t = $('#toast');
    t.textContent = msg;
    t.className = `toast ${kind}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.add('hidden'), 4500);
  }

  // ---- header -------------------------------------------------------------
  function renderHeader() {
    const s = state.status;
    if (!s) return;
    const t = s.tracker || {};
    const rec = s.record || {};
    const el = $('#team-record');
    if (rec.games) {
      el.textContent = `${rec.wins}-${rec.losses}${rec.draws ? '-' + rec.draws : ''} as a 5-stack · ${Math.round((rec.wins / rec.games) * 100)}% win rate`;
    } else {
      el.textContent = s.configured ? 'No 5-stack games tracked yet' : 'Setup needed';
    }
    const pill = $('#status-pill');
    if (s.demo) {
      pill.innerHTML = '<span class="dot ok"></span>Demo data';
      pill.title = 'Synthetic games. Configure config.json and run without --demo for live tracking.';
    } else if (!s.configured) {
      pill.innerHTML = '<span class="dot bad"></span>Not configured';
      pill.title = s.problems.join(' ');
    } else if (t.syncing) {
      pill.innerHTML = '<span class="dot busy"></span>Syncing…';
    } else if (t.last_error) {
      pill.innerHTML = '<span class="dot bad"></span>Sync error';
      pill.title = t.last_error;
    } else {
      const rl = s.ratelimit || {};
      pill.innerHTML = `<span class="dot ok"></span>Synced ${fmt.ago(t.last_sync)}`;
      pill.title = rl.remaining != null ? `${rl.remaining}/${rl.limit} API requests left in this window` : 'Live tracking active';
    }
    const banner = $('#banner');
    if (s.demo) {
      banner.className = 'banner info';
      banner.innerHTML = '<strong>Demo mode.</strong> These are made-up games. Add your API key and Riot IDs to <code>config.json</code>, then start without <code>--demo</code>.';
    } else if (!s.configured) {
      banner.className = 'banner';
      banner.innerHTML = `<strong>Setup needed.</strong> ${esc(s.problems.join(' '))} <a href="#setup">Open setup →</a>`;
    } else if (t.last_error) {
      banner.className = 'banner';
      banner.innerHTML = `<strong>Last sync failed:</strong> ${esc(t.last_error)} <a href="#setup">Details →</a>`;
    } else if (s.tunnel && s.tunnel.mode && s.tunnel.mode !== 'off' && s.tunnel.status === 'error') {
      banner.className = 'banner';
      banner.innerHTML = `<strong>Tunnel problem:</strong> ${esc(s.tunnel.error || 'cloudflared is not running')} <a href="#setup">Details →</a>`;
    } else {
      banner.className = 'banner hidden';
    }
    $('#sync-btn').disabled = !s.configured || s.demo || !!t.syncing;
    $('#logout-btn').classList.toggle('hidden', !(s.auth && s.auth.enabled));
  }

  // ---- charts -------------------------------------------------------------
  function sparkline(values, { w = 150, h = 36 } = {}) {
    const vals = values.filter(isNum).map(Number);
    if (vals.length < 2) return '';
    const min = Math.min(...vals), max = Math.max(...vals), pad = 4;
    const x = (i) => pad + (i * (w - 2 * pad)) / (vals.length - 1);
    const y = (v) => (max === min ? h / 2 : pad + (h - 2 * pad) * (1 - (v - min) / (max - min)));
    const d = vals.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    const last = vals.length - 1;
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="Trend over the last ${vals.length} games">` +
      `<path d="${d}" fill="none" stroke="var(--spark)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>` +
      `<circle cx="${x(last).toFixed(1)}" cy="${y(vals[last]).toFixed(1)}" r="3.5" fill="var(--accent)"/></svg>`;
  }

  function hbars(rows) {
    if (!rows.length) return '<p class="muted">No data yet.</p>';
    return rows.map((r) =>
      `<div class="hbar-row" title="${esc(r.title || '')}">` +
      `<div class="hbar-label">${esc(r.label)}</div>` +
      `<div class="hbar-track"><div class="hbar-fill" style="width:${Math.max(0, Math.min(100, Math.round((r.value || 0) * 100)))}%"></div></div>` +
      `<div class="hbar-val">${esc(r.text)} <span class="muted">${esc(r.n || '')}</span></div></div>`
    ).join('');
  }

  const kpi = (label, value, sub = '', extra = '') =>
    `<div class="tile"><div class="tile-label">${esc(label)}</div><div class="tile-value">${value}</div>` +
    `${sub ? `<div class="tile-sub">${sub}</div>` : ''}${extra}</div>`;

  function emptyState() {
    const s = state.status;
    if (s.demo) return '<div class="card empty"><h2>No demo data</h2><p>Restart with <code>python server.py --demo</code>.</p></div>';
    if (!s.configured) {
      return `<div class="card empty"><h2>Let's get set up</h2><p>${esc(s.problems.join(' '))}</p><p><a href="#setup">Open the setup guide →</a></p></div>`;
    }
    const t = s.tracker || {};
    return `<div class="card empty"><h2>No 5-stack games yet</h2>` +
      `<p>${t.syncing ? 'Scanning everyone\'s match history now…' : 'Games where all five of you were on the same team will show up here after the next sync.'}</p>` +
      `<p class="muted small">Tracked modes: ${esc((s.modes || []).join(', ') || 'all')}. Play a game together, then hit Sync now.</p></div>`;
  }

  // ---- overview -------------------------------------------------------------
  function viewOverview() {
    const st = state.stats, team = st.team, idx = memberIndex();
    if (!team.games) return emptyState();
    const last = team.recent[0];
    const kpis = [
      kpi('5-stack games', fmt.n0(team.games), `since ${fmt.date(team.first_played)}`),
      kpi('Win rate', fmt.pct(team.win_rate), `${team.wins}W · ${team.losses}L${team.draws ? ' · ' + team.draws + 'D' : ''}`),
      kpi('Current streak', team.streak || '–', 'wins or losses in a row'),
      kpi('Avg round diff', fmt.signed(team.avg_round_diff), 'rounds per game'),
      kpi('Last game', last ? `<span class="chip ${last.result}">${fmt.res(last.result)}</span> ${last.rounds_won}–${last.rounds_lost}` : '–',
        last ? `${esc(last.map)} · ${fmt.date(last.started_at)}` : ''),
    ];
    const mapChart = hbars(team.by_map.map((m) => ({
      label: m.map, value: m.win_rate, text: fmt.pct(m.win_rate), n: `${m.wins}-${m.losses}`,
      title: `${m.map}: ${m.wins}-${m.losses} (${fmt.pct(m.win_rate)}), avg round diff ${fmt.signed(m.avg_round_diff)}`,
    })));
    const recent = team.recent.map((r) =>
      `<li class="recent-row"><span class="chip ${r.result}">${fmt.res(r.result)}</span>` +
      `<span class="recent-score">${r.rounds_won}–${r.rounds_lost}</span><span>${esc(r.map)}</span>` +
      `<span class="muted small mode">${esc(r.mode_label || '')}</span><span class="muted small">${fmt.date(r.started_at)}</span></li>`
    ).join('');
    const rows = st.members.slice().sort((a, b) => (b.overall.acs || 0) - (a.overall.acs || 0)).map((m) => {
      const o = m.overall, slot = idx.get(m.puuid)?.slot || 1;
      if (!o.games) return `<tr><td><span class="swatch s${slot}"></span>${esc(m.nickname)}</td><td class="muted" colspan="8">no games yet</td></tr>`;
      return `<tr><td><span class="swatch s${slot}"></span>${esc(m.nickname)} <span class="muted small">${esc(m.name)}#${esc(m.tag)}</span></td>` +
        `<td class="num">${o.games}</td><td class="num">${fmt.pct(o.win_rate)}</td><td class="num">${fmt.n0(o.acs)}</td>` +
        `<td class="num">${fmt.n2(o.kd)}</td><td class="num">${fmt.n1(o.avg_kills)} / ${fmt.n1(o.avg_deaths)} / ${fmt.n1(o.avg_assists)}</td>` +
        `<td class="num">${fmt.n0(o.adr)}</td><td class="num">${fmt.pct1(o.hs_pct)}</td>` +
        `<td>${sparkline(m.form.slice().reverse().map((f) => f.acs), { w: 120, h: 30 })}</td></tr>`;
    }).join('');
    return `<section class="kpis">${kpis.join('')}</section>
      <section class="grid-2">
        <div class="card"><h2>Map performance</h2><p class="muted small">Win rate as a 5-stack, ${team.games} games</p>${mapChart}</div>
        <div class="card"><h2>Recent games</h2><ul class="recent">${recent}</ul></div>
      </section>
      <section class="card"><h2>Squad in 5-stack games</h2>
        <div class="table-wrap"><table><thead><tr><th>Player</th><th class="num">Games</th><th class="num">Win %</th><th class="num">ACS</th><th class="num">K/D</th><th class="num">K / D / A per game</th><th class="num">ADR</th><th class="num">HS %</th><th>ACS, last 15</th></tr></thead><tbody>${rows}</tbody></table></div>
      </section>`;
  }

  // ---- 5-stack vs. usual ----------------------------------------------------------
  const DEV_LABEL = {
    better: '▲ Better', worse: '▼ Worse', leaning_better: '△ Slightly better', leaning_worse: '▽ Slightly worse',
    same: 'No real change', too_few: 'Too few games',
  };
  const devBadge = (v) => `<span class="dev ${v}">${DEV_LABEL[v] || ''}</span>`;
  const devValue = (key, v) => ({ acs: fmt.n0, adr: fmt.n0, hs_pct: fmt.pct1, win_rate: fmt.pct }[key] || fmt.n2)(v);
  // Round before signing so a -0.004 gap reads as "0.00", not "-0.00".
  const tidy = (v, dp) => Number(v.toFixed(dp)) || 0;
  function devDiff(mt) {
    if (mt.key === 'win_rate') return `${fmt.signed(tidy(mt.diff * 100, 0), 0)} pts`;
    if (mt.key === 'hs_pct') return `${fmt.signed(tidy(mt.diff, 1))} pts`;
    const d = ['acs', 'adr'].includes(mt.key) ? fmt.signed(tidy(mt.diff, 0), 0) : (tidy(mt.diff, 2) > 0 ? '+' : '') + fmt.n2(tidy(mt.diff, 2));
    return d + (isNum(mt.pct) ? ` <span class="muted">(${fmt.signed(tidy(mt.pct * 100, 0), 0)}%)</span>` : '');
  }
  const devMetric = (dv, key) => dv?.metrics.find((x) => x.key === key);

  function devSummary(st, idx) {
    const withBase = st.members.filter((m) => m.deviation && m.overall.games);
    if (!withBase.length) {
      return `<section class="card"><h2>5-stack vs. their other games</h2>
        <p class="muted">No non-5-stack games stored yet. Each member's other games in the tracked modes are collected on the next sync.</p></section>`;
    }
    const acsPct = (m) => devMetric(m.deviation, 'acs')?.pct ?? -Infinity;
    const rows = withBase.slice().sort((a, b) => acsPct(b) - acsPct(a)).map((m) => {
      const dv = m.deviation, slot = idx.get(m.puuid)?.slot || 1;
      const acs = devMetric(dv, 'acs'), kd = devMetric(dv, 'kd'), wr = devMetric(dv, 'win_rate');
      const so = dv.standout && devMetric(dv, dv.standout);
      return `<tr><td><span class="swatch s${slot}"></span>${esc(m.nickname)}</td>` +
        `<td class="num">${dv.stack_games} <span class="muted">/ ${dv.usual_games}</span></td>` +
        `<td class="num">${acs ? `${fmt.n0(acs.stack)} <span class="muted">vs ${fmt.n0(acs.usual)}</span>` : '–'}</td>` +
        `<td class="num">${acs ? devDiff(acs) : '–'}</td>` +
        `<td class="num">${kd ? devDiff(kd) : '–'}</td>` +
        `<td class="num">${wr ? devDiff(wr) : '–'}</td>` +
        `<td>${acs ? devBadge(acs.verdict) : ''}</td>` +
        `<td>${so ? `${esc(so.label)} ${devBadge(so.verdict)}` : '<span class="muted">–</span>'}</td></tr>`;
    }).join('');
    return `<section class="card"><h2>5-stack vs. their other games</h2>
      <p class="muted small">Each player's 5-stack games compared with their games outside the stack (solo queue or smaller parties) in the tracked modes. "Better" or "worse" means the gap is about two standard errors or more; "slightly" means one to two; smaller gaps are within normal game-to-game variation.</p>
      <div class="table-wrap"><table><thead><tr><th>Player</th><th class="num">Games stack / other</th><th class="num">ACS</th><th class="num">ACS change</th><th class="num">K/D change</th><th class="num">Win % change</th><th>ACS verdict</th><th>Biggest change</th></tr></thead><tbody>${rows}</tbody></table></div>
    </section>`;
  }

  function devBlock(m) {
    const dv = m.deviation;
    if (!dv) return '<h3>Compared with their other games</h3><p class="muted small">No non-5-stack games stored for this player yet.</p>';
    const rows = dv.metrics.map((mt) =>
      `<tr><td>${esc(mt.label)} <span class="muted small">${mt.better === 'lower' ? '(lower is better)' : ''}</span></td>` +
      `<td class="num">${devValue(mt.key, mt.stack)}</td><td class="num">${devValue(mt.key, mt.usual)}</td>` +
      `<td class="num">${devDiff(mt)}</td><td>${devBadge(mt.verdict)}</td></tr>`).join('');
    const note = dv.enough ? '' : ` Differences are judged once both sides have ${dv.min_games} games.`;
    return `<h3>Compared with their other games</h3>
      <p class="muted small">${dv.stack_games} 5-stack games vs. ${dv.usual_games} other games (${esc(dv.usual_modes.join(', ') || 'tracked modes')}).${note}</p>
      <div class="table-wrap"><table class="compact"><thead><tr><th>Stat</th><th class="num">5-stack</th><th class="num">Other games</th><th class="num">Difference</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  // ---- players ----------------------------------------------------------------
  function viewPlayers() {
    const st = state.stats, idx = memberIndex();
    if (!st.members.length) return emptyState();
    const breakdown = (rows, key) => {
      if (!rows.length) return '<p class="muted">No games yet.</p>';
      return `<div class="table-wrap"><table class="compact"><thead><tr><th>${key === 'agent' ? 'Agent' : 'Map'}</th><th class="num">Games</th><th class="num">Win %</th><th class="num">ACS</th><th class="num">K/D</th><th class="num">K / D / A</th><th class="num">ADR</th><th class="num">HS %</th></tr></thead><tbody>` +
        rows.map((r) => `<tr><td>${esc(r[key])}</td><td class="num">${r.games}</td><td class="num">${fmt.pct(r.win_rate)}</td><td class="num">${fmt.n0(r.acs)}</td><td class="num">${fmt.n2(r.kd)}</td><td class="num">${fmt.n1(r.avg_kills)} / ${fmt.n1(r.avg_deaths)} / ${fmt.n1(r.avg_assists)}</td><td class="num">${fmt.n0(r.adr)}</td><td class="num">${fmt.pct1(r.hs_pct)}</td></tr>`).join('') +
        '</tbody></table></div>';
    };
    return devSummary(st, idx) + st.members.map((m) => {
      const o = m.overall, slot = idx.get(m.puuid)?.slot || 1;
      const form = m.form.slice(0, 10).map((f) =>
        `<span class="chip ${f.result}" title="${esc(f.map)} · ${esc(f.agent)} · ${f.kills}/${f.deaths}/${f.assists} · ACS ${f.acs}">${fmt.res(f.result)}</span>`).join('');
      const bk = m.best.kills, ba = m.best.acs;
      const body = o.games
        ? `<div class="kpis small">
            ${kpi('Games', o.games, `${o.wins}W · ${o.losses}L`)}
            ${kpi('Win rate', fmt.pct(o.win_rate))}
            ${kpi('ACS', fmt.n0(o.acs), 'per round', sparkline(m.form.slice().reverse().map((f) => f.acs), { w: 110, h: 28 }))}
            ${kpi('K/D', fmt.n2(o.kd), `${fmt.n1(o.avg_kills)} kills · ${fmt.n1(o.avg_deaths)} deaths`)}
            ${kpi('ADR', fmt.n0(o.adr), 'damage per round')}
            ${kpi('Headshot %', fmt.pct1(o.hs_pct))}
          </div>
          <div class="grid-2"><div><h3>By agent</h3>${breakdown(m.by_agent, 'agent')}</div><div><h3>By map</h3>${breakdown(m.by_map, 'map')}</div></div>
          ${devBlock(m)}
          <p class="muted small">Best game: ${bk ? `${bk.value} kills on ${esc(bk.map)} as ${esc(bk.agent)} (${fmt.date(bk.started_at)})` : '–'}${ba ? ` · Peak ACS ${fmt.n0(ba.value)} on ${esc(ba.map)}` : ''}</p>`
        : '<p class="muted">No 5-stack games recorded for this player yet.</p>';
      return `<section class="card player">
        <header class="player-head"><span class="swatch s${slot} lg"></span><div><h2>${esc(m.nickname)}</h2><div class="muted small">${esc(m.name)}#${esc(m.tag)}${m.tier_name ? ' · ' + esc(m.tier_name) : ''}</div></div><div class="form">${form}</div></header>
        ${body}</section>`;
    }).join('');
  }

  // ---- visualizations (drawn by web/viz.js) ---------------------------------------
  function viewViz() {
    const idx = memberIndex();
    const out = window.FiveViz.html(state.insights, { esc, fmt, slot: (puuid) => idx.get(puuid)?.slot });
    return out == null ? emptyState() : out;
  }

  // ---- odds & bets --------------------------------------------------------------
  function oddBtn(mk, s, label) {
    const on = state.slip.some((x) => x.market_id === mk.market_id && x.selection === s.key);
    return `<button class="odd ${on ? 'on' : ''}" data-m="${esc(mk.market_id)}" data-s="${esc(s.key)}" aria-pressed="${on}" title="${Math.round(s.fair_prob * 100)}% fair probability">` +
      `${label ? `<span>${esc(label)}</span>` : ''}<b>${fmt.odds(s)}</b></button>`;
  }

  function viewOdds() {
    const od = state.odds, c = state.content, idx = memberIndex();
    const members = state.status.members || [];
    const ctxBar = `<section class="card"><div class="ctx-row">
        <label>Expected map<select id="ctx-map"><option value="">Any map</option>${c.maps.map((m) => `<option ${state.ctx.map === m ? 'selected' : ''}>${esc(m)}</option>`).join('')}</select></label>
        ${members.map((m) => {
          const played = c.played[m.puuid] || [];
          const opts = [...played, ...c.agents.filter((a) => !played.includes(a))];
          return `<label>${esc(m.nickname)}<select class="ctx-agent" data-puuid="${esc(m.puuid)}"><option value="">Any agent</option>${opts.map((a) => `<option ${state.ctx.agents[m.puuid] === a ? 'selected' : ''}>${esc(a)}</option>`).join('')}</select></label>`;
        }).join('')}
        <button class="btn ghost" id="ctx-clear">Clear</button>
        <label class="right">Odds format<select id="odds-format"><option value="american" ${state.oddsFormat === 'american' ? 'selected' : ''}>American</option><option value="decimal" ${state.oddsFormat === 'decimal' ? 'selected' : ''}>Decimal</option></select></label>
      </div>
      <p class="muted small">Lines come from your 5-stack history, weighted toward recent games. Choosing a map or agents up-weights matching games. House edge ${Math.round((od.house_edge || 0) * 100)}%.</p></section>`;
    if (!od.ready) {
      return ctxBar + `<div class="card empty"><h2>No odds yet</h2><p>${esc(od.message)}</p></div>` + betsSection();
    }
    const team = od.team.map((mk) =>
      `<div class="market"><h3>${esc(mk.label)}</h3><p class="muted small">${esc(mk.desc)}${mk.basis ? ` · ${mk.basis.games} games, ${fmt.pct(mk.basis.win_rate)} raw win rate${mk.basis.map_games ? `, ${mk.basis.map_games} on this map` : ''}` : ''}${mk.mean != null ? ` · avg ${mk.mean}` : ''}</p>` +
      `<div class="sels">${mk.selections.map((s) => oddBtn(mk, s, s.label)).join('')}</div></div>`).join('');
    const props = new Map(od.player_props.map((p) => [p.market_id, p]));
    const propRows = od.members.map((m) => {
      const slot = idx.get(m.puuid)?.slot || 1;
      return `<tr><th scope="row"><span class="swatch s${slot}"></span>${esc(m.nickname)}<div class="muted small">${m.games} games${m.context_agent ? ' · ' + esc(m.context_agent) : ''} · <span class="conf ${m.confidence}">${m.confidence} confidence</span>${m.borrowed ? ' · thin history, team average blended in' : ''}</div></th>` +
        od.stat_defs.map((sd) => {
          const mk = props.get(`ou:${sd.key}:${m.puuid}`);
          if (!mk) return '<td class="prop muted">–</td>';
          const [o, u] = mk.selections;
          return `<td class="prop" title="Average ${mk.mean}"><div class="line">${mk.line}</div>${oddBtn(mk, o, 'O')}${oddBtn(mk, u, 'U')}</td>`;
        }).join('') + '</tr>';
    }).join('');
    const tops = od.top_markets.map((mk) =>
      `<div class="market"><h3>${esc(mk.label)}</h3><p class="muted small">${esc(mk.desc)}</p>` +
      mk.selections.map((s) => {
        const slot = idx.get(s.key)?.slot || 1;
        return `<div class="top-row"><span class="swatch s${slot}"></span><span>${esc(s.label)}</span>` +
          `<span class="top-bar"><span class="top-fill s${slot}" style="width:${Math.round(s.fair_prob * 100)}%"></span></span>` +
          `<span class="muted small num">${Math.round(s.fair_prob * 100)}%</span>${oddBtn(mk, s, '')}</div>`;
      }).join('') + '</div>').join('');
    return ctxBar + `<div class="odds-layout"><div>
        <section class="card"><h2>Team markets</h2><div class="markets">${team}</div></section>
        <section class="card"><h2>Player props · over / under</h2><p class="muted small">For the next 5-stack game. Tap O or U to add a pick to the slip.</p>
          <div class="table-wrap"><table class="props"><thead><tr><th>Player</th>${od.stat_defs.map((s) => `<th>${esc(s.label)}</th>`).join('')}</tr></thead><tbody>${propRows}</tbody></table></div></section>
        <section class="card"><h2>Who tops the scoreboard?</h2><div class="markets">${tops}</div></section>
        ${betsSection()}
      </div><aside class="slip card" id="slip">${slipHtml()}</aside></div>`;
  }

  function slipHtml() {
    const me = state.me;
    const account = me
      ? `<div class="account"><div>Betting as <b>${esc(me.name)}</b></div><div class="muted small">Balance ${fmt.credits(me.balance)} credits</div>` +
        `<div class="btn-row"><button class="btn ghost small" id="bettor-signout">Sign out</button><button class="btn ghost small" id="bettor-password">Change password</button></div></div>`
      : `<div class="account"><label>Name<input id="bettor-name" placeholder="Your name" value="${esc(state.bettor)}" autocomplete="username" maxlength="32"></label>` +
        `<label>Betting password<input id="bettor-pass" type="password" placeholder="Yours alone, not the site password" autocomplete="current-password"></label>` +
        `<div class="btn-row"><button class="btn small" id="bettor-signin">Sign in</button><button class="btn ghost small" id="bettor-register">Create account</button></div>` +
        `<p class="muted small">Each bettor has a personal password, so nobody can bet or cancel under your name. New accounts start with ${fmt.credits(state.status.starting_balance)} credits.</p></div>`;
    const items = state.slip.map((x, i) =>
      `<div class="slip-item"><div><div class="slip-desc">${esc(x.desc)}</div><div class="muted small">${esc(x.selLabel)} @ ${esc(x.american)} (${Number(x.decimal).toFixed(2)})</div></div>` +
      `<input type="number" min="1" step="1" value="${x.stake}" data-i="${i}" class="stake" aria-label="Stake">` +
      `<button class="btn ghost icon rm" data-i="${i}" aria-label="Remove">✕</button>` +
      `<div class="muted small towin">To win ${fmt.credits(x.stake * (x.decimal - 1))}</div></div>`).join('');
    const total = state.slip.reduce((a, x) => a + (Number(x.stake) || 0), 0);
    return `<h2>Bet slip</h2>
      ${account}
      ${items || '<p class="muted">Tap any odds to add a pick.</p>'}
      ${state.slip.length ? `<div class="slip-total">Total stake ${fmt.credits(total)}</div><button class="btn primary" id="place-bets" ${me ? '' : 'disabled title="Sign in first"'}>Place ${state.slip.length} bet${state.slip.length > 1 ? 's' : ''}</button><button class="btn ghost" id="clear-slip" style="width:100%;margin-top:6px">Clear slip</button>` : ''}`;
  }

  function betsSection() {
    const pending = state.bets.filter((b) => b.status === 'pending');
    const settled = state.bets.filter((b) => b.status !== 'pending').slice(0, 40);
    const head = '<thead><tr><th>Bettor</th><th>Pick</th><th class="num">Odds</th><th class="num">Stake</th><th>Status</th><th class="num">Return</th></tr></thead>';
    const row = (b) =>
      `<tr><td>${esc(b.bettor)}</td><td>${esc(b.description)}</td><td class="num">${fmt.american(b.odds_decimal)}</td><td class="num">${fmt.credits(b.stake)}</td>` +
      `<td><span class="status ${b.status}">${b.status}</span>${b.note ? `<div class="muted small">${esc(b.note)}</div>` : ''}${b.actual_value != null && b.status !== 'pending' ? `<div class="muted small">actual ${fmt.n1(b.actual_value)}</div>` : ''}</td>` +
      `<td class="num">${b.status !== 'pending' ? fmt.credits(b.payout || 0)
        : isMine(b) ? `<button class="btn ghost small cancel-bet" data-id="${b.id}">Cancel</button>`
        : (state.status.auth && state.status.auth.admin_required) ? `<button class="btn ghost small cancel-bet admin" data-id="${b.id}" title="Needs the admin password">Admin cancel</button>`
        : ''}</td></tr>`;
    return `<section class="card"><h2>Open bets <span class="muted">(${pending.length})</span></h2>
        ${pending.length ? `<div class="table-wrap"><table class="compact">${head}<tbody>${pending.map(row).join('')}</tbody></table></div>` : '<p class="muted">No open bets. Bets settle automatically when the next 5-stack game is synced.</p>'}
        <p class="muted small">Balances and rankings live on the <a href="#bettors">Bettors</a> tab.</p></section>
      <section class="card"><h2>Settled bets</h2>
        ${settled.length ? `<div class="table-wrap"><table class="compact">${head}<tbody>${settled.map(row).join('')}</tbody></table></div>` : '<p class="muted">Nothing settled yet.</p>'}</section>`;
  }

  function findMarket(id, key) {
    const od = state.odds || {};
    for (const g of ['team', 'player_props', 'top_markets']) {
      for (const mk of od[g] || []) {
        if (mk.market_id === id) return { mk, sel: mk.selections.find((s) => s.key === key) };
      }
    }
    return {};
  }

  function toggleSlip(marketId, selKey) {
    const i = state.slip.findIndex((x) => x.market_id === marketId && x.selection === selKey);
    if (i >= 0) {
      state.slip.splice(i, 1);
    } else {
      const { mk, sel } = findMarket(marketId, selKey);
      if (!mk || !sel) return;
      state.slip = state.slip.filter((x) => x.market_id !== marketId); // one side per market
      state.slip.push({
        market_id: marketId, selection: selKey, selLabel: sel.label,
        desc: mk.type === 'ou' ? `${mk.member} ${mk.stat_label}` : mk.label,
        american: sel.american, decimal: sel.decimal, line: mk.line, stake: state.stake,
      });
    }
    drawSlip();
    syncOddButtons();
  }

  function syncOddButtons() {
    $$('button.odd').forEach((b) => {
      const on = state.slip.some((x) => x.market_id === b.dataset.m && x.selection === b.dataset.s);
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', String(on));
    });
  }

  function drawSlip() {
    const slip = $('#slip');
    if (!slip) return;
    slip.innerHTML = slipHtml();
    bindSlip();
  }

  async function bettorSession(path) {
    const name = ($('#bettor-name')?.value || '').trim();
    const password = $('#bettor-pass')?.value || '';
    if (!name) { toast('Enter your name', 'bad'); return; }
    if (!password) { toast('Enter your betting password', 'bad'); return; }
    try {
      const r = await api(path, { method: 'POST', body: JSON.stringify({ name, password }) });
      state.bettor = r.bettor.name;
      localStorage.setItem('fs.bettor', state.bettor);
      toast(path.endsWith('register') ? `Account created. Welcome, ${r.bettor.name}.` : `Signed in as ${r.bettor.name}`, 'good');
      await loadBets();
      draw();
    } catch (e) {
      toast(e.message, 'bad');
    }
  }

  function bindSlip() {
    const slip = $('#slip');
    if (!slip) return;
    $('#bettor-signin')?.addEventListener('click', () => bettorSession('/api/bettor/login'));
    $('#bettor-register')?.addEventListener('click', () => bettorSession('/api/bettor/register'));
    $('#bettor-pass')?.addEventListener('keydown', (e) => { if (e.key === 'Enter') bettorSession('/api/bettor/login'); });
    $('#bettor-signout')?.addEventListener('click', async () => {
      try { await api('/api/bettor/logout', { method: 'POST', body: '{}' }); } catch (e) { /* cookie is cleared anyway */ }
      await loadBets();
      draw();
    });
    $('#bettor-password')?.addEventListener('click', async () => {
      const old = window.prompt('Current betting password');
      if (old == null) return;
      const nw = window.prompt('New betting password (4 to 64 characters)');
      if (nw == null) return;
      try { await api('/api/bettor/password', { method: 'POST', body: JSON.stringify({ old, new: nw }) }); toast('Password changed', 'good'); }
      catch (e) { toast(e.message, 'bad'); }
    });
    $$('.stake', slip).forEach((inp) => inp.addEventListener('input', (e) => {
      const it = state.slip[Number(e.target.dataset.i)];
      if (!it) return;
      it.stake = Math.max(0, Number(e.target.value) || 0);
      state.stake = it.stake || state.stake;
      localStorage.setItem('fs.stake', String(state.stake));
      const tw = e.target.parentElement.querySelector('.towin');
      if (tw) tw.textContent = `To win ${fmt.credits(it.stake * (it.decimal - 1))}`;
      const tot = $('.slip-total', slip);
      if (tot) tot.textContent = `Total stake ${fmt.credits(state.slip.reduce((a, x) => a + (Number(x.stake) || 0), 0))}`;
    }));
    $$('.rm', slip).forEach((b) => b.addEventListener('click', () => { state.slip.splice(Number(b.dataset.i), 1); drawSlip(); syncOddButtons(); }));
    $('#clear-slip')?.addEventListener('click', () => { state.slip = []; drawSlip(); syncOddButtons(); });
    $('#place-bets')?.addEventListener('click', placeSlip);
  }

  async function placeSlip() {
    if (!state.me) { toast('Sign in as a bettor first', 'bad'); return; }
    const failures = [];
    const remaining = [];
    for (const it of state.slip) {
      try {
        await api('/api/bets', { method: 'POST', body: JSON.stringify({ market_id: it.market_id, selection: it.selection, stake: it.stake, context: state.ctx }) });
      } catch (e) {
        failures.push(`${it.desc}: ${e.message}`);
        remaining.push(it);
      }
    }
    state.slip = remaining;
    if (failures.length) toast(failures.join(' · '), 'bad');
    else toast('Bets placed. They settle after the next 5-stack game.', 'good');
    await loadBets();
    draw();
  }

  async function refreshOdds() {
    try { await loadOdds(); draw(); } catch (e) { toast(e.message, 'bad'); }
  }

  // ---- bettors ----------------------------------------------------------------------
  function viewBettors() {
    const bettors = state.bettors || [];
    const start = state.status.starting_balance || 1000;
    if (!bettors.length) {
      return `<div class="card empty"><h2>No bettors yet</h2><p>Go to <a href="#odds">Odds &amp; Bets</a>, type your name in the bet slip and place a pick. Everyone starts with ${fmt.credits(start)} credits.</p></div>`;
    }
    const maxBal = Math.max(1, ...bettors.map((b) => b.balance));
    const bestWin = {};
    state.bets.filter((b) => b.status === 'won').forEach((b) => {
      const net = (b.payout || 0) - b.stake;
      const key = b.bettor.toLowerCase();
      if (!bestWin[key] || net > bestWin[key].net) bestWin[key] = { net, desc: b.description };
    });
    const inPlay = bettors.reduce((a, b) => a + b.balance + (b.pending_stake || 0), 0);
    const rewarded = bettors.reduce((a, b) => a + (b.rewards || 0), 0);
    const issued = bettors.length * start + rewarded;
    const house = issued - inPlay;
    const leader = bettors[0];
    const kpis = [
      kpi('Bettors', bettors.length, `${fmt.credits(start)} credits each to start`),
      kpi('Leader', esc(leader.name), `${fmt.credits(leader.balance)} credits`),
      kpi('Credits in circulation', fmt.credits(inPlay), `${fmt.credits(issued)} issued${rewarded ? `, ${fmt.credits(rewarded)} of it as game rewards` : ''}`),
      kpi('The house is', `<span class="${house > 0 ? 'up' : house < 0 ? 'down' : ''}">${fmt.signed(house, 0)}</span>`, house >= 0 ? 'ahead of the squad' : 'behind the squad'),
    ];
    const me = (state.me ? state.me.name : '').toLowerCase();
    const rows = bettors.map((b, i) => {
      const settled = b.won + b.lost;
      const bw = bestWin[b.name.toLowerCase()];
      const rank = i < 3 ? ['🥇', '🥈', '🥉'][i] : String(i + 1);
      return `<tr class="${b.name.toLowerCase() === me ? 'me' : ''}"><td class="rank">${rank}</td>` +
        `<td><b>${esc(b.name)}</b>${b.claimed === false ? ' <span class="muted small">unclaimed</span>' : ''}${bw ? `<div class="muted small">Best win +${fmt.credits(bw.net)} · ${esc(bw.desc)}</div>` : ''}</td>` +
        `<td class="num balance">${fmt.credits(b.balance)}</td>` +
        `<td class="bar-cell"><div class="hbar-track"><div class="hbar-fill" style="width:${Math.round((b.balance / maxBal) * 100)}%"></div></div></td>` +
        `<td class="num ${b.profit > 0 ? 'up' : b.profit < 0 ? 'down' : ''}">${fmt.signed(b.profit, 0)}</td>` +
        `<td class="num">${b.rewards ? '+' + fmt.credits(b.rewards) : '–'}</td>` +
        `<td class="num">${b.won}-${b.lost}${b.void ? '-' + b.void : ''}</td>` +
        `<td class="num">${settled ? fmt.pct(b.won / settled) : '–'}</td>` +
        `<td class="num">${b.roi != null ? fmt.signed(b.roi * 100, 0) + '%' : '–'}</td>` +
        `<td class="num">${b.pending}${b.pending_stake ? ` <span class="muted small">(${fmt.credits(b.pending_stake)})</span>` : ''}</td></tr>`;
    }).join('');
    const recent = state.bets.filter((b) => ['won', 'lost', 'void'].includes(b.status)).slice(0, 12).map((b) => {
      const net = (b.payout || 0) - b.stake;
      return `<li class="recent-row bet"><span class="status ${b.status}">${b.status}</span>` +
        `<span class="num ${net > 0 ? 'up' : net < 0 ? 'down' : ''}">${fmt.signed(net, 0)}</span>` +
        `<span><b>${esc(b.bettor)}</b> · ${esc(b.description)}</span>` +
        `<span class="muted small">${fmt.date(b.settled_ts ? b.settled_ts * 1000 : null)}</span></li>`;
    }).join('');
    return `<section class="kpis">${kpis.join('')}</section>
      <section class="card"><h2>Rankings</h2><p class="muted small">Ordered by balance. Profit is betting only: it counts open stakes, is measured against the ${fmt.credits(start)} everyone started with, and leaves out game rewards (shown separately).</p>
        <div class="table-wrap"><table class="rankings"><thead><tr><th class="rank">#</th><th>Bettor</th><th class="num">Credits</th><th></th><th class="num">Profit</th><th class="num">Rewards</th><th class="num">W-L-void</th><th class="num">Win %</th><th class="num">ROI</th><th class="num">Open</th></tr></thead><tbody>${rows}</tbody></table></div>
        <div class="btn-row"><button class="btn ghost small" id="reset-bets">Reset season</button></div></section>
      <section class="card"><h2>Recent results</h2>${recent ? `<ul class="recent">${recent}</ul>` : '<p class="muted">No settled bets yet. Bets settle when the next 5-stack game is recorded.</p>'}</section>
      ${rewardsCard()}`;
  }

  function rewardsCard() {
    const s = state.status, game = s.game_reward || 0, win = s.win_reward || 0, bonus = s.performance_bonus_max || 0;
    if (!game && !win && !bonus) return '';
    const rule = `Every squad member earns ${fmt.credits(game)} credits for each 5-stack game${win ? ` (${fmt.credits(game + win)} for a win)` : ''}, ` +
      `plus a performance bonus of up to ${fmt.credits(bonus)} based on how their ACS compares with their own previous 5-stack games: ` +
      'beat 80% of them and the bonus is 80%, rounded to the nearest 5. With fewer than 5 of those games to compare against, the bonus is half.';
    const byGame = new Map();
    (state.rewards || []).forEach((r) => {
      if (!byGame.has(r.match_id)) byGame.set(r.match_id, []);
      byGame.get(r.match_id).push(r);
    });
    const games = [...byGame.values()].slice(0, 8).map((rs) => {
      const g = rs[0], won = g.result === 'win';
      const people = rs.map((r) => {
        const why = r.beat_share == null ? 'fewer than 5 earlier 5-stack games' : `beat ${fmt.pct(r.beat_share)} of their earlier 5-stack games`;
        return `<span class="reward" title="${esc(`${r.nickname || r.bettor}: ACS ${fmt.n0(r.acs)}, ${why}`)}"><b>${esc(r.bettor)}</b> +${fmt.credits(r.base + r.bonus)}</span>`;
      }).join('');
      return `<li class="recent-row reward-row"><span class="chip ${won ? 'win' : 'loss'}">${won ? 'W' : 'L'}</span>` +
        `<span class="recent-score">${g.rounds_won ?? '?'}–${g.rounds_lost ?? '?'}</span><span>${esc(g.map || '')}</span>` +
        `<span class="rewards-list">${people}</span><span class="muted small">${fmt.date(g.started_ts ? g.started_ts * 1000 : null)}</span></li>`;
    }).join('');
    return `<section class="card"><h2>Game rewards</h2><p class="muted small">${rule} Hover a name for the details.</p>` +
      (games ? `<ul class="recent">${games}</ul>` : '<p class="muted">No rewards yet. They are paid when the next 5-stack game is recorded.</p>') + '</section>';
  }

  // ---- matches --------------------------------------------------------------------
  function matchDetail(m, idx) {
    const rounds = (m.rounds_won || 0) + (m.rounds_lost || 0) || 1;
    const rows = m.players.map((p) => {
      const shots = (p.headshots || 0) + (p.bodyshots || 0) + (p.legshots || 0);
      const slot = idx.get(p.puuid)?.slot || 1;
      return `<tr><td><span class="swatch s${slot}"></span>${esc(p.nickname || p.name || String(p.puuid).slice(0, 8))}</td><td>${esc(p.agent || '')}</td>` +
        `<td class="num">${fmt.n0((p.score || 0) / rounds)}</td><td class="num">${p.kills ?? '–'}</td><td class="num">${p.deaths ?? '–'}</td><td class="num">${p.assists ?? '–'}</td>` +
        `<td class="num">${fmt.n0((p.damage_dealt || 0) / rounds)}</td><td class="num">${shots ? fmt.pct1(((p.headshots || 0) / shots) * 100) : '–'}</td><td class="muted small">${esc(p.tier_name || '')}</td></tr>`;
    }).join('');
    return `<table class="compact inner"><thead><tr><th>Player</th><th>Agent</th><th class="num">ACS</th><th class="num">K</th><th class="num">D</th><th class="num">A</th><th class="num">ADR</th><th class="num">HS %</th><th>Rank</th></tr></thead><tbody>${rows}</tbody></table>` +
      `<div class="muted small">${m.game_length_ms ? `Game length ${Math.round(m.game_length_ms / 60000)} min · ` : ''}${m.party_verified === 1 ? 'All five in one party · ' : ''}${esc(m.season || '')}</div>`;
  }

  function viewMatches() {
    const ms = state.matches, idx = memberIndex();
    if (!ms.length) return emptyState();
    const f = state.matchFilter;
    const maps = [...new Set(ms.map((m) => m.map).filter(Boolean))].sort();
    const modes = [...new Set(ms.map((m) => m.mode_label).filter(Boolean))].sort();
    const list = ms.filter((m) => (!f.map || m.map === f.map) && (!f.result || m.result === f.result) && (!f.mode || m.mode_label === f.mode));
    const rows = list.map((m) => {
      const open = state.expanded.has(m.match_id);
      const rounds = (m.rounds_won || 0) + (m.rounds_lost || 0) || 1;
      const top = m.players[0];
      return `<tr class="match-row ${m.result}" data-id="${esc(m.match_id)}"><td><span class="chip ${m.result}">${fmt.res(m.result)}</span></td>` +
        `<td class="num"><b>${m.rounds_won}–${m.rounds_lost}</b></td><td>${esc(m.map)}</td><td class="muted">${esc(m.mode_label || '')}</td>` +
        `<td>${top ? `${esc(top.nickname || top.name)} · ${fmt.n0((top.score || 0) / rounds)} ACS · ${esc(top.agent || '')}` : ''}</td>` +
        `<td class="muted small">${fmt.date(m.started_at)}</td><td class="muted small">${open ? '▾' : '▸'}</td></tr>` +
        (open ? `<tr class="detail"><td colspan="7">${matchDetail(m, idx)}</td></tr>` : '');
    }).join('');
    return `<section class="card"><div class="filters">
        <select id="f-map"><option value="">All maps</option>${maps.map((x) => `<option ${f.map === x ? 'selected' : ''}>${esc(x)}</option>`).join('')}</select>
        <select id="f-result"><option value="">All results</option><option value="win" ${f.result === 'win' ? 'selected' : ''}>Wins</option><option value="loss" ${f.result === 'loss' ? 'selected' : ''}>Losses</option></select>
        <select id="f-mode"><option value="">All modes</option>${modes.map((x) => `<option ${f.mode === x ? 'selected' : ''}>${esc(x)}</option>`).join('')}</select>
        <span class="muted small">${list.length} of ${ms.length} games · click a row for the scoreboard</span></div>
      <div class="table-wrap"><table><thead><tr><th></th><th class="num">Score</th><th>Map</th><th>Mode</th><th>Top performer</th><th>Date</th><th></th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
  }

  // ---- setup -----------------------------------------------------------------------
  function viewSetup() {
    const s = state.status, t = s.tracker || {}, rl = s.ratelimit || {};
    const members = (s.members || []).map((m) => `<li>${esc(m.nickname)} <span class="muted">${esc(m.name)}#${esc(m.tag)}</span> <span class="muted small">${esc(String(m.puuid).slice(0, 8))}…</span></li>`).join('');
    const log = (s.log || []).slice().reverse().map((l) => `<div class="log-line"><span class="muted">${new Date(l.ts * 1000).toLocaleTimeString()}</span> ${esc(l.msg)}</div>`).join('');
    const r = t.last_result;
    const tn = s.tunnel || {}, au = s.auth || {};
    const tunnelStatus = !tn.mode || tn.mode === 'off'
      ? '<span class="muted">off</span> <span class="muted small">start with <code>run-online.bat</code> or <code>python server.py --tunnel</code></span>'
      : `<span class="status ${tn.status === 'up' ? 'won' : tn.status === 'error' ? 'lost' : 'pending'}">${esc(tn.status)}</span> <span class="muted small">${esc(tn.mode)} tunnel</span>`;
    const onlineCard = `<section class="card"><h2>Online access</h2><ul class="plain">
        <li>Site password: ${au.enabled ? '<span class="status won">on</span>' : '<span class="status lost">off</span> <span class="muted small">set <code>site_password</code> in config.json before sharing a link</span>'}${au.admin_required ? ' <span class="muted small">· admin password required for Reset season</span>' : ''}</li>
        <li>Tunnel: ${tunnelStatus}</li>
        ${tn.url ? `<li>Public link: <a href="${esc(tn.url)}" target="_blank" rel="noopener">${esc(tn.url)}</a> <button class="btn ghost small" id="copy-url" data-url="${esc(tn.url)}">Copy</button></li>` : ''}
        ${tn.error ? `<li class="down">${esc(tn.error)}</li>` : ''}
      </ul>
      <p class="muted small">Share the link and the squad password with your friends. A quick tunnel gets a new random address each time the server restarts; for a permanent one, create a tunnel in the Cloudflare Zero Trust dashboard, put its token in <code>tunnel_token</code> and set <code>"tunnel": "token"</code> (see README).</p></section>`;
    return `<section class="card"><h2>Status</h2><ul class="plain">
        <li>Config: ${s.demo ? '<span class="status pending">demo mode</span>' : s.configured ? '<span class="status won">ready</span>' : `<span class="status lost">needs attention</span> ${esc((s.problems || []).join(' '))}`}</li>
        <li>API key: ${esc(s.api_key_masked || 'not set')}</li>
        <li>Region: ${esc(s.region || '–')} · Modes: ${esc((s.modes || []).join(', ') || 'all')} · Poll every ${esc(s.poll_interval_minutes)} min</li>
        <li>Members resolved: ${(s.members || []).length} of ${s.expected_members}</li>
        <li>Last sync: ${t.last_sync ? fmt.ago(t.last_sync) : 'never'}${r ? ` · ${r.new_matches} new game(s), ${r.candidates} candidates checked, ${r.api_calls} API calls` : ''}${t.last_error ? ` · <span class="down">${esc(t.last_error)}</span>` : ''}</li>
        <li>Rate limit: ${rl.remaining != null ? `${rl.remaining} of ${rl.limit} requests left in the current window` : 'unknown until the first request'}</li>
        <li>5-stack games stored: ${s.games}</li></ul>
        <div class="btn-row"><button class="btn" id="sync-now" ${!s.configured || s.demo ? 'disabled' : ''}>Sync now</button><button class="btn ghost" id="sync-full" ${!s.configured || s.demo ? 'disabled' : ''}>Full re-scan</button></div></section>
      ${onlineCard}
      <section class="card"><h2>Squad</h2>${members ? `<ul class="plain">${members}</ul>` : '<p class="muted">No members resolved yet.</p>'}</section>
      <section class="card"><h2>How to set up</h2><ol>
        <li>Get a free HenrikDev API key: open <a href="https://api.henrikdev.xyz/dashboard/" target="_blank" rel="noopener">api.henrikdev.xyz/dashboard</a>, sign in with Discord, and generate a <em>Basic</em> key.</li>
        <li>Open <code>config.json</code> next to <code>server.py</code>. Paste the key into <code>api_key</code>, set your <code>region</code>, and list all five Riot IDs (Name#TAG) under <code>members</code>. Nicknames are optional.</li>
        <li>Restart the server (<code>python server.py</code> or <code>run.bat</code>). The first sync scans everyone's stored match history and keeps only games where all five of you were on the same team.</li>
        <li>Leave it running. It re-checks every few minutes, records new 5-stack games and settles open bets automatically. Set <code>host</code> to <code>0.0.0.0</code> to let friends on your network open it too.</li></ol>
        <p class="muted small">Only game modes listed in <code>modes</code> count (default: competitive, unrated, premier). HenrikDev's stored history can have gaps; a game that is missing for one player is verified through the full match record.</p></section>
      <section class="card"><h2>Sync log</h2><div class="log">${log || '<span class="muted">Nothing yet.</span>'}</div></section>`;
  }

  // ---- render & events ---------------------------------------------------------------
  function draw() {
    const view = $('#view');
    try {
      switch (state.view) {
        case 'overview': view.innerHTML = viewOverview(); break;
        case 'players': view.innerHTML = viewPlayers(); break;
        case 'viz': view.innerHTML = viewViz(); break;
        case 'odds': view.innerHTML = viewOdds(); break;
        case 'bettors': view.innerHTML = viewBettors(); break;
        case 'matches': view.innerHTML = viewMatches(); break;
        default: view.innerHTML = viewSetup();
      }
    } catch (e) {
      console.error(e);
      view.innerHTML = `<div class="card error"><h2>Something went wrong drawing this page</h2><p>${esc(e.message)}</p></div>`;
    }
    bind();
    if (state.view === 'viz' && view.querySelector('[data-chart]')) window.FiveViz.mount(view);
  }

  async function render() {
    const view = $('#view');
    view.setAttribute('aria-busy', 'true');
    try {
      switch (state.view) {
        case 'overview': case 'players': await loadStats(); break;
        case 'viz': await loadInsights(); break;
        case 'odds': await Promise.all([loadOdds(), loadBets(), loadContent()]); break;
        case 'bettors': await loadBets(); break;
        case 'matches': await loadMatches(); break;
        default: await loadStatus();
      }
      draw();
    } catch (e) {
      view.innerHTML = `<div class="card error"><h2>Could not load data</h2><p>${esc(e.message)}</p></div>`;
    }
    view.removeAttribute('aria-busy');
  }

  function bind() {
    const view = $('#view');
    $$('button.odd', view).forEach((b) => b.addEventListener('click', () => toggleSlip(b.dataset.m, b.dataset.s)));
    $('#ctx-map')?.addEventListener('change', (e) => { state.ctx.map = e.target.value; refreshOdds(); });
    $$('.ctx-agent', view).forEach((sel) => sel.addEventListener('change', (e) => {
      const p = e.target.dataset.puuid;
      if (e.target.value) state.ctx.agents[p] = e.target.value; else delete state.ctx.agents[p];
      refreshOdds();
    }));
    $('#ctx-clear')?.addEventListener('click', () => { state.ctx = { map: '', agents: {} }; refreshOdds(); });
    $('#odds-format')?.addEventListener('change', (e) => { state.oddsFormat = e.target.value; localStorage.setItem('fs.oddsFormat', state.oddsFormat); draw(); });
    bindSlip();
    $$('.cancel-bet', view).forEach((b) => b.addEventListener('click', async () => {
      const headers = {};
      if (b.classList.contains('admin')) {
        const pw = window.prompt('Admin password');
        if (pw == null) return;
        headers['X-Admin-Password'] = pw;
      }
      try { await api('/api/bets/' + b.dataset.id, { method: 'DELETE', headers }); toast('Bet cancelled, stake refunded'); await loadBets(); draw(); }
      catch (e) { toast(e.message, 'bad'); }
    }));
    $('#reset-bets')?.addEventListener('click', async () => {
      if (!window.confirm('Reset every bettor to the starting balance and delete all bets?')) return;
      const headers = {};
      if (state.status.auth && state.status.auth.admin_required) {
        const pw = window.prompt('Admin password');
        if (pw == null) return;
        headers['X-Admin-Password'] = pw;
      }
      try { await api('/api/bettors/reset', { method: 'POST', body: '{}', headers }); toast('Season reset'); await loadBets(); draw(); }
      catch (e) { toast(e.message, 'bad'); }
    });
    $('#copy-url')?.addEventListener('click', async (e) => {
      try { await navigator.clipboard.writeText(e.currentTarget.dataset.url); toast('Link copied'); }
      catch (err) { toast('Could not copy; select the link and copy it manually', 'bad'); }
    });
    $$('tr.match-row', view).forEach((r) => r.addEventListener('click', () => {
      const id = r.dataset.id;
      if (state.expanded.has(id)) state.expanded.delete(id); else state.expanded.add(id);
      draw();
    }));
    ['map', 'result', 'mode'].forEach((k) => $('#f-' + k)?.addEventListener('change', (e) => { state.matchFilter[k] = e.target.value; draw(); }));
    $('#sync-now')?.addEventListener('click', () => sync(false));
    $('#sync-full')?.addEventListener('click', () => sync(true));
  }

  async function sync(full) {
    try {
      const r = await api('/api/sync', { method: 'POST', body: JSON.stringify({ full }) });
      toast(r.busy ? 'A sync is already running' : full ? 'Full re-scan started' : 'Sync started');
      await loadStatus();
      pollUntilIdle();
    } catch (e) {
      toast(e.message, 'bad');
    }
  }

  let polling = false;
  async function pollUntilIdle() {
    if (polling) return;
    polling = true;
    try {
      for (let i = 0; i < 300; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        await loadStatus();
        if (!state.status.tracker?.syncing) { await render(); return; }
      }
    } catch (e) { /* server went away; the periodic refresh will recover */ }
    finally { polling = false; }
  }

  function route() {
    const v = (location.hash || '#overview').slice(1);
    state.view = VIEWS.includes(v) ? v : 'overview';
    window.FiveViz?.hideTip();
    $$('#tabs a').forEach((a) => a.classList.toggle('active', a.dataset.view === state.view));
    render();
  }

  async function init() {
    const saved = new URLSearchParams(location.search).get('theme') || localStorage.getItem('fs.theme');
    if (saved === 'light' || saved === 'dark') document.documentElement.dataset.theme = saved;
    $('#theme-btn').addEventListener('click', () => {
      const cur = document.documentElement.dataset.theme || (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
      const next = cur === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      localStorage.setItem('fs.theme', next);
    });
    $('#sync-btn').addEventListener('click', () => sync(false));
    window.addEventListener('hashchange', route);
    try {
      await Promise.all([loadStatus(), loadContent()]);
    } catch (e) {
      $('#view').innerHTML = `<div class="card error"><h2>Could not reach the server</h2><p>${esc(e.message)}</p></div>`;
      return;
    }
    route();
    if (state.status.tracker?.syncing) pollUntilIdle();
    setInterval(async () => {
      const prevGames = state.status?.games, wasSyncing = state.status?.tracker?.syncing;
      try { await loadStatus(); } catch (e) { return; }
      const nowSyncing = state.status.tracker?.syncing;
      if (nowSyncing && !wasSyncing) pollUntilIdle();
      else if (state.status.games !== prevGames) render();
    }, 20000);
  }

  document.addEventListener('DOMContentLoaded', init);
})();
