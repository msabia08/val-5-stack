/* 5-Stack Tracker front-end. Plain JS, no build step, no dependencies. */
(() => {
  'use strict';

  const VIEWS = ['overview', 'players', 'forecasts', 'viz', 'odds', 'bettors', 'shop', 'arcade', 'troop', 'matches', 'setup'];
  const state = {
    view: 'overview',
    status: null, stats: null, matches: null, odds: null, content: null, insights: null, forecasts: null,
    fc: { stat: 'acs', player: '', cell: '', role: '' }, // Forecasts tab: stat, player (puuid), map|role cell filter and role ('' = all)
    bets: [], bettors: [], rewards: [], slip: [], me: null, seasons: null, resetOpen: false,
    ctx: { map: '', agents: {} },
    topDir: {}, // scoreboard card -> 'low' when flipped to its counter market (bottom of the scoreboard)
    bettor: localStorage.getItem('fs.bettor') || '',
    oddsFormat: localStorage.getItem('fs.oddsFormat') || 'american',
    stake: Number(localStorage.getItem('fs.stake') || 50) || 50,
    slipMode: 'single',
    matchFilter: { map: '', result: '', mode: '' },
    recap: null, recapId: '', // Matches tab: the recap shown ('' = the latest game)
    playerPick: '', // Players tab: the player in the detail ('' = the first)
    vizGenre: 'all', // Charts tab: which group of charts is shown
    trends: { stat: 'acs', range: 'all', hidden: {} }, // Players tab's trend chart: stat, every game or the last 10, players hidden
    shop: null, troop: null, looks: {}, profile: null, troopPick: '', arcade: null, // Onkey's Shop and Monkeys tabs (web/shop.js); troopPick: the profile shown
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
    oddsDec: (dec) => (state.oddsFormat === 'decimal' ? Number(dec).toFixed(2) : fmt.american(dec)),
    res: (r) => (r === 'win' ? 'W' : r === 'loss' ? 'L' : 'D'),
  };

  const memberIndex = () => {
    const m = new Map();
    (state.status?.members || []).forEach((x, i) => m.set(x.puuid, { ...x, slot: (i % 8) + 1 }));
    return m;
  };

  // Stable color per bettor name, so a person's slips always look the same at a glance.
  // A bettor's colour is their squad member's colour (the member whose rewards go to that account), so the
  // five of us look the same everywhere. Anyone else shares the slots after the squad's.
  function bettorSlot(name) {
    const key = String(name || '').toLowerCase();
    const members = state.status?.members || [];
    const i = members.findIndex((m) => (m.bettor || m.nickname || '').toLowerCase() === key);
    if (i >= 0) return (i % 8) + 1;
    const spare = 8 - Math.min(members.length, 7);
    let h = 0;
    for (let c = 0; c < key.length; c++) h = (h * 31 + key.charCodeAt(c)) >>> 0;
    return 8 - (h % spare);
  }

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
  // The signed-in bettor (balance and open bets) for the top-bar chip; bets.js's loadBets refreshes it too.
  const loadMe = async () => {
    try { state.me = (await api('/api/bettor/me')).bettor; } catch (e) { return; }
    renderMe();
  };
  let shownBalance = null; // { name, balance } last shown, for the bet slip's balance ticker
  // The top bar's three chips, on every page: credits (to Standings), bananas (to the Shop) and the signed-in
  // bettor's badge and name (to their profile on the Monkeys tab). Signed out, only the profile chip shows, saying Sign in.
  function renderMe() {
    const me = state.me, shop = window.FiveShop;
    const credits = $('#me-credits'), bananas = $('#me-bananas'), chip = $('#me-chip');
    shop.syncMe(); // a new sign-in brings its own bananas, items and themes
    credits.classList.toggle('hidden', !me);
    bananas.classList.toggle('hidden', !me);
    credits.innerHTML = `<b>${me ? fmt.credits(me.balance) : '–'}</b><span class="me-unit">credits</span>`;
    credits.title = me ? `${fmt.credits(me.balance)} credits${me.open_bets ? `, plus ${fmt.credits(me.open_stake)} on open bets` : ''}. Open the rankings.` : 'Sign in to see your credits';
    const nb = me && me.bananas != null ? shop.bn(me.bananas) : '–';
    bananas.innerHTML = `<b>${nb}</b><span aria-hidden="true">🍌</span><span class="sr-only">bananas</span>`;
    bananas.title = me ? `${nb} bananas. Open Onkey's Shop.` : 'Sign in to see your bananas';
    chip.href = me ? `#monkeys/${encodeURIComponent(me.name)}` : '#odds';
    chip.innerHTML = `<span class="me-avatar" aria-hidden="true">${me ? shop.badgeOf(me.name) : '🐒'}</span>` +
      `<span class="me-name">${me ? shop.nameHtml(me.name) : 'Sign in'}</span>`;
    chip.title = me ? `Signed in as ${me.name}. Open your profile.` : 'Sign in on the Place bets page to bet and shop';
    if (!me) return;
    // The balance under the bet slip (Place bets only) counts to a new value; the chip just shows it.
    const was = shownBalance && shownBalance.name === me.name ? shownBalance.balance : null;
    const slipBalance = $('#slip .acct-balance b');
    if (slipBalance && was !== null && Math.abs(was - me.balance) >= 0.5) tickBalance(slipBalance, was, me.balance);
    shownBalance = { name: me.name, balance: me.balance };
    celebrateWins(me);
    announceTransfers(me);
    announceTaxes(me);
  }

  // The generous monkey gets rewarded: a toast for generosity tax collected (a cut of the next win of someone this
  // bettor sent credits to) since this browser last looked (fs.seenTax.<name>: the newest tax_ts shown).
  function announceTaxes(me) {
    const key = `fs.seenTax.${me.name.toLowerCase()}`, got = me.recent_taxes || [];
    if (document.hidden) return;
    const newest = got.reduce((a, t) => Math.max(a, t.tax_ts || 0), 0);
    let seen = null;
    try { seen = localStorage.getItem(key); localStorage.setItem(key, String(Math.max(newest, Number(seen) || 0))); }
    catch (e) { return; }
    if (seen === null) return;
    const fresh = got.filter((t) => (t.tax_ts || 0) > Number(seen));
    if (!fresh.length) return;
    const total = fresh.reduce((a, t) => a + t.tax_amount, 0);
    toast(`The generous monkey gets rewarded: +${fmt.credits(total)} credits in generosity tax from ` +
      `${[...new Set(fresh.map((t) => t.recipient))].join(', ')}'s win${fresh.length === 1 ? '' : 's'}`, 'good');
  }

  // A toast for credits other bettors sent since this browser last looked (fs.seenTransfers.<name> holds the newest
  // transfer id shown), so one that arrives while the site is closed is mentioned on the next visit. The first visit
  // on a device only sets the marker, and a hidden tab waits for the next poll.
  function announceTransfers(me) {
    const key = `fs.seenTransfers.${me.name.toLowerCase()}`, got = me.recent_received || [];
    if (document.hidden) return;
    const newest = got.reduce((a, t) => Math.max(a, t.id), 0);
    let seen = null;
    try { seen = localStorage.getItem(key); localStorage.setItem(key, String(Math.max(newest, Number(seen) || 0))); }
    catch (e) { return; } // no storage: no way to tell what's new
    if (seen === null) return;
    const fresh = got.filter((t) => t.id > Number(seen));
    if (!fresh.length) return;
    const total = fresh.reduce((a, t) => a + t.amount, 0);
    toast(fresh.length === 1
      ? `${fresh[0].sender} sent you ${fmt.credits(total)} credits${fresh[0].note ? `: “${fresh[0].note}”` : ''}`
      : `${fresh.length} transfers came in: ${fmt.credits(total)} credits`, 'good');
  }

  // ---- little celebrations ----------------------------------------------------------
  const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const LONG_SHOT_DECIMAL = 6; // +500 or longer: gold confetti, and more of it

  // Count a balance from its old value to the new one, flashing green (up) or red (down).
  function tickBalance(el, from, to) {
    el.classList.remove('tick-up', 'tick-down');
    void el.offsetWidth; // restart the flash
    el.classList.add(to > from ? 'tick-up' : 'tick-down');
    if (reducedMotion() || document.hidden) return; // a hidden tab pauses the frames: just show the new balance
    const t0 = performance.now(), dur = 900;
    const step = (now) => {
      const k = Math.min(1, (now - t0) / dur), eased = 1 - (1 - k) ** 3;
      el.textContent = fmt.credits(from + (to - from) * eased);
      if (k < 1 && el.isConnected) requestAnimationFrame(step);
    };
    el.textContent = fmt.credits(from);
    requestAnimationFrame(step);
  }

  // Confetti for wins settled since this browser last saw one (fs.seenWins.<name> holds the newest settled_ts
  // shown), so a game that settles while the site is closed still gets its confetti on the next visit or poll.
  // The first visit on a device only sets the marker: old wins aren't celebrated. The confetti comes out of the bet
  // slip's balance, so it waits until you're on Place bets (renderMe runs after every redraw), and in a background
  // tab until the tab is visible again (browsers pause animations in hidden tabs).
  let celebrateLater = false;
  function celebrateWins(me) {
    const key = `fs.seenWins.${me.name.toLowerCase()}`, wins = me.recent_wins || [];
    const newest = wins.reduce((a, w) => Math.max(a, w.settled_ts || 0), 0);
    let seen = null;
    try { seen = localStorage.getItem(key); } catch (e) { return; } // no storage: no way to tell what's new
    const save = () => { try { localStorage.setItem(key, String(newest)); } catch (e) { /* storage blocked */ } };
    if (seen === null) { save(); return; }
    const fresh = wins.filter((w) => (w.settled_ts || 0) > Number(seen));
    const origin = $('#slip .acct-balance b');
    if (!fresh.length || !origin) return;
    if (document.hidden) {
      if (!celebrateLater) {
        celebrateLater = true;
        document.addEventListener('visibilitychange', function shown() {
          if (document.hidden) return;
          document.removeEventListener('visibilitychange', shown);
          celebrateLater = false;
          if (state.me) celebrateWins(state.me);
        });
      }
      return;
    }
    save();
    const longShot = fresh.some((w) => w.odds_decimal >= LONG_SHOT_DECIMAL);
    const what = (w) => (w.market_type === 'parlay' ? `Your ${(w.description || '').split(' + ').length}-leg parlay` : `“${w.description}”`);
    const total = fresh.reduce((a, w) => a + (w.payout || 0), 0);
    toast(`${longShot ? 'Long shot! ' : 'Winner! '}` + (fresh.length === 1 ? `${what(fresh[0])} paid ${fmt.credits(total)} credits`
      : `${fresh.length} bets paid ${fmt.credits(total)} credits`), 'good');
    confetti(origin, longShot, window.FiveShop.myCelebration());
  }

  const CONFETTI = ['#ff4d6d', '#ffd23f', '#3bceac', '#4f8cff', '#b86bff', '#ff8a00'];
  const GOLD = ['#ffd700', '#ffc107', '#ffe082', '#fff3c4', '#e6a800'];
  // A burst of confetti from an element (the bet slip's balance), falling under gravity and fading out.
  // style: a Onkey's Shop celebration, { colors } for its own palette or { emoji } to throw emoji instead.
  function confetti(from, big, style) {
    if (!from || reducedMotion()) return;
    const r = from.getBoundingClientRect(), x0 = r.left + r.width / 2, y0 = r.top + r.height / 2;
    const emoji = style && style.emoji, n = emoji ? (big ? 70 : 40) : big ? 160 : 80;
    const colors = big ? GOLD : (style && style.colors) || CONFETTI;
    for (let i = 0; i < n; i++) {
      const p = document.createElement('span');
      if (emoji) {
        p.className = 'confetti emoji';
        p.textContent = emoji[i % emoji.length];
        p.style.cssText = `left:${x0}px;top:${y0}px`;
      } else {
        p.className = `confetti${i % 3 === 0 ? ' round' : ''}`;
        p.style.cssText = `left:${x0}px;top:${y0}px;background:${colors[i % colors.length]}`;
      }
      document.body.appendChild(p);
      const ang = Math.PI * (0.05 + Math.random() * 0.9), speed = (big ? 420 : 320) * (0.35 + Math.random());
      const vx = Math.cos(ang) * speed, vy = Math.sin(ang) * speed * 0.5 - 160, T = 1.6 + Math.random() * 0.9, spin = (Math.random() - 0.5) * 1440;
      const frames = [0, 0.25, 0.5, 0.75, 1].map((k) => {
        const t = k * T;
        return { transform: `translate(${vx * t}px, ${vy * t + 450 * t * t}px) rotate(${spin * k}deg)`, opacity: k === 1 ? 0 : 1 };
      });
      p.animate(frames, { duration: T * 1000, easing: 'linear' }).onfinish = () => p.remove();
      setTimeout(() => p.remove(), 4000); // in case the animation never finishes
    }
  }
  const loadStats = async () => { state.stats = await api('/api/stats'); };
  const loadInsights = async () => { state.insights = await api('/api/insights'); };
  // The next-game forecast uses the odds board's map and agents (set on Place bets), and the board itself is loaded
  // too, so the Next game card can show the betting line and add it to the slip.
  const loadForecasts = async () => {
    const p = new URLSearchParams({ stat: state.fc.stat });
    if (state.fc.player) p.set('player', state.fc.player);
    if (state.ctx.map) p.set('map', state.ctx.map);
    if (state.fc.player && state.ctx.agents[state.fc.player]) p.set('agent', state.ctx.agents[state.fc.player]);
    const [f] = await Promise.all([api('/api/forecasts?' + p.toString()), loadOdds().catch(() => null)]);
    state.forecasts = f;
    state.fc.player = state.forecasts.player?.puuid || '';
  };
  // The Overview's extras: the latest game's recap and the bettors' leaderboard (kept apart from the Matches tab's recap).
  const loadOverview = async () => {
    const [recap, lb, won, lost] = await Promise.all([api('/api/recap').catch(() => null), api('/api/bettors').catch(() => null),
      api('/api/bets?status=won&limit=300').catch(() => null), api('/api/bets?status=lost&limit=300').catch(() => null)]);
    state.overviewRecap = recap;
    if (lb) state.bettors = lb.bettors;
    state.overviewBets = [...(won ? won.bets : []), ...(lost ? lost.bets : [])];
  };
  const loadMatches = async () => { state.matches = (await api('/api/matches?limit=400')).matches; };
  const loadRecap = async () => { state.recap = await api('/api/recap' + (state.recapId ? '?match=' + encodeURIComponent(state.recapId) : '')); };
  const loadContent = async () => { state.content = await api('/api/content'); };
  // The betting UI lives in web/bets.js (window.FiveBets); these names keep the call sites below unchanged.
  const { loadBets, loadBettingReport, loadSeasons, betsSection, slipHtml, viewBettors, customLineCard, myBetsCard } = window.FiveBets;
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

  // A one-line explanation with the rest folded behind "How this works" (the full text is still one click away).
  const how = (summary, more) => `<details class="how"><summary>${summary}</summary><div class="how-body">${more}</div></details>`;
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
    // The last night the squad played (the Matches tab's rule: a break of more than NIGHT_GAP_S starts a new night).
    // The last game itself has its own card below, so this tile says how the whole night went.
    const ts = (g) => Date.parse(g.started_at) / 1000 || 0;
    const night = [];
    for (const g of team.recent) {
      if (night.length && ts(night[night.length - 1]) - ts(g) > NIGHT_GAP_S) break;
      night.push(g);
    }
    const nightW = night.filter((g) => g.result === 'win').length, nightL = night.filter((g) => g.result === 'loss').length;
    const nightDay = night.length ? new Date(ts(night[0]) * 1000).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' }) : '';
    const kpis = [
      kpi('5-stack games', fmt.n0(team.games), `since ${fmt.date(team.first_played)}`),
      kpi('Win rate', fmt.pct(team.win_rate), `${team.wins}W · ${team.losses}L${team.draws ? ' · ' + team.draws + 'D' : ''}`),
      kpi('Current streak', team.streak || '–', 'wins or losses in a row'),
      kpi('Avg round diff', fmt.signed(team.avg_round_diff), 'rounds per game'),
      kpi('Last night', night.length ? `<span class="${nightW > nightL ? 'up' : nightW < nightL ? 'down' : ''}">${nightW}–${nightL}</span>` : '–',
        night.length ? `${nightDay} · ${night.length}${night.length === team.recent.length ? '+' : ''} game${night.length === 1 ? '' : 's'}` : ''),
    ];
    const mapChart = hbars(team.by_map.map((m) => ({
      label: m.map, value: m.win_rate, text: fmt.pct(m.win_rate), n: `${m.wins}-${m.losses}`,
      title: `${m.map}: ${m.wins}-${m.losses} (${fmt.pct(m.win_rate)}), avg round diff ${fmt.signed(m.avg_round_diff)}`,
    })));
    // The front page: what happened lately. Full detail lives on Matches (recap), Standings / Place bets and Players.
    return `<section class="kpis">${kpis.join('')}</section>
      <section class="ov-grid">${overviewLastGame(idx)}${overviewBetting()}</section>
      <section class="ov-grid">${overviewTrending(st, idx)}
        <div class="card"><h2>Map performance</h2><p class="muted small">Win rate as a 5-stack, ${team.games} games</p>${mapChart}</div>
      </section>`;
  }

  // The latest game's result and its top OV_HIGHLIGHTS highlights (from /api/recap), linking to the full recap on Matches.
  // Compact cards (title, then player · detail on one line; long text is cut and shown whole on hover), so five of
  // them stay about as tall as the Betting card beside them.
  const OV_HIGHLIGHTS = 5;
  function overviewLastGame(idx) {
    const r = state.overviewRecap;
    if (!r) return '';
    const m = r.match;
    const who = (h) => (h.puuid ? `<span class="swatch s${idx.get(h.puuid)?.slot || 1}"></span>${esc(h.nickname || '')}` : '<span class="muted">Squad</span>');
    const cards = r.highlights.slice(0, OV_HIGHLIGHTS).map((h) => `<div class="hl-card tone-${esc(h.tone)}" title="${esc(h.detail ? `${h.title}: ${h.detail}` : h.title)}">` +
      `<div class="hl-title">${esc(h.title)}</div><div class="ov-hl-meta"><span class="hl-who">${who(h)}</span>` +
      `${h.detail ? `<span class="hl-detail">${esc(h.detail)}</span>` : ''}</div></div>`).join('');
    return `<section class="card"><div class="section-head"><h2>Last game</h2><a class="recap-latest go-link" href="#matches">Full recap ›</a></div>
      <div class="ov-game"><span class="chip ${esc(m.result || '')}">${fmt.res(m.result)}</span><b>${m.rounds_won}–${m.rounds_lost}</b>` +
      `<span>${esc(m.map || '')}</span><span class="muted small">${esc(m.mode_label || '')} · ${fmt.date(m.started_ts ? m.started_ts * 1000 : null)}</span></div>
      ${cards ? `<div class="ov-hl">${cards}</div>` : '<p class="muted">Nothing out of the ordinary this game.</p>'}</section>`;
  }

  // The Betting card, in three parts that share one look (tiles and rows): your balance, the leaderboard's top 3,
  // and the standout bets settled on the last OV_BET_GAMES games.
  const OV_BET_GAMES = 5;
  function overviewBetting() {
    const me = state.me, lb = state.bettors || [];
    const who = (name) => `<span class="swatch s${bettorSlot(name)}"></span><b>${window.FiveShop.nameHtml(name)}</b>`;
    const balance = me
      ? `<div class="ov-tile ov-balance"><div class="ov-tile-label">Your balance</div><div class="ov-tile-value">${fmt.credits(me.balance)} <span class="ov-unit">credits</span></div>` +
        `<div class="ov-tile-sub">${me.open_bets ? `+${fmt.credits(me.open_stake)} riding on ${me.open_bets} open bet${me.open_bets === 1 ? '' : 's'}` : 'No open bets'}</div></div>`
      : '<p class="muted small">Sign in on the <a href="#odds">Place bets</a> page to bet and see your balance here.</p>';
    const leaders = lb.slice(0, 3).map((b, i) =>
      `<li class="${me && b.name.toLowerCase() === me.name.toLowerCase() ? 'me' : ''}"><span class="ov-medal">${['🥇', '🥈', '🥉'][i]}</span><span class="ov-name">${who(b.name)}</span>` +
      `<span class="num">${fmt.credits(b.balance)}</span><span class="num ${b.profit > 0 ? 'up' : b.profit < 0 ? 'down' : 'muted'}">${fmt.signed(b.profit, 0)}</span></li>`).join('');
    return `<section class="card ov-betting"><div class="section-head"><h2>Betting</h2><span class="head-links"><a class="go-link" href="#odds">Place bets ›</a><a class="go-link" href="#bettors">Standings ›</a></span></div>
      ${balance}
      <h3 class="ov-sub">Leaderboard <span>credits and profit</span></h3>${leaders ? `<ol class="ov-leaders">${leaders}</ol>` : '<p class="muted small">No bettors yet.</p>'}
      ${overviewStandouts(who)}</section>`;
  }

  // Biggest win (most profit), biggest loss (biggest stake lost), longest odds won and shortest odds lost among the
  // bets settled on the last OV_BET_GAMES games, whoever placed them.
  function overviewStandouts(who) {
    const all = (state.overviewBets || []).filter((b) => b.settled_match_id);
    const games = [...new Map(all.map((b) => [b.settled_match_id, b.game_started_ts || 0])).entries()]
      .sort((x, y) => y[1] - x[1]).slice(0, OV_BET_GAMES).map(([id]) => id);
    const bets = all.filter((b) => games.includes(b.settled_match_id));
    const won = bets.filter((b) => b.status === 'won'), lost = bets.filter((b) => b.status === 'lost');
    const top = (list, score) => list.reduce((best, b) => (!best || score(b) > score(best) ? b : best), null);
    const what = (b) => (b.market_type === 'parlay' ? `${(b.description || '').split(' + ').length}-leg parlay` : b.description || '');
    const odds = (b) => fmt.odds({ decimal: b.odds_decimal, american: fmt.american(b.odds_decimal) });
    const tiles = [
      ['Biggest win', top(won, (b) => (b.payout || 0) - b.stake), (b) => `+${fmt.credits((b.payout || 0) - b.stake)}`, 'up'],
      ['Biggest loss', top(lost, (b) => b.stake), (b) => `−${fmt.credits(b.stake)}`, 'down'],
      ['Longest odds won', top(won, (b) => b.odds_decimal), odds, 'up'],
      ['Shortest odds lost', top(lost, (b) => -b.odds_decimal), odds, 'down'],
    ].map(([label, b, value, cls]) => (b
      ? `<div class="ov-tile"><div class="ov-tile-label">${label}</div><div class="ov-tile-value ${cls}">${value(b)}</div>` +
        `<div class="ov-tile-sub" title="${esc(`${b.bettor}: ${what(b)}`)}">${who(b.bettor)} · ${esc(what(b))}</div></div>`
      : `<div class="ov-tile empty"><div class="ov-tile-label">${label}</div><div class="ov-tile-value muted">–</div></div>`)).join('');
    return `<h3 class="ov-sub">Last ${OV_BET_GAMES} games</h3>` +
      (bets.length ? `<div class="ov-tiles">${tiles}</div>` : '<p class="muted small">No settled bets on the last few games.</p>');
  }

  // Each player's ACS and K/D over their last 5 games against the 10 before (the same trend as the Players tab).
  function overviewTrending(st, idx) {
    // Each stat: the last TREND_RECENT games' average, and a coloured change against the TREND_BEFORE before them
    // (grey when it's under TREND_MIN_CHANGE). The two averages are in the tooltip.
    const cell = (m, key, f) => {
      const tr = trend(m, key);
      if (!tr) return '<td class="num muted">–</td>';
      const d = tr.now - tr.then, cls = Math.abs(tr.rel) < TREND_MIN_CHANGE ? 'flat' : d > 0 ? 'up' : 'down';
      return `<td class="num" title="Last ${TREND_RECENT} games: ${f(tr.now)} vs ${f(tr.then)} in the ${TREND_BEFORE} before">` +
        `<b>${f(tr.now)}</b><span class="trend-delta ${cls}">${cls === 'flat' ? '' : d > 0 ? '▲ ' : '▼ '}${d >= 0 ? '+' : '−'}${f(Math.abs(d))}</span></td>`;
    };
    const members = st.members.filter((m) => m.overall.games)
      .sort((a, b) => ((trend(b, 'acs') || { rel: -9 }).rel) - ((trend(a, 'acs') || { rel: -9 }).rel));
    const rows = members.map((m) => `<tr><th scope="row"><span class="swatch s${idx.get(m.puuid)?.slot || 1}"></span>${esc(m.nickname)}</th>` +
      `${cell(m, 'acs', fmt.n0)}${cell(m, 'kd', fmt.n2)}<td class="ov-spark">${sparkline(statSeries(m, 'acs'), { w: 110, h: 26 })}</td></tr>`).join('');
    return `<section class="card"><div class="section-head"><h2>Who's trending</h2><a class="go-link" href="#players">Players ›</a></div>
      <p class="muted small">Last ${TREND_RECENT} games against the ${TREND_BEFORE} before them, hottest first.</p>
      <div class="table-wrap"><table class="ov-trending"><thead><tr><th>Player</th><th class="ov-h">ACS</th><th class="ov-h">K/D</th><th class="ov-h ov-spark">ACS, last ${fmt.n0(Math.max(...members.map((m) => m.form.length)))} games</th></tr></thead>` +
      `<tbody>${rows}</tbody></table></div></section>`;
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
  // The tab is one comparison table (a row per player, with how their 5-stack games compare with their other games)
  // and one player's detail below it, picked from the table or the buttons. Numbers that are the same for everyone
  // (games, win rate, win rate by map: every tracked game has all five) live on the Overview tab instead.
  // The detail used to show a "Compared with their other games" table and recent W/L chips; both are switched off,
  // set either to true to bring it back.
  const PLAYER_CARD_OTHER_GAMES = false;
  const PLAYER_CARD_FORM = false;
  // The major per-player stats: [label, key (per-game value in form / range), overall value, format, higher is better].
  const PLAYER_COLS = [
    ['ACS', 'acs', (m) => m.overall.acs, fmt.n0, true],
    ['K/D', 'kd', (m) => m.overall.kd, fmt.n2, true],
    ['Kills', 'kills', (m) => m.overall.avg_kills, fmt.n1, true],
    ['Deaths', 'deaths', (m) => m.overall.avg_deaths, fmt.n1, false],
    ['Assists', 'assists', (m) => m.overall.avg_assists, fmt.n1, true],
    ['ADR', 'adr', (m) => m.overall.adr, fmt.n0, true],
    ['HS %', 'hs_pct', (m) => m.overall.hs_pct, fmt.pct1, true],
  ];
  // Trends compare a player's last TREND_RECENT games with the TREND_BEFORE before them (the sparklines' numbers).
  const TREND_RECENT = 5, TREND_BEFORE = 10, TREND_MIN_CHANGE = 0.05;
  const gameStat = (f, key) => (key === 'kd' ? f.kills / Math.max(1, f.deaths) : f[key]);

  // A player's per-game values for one stat, oldest first (the last 15 games).
  const statSeries = (m, key) => m.form.slice().reverse().map((f) => gameStat(f, key));

  function trend(m, key) {
    const recent = m.form.slice(0, TREND_RECENT), before = m.form.slice(TREND_RECENT, TREND_RECENT + TREND_BEFORE);
    if (recent.length < TREND_RECENT || before.length < TREND_RECENT) return null;
    const mean = (games) => {
      if (key === 'kd') return games.reduce((a, f) => a + f.kills, 0) / Math.max(1, games.reduce((a, f) => a + f.deaths, 0));
      const vals = games.map((f) => f[key]).filter(isNum);
      return vals.length ? vals.reduce((a, v) => a + v, 0) / vals.length : null;
    };
    const now = mean(recent), then = mean(before);
    if (!isNum(now) || !isNum(then) || !then) return null;
    return { now, then, rel: (now - then) / Math.abs(then) };
  }

  function viewPlayers() {
    const st = state.stats, idx = memberIndex();
    const members = st.members.filter((m) => m.overall.games);
    if (!members.length) return emptyState();
    const focus = (members.find((m) => m.puuid === state.playerPick) || members[0]).puuid; // the detail's player
    return playersTable(members, idx) + playerDetail(members, idx) +
      window.FiveViz.playerTrends(st.timeline, members, { esc, fmt, slot: (puuid) => idx.get(puuid)?.slot }, { ...state.trends, focus });
  }

  // One row per player: this 5-stack's numbers (the best in each column highlighted, and an arrow when the player's
  // last 5 games are trending up or down), then how they compare with their games outside the stack.
  function playersTable(members, idx) {
    const pick = (members.find((m) => m.puuid === state.playerPick) || members[0]).puuid;
    const best = PLAYER_COLS.map(([, , get, , up]) => (up ? Math.max : Math.min)(...members.map((m) => get(m) ?? (up ? -Infinity : Infinity))));
    const rows = members.slice().sort((a, b) => (b.overall.acs || 0) - (a.overall.acs || 0)).map((m) => {
      const slot = idx.get(m.puuid)?.slot || 1, dv = m.deviation;
      const cells = PLAYER_COLS.map(([, key, get, f, up], i) => {
        const v = get(m);
        if (!isNum(v)) return '<td class="num">–<span class="pc-arrow"></span></td>';
        const tr = trend(m, key);
        let arrow = '<span class="pc-arrow"></span>', title = '';
        if (tr) {
          title = ` title="Last ${TREND_RECENT} games: ${f(tr.now)} vs ${f(tr.then)} in the ${TREND_BEFORE} before"`;
          if (Math.abs(tr.rel) >= TREND_MIN_CHANGE) {
            arrow = `<span class="pc-arrow ${(tr.rel > 0) === up ? 'up' : 'down'}">${tr.rel > 0 ? '▲' : '▼'}</span>`;
          }
        }
        return `<td class="num${v === best[i] ? ' pc-best' : ''}"${title}>${f(v)}${arrow}</td>`;
      }).join('');
      const acs = devMetric(dv, 'acs'), kd = devMetric(dv, 'kd'), wr = devMetric(dv, 'win_rate');
      const so = dv && dv.standout && devMetric(dv, dv.standout);
      const vs = dv
        ? `<td class="num pl-split" title="${dv.usual_games} other games">${acs ? devDiff(acs) : '–'}</td><td class="num">${kd ? devDiff(kd) : '–'}</td>` +
          `<td class="num">${wr ? devDiff(wr) : '–'}</td><td>${so ? `${esc(so.label)} ${devBadge(so.verdict)}` : (acs ? devBadge(acs.verdict) : '<span class="muted">–</span>')}</td>`
        : '<td class="pl-split muted" colspan="4">No other games stored yet</td>';
      return `<tr class="pl-row${m.puuid === pick ? ' on' : ''}" data-puuid="${esc(m.puuid)}"><th scope="row"><span class="swatch s${slot}"></span>${esc(m.nickname)}</th>${cells}${vs}</tr>`;
    }).join('');
    return `<section class="card"><h2>Squad comparison</h2>
      ${how(`Per-game averages over the squad's ${fmt.n0(members[0].overall.games)} 5-stack games. Click a row for that player's detail.`,
        `The best in each column is highlighted. ▲ / ▼ mark a stat trending up or down: the player's last ${TREND_RECENT} games against the ${TREND_BEFORE} before them, 5% or more apart (green is good; for deaths, fewer is good). Hover a number for the two figures. <b>5-stack vs. their other games</b> compares each player's 5-stack games with their games outside the stack (solo queue or smaller parties): "better" or "worse" means the gap is about two standard errors or more, "slightly" one to two.`)}
      <div class="table-wrap"><table class="pl-table"><thead>
        <tr><th></th><th colspan="${PLAYER_COLS.length}" class="pl-group">This 5-stack</th><th colspan="4" class="pl-group pl-split">5-stack vs. their other games</th></tr>
        <tr><th>Player</th>${PLAYER_COLS.map(([label]) => `<th class="num">${label}</th>`).join('')}<th class="num pl-split">ACS</th><th class="num">K/D</th><th class="num">Win %</th><th>Biggest change</th></tr>
      </thead><tbody>${rows}</tbody></table></div></section>`;
  }

  // One player's detail: a tile per major stat (average, sparkline, highest and lowest game), their best game, and
  // their by-agent and by-map tables.
  function playerDetail(members, idx) {
    const m = members.find((x) => x.puuid === state.playerPick) || members[0];
    const slot = idx.get(m.puuid)?.slot || 1, bk = m.best.kills;
    const picker = `<div class="seg" role="tablist">${members.map((x) => `<button type="button" class="seg-btn pl-pick ${x.puuid === m.puuid ? 'on' : ''}" data-puuid="${esc(x.puuid)}" role="tab" aria-selected="${x.puuid === m.puuid}"><span class="swatch s${idx.get(x.puuid)?.slot || 1}"></span>${esc(x.nickname)}</button>`).join('')}</div>`;
    const gameLink = (g, word, f) => `<a href="#matches" class="recap-link" data-recap-match="${esc(g.match_id)}" title="${esc(`${g.map || '?'} · ${g.agent || '?'} · ${fmt.date(g.started_at)}: open the recap`)}">${word} ${f(g.value)}</a>`;
    const tiles = PLAYER_COLS.map(([label, key, get, f]) => {
      const r = m.range && m.range[key];
      const one = ['kills', 'deaths', 'assists'].includes(key) ? fmt.n0 : f; // a single game's count is a whole number
      const range = r ? `<div class="tile-range">${gameLink(r.high, 'High', one)} · ${gameLink(r.low, 'Low', one)}</div>` : '';
      return kpi(label, f(get(m)), '', sparkline(statSeries(m, key), { w: 110, h: 28 }) + range);
    }).join('');
    const table = (rows, key) => {
      if (!rows.length) return '<p class="muted">No games yet.</p>';
      const withWin = key === 'agent'; // win rate by map is the same for all five, so only agents show it
      return `<div class="table-wrap"><table class="compact"><thead><tr><th>${key === 'agent' ? 'Agent' : 'Map'}</th><th class="num">Games</th>${withWin ? '<th class="num">Win %</th>' : ''}<th class="num">ACS</th><th class="num">K/D</th><th class="num">ADR</th><th class="num">HS %</th></tr></thead><tbody>` +
        rows.slice().sort((a, b) => b.games - a.games).map((r) => `<tr><td>${esc(r[key])}</td><td class="num">${r.games}</td>${withWin ? `<td class="num">${fmt.pct(r.win_rate)}</td>` : ''}<td class="num">${fmt.n0(r.acs)}</td><td class="num">${fmt.n2(r.kd)}</td><td class="num">${fmt.n0(r.adr)}</td><td class="num">${fmt.pct1(r.hs_pct)}</td></tr>`).join('') +
        '</tbody></table></div>';
    };
    const form = m.form.slice(0, 10).map((f) =>
      `<span class="chip ${f.result}" title="${esc(f.map)} · ${esc(f.agent)} · ${f.kills}/${f.deaths}/${f.assists} · ACS ${f.acs}">${fmt.res(f.result)}</span>`).join('');
    const bestTile = bk
      ? `<a href="#matches" class="tile tile-link" data-recap-match="${esc(bk.match_id)}" title="Open this game's recap"><div class="tile-label">Best game</div><div class="tile-value">${fmt.n0(bk.value)} kills</div>` +
        `<div class="tile-sub">${esc(bk.map || '?')} · ${esc(bk.agent || '?')} · ${fmt.date(bk.started_at)} · recap ›</div></a>`
      : kpi('Best game', '–');
    return `<section class="card player" id="player-detail">
      <div class="pl-picker">${picker}</div>
      <header class="player-head"><span class="swatch s${slot} lg"></span><div><h2>${esc(m.nickname)}</h2><div class="muted small">${esc(m.name)}#${esc(m.tag)}${m.tier_name ? ' · ' + esc(m.tier_name) : ''}</div></div>` +
        `${PLAYER_CARD_FORM ? `<div class="form">${form}</div>` : ''}<div class="pl-links"><a href="#forecasts" class="go-link" data-forecast-player="${esc(m.puuid)}">${esc(m.nickname)}'s forecasts ›</a><a href="#viz" class="go-link">Charts ›</a></div></header>
      <p class="muted small">Per-game averages; the lines show the last ${m.form.length} games, and High / Low are their best and worst complete 5-stack games (click one for its recap).</p>
      <div class="kpis small pl-tiles">${tiles}${bestTile}</div>
      <div class="grid-2"><div><h3>By agent</h3>${table(m.by_agent, 'agent')}</div><div><h3>By map</h3>${table(m.by_map, 'map')}</div></div>
      ${PLAYER_CARD_OTHER_GAMES ? devBlock(m) : ''}
    </section>`;
  }

  // ---- visualizations (drawn by web/viz.js) ---------------------------------------
  function viewViz() {
    const idx = memberIndex();
    const out = window.FiveViz.html(state.insights, { esc, fmt, slot: (puuid) => idx.get(puuid)?.slot, bettorSlot }, state.vizGenre);
    return out == null ? emptyState() : out;
  }

  // ---- forecasts (drawn by web/viz.js) ---------------------------------------------
  function viewForecasts() {
    const idx = memberIndex();
    return window.FiveViz.forecasts(state.forecasts, {
      esc, fmt, slot: (puuid) => idx.get(puuid)?.slot, bettorSlot, oddBtn, odds: state.odds, ctx: state.ctx,
      goRecap: (id) => { state.recapId = id; location.hash = '#matches'; }, // a game in the chart opens its recap
    }, state.fc);
  }
  async function openRecap(id) {
    if (!id) return;
    state.recapId = id;
    try { await loadRecap(); draw(); } catch (e) { toast(e.message, 'bad'); return; }
    $('#recap')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  async function refreshForecasts() {
    try { await loadForecasts(); draw(); } catch (e) { toast(e.message, 'bad'); }
  }

  // ---- odds & bets --------------------------------------------------------------
  // A flaming border marks a pick that would have won each of the last 3+ games (s.streak), a frosty one a pick that
  // usually hits but has missed its last 3+ (s.cold, multi-way markets only); both from BetManager.mark_streaks.
  function oddBtn(mk, s, label) {
    const on = state.slip.some((x) => x.market_id === mk.market_id && x.selection === s.key);
    const runOf = (n) => (n >= state.odds.streak_lookback ? `${n}+` : n);
    const note = s.streak ? `Hit ${runOf(s.streak)} games in a row` : s.cold ? `Missed ${runOf(s.cold)} games in a row` : '';
    const cls = s.streak ? ' hot' : s.cold ? ' cold' : '';
    return `<button class="odd ${on ? 'on' : ''}${cls}" data-m="${esc(mk.market_id)}" data-s="${esc(s.key)}" aria-pressed="${on}" title="${Math.round(s.fair_prob * 100)}% fair probability${note ? ` · ${note}` : ''}">` +
      `${label ? `<span>${esc(label)}</span>` : ''}<b>${fmt.odds(s)}</b>${note ? `<span class="sr-only">, ${note}</span>` : ''}</button>`;
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
      ${how(`Odds for the next game, from your 5-stack history (house edge ${Math.round((od.house_edge || 0) * 100)}%). Pick a map or agents to sharpen them.`,
        `<p>Lines come from your 5-stack history, weighted toward recent games. Choosing a map or agents up-weights matching games.` +
        `${od.partial_games?.forfeits ? ` ${od.partial_games.forfeits} surrendered ${od.partial_games.forfeits === 1 ? 'game is' : 'games are'} scaled to a full ${od.partial_games.full_game_rounds}-round game and counted at reduced weight.` : ''}</p>` +
        `<p>Bets count for the next game, and for the game that just started if you place them within ${fmt.n0(state.status.bet_grace_minutes ?? 2)} minutes of it starting (while loading in or in round 1). After that they carry over to the following game.</p>` +
        '<p>If the next game ends early by a surrender, the match result stands. Other bets settle only if they were already decided (an over that had already cleared its line wins; its under loses); everything else is refunded. Parlays apply this leg by leg: undecided legs are dropped and the rest still count. A remake in the first few rounds doesn\'t count as a game: bets carry over to the next one.</p>')}</section>`;
    if (!od.ready) {
      return ctxBar + `<div class="card empty"><h2>No odds yet</h2><p>${esc(od.message)}</p></div>` + betsSection();
    }
    // Exact score spans the grid, its 12 scores in a compact grid of their own.
    // No subtitles (each card's details are in its title's tooltip) and buttons pinned to the bottom, so each row lines up.
    const team = od.team.map((mk) => {
      const btnLabel = (s) => (mk.type === 'team_margin' ? s.label.replace(/^Win by /, '') : s.label); // the slip still says "Win by 1–2"
      const sels = `<div class="sels ${mk.type}">${mk.selections.map((s) => oddBtn(mk, s, btnLabel(s))).join('')}</div>`;
      const sub = mk.desc + (mk.basis ? ` · ${fmt.pct(mk.basis.win_rate)} of ${mk.basis.games} won${mk.basis.map_games ? `, ${mk.basis.map_games} on this map` : ''}` : '') +
        (mk.mean != null ? ` · avg ${mk.mean}` : '');
      return `<div class="market${mk.type === 'team_score' ? ' wide' : ''}"><h3 title="${esc(sub)}">${esc(mk.label)}</h3>${sels}</div>`; // details on hover
    }).join('');
    const props = new Map(od.player_props.map((p) => [p.market_id, p]));
    const propRows = od.members.map((m) => {
      const slot = idx.get(m.puuid)?.slot || 1;
      return `<tr><th scope="row"><span class="swatch s${slot}"></span>${esc(m.nickname)}${m.context_agent || m.borrowed ? `<div class="muted small">${[m.context_agent ? esc(m.context_agent) : '', m.borrowed ? 'thin history, team average blended in' : ''].filter(Boolean).join(' · ')}</div>` : ''}</th>` +
        od.stat_defs.map((sd) => {
          const mk = props.get(`ou:${sd.key}:${m.puuid}`);
          if (!mk) return '<td class="prop muted">–</td>';
          const [o, u] = mk.selections;
          // The line as plain bold text, then over / under as one button split down the middle.
          return `<td class="prop" title="Line ${mk.line} · average ${mk.mean}"><div class="prop-cell"><span class="prop-line">${mk.line}</span>` +
            `<div class="prop-pair${o.streak || u.streak || o.cold || u.cold ? ' lit' : ''}">${oddBtn(mk, o, 'O')}${oddBtn(mk, u, 'U')}</div></div></td>`;
        }).join('') + '</tr>';
    }).join('');
    // Each scoreboard card is a pair: the "top" market and its counter, flipped with a toggle. No subtitles, so every
    // card is the same size: each button's meaning is its tooltip, and the stats whose names don't say it get an ⓘ.
    const EXPLAIN_TOPS = ['acs_rel'];
    const byId = new Map(od.top_markets.map((mk) => [mk.market_id, mk]));
    const tops = od.top_markets.filter((mk) => mk.direction !== 'low').map((hi) => {
      const lo = byId.get(hi.counter_id);
      const dir = lo && state.topDir[hi.pair] === 'low' ? 'low' : 'high';
      const mk = dir === 'low' ? lo : hi;
      const toggle = lo ? `<div class="slip-mode top-toggle" role="tablist" aria-label="${esc(hi.label)} or ${esc(lo.label)}">` +
        [['high', hi], ['low', lo]].map(([d, m]) =>
          `<button type="button" class="mode-btn top-dir ${dir === d ? 'on' : ''}" data-pair="${esc(hi.pair)}" data-dir="${d}" role="tab" aria-selected="${dir === d}" title="${esc(m.desc)}">` +
          `${esc(m.label)}${EXPLAIN_TOPS.includes(hi.stat) ? `<span class="top-info" aria-hidden="true">ⓘ</span><span class="sr-only">: ${esc(m.desc)}</span>` : ''}</button>`).join('') +
        '</div>' : `<h3 title="${esc(mk.desc)}">${esc(mk.label)}</h3>`;
      return `<div class="market">${toggle}` +
        mk.selections.map((s) => {
          const slot = idx.get(s.key)?.slot || 1;
          return `<div class="top-row"><span class="swatch s${slot}"></span><span>${esc(s.label)}</span>` +
            `<span class="top-bar"><span class="top-fill s${slot}" style="width:${Math.round(s.fair_prob * 100)}%"></span></span>` +
            `<span class="muted small num">${Math.round(s.fair_prob * 100)}%</span>${oddBtn(mk, s, '')}</div>`;
        }).join('') + '</div>';
    }).join('');
    return ctxBar + `<div class="odds-layout"><div>
        <section class="card"><h2>Team markets</h2>${how('How the next game goes for the squad as a whole.', 'Rounds won / lost, winning margin and exact score all come from one model of the final score, so they agree with the match-result and overtime odds.')}<div class="markets">${team}</div></section>
        <section class="card"><h2>Player props · over / under</h2><p class="muted small">For the next 5-stack game. Each cell shows the line, then the odds for going over (O) or under (U) it; tap O or U to add a pick to the slip.</p>
          <div class="table-wrap"><table class="props"><thead><tr><th>Player</th>${od.stat_defs.map((s) => `<th>${esc(s.label)}</th>`).join('')}</tr></thead><tbody>${propRows}</tbody></table></div></section>
        <section class="card"><h2>Top and bottom of the scoreboard</h2>${how('Pick who finishes first in a stat, or flip a card for who finishes last.', '"Popped off" and "Got diff\'d" rank everyone against their own average ACS instead of against each other, so anyone can win them. A tie refunds the stake.')}<div class="markets">${tops}</div></section>
        ${betsSection()}
      </div><aside class="odds-side"><div class="slip card" id="slip">${slipHtml()}</div>${customLineCard()}${myBetsCard()}</aside></div>`;
  }


  async function refreshOdds() {
    try { await loadOdds(); draw(); } catch (e) { toast(e.message, 'bad'); }
  }


  // ---- matches --------------------------------------------------------------------
  const NIGHT_GAP_S = 3 * 3600; // a longer break between game starts begins a new night (insights.SESSION_GAP_S)
  const NIGHTS_OPEN = 3; // the latest nights shown open on the Matches tab; older ones are folded
  function viewMatches() {
    const ms = state.matches, idx = memberIndex();
    if (!ms.length) return emptyState();
    const shown = state.recap && state.recap.match.match_id;
    const recap = window.FiveRecap.html(state.recap, { esc, fmt, slot: (puuid) => idx.get(puuid)?.slot });
    const f = state.matchFilter;
    const maps = [...new Set(ms.map((m) => m.map).filter(Boolean))].sort();
    const modes = [...new Set(ms.map((m) => m.mode_label).filter(Boolean))].sort();
    const keep = (m) => (!f.map || m.map === f.map) && (!f.result || m.result === f.result) && (!f.mode || m.mode_label === f.mode);
    // Nights: games newest first, a new night after a break of more than NIGHT_GAP_S between game starts (the Charts
    // tab's "nights" use the same rule). Grouped before filtering, so a filter never splits a night.
    const nights = [];
    ms.forEach((m) => {
      const cur = nights[nights.length - 1], prev = cur && cur[cur.length - 1];
      if (prev && (prev.started_ts || 0) - (m.started_ts || 0) <= NIGHT_GAP_S) cur.push(m); else nights.push([m]);
    });
    const shownCount = nights.reduce((a, n) => a + n.filter(keep).length, 0);
    const time = (m) => (m.started_ts ? new Date(m.started_ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '');
    const row = (m) => {
      const rounds = (m.rounds_won || 0) + (m.rounds_lost || 0) || 1;
      const top = m.players[0];
      return `<tr class="match-row ${m.result}${m.match_id === shown ? ' on' : ''}" data-id="${esc(m.match_id)}" title="Open this game's recap">` +
        `<td><span class="chip ${m.result}">${fmt.res(m.result)}</span> <b class="m-score">${m.rounds_won}–${m.rounds_lost}</b>` +
        `${m.ending === 'forfeit' ? ' <span class="tag ff" title="Ended early by a surrender">FF</span>' : ''}</td><td>${esc(m.map)}</td><td class="muted">${esc(m.mode_label || '')}</td>` +
        `<td>${top ? `${esc(top.nickname || top.name)} · ${fmt.n0((top.score || 0) / rounds)} ACS · ${esc(top.agent || '')}` : ''}</td>` +
        `<td class="muted small">${time(m)}</td><td class="m-go">${m.match_id === shown ? 'showing' : '›'}</td></tr>`;
    };
    let open = 0;
    const groups = nights.map((night) => {
      const games = night.filter(keep);
      if (!games.length) return '';
      const first = night[night.length - 1], w = games.filter((m) => m.result === 'win').length, l = games.filter((m) => m.result === 'loss').length;
      const date = first.started_ts ? new Date(first.started_ts * 1000).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' }) : '';
      const isOpen = open < NIGHTS_OPEN || games.some((m) => m.match_id === shown);
      open += 1;
      return `<details class="night"${isOpen ? ' open' : ''}><summary><span class="night-date">${esc(date)}</span>` +
        `<span class="night-rec"><b class="up">${w}</b>–<b class="down">${l}</b></span><span class="muted small">${games.length} game${games.length === 1 ? '' : 's'}</span></summary>` +
        `<div class="table-wrap"><table class="match-table"><colgroup><col class="c-res"><col class="c-map"><col class="c-mode"><col><col class="c-time"><col class="c-go"></colgroup>` +
        `<tbody>${games.map(row).join('')}</tbody></table></div></details>`;
    }).join('');
    return recap + `<section class="card"><h2>All games</h2><div class="filters">
        <select id="f-map"><option value="">All maps</option>${maps.map((x) => `<option ${f.map === x ? 'selected' : ''}>${esc(x)}</option>`).join('')}</select>
        <select id="f-result"><option value="">All results</option><option value="win" ${f.result === 'win' ? 'selected' : ''}>Wins</option><option value="loss" ${f.result === 'loss' ? 'selected' : ''}>Losses</option></select>
        <select id="f-mode"><option value="">All modes</option>${modes.map((x) => `<option ${f.mode === x ? 'selected' : ''}>${esc(x)}</option>`).join('')}</select>
        <span class="muted small">${shownCount} of ${ms.length} games · by night, newest first · click a game for its recap</span></div>
      ${groups || '<p class="muted">No games match these filters.</p>'}</section>`;
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
        case 'forecasts': view.innerHTML = viewForecasts(); break;
        case 'viz': view.innerHTML = viewViz(); break;
        case 'odds': view.innerHTML = viewOdds(); break;
        case 'bettors': view.innerHTML = viewBettors(); break;
        case 'shop': view.innerHTML = window.FiveShop.viewShop(); break;
        case 'troop': view.innerHTML = window.FiveShop.viewTroop(); break;
        case 'arcade': view.innerHTML = window.FiveArcade.viewArcade(); break;
        case 'matches': view.innerHTML = viewMatches(); break;
        default: view.innerHTML = viewSetup();
      }
    } catch (e) {
      console.error(e);
      view.innerHTML = `<div class="card error"><h2>Something went wrong drawing this page</h2><p>${esc(e.message)}</p></div>`;
    }
    renderMe();
    bind();
    if (['viz', 'bettors', 'forecasts', 'matches', 'players'].includes(state.view) && view.querySelector('[data-chart], [data-tip]')) window.FiveViz.mount(view);
  }

  async function render() {
    const view = $('#view');
    view.setAttribute('aria-busy', 'true');
    try {
      switch (state.view) {
        case 'overview': await Promise.all([loadStats(), loadOverview()]); break;
        case 'players': await loadStats(); break;
        case 'forecasts': await loadForecasts(); break;
        case 'viz': await loadInsights(); break;
        case 'odds': await Promise.all([loadOdds(), loadBets(), loadContent()]); break;
        case 'bettors': await Promise.all([loadBets(), loadBettingReport(), loadSeasons()]); break;
        case 'shop': await Promise.all([window.FiveShop.loadShop(), window.FiveShop.loadTroop()]); break;
        case 'troop': await Promise.all([window.FiveShop.loadTroop(), window.FiveShop.loadProfile(), state.shop ? null : window.FiveShop.loadShop()]); break;
        case 'arcade': await window.FiveArcade.load(); break;
        case 'matches': await Promise.all([loadMatches(), loadRecap()]); break;
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
    // Players tab: pick a player (table row or button), jump to a recap or their forecasts.
    const pickPlayer = (puuid) => { state.playerPick = puuid; draw(); $('#player-detail')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); };
    $$('.pl-row', view).forEach((r) => r.addEventListener('click', () => pickPlayer(r.dataset.puuid)));
    $$('.pl-pick', view).forEach((b) => b.addEventListener('click', () => { state.playerPick = b.dataset.puuid; draw(); }));
    $$('[data-recap-match]', view).forEach((a) => a.addEventListener('click', (e) => { e.stopPropagation(); state.recapId = a.dataset.recapMatch; }));
    $$('.viz-genre', view).forEach((b) => b.addEventListener('click', () => { state.vizGenre = b.dataset.v; draw(); }));
    // Players tab's trend chart: stat, every game or the last 10, and showing / hiding players.
    $$('.tr-stat', view).forEach((b) => b.addEventListener('click', () => { state.trends.stat = b.dataset.v; draw(); }));
    $$('.tr-range', view).forEach((b) => b.addEventListener('click', () => { state.trends.range = b.dataset.v; draw(); }));
    $$('.tr-player', view).forEach((b) => b.addEventListener('click', () => { const h = state.trends.hidden; h[b.dataset.puuid] = !h[b.dataset.puuid]; draw(); }));
    $$('[data-forecast-player]', view).forEach((a) => a.addEventListener('click', () => { state.fc.player = a.dataset.forecastPlayer; state.fc.cell = ''; }));
    $$('.fc-player', view).forEach((b) => b.addEventListener('click', () => { state.fc.player = b.dataset.v; state.fc.cell = ''; refreshForecasts(); }));
    $$('.fc-stat', view).forEach((b) => b.addEventListener('click', () => { state.fc.stat = b.dataset.v; refreshForecasts(); }));
    $$('.fc-cell', view).forEach((td) => {
      const pickCell = () => {
        window.FiveViz.hideTip();
        state.fc.cell = state.fc.cell === td.dataset.cell ? '' : td.dataset.cell;
        draw();
        if (state.fc.cell) $('#viz-forecast-strip')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); // the filtered games sit above the grid
      };
      td.addEventListener('click', pickCell);
      td.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pickCell(); } });
    });
    $('#fc-all')?.addEventListener('click', () => { state.fc.cell = ''; draw(); });
    $$('.fc-role', view).forEach((b) => b.addEventListener('click', () => { state.fc.role = b.dataset.v; state.fc.cell = ''; draw(); }));
    $('#ctx-map')?.addEventListener('change', (e) => { state.ctx.map = e.target.value; refreshOdds(); });
    $$('.ctx-agent', view).forEach((sel) => sel.addEventListener('change', (e) => {
      const p = e.target.dataset.puuid;
      if (e.target.value) state.ctx.agents[p] = e.target.value; else delete state.ctx.agents[p];
      refreshOdds();
    }));
    $('#ctx-clear')?.addEventListener('click', () => { state.ctx = { map: '', agents: {} }; refreshOdds(); });
    $('#odds-format')?.addEventListener('change', (e) => { state.oddsFormat = e.target.value; localStorage.setItem('fs.oddsFormat', state.oddsFormat); draw(); });
    $$('.top-dir', view).forEach((b) => b.addEventListener('click', () => { state.topDir[b.dataset.pair] = b.dataset.dir; draw(); }));
    window.FiveBets.bind(view);
    window.FiveShop.bind(view);
    window.FiveArcade.bind(view);
    $('#copy-url')?.addEventListener('click', async (e) => {
      try { await navigator.clipboard.writeText(e.currentTarget.dataset.url); toast('Link copied'); }
      catch (err) { toast('Could not copy; select the link and copy it manually', 'bad'); }
    });
    $$('tr.match-row', view).forEach((r) => r.addEventListener('click', () => openRecap(r.dataset.id)));
    $$('.recap-nav', view).forEach((b) => b.addEventListener('click', () => openRecap(b.dataset.recap)));
    $$('.recap-latest', view).forEach((a) => a.addEventListener('click', () => { state.recapId = ''; })); // the Overview's "Last game" link
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

  // Emoji in the nav (every entry, group buttons and menu rows). Set to false to hide them all; the markup in index.html
  // stays, so setting it back to true brings them back.
  const NAV_ICONS = true;

  // The nav's dropdown groups (Stats, Betting, Onkey's in index.html): a group's button names the page you're on, with
  // its icon, and lights up; the menu opens on click (or Enter / Space / ↓), arrow keys move through it, and Escape,
  // a pick or a click elsewhere closes it. Only one menu is open at a time.
  const navGroups = () => $$('#tabs .nav-group');
  function navMenu(group, open) {
    $('.nav-menu', group).classList.toggle('hidden', !open);
    $('.nav-trigger', group).setAttribute('aria-expanded', String(open));
  }
  const closeNavMenus = (except) => navGroups().forEach((g) => { if (g !== except) navMenu(g, false); });
  function bindNavMenus() {
    document.documentElement.classList.toggle('no-nav-icons', !NAV_ICONS);
    navGroups().forEach((group) => {
      const menu = $('.nav-menu', group), trigger = $('.nav-trigger', group);
      const items = () => $$('a', menu);
      const focusItem = (i) => { const list = items(); list[(i + list.length) % list.length].focus(); };
      trigger.addEventListener('click', () => { closeNavMenus(group); navMenu(group, menu.classList.contains('hidden')); });
      trigger.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown') { e.preventDefault(); closeNavMenus(group); navMenu(group, true); focusItem(Math.max(0, items().findIndex((a) => a.classList.contains('active')))); }
      });
      menu.addEventListener('keydown', (e) => {
        const i = items().indexOf(document.activeElement);
        if (e.key === 'ArrowDown') { e.preventDefault(); focusItem(i + 1); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); focusItem(i - 1); }
        else if (e.key === 'Escape') { navMenu(group, false); trigger.focus(); }
        else if (e.key === 'Tab') navMenu(group, false);
      });
      menu.addEventListener('click', () => navMenu(group, false));
    });
    document.addEventListener('click', (e) => { if (!e.target.closest('#tabs .nav-group')) closeNavMenus(); });
  }
  // A group's button shows the current page's name and icon while you're on one of its pages, else its own.
  function syncNavGroups() {
    navGroups().forEach((group) => {
      const cur = $(`.nav-menu a[data-view="${state.view}"]`, group);
      const name = cur ? [...$('b', cur).childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('').trim() : group.dataset.label;
      $('.nav-trigger', group).classList.toggle('active', !!cur);
      $('.nav-label', group).textContent = name;
      $('.nav-trigger .nav-icon', group).textContent = cur ? cur.dataset.icon : group.dataset.icon;
      navMenu(group, false);
    });
  }

  function route() {
    // #monkeys/<name> opens that bettor's profile on the Monkeys tab (the view is still called troop; #troop works too).
    let [v, arg] = (location.hash || '#overview').slice(1).split('/');
    if (v === 'monkeys') v = 'troop';
    state.view = VIEWS.includes(v) ? v : 'overview';
    if (state.view === 'troop') {
      try { state.troopPick = arg ? decodeURIComponent(arg) : ''; } catch (e) { state.troopPick = ''; }
      if (arg) window.scrollTo(0, 0);
    }
    window.FiveViz?.hideTip();
    $$('#tabs a, #setup-btn').forEach((a) => a.classList.toggle('active', a.dataset.view === state.view)); // Setup lives on the ⚙ button
    syncNavGroups();
    render();
  }

  let checkTheme = () => {}; // set in init: drops a shop theme the signed-in bettor doesn't own
  async function init() {
    const shop = window.FiveShop;
    shop.init({ state, $, $$, api, draw, esc, fmt, kpi, toast, confetti, onShop: () => checkTheme() });
    window.FiveBets.init({ state, $, $$, api, bettorSlot, draw, esc, fmt, kpi, memberIndex, toast, nameHtml: shop.nameHtml, ticketClass: shop.ticketClass, ticketExtras: shop.ticketExtras });
    window.FiveArcade.init({ state, $, $$, api, draw, esc, fmt, toast, nameHtml: shop.nameHtml, loadMe });
    // Themes cycle dark -> light -> the ones the signed-in bettor bought in Onkey's Shop (Greg Mode: the light colours
    // over web/assets/greg.png; Onkey Mode; Jungle Mode) -> dark. A shop theme is only applied once the shop confirms
    // it's owned (checkTheme, after every shop load), so a saved or ?theme= one waits, and one you don't own is dropped.
    const THEMES = { dark: 'Dark', light: 'Light', greg: 'Greg Mode', onkey: 'Onkey Mode', jungle: 'Jungle Mode', sakura: 'Sakura', midnight: 'Midnight', terminal: 'Terminal', synthwave: 'Synthwave' };
    const FREE_THEMES = ['dark', 'light'];
    const themeBtn = $('#theme-btn');
    const cycle = () => [...FREE_THEMES, ...shop.ownedThemes().map((t) => t.key)];
    const nextTheme = (t) => { const c = cycle(), i = c.indexOf(t); return c[(i + 1) % c.length]; };
    // Two themes (only dark and light): ◐ toggles. More than two (any bought in the shop): ◐ opens a picker.
    const picker = () => cycle().length > 2;
    const showTheme = (t) => {
      themeBtn.title = picker() ? `Theme: ${THEMES[t]} (click to pick another)` : `Theme: ${THEMES[t]} (click for ${THEMES[nextTheme(t)]})`;
      themeBtn.setAttribute('aria-haspopup', picker() ? 'true' : 'false');
      if (!picker()) themeMenu(false);
    };
    const menu = $('#theme-menu');
    const setTheme = (t) => {
      document.documentElement.dataset.theme = t;
      localStorage.setItem('fs.theme', t);
      wanted = null;
      showTheme(t);
    };
    function themeMenu(open) {
      menu.classList.toggle('hidden', !open);
      themeBtn.setAttribute('aria-expanded', String(open));
      if (!open) return;
      const cur = current();
      menu.innerHTML = cycle().map((t) => `<button type="button" class="theme-pick ${t === cur ? 'active' : ''}" role="menuitemradio" aria-checked="${t === cur}" data-theme-pick="${t}">` +
        `<span class="shop-theme" data-theme-preview="${t}"><i></i><i></i><i></i></span><b>${esc(THEMES[t])}</b><span class="theme-tick" aria-hidden="true">${t === cur ? '✓' : ''}</span></button>`).join('') +
        '<a class="theme-more" href="#shop" role="menuitem">More themes in Onkey\'s Shop ›</a>';
      ($('.theme-pick.active', menu) || $('.theme-pick', menu)).focus();
    }
    menu.addEventListener('click', (e) => {
      const b = e.target.closest('[data-theme-pick]');
      if (b) setTheme(b.dataset.themePick);
      themeMenu(false);
      if (b) themeBtn.focus();
    });
    menu.addEventListener('keydown', (e) => {
      const items = $$('.theme-pick, .theme-more', menu), i = items.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length].focus(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
      else if (e.key === 'Escape') { themeMenu(false); themeBtn.focus(); }
      else if (e.key === 'Tab') themeMenu(false);
    });
    document.addEventListener('click', (e) => { if (!e.target.closest('#theme-menu, #theme-btn')) themeMenu(false); });
    let wanted = new URLSearchParams(location.search).get('theme') || localStorage.getItem('fs.theme'); // a shop theme waits here
    if (FREE_THEMES.includes(wanted)) document.documentElement.dataset.theme = wanted;
    const current = () => document.documentElement.dataset.theme || 'dark'; // dark unless a theme was picked, whatever the OS prefers
    checkTheme = () => {
      const owned = shop.ownedThemes().map((t) => t.key), cur = document.documentElement.dataset.theme;
      if (cur && !FREE_THEMES.includes(cur) && !owned.includes(cur)) { // signed out, or someone else's theme on this device
        delete document.documentElement.dataset.theme;
        wanted = cur;
      } else if (wanted && owned.includes(wanted)) {
        document.documentElement.dataset.theme = wanted;
        wanted = null;
      }
      showTheme(current());
    };
    showTheme(current());
    themeBtn.addEventListener('mouseenter', () => showTheme(current())); // the cycle grows once the shop has loaded
    themeBtn.addEventListener('click', () => {
      if (picker()) themeMenu(menu.classList.contains('hidden'));
      else setTheme(nextTheme(current()));
    });
    // Greg Mode: every click drops a little Greg from the pointer (never blocks the click; off for reduced motion).
    document.addEventListener('click', (e) => {
      if (document.documentElement.dataset.theme !== 'greg' || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      if (document.querySelectorAll('.greg-drop').length >= 25) return;
      const greg = document.createElement('img');
      greg.src = '/assets/greg-drop.png';
      greg.alt = '';
      greg.className = 'greg-drop';
      greg.style.left = `${e.clientX}px`;
      greg.style.top = `${e.clientY}px`;
      greg.style.setProperty('--drift', `${Math.round((Math.random() - 0.5) * 220)}px`);
      greg.style.setProperty('--spin', `${Math.round((Math.random() - 0.5) * 900)}deg`);
      greg.addEventListener('animationend', () => greg.remove());
      document.body.append(greg);
    });
    $('#sync-btn').addEventListener('click', () => sync(false));
    bindNavMenus();
    window.addEventListener('hashchange', route);
    try {
      await Promise.all([loadStatus(), loadContent(), loadMe(), shop.loadTroop().catch(() => {})]); // the troop's looks style names everywhere
    } catch (e) {
      $('#view').innerHTML = `<div class="card error"><h2>Could not reach the server</h2><p>${esc(e.message)}</p></div>`;
      return;
    }
    route();
    if (state.status.tracker?.syncing) pollUntilIdle();
    setInterval(async () => {
      const prevGames = state.status?.games, wasSyncing = state.status?.tracker?.syncing;
      try { await loadStatus(); } catch (e) { return; }
      loadMe(); // bets settle when games arrive, so the balance chip keeps up
      const nowSyncing = state.status.tracker?.syncing;
      if (nowSyncing && !wasSyncing) pollUntilIdle();
      else if (state.status.games !== prevGames) render();
    }, 20000);
  }

  document.addEventListener('DOMContentLoaded', init);
})();
