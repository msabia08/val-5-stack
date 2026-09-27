/* 5-Stack Tracker: Visualizations tab. Plain SVG/HTML charts drawn from /api/insights, no dependencies.
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
    { label: 'Night', sub: '9–12', test: (h) => h >= 21 },
    { label: 'After midnight', sub: '12–6', test: (h) => h < 6 },
  ];
  const DAMAGE_GAMES = 20;
  const MAX_COMPS = 8;
  const SLOTS = 8;

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
  const dateShort = (ts) => new Date(ts * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  const dateLong = (ts) => {
    const d = new Date(ts * 1000);
    return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' }) + ' · ' +
      d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  };
  const resultText = (g) => `${g.result === 'win' ? 'W' : g.result === 'loss' ? 'L' : 'D'} ${g.rounds_won}–${g.rounds_lost}`;
  const memberSlot = (puuid) => h.slot(puuid) || 1;
  const nick = (puuid) => (data.members.find((m) => m.puuid === puuid) || {}).nickname || '?';

  // A tooltip spec lives on the mark as JSON: { t: title, r: [[label, value, colorToken|null], ...] }.
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
  function divFill(t) {
    const p = Math.round(clamp(Math.abs(t), 0, 1) * 100);
    const pole = t >= 0 ? 'var(--div-pos)' : 'var(--div-neg)';
    return { bg: `color-mix(in oklab, ${pole} ${p}%, var(--div-mid))`, ink: p > 55 ? '#fff' : 'var(--text)' };
  }

  function table(head, rows) {
    return `<details class="viz-table"><summary>Show as table</summary><div class="table-wrap"><table class="compact"><thead><tr>` +
      head.map((c, i) => `<th${i ? ' class="num"' : ''}>${h.esc(c)}</th>`).join('') + '</tr></thead><tbody>' +
      rows.map((r) => '<tr>' + r.map((c, i) => `<td${i ? ' class="num"' : ''}>${h.esc(c)}</td>`).join('') + '</tr>').join('') +
      '</tbody></table></div></details>';
  }

  const card = (id, title, takeaway, body, extra = '') =>
    `<section class="card viz-card" id="viz-${id}"><h2>${h.esc(title)}</h2>` +
    `${takeaway ? `<p class="viz-takeaway">${takeaway}</p>` : ''}${body}${extra}</section>`;
  const slot = (name, minH) => `<div class="viz-plot" data-chart="${name}" style="min-height:${minH}px"></div>`;
  const legend = (items) => `<div class="viz-legend">${items.map((it) =>
    `<span class="viz-legend-item"><span class="viz-key ${it.kind || 'rect'}" style="background:var(${it.color})"></span>${h.esc(it.label)}</span>`).join('')}</div>`;

  // Bettor colors follow the person: a bettor who is also a squad member keeps that member's color.
  function bettorColors() {
    const byNick = new Map(data.members.map((m) => [m.nickname.toLowerCase(), memberSlot(m.puuid)]));
    const used = new Set(byNick.values());
    const free = Array.from({ length: SLOTS }, (_, i) => i + 1).filter((s) => !used.has(s));
    const out = new Map();
    data.bankroll.forEach((b) => {
      const s = byNick.get(b.name.toLowerCase()) || free.shift();
      out.set(b.name, s ? `--s${s}` : '--muted'); // past eight colors, the rest share the muted gray
    });
    return out;
  }

  // ---- page -------------------------------------------------------------------------
  function html(d, helpers) {
    data = d; h = helpers;
    const g = d.games;
    if (!g.length) return null;
    const m = d.moments;
    const tile = (label, value, sub) =>
      `<div class="tile"><div class="tile-label">${h.esc(label)}</div><div class="tile-value">${value}</div><div class="tile-sub">${sub}</div></div>`;
    const kpis = `<section class="kpis">
      ${tile(`Close games (±${m.close_margin} rounds)`, m.close.games ? rec(m.close) : '–', m.close.games ? `${pct(m.close.win_rate)} won · ${plural(m.close.games, 'game')}` : 'none yet')}
      ${tile(`Blowouts (±${m.blowout_margin}+ rounds)`, m.blowout.games ? rec(m.blowout) : '–', m.blowout.games ? `${pct(m.blowout.win_rate)} won · ${plural(m.blowout.games, 'game')}` : 'none yet')}
      ${tile('After a win / after a loss', `${pct(m.after_win.win_rate)} <span class="muted">/</span> ${pct(m.after_loss.win_rate)}`, `win rate in the next game that night · ${m.after_win.games + m.after_loss.games} games`)}
      ${tile('First game / later games', `${pct(m.first_of_session.win_rate)} <span class="muted">/</span> ${pct(m.later_in_session.win_rate)}`, `win rate · ${plural(m.sessions, 'night')} of play`)}
    </section>`;

    return kpis +
      formCard() +
      `<div class="viz-cols">${timeCard()}${sessionCard()}</div>` +
      mapCard() +
      `<div class="viz-cols">${swingCard()}${aimCard()}</div>` +
      damageCard() +
      `<div class="viz-cols">${compCard()}${bankrollCard()}</div>`;
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
      legend([{ label: `Win rate, last ${w} games`, color: '--accent', kind: 'line' }, { label: 'Won by', color: '--div-pos' }, { label: 'Lost by', color: '--div-neg' }]) +
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
      const color = q.margin > 0 ? '--div-pos' : '--div-neg';
      s += `<path style="fill:var(${color})" d="${colPath(x(i) - cw / 2, cw, mid2, y2(q.margin))}"/>`;
    });
    s += `<line class="viz-base" x1="${m.l}" x2="${W - m.r}" y1="${mid2}" y2="${mid2}"/>` +
      `<text class="viz-ax" x="${m.l - 8}" y="${mid2 + 4}" text-anchor="end">0</text>` +
      `<text class="viz-ax viz-panel" x="${m.l}" y="${top2 - 14}">Round margin per game</text>`;
    // x axis: ~5 date ticks
    const ticks = Math.min(n, Math.max(2, Math.floor(pw / 110)));
    for (let k = 0; k < ticks; k++) {
      const i = Math.round((k * (n - 1)) / Math.max(1, ticks - 1));
      s += `<text class="viz-ax" x="${x(i)}" y="${Ht - 6}" text-anchor="middle">${h.esc(dateShort(g[i].ts))}</text>`;
    }
    s += `<line class="viz-cross" x1="0" x2="0" y1="${m.t}" y2="${top2 + H2}" visibility="hidden"/>` +
      `<rect class="viz-hit" data-cross="form" x="${m.l}" y="0" width="${pw}" height="${top2 + H2}" tabindex="0" aria-label="Game-by-game details; use the arrow keys"/></svg>`;
    el.innerHTML = s;
    cross.form = {
      xs: g.map((_, i) => x(i)),
      tips: g.map((q) => ({
        t: dateLong(q.ts),
        r: [[`on ${q.map || '?'}${q.mode_label ? ' · ' + q.mode_label : ''}`, resultText(q), q.margin > 0 ? '--div-pos' : q.margin < 0 ? '--div-neg' : null],
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
      ? `Game 1: <strong>${pct(first.win_rate)}</strong> → game 3 and later: <strong>${pct(later.wins / later.games)}</strong>`
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
      ? `<strong>${h.esc(rows[0].nickname)}</strong> swings the most: ${n0(rows[0].gap)} more ACS in wins than in losses`
      : 'Needs both wins and losses on record.';
    return card('swing', 'Who swings results', take,
      legend([{ label: 'ACS in losses', color: '--pair-lo', kind: 'dot' }, { label: 'ACS in wins', color: '--pair-hi', kind: 'dot' }]) +
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
      s += `<g class="viz-row"${tip(p.nickname, [['in wins', `${n0(p.acs_win)} ACS`, '--pair-hi'], ['in losses', `${n0(p.acs_loss)} ACS`, '--pair-lo'], ['games', `${p.games_win} W · ${p.games_loss} L`, null]])}>` +
        `<rect class="viz-rowhit" x="0" y="${cy - rh / 2}" width="${W}" height="${rh}"/>` +
        `<circle cx="10" cy="${cy}" r="5" style="fill:var(--s${memberSlot(p.puuid)})"/>` +
        `<text class="viz-label" x="22" y="${cy + 4}">${h.esc(p.nickname)}</text>` +
        `<line class="viz-connector" x1="${x(p.acs_loss)}" x2="${x(p.acs_win)}" y1="${cy}" y2="${cy}"/>` +
        `<circle class="viz-dot" cx="${x(p.acs_loss)}" cy="${cy}" r="5" style="fill:var(--pair-lo)"/>` +
        `<circle class="viz-dot" cx="${x(p.acs_win)}" cy="${cy}" r="5" style="fill:var(--pair-hi)"/>` +
        `<text class="viz-label" x="${W - m.r + 10}" y="${cy + 4}">+${n0(p.gap)}</text></g>`;
    });
    el.innerHTML = s + '</svg>';
  }

  // ---- 6. aim profile: head / body / legs -------------------------------------------------
  const AIM = [['head', 'Head', '--aim-1'], ['body', 'Body', '--aim-2'], ['leg', 'Legs', '--aim-3']];
  const aimRows = () => data.players.filter((p) => p.aim.head + p.aim.body + p.aim.leg > 0).sort((a, b) => b.aim.head_pct - a.aim.head_pct);

  function aimCard() {
    const rows = aimRows();
    const take = rows.length ? `Sharpest: <strong>${h.esc(rows[0].nickname)}</strong>, ${(rows[0].aim.head_pct * 100).toFixed(1)}% of hits to the head` : '';
    return card('aim', 'Aim profile', take,
      legend(AIM.map(([, label, color]) => ({ label, color }))) + slot('aim', 16 + rows.length * 38),
      table(['Player', 'Head', 'Body', 'Legs', 'Hits'], rows.map((p) => [p.nickname, pct(p.aim.head_pct), pct(p.aim.body_pct), pct(p.aim.leg_pct), n0(p.aim.head + p.aim.body + p.aim.leg)])));
  }

  function drawAim(el, W) {
    const rows = aimRows();
    if (!rows.length) { el.innerHTML = '<p class="muted">No shot data yet.</p>'; return; }
    const m = { l: 110, r: 76, t: 8, b: 8 }, rh = 38, t = 18, pw = W - m.l - m.r;
    let s = `<svg class="viz-svg" width="${W}" height="${m.t + rows.length * rh + m.b}" role="img" aria-label="Share of hits to head, body and legs per player">`;
    rows.forEach((p, i) => {
      const cy = m.t + i * rh + rh / 2, total = p.aim.head + p.aim.body + p.aim.leg;
      s += `<g class="viz-row"${tip(p.nickname, AIM.map(([k, label, color]) => [label.toLowerCase(), `${(p.aim[k + '_pct'] * 100).toFixed(1)}%`, color]).concat([['hits', n0(total), null]]))}>` +
        `<rect class="viz-rowhit" x="0" y="${cy - rh / 2}" width="${W}" height="${rh}"/>` +
        `<circle cx="10" cy="${cy}" r="5" style="fill:var(--s${memberSlot(p.puuid)})"/>` +
        `<text class="viz-label" x="22" y="${cy + 4}">${h.esc(p.nickname)}</text>`;
      let x = m.l;
      AIM.forEach(([k, , color], j) => {
        const w = p.aim[k + '_pct'] * pw, last = j === AIM.length - 1;
        const segW = Math.max(0, w - (last ? 0 : 2)); // 2px surface gap between segments
        if (segW > 0) s += `<path style="fill:var(${color})" d="${barPath(x, x + segW, cy, t, last)}"/>`;
        x += w;
      });
      s += `<text class="viz-label" x="${W - m.r + 10}" y="${cy + 4}">${(p.aim.head_pct * 100).toFixed(1)}% HS</text></g>`;
    });
    el.innerHTML = s + '</svg>';
  }

  // ---- 7. damage share, last N games ---------------------------------------------------------
  function damageGames() { return data.games.filter((g) => Object.keys(g.damage_share).length).slice(-DAMAGE_GAMES); }

  function damageCard() {
    const g = damageGames();
    const avg = data.members.map((m) => ({ ...m, share: g.reduce((a, x) => a + (x.damage_share[m.puuid] || 0), 0) / (g.length || 1) }));
    const top = avg.slice().sort((a, b) => b.share - a.share)[0];
    const take = g.length ? `<strong>${h.esc(top.nickname)}</strong> dealt ${pct(top.share)} of the team's damage over the last ${plural(g.length, 'game')} (an even split is 20%)` : '';
    return card('damage', 'Who carries the damage', take,
      legend(avg.map((m) => ({ label: `${m.nickname} ${pct(m.share)}`, color: `--s${memberSlot(m.puuid)}` }))) + slot('damage', 250),
      table(['Game', 'Result'].concat(data.members.map((m) => m.nickname)),
        g.slice().reverse().map((x) => [`${dateShort(x.ts)} · ${x.map || ''}`, resultText(x)].concat(data.members.map((m) => pct(x.damage_share[m.puuid]))))));
  }

  function drawDamage(el, W) {
    const g = damageGames();
    if (!g.length) { el.innerHTML = '<p class="muted">No damage data yet.</p>'; return; }
    const m = { l: 44, r: 12, t: 8, b: 34 }, H = 190;
    const pw = W - m.l - m.r, band = pw / g.length, cw = Math.min(24, band - 4);
    const y = (v) => m.t + (1 - v) * H;
    let s = `<svg class="viz-svg" width="${W}" height="${m.t + H + m.b}" role="img" aria-label="Share of team damage per player, per game">`;
    [0.25, 0.5, 0.75, 1].forEach((v) => {
      s += `<line class="viz-grid" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/>` +
        `<text class="viz-ax" x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${v * 100}%</text>`;
    });
    g.forEach((q, i) => {
      const x = m.l + (i + 0.5) * band - cw / 2;
      let acc = 0;
      const ms = data.members.filter((mm) => q.damage_share[mm.puuid] != null);
      ms.forEach((mm, j) => {
        const v = q.damage_share[mm.puuid], top = j === ms.length - 1;
        const yb = y(acc), yt = y(acc + v) + (top ? 0 : 2); // 2px surface gap between stacked segments
        if (yb - yt > 0.5) s += top ? `<path style="fill:var(--s${memberSlot(mm.puuid)})" d="${colPath(x, cw, yb, yt)}"/>` :
          `<rect x="${x}" y="${yt}" width="${cw}" height="${yb - yt}" style="fill:var(--s${memberSlot(mm.puuid)})"/>`;
        acc += v;
      });
      const rows = ms.slice().sort((a, b) => q.damage_share[b.puuid] - q.damage_share[a.puuid])
        .map((mm) => [mm.nickname, pct(q.damage_share[mm.puuid]), `--s${memberSlot(mm.puuid)}`]);
      s += `<rect class="viz-colhit" x="${x - 2}" y="${m.t}" width="${cw + 4}" height="${H}"${tip(`${dateShort(q.ts)} · ${q.map || '?'} · ${resultText(q)}`, rows)}/>` +
        `<text class="viz-ax" x="${x + cw / 2}" y="${m.t + H + 16}" text-anchor="middle">${q.result === 'win' ? 'W' : q.result === 'loss' ? 'L' : 'D'}</text>`;
    });
    s += `<line class="viz-base" x1="${m.l}" x2="${W - m.r}" y1="${y(0)}" y2="${y(0)}"/>` +
      `<text class="viz-ax" x="${m.l}" y="${m.t + H + 30}">${h.esc(dateShort(g[0].ts))}</text>` +
      `<text class="viz-ax" x="${W - m.r}" y="${m.t + H + 30}" text-anchor="end">${h.esc(dateShort(g[g.length - 1].ts))}</text>`;
    el.innerHTML = s + '</svg>';
  }

  // ---- 8. team comps by role shape ---------------------------------------------------------
  function compCard() {
    const rows = data.comps.slice(0, MAX_COMPS);
    const good = rows.filter((c) => c.games >= 3).sort((a, b) => b.win_rate - a.win_rate);
    const take = good.length ? `Best comp with 3+ games: <strong>${h.esc(good[0].label)}</strong> (${rec(good[0])})` : 'Play a comp 3+ times to rank it.';
    return card('comps', 'Team comps by role', take, slot('comps', 30 + rows.length * 34) +
      '<p class="muted small">D Duelist · C Controller · I Initiator · S Sentinel. Most-played first.</p>',
      table(['Comp', 'Games', 'Record', 'Win rate'], data.comps.map((c) => [c.label, String(c.games), rec(c), pct(c.win_rate)])));
  }

  function drawComps(el, W) {
    const rows = data.comps.slice(0, MAX_COMPS);
    if (!rows.length) { el.innerHTML = '<p class="muted">No games yet.</p>'; return; }
    const m = { l: 104, r: 88, t: 6, b: 24 }, rh = 34, t = 16, pw = W - m.l - m.r, H = rows.length * rh;
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
        `<text class="viz-label viz-mono" x="0" y="${cy + 4}">${h.esc(c.key)}</text>` +
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
      legend(b.map((x) => ({ label: x.name, color: colors.get(x.name), kind: 'line' }))) + slot('bankroll', 230),
      table(['Bettor', 'Settled bets', 'Profit'], final.map((x) => [x.name, String(b.find((y) => y.name === x.name).points.length), n0(x.profit)])));
  }

  function drawBankroll(el, W) {
    const b = data.bankroll;
    if (!b.length) return;
    const colors = bettorColors();
    const times = [...new Set(b.flatMap((x) => x.points.map((p) => p.ts)))].sort((p, q) => p - q);
    const t0 = times[0], t1 = times[times.length - 1] > t0 ? times[times.length - 1] : t0 + 1;
    const vals = b.flatMap((x) => x.points.map((p) => p.profit)).concat([0]);
    const top = niceMax(Math.max(...vals.map(Math.abs)));
    const lo = Math.min(...vals) < 0 ? -top : 0, hi = Math.max(...vals) > 0 ? top : 0;
    const labelRoom = b.length <= 4 ? 84 : 12;
    const m = { l: 52, r: labelRoom, t: 12, b: 26 }, H = 180, pw = W - m.l - m.r;
    const x = (ts) => m.l + ((ts - t0) / (t1 - t0)) * pw;
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

  // ---- mounting, tooltips, crosshair ----------------------------------------------------------
  const DRAW = { form: drawForm, session: drawSession, swing: drawSwing, aim: drawAim, damage: drawDamage, comps: drawComps, bankroll: drawBankroll };

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
    (spec.r || []).forEach(([label, value, color]) => {
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

  window.FiveViz = { html, mount, hideTip };
})();
