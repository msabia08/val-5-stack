/* 5-Stack Tracker: the Charts tab (route #viz). Plain SVG/HTML charts drawn from /api/insights, no dependencies.
 *
 * FiveViz.html(data, helpers) returns the page with empty [data-chart] slots; FiveViz.mount(root) draws
 * each slot at its real width (so text never scales) and redraws on resize. Colors are CSS variables, so
 * the theme toggle re-colors charts without a redraw. Tooltip text is always set with textContent.
 */
(() => {
  'use strict';

  const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  // Games after midnight count toward the night before (a 1 AM Saturday game is Friday night).
  const BUCKETS = [
    { label: 'Morning', sub: '6–12', test: (h) => h >= 6 && h < 12 },
    { label: 'Afternoon', sub: '12–5', test: (h) => h >= 12 && h < 17 },
    { label: 'Evening', sub: '5–9', test: (h) => h >= 17 && h < 21 },
    { label: 'Night', sub: '9 pm–6 am', test: (h) => h >= 21 || h < 6 },
  ];
  const MAX_COMPS = 8;

  let data = null;
  let h = null; // helpers from app.js: esc, fmt, slot(puuid)
  let tipEl = null;
  const cross = {}; // chart name -> { xs, tips, line, plot } for crosshair charts
  let observer = null;
  let lastWidth = 0;

  // ---- small helpers ------------------------------------------------------------
  const pct = (v) => (v == null ? '–' : Math.round(v * 100) + '%');
  const signedPct = (v) => (v == null ? '–' : (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(Math.round(v * 100)) + '%');
  const rec = (r) => `${r.wins}–${r.losses}`;
  const n0 = (v) => (v == null ? '–' : Math.round(v).toLocaleString());
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  // Dates carry the year whenever the history is not all from this calendar year.
  let withYear = false;
  const dateShort = (ts) => new Date(ts * 1000).toLocaleDateString(undefined, withYear ? { month: 'short', day: 'numeric', year: 'numeric' } : { month: 'short', day: 'numeric' });
  const dateLong = (ts) => {
    const d = new Date(ts * 1000);
    return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', ...(withYear ? { year: 'numeric' } : {}) }) + ' · ' +
      d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  };
  const GAP_DAYS = 45; // a longer break between games is marked on the form chart
  function gapText(seconds) {
    const days = seconds / 86400;
    if (days >= 365) { const y = Math.round(days / 365); return `${y} yr${y > 1 ? 's' : ''} later`; }
    const mo = Math.round(days / 30.4);
    return mo >= 2 ? `${mo} months later` : `${Math.round(days)} days later`;
  }
  const SMALL_SAMPLE = 20; // below this many games, the page says its patterns are early reads
  const resultText = (g) => `${g.result === 'win' ? 'W' : g.result === 'loss' ? 'L' : 'D'} ${g.rounds_won}–${g.rounds_lost}`;
  const memberSlot = (puuid) => h.slot(puuid) || 1;
  const nick = (puuid) => (data.members.find((m) => m.puuid === puuid) || {}).nickname || '?';

  // A tooltip spec lives on the mark as JSON: { t: title, r: [[label, value, colorToken|null], ...] }. A row that is a
  // plain string is a short note line (a verdict, a section heading) instead.
  const tip = (title, rows) => ` data-tip="${h.esc(JSON.stringify({ t: title, r: rows }))}" tabindex="0"`;

  function niceMax(v) {
    if (v <= 0) return 1;
    const p = 10 ** Math.floor(Math.log10(v));
    return [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].map((m) => m * p).find((m) => m >= v - 1e-9);
  }

  // Column with a 4px rounded data end and a square baseline end. y0 = baseline, y1 = data end.
  function colPath(x, w, y0, y1) {
    const r = Math.min(4, w / 2, Math.abs(y1 - y0));
    const up = y1 < y0;
    const e = up ? y1 + r : y1 - r;
    return `M${x},${y0}V${e}Q${x},${y1} ${x + r},${y1}H${x + w - r}Q${x + w},${y1} ${x + w},${e}V${y0}Z`;
  }
  // Horizontal bar from x0 (square) to x1 (rounded), centered on cy.
  function barPath(x0, x1, cy, t, round = true) {
    const y = cy - t / 2;
    const r = round ? Math.min(4, t / 2, Math.max(0, x1 - x0)) : 0;
    return `M${x0},${y}H${x1 - r}Q${x1},${y} ${x1},${y + r}V${y + t - r}Q${x1},${y + t} ${x1 - r},${y + t}H${x0}Z`;
  }

  // Diverging fill: t in [-1, 1] -> blue (positive) / red (negative) mixed into the neutral midpoint.
  // money = true colours gains green and losses red, like profit elsewhere on the site.
  function divFill(t, money = false) {
    const p = Math.round(clamp(Math.abs(t), 0, 1) * 100);
    const pole = t >= 0 ? (money ? 'var(--up)' : 'var(--div-pos)') : (money ? 'var(--down)' : 'var(--div-neg)');
    return { bg: `color-mix(in oklab, ${pole} ${p}%, var(--div-mid))`, ink: p > 55 ? '#fff' : 'var(--text)' };
  }

  // The "Show as table" view under each chart (Charts, Bettors and Forecasts tabs). Switched off; set to true to bring
  // every one of them back.
  const SHOW_TABLES = false;

  function table(head, rows) {
    if (!SHOW_TABLES) return '';
    return `<details class="viz-table"><summary>Show as table</summary><div class="table-wrap"><table class="compact"><thead><tr>` +
      head.map((c, i) => `<th${i ? ' class="num"' : ''}>${h.esc(c)}</th>`).join('') + '</tr></thead><tbody>' +
      rows.map((r) => '<tr>' + r.map((c, i) => `<td${i ? ' class="num"' : ''}>${h.esc(c)}</td>`).join('') + '</tr>').join('') +
      '</tbody></table></div></details>';
  }

  // A one-line explanation with the rest folded behind "How this works" (the full text is still one click away).
  const how = (summary, more) => `<details class="how"><summary>${summary}</summary><div class="how-body">${more}</div></details>`;
  const card = (id, title, takeaway, body, extra = '') =>
    `<section class="card viz-card" id="viz-${id}"><h2>${h.esc(title)}</h2>` +
    `${takeaway ? `<p class="viz-takeaway">${takeaway}</p>` : ''}${body}${extra}</section>`;
  const slot = (name, minH) => `<div class="viz-plot" data-chart="${name}" style="min-height:${minH}px"></div>`;
  const legend = (items) => `<div class="viz-legend">${items.map((it) =>
    `<span class="viz-legend-item"><span class="viz-key ${it.kind || 'rect'}" style="background:var(${it.color})"></span>${h.esc(it.label)}</span>`).join('')}</div>`;

  // Bettor colors follow the person: a bettor who is also a squad member keeps that member's color.
  // A bettor is colored like the squad member whose account it is (the page's bettorSlot helper).
  function bettorColors() {
    return new Map(data.bankroll.map((b) => [b.name, `--s${h.bettorSlot(b.name)}`]));
  }

  // ---- page -------------------------------------------------------------------------
  // Chart genres for the buttons at the top of the Charts tab; "all" keeps the full page in its usual order.
  const GENRES = [['all', 'All'], ['results', 'Results'], ['players', 'Players'], ['rounds', 'Rounds']];

  function html(d, helpers, genre = 'all') {
    data = d; h = helpers;
    const g = d.games;
    if (!g.length) return null;
    const thisYear = new Date().getFullYear();
    withYear = g.some((x) => new Date(x.ts * 1000).getFullYear() !== thisYear);
    const m = d.moments;
    const recOr = (r) => (r.games ? rec(r) : '–');
    const tile = (label, value, sub) =>
      `<div class="tile"><div class="tile-label">${h.esc(label)}</div><div class="tile-value">${value}</div><div class="tile-sub">${sub}</div></div>`;
    const kpis = `<section class="kpis">
      ${tile(`Close games (±${m.close_margin} rounds)`, m.close.games ? rec(m.close) : '–', m.close.games ? `${pct(m.close.win_rate)} won · ${plural(m.close.games, 'game')}` : 'none yet')}
      ${tile(`Blowouts (±${m.blowout_margin}+ rounds)`, m.blowout.games ? rec(m.blowout) : '–', m.blowout.games ? `${pct(m.blowout.win_rate)} won · ${plural(m.blowout.games, 'game')}` : 'none yet')}
      ${tile('After a win / after a loss', `${recOr(m.after_win)} <span class="muted">/</span> ${recOr(m.after_loss)}`, `next game the same night · ${pct(m.after_win.win_rate)} / ${pct(m.after_loss.win_rate)} won`)}
      ${tile('First game / later games', `${recOr(m.first_of_session)} <span class="muted">/</span> ${recOr(m.later_in_session)}`, `of a night · ${pct(m.first_of_session.win_rate)} / ${pct(m.later_in_session.win_rate)} won · ${plural(m.sessions, 'night')}`)}
    </section>`;
    const early = g.length < SMALL_SAMPLE
      ? `<p class="viz-note">Only ${plural(g.length, '5-stack game')} so far, so treat these as early reads: one more win or loss can move a percentage a lot. Records are shown next to rates for that reason.</p>`
      : '';

    const cols = (a, b) => `<div class="viz-cols">${a}${b}</div>`;
    const pages = {
      all: () => kpis + formCard() + cols(damageCard(), aimCard()) + swingCard() + cols(timeCard(), sessionCard()) +
        mapCard() + agentCard() + cols(clutchCard(), multiKillCard()) + cols(compCard(), spikeCard()),
      results: () => kpis + formCard() + cols(timeCard(), sessionCard()) + compCard(),  // how the squad does, and when
      players: () => cols(damageCard(), aimCard()) + swingCard() + mapCard() + agentCard(),  // each player's impact, maps, agents
      rounds: () => cols(clutchCard(), multiKillCard()) + spikeCard(),  // clutches, multi-kills, spike sites
    };
    const pick = pages[genre] ? genre : 'all';
    const buttons = `<div class="seg viz-genres" role="tablist" aria-label="Chart genres">${GENRES.map(([k, label]) =>
      `<button type="button" class="seg-btn viz-genre ${k === pick ? 'on' : ''}" data-v="${k}" role="tab" aria-selected="${k === pick}">${label}</button>`).join('')}</div>`;
    return buttons + early + pages[pick]();
  }

  // ---- 1. form over time + round margins ------------------------------------------------
  function formCard() {
    const g = data.games, w = data.constants.form_window;
    const last = g.slice(-w);
    const lw = last.filter((x) => x.result === 'win').length, ll = last.filter((x) => x.result === 'loss').length;
    const all = { wins: g.filter((x) => x.result === 'win').length, losses: g.filter((x) => x.result === 'loss').length };
    const take = `Last ${last.length}: <strong>${lw}–${ll}</strong> (${pct(lw / last.length)}) · all games ${rec(all)}`;
    const rows = g.slice().reverse().map((x) => [dateLong(x.ts), x.map || '', resultText(x), x.form == null ? '–' : pct(x.form)]);
    return card('form', 'Form over time', take,
      legend([{ label: `Win rate, last ${w} games`, color: '--accent', kind: 'line' }, { label: 'Won by', color: '--up' }, { label: 'Lost by', color: '--down' }]) +
      slot('form', 310), table(['Game', 'Map', 'Result', `Win rate, last ${w}`], rows));
  }

  function drawForm(el, W) {
    const g = data.games, n = g.length;
    const m = { l: 44, r: 44, t: 12 };
    const H1 = 150, gap = 40, H2 = 96, axis = 24;
    const pw = W - m.l - m.r, band = pw / n;
    const x = (i) => m.l + (i + 0.5) * band;
    const y1 = (v) => m.t + (1 - v) * H1;
    const top2 = m.t + H1 + gap, mid2 = top2 + H2 / 2;
    const maxM = niceMax(Math.max(2, ...g.map((q) => Math.abs(q.margin))));
    const y2 = (v) => mid2 - (v / maxM) * (H2 / 2);
    const Ht = top2 + H2 + axis;
    let s = `<svg class="viz-svg" width="${W}" height="${Ht}" role="img" aria-label="Rolling win rate and round margin for each game">`;
    // panel 1 grid
    [0, 0.5, 1].forEach((v) => {
      s += `<line class="${v === 0.5 ? 'viz-ref' : 'viz-grid'}" x1="${m.l}" x2="${W - m.r}" y1="${y1(v)}" y2="${y1(v)}"/>` +
        `<text class="viz-ax" x="${m.l - 8}" y="${y1(v) + 4}" text-anchor="end">${v * 100}%</text>`;
    });
    const pts = g.map((q, i) => (q.form == null ? null : [x(i), y1(q.form)])).filter(Boolean);
    if (pts.length > 1) s += `<path class="viz-line" style="stroke:var(--accent)" d="M${pts.map((p) => p.join(',')).join('L')}"/>`;
    if (pts.length) {
      const [lx, ly] = pts[pts.length - 1];
      s += `<circle class="viz-dot" cx="${lx}" cy="${ly}" r="4" style="fill:var(--accent)"/>` +
        `<text class="viz-label" x="${lx + 8}" y="${ly + 4}">${pct(g[n - 1].form)}</text>`;
    }
    // panel 2: round margin columns
    s += `<text class="viz-ax" x="${m.l - 8}" y="${y2(maxM) + 4}" text-anchor="end">+${maxM}</text>` +
      `<text class="viz-ax" x="${m.l - 8}" y="${y2(-maxM) + 4}" text-anchor="end">−${maxM}</text>` +
      `<line class="viz-grid" x1="${m.l}" x2="${W - m.r}" y1="${y2(maxM)}" y2="${y2(maxM)}"/>` +
      `<line class="viz-grid" x1="${m.l}" x2="${W - m.r}" y1="${y2(-maxM)}" y2="${y2(-maxM)}"/>`;
    const cw = Math.max(2, Math.min(24, band - 2));
    g.forEach((q, i) => {
      if (!q.margin) return;
      const color = q.margin > 0 ? '--up' : '--down';
      s += `<path style="fill:var(${color})" d="${colPath(x(i) - cw / 2, cw, mid2, y2(q.margin))}"/>`;
    });
    s += `<line class="viz-base" x1="${m.l}" x2="${W - m.r}" y1="${mid2}" y2="${mid2}"/>` +
      `<text class="viz-ax" x="${m.l - 8}" y="${mid2 + 4}" text-anchor="end">0</text>` +
      `<text class="viz-ax viz-panel" x="${m.l}" y="${top2 - 14}">Round margin per game</text>`;
    // Long breaks between games are invisible on a per-game axis, so mark each one.
    const gaps = [];
    for (let i = 1; i < n; i++) if (g[i].ts - g[i - 1].ts > GAP_DAYS * 86400) gaps.push(i);
    gaps.forEach((i) => {
      const gx = m.l + i * band;
      s += `<line class="viz-gap" x1="${gx}" x2="${gx}" y1="${m.t}" y2="${top2 + H2}"/>` +
        `<text class="viz-ax viz-gap-label" x="${gx + 4}" y="${m.t + 10}">${h.esc(gapText(g[i].ts - g[i - 1].ts))}</text>`;
    });
    // x axis: first and last game and the first game after each gap, then evenly spaced fillers.
    // Ticks need room for their label, and a date is never repeated.
    const labelW = withYear ? 96 : 60;
    const want = [0, n - 1, ...gaps];
    const fill = Math.max(2, Math.floor(pw / (labelW + 30)));
    for (let k = 0; k < fill; k++) want.push(Math.round((k * (n - 1)) / Math.max(1, fill - 1)));
    const placed = [];
    [...new Set(want)].forEach((i) => {
      const label = dateShort(g[i].ts);
      if (placed.some((p) => Math.abs(x(p.i) - x(i)) < labelW || p.label === label)) return;
      placed.push({ i, label });
    });
    placed.forEach(({ i, label }) => {
      const anchor = x(i) - labelW / 2 < 0 ? 'start' : x(i) + labelW / 2 > W ? 'end' : 'middle';
      s += `<text class="viz-ax" x="${x(i)}" y="${Ht - 6}" text-anchor="${anchor}">${h.esc(label)}</text>`;
    });
    s += `<line class="viz-cross" x1="0" x2="0" y1="${m.t}" y2="${top2 + H2}" visibility="hidden"/>` +
      `<rect class="viz-hit" data-cross="form" x="${m.l}" y="0" width="${pw}" height="${top2 + H2}" tabindex="0" aria-label="Game-by-game details; use the arrow keys"/></svg>`;
    el.innerHTML = s;
    cross.form = {
      xs: g.map((_, i) => x(i)),
      tips: g.map((q) => ({
        t: dateLong(q.ts),
        r: [[`on ${q.map || '?'}${q.mode_label ? ' · ' + q.mode_label : ''}`, resultText(q), q.margin > 0 ? '--up' : q.margin < 0 ? '--down' : null],
          [`win rate, last ${data.constants.form_window}`, q.form == null ? '–' : pct(q.form), '--accent'],
          ['of the night', `game ${q.game_in_session}`, null]],
      })),
    };
  }

  // ---- 2. when you win: day x time of day ----------------------------------------------
  function timeGrid() {
    const cells = DAYS.map(() => BUCKETS.map(() => ({ wins: 0, losses: 0, games: 0 })));
    data.games.forEach((g) => {
      const d = new Date(g.ts * 1000), hr = d.getHours();
      let day = (d.getDay() + 6) % 7;
      if (hr < 6) day = (day + 6) % 7;
      const b = BUCKETS.findIndex((x) => x.test(hr));
      const c = cells[day][b];
      c.games++;
      if (g.result === 'win') c.wins++; else if (g.result === 'loss') c.losses++;
    });
    return cells;
  }

  function timeCard() {
    const cells = timeGrid();
    const flat = [];
    cells.forEach((row, di) => row.forEach((c, bi) => c.games && flat.push({ ...c, di, bi, wr: c.wins / c.games })));
    const ranked = flat.filter((c) => c.games >= 3).sort((a, b) => b.wr - a.wr || b.games - a.games);
    const where = (c) => `${DAYS[c.di]} ${BUCKETS[c.bi].label.toLowerCase()}`;
    const take = ranked.length >= 2
      ? `Best: <strong>${h.esc(where(ranked[0]))}</strong> (${rec(ranked[0])}) · worst: <strong>${h.esc(where(ranked[ranked.length - 1]))}</strong> (${rec(ranked[ranked.length - 1])})`
      : 'Needs at least 3 games in a time slot to call a best and worst.';
    let grid = `<div class="table-wrap"><table class="viz-heat"><thead><tr><th></th>` +
      BUCKETS.map((b) => `<th><div>${h.esc(b.label)}</div><div class="muted small">${h.esc(b.sub)}</div></th>`).join('') + '</tr></thead><tbody>';
    cells.forEach((row, di) => {
      grid += `<tr><th scope="row">${DAYS[di]}</th>`;
      row.forEach((c, bi) => {
        if (!c.games) { grid += '<td class="viz-empty"></td>'; return; }
        const wr = c.wins / c.games;
        const f = divFill((wr - 0.5) * 2 * (c.games / (c.games + 2))); // few games -> paler
        grid += `<td style="background:${f.bg};color:${f.ink}"${tip(`${DAYS[di]} · ${BUCKETS[bi].label}`, [['record', rec(c), null], ['win rate', pct(wr), null], ['games', String(c.games), null]])}>${rec(c)}</td>`;
      });
      grid += '</tr>';
    });
    grid += '</tbody></table></div>';
    const scale = `<div class="viz-scale"><span>More losses</span><span class="viz-scale-bar"></span><span>More wins</span></div>`;
    const rows = [];
    cells.forEach((row, di) => row.forEach((c, bi) => c.games && rows.push([`${DAYS[di]} ${BUCKETS[bi].label}`, rec(c), pct(c.wins / c.games)])));
    return card('time', 'When you win', take, grid + scale +
      '<p class="muted small">Your local time. Games after midnight count toward the night before; cells with few games are paler.</p>',
      table(['Slot', 'Record', 'Win rate'], rows));
  }

  // ---- 3. nightly fatigue: win rate by game number in a session ------------------------
  function sessionCard() {
    const s = data.session_games, first = s[0];
    const later = s.slice(2).reduce((a, x) => ({ wins: a.wins + x.wins, losses: a.losses + x.losses, games: a.games + x.games }), { wins: 0, losses: 0, games: 0 });
    const take = first.games && later.games
      ? `Game 1: <strong>${pct(first.win_rate)}</strong> (${rec(first)}) → game 3 and later: <strong>${pct(later.wins / later.games)}</strong> (${rec(later)})`
      : 'Play a few multi-game nights to see how the squad holds up.';
    return card('session', 'Does the squad fade late?', take, slot('session', 230) +
      `<p class="muted small">A night is a run of games with no break over ${data.constants.session_gap_h} hours.</p>`,
      table(['Game of the night', 'Games', 'Record', 'Win rate'], s.map((x) => [x.label, String(x.games), rec(x), pct(x.win_rate)])));
  }

  function drawSession(el, W) {
    const s = data.session_games;
    const m = { l: 44, r: 12, t: 22, b: 40 }, H = 170;
    const pw = W - m.l - m.r, band = pw / s.length, cw = Math.min(24, band * 0.5);
    const y = (v) => m.t + (1 - v) * H;
    let out = `<svg class="viz-svg" width="${W}" height="${m.t + H + m.b}" role="img" aria-label="Win rate by game number within a night">`;
    [0, 0.5, 1].forEach((v) => {
      out += `<line class="${v === 0 ? 'viz-base' : v === 0.5 ? 'viz-ref' : 'viz-grid'}" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/>` +
        `<text class="viz-ax" x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${v * 100}%</text>`;
    });
    s.forEach((b, i) => {
      const cx = m.l + (i + 0.5) * band;
      if (b.games) {
        out += `<path class="viz-mark" style="fill:var(--accent)" d="${colPath(cx - cw / 2, cw, y(0), y(Math.max(b.win_rate, 0.004)))}"` +
          `${tip(`Game ${b.label} of the night`, [['win rate', pct(b.win_rate), '--accent'], ['record', rec(b), null]])}/>` +
          `<text class="viz-label" x="${cx}" y="${y(b.win_rate) - 6}" text-anchor="middle">${pct(b.win_rate)}</text>`;
      }
      out += `<text class="viz-ax" x="${cx}" y="${m.t + H + 16}" text-anchor="middle">Game ${h.esc(b.label)}</text>` +
        `<text class="viz-ax viz-sub" x="${cx}" y="${m.t + H + 30}" text-anchor="middle">${b.games ? plural(b.games, 'game') : 'none'}</text>`;
    });
    el.innerHTML = out + '</svg>';
  }

  // ---- 4. map x player: ACS vs each player's own average --------------------------------
  function mapCard() {
    const maps = data.maps, ps = data.players.filter((p) => p.games);
    let best = null, worst = null;
    ps.forEach((p) => maps.forEach(({ map }) => {
      const c = p.maps[map];
      if (!c || c.games < 3 || c.vs_avg == null) return;
      if (!best || c.vs_avg > best.v) best = { p, map, v: c.vs_avg };
      if (!worst || c.vs_avg < worst.v) worst = { p, map, v: c.vs_avg };
    }));
    const take = best && worst
      ? `Biggest lift: <strong>${h.esc(best.p.nickname)} on ${h.esc(best.map)}</strong> (${signedPct(best.v)}) · biggest drop: <strong>${h.esc(worst.p.nickname)} on ${h.esc(worst.map)}</strong> (${signedPct(worst.v)})`
      : 'Needs a few games per map to compare.';
    let grid = `<div class="table-wrap"><table class="viz-heat"><thead><tr><th></th>` +
      maps.map((x) => `<th><div>${h.esc(x.map)}</div><div class="muted small">${plural(x.games, 'game')}</div></th>`).join('') + '</tr></thead><tbody>';
    ps.forEach((p) => {
      grid += `<tr><th scope="row"><span class="swatch s${memberSlot(p.puuid)}"></span>${h.esc(p.nickname)} <span class="muted small">avg ${n0(p.acs)}</span></th>`;
      maps.forEach(({ map }) => {
        const c = p.maps[map];
        if (!c) { grid += '<td class="viz-empty"></td>'; return; }
        const t = c.games >= 2 && c.vs_avg != null ? clamp(c.vs_avg / 0.2, -1, 1) : 0;
        const f = divFill(t);
        const cls = c.games < 2 ? ' class="viz-thin"' : '';
        grid += `<td${cls} style="background:${c.games >= 2 ? f.bg : 'transparent'};color:${c.games >= 2 ? f.ink : 'var(--muted)'}"` +
          `${tip(`${p.nickname} on ${map}`, [['ACS', n0(c.acs), null], ['vs own average', signedPct(c.vs_avg), null], ['games', String(c.games), null]])}>${n0(c.acs)}</td>`;
      });
      grid += '</tr>';
    });
    grid += '</tbody></table></div>';
    const rows = [];
    ps.forEach((p) => maps.forEach(({ map }) => { const c = p.maps[map]; if (c) rows.push([`${p.nickname} · ${map}`, n0(c.acs), signedPct(c.vs_avg), String(c.games)]); }));
    return card('maps', 'Who shows up on which map', take, grid +
      '<div class="viz-scale"><span>Below own average</span><span class="viz-scale-bar"></span><span>Above own average</span></div>' +
      '<p class="muted small">Each cell is ACS on that map, colored against the player\'s own average (full color at ±20%). Single games are left uncolored.</p>',
      table(['Player · map', 'ACS', 'vs own average', 'Games'], rows));
  }

  // ---- 5. who swings results: ACS in wins vs losses --------------------------------------
  const swingRows = () => data.players.filter((p) => p.games_win && p.games_loss)
    .map((p) => ({ ...p, gap: p.acs_win - p.acs_loss })).sort((a, b) => b.gap - a.gap);

  function swingCard() {
    const rows = swingRows();
    const take = rows.length
      ? `<strong>${h.esc(rows[0].nickname)}</strong> swings the most: ${n0(rows[0].gap)} more ACS in wins than in losses` +
        (rows[rows.length - 1].gap < 0 ? ` · <strong>${h.esc(rows[rows.length - 1].nickname)}</strong> actually scores more in losses` : '')
      : 'Needs both wins and losses on record.';
    return card('swing', 'Who swings results', take,
      '<div class="viz-legend"><span class="viz-legend-item"><b class="swing-key up">W</b>ACS in wins</span>' +
      '<span class="viz-legend-item"><b class="swing-key down">L</b>ACS in losses</span></div>' +
      slot('swing', 34 + rows.length * 38) +
      '<p class="muted small">The bigger the gap, the more the result tracks that player\'s game.</p>',
      table(['Player', 'ACS in wins', 'ACS in losses', 'Gap'], rows.map((p) => [p.nickname, n0(p.acs_win), n0(p.acs_loss), n0(p.gap)])));
  }

  function drawSwing(el, W) {
    const rows = swingRows();
    if (!rows.length) { el.innerHTML = '<p class="muted">No data yet.</p>'; return; }
    const m = { l: 110, r: 60, t: 8, b: 26 }, rh = 38, H = rows.length * rh;
    const lo = Math.min(...rows.map((p) => p.acs_loss)), hi = Math.max(...rows.map((p) => p.acs_win));
    const step = niceMax((hi - lo) / 4);
    const x0 = Math.floor((lo - step * 0.5) / step) * step, x1 = Math.ceil((hi + step * 0.5) / step) * step;
    const pw = W - m.l - m.r, x = (v) => m.l + ((v - x0) / (x1 - x0)) * pw;
    let s = `<svg class="viz-svg" width="${W}" height="${m.t + H + m.b}" role="img" aria-label="ACS in wins versus losses per player">`;
    for (let v = x0; v <= x1 + 1e-9; v += step) {
      s += `<line class="viz-grid" x1="${x(v)}" x2="${x(v)}" y1="${m.t}" y2="${m.t + H}"/>` +
        `<text class="viz-ax" x="${x(v)}" y="${m.t + H + 18}" text-anchor="middle">${n0(v)}</text>`;
    }
    rows.forEach((p, i) => {
      const cy = m.t + i * rh + rh / 2;
      // A big W at their ACS in wins and an L at their ACS in losses, nudged apart when they'd overlap.
      let xw = x(p.acs_win), xl = x(p.acs_loss);
      if (Math.abs(xw - xl) < 16) { const mid = (xw + xl) / 2, dir = xw >= xl ? 1 : -1; xw = mid + dir * 8; xl = mid - dir * 8; }
      s += `<g class="viz-row"${tip(p.nickname, [['in wins', `${n0(p.acs_win)} ACS`, '--up'], ['in losses', `${n0(p.acs_loss)} ACS`, '--down'], ['games', `${p.games_win} W · ${p.games_loss} L`, null]])}>` +
        `<rect class="viz-rowhit" x="0" y="${cy - rh / 2}" width="${W}" height="${rh}"/>` +
        `<circle cx="10" cy="${cy}" r="5" style="fill:var(--s${memberSlot(p.puuid)})"/>` +
        `<text class="viz-label" x="22" y="${cy + 4}">${h.esc(p.nickname)}</text>` +
        `<line class="viz-connector" x1="${x(p.acs_loss)}" x2="${x(p.acs_win)}" y1="${cy}" y2="${cy}"/>` +
        `<text class="swing-mark down" x="${xl.toFixed(1)}" y="${cy}" text-anchor="middle" dominant-baseline="central">L</text>` +
        `<text class="swing-mark up" x="${xw.toFixed(1)}" y="${cy}" text-anchor="middle" dominant-baseline="central">W</text>` +
        `<text class="viz-label" x="${W - m.r + 10}" y="${cy + 4}">${p.gap >= 0 ? '+' : '−'}${n0(Math.abs(p.gap))}</text></g>`;
    });
    el.innerHTML = s + '</svg>';
  }

  // ---- 6. aim profile: head / body / legs -------------------------------------------------
  // A figure per player, side by side (sharpest first): head, body and legs each show the share of the player's hits
  // that land there, shaded against the rest of the squad for that part (the highest share gets the strongest blue).
  const AIM = [['head', 'Head', '--aim-1'], ['body', 'Body', '--aim-2'], ['leg', 'Legs', '--aim-3']];
  const aimRows = () => data.players.filter((p) => p.aim.head + p.aim.body + p.aim.leg > 0).sort((a, b) => b.aim.head_pct - a.aim.head_pct);
  // The figure, in a 100 x 220 box: a round head, a body (a torso with rounded shoulders, a little narrower at the
  // hips, and two slightly tapered arms) and two slightly tapered legs, a few units apart. The right-hand arm and leg
  // are the left ones mirrored.
  const MIRROR = (d) => `<path d="${d}"/><path transform="matrix(-1 0 0 1 100 0)" d="${d}"/>`;
  const FIGURE = {
    head: '<circle cx="50" cy="19" r="16"/>',
    body: '<path d="M34 40 H66 Q76 40 76 51 L73 110 H27 L24 51 Q24 40 34 40 Z"/>' +
      MIRROR('M8.5 51 Q8.5 45 14 45 Q19.5 45 19.5 51 L18.5 103 Q18.5 109 14 109 Q9.5 109 9.5 103 Z'),
    leg: MIRROR('M31.5 113 H48.5 L47.5 207 Q47.5 214 41.5 214 H36 Q30 214 30 207 L28.5 117 Q28.5 113 31.5 113 Z'),
  };
  const AIM_LABEL_AT = { head: [50, 19], body: [50, 76], leg: [50, 162] };

  function aimCard() {
    const rows = aimRows();
    const take = rows.length ? `Sharpest: <strong>${h.esc(rows[0].nickname)}</strong>, ${(rows[0].aim.head_pct * 100).toFixed(1)}% of hits to the head` : '';
    const scale = '<div class="viz-legend"><span class="viz-legend-item"><span class="aim-scale" aria-hidden="true"></span>Stronger blue = the highest share in the squad for that part</span></div>';
    return card('aim', 'Aim profile', take, scale + slot('aim', 200),
      table(['Player', 'Head', 'Body', 'Legs', 'Hits'], rows.map((p) => [p.nickname, pct(p.aim.head_pct), pct(p.aim.body_pct), pct(p.aim.leg_pct), n0(p.aim.head + p.aim.body + p.aim.leg)])));
  }

  // The figures' layout at width W (5 in a row, or 3 + 2 on a phone). The damage card beside it uses the height to
  // end level with it.
  function aimLayout(W, n) {
    const perRow = W >= 420 ? Math.min(5, n) : Math.min(3, n);
    const colW = W / perRow, sc = Math.min(1.2, (colW - 12) / 100), figH = 220 * sc, rowH = figH + 40;
    return { perRow, colW, sc, figH, rowH, height: Math.ceil(n / perRow) * rowH };
  }

  function drawAim(el, W) {
    const rows = aimRows();
    if (!rows.length) { el.innerHTML = '<p class="muted">No shot data yet.</p>'; return; }
    const { perRow, colW, sc, figH, rowH } = aimLayout(W, rows.length);
    // Each part on its own squad scale: the lowest share in the squad is pale, the highest the strongest blue.
    const span = Object.fromEntries(AIM.map(([k]) => {
      const vals = rows.map((p) => p.aim[k + '_pct']);
      return [k, [Math.min(...vals), Math.max(...vals)]];
    }));
    const shade = (k, v) => {
      const [lo, hi] = span[k];
      return (hi - lo < 1e-9 ? 0.6 : 0.18 + 0.82 * ((v - lo) / (hi - lo))).toFixed(3);
    };
    let svg = `<svg class="viz-svg" width="${W}" height="${Math.ceil(rows.length / perRow) * rowH}" role="img" aria-label="Share of hits to head, body and legs per player">`;
    rows.forEach((p, i) => {
      const row = Math.floor(i / perRow), inRow = Math.min(perRow, rows.length - row * perRow);
      const left = (W - inRow * colW) / 2 + (i % perRow) * colW, x0 = left + (colW - 100 * sc) / 2, y0 = row * rowH + 4;
      const total = p.aim.head + p.aim.body + p.aim.leg, cx = x0 + 50 * sc;
      svg += `<g class="viz-row"${tip(p.nickname, AIM.map(([k, label, color]) => [label.toLowerCase(), `${(p.aim[k + '_pct'] * 100).toFixed(1)}%`, color]).concat([['hits', n0(total), null]]))}>` +
        `<rect class="viz-rowhit" x="${left + 2}" y="${row * rowH}" width="${colW - 4}" height="${rowH - 2}" rx="8"/>` +
        `<g transform="translate(${x0.toFixed(1)} ${y0}) scale(${sc.toFixed(3)})">` +
        AIM.map(([k]) => `<g class="aim-part" style="fill-opacity:${shade(k, p.aim[k + '_pct'])}">${FIGURE[k]}</g>`).join('') + '</g>';
      AIM.forEach(([k]) => {
        const [ux, uy] = AIM_LABEL_AT[k];
        svg += `<text class="aim-pct${k === 'head' ? ' head' : ''}" x="${(x0 + ux * sc).toFixed(1)}" y="${(y0 + uy * sc + 4).toFixed(1)}" text-anchor="middle">${Math.round(p.aim[k + '_pct'] * 100)}%</text>`;
      });
      svg += `<text class="viz-label" x="${cx.toFixed(1)}" y="${(y0 + figH + 18).toFixed(1)}" text-anchor="middle">${h.esc(p.nickname)}</text>` +
        `<rect x="${(cx - 12).toFixed(1)}" y="${(y0 + figH + 25).toFixed(1)}" width="24" height="3" rx="1.5" style="fill:var(--s${memberSlot(p.puuid)})"/></g>`;
    });
    el.innerHTML = svg + '</svg>';
  }

  // ---- 7. who carries the damage: all-time damage as 100 bullets, a row per player ------------------------------
  // data: each game's damage_cum, every player's share of all the squad's damage from the first game through it; the
  // last game's is the all-time split. Each share is rounded to whole bullets that always add up to 100.
  function damageSplit() {
    const g = data.games.filter((x) => Object.keys(x.damage_cum || {}).length);
    if (!g.length) return { games: 0, rows: [] };
    const last = g[g.length - 1].damage_cum;
    const rows = data.members.filter((m) => last[m.puuid]).map((m) => ({ m, share: last[m.puuid] })).sort((a, b) => b.share - a.share);
    const counts = toCounts(rows.map((x) => x.share), 100);
    return { games: g.length, rows: rows.map((x, i) => ({ ...x, bullets: counts[i] })) };
  }

  // Whole counts out of n in proportion to shares: each share rounded, with the leftovers going to the biggest
  // remainders so the counts always add up to n (no half bullets).
  function toCounts(shares, n) {
    const raw = shares.map((v) => v * n), counts = raw.map(Math.floor);
    let left = n - counts.reduce((a, c) => a + c, 0);
    raw.map((v, i) => [v - counts[i], i]).sort((a, b) => b[0] - a[0]).forEach(([, i]) => { if (left > 0) { counts[i] += 1; left -= 1; } });
    return counts;
  }

  function damageCard() {
    const d = damageSplit(), top = d.rows[0];
    const take = top ? `<strong>${h.esc(top.m.nickname)}</strong> has dealt ${top.bullets} of every 100 damage across all ${plural(d.games, 'game')} (an even split is 20)` : '';
    return card('damage', 'Who carries the damage', take,
      slot('damage', 5 * 34) +
      '<p class="muted small">Every 100 damage the squad has dealt: one bullet per 1%.</p>',
      table(['Player', 'Bullets', 'Share'], d.rows.map((x) => [x.m.nickname, n0(x.bullets), `${(x.share * 100).toFixed(1)}%`])));
  }

  // 100 bullets, a row per player (biggest share first): their name, a bullet per 1% of the damage and the count.
  // The bullets shrink when needed so the longest row fits; hovering a player's row fades everyone else's.
  const bullet = (x, y, w, hgt, color) => {
    const tipH = w * 1.15, neck = y + tipH;
    return `<path style="fill:var(${color})" d="M${x.toFixed(1)} ${neck.toFixed(1)} Q${x.toFixed(1)} ${(y + tipH * 0.2).toFixed(1)} ${(x + w / 2).toFixed(1)} ${y.toFixed(1)} ` +
      `Q${(x + w).toFixed(1)} ${(y + tipH * 0.2).toFixed(1)} ${(x + w).toFixed(1)} ${neck.toFixed(1)} Z"/>` +
      `<rect class="dmg-casing" x="${x.toFixed(1)}" y="${(neck + 1).toFixed(1)}" width="${w.toFixed(1)}" height="${(hgt - tipH - 1).toFixed(1)}" rx="1" style="fill:var(${color})"/>`;
  };

  function drawDamage(el, W) {
    const d = damageSplit();
    if (!d.rows.length) { el.innerHTML = '<p class="muted">No damage data yet.</p>'; return; }
    const m = { l: 86, r: 34 }, most = Math.max(...d.rows.map((x) => x.bullets));
    const room = (W - m.l - m.r) / most; // width per bullet, bullet plus the space after it
    const bw = Math.max(3, Math.min(14, room * 0.72)), gap = Math.max(1, Math.min(bw * 0.35, room - bw));
    // Side by side with the aim profile (both half width), the rows spread out to the figures' height so the two
    // cards end level; stacked on a phone they just take the room they need.
    const n = aimRows().length, level = W >= 420 && n ? aimLayout(W, n).height / d.rows.length : 0;
    const bh = Math.min(36, bw * 2.6), rh = Math.max(bh + 10, 24, level);
    let s = `<svg class="viz-svg dmg-belt" width="${W}" height="${d.rows.length * rh}" role="img" aria-label="Every 100 damage the squad has dealt, a row of bullets per player">`;
    d.rows.forEach((x, i) => {
      const color = `--s${memberSlot(x.m.puuid)}`, top = i * rh, cy = top + rh / 2, y0 = cy - bh / 2;
      s += `<g class="viz-row"${tip(x.m.nickname, [['of every 100 damage', n0(x.bullets), color], ['all-time share', `${(x.share * 100).toFixed(1)}%`, null]])}>` +
        `<rect class="viz-rowhit" x="0" y="${top}" width="${W}" height="${rh}"/>` +
        `<circle cx="8" cy="${cy}" r="5" style="fill:var(${color})"/>` +
        `<text class="viz-label" x="20" y="${cy + 4}">${h.esc(x.m.nickname)}</text>`;
      for (let c = 0; c < x.bullets; c += 1) s += bullet(m.l + c * (bw + gap), y0, bw, bh, color);
      s += `<text class="viz-label" x="${(m.l + x.bullets * (bw + gap) + 6).toFixed(1)}" y="${cy + 4}">${x.bullets}</text></g>`;
    });
    el.innerHTML = s + '</svg>';
  }

  // ---- 8. team comps by role shape ---------------------------------------------------------
  function compCard() {
    const rows = data.comps.slice(0, MAX_COMPS);
    const good = rows.filter((c) => c.games >= 3).sort((a, b) => b.win_rate - a.win_rate);
    const take = good.length ? `Best comp with 3+ games: <strong>${h.esc(good[0].label)}</strong> (${rec(good[0])})` : 'Play a comp 3+ times to rank it.';
    return card('comps', 'Team comps by role', take, slot('comps', 30 + rows.length * 42) +
      '<p class="muted small">D Duelist · C Controller · I Initiator · S Sentinel. Most-played first.</p>',
      table(['Comp', 'Games', 'Record', 'Win rate'], data.comps.map((c) => [c.label, String(c.games), rec(c), pct(c.win_rate)])));
  }

  // A comp's roles as a lineup of letters: "2D 1C 1I 1S" -> D D C I S (U for an agent we can't place).
  const lineup = (key) => [...key.matchAll(/(\d+)([A-Z])/g)].flatMap(([, n, r]) => Array(Number(n)).fill(r));
  const CHIP = 18, CHIP_GAP = 3;

  function drawComps(el, W) {
    const rows = data.comps.slice(0, MAX_COMPS);
    if (!rows.length) { el.innerHTML = '<p class="muted">No games yet.</p>'; return; }
    const m = { l: 5 * (CHIP + CHIP_GAP) + 10, r: 86, t: 6, b: 24 }, rh = 42, t = 16, pw = W - m.l - m.r, H = rows.length * rh;
    const x = (v) => m.l + v * pw;
    let s = `<svg class="viz-svg" width="${W}" height="${m.t + H + m.b}" role="img" aria-label="Win rate by team composition">`;
    [0, 0.5, 1].forEach((v) => {
      s += `<line class="${v === 0 ? 'viz-base' : v === 0.5 ? 'viz-ref' : 'viz-grid'}" x1="${x(v)}" x2="${x(v)}" y1="${m.t}" y2="${m.t + H}"/>` +
        `<text class="viz-ax" x="${x(v)}" y="${m.t + H + 16}" text-anchor="middle">${v * 100}%</text>`;
    });
    rows.forEach((c, i) => {
      const cy = m.t + i * rh + rh / 2;
      s += `<g class="viz-row"${tip(c.label, [['win rate', pct(c.win_rate), '--accent'], ['record', rec(c), null]])}>` +
        `<rect class="viz-rowhit" x="0" y="${cy - rh / 2}" width="${W}" height="${rh}"/>` +
        lineup(c.key).map((r, j) => `<rect class="role-chip" x="${j * (CHIP + CHIP_GAP)}" y="${cy - CHIP / 2}" width="${CHIP}" height="${CHIP}" rx="4"/>` +
          `<text class="role-chip-t" x="${j * (CHIP + CHIP_GAP) + CHIP / 2}" y="${cy}" text-anchor="middle" dominant-baseline="central">${h.esc(r)}</text>`).join('') +
        (c.win_rate > 0 ? `<path style="fill:var(--accent)${c.games < 3 ? ';opacity:.45' : ''}" d="${barPath(x(0), x(c.win_rate), cy, t)}"/>` : '') +
        `<text class="viz-label" x="${W - m.r + 10}" y="${cy + 4}">${pct(c.win_rate)} <tspan class="viz-sub">${rec(c)}</tspan></text></g>`;
    });
    el.innerHTML = s + '</svg>';
  }

  // ---- 9. bettor profit over time ------------------------------------------------------------
  function bankrollCard() {
    const b = data.bankroll;
    if (!b.length) return card('bankroll', 'Bettor profit over time', '', '<p class="muted">No settled bets yet. Place some on the Odds &amp; Bets tab.</p>');
    const colors = bettorColors();
    const final = b.map((x) => ({ name: x.name, profit: x.points[x.points.length - 1].profit })).sort((a, b2) => b2.profit - a.profit);
    const take = `<strong>${h.esc(final[0].name)}</strong> leads at ${final[0].profit >= 0 ? '+' : '−'}${n0(Math.abs(final[0].profit))} credits`;
    return card('bankroll', 'Bettor profit over time', take,
      legend(b.map((x) => ({ label: x.name, color: colors.get(x.name), kind: 'line' }))) + slot('bankroll', 230) +
      '<p class="muted small">Each step is a moment when bets settled, evenly spaced rather than to time scale.</p>',
      table(['Bettor', 'Settled bets', 'Profit'], final.map((x) => [x.name, String(b.find((y) => y.name === x.name).points.length), n0(x.profit)])));
  }

  function drawBankroll(el, W) {
    const b = data.bankroll;
    if (!b.length) return;
    const colors = bettorColors();
    const times = [...new Set(b.flatMap((x) => x.points.map((p) => p.ts)))].sort((p, q) => p - q);
    // One step per settlement moment, not real time: long quiet stretches would squash the action.
    const t0 = times[0], t1 = times[times.length - 1];
    const step = new Map(times.map((t, i) => [t, i]));
    const vals = b.flatMap((x) => x.points.map((p) => p.profit)).concat([0]);
    const top = niceMax(Math.max(...vals.map(Math.abs)));
    const lo = Math.min(...vals) < 0 ? -top : 0, hi = Math.max(...vals) > 0 ? top : 0;
    const labelRoom = b.length <= 4 ? 84 : 12;
    const m = { l: 52, r: labelRoom, t: 12, b: 26 }, H = 180, pw = W - m.l - m.r;
    const x = (ts) => m.l + (times.length > 1 ? (step.get(ts) / (times.length - 1)) * pw : pw / 2);
    const y = (v) => m.t + ((hi - v) / ((hi - lo) || 1)) * H;
    let s = `<svg class="viz-svg" width="${W}" height="${m.t + H + m.b}" role="img" aria-label="Cumulative betting profit per bettor">`;
    [hi, hi / 2, 0, lo / 2, lo].filter((v, i, a) => a.indexOf(v) === i).forEach((v) => {
      s += `<line class="${v === 0 ? 'viz-base' : 'viz-grid'}" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/>` +
        `<text class="viz-ax" x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${v > 0 ? '+' : v < 0 ? '−' : ''}${n0(Math.abs(v))}</text>`;
    });
    const ends = [];
    b.forEach((ser) => {
      let d = `M${x(ser.points[0].ts)},${y(0)}`;
      // Steps: profit changes when a bet settles. Bets settled together are one step, not a spike.
      ser.points.forEach((p, i) => { if (ser.points[i + 1]?.ts !== p.ts) d += `H${x(p.ts)}V${y(p.profit)}`; });
      d += `H${x(t1)}`;
      const last = ser.points[ser.points.length - 1];
      s += `<path class="viz-line" style="stroke:var(${colors.get(ser.name)})" d="${d}"/>`;
      ends.push({ name: ser.name, y: y(last.profit), profit: last.profit, color: colors.get(ser.name) });
    });
    ends.forEach((e) => { s += `<circle class="viz-dot" cx="${x(t1)}" cy="${e.y}" r="4" style="fill:var(${e.color})"/>`; });
    if (b.length <= 4) { // direct labels only when they don't collide; otherwise the legend carries identity
      ends.sort((a, c) => a.y - c.y).forEach((e, i, arr) => {
        if (i && e.y - arr[i - 1].y < 13) return;
        s += `<text class="viz-label" x="${x(t1) + 8}" y="${e.y + 4}">${h.esc(e.name)}</text>`;
      });
    }
    s += `<text class="viz-ax" x="${m.l}" y="${m.t + H + 18}">${h.esc(dateShort(t0))}</text>` +
      `<text class="viz-ax" x="${x(t1)}" y="${m.t + H + 18}" text-anchor="end">${h.esc(dateShort(t1))}</text>` +
      `<line class="viz-cross" x1="0" x2="0" y1="${m.t}" y2="${m.t + H}" visibility="hidden"/>` +
      `<rect class="viz-hit" data-cross="bankroll" x="${m.l}" y="0" width="${pw}" height="${m.t + H}" tabindex="0" aria-label="Profit at each settlement; use the arrow keys"/></svg>`;
    el.innerHTML = s;
    const at = (ser, ts) => { let v = null; ser.points.forEach((p) => { if (p.ts <= ts) v = p.profit; }); return v; };
    cross.bankroll = {
      xs: times.map(x),
      tips: times.map((ts) => ({
        t: dateLong(ts),
        r: b.map((ser) => [ser.name, at(ser, ts) == null ? '–' : `${at(ser, ts) >= 0 ? '+' : '−'}${n0(Math.abs(at(ser, ts)))}`, colors.get(ser.name)])
          .sort((p, q) => parseFloat(q[1].replace('−', '-')) - parseFloat(p[1].replace('−', '-'))),
      })),
    };
  }

  // ---- agent pool: who's best on what -------------------------------------------------------
  function agentCard() {
    const ps = data.players.filter((p) => p.games);
    const byAgent = new Map();
    ps.forEach((p) => p.agents.forEach((a) => {
      if (!byAgent.has(a.agent)) byAgent.set(a.agent, { agent: a.agent, role: a.role, games: 0 });
      byAgent.get(a.agent).games += a.games;
    }));
    const roleOrder = Object.keys(data.roles || {}).concat(['Unknown']);
    const agents = [...byAgent.values()].sort((a, b) => roleOrder.indexOf(a.role) - roleOrder.indexOf(b.role) || b.games - a.games);
    if (!agents.length) return '';
    let best = null;
    ps.forEach((p) => p.agents.forEach((a) => {
      if (a.games >= 3 && a.vs_avg != null && (!best || a.vs_avg > best.a.vs_avg)) best = { p, a };
    }));
    const flex = ps.slice().sort((a, b) => b.agents.length - a.agents.length)[0];
    const take = (best ? `Best fit: <strong>${h.esc(best.p.nickname)} on ${h.esc(best.a.agent)}</strong> (${signedPct(best.a.vs_avg)} ACS vs their average, ${plural(best.a.games, 'game')}) · ` : '') +
      `most flexible: <strong>${h.esc(flex.nickname)}</strong> (${plural(flex.agents.length, 'agent')})`;
    let grid = '<div class="table-wrap"><table class="viz-heat"><thead><tr><th></th>' +
      ps.map((p) => `<th><span class="swatch s${memberSlot(p.puuid)}"></span>${h.esc(p.nickname)}<div class="muted small">avg ${n0(p.acs)}</div></th>`).join('') + '</tr></thead><tbody>';
    let role = null;
    agents.forEach((ag) => {
      if (ag.role !== role) {
        role = ag.role;
        grid += `<tr><th scope="rowgroup" colspan="${ps.length + 1}" class="viz-group">${h.esc(role)}s</th></tr>`;
      }
      grid += `<tr><th scope="row">${h.esc(ag.agent)}</th>`;
      ps.forEach((p) => {
        const a = p.agents.find((x) => x.agent === ag.agent);
        if (!a) { grid += '<td class="viz-empty"></td>'; return; }
        const colored = a.games >= 2 && a.vs_avg != null;
        const f = divFill(colored ? clamp(a.vs_avg / 0.2, -1, 1) : 0);
        grid += `<td${colored ? '' : ' class="viz-thin"'} style="background:${colored ? f.bg : 'transparent'};color:${colored ? f.ink : 'var(--muted)'}"` +
          `${tip(`${p.nickname} on ${ag.agent}`, [['ACS', n0(a.acs), null], ['vs own average', signedPct(a.vs_avg), null], ['record', `${a.wins}–${a.games - a.wins}`, null], ['games', String(a.games), null]])}>` +
          `${n0(a.acs)}<div class="viz-cell-sub">${a.games}g</div></td>`;
      });
      grid += '</tr>';
    });
    grid += '</tbody></table></div>';
    const rows = [];
    ps.forEach((p) => p.agents.forEach((a) => rows.push([`${p.nickname} · ${a.agent}`, String(a.games), pct(a.win_rate), n0(a.acs), signedPct(a.vs_avg)])));
    return card('agents', "Agent pool: who's best on what", take, grid +
      '<div class="viz-scale"><span>Below own average</span><span class="viz-scale-bar"></span><span>Above own average</span></div>' +
      "<p class=\"muted small\">ACS on each agent, colored against the player's own average (full color at ±20%); the small number is games played. Single games are left uncolored.</p>",
      table(['Player · agent', 'Games', 'Win rate', 'ACS', 'vs own average'], rows));
  }

  // ---- clutches and multi-kills (round timelines) ---------------------------------------------
  function roundsNote() {
    const r = data.rounds;
    if (!r || r.games >= r.total_games) return '';
    return `<p class="muted small">Round data for ${r.games} of ${plural(r.total_games, 'game')}; older games are filled in a few per sync.</p>`;
  }
  const clutchTotals = (p) => p.clutch.reduce((a, c) => ({ attempts: a.attempts + c.attempts, wins: a.wins + c.wins }), { attempts: 0, wins: 0 });
  const clutchRows = () => data.rounds.players.map((p) => ({ ...p, ...clutchTotals(p) })).sort((a, b) => b.wins - a.wins || a.attempts - b.attempts);

  function clutchCard() {
    const r = data.rounds;
    if (!r || !r.games) return card('clutch', 'Clutches', '', '<p class="muted">No round data yet. It fills in as games sync.</p>');
    const rows = clutchRows();
    const top = rows[0];
    const take = top.attempts ? `<strong>${h.esc(top.nickname)}</strong> wins the most: ${top.wins} of ${plural(top.attempts, 'clutch')} (${pct(top.wins / top.attempts)})` : 'No clutch situations yet.';
    return card('clutch', 'Clutches', take,
      legend([{ label: 'Won', color: '--up' }, { label: 'Lost', color: '--down' }]) + slot('clutch', 16 + rows.length * 38) +
      '<p class="muted small">A clutch is any round where one of you is the last one standing against 1-5 enemies. Hover a bar for the 1vX breakdown.</p>' + roundsNote(),
      table(['Player', '1v1', '1v2', '1v3', '1v4', '1v5', 'Total'], rows.map((p) => [p.nickname, ...p.clutch.map((c) => `${c.wins}/${c.attempts}`), `${p.wins}/${p.attempts}`])));
  }

  function drawClutch(el, W) {
    const rows = clutchRows();
    const m = { l: 110, r: 96, t: 8, b: 8 }, rh = 38, t = 18, pw = W - m.l - m.r;
    const maxA = Math.max(1, ...rows.map((p) => p.attempts));
    let s = `<svg class="viz-svg" width="${W}" height="${m.t + rows.length * rh + m.b}" role="img" aria-label="Clutches won and lost per player">`;
    rows.forEach((p, i) => {
      const cy = m.t + i * rh + rh / 2, x0 = m.l, xw = x0 + (p.wins / maxA) * pw, xa = x0 + (p.attempts / maxA) * pw;
      s += `<g class="viz-row"${tip(`${p.nickname}: ${p.wins} of ${plural(p.attempts, 'clutch')}`, p.clutch.filter((c) => c.attempts).map((c) => [`1v${c.vs}`, `${c.wins}/${c.attempts}`, null]))}>` +
        `<rect class="viz-rowhit" x="0" y="${cy - rh / 2}" width="${W}" height="${rh}"/>` +
        `<circle cx="10" cy="${cy}" r="5" style="fill:var(--s${memberSlot(p.puuid)})"/><text class="viz-label" x="22" y="${cy + 4}">${h.esc(p.nickname)}</text>`;
      if (p.wins) s += `<path style="fill:var(--up)" d="${barPath(x0, Math.max(x0 + 0.5, xw - (p.attempts > p.wins ? 2 : 0)), cy, t, p.attempts === p.wins)}"/>`;
      if (p.attempts > p.wins) s += `<path style="fill:var(--down)" d="${barPath(xw, xa, cy, t)}"/>`;
      s += `<text class="viz-label" x="${W - m.r + 10}" y="${cy + 4}">${p.wins}/${p.attempts}${p.attempts ? ` <tspan class="viz-sub">${pct(p.wins / p.attempts)}</tspan>` : ''}</text></g>`;
    });
    el.innerHTML = s + '</svg>';
  }

  const MULTI = [['k3', '3K', '--aim-3'], ['k4', '4K', '--aim-2'], ['k5', 'Ace', '--aim-1']];
  const multiRows = () => data.rounds.players.slice().sort((a, b) => (b.k5 - a.k5) || (b.k4 - a.k4) || (b.k3 - a.k3));

  function multiKillCard() {
    const r = data.rounds;
    if (!r || !r.games) return card('multi', 'Multi-kills', '', '<p class="muted">No round data yet. It fills in as games sync.</p>');
    const rows = multiRows();
    const take = rows[0].k5 ? `Most aces: <strong>${h.esc(rows[0].nickname)}</strong> (${rows[0].k5})` : 'No aces yet.';
    return card('multi', 'Multi-kills', take, legend(MULTI.map(([, label, color]) => ({ label, color }))) + slot('multi', 16 + rows.length * 38) +
      '<p class="muted small">Rounds where a player got 3, 4 or all 5 kills.</p>' + roundsNote(),
      table(['Player', '3K', '4K', 'Aces', 'Per 100 rounds'], rows.map((p) => [p.nickname, String(p.k3), String(p.k4), String(p.k5),
        p.rounds ? ((p.k3 + p.k4 + p.k5) / p.rounds * 100).toFixed(1) : '–'])));
  }

  function drawMulti(el, W) {
    const rows = multiRows();
    const m = { l: 110, r: 60, t: 8, b: 8 }, rh = 38, t = 18, pw = W - m.l - m.r;
    const maxT = Math.max(1, ...rows.map((p) => p.k3 + p.k4 + p.k5));
    let s = `<svg class="viz-svg" width="${W}" height="${m.t + rows.length * rh + m.b}" role="img" aria-label="3K, 4K and ace rounds per player">`;
    rows.forEach((p, i) => {
      const cy = m.t + i * rh + rh / 2, total = p.k3 + p.k4 + p.k5;
      s += `<g class="viz-row"${tip(p.nickname, MULTI.map(([k, label, color]) => [label, String(p[k]), color]))}>` +
        `<rect class="viz-rowhit" x="0" y="${cy - rh / 2}" width="${W}" height="${rh}"/>` +
        `<circle cx="10" cy="${cy}" r="5" style="fill:var(--s${memberSlot(p.puuid)})"/><text class="viz-label" x="22" y="${cy + 4}">${h.esc(p.nickname)}</text>`;
      let x = m.l;
      const segs = MULTI.filter(([k]) => p[k]);
      segs.forEach(([k, , color], j) => {
        const w = (p[k] / maxT) * pw, last = j === segs.length - 1;
        s += `<path style="fill:var(${color})" d="${barPath(x, x + Math.max(0.5, w - (last ? 0 : 2)), cy, t, last)}"/>`;
        x += w;
      });
      s += `<text class="viz-label" x="${W - m.r + 10}" y="${cy + 4}">${total}</text></g>`;
    });
    el.innerHTML = s + '</svg>';
  }

  // ---- spike sites -----------------------------------------------------------------------------
  let spikeSide = 'att'; // the spike sites card's side: 'att' (after we plant) or 'def' (after they plant)

  function spikeCard() {
    const r = data.rounds;
    if (!r || !r.games) return '';
    const maps = r.spikes.filter((m) => Object.keys(m.sites).length);
    if (!maps.length) return '';
    const sites = [...new Set(maps.flatMap((m) => Object.keys(m.sites)))].sort();
    const cands = [];
    maps.forEach((m) => Object.entries(m.sites).forEach(([site, v]) => {
      if (v.att_plants >= 5) cands.push({ kind: 'att', map: m.map, site, rate: v.att_wins / v.att_plants, n: v.att_plants });
      if (v.def_plants >= 5) cands.push({ kind: 'def', map: m.map, site, rate: v.def_wins / v.def_plants, n: v.def_plants });
    }));
    const bestAtt = cands.filter((c) => c.kind === 'att').sort((a, b) => b.rate - a.rate)[0];
    const worstDef = cands.filter((c) => c.kind === 'def').sort((a, b) => a.rate - b.rate)[0];
    const take = [bestAtt ? `Best plant: <strong>${h.esc(bestAtt.map)} ${h.esc(bestAtt.site)}</strong> (${pct(bestAtt.rate)} won after planting, ${bestAtt.n} plants)` : '',
      worstDef ? `weakest retake: <strong>${h.esc(worstDef.map)} ${h.esc(worstDef.site)}</strong> (${pct(worstDef.rate)} won)` : ''].filter(Boolean).join(' · ') || 'Needs 5+ plants on a site to call it.';
    // One side at a time (spikeSide, flipped in place by the toggle): a row per map with a tile per site it has,
    // so a map without a C site just has no C tile. Attack rows also show how often we plant.
    const pane = (kind) => {
      const plantsKey = kind === 'att' ? 'att_plants' : 'def_plants', winsKey = kind === 'att' ? 'att_wins' : 'def_wins';
      const rowsHtml = maps.map((m) => {
        const tiles = Object.keys(m.sites).sort().filter((x) => m.sites[x][plantsKey]).map((x) => {
          const v = m.sites[x], n = v[plantsKey], rate = v[winsKey] / n;
          const f = divFill((rate - 0.5) * 2 * (n / (n + 3)));
          return `<div class="spike-site" style="background:${f.bg};color:${f.ink}"${tip(`${m.map} ${x} · ${kind === 'att' ? 'our plants' : 'enemy plants'}`,
            [[kind === 'att' ? 'won after planting' : 'won (retake or hold)', pct(rate), null], ['record', `${v[winsKey]}–${n - v[winsKey]}`, null]])}>` +
            `<span class="spike-letter">${h.esc(x)}</span>${pct(rate)}<div class="viz-cell-sub">${n} plant${n === 1 ? '' : 's'}</div></div>`;
        }).join('');
        const plant = kind === 'att' ? `<div class="spike-plant"${tip(`${m.map}: attack rounds with a plant`, [['plants', `${m.attack_plants} of ${m.attack_rounds}`, null]])}>` +
          `${m.attack_rounds ? pct(m.attack_plants / m.attack_rounds) : '–'}<div class="viz-cell-sub">planted</div></div>` : '';
        return `<div class="spike-row"><div class="spike-map">${h.esc(m.map)} <span class="muted small">${plural(m.games, 'game')}</span></div>${plant}` +
          `<div class="spike-sites">${tiles || '<span class="muted small">no plants yet</span>'}</div></div>`;
      }).join('');
      return `<div class="spike-pane ${kind}" data-spike-pane="${kind}"${spikeSide === kind ? '' : ' hidden'}>${rowsHtml}</div>`;
    };
    const toggle = `<div class="seg spike-toggle" role="tablist">${[['att', 'Attack: our plants'], ['def', 'Defence: their plants']].map(([k, l]) =>
      `<button type="button" class="seg-btn spike-side ${spikeSide === k ? 'on' : ''}" data-side="${k}" role="tab" aria-selected="${spikeSide === k}">${l}</button>`).join('')}</div>`;
    const rows = [];
    maps.forEach((m) => Object.entries(m.sites).forEach(([x, v]) => rows.push([`${m.map} ${x}`, `${v.att_wins}–${v.att_plants - v.att_wins}`, `${v.def_wins}–${v.def_plants - v.def_wins}`])));
    return card('spikes', 'Spike sites', take,
      toggle + pane('att') + pane('def') +
      '<div class="viz-scale"><span>Lose more</span><span class="viz-scale-bar"></span><span>Win more</span></div>' +
      '<p class="muted small">Round win rate after the spike goes down, per site; sites with few plants are paler. "Planted" is the share of our attack rounds with a plant (regulation only).</p>' + roundsNote(),
      table(['Map · site', 'After our plant (W–L)', 'After their plant (W–L)'], rows));
  }

  // ---- betting report card -----------------------------------------------------------------------
  function bettingCard() {
    const b = data.betting;
    if (!b || !b.settled) return '';
    const cats = b.categories.filter((c) => b.bettors.some((n) => b.by_type[n][c.key]));
    const selfBy = new Map(b.self.map((x) => [x.bettor, x]));
    const cell = (rec, label, cls = '') => {
      if (!rec || !rec.bets) return `<td class="viz-empty ${cls}"></td>`;
      const f = divFill(rec.roi == null ? 0 : clamp(rec.roi / 0.5, -1, 1) * (rec.bets / (rec.bets + 3)), true);
      return `<td class="${cls}" style="background:${f.bg};color:${f.ink}"${tip(label, [['ROI', rec.roi == null ? '–' : signedPct(rec.roi), null], ['record', `${rec.won}–${rec.lost}`, null],
        ['wagered', n0(rec.staked), null], ['net', `${rec.net >= 0 ? '+' : '−'}${n0(Math.abs(rec.net))}`, null]])}>` +
        `${rec.roi == null ? '–' : signedPct(rec.roi)}<div class="viz-cell-sub">${rec.won}–${rec.lost}</div></td>`;
    };
    let grid = `<div class="table-wrap"><table class="viz-heat"><thead><tr><th></th>${cats.map((c) => `<th>${h.esc(c.label)}</th>`).join('')}` +
      '<th class="viz-split">On yourself</th><th>On others</th></tr></thead><tbody>';
    b.bettors.forEach((n) => {
      const sb = selfBy.get(n);
      grid += `<tr><th scope="row"><span class="swatch s${h.bettorSlot(n)}"></span>${h.esc(n)}</th>` +
        cats.map((c) => cell(b.by_type[n][c.key], `${n} · ${c.label}`)).join('') +
        cell(sb && sb.own, `${n} betting on themselves`, 'viz-split') + cell(sb && sb.others, `${n} betting on others`) + '</tr>';
    });
    grid += '</tbody></table></div>';
    const catLabel = new Map(b.categories.map((c) => [c.key, c.label]));
    let best = null;
    b.bettors.forEach((n) => Object.entries(b.by_type[n]).forEach(([k, rec]) => {
      if (rec.bets >= 5 && rec.roi != null && (!best || rec.roi > best.rec.roi)) best = { n, k, rec };
    }));
    const selves = b.self.filter((x) => x.own.bets >= 3 && x.others.bets >= 3);
    const loyal = selves.sort((x, y) => (y.own.roi - y.others.roi) - (x.own.roi - x.others.roi))[0];
    const take = [best ? `Sharpest: <strong>${h.esc(best.n)} on ${h.esc(catLabel.get(best.k))}</strong> (${signedPct(best.rec.roi)} ROI over ${plural(best.rec.bets, 'bet')})` : '',
      loyal ? `<strong>${h.esc(loyal.bettor)}</strong> does best betting on themselves (${signedPct(loyal.own.roi)} vs ${signedPct(loyal.others.roi)} on others)` : ''].filter(Boolean).join(' · ') || 'Needs a few settled bets per market.';
    const rows = [];
    b.bettors.forEach((n) => Object.entries(b.by_type[n]).forEach(([k, rec]) => rows.push([`${n} · ${catLabel.get(k)}`, String(rec.bets), `${rec.won}–${rec.lost}`, n0(rec.staked), rec.roi == null ? '–' : signedPct(rec.roi)])));
    return card('betting', 'Betting report card', take,
      grid + '<div class="viz-scale"><span>Losing money</span><span class="viz-scale-bar money"></span><span>Making money</span></div>' +
      how('Return on investment by market type; the small numbers are won–lost.', '"On yourself" counts your player props and scoreboard picks on your own player. Cells with few bets are paler.'),
      table(['Bettor · market', 'Bets', 'W–L', 'Wagered', 'ROI'], rows));
  }

  // ---- odds accuracy: did picks win as often as the odds said? ----------------------------------------
  const VERDICT = { about_right: 'About right', generous: 'Too generous', stingy: 'Too stingy' };
  const FEW_IN_BIN = 5; // bins with fewer picks are drawn paler
  const oneDp = (v) => (Math.round(v * 10) / 10).toLocaleString();

  function accuracyCard() {
    const a = data.betting && data.betting.accuracy;
    if (!a || !a.picks) return '';
    let take;
    if (!a.verdict) {
      take = `Needs at least ${a.min_picks} settled picks to judge (${a.picks} so far).`;
    } else {
      const verdict = { about_right: 'in line with the odds', generous: 'more than the odds said: they were too generous',
        stingy: 'less than the odds said: they were too stingy' }[a.verdict];
      const off = a.by_type.filter((t) => t.verdict && t.verdict !== 'about_right')
        .map((t) => `${h.esc(t.label)} ${VERDICT[t.verdict].toLowerCase()}`);
      take = `Picks won <strong>${a.won} of ${a.picks}</strong> against ${oneDp(a.expected)} expected, <strong>${verdict}</strong>` +
        (off.length ? ` · ${off.join(' · ')}` : '');
    }
    const byType = `<div class="table-wrap"><table class="compact"><thead><tr><th>Market</th><th class="num">Picks</th>` +
      '<th class="num">Won</th><th class="num">Odds expected</th><th>Verdict</th></tr></thead><tbody>' +
      a.by_type.map((t) => `<tr><td>${h.esc(t.label)}</td><td class="num">${t.picks}</td><td class="num">${t.won}</td>` +
        `<td class="num">${oneDp(t.expected)}</td><td>${t.verdict ? VERDICT[t.verdict] : `<span class="muted">Too few picks</span>`}</td></tr>`).join('') +
      '</tbody></table></div>';
    const rows = a.bins.filter((b) => b.picks).map((b) => [`${pct(b.lo)}–${pct(b.hi)}`, String(b.picks), pct(b.chance), `${b.won} (${pct(b.win_rate)})`,
      `${pct(b.range[0])}–${pct(b.range[1])}`]);
    return card('accuracy', 'Are the odds right?', take,
      `<div class="viz-cols viz-accuracy"><div>${slot('accuracy', 250)}</div><div>${byType}</div></div>` +
      how('Dots on the diagonal mean the odds were right.', 'Each dot groups picks by the chance the odds gave them (before the house edge) and shows how often they actually won; ' +
        'the bar is the likely range for that many picks. Singles and parlay legs both count, ' +
        'a pick several people bet in the same game counts once, and voids are left out. "Too generous" means those picks won more often than their price allowed.'),
      table(['Odds gave', 'Picks', 'Average chance', 'Won', 'Likely range'], rows));
  }

  function drawAccuracy(el, W) {
    const a = data.betting.accuracy;
    const m = { l: 44, r: 16, t: 12, b: 34 };
    const H = clamp(W - m.l - m.r, 180, 300), size = H; // square, so "the odds were right" is the 45° diagonal
    const x = (v) => m.l + v * size, y = (v) => m.t + (1 - v) * H;
    let s = `<svg class="viz-svg" width="${m.l + size + m.r}" height="${m.t + H + m.b}" role="img" aria-label="How often picks won against the chance the odds gave them">`;
    [0, 0.25, 0.5, 0.75, 1].forEach((v) => {
      s += `<line class="${v ? 'viz-grid' : 'viz-base'}" x1="${m.l}" x2="${x(1)}" y1="${y(v)}" y2="${y(v)}"/>` +
        `<text class="viz-ax" x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${pct(v)}</text>` +
        `<text class="viz-ax" x="${x(v)}" y="${m.t + H + 16}" text-anchor="middle">${pct(v)}</text>`;
    });
    s += `<text class="viz-ax" x="${x(0.5)}" y="${m.t + H + 30}" text-anchor="middle">Chance the odds gave</text>` +
      `<line class="viz-ref" x1="${x(0)}" y1="${y(0)}" x2="${x(1)}" y2="${y(1)}" stroke-dasharray="4 4"/>` +
      `<text class="viz-sub" x="${x(0.03)}" y="${y(0.93)}">Won more often than the odds said</text>` +
      `<text class="viz-sub" x="${x(0.97)}" y="${y(0.04)}" text-anchor="end">Won less often</text>`;
    const shown = a.bins.filter((b) => b.picks);
    const dots = shown.map((b) => ({ x: x(b.chance), y: y(b.win_rate) }));
    const boxes = []; // placed labels; a label that would overlap a dot or another label is left to the tooltip
    const hits = (bx) => boxes.concat(dots.map((d) => ({ x0: d.x - 7, x1: d.x + 7, y0: d.y - 7, y1: d.y + 7 })))
      .some((o) => bx.x0 < o.x1 && o.x0 < bx.x1 && bx.y0 < o.y1 && o.y0 < bx.y1);
    shown.forEach((b, i) => {
      const cx = dots[i].x, cy = dots[i].y, faint = b.picks < FEW_IN_BIN ? ' style="opacity:.45"' : '';
      const text = `${b.won}/${b.picks}`, tw = text.length * 7;
      const place = [{ x0: cx + 10, anchor: 'start' }, { x0: cx - 10 - tw, anchor: 'end' }]
        .map((p) => ({ ...p, x0: p.x0, x1: p.x0 + tw, y0: cy - 8, y1: cy + 6 })).find((bx) => !hits(bx));
      if (place) boxes.push(place);
      s += `<g class="viz-mark"${faint}${tip(`Odds gave ${pct(b.lo)}–${pct(b.hi)}`, [
        ['average chance', pct(b.chance), null], ['actually won', `${pct(b.win_rate)} (${b.won} of ${b.picks})`, '--accent'],
        ['likely range', `${pct(b.range[0])}–${pct(b.range[1])}`, null]])}>` +
        `<line class="viz-connector" style="stroke:var(--accent);opacity:.5" x1="${cx}" x2="${cx}" y1="${y(b.range[0])}" y2="${y(b.range[1])}"/>` +
        `<circle cx="${cx}" cy="${cy}" r="14" fill="transparent"/>` +
        `<circle class="viz-dot" cx="${cx}" cy="${cy}" r="6" style="fill:var(--accent)"/>` +
        (place ? `<text class="viz-label" x="${place.anchor === 'start' ? cx + 10 : cx - 10}" y="${cy + 4}" text-anchor="${place.anchor}">${text}</text>` : '') + '</g>';
    });
    el.innerHTML = s + '</svg>';
  }

  // ---- Forecasts tab: each game against what the odds engine predicted beforehand (/api/forecasts) ------------
  const STRIP_MAX = 60; // the game strip shows at most this many recent games
  let fc = null; // { d: /api/forecasts response, pick: { stat, player, cell } }
  const fcStat = () => fc.d.stats.find((s) => s.key === fc.d.stat);
  const FC_GROUPS = [['game', 'Per game · the betting lines'], ['round', 'Per round'], ['score', 'Scores']];
  const fcVal = (v) => (v == null ? '–' : fc.d.stat === 'hs_pct' ? `${oneDp(v)}%` : ['acs', 'adr'].includes(fc.d.stat) ? n0(v)
    : fcStat().group === 'round' ? v.toFixed(2) : oneDp(v));
  const fcSigned = (v) => (v > 0 ? '+' : v < 0 ? '−' : '±') + fcVal(Math.abs(v));
  const fcSign = () => (fcStat().lower_is_better ? -1 : 1); // +1: higher is better
  const fcCellKey = (map, role) => `${map || ''}|${role || ''}`;
  const fcWhere = (c) => [c.map, c.role].filter(Boolean).join(' as ') || 'overall';
  const fcPlace = (g) => (g.actual > g.range[1] ? 'above' : g.actual < g.range[0] ? 'below' : 'inside');
  const fcBetter = (g) => fcPlace(g) !== 'inside' && (fcPlace(g) === 'above') === (fcSign() > 0);

  function forecasts(d, helpers, pick) {
    h = helpers;
    fc = { d, pick };
    const p = d.player;
    withYear = (p?.games || []).some((g) => new Date(g.ts * 1000).getFullYear() !== new Date().getFullYear());
    const label = fcStat().label;
    const seg = (cls, items) => `<div class="seg" role="tablist">${items.map((it) =>
      `<button type="button" class="seg-btn ${cls} ${it.on ? 'on' : ''}" data-v="${h.esc(it.v)}" role="tab" aria-selected="${it.on}"${it.off ? ` disabled title="${h.esc(it.off)}"` : ''}>` +
      `${it.swatch ? `<span class="swatch s${it.swatch}"></span>` : ''}${h.esc(it.label)}</button>`).join('')}</div>`;
    const pickers = `<section class="card fc-pickers">` +
      seg('fc-player', d.players.map((x) => ({ v: x.puuid, label: x.nickname, swatch: h.slot(x.puuid) || 1, on: p && x.puuid === p.puuid,
        off: x.forecast_games ? '' : `Needs more than ${d.min_prior} games (has ${x.total_games})` }))) +
      FC_GROUPS.map(([g, caption]) => `<div class="fc-group"><span class="fc-caption">${h.esc(caption)}</span>` +
        seg('fc-stat', d.stats.filter((s) => s.group === g).map((s) => ({ v: s.key, label: s.short, on: s.key === d.stat }))) + '</div>').join('') +
      '</section>';
    const intro = how('Each game\'s forecast, made beforehand from earlier games only, next to what really happened.',
      `Before every game, the odds engine would have predicted a range for each player from their earlier 5-stack games only, ` +
      `weighting recent games and games on the same map and agent more, exactly like the player-prop lines. ` +
      `About ${pct(d.coverage)} of games should land inside the range, which is lopsided when the stat is (a few big games stretch the top); ` +
      `the tick marks the typical game, where a betting line would sit.`);
    if (!p || !p.overall) {
      return intro + pickers + `<section class="card"><h2>No forecasts yet</h2><p class="muted">A game gets a forecast once the player has ${d.min_prior} earlier 5-stack games` +
        ` (surrenders aren't forecast).</p></section>`;
    }
    const o = p.overall, sign = fcSign();
    const better = sign > 0 ? o.above : o.below, worse = sign > 0 ? o.below : o.above;
    const tile = (lbl, value, sub) => `<div class="tile"><div class="tile-label">${h.esc(lbl)}</div><div class="tile-value">${value}</div><div class="tile-sub">${sub}</div></div>`;
    const kpis = `<section class="kpis">
      ${tile('Inside the forecast range', pct(o.inside / o.games), `${o.inside} of ${plural(o.games, 'game')} · aim is ${pct(d.coverage)}`)}
      ${tile('Better than forecast', String(better), `${pct(better / o.games)} of games`)}
      ${tile('Worse than forecast', String(worse), `${pct(worse / o.games)} of games`)}
      ${tile(`Average ${label} vs forecast`, fcSigned(o.diff), `${fcVal(o.actual)} actual vs ${fcVal(o.expected)} expected`)}
    </section>`;
    return intro + pickers + kpis + fcStripCard(p) + fcGridCard(p);
  }

  function fcGridCard(p) {
    const d = fc.d, sign = fcSign(), label = fcStat().label;
    const byKey = new Map(p.cells.map((c) => [fcCellKey(c.map, c.role), c]));
    const roles = d.roles.filter((r) => p.cells.some((c) => c.role === r));
    const maps = p.cells.filter((c) => c.map && !c.role).sort((a, b) => b.games - a.games || a.map.localeCompare(b.map)).map((c) => c.map);
    const cell = (c, key, cls = '') => {
      if (!c) return `<td class="viz-empty ${cls}"></td>`;
      const half = Math.max(1e-9, (c.high - c.low) / 2);
      const f = divFill(clamp((sign * c.diff) / half, -1, 1) * (c.games / (c.games + 2)));
      const better = sign > 0 ? c.above : c.below, worse = sign > 0 ? c.below : c.above;
      const rows = [['actual average', fcVal(c.actual), null], ['expected', fcVal(c.expected), null],
        ['typical game', fcVal(c.typical), null], ['forecast range', `${fcVal(c.low)}–${fcVal(c.high)}`, '--accent'],
        ['inside the range', `${c.inside} of ${c.games}`, null], ['better / worse', `${better} / ${worse}`, null]]
        .concat(c.agents.length ? ['By agent: actual vs expected'] : [])
        .concat(c.agents.map((a) => [`${a.agent} · ${plural(a.games, 'game')}`, `${fcVal(a.actual)} vs ${fcVal(a.expected)}`, null]));
      const sel = fc.pick.cell === key ? ' fc-sel' : '';
      return `<td class="fc-cell ${cls}${sel}" data-cell="${h.esc(key)}" style="background:${f.bg};color:${f.ink}"${tip(`${fcWhere(c)} · ${plural(c.games, 'game')}`, rows)}>` +
        `${fcVal(c.actual)}<div class="viz-cell-sub">vs ${fcVal(c.expected)} · ${c.games}g</div></td>`;
    };
    const totals = roles.length > 1; // an "All roles" column only adds something when they played more than one role
    let grid = `<div class="table-wrap"><table class="viz-heat"><thead><tr><th></th>${roles.map((r) => `<th>${h.esc(r)}</th>`).join('')}` +
      `${totals ? '<th class="viz-split">All roles</th>' : ''}</tr></thead><tbody>`;
    maps.forEach((m) => {
      grid += `<tr><th scope="row">${h.esc(m)}</th>${roles.map((r) => cell(byKey.get(fcCellKey(m, r)), fcCellKey(m, r))).join('')}` +
        `${totals ? cell(byKey.get(fcCellKey(m, null)), fcCellKey(m, null), 'viz-split') : ''}</tr>`;
    });
    grid += `<tr><th scope="row">All maps</th>${roles.map((r) => cell(byKey.get(fcCellKey(null, r)), fcCellKey(null, r))).join('')}` +
      `${totals ? `<td class="viz-split muted small">${plural(p.overall.games, 'game')}</td>` : ''}</tr></tbody></table></div>`;
    const takeParts = [];
    if (p.best) takeParts.push(`beats the forecast most on <strong>${h.esc(fcWhere(p.best))}</strong> (${fcSigned(p.best.diff)} ${h.esc(label)} over ${plural(p.best.games, 'game')})`);
    if (p.worst) takeParts.push(`falls short most on <strong>${h.esc(fcWhere(p.worst))}</strong> (${fcSigned(p.worst.diff)} over ${plural(p.worst.games, 'game')})`);
    const take = takeParts.length ? `${h.esc(p.nickname)} ${takeParts.join(' · ')}` : `Needs ${d.min_cell}+ games on a map and role before calling out where ${h.esc(p.nickname)} beats or misses the forecast.`;
    const rows = p.cells.map((c) => [`${c.map || 'All maps'} · ${c.role || 'All roles'}`, String(c.games), `${fcVal(c.low)}–${fcVal(c.high)}`, fcVal(c.expected), fcVal(c.actual), `${c.above}/${c.inside}/${c.below}`]);
    return card('forecast-grid', `${label}: forecast vs actual by map and role`, take,
      grid + `<div class="viz-scale"><span>Worse than forecast</span><span class="viz-scale-bar"></span><span>Better than forecast</span></div>` +
      how('Click a cell to show just those games in the chart above.', `Each cell shows the actual average${fcStat().lower_is_better ? ' (fewer is better)' : ''}, the expected average and the number of games. ` +
        'Hover for the range and each agent. Cells with few games are paler.'),
      table(['Map · role', 'Games', 'Forecast range', 'Expected', 'Actual', 'Above/inside/below'], rows));
  }

  function fcFiltered(p) {
    const [map, role] = (fc.pick.cell || '|').split('|');
    return p.games.filter((g) => (!map || g.map === map) && (!role || g.role === role));
  }

  function fcStripCard(p) {
    const games = fcFiltered(p);
    const [map, role] = (fc.pick.cell || '|').split('|');
    const where = fc.pick.cell ? fcWhere({ map, role }) : '';
    const shown = Math.min(games.length, STRIP_MAX);
    const head = where ? `Showing ${plural(games.length, 'game')} ${map ? 'on ' : 'as '}<strong>${h.esc(where)}</strong> <button type="button" class="btn ghost small" id="fc-all">Show all games</button>`
      : `Every forecast game, oldest to newest${games.length > STRIP_MAX ? ` (the last ${STRIP_MAX} of ${games.length})` : ''}.`;
    const rows = games.slice(-shown).map((g) => [`${dateShort(g.ts)} · ${g.map}`, g.agent || '?', `${fcVal(g.range[0])}–${fcVal(g.range[1])}`, fcVal(g.typical), fcVal(g.actual), fcPlace(g)]);
    return card('forecast-strip', 'Game by game', head,
      legend([{ label: 'Forecast range', color: '--accent', kind: 'range' }, { label: 'Typical game', color: '--accent', kind: 'line' },
        { label: 'Better than forecast', color: '--div-pos', kind: 'dot' },
        { label: 'Inside the range', color: '--text-2', kind: 'dot' }, { label: 'Worse than forecast', color: '--div-neg', kind: 'dot' }]) +
      (games.length ? slot('forecast', 240) : '<p class="muted">No games here.</p>'),
      table(['Game', 'Agent', 'Forecast range', 'Typical', 'Actual', 'Result'], rows));
  }

  function drawForecast(el, W) {
    const p = fc.d.player, games = fcFiltered(p).slice(-STRIP_MAX);
    if (!games.length) return;
    const vals = games.flatMap((g) => [g.range[0], g.range[1], g.actual]);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const step = niceMax((hi - lo) / 4 || 1);
    lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
    const m = { l: 44, r: 12, t: 10, b: 26 }, H = 190, pw = W - m.l - m.r, n = games.length;
    const x = (i) => m.l + ((i + 0.5) / n) * pw;
    const y = (v) => m.t + ((hi - v) / ((hi - lo) || 1)) * H;
    const bw = clamp((pw / n) * 0.55, 3, 12);
    let s = `<svg class="viz-svg" width="${W}" height="${m.t + H + m.b}" role="img" aria-label="Forecast range and actual value per game">`;
    for (let v = lo; v <= hi + 1e-9; v += step) {
      s += `<line class="${v === lo ? 'viz-base' : 'viz-grid'}" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/>` +
        `<text class="viz-ax" x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${fcVal(v)}</text>`;
    }
    games.forEach((g, i) => {
      const cx = x(i), place = fcPlace(g);
      const color = place === 'inside' ? '--text-2' : fcBetter(g) ? '--div-pos' : '--div-neg';
      s += `<rect x="${cx - bw / 2}" y="${y(g.range[1])}" width="${bw}" height="${Math.max(1, y(g.range[0]) - y(g.range[1]))}" rx="${Math.min(4, bw / 2)}" style="fill:var(--accent);opacity:.22"/>` +
        `<line x1="${cx - bw / 2}" x2="${cx + bw / 2}" y1="${y(g.typical)}" y2="${y(g.typical)}" style="stroke:var(--accent);stroke-width:2"/>` +
        `<circle class="viz-dot" cx="${cx}" cy="${y(g.actual)}" r="4.5" style="fill:var(${color})"/>`;
    });
    s += `<text class="viz-ax" x="${x(0)}" y="${m.t + H + 18}">${h.esc(dateShort(games[0].ts))}</text>` +
      (n > 1 ? `<text class="viz-ax" x="${x(n - 1)}" y="${m.t + H + 18}" text-anchor="end">${h.esc(dateShort(games[n - 1].ts))}</text>` : '') +
      `<line class="viz-cross" x1="0" x2="0" y1="${m.t}" y2="${m.t + H}" visibility="hidden"/>` +
      `<rect class="viz-hit" data-cross="forecast" x="${m.l}" y="0" width="${pw}" height="${m.t + H}" tabindex="0" aria-label="Forecast and actual per game; use the arrow keys"/></svg>`;
    el.innerHTML = s;
    const label = fcStat().label;
    cross.forecast = {
      xs: games.map((_, i) => x(i)),
      tips: games.map((g) => ({
        t: `${dateLong(g.ts)} · ${g.map}`,
        r: [[label, fcVal(g.actual), fcPlace(g) === 'inside' ? '--text-2' : fcBetter(g) ? '--div-pos' : '--div-neg'],
          fcPlace(g) === 'inside' ? 'Inside the forecast range' : fcBetter(g) ? 'Better than forecast' : 'Worse than forecast',
          ['forecast range', `${fcVal(g.range[0])}–${fcVal(g.range[1])}`, '--accent'], ['typical game', fcVal(g.typical), null],
          ['expected', fcVal(g.expected), null], [g.role, g.agent || '?', null], ['result', resultText(g), null]],
      })),
    };
  }

  // ---- Players tab: everyone's trend in one stat, game by game (data: /api/stats "timeline") ----------------------
  const TREND_STATS = [['acs', 'ACS'], ['kd', 'K/D'], ['kills', 'Kills'], ['deaths', 'Deaths'], ['assists', 'Assists'], ['adr', 'ADR'], ['hs_pct', 'HS %']];
  const TREND_RANGES = [['all', 'Every game'], ['10', 'Last 10 games']];
  let pt = null; // { tl, members, pick: { stat, range, hidden } }
  const ptVal = (v, stat) => (v == null ? '–' : stat === 'hs_pct' ? `${oneDp(v)}%` : stat === 'kd' ? v.toFixed(2)
    : ['acs', 'adr'].includes(stat) ? n0(v) : oneDp(v));

  // The games shown (every complete game, or the last 10) and each player's values lined up with them.
  function trendWindow(tl, pick) {
    const from = pick.range === 'all' ? 0 : Math.max(0, tl.games.length - Number(pick.range));
    return { games: tl.games.slice(from), vals: (puuid) => tl.series[puuid][pick.stat].slice(from), from };
  }

  // A player's average over the shown games (K/D pools their kills and deaths, like everywhere else).
  function trendAverage(series, stat, from) {
    if (stat === 'kd') {
      const k = series.kills.slice(from).reduce((a, x) => a + (x || 0), 0), d = series.deaths.slice(from).reduce((a, x) => a + (x || 0), 0);
      return k / Math.max(1, d);
    }
    const xs = series[stat].slice(from).filter((x) => x != null);
    return xs.length ? xs.reduce((a, x) => a + x, 0) / xs.length : null;
  }

  function playerTrends(tl, members, helpers, pick) {
    h = helpers;
    pt = { tl, members, pick };
    withYear = tl.games.some((g) => new Date(g.ts * 1000).getFullYear() !== new Date().getFullYear());
    if (tl.games.length < 2) return card('trends', 'Trends', '', '<p class="muted">Trends appear after a couple of complete 5-stack games.</p>');
    const seg = (cls, items) => `<div class="seg" role="tablist">${items.map(([v, label]) =>
      `<button type="button" class="seg-btn ${cls} ${String(v) === String(cls === 'tr-stat' ? pick.stat : pick.range) ? 'on' : ''}" data-v="${h.esc(v)}">${h.esc(label)}</button>`).join('')}</div>`;
    const players = members.map((m) => {
      const off = !!pick.hidden[m.puuid];
      return `<button type="button" class="tr-player ${off ? 'off' : ''}${m.puuid === pick.focus ? ' focus' : ''}" data-puuid="${h.esc(m.puuid)}" aria-pressed="${!off}" title="${off ? 'Show' : 'Hide'} ${h.esc(m.nickname)}">` +
        `<span class="viz-key line" style="background:var(--s${h.slot(m.puuid) || 1})"></span>${h.esc(m.nickname)}</button>`;
    }).join('');
    const label = TREND_STATS.find(([k]) => k === pick.stat)[1];
    const lower = pick.stat === 'deaths';
    const { games, from } = trendWindow(tl, pick);
    const span = pick.range === 'all' ? `all ${games.length} games` : `the last ${games.length} games`;
    const ranked = members.filter((m) => !pick.hidden[m.puuid] && tl.series[m.puuid])
      .map((m) => ({ m, avg: trendAverage(tl.series[m.puuid], pick.stat, from), last: tl.series[m.puuid][pick.stat].slice(-1)[0] }))
      .filter((x) => x.avg != null).sort((a, b) => (lower ? a.avg - b.avg : b.avg - a.avg));
    const take = ranked.length ? `${lower ? 'Fewest deaths' : `Best ${h.esc(label)}`} over ${span}: <strong>${h.esc(ranked[0].m.nickname)}</strong> (${ptVal(ranked[0].avg, pick.stat)} a game)` : '';
    const rows = ranked.map(({ m, avg, last }) => [m.nickname, ptVal(avg, pick.stat), ptVal(last, pick.stat)]);
    return card('trends', `Trends: ${label}`, take,
      `<div class="tr-controls">${seg('tr-stat', TREND_STATS)}${seg('tr-range', TREND_RANGES)}</div><div class="tr-legend">${players}</div>` + slot('trends', 290) +
      `<p class="muted small">${pick.range === 'all' ? 'Every complete 5-stack game' : `The last ${games.length} complete 5-stack games`}, oldest to newest; each point is one game. The player picked above stands out; click a name to hide or show that player, and hover for everyone's value at a game.</p>`,
      table(['Player', `Average over ${span}`, 'Latest game'], rows));
  }

  function drawTrends(el, W) {
    const { tl, members, pick } = pt;
    const stat = pick.stat, win = trendWindow(tl, pick), games = win.games, G = games.length;
    // The player picked in the detail above (pick.focus) is drawn bold and last, on top; the rest are faint.
    const lines = members.filter((m) => !pick.hidden[m.puuid] && tl.series[m.puuid])
      .map((m) => ({ m, color: `--s${h.slot(m.puuid) || 1}`, vals: win.vals(m.puuid), cls: m.puuid === pick.focus ? 'focus' : 'dim' }))
      .sort((a, b) => (a.cls === 'focus') - (b.cls === 'focus'));
    const all = lines.flatMap((l) => l.vals).filter((v) => v != null);
    if (!all.length) { el.innerHTML = '<p class="muted">Pick at least one player.</p>'; return; }
    let lo = Math.min(...all), hi = Math.max(...all);
    const step = niceMax((hi - lo) / 4 || 1);
    lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
    const m = { l: 48, r: 14, t: 10, b: 26 }, H = 250, pw = W - m.l - m.r;
    const x = (i) => m.l + (G > 1 ? (i / (G - 1)) * pw : pw / 2);
    const y = (v) => m.t + ((hi - v) / ((hi - lo) || 1)) * H;
    let s = `<svg class="viz-svg" width="${W}" height="${m.t + H + m.b}" role="img" aria-label="${h.esc(stat)} per game for each player">`;
    for (let v = lo; v <= hi + 1e-9; v += step) {
      s += `<line class="${v === lo ? 'viz-base' : 'viz-grid'}" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/>` +
        `<text class="viz-ax" x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${ptVal(v, stat)}</text>`;
    }
    lines.forEach((l) => {
      let d = '', pen = false, last = null;
      l.vals.forEach((v, i) => {
        if (v == null) { pen = false; return; }
        d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
        pen = true; last = i;
      });
      s += `<g class="tr-line ${l.cls}"><path class="viz-line" style="stroke:var(${l.color})" d="${d}"/>`;
      if (G <= 15) { // few enough games to mark each one
        l.vals.forEach((v, i) => { if (v != null && i !== last) s += `<circle class="viz-dot" cx="${x(i)}" cy="${y(v)}" r="3" style="fill:var(${l.color})"/>`; });
      }
      if (last != null) s += `<circle class="viz-dot" cx="${x(last)}" cy="${y(l.vals[last])}" r="4" style="fill:var(${l.color})"/>`;
      s += '</g>';
    });
    s += `<text class="viz-ax" x="${m.l}" y="${m.t + H + 18}">${h.esc(dateShort(games[0].ts))}</text>` +
      `<text class="viz-ax" x="${x(G - 1)}" y="${m.t + H + 18}" text-anchor="end">${h.esc(dateShort(games[G - 1].ts))}</text>` +
      `<line class="viz-cross" x1="0" x2="0" y1="${m.t}" y2="${m.t + H}" visibility="hidden"/>` +
      `<rect class="viz-hit" data-cross="trends" x="${m.l}" y="0" width="${pw}" height="${m.t + H}" tabindex="0" aria-label="Each player's value game by game; use the arrow keys"/></svg>`;
    el.innerHTML = s;
    const lower = stat === 'deaths';
    cross.trends = {
      xs: games.map((_, i) => x(i)),
      tips: games.map((g, i) => ({
        t: `${dateLong(g.ts)} · ${g.map} · ${g.result === 'win' ? 'W' : g.result === 'loss' ? 'L' : 'D'} ${g.score[0]}–${g.score[1]}`,
        r: lines.map((l) => [l.m.nickname, ptVal(l.vals[i], stat), l.color])
          .sort((a, b) => { const va = parseFloat(a[1]), vb = parseFloat(b[1]); return (isNaN(va) ? 1 : isNaN(vb) ? -1 : lower ? va - vb : vb - va); }),
      })),
    };
  }

  // ---- mounting, tooltips, crosshair ----------------------------------------------------------
  const DRAW = { form: drawForm, session: drawSession, swing: drawSwing, aim: drawAim, damage: drawDamage, comps: drawComps, bankroll: drawBankroll, accuracy: drawAccuracy, forecast: drawForecast, trends: drawTrends,
    clutch: drawClutch, multi: drawMulti };

  function drawAll(root) {
    root.querySelectorAll('[data-chart]').forEach((el) => {
      const fn = DRAW[el.dataset.chart];
      if (!fn) return;
      try { fn(el, Math.max(280, el.clientWidth)); } catch (e) {
        console.error(e);
        el.innerHTML = `<p class="muted">Could not draw this chart: ${h.esc(e.message)}</p>`;
      }
    });
  }

  function showTip(spec, x, y) {
    tipEl.replaceChildren();
    const title = document.createElement('div');
    title.className = 'viz-tip-title';
    title.textContent = spec.t;
    tipEl.append(title);
    (spec.r || []).forEach((r) => {
      if (typeof r === 'string') {
        const note = document.createElement('div');
        note.className = 'viz-tip-note';
        note.textContent = r;
        tipEl.append(note);
        return;
      }
      const [label, value, color] = r;
      const row = document.createElement('div');
      row.className = 'viz-tip-row';
      const key = document.createElement('span');
      key.className = 'viz-tip-key';
      if (color && /^--[a-z0-9-]+$/.test(color)) key.style.background = `var(${color})`; else key.classList.add('none');
      const v = document.createElement('strong');
      v.textContent = value;
      const l = document.createElement('span');
      l.textContent = label;
      row.append(key, v, l);
      tipEl.append(row);
    });
    tipEl.hidden = false;
    const r = tipEl.getBoundingClientRect();
    let left = x + 14, top = y + 14;
    if (left + r.width > window.innerWidth - 8) left = x - r.width - 14;
    if (top + r.height > window.innerHeight - 8) top = y - r.height - 14;
    tipEl.style.left = Math.max(8, left) + 'px';
    tipEl.style.top = Math.max(8, top) + 'px';
  }
  const hideTip = () => { if (tipEl) tipEl.hidden = true; };

  function crossAt(hit, i, clientX, clientY) {
    const c = cross[hit.dataset.cross];
    if (!c || !c.xs.length) return;
    i = clamp(i, 0, c.xs.length - 1);
    hit.dataset.idx = i;
    const line = hit.parentNode.querySelector('.viz-cross');
    line.setAttribute('x1', c.xs[i]); line.setAttribute('x2', c.xs[i]); line.setAttribute('visibility', 'visible');
    if (clientX == null) {
      const box = hit.getBoundingClientRect();
      clientX = box.left + (c.xs[i] - Number(hit.getAttribute('x'))); clientY = box.top + 20;
    }
    showTip(c.tips[i], clientX, clientY);
  }
  function nearest(hit, clientX) {
    const c = cross[hit.dataset.cross];
    const px = clientX - hit.ownerSVGElement.getBoundingClientRect().left;
    let best = 0;
    c.xs.forEach((v, i) => { if (Math.abs(v - px) < Math.abs(c.xs[best] - px)) best = i; });
    return best;
  }
  function endCross(hit) {
    hit.parentNode.querySelector('.viz-cross')?.setAttribute('visibility', 'hidden');
    hideTip();
  }

  function mount(root) {
    if (!tipEl) {
      tipEl = document.createElement('div');
      tipEl.className = 'viz-tip';
      tipEl.setAttribute('role', 'tooltip');
      tipEl.hidden = true;
      document.body.append(tipEl);
    }
    drawAll(root);
    lastWidth = root.clientWidth;
    if (!observer) {
      let raf = 0;
      observer = new ResizeObserver(() => {
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(() => {
          if (!root.querySelector('[data-chart]') || root.clientWidth === lastWidth) return;
          lastWidth = root.clientWidth;
          hideTip();
          drawAll(root);
        });
      });
      observer.observe(root);
    }
    if (root.dataset.vizBound) return;
    root.dataset.vizBound = '1';
    // The spike sites card's Attack / Defence toggle flips its panes in place (no redraw).
    root.addEventListener('click', (e) => {
      const b = e.target.closest?.('.spike-side');
      if (!b) return;
      spikeSide = b.dataset.side;
      const c = b.closest('.viz-card');
      c.querySelectorAll('.spike-side').forEach((x) => { x.classList.toggle('on', x === b); x.setAttribute('aria-selected', String(x === b)); });
      c.querySelectorAll('[data-spike-pane]').forEach((p) => { p.hidden = p.dataset.spikePane !== spikeSide; });
    });
    root.addEventListener('pointermove', (e) => {
      const hit = e.target.closest?.('.viz-hit');
      if (hit) return crossAt(hit, nearest(hit, e.clientX), e.clientX, e.clientY);
      const m = e.target.closest?.('[data-tip]');
      if (m) showTip(JSON.parse(m.dataset.tip), e.clientX, e.clientY); else hideTip();
    });
    root.addEventListener('pointerleave', hideTip);
    root.addEventListener('pointerout', (e) => {
      const hit = e.target.closest?.('.viz-hit');
      if (hit && !hit.contains(e.relatedTarget)) endCross(hit);
    });
    root.addEventListener('focusin', (e) => {
      const hit = e.target.closest?.('.viz-hit');
      if (hit) return crossAt(hit, Number(hit.dataset.idx || cross[hit.dataset.cross]?.xs.length - 1 || 0));
      const m = e.target.closest?.('[data-tip]');
      if (!m) return;
      const b = m.getBoundingClientRect();
      showTip(JSON.parse(m.dataset.tip), b.right, b.top);
    });
    root.addEventListener('focusout', (e) => {
      const hit = e.target.closest?.('.viz-hit');
      if (hit) endCross(hit); else hideTip();
    });
    root.addEventListener('keydown', (e) => {
      const hit = e.target.closest?.('.viz-hit');
      if (!hit || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
      e.preventDefault();
      const n = cross[hit.dataset.cross]?.xs.length || 0;
      const cur = Number(hit.dataset.idx || n - 1);
      crossAt(hit, e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : cur + (e.key === 'ArrowLeft' ? -1 : 1));
    });
  }

  // The betting report card lives on the Bettors page: same drawing code, its own data (/api/betting-report).
  // The Bettors tab's betting cards (data from /api/betting-report): the report card and bettor profit over time
  // near the top, and the odds accuracy card at the bottom of the page.
  function bettingReport(report, helpers) {
    h = helpers;
    data = { ...(data || {}), betting: report, bankroll: report.bankroll || [] };
    return bettingCard() + bankrollCard();
  }
  function oddsAccuracy(report, helpers) {
    h = helpers;
    data = { ...(data || {}), betting: report };
    return accuracyCard();
  }

  window.FiveViz = { html, mount, hideTip, bettingReport, oddsAccuracy, forecasts, playerTrends };
})();
