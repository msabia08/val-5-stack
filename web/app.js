/* 5-Stack Tracker front-end. Plain JS, no build step, no dependencies. */
(() => {
  'use strict';

  const VIEWS = ['overview', 'players', 'squad', 'forecasts', 'viz', 'odds', 'bettors', 'hunt', 'slots', 'blackjack', 'poker', 'wheel', 'shop', 'arcade', 'troop', 'matches', 'setup'];
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
    if (!res.ok) {
      const error = new Error(data.error || `${res.status} ${res.statusText}`);
      error.status = res.status;
      throw error;
    }
    return data;
  }
  const loadStatus = async () => {
    state.status = await api('/api/status');
    window.FiveRoster.size = (state.status.members || []).length; // "5-stack" wording follows the squad size
    renderHeader();
  };
  // The signed-in bettor (balance and open bets) for the top-bar chip; bets.js's loadBets refreshes it too.
  const loadMe = async () => {
    try { state.me = (await api('/api/bettor/me')).bettor; } catch (e) { return; }
    renderMe();
  };
  let shownBalance = null; // { name, balance } last shown, for the credits chip's ticker
  // A balance held for a spin that's still turning (slots, the daily wheel). The server settles a spin before its
  // animation ends, so a refresh of /api/bettor/me in the meantime (the 20-second poll) would show the result early in
  // the top bar or on the machine. While a hold is on, both show the held value; releasing it counts the chip to the real
  // balance. It lapses after HOLD_MAX_MS in case a page never lets go.
  const HOLD_MAX_MS = 30000;
  let held = null;
  function holdBalance(value) {
    if (!state.me) return;
    held = { name: state.me.name, value, until: Date.now() + HOLD_MAX_MS };
    renderMe();
  }
  function releaseBalance() {
    if (!held) return;
    held = null;
    renderMe();
  }
  function displayBalance() {
    const me = state.me;
    if (!me) return null;
    return held && held.name === me.name && Date.now() < held.until ? held.value : me.balance;
  }

  // The top bar's three chips, on every page: credits (to Standings), bananas (to the Shop) and the signed-in
  // bettor's badge and name, which opens the account menu. Signed out, only that chip shows, saying Sign in.
  function renderMe() {
    const me = state.me, shop = window.FiveShop;
    const bal = me ? displayBalance() : null;
    const credits = $('#me-credits'), bananas = $('#me-bananas'), chip = $('#me-chip');
    shop.syncMe(); // a new sign-in brings its own bananas, items and themes
    credits.classList.toggle('hidden', !me);
    bananas.classList.toggle('hidden', !me);
    wheelReady(me);
    credits.innerHTML = `<b>${me ? fmt.credits(bal) : '–'}</b><span class="me-unit">credits</span>`;
    credits.title = me ? `${fmt.credits(bal)} credits${me.open_bets ? `, plus ${fmt.credits(me.open_stake)} on open bets` : ''}. Open the rankings.` : 'Sign in to see your credits';
    const nb = me && me.bananas != null ? shop.bn(me.bananas) : '–';
    bananas.innerHTML = `<b>${nb}</b><span aria-hidden="true">🍌</span><span class="sr-only">bananas</span>`;
    bananas.title = me ? `${nb} bananas. Open Onkey's Shop.` : 'Sign in to see your bananas';
    chip.innerHTML = `<span class="me-avatar" aria-hidden="true">${me ? shop.badgeOf(me.name) : '🐒'}</span>` +
      `<span class="me-name">${me ? shop.nameHtml(me.name) : 'Sign in'}</span>`;
    chip.title = me ? `Signed in as ${me.name}: your profile, password and sign out` : 'Sign in to bet and shop';
    accountMenuRefresh();
    if (!me) return;
    // The credits chip counts to a new balance.
    const was = shownBalance && shownBalance.name === me.name ? shownBalance.balance : null;
    const chipBalance = $('#me-credits b');
    if (chipBalance && was !== null && Math.abs(was - bal) >= 0.5) tickBalance(chipBalance, was, bal);
    shownBalance = { name: me.name, balance: bal };
    celebrateWins(me);
    window.FiveOnkey.settled(me);
    announceTransfers(me);
    announceTaxes(me);
    announceGiveaways(me);
  }

  // The daily wheel's reminder: while the signed-in bettor has a spin waiting (/api/bettor/me wheel_ready), a "Spin
  // ready" chip in the top bar and a glowing dot on the Casino menu and its Daily wheel entry. It's refreshed with every
  // redraw and the 20-second status poll, so it turns on at midnight Pacific and off once they spin. The chip hides on
  // the wheel itself.
  function wheelReady(me) {
    const ready = !!(me && me.wheel_ready);
    $('#me-wheel')?.classList.toggle('hidden', !ready || state.view === 'wheel');
    $('.nav-group[data-label="Casino"] .nav-trigger')?.classList.toggle('nav-alert', ready);
    $('.nav-menu a[data-view="wheel"]')?.classList.toggle('nav-alert', ready);
  }

  // A toast for credits the house gave this bettor (a secret objective met, a bad beat refunded) since this browser last
  // looked (fs.seenHouse.<name>: the newest payout id shown). The first visit only sets the marker.
  function announceGiveaways(me) {
    const key = `fs.seenHouse.${me.name.toLowerCase()}`, got = me.recent_giveaways || [];
    if (document.hidden) return;
    const newest = got.reduce((a, g) => Math.max(a, g.id), 0);
    let seen = null;
    try { seen = localStorage.getItem(key); localStorage.setItem(key, String(Math.max(newest, Number(seen) || 0))); }
    catch (e) { return; }
    if (seen === null) return;
    // The daily wheel shows its own prizes as they land, so only objectives, refunds and insurance get a toast.
    const fresh = got.filter((g) => g.id > Number(seen) && !['wheel', 'jackpot'].includes(g.kind));
    if (!fresh.length) return;
    const total = fresh.reduce((a, g) => a + g.amount, 0);
    const what = (g) => (g.kind === 'objective' ? `secret objective met: ${g.note}` : g.note);
    toast(fresh.length === 1 ? `The house paid you ${fmt.credits(total)} credits (${what(fresh[0])})`
      : `The house paid you ${fmt.credits(total)} credits for ${fresh.length} giveaways`, 'good');
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
  // The first visit on a device only sets the marker: old wins aren't celebrated. The confetti comes out of the
  // credits chip in the top bar, on any page, and in a background tab waits until the tab is visible again (browsers
  // pause animations in hidden tabs).
  let celebrateLater = false;
  function celebrateWins(me) {
    const key = `fs.seenWins.${me.name.toLowerCase()}`, wins = me.recent_wins || [];
    const newest = wins.reduce((a, w) => Math.max(a, w.settled_ts || 0), 0);
    let seen = null;
    try { seen = localStorage.getItem(key); } catch (e) { return; } // no storage: no way to tell what's new
    const save = () => { try { localStorage.setItem(key, String(newest)); } catch (e) { /* storage blocked */ } };
    if (seen === null) { save(); return; }
    const fresh = wins.filter((w) => (w.settled_ts || 0) > Number(seen));
    const origin = $('#me-credits b');
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
  // A burst of confetti from an element (the credits chip), falling under gravity and fading out.
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
  // The next-game forecast uses the odds board's map and agents (none since the pickers were removed), and the board itself is loaded
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
  // The Overview's extras: the latest game's recap (kept apart from the Matches tab's), the bettors' leaderboard, the
  // settled bets for the standouts, the open bets for "Riding on the next game", the odds board for the Next game
  // strip, and the troop and arcade boards for the Onkey's card.
  const loadOverview = async () => {
    const [recap, lb, won, lost, open, arcade] = await Promise.all([api('/api/recap').catch(() => null), api('/api/bettors').catch(() => null),
      api('/api/bets?status=won&limit=5000').catch(() => null), api('/api/bets?status=lost&limit=5000').catch(() => null),
      api('/api/bets?status=pending&limit=5000').catch(() => null), api('/api/arcade').catch(() => null),
      loadOdds().catch(() => null), window.FiveShop.loadTroop().catch(() => null)]);
    state.overviewRecap = recap;
    if (lb) state.bettors = lb.bettors;
    state.overviewBets = [...(won ? won.bets : []), ...(lost ? lost.bets : [])];
    state.overviewOpen = open ? open.bets : [];
    state.overviewArcade = arcade;
  };
  const loadMatches = async () => { state.matches = (await api('/api/matches?limit=400')).matches; };
  const loadRecap = async () => { state.recap = await api('/api/recap' + (state.recapId ? '?match=' + encodeURIComponent(state.recapId) : '')); };
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
      el.textContent = `${rec.wins}-${rec.losses}${rec.draws ? '-' + rec.draws : ''} as a ${stackWord()} · ${Math.round((rec.wins / rec.games) * 100)}% win rate`;
    } else {
      el.textContent = s.configured ? `No ${stackWord()} games tracked yet` : 'Setup needed';
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

  // A one-line explanation with the rest folded behind "How this works" (the full text is still one click away).
  const how = (summary, more) => `<details class="how"><summary>${summary}</summary><div class="how-body">${more}</div></details>`;
  // A bettor in small text (tile sub-lines, meta lines, notes): their colour swatch and plain name. nameHtml()'s
  // decorations (badge, title, prank marks, name colour) are for names shown at full size; see docs/DESIGN.md.
  const plainName = (name) => `<span class="swatch s${bettorSlot(name)}"></span>${esc(name)}`;
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
    return `<div class="card empty"><h2>No ${stackWord()} games yet</h2>` +
      `<p>${t.syncing ? 'Scanning everyone\'s match history now…' : 'Games where all five of you were on the same team will show up here after the next sync.'}</p>` +
      `<p class="muted small">Only Competitive games count. Play one together, then hit Sync now.</p></div>`;
  }

  // ---- overview -------------------------------------------------------------
  // The front page, sized to fit one 1920x1080 screen: three tiles (record, form, last night) and the next game's
  // odds on top, then a 3 x 2 grid of cards: the last game, betting and what's riding on the next game, then who's
  // trending, the maps and Onkey's. Full detail lives on Matches
  // (recaps), Place bets / Standings and Players; most things here click through to them.
  const OV_FORM = 10; // the Form tile's results
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
    // Form: the last OV_FORM results, oldest on the left, each opening its recap.
    const form = team.recent.slice(0, OV_FORM).reverse().map((g) =>
      `<a href="#matches" class="chip ${esc(g.result || '')}" data-recap-match="${esc(g.match_id)}" ` +
      `title="${esc(`${g.rounds_won}–${g.rounds_lost} on ${g.map || '?'}, ${fmt.date(g.started_at)}: open the recap`)}">${fmt.res(g.result)}</a>`).join('');
    const tiles = [
      kpi('Record', `${team.wins}–${team.losses}${team.draws ? `–${team.draws}` : ''}`, `${fmt.pct(team.win_rate)} win rate · since ${fmt.date(team.first_played)}`),
      kpi('Form', `<span class="ov-form">${form}</span>`, `Last ${Math.min(OV_FORM, team.recent.length)} games · streak ${team.streak || '–'}`),
      kpi('Last night', night.length ? `<span class="${nightW > nightL ? 'up' : nightW < nightL ? 'down' : ''}">${nightW}–${nightL}</span>` : '–',
        night.length ? `${nightDay} · ${night.length}${night.length === team.recent.length ? '+' : ''} game${night.length === 1 ? '' : 's'}` : ''),
    ];
    return `<section class="ov-top">${tiles.join('')}${overviewNext()}</section>
      <section class="ov-grid">${overviewLastGame(idx)}${overviewBetting()}${overviewRiding()}
        ${overviewTrending(st, idx)}${overviewMaps(team)}${overviewOnkeys()}</section>`;
  }

  // The next game: the match result and up to OV_HOT picks on a hot streak (won their last 3+ games at today's line),
  // as the Place bets buttons, so a click adds them to the slip. Picks likelier than OV_HOT_MAX are left out. Needs the odds board (loaded with the Overview).
  const OV_HOT = 2;
  const OV_HOT_MAX = 0.75; // a near-certain pick ("Comeback: No" at 1.02) isn't worth the space, streak or not
  function overviewNext() {
    const od = state.odds;
    if (!od || !od.ready) return '';
    const win = od.team.find((mk) => mk.market_id === 'team:win');
    if (!win) return '';
    const label = (mk, s) => (mk.type === 'ou' ? `${mk.member} ${mk.stat_label.toLowerCase()} ${s.label.toLowerCase()}` : `${mk.label}: ${s.label}`);
    const hot = [...od.team, ...od.player_props, ...od.top_markets]
      .filter((mk) => mk.market_id !== 'team:win')
      .flatMap((mk) => mk.selections.filter((s) => s.streak && s.fair_prob <= OV_HOT_MAX).map((s) => ({ mk, s })))
      .sort((a, b) => b.s.streak - a.s.streak || b.s.fair_prob - a.s.fair_prob)
      .slice(0, OV_HOT);
    const [w, l] = win.selections;
    return `<div class="tile ov-next"><div class="tile-label">Next game <a class="go-link" href="#odds">Place bets ›</a></div>
      <div class="ov-next-picks"><div class="ov-next-main">${oddBtn(win, w, w.label)}${oddBtn(win, l, l.label)}</div>` +
      (hot.length ? `<div class="ov-next-hot">${hot.map(({ mk, s }) => oddBtn(mk, s, label(mk, s))).join('')}</div>` : '') + `</div>
      <div class="tile-sub" id="ov-slip-note">${ovSlipNote()}</div></div>`;
  }
  const ovSlipNote = () => (state.slip.length
    ? `${state.slip.length} pick${state.slip.length === 1 ? '' : 's'} in your slip · <a href="#odds">Open the bet slip ›</a>`
    : `Tap odds to add them to your slip${state.odds && state.odds.team.some((mk) => mk.selections.some((s) => s.streak)) ? '; flames are picks that hit their last games in a row' : ''}`);

  // Map performance: win rate per map, best first, green at 50% or better and red under it. Maps with few games are
  // faded (a 0-2 isn't a verdict), and each row shows the average round difference and opens Matches filtered to it.
  const OV_MAP_FEW = 3;
  function overviewMaps(team) {
    const maps = [...team.by_map].sort((a, b) => b.win_rate - a.win_rate || b.games - a.games);
    const rows = maps.map((m) => {
      const few = m.games <= OV_MAP_FEW;
      return `<a href="#matches" class="ov-map${few ? ' few' : ''}" data-map-filter="${esc(m.map)}" ` +
        `title="${esc(`${m.map}: ${m.wins}-${m.losses} (${fmt.pct(m.win_rate)}), average round difference ${fmt.signed(m.avg_round_diff)}${few ? '. Only a few games.' : ''} Open these games on Matches.`)}">` +
        `<span class="ov-map-name">${esc(m.map)}</span>` +
        `<span class="hbar-track"><span class="hbar-fill ${m.win_rate >= 0.5 ? 'good' : 'bad'}" style="width:${Math.max(0, Math.min(100, Math.round(m.win_rate * 100)))}%"></span></span>` +
        `<span class="num">${fmt.pct(m.win_rate)}</span><span class="num muted">${m.wins}-${m.losses}</span>` +
        `<span class="num ${m.avg_round_diff > 0 ? 'up' : m.avg_round_diff < 0 ? 'down' : 'muted'}">${fmt.signed(m.avg_round_diff)}</span></a>`;
    }).join('');
    return `<section class="card"><div class="section-head"><h2>Map performance</h2><a class="go-link" href="#matches">Matches ›</a></div>
      <div class="ov-map ov-map-head" aria-hidden="true"><span>Map</span><span>Win rate, best first</span><span class="num">Win</span><span class="num">W-L</span><span class="num">Rounds</span></div>
      ${rows || '<p class="muted">No data yet.</p>'}</section>`;
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

  // The Betting card: who leads (one line, plus your own place when you're signed in and not first) and the standout
  // bets settled this season. The balance is in the top bar, and the full table on Standings.
  function overviewBetting() {
    const lb = state.bettors || [];
    const profit = (b) => `<span class="${b.profit > 0 ? 'up' : b.profit < 0 ? 'down' : 'muted'}">${fmt.signed(b.profit, 0)}</span>`;
    const lead = lb[0];
    // The leader as a banner: their name at full size (with their shop look) and their credits, profit underneath.
    const leader = lead
      ? `<a class="ov-lead" href="#bettors" title="Open the full standings"><div><div class="ov-lead-label">Leading the season</div>` +
        `<div class="ov-lead-name"><b>${window.FiveShop.nameHtml(lead.name)}</b></div>` +
        '</div>' +
        `<div class="ov-lead-num"><b>${fmt.credits(lead.balance)}</b> credits<div>${profit(lead)} profit</div></div></a>`
      : '<p class="muted small">No bettors yet.</p>';
    return `<section class="card ov-betting"><div class="section-head"><h2>Betting</h2><span class="head-links"><a class="go-link" href="#odds">Place bets ›</a><a class="go-link" href="#bettors">Standings ›</a></span></div>
      ${leader}
      ${overviewStandouts()}</section>`;
  }

  // Biggest win (most profit), biggest loss (biggest stake lost), longest odds won and shortest odds lost among the
  // bets settled this season (a season reset archives the bets table, so it holds only this season's), whoever placed
  // them. The bettor under each is a plain name (plainName), since the tile's sub-line is small text.
  function overviewStandouts() {
    const bets = (state.overviewBets || []).filter((b) => b.settled_match_id);
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
        `<div class="ov-tile-sub" title="${esc(`${b.bettor}: ${what(b)}`)}">${plainName(b.bettor)} · ${esc(what(b))}</div></div>`
      : `<div class="ov-tile empty"><div class="ov-tile-label">${label}</div><div class="ov-tile-value muted">–</div></div>`)).join('');
    return '<h3 class="ov-sub">This season</h3>' +
      (bets.length ? `<div class="ov-tiles">${tiles}</div>` : '<p class="muted small">No settled bets this season yet.</p>');
  }

  // Riding on the next game: every open bet (they all settle on the next 5-stack game), as the total staked, the pick
  // with the most credits behind it, and the biggest open bets. Bettors are plain names (small text).
  const OV_OPEN_BIG = 3;
  function overviewRiding() {
    const open = state.overviewOpen || [];
    const head = '<div class="section-head"><h2>Riding on the next game</h2><a class="go-link" href="#odds">Place bets ›</a></div>';
    if (!open.length) return `<section class="card">${head}<p class="muted">Nothing on the next game yet. <a href="#odds">Place the first bet ›</a></p></section>`;
    const staked = open.reduce((a, b) => a + b.stake, 0);
    const bettors = new Set(open.map((b) => b.bettor.toLowerCase())).size;
    const what = (b) => (b.market_type === 'parlay' ? `${(b.description || '').split(' + ').length}-leg parlay` : b.description || '');
    // The most-backed pick: singles only (a parlay leg isn't a stake on its own), grouped by market and selection.
    const picks = new Map();
    open.filter((b) => b.market_type !== 'parlay').forEach((b) => {
      const k = `${b.market_id}|${b.selection}`, p = picks.get(k) || { desc: what(b), stake: 0, who: new Set() };
      p.stake += b.stake; p.who.add(b.bettor.toLowerCase()); picks.set(k, p);
    });
    const top = [...picks.values()].sort((a, b) => b.stake - a.stake)[0];
    const big = [...open].sort((a, b) => b.stake - a.stake).slice(0, OV_OPEN_BIG).map((b) =>
      `<li title="${esc(`${b.bettor}: ${what(b)}`)}"><span class="ov-open-who">${plainName(b.bettor)}</span><span class="ov-open-what">${esc(what(b))}</span>` +
      `<span class="num"><b>${fmt.credits(b.stake)}</b> @ ${fmt.oddsDec(b.odds_decimal)}</span></li>`).join('');
    return `<section class="card">${head}
      <div class="ov-open-total"><b>${fmt.credits(staked)}</b> credits on ${open.length} open bet${open.length === 1 ? '' : 's'} <span class="muted">from ${bettors} bettor${bettors === 1 ? '' : 's'}</span></div>
      ${top ? `<div class="ov-open-top"><div class="ov-tile-label">Most backed</div><div class="ov-open-pick">${esc(top.desc)}</div>` +
        `<div class="ov-tile-sub">${fmt.credits(top.stake)} credits from ${top.who.size} bettor${top.who.size === 1 ? '' : 's'}</div></div>` : ''}
      <h3 class="ov-sub">Biggest bets</h3><ul class="ov-open-list">${big}</ul></section>`;
  }

  // Onkey's: the shop, the pranks and the arcade on one card. The top collector (bananas' worth of looks), the pranks
  // in play right now (newest first, OV_PRANKS of them), and each arcade game's best score. Names are plain (small text).
  const OV_PRANKS = 3;
  function overviewOnkeys() {
    const t = state.troop, arcade = state.overviewArcade;
    const head = '<div class="section-head"><h2>Onkey\'s</h2><span class="head-links"><a class="go-link" href="#shop">Shop ›</a><a class="go-link" href="#arcade">Arcade ›</a><a class="go-link" href="#monkeys">Monkeys ›</a></span></div>';
    if (!t) return `<section class="card">${head}<p class="muted">Onkey's is closed right now.</p></section>`;
    const display = new Map(t.troop.map((b) => [b.name.toLowerCase(), b.name]));
    const collector = [...t.troop].sort((a, b) => b.collection - a.collection)[0];
    const top = collector && collector.collection > 0
      ? `<a class="ov-onkey-top" href="#monkeys/${encodeURIComponent(collector.name)}"><div><div class="ov-tile-label">Top collector</div>` +
        `<div class="ov-onkey-name">${window.FiveShop.nameHtml(collector.name)}</div></div>` +
        `<div class="ov-onkey-num"><b>${window.FiveShop.bn(collector.collection)}</b> bananas of looks<div>${collector.items} item${collector.items === 1 ? '' : 's'}</div></div></a>`
      : '<p class="muted small">Nobody has bought anything yet.</p>';
    const pranks = Object.entries(t.looks || {}).flatMap(([who, l]) => (l.pranks || []).map((p) => ({ ...p, on: display.get(who) || who })))
      .sort((a, b) => b.id - a.id).slice(0, OV_PRANKS);
    const left = (p) => (p.games_left != null ? `${p.games_left} game${p.games_left === 1 ? '' : 's'} left` : `until ${new Date(p.expires_ts * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' })}`);
    const prankRows = pranks.map((p) => `<li title="${esc(`${p.by} on ${p.on}: ${p.name}${p.text ? ` "${p.text}"` : ''}`)}">` +
      `<span class="ov-prank-icon" aria-hidden="true">${esc(p.emoji || '')}</span>` +
      `<span class="ov-prank-what">${plainName(p.by)} <span class="muted">on</span> ${plainName(p.on)}: ${esc(p.name)}${p.text ? ` <span class="muted">“${esc(p.text)}”</span>` : ''}</span>` +
      `<span class="muted small">${left(p)}</span></li>`).join('');
    const games = arcade && arcade.games ? arcade.games.map((g) => {
      const best = (g.board || [])[0];
      return `<a class="ov-arcade" href="#arcade" title="${esc(g.name)}: ${best ? `best ${best.score} by ${best.bettor}` : 'no scores yet'}">` +
        `<div class="ov-tile-label">${esc(g.name)}</div><div class="ov-arcade-score">${best ? fmt.n0(best.score) : '–'}</div>` +
        `<div class="ov-tile-sub">${best ? plainName(best.bettor) : 'Nobody yet'}</div></a>`;
    }).join('') : '';
    return `<section class="card">${head}${top}
      <h3 class="ov-sub">Pranks in play</h3>${prankRows ? `<ul class="ov-pranks">${prankRows}</ul>` : '<p class="muted small">No pranks right now. <a href="#shop">Start one ›</a></p>'}
      ${games ? `<h3 class="ov-sub">Arcade bests</h3><div class="ov-arcades">${games}</div>` : ''}</section>`;
  }

  // Who's trending: each player's ACS over their last TREND_RECENT games against the TREND_BEFORE before them, as a
  // verdict (heating up / cooling off / steady at TREND_MIN_CHANGE), hottest first. The numbers are the last games'
  // averages, each with its change against the games before (grey under TREND_MIN_CHANGE; both averages in the
  // tooltip). The sparklines share one scale so the rows compare, with the compared
  // games shaded. A row opens that player on the Players tab.
  const TREND_VERDICT = { up: 'Heating up', down: 'Cooling off', flat: 'Steady' };
  function trendSpark(vals, lo, hi, { w = 110, h = 28 } = {}) {
    const pts = vals.map((v, i) => [i, v]).filter(([, v]) => isNum(v));
    if (pts.length < 2) return '';
    const pad = 3, n = vals.length;
    const x = (i) => pad + (i * (w - 2 * pad)) / Math.max(1, n - 1);
    const y = (v) => (hi === lo ? h / 2 : pad + (h - 2 * pad) * (1 - (v - lo) / (hi - lo)));
    const shadeFrom = x(Math.max(0, n - TREND_RECENT)) - 3;
    const d = pts.map(([i, v], k) => `${k ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    const [li, lv] = pts[pts.length - 1];
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="ACS over the last ${n} games">` +
      `<rect x="${shadeFrom.toFixed(1)}" y="0" width="${(w - shadeFrom).toFixed(1)}" height="${h}" rx="3" fill="var(--accent-soft)"/>` +
      `<path d="${d}" fill="none" stroke="var(--spark)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>` +
      `<circle cx="${x(li).toFixed(1)}" cy="${y(lv).toFixed(1)}" r="3.5" fill="var(--accent)"/></svg>`;
  }
  function overviewTrending(st, idx) {
    const members = st.members.filter((m) => m.overall.games)
      .sort((a, b) => ((trend(b, 'acs') || { rel: -9 }).rel) - ((trend(a, 'acs') || { rel: -9 }).rel));
    // One scale for every sparkline: the lowest and highest ACS among all the games shown.
    const series = new Map(members.map((m) => [m.puuid, statSeries(m, 'acs')]));
    const all = [...series.values()].flat().filter(isNum);
    const lo = Math.min(...all), hi = Math.max(...all);
    const value = (m, key, f, label) => {
      const tr = trend(m, key);
      if (!tr) return '<td class="num ov-c muted">–</td>';
      const d = tr.now - tr.then, cls = Math.abs(tr.rel) < TREND_MIN_CHANGE ? 'flat' : d > 0 ? 'up' : 'down';
      return `<td class="num ov-c" title="${label}: ${f(tr.now)} in the last ${TREND_RECENT} games vs ${f(tr.then)} in the ${TREND_BEFORE} before (${fmt.signed(Math.round(tr.rel * 100), 0)}%)"><b>${f(tr.now)}</b>` +
        `<span class="trend-delta ${cls}">${cls === 'flat' ? '' : d > 0 ? '▲ ' : '▼ '}${d >= 0 ? '+' : '−'}${f(Math.abs(d))}</span></td>`;
    };
    const rows = members.map((m) => {
      const tr = trend(m, 'acs');
      const dir = !tr ? null : Math.abs(tr.rel) < TREND_MIN_CHANGE ? 'flat' : tr.rel > 0 ? 'up' : 'down';
      const verdict = dir ? `<span class="trend-tag ${dir}" title="ACS ${fmt.n0(tr.now)} in the last ${TREND_RECENT} games vs ${fmt.n0(tr.then)} in the ${TREND_BEFORE} before (${fmt.signed(Math.round(tr.rel * 100), 0)}%)">${TREND_VERDICT[dir]}</span>` : '<span class="muted small">Too few games</span>';
      return `<tr class="ov-player" data-puuid="${esc(m.puuid)}" title="Open ${esc(m.nickname)} on the Players tab">` +
        `<th scope="row"><a href="#players" data-player-pick="${esc(m.puuid)}"><span class="swatch s${idx.get(m.puuid)?.slot || 1}"></span>${esc(m.nickname)}</a></th>` +
        `<td>${verdict}</td>${value(m, 'acs', fmt.n0, 'ACS')}${value(m, 'kd', fmt.n2, 'K/D')}` +
        `<td class="ov-spark">${trendSpark(series.get(m.puuid), lo, hi)}</td></tr>`;
    }).join('');
    const games = fmt.n0(Math.max(...members.map((m) => m.form.length)));
    return `<section class="card"><div class="section-head"><h2>Who's trending</h2><a class="go-link" href="#players">Players ›</a></div>
      <div class="table-wrap"><table class="ov-trending"><thead><tr><th>Player</th>` +
      `<th title="ACS in the last ${TREND_RECENT} games against the ${TREND_BEFORE} before them, hottest first">Last ${TREND_RECENT} games</th>` +
      `<th class="ov-c" title="Average ACS in the last ${TREND_RECENT} games">ACS</th><th class="ov-c" title="K/D in the last ${TREND_RECENT} games">K/D</th>` +
      `<th class="ov-spark" title="ACS in each of the last ${games} games, all players on one scale; shaded: the last ${TREND_RECENT}">ACS, last ${games}</th></tr></thead>` +
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
    if (!dv) return `<h3>Compared with their other games</h3><p class="muted small">No non-${stackWord()} games stored for this player yet.</p>`;
    const rows = dv.metrics.map((mt) =>
      `<tr><td>${esc(mt.label)} <span class="muted small">${mt.better === 'lower' ? '(lower is better)' : ''}</span></td>` +
      `<td class="num">${devValue(mt.key, mt.stack)}</td><td class="num">${devValue(mt.key, mt.usual)}</td>` +
      `<td class="num">${devDiff(mt)}</td><td>${devBadge(mt.verdict)}</td></tr>`).join('');
    const note = dv.enough ? '' : ` Differences are judged once both sides have ${dv.min_games} games.`;
    return `<h3>Compared with their other games</h3>
      <p class="muted small">${dv.stack_games} ${stackWord()} games vs. ${dv.usual_games} other games (${esc(dv.usual_modes.join(', ') || 'tracked modes')}).${note}</p>
      <div class="table-wrap"><table class="compact"><thead><tr><th>Stat</th><th class="num">${stackWord()}</th><th class="num">Other games</th><th class="num">Difference</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  // ---- players ----------------------------------------------------------------
  // The tab is one comparison table (a row per player, with how their 5-stack games compare with their other games)
  // and one player's detail below it, picked from the table or the buttons. Numbers that are the same for everyone
  // (games, win rate, win rate by map: every tracked game has all five) live on the Overview tab instead.
  // The detail used to show a "Compared with their other games" table and recent W/L chips; both are switched off,
  // set either to true to bring it back.
  const PLAYER_CARD_OTHER_GAMES = false;
  const PLAYER_CARD_FORM = false;
  // The comparison table's "5-stack vs. their other games" columns (ACS, K/D, win % and the biggest change against the
  // player's games outside the stack) are switched off too; set to true to bring them back.
  const PLAYER_TABLE_OTHER_GAMES = false;
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
      const vs = !PLAYER_TABLE_OTHER_GAMES ? '' : dv
        ? `<td class="num pl-split" title="${dv.usual_games} other games">${acs ? devDiff(acs) : '–'}</td><td class="num">${kd ? devDiff(kd) : '–'}</td>` +
          `<td class="num">${wr ? devDiff(wr) : '–'}</td><td>${so ? `${esc(so.label)} ${devBadge(so.verdict)}` : (acs ? devBadge(acs.verdict) : '<span class="muted">–</span>')}</td>`
        : '<td class="pl-split muted" colspan="4">No other games stored yet</td>';
      return `<tr class="pl-row${m.puuid === pick ? ' on' : ''}" data-puuid="${esc(m.puuid)}"><th scope="row"><span class="swatch s${slot}"></span>${esc(m.nickname)}</th>${cells}${vs}</tr>`;
    }).join('');
    return `<section class="card"><h2>Squad comparison</h2>
      ${how(`Per-game averages over the squad's ${fmt.n0(members[0].overall.games)} ${stackWord()} games. Click a row for that player's detail.`,
        `The best in each column is highlighted. ▲ / ▼ mark a stat trending up or down: the player's last ${TREND_RECENT} games against the ${TREND_BEFORE} before them, 5% or more apart (green is good; for deaths, fewer is good). Hover a number for the two figures.` +
        (PLAYER_TABLE_OTHER_GAMES ? ` <b>${stackWord()} vs. their other games</b> compares each player's ${stackWord()} games with their games outside the stack (solo queue or smaller parties): "better" or "worse" means the gap is about two standard errors or more, "slightly" one to two.` : ''))}
      <div class="table-wrap"><table class="pl-table"><thead>
        ${PLAYER_TABLE_OTHER_GAMES ? `<tr><th></th><th colspan="${PLAYER_COLS.length}" class="pl-group">This ${stackWord()}</th><th colspan="4" class="pl-group pl-split">${stackWord()} vs. their other games</th></tr>` : ''}
        <tr><th>Player</th>${PLAYER_COLS.map(([label]) => `<th class="num">${label}</th>`).join('')}${PLAYER_TABLE_OTHER_GAMES ? '<th class="num pl-split">ACS</th><th class="num">K/D</th><th class="num">Win %</th><th>Biggest change</th>' : ''}</tr>
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
      <p class="muted small">Per-game averages; the lines show the last ${m.form.length} games, and High / Low are their best and worst complete ${stackWord()} games (click one for its recap).</p>
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
  function oddBtn(mk, s, label, extra = '') { // extra: more classes for the button (e.g. 'win' / 'loss' to colour its label)
    const on = state.slip.some((x) => x.market_id === mk.market_id && x.selection === s.key);
    const runOf = (n) => (n >= state.odds.streak_lookback ? `${n}+` : n);
    const note = [s.boost ? `Odds boost: was ${fmt.odds({ decimal: s.boost.from_decimal, american: s.boost.from_american })}, singles up to ${fmt.credits(s.boost.max_stake)} credits` : '',
      s.streak ? `Hit ${runOf(s.streak)} games in a row` : s.cold ? `Missed ${runOf(s.cold)} games in a row` : ''].filter(Boolean).join(' · ');
    const cls = (s.streak ? ' hot' : s.cold ? ' cold' : '') + (s.boost ? ' boosted' : '') + (extra ? ` ${extra}` : '');
    return `<button class="odd ${on ? 'on' : ''}${cls}" data-m="${esc(mk.market_id)}" data-s="${esc(s.key)}" aria-pressed="${on}" title="${Math.round(s.fair_prob * 100)}% fair probability${note ? ` · ${note}` : ''}">` +
      `${label ? `<span>${esc(label)}</span>` : ''}<b>${fmt.odds(s)}</b>${note ? `<span class="sr-only">, ${note}</span>` : ''}</button>`;
  }

  // The line of fact under each team-market card (how often it happened, the streak, the final score's likeliest and
  // its overtime label). Off to keep the section short; set to true to bring them all back.
  const TM_FACTS = false;

  function viewOdds() {
    const od = state.odds, idx = memberIndex();
    // Only the odds format sits above the markets. The map and agent pickers are gone, so the odds always use every
    // game (state.ctx stays empty; the API still takes a map and agents).
    // The odds format, as a two-way toggle at the top of the sidebar, above the bet slip.
    const fmtBtn = (v, label) => `<button type="button" class="mode-btn ${state.oddsFormat === v ? 'on' : ''}" data-odds-fmt="${v}" aria-pressed="${state.oddsFormat === v}">${label}</button>`;
    const fmtBar = `<div class="odds-format"><span>Odds format</span><div class="slip-mode" role="group" aria-label="Odds format">${fmtBtn('american', 'American')}${fmtBtn('decimal', 'Decimal')}</div></div>`;
    if (!od.ready) {
      return `<div class="card empty"><h2>No odds yet</h2><p>${esc(od.message)}</p></div>` + betsSection();
    }
    // Team markets: the match result first (two big buttons either side of a bar split by each side's chance, centred,
    // with the last five results under its middle), then the other markets as question cards (with a line on how often, when TM_FACTS is on)
    // each has happened, then every final score in one row with a bar above each for how likely it is. A card's
    // details are in its question's tooltip; a market the board doesn't have yet (moments need 5 games with round
    // data) just isn't drawn.
    const tm = new Map(od.team.map((mk) => [mk.market_id, mk]));
    const share = (b) => (b && b.games ? fmt.pct(b.hits / b.games) : '–');
    const win = tm.get('team:win');
    let hero = '';
    if (win) {
      const [w, l] = win.selections, pw = w.fair_prob / (w.fair_prob + l.fair_prob);
      const recent = ((win.basis && win.basis.recent) || []).slice().reverse(); // oldest first, so the newest is on the right
      // The buttons sit either side of the middle column (bar plus the line under it), centred on it vertically.
      hero = `<div class="tm-match" title="${esc(win.desc)}">${oddBtn(win, w, w.label)}<div class="tm-mid">` +
        `<div class="tm-split" role="img" aria-label="${fmt.pct(pw)} chance to win"><span class="tm-split-win" style="width:${(pw * 100).toFixed(1)}%"></span></div>` +
        `<div class="tm-under"><span>${fmt.pct(pw)} to win</span>` +
        `<span class="tm-form" title="The last ${recent.length} games, oldest to newest">${recent.map((r) => `<span class="chip ${esc(r || '')}">${fmt.res(r)}</span>`).join('')}</span>` +
        `<span>${fmt.pct(1 - pw)} to lose</span></div></div>${oddBtn(win, l, l.label)}</div>`;
    }
    // A question card: the question, its picks (all of them by default) and, when TM_FACTS is on, one line of fact
    // under them (a pick on a streak says so instead).
    const qcard = (mk, question, fact, opts = {}) => {
      if (!mk) return '';
      const sels = opts.sels || mk.selections;
      const hot = sels.find((s) => s.streak);
      const line = hot ? `🔥 ${esc(hot.label)} in each of the last ${hot.streak} games` : fact;
      return `<div class="tm-q${opts.wide ? ' wide' : ''}"><div class="tm-question" title="${esc(mk.desc)}">${esc(question)}</div>` +
        `<div class="tm-answers" style="grid-template-columns:repeat(${sels.length}, minmax(0, 1fr))">` +
        `${sels.map((s) => oddBtn(mk, s, opts.label ? opts.label(s) : s.label, opts.cls ? opts.cls(s) : '')).join('')}</div>` +
        `${TM_FACTS ? `<div class="tm-fact">${line}</div>` : ''}</div>`;
    };
    const margin = tm.get('team:margin'), ot = tm.get('team:ot'), comeback = tm.get('team:comeback'), flawless = tm.get('team:flawless');
    const cards = [
      qcard(margin, 'By how much?', 'Overtime counts as 1–2 either way',
        { wide: true, label: (s) => s.label.replace(' by ', ' '), cls: (s) => (s.key.startsWith('l') ? 'loss' : 'win') }),
      qcard(tm.get('team:pistol'), 'Win the pistol?', `Round 1 won in ${share(tm.get('team:pistol')?.basis)} of games`),
      qcard(tm.get('team:half'), 'Ahead at half-time?', `Ahead after round 12 in ${share(tm.get('team:half')?.basis)} of games`),
      qcard(ot, 'Overtime?', ot && ot.basis ? `Reached 12–12 in ${ot.basis.hits} of ${ot.basis.games} games` : ''),
      qcard(tm.get('team:ace'), 'Anyone ace?', `An ace in ${share(tm.get('team:ace')?.basis)} of games`),
      qcard(comeback, 'Comeback from 5 down?', comeback ? `Happened ${comeback.basis.hits === 1 ? 'once' : `${comeback.basis.hits} times`} in ${comeback.basis.games} games` : ''),
      qcard(flawless, 'Flawless rounds?', flawless ? `${flawless.basis.mean} a game on average (won with nobody dying)` : ''),
    ].join('');
    const score = tm.get('team:score');
    let finalScore = '';
    if (score) {
      const peak = Math.max(...score.selections.map((s) => s.fair_prob)) || 1;
      const likeliest = score.selections.reduce((a, s) => (s.fair_prob > a.fair_prob ? s : a));
      const kind = (k) => (k.startsWith('ot') ? 'ot' : k.endsWith('-13') ? 'loss' : 'win');
      const short = (s) => ({ 'ot-loss': 'OT L', 'ot-win': 'OT W' }[s.key] || s.label);
      finalScore = `<div class="tm-q tm-final"><div class="tm-question" title="${esc(score.desc)}">Final score?</div>` +
        `<div class="tm-mountain">${score.selections.map((s) => `<div class="${kind(s.key)}${s === likeliest ? ' top' : ''}" title="${esc(s.label)}: ${fmt.pct(s.fair_prob)} chance">` +
        `<i style="height:${Math.max(3, Math.round((s.fair_prob / peak) * 54))}px"></i>${oddBtn(score, s, short(s))}</div>`).join('')}</div>` +
        `${TM_FACTS ? `<div class="tm-axis">overtime</div><div class="tm-fact">Likeliest: ${esc(likeliest.label)} (${fmt.pct(likeliest.fair_prob)}), outlined. Hover a bar for its chance.</div>` : ''}</div>`;
    }
    const team = hero + `<div class="tm-qgrid">${cards}${finalScore}</div>`;
    // The odds boost of the game (bets.py BOOST): one pick at a better price until the next game is recorded.
    let boostCard = '';
    const bo = od.boost;
    const boMarket = bo && [...od.team, ...od.player_props, ...od.top_markets].find((mk) => mk.market_id === bo.market_id);
    const boSel = boMarket && boMarket.selections.find((x) => x.key === bo.selection);
    if (boSel) {
      boostCard = `<section class="card boost-card"><h2>Odds boost of the game</h2>` +
        `<p class="boost-pick">${esc(bo.description)}</p>` +
        `<div class="boost-price"><s aria-label="usual price">${fmt.odds({ decimal: bo.from_decimal, american: bo.from_american })}</s>${oddBtn(boMarket, boSel, 'Boosted')}</div>` +
        `<p class="muted small">${fmt.pct(bo.pct)} more profit than the usual price, on singles up to ${fmt.credits(bo.max_stake)} credits. A new pick is boosted after every game.</p></section>`;
    }
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
    return `<div class="odds-layout"><div>
        <section class="card"><h2>Team markets</h2>${how('How the next game goes for the squad as a whole.', 'Margin and final score come from one model of the final score, so they agree with the match-result and overtime odds, and they cover every result: one pick wins each game. An overtime game counts as 1–2 for the margin and is its own final-score pick. The pistol, half-time, ace, comeback and flawless-round markets are read from each game\'s round-by-round record; if that record isn\'t available for the game played, those bets are refunded. On a surrender, the ones already decided settle and the rest are refunded.')}${team}</section>
        <section class="card"><h2>Player props · over / under</h2><p class="muted small">The line for the next game, then the odds for over (O) and under (U); tap one to add it to the slip.</p>
          <div class="table-wrap"><table class="props"><thead><tr><th>Player</th>${od.stat_defs.map((s) => `<th>${esc(s.label)}</th>`).join('')}</tr></thead><tbody>${propRows}</tbody></table></div></section>
        <section class="card"><h2>Top and bottom of the scoreboard</h2>${how('Pick who finishes first in a stat, or flip a card for who finishes last.', '"Popped off" and "Got diff\'d" rank everyone against their own average ACS instead of against each other, so anyone can win them. A tie refunds the stake.')}<div class="markets">${tops}</div></section>
        ${betsSection()}
      </div><aside class="odds-side">${fmtBar}${boostCard}<div class="slip card" id="slip">${slipHtml()}</div>${customLineCard()}${myBetsCard()}</aside></div>`;
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

  // ---- squad roster ----------------------------------------------------------------
  // The pool is everyone the tracker knows; the active squad (2 to 5 of them) is who it follows: games count when
  // every active player was on the same team. Rows drag between the Active squad and Bench tables (the buttons do
  // the same, for touch screens), the whole line-up is posted to /api/roster/active, and the history is re-derived.
  // New players join the bench; each signed-in bettor manages their own entry through /api/bettor/riot-id.
  const loadRoster = async () => { state.roster = await api('/api/roster'); };

  function viewSquad() {
    const s = state.status, me = state.me, roster = state.roster || {};
    const active = roster.members || s.members || [], bench = roster.bench || [];
    const ro = s.roster || { min: 2, max: 5, size: active.length, pool: active.length + bench.length, editable: false, admin_required: false };
    const idx = memberIndex();
    const mine = me && me.member ? me.member.puuid : null;
    const full = active.length >= ro.max, atMin = active.length <= ro.min, canEdit = ro.editable;
    const row = (m, isActive) => {
      const slot = isActive ? (idx.get(m.puuid)?.slot || 1) : 0;
      const own = m.puuid === mine;
      let actions = !canEdit ? '' : isActive
        ? `<button class="btn ghost small squad-swap" data-puuid="${esc(m.puuid)}" data-to="bench" ${atMin ? `disabled title="The squad needs at least ${ro.min} players"` : ''}>Bench</button> `
        : `<button class="btn ghost small squad-swap" data-puuid="${esc(m.puuid)}" data-to="active" ${full ? `disabled title="The squad is full (${ro.max}); bench someone first"` : ''}>Swap in</button> `;
      if (canEdit && own) {
        actions += `<button class="btn ghost small squad-edit" data-current="${esc(m.name)}#${esc(m.tag)}">Change Riot ID</button> ` +
          `<button class="btn ghost small roster-nick" data-puuid="${esc(m.puuid)}" data-nick="${esc(m.nickname)}" data-own="1" data-riot="${esc(m.name)}#${esc(m.tag)}">Nickname</button> ` +
          `<button class="btn ghost small squad-leave" ${isActive && atMin ? `disabled title="The squad needs at least ${ro.min} players"` : ''}>Remove me</button>`;
      } else if (canEdit) {
        if (!m.linked && me && !mine) actions += `<button class="btn ghost small squad-claim" data-riot="${esc(m.name)}#${esc(m.tag)}">This is me</button> `;
        actions += `<button class="btn ghost small roster-nick" data-puuid="${esc(m.puuid)}" data-nick="${esc(m.nickname)}">Nickname</button> ` +
          `<button class="btn ghost small roster-remove" data-puuid="${esc(m.puuid)}" data-name="${esc(m.name)}#${esc(m.tag)}" ${isActive && atMin ? `disabled title="The squad needs at least ${ro.min} players"` : ''}>${ro.admin_required ? 'Admin remove' : 'Remove'}</button>`;
      }
      return `<tr class="${own ? 'me' : ''}" ${canEdit ? 'draggable="true"' : ''} data-puuid="${esc(m.puuid)}" data-name="${esc(m.nickname)}">` +
        `<td>${canEdit ? '<span class="grip" aria-hidden="true" title="Drag to the other table">⋮⋮</span>' : ''}${slot ? `<span class="swatch s${slot}"></span>` : ''}<b>${esc(m.nickname)}</b>${own ? ' <span class="muted small">(you)</span>' : ''}</td>` +
        `<td>${esc(m.name)}#${esc(m.tag)}${m.previous_name ? `<div class="muted small">was ${esc(m.previous_name)}</div>` : ''}</td>` +
        `<td class="muted small">${m.linked ? esc(m.bettor) : `<span title="Nobody has claimed this entry from their betting account yet">${esc(m.bettor)} · unclaimed</span>`}</td>` +
        `<td class="muted small">${m.name_checked_ts ? fmt.ago(m.name_checked_ts) : 'not yet'}</td>` +
        `<td class="actions">${actions}</td></tr>`;
    };
    const head = '<thead><tr><th>Nickname</th><th>Riot ID</th><th>Account</th><th>Name checked</th><th></th></tr></thead>';
    const spots = Array.from({ length: Math.max(0, ro.max - active.length) }, () =>
      `<tr class="slot-empty"><td colspan="5">${canEdit ? 'Open spot: drag a player here, or Swap in from the bench' : 'Open spot'}</td></tr>`).join('');
    const activeTable = `<div class="table-wrap"><table class="roster">${head}<tbody class="drop-target" data-zone="active">${active.map((m) => row(m, true)).join('')}${spots}</tbody></table></div>`;
    const benchRows = bench.map((m) => row(m, false)).join('') ||
      `<tr class="slot-empty"><td colspan="5">${canEdit ? 'Nobody on the bench. Drag a player here to sit them out, or add one below.' : 'Nobody on the bench.'}</td></tr>`;
    const benchTable = `<div class="table-wrap"><table class="roster">${head}<tbody class="drop-target" data-zone="bench">${benchRows}</tbody></table></div>`;
    const why = s.demo ? 'Demo mode: the demo squad is fixed.' : !canEdit ? 'Add your HenrikDev API key first (see Setup, the ⚙ button).' : '';
    let joinCard;
    if (why) joinCard = `<p class="muted">${esc(why)}</p>`;
    else if (!me) joinCard = '<p class="muted">Sign in (the profile chip, top right) or create an account with your Riot ID to put yourself in the pool.</p>';
    else if (me.member) joinCard = `<p class="muted">You're ${me.member.active ? 'on the squad' : 'on the bench'} as <b>${esc(me.member.name)}#${esc(me.member.tag)}</b>. Use <em>Change Riot ID</em> on your row if you renamed or want another account counted, <em>Bench</em> / <em>Swap in</em> to sit out or play, or <em>Remove me</em> to leave the pool.</p>`;
    else joinCard = `<div class="roster-add"><label>Your Riot ID<input id="squad-join-riot" placeholder="Name#TAG" maxlength="40" autocomplete="off"></label>` +
        `<label>Nickname (optional)<input id="squad-join-nick" placeholder="What the squad calls you" maxlength="32" autocomplete="off" value="${esc(me.name)}"></label>` +
        `<button class="btn" id="squad-join">Join as ${esc(me.name)}</button></div>` +
        `<p class="muted small">Looked up on HenrikDev, so use your current Riot ID. You go straight onto the squad when there's a spot, otherwise onto the bench. Your rewards go to this account.</p>`;
    const adminCard = !canEdit ? '' :
      `<details class="how"><summary>Add someone else to the bench${ro.admin_required ? ' (admin)' : ''}</summary><div class="how-body">` +
      `<div class="roster-add"><label>Riot ID<input id="roster-riot" placeholder="Name#TAG" maxlength="40" autocomplete="off"></label>` +
      `<label>Nickname (optional)<input id="roster-nickname" placeholder="What the squad calls them" maxlength="32" autocomplete="off"></label>` +
      `<button class="btn" id="roster-add">Add</button></div>` +
      `<p class="muted small">For a player without a betting account. They land on the bench; drag them into the squad when they're playing. They can claim the entry later with <em>This is me</em> after signing in.</p></div></details>`;
    return `<section class="card"><h2>Active squad <span class="muted">${active.length} of ${ro.max}</span></h2>
      ${how(`Games count when every player in this table was on the same team. Between ${ro.min} and ${ro.max} players; drag rows in from the bench (or use the buttons) when the line-up changes.`,
        `<p>The tracker follows the active squad, whoever else they queue with. A game is recorded when all of them played it on the same team, so a squad of two counts every game those two played together and a squad of five only counts games with all five.</p>` +
        `<p>Everyone the tracker knows is in the pool; whoever isn't playing sits on the bench below. Changing the line-up re-derives the recorded games from the match lines already stored (no extra API calls): games the new player wasn't in stop counting, games the current squad all played start counting. A full re-scan then runs for anything only the full match records can prove. Open bets settle on the next recorded game as usual; rewards go to the account that owns each entry.</p>` +
        `<p>Your entry is yours: create your betting account with your Riot ID or add it later, change it if you rename or switch accounts, sit out or swap in, and remove it when you want out. Players are tracked by their Riot account, not their name; renames are picked up on their own (once a day, and on every sync when the API includes the name) and the old name is shown.</p>` +
        (ro.admin_required ? '<p>Changing the line-up, removing someone else or adding a player without an account asks for the admin password.</p>' : ''))}
      ${activeTable}
      ${canEdit ? '<div class="btn-row"><button class="btn ghost small" id="roster-refresh">Check for renames now</button></div>' : ''}</section>
      <section class="card"><h2>Bench <span class="muted">${bench.length}</span></h2>
      <p class="muted small">In the pool but not playing right now. Their games aren't tracked until they're swapped in.</p>
      ${benchTable}</section>
      <section class="card"><h2>Your place in the pool</h2>${joinCard}${adminCard}</section>`;
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
        <li>Region: ${esc(s.region || '–')} · Competitive games only · Poll every ${esc(s.poll_interval_minutes)} min</li>
        <li>Squad: ${(s.members || []).length} player${(s.members || []).length === 1 ? '' : 's'} (<a href="#squad">manage on the Squad tab</a>)</li>
        <li>Last sync: ${t.last_sync ? fmt.ago(t.last_sync) : 'never'}${r ? ` · ${r.new_matches} new game(s), ${r.candidates} candidates checked, ${r.api_calls} API calls` : ''}${t.last_error ? ` · <span class="down">${esc(t.last_error)}</span>` : ''}</li>
        <li>Rate limit: ${rl.remaining != null ? `${rl.remaining} of ${rl.limit} requests left in the current window` : 'unknown until the first request'}</li>
        <li>${stackWord()} games stored: ${s.games}</li></ul>
        <div class="btn-row"><button class="btn" id="sync-now" ${!s.configured || s.demo ? 'disabled' : ''}>Sync now</button><button class="btn ghost" id="sync-full" ${!s.configured || s.demo ? 'disabled' : ''}>Full re-scan</button></div></section>
      ${onlineCard}
      <section class="card"><h2>Squad</h2>${members ? `<ul class="plain">${members}</ul>` : '<p class="muted">Nobody on the squad yet.</p>'}<p class="muted small">Add or remove players on the <a href="#squad">Squad tab</a>.</p></section>
      <section class="card"><h2>How to set up</h2><ol>
        <li>Get a free HenrikDev API key: open <a href="https://api.henrikdev.xyz/dashboard/" target="_blank" rel="noopener">api.henrikdev.xyz/dashboard</a>, sign in with Discord, and generate a <em>Basic</em> key.</li>
        <li>Open <code>config.json</code> next to <code>server.py</code>. Paste the key into <code>api_key</code>, set your <code>region</code>, and (optionally) list your squad's Riot IDs (Name#TAG) under <code>members</code> to seed the squad; the Squad tab manages it from then on.</li>
        <li>Restart the server (<code>python server.py</code> or <code>run.bat</code>). The first sync scans everyone's stored match history and keeps only games where everyone on the squad was on the same team.</li>
        <li>Leave it running. It re-checks every few minutes, records new ${stackWord()} games and settles open bets automatically. Set <code>host</code> to <code>0.0.0.0</code> to let friends on your network open it too.</li></ol>
        <p class="muted small">Only Competitive games count; every other mode is skipped. HenrikDev's stored history can have gaps; a game that is missing for one player is verified through the full match record.</p></section>
      <section class="card"><h2>Sync log</h2><div class="log">${log || '<span class="muted">Nothing yet.</span>'}</div></section>`;
  }

  // ---- render & events ---------------------------------------------------------------
  function draw() {
    const view = $('#view');
    try {
      switch (state.view) {
        case 'overview': view.innerHTML = viewOverview(); break;
        case 'players': view.innerHTML = viewPlayers(); break;
        case 'squad': view.innerHTML = viewSquad(); break;
        case 'forecasts': view.innerHTML = viewForecasts(); break;
        case 'viz': view.innerHTML = viewViz(); break;
        case 'odds': view.innerHTML = viewOdds(); break;
        case 'bettors': view.innerHTML = viewBettors(); break;
        case 'shop': view.innerHTML = window.FiveShop.viewShop(); break;
        case 'troop': view.innerHTML = window.FiveShop.viewTroop(); break;
        case 'arcade': view.innerHTML = window.FiveArcade.viewArcade(); break;
        case 'slots': view.innerHTML = window.FiveSlots.view(); break;
        case 'blackjack': view.innerHTML = window.FiveBlackjack.view(); break;
        case 'poker': view.innerHTML = window.FivePoker.view(); break;
        case 'wheel': view.innerHTML = window.FiveWheel.view(); break;
        case 'hunt': view.innerHTML = window.FiveHunt.view(); break;
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
        case 'squad': await Promise.all([loadStatus(), loadMe(), loadRoster()]); break;
        case 'forecasts': await loadForecasts(); break;
        case 'viz': await loadInsights(); break;
        case 'odds': await Promise.all([loadOdds(), loadBets()]); break;
        case 'bettors': await Promise.all([loadBets(), loadBettingReport(), loadSeasons()]); break;
        case 'shop': await Promise.all([window.FiveShop.loadShop(), window.FiveShop.loadTroop()]); break;
        case 'troop': await Promise.all([window.FiveShop.loadTroop(), window.FiveShop.loadProfile(), state.shop ? null : window.FiveShop.loadShop()]); break;
        case 'arcade': await window.FiveArcade.load(); break;
        case 'slots': await window.FiveSlots.load(); break;
        case 'blackjack': await window.FiveBlackjack.load(); break;
        case 'poker': await window.FivePoker.load(); break;
        case 'wheel': await window.FiveWheel.load(); break;
        case 'hunt': await window.FiveHunt.load(); break;
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
    $$('[data-odds-fmt]').forEach((b) => b.addEventListener('click', () => {
      if (state.oddsFormat === b.dataset.oddsFmt) return;
      state.oddsFormat = b.dataset.oddsFmt; localStorage.setItem('fs.oddsFormat', state.oddsFormat); draw();
    }));
    $$('.top-dir', view).forEach((b) => b.addEventListener('click', () => { state.topDir[b.dataset.pair] = b.dataset.dir; draw(); }));
    window.FiveBets.bind(view);
    // Overview: the Next game strip's note follows the slip; a trending row opens that player, a map row its games.
    $$('.ov-next button.odd', view).forEach((b) => b.addEventListener('click', () => { const n = $('#ov-slip-note'); if (n) n.innerHTML = ovSlipNote(); }));
    $$('tr.ov-player', view).forEach((r) => r.addEventListener('click', () => { state.playerPick = r.dataset.puuid; location.hash = '#players'; }));
    $$('[data-player-pick]', view).forEach((a) => a.addEventListener('click', (e) => { e.stopPropagation(); state.playerPick = a.dataset.playerPick; }));
    $$('[data-map-filter]', view).forEach((a) => a.addEventListener('click', () => {
      state.matchFilter = { map: a.dataset.mapFilter, result: '', mode: '' };
      state.recapId = '';
    }));
    window.FiveShop.bind(view);
    window.FiveArcade.bind(view);
    window.FiveSlots.bind(view);
    window.FiveBlackjack.bind(view);
    window.FivePoker.bind(view);
    window.FiveWheel.bind(view);
    window.FiveHunt.bind(view);
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
    // Squad tab: line-up, add, remove, nickname, rename check. Edits ask for the admin password when one is set.
    const squadReload = async () => {
      await Promise.all([loadStatus(), loadMe(), state.view === 'squad' ? loadRoster() : Promise.resolve()]);
      draw();
      if (state.status.tracker?.syncing) pollUntilIdle();
    };
    const rosterCall = async (path, opts, done) => {
      const headers = {};
      if (state.status.roster && state.status.roster.admin_required) {
        const pw = window.prompt('Admin password');
        if (pw == null) return;
        headers['X-Admin-Password'] = pw;
      }
      try {
        const r = await api(path, { ...opts, headers });
        toast(done(r), 'good');
        await squadReload();
      } catch (e) {
        toast(e.message, 'bad');
      }
    };
    // The line-up: move one player onto the squad (before `before`, if given) or onto the bench, then post the
    // whole active list. Drag and drop and the Bench / Swap in buttons both end up here.
    const applyLineup = (puuid, toActive, before) => {
      const ro = state.status.roster || { min: 2, max: 5 };
      const current = ((state.roster && state.roster.members) || state.status.members || []).map((m) => m.puuid);
      const next = current.filter((p) => p !== puuid);
      if (toActive) {
        const at = before ? next.indexOf(before) : -1;
        if (at >= 0) next.splice(at, 0, puuid); else next.push(puuid);
      }
      if (next.length < ro.min) { toast(`The squad needs at least ${ro.min} players; swap someone in first`, 'bad'); return; }
      if (next.length > ro.max) { toast(`The squad is full (${ro.max}); bench someone first`, 'bad'); return; }
      if (next.join() === current.join()) return;
      rosterCall('/api/roster/active', { method: 'POST', body: JSON.stringify({ puuids: next }) },
        (r) => (r.changed ? 'Line-up changed. Re-checking the history…' : 'Order saved'));
    };
    $$('.squad-swap', view).forEach((b) => b.addEventListener('click', () => applyLineup(b.dataset.puuid, b.dataset.to === 'active')));
    let dragging = null;
    $$('tr[draggable="true"]', view).forEach((tr) => {
      tr.addEventListener('dragstart', (e) => {
        dragging = tr.dataset.puuid;
        tr.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        try { e.dataTransfer.setData('text/plain', dragging); } catch (err) { /* not supported */ }
      });
      tr.addEventListener('dragend', () => { tr.classList.remove('dragging'); $$('.drop-target.drag-over', view).forEach((z) => z.classList.remove('drag-over')); });
    });
    $$('.drop-target', view).forEach((zone) => {
      zone.addEventListener('dragover', (e) => { if (!dragging) return; e.preventDefault(); e.dataTransfer.dropEffect = 'move'; zone.classList.add('drag-over'); });
      zone.addEventListener('dragleave', (e) => { if (!zone.contains(e.relatedTarget)) zone.classList.remove('drag-over'); });
      zone.addEventListener('drop', (e) => {
        e.preventDefault();
        zone.classList.remove('drag-over');
        const puuid = dragging || e.dataTransfer.getData('text/plain');
        dragging = null;
        if (!puuid) return;
        const over = e.target.closest('tr[data-puuid]');
        const before = over && over.dataset.puuid !== puuid ? over.dataset.puuid : undefined;
        applyLineup(puuid, zone.dataset.zone === 'active', before);
      });
    });
    $('#roster-add')?.addEventListener('click', () => {
      const riot = ($('#roster-riot')?.value || '').trim(), nickname = ($('#roster-nickname')?.value || '').trim();
      if (!riot.includes('#')) { toast('Enter a Riot ID like Name#TAG', 'bad'); return; }
      rosterCall('/api/roster', { method: 'POST', body: JSON.stringify({ riot_id: riot, nickname }) }, (r) =>
        r.added ? `${r.member.nickname} added to the bench. Drag them into the squad when they're playing.`
          : r.renamed ? `${r.member.nickname} is already in the pool; updated to ${r.member.name}#${r.member.tag}`
          : `${r.member.nickname} is already in the pool`);
    });
    $('#roster-riot')?.addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#roster-add')?.click(); });
    $$('.roster-remove', view).forEach((b) => b.addEventListener('click', () => {
      if (!window.confirm(`Remove ${b.dataset.name} from the pool? If they're on the squad, games that needed them stop counting.`)) return;
      rosterCall('/api/roster/' + encodeURIComponent(b.dataset.puuid), { method: 'DELETE' }, (r) => (r.changes && r.changes.was_active ? 'Removed. Re-checking the history…' : 'Removed from the bench'));
    }));
    $$('.roster-nick', view).forEach((b) => b.addEventListener('click', () => {
      const nick = window.prompt('Nickname', b.dataset.nick);
      if (nick == null) return;
      if (b.dataset.own) {  // your own entry: no admin password needed
        selfCall('/api/bettor/riot-id', { method: 'POST', body: JSON.stringify({ riot_id: b.dataset.riot, nickname: nick }) }, (r) => `Nickname set to ${r.member.nickname}`);
        return;
      }
      rosterCall('/api/roster/' + encodeURIComponent(b.dataset.puuid), { method: 'POST', body: JSON.stringify({ nickname: nick }) }, (r) => `Nickname set to ${r.member.nickname}`);
    }));
    // Your own entry (no admin password): join, change Riot ID, claim an unclaimed entry, leave.
    const selfCall = async (path, opts, done) => {
      try {
        const r = await api(path, opts);
        toast(done(r), 'good');
        await squadReload();
      } catch (e) {
        toast(e.message, 'bad');
      }
    };
    const joinMsg = (r) => r.added ? (r.active ? `You're on the squad as ${r.member.name}#${r.member.tag}. Re-checking the history…`
        : `You're in the pool as ${r.member.name}#${r.member.tag}, on the bench: the squad is full. Swap in when a spot opens.`)
      : r.replaced ? `Switched to ${r.member.name}#${r.member.tag}.${r.changes ? ' Re-checking the history…' : ''}`
      : r.renamed ? `Updated to ${r.member.name}#${r.member.tag}` : `${r.member.name}#${r.member.tag} is yours`;
    $('#squad-join')?.addEventListener('click', () => {
      const riot = ($('#squad-join-riot')?.value || '').trim(), nickname = ($('#squad-join-nick')?.value || '').trim();
      if (!riot.includes('#')) { toast('Enter your Riot ID like Name#TAG', 'bad'); return; }
      selfCall('/api/bettor/riot-id', { method: 'POST', body: JSON.stringify({ riot_id: riot, nickname }) }, joinMsg);
    });
    $('#squad-join-riot')?.addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#squad-join')?.click(); });
    $$('.squad-edit', view).forEach((b) => b.addEventListener('click', () => {
      const riot = window.prompt('Your Riot ID (Name#TAG)', b.dataset.current);
      if (riot == null) return;
      if (!riot.includes('#')) { toast('Enter a Riot ID like Name#TAG', 'bad'); return; }
      selfCall('/api/bettor/riot-id', { method: 'POST', body: JSON.stringify({ riot_id: riot.trim() }) }, joinMsg);
    }));
    $$('.squad-claim', view).forEach((b) => b.addEventListener('click', () => {
      if (!window.confirm(`Claim ${b.dataset.riot} as your Riot ID? Rewards for its games go to your account from now on.`)) return;
      selfCall('/api/bettor/riot-id', { method: 'POST', body: JSON.stringify({ riot_id: b.dataset.riot }) }, joinMsg);
    }));
    $$('.squad-leave', view).forEach((b) => b.addEventListener('click', () => {
      if (!window.confirm('Remove your Riot ID from the pool? If you\'re on the squad, games that needed you stop counting and your rewards stop.')) return;
      selfCall('/api/bettor/riot-id', { method: 'DELETE' }, (r) => (r.changes && r.changes.was_active ? 'You left the squad. Re-checking the history…' : 'Removed your Riot ID from the pool'));
    }));
    $('#roster-refresh')?.addEventListener('click', async () => {
      try {
        const r = await api('/api/roster/refresh', { method: 'POST', body: '{}' });
        toast(r.renamed.length ? r.renamed.map((x) => `${x.from} → ${x.to}`).join(', ') : 'No renames found', 'good');
        await squadReload();
      } catch (e) { toast(e.message, 'bad'); }
    });
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
  const NAV_ICONS = false;

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
    // #casino opens Slots.
    let [v, arg] = (location.hash || '#overview').slice(1).split('/');
    if (v === 'monkeys') v = 'troop';
    if (v === 'casino') v = 'slots'; // the Casino menu's first page
    state.view = VIEWS.includes(v) ? v : 'overview';
    window.FiveOnkey?.note('view', { view: state.view });
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
  // The account menu under the profile chip (top right, every page). Signed out: name, betting password, Sign in and
  // Create account. Signed in: your profile, Change password and Sign out. Anything with data-signin (the bet slip's
  // "Sign in to place", the shop's "Sign in to buy", ...) opens it too.
  let accountOpen = false;
  function accountMenuHtml() {
    const me = state.me;
    if (me) {
      return `<div class="acct-head">Signed in as <b>${window.FiveShop.nameHtml(me.name)}</b></div>` +
        `<a class="acct-item" role="menuitem" href="#monkeys/${encodeURIComponent(me.name)}">Your profile</a>` +
        // Your place on the squad (the Squad page has the same controls): your Riot ID, or a way to add it.
        (me.member
          ? `<div class="acct-head muted small">${me.member.active ? 'On the squad' : 'On the bench'} as <b>${esc(me.member.name)}#${esc(me.member.tag)}</b></div>` +
            (me.member.active
              ? '<button type="button" class="acct-item" role="menuitem" id="bettor-riot-active" data-active="0">Sit out (move to the bench)</button>'
              : '<button type="button" class="acct-item" role="menuitem" id="bettor-riot-active" data-active="1">Join the squad</button>') +
            `<button type="button" class="acct-item" role="menuitem" id="bettor-riot-edit" data-current="${esc(me.member.name)}#${esc(me.member.tag)}">Change Riot ID</button>` +
            '<button type="button" class="acct-item" role="menuitem" id="bettor-riot-leave">Remove my Riot ID</button>'
          : '<button type="button" class="acct-item" role="menuitem" id="bettor-riot-edit" data-current="">Join the squad with your Riot ID</button>') +
        '<button type="button" class="acct-item" role="menuitem" id="bettor-password">Change password</button>' +
        '<button type="button" class="acct-item" role="menuitem" id="bettor-signout">Sign out</button>';
    }
    return '<form class="acct-form" id="acct-form"><label>Name<input id="bettor-name" placeholder="Your name" ' +
      `value="${esc(state.bettor)}" autocomplete="username" maxlength="32"></label>` +
      '<label>Betting password<input id="bettor-pass" type="password" placeholder="Yours alone, not the site password" autocomplete="current-password"></label>' +
      '<label>Riot ID <span class="muted">(optional, new accounts)</span><input id="bettor-riot" placeholder="Name#TAG, to be on the squad" maxlength="40" autocomplete="off"></label>' +
      '<div class="btn-row"><button class="btn primary small" id="bettor-signin">Sign in</button><button type="button" class="btn ghost small" id="bettor-register">Create account</button></div>' +
      `<p class="muted small">Your own password, so nobody can bet or cancel under your name. New accounts start with ${fmt.credits(state.status.starting_balance)} credits; add your Riot ID and you're on the squad, so your games count and your rewards land here.</p></form>`;
  }
  function accountMenu(open) {
    const menu = $('#account-menu'), chip = $('#me-chip');
    accountOpen = open;
    menu.classList.toggle('hidden', !open);
    chip.setAttribute('aria-expanded', String(open));
    if (!open) return;
    closeNavMenus();
    // Right-aligned under the chip (the menu sits in .topbar-right, which is position: relative).
    menu.style.right = `${Math.max(0, menu.parentElement.getBoundingClientRect().right - chip.getBoundingClientRect().right)}px`;
    menu.innerHTML = accountMenuHtml();
    bindAccount(menu);
    const name = $('#bettor-name', menu);
    (name ? (name.value ? $('#bettor-pass', menu) : name) : $('.acct-item', menu)).focus();
  }
  // After a sign-in or sign-out the menu shows the other side; a closed menu stays closed.
  function accountMenuRefresh() {
    if (!accountOpen) return;
    const menu = $('#account-menu');
    if (menu.contains(document.activeElement) && $('#bettor-name', menu)) return; // don't wipe what's being typed
    menu.innerHTML = accountMenuHtml();
    bindAccount(menu);
  }
  async function bettorSession(path) {
    const name = ($('#bettor-name')?.value || '').trim();
    const password = $('#bettor-pass')?.value || '';
    const registering = path.endsWith('register');
    const riot = registering ? ($('#bettor-riot')?.value || '').trim() : '';
    if (!name) { toast('Enter your name', 'bad'); return; }
    if (!password) { toast('Enter your betting password', 'bad'); return; }
    if (riot && !riot.includes('#')) { toast('Enter a Riot ID like Name#TAG, or leave it empty', 'bad'); return; }
    try {
      const r = await api(path, { method: 'POST', body: JSON.stringify(registering ? { name, password, riot_id: riot } : { name, password }) });
      state.bettor = r.bettor.name;
      localStorage.setItem('fs.bettor', state.bettor);
      if (r.warning) toast(r.warning, 'bad');
      else if (registering) toast(r.member ? `Welcome, ${r.bettor.name}. You're on the squad as ${r.member.name}#${r.member.tag}; re-checking the history…` : `Account created. Welcome, ${r.bettor.name}.`, 'good');
      else toast(`Signed in as ${r.bettor.name}`, 'good');
      accountMenu(false);
      await Promise.all([loadStatus(), loadBets()]);
      draw();
    } catch (e) {
      toast(e.message, 'bad');
    }
  }

  // Set or change the signed-in bettor's Riot ID (their squad entry), or leave the squad; the server re-checks the
  // history. The Squad page offers the same through its own buttons.
  async function changeRiotId(current) {
    const riot = window.prompt('Your Riot ID (Name#TAG). Games count when everyone on the squad is on the same team.', current || '');
    if (riot == null) return;
    if (!riot.includes('#')) { toast('Enter a Riot ID like Name#TAG', 'bad'); return; }
    try {
      const r = await api('/api/bettor/riot-id', { method: 'POST', body: JSON.stringify({ riot_id: riot.trim() }) });
      toast(r.added ? (r.active ? `You're on the squad as ${r.member.name}#${r.member.tag}. Re-checking the history…`
          : `You're in the pool as ${r.member.name}#${r.member.tag}, on the bench: the squad is full.`)
        : r.replaced ? `Switched to ${r.member.name}#${r.member.tag}.${r.changes ? ' Re-checking the history…' : ''}`
        : r.renamed ? `Updated to ${r.member.name}#${r.member.tag}` : `${r.member.name}#${r.member.tag} is yours`, 'good');
      await Promise.all([loadStatus(), loadBets(), loadMe()]);
      if (state.view === 'squad') await loadRoster();
      draw();
    } catch (e) { toast(e.message, 'bad'); }
  }

  async function leaveSquad() {
    if (!window.confirm('Remove your Riot ID from the pool? If you\'re on the squad, games that needed you stop counting and your rewards stop.')) return;
    try {
      const r = await api('/api/bettor/riot-id', { method: 'DELETE' });
      toast(r.changes && r.changes.was_active ? 'You left the squad. Re-checking the history…' : 'Removed your Riot ID from the pool', 'good');
      await Promise.all([loadStatus(), loadBets(), loadMe()]);
      if (state.view === 'squad') await loadRoster();
      draw();
    } catch (e) { toast(e.message, 'bad'); }
  }

  // Sit out (bench) or join the squad from your own entry, without touching anyone else's.
  async function toggleOwnActive(active) {
    try {
      const r = await api('/api/bettor/riot-id/active', { method: 'POST', body: JSON.stringify({ active }) });
      toast(active ? "You're on the squad. Re-checking the history…" : "You're on the bench. Re-checking the history…", 'good');
      await Promise.all([loadStatus(), loadBets(), loadMe()]);
      if (state.view === 'squad') await loadRoster();
      draw();
      if (r.changed && state.status.tracker?.syncing) pollUntilIdle();
    } catch (e) { toast(e.message, 'bad'); }
  }
  function bindAccount(menu) {
    $('#acct-form', menu)?.addEventListener('submit', (e) => { e.preventDefault(); bettorSession('/api/bettor/login'); });
    $('#bettor-register', menu)?.addEventListener('click', () => bettorSession('/api/bettor/register'));
    $('#bettor-riot-edit', menu)?.addEventListener('click', (e) => { accountMenu(false); changeRiotId(e.currentTarget.dataset.current); });
    $('#bettor-riot-leave', menu)?.addEventListener('click', () => { accountMenu(false); leaveSquad(); });
    $('#bettor-riot-active', menu)?.addEventListener('click', (e) => { accountMenu(false); toggleOwnActive(e.currentTarget.dataset.active === '1'); });
    $('#bettor-signout', menu)?.addEventListener('click', async () => {
      accountMenu(false);
      try { await api('/api/bettor/logout', { method: 'POST', body: '{}' }); } catch (e) { /* cookie is cleared anyway */ }
      await loadBets();
      draw();
    });
    $('#bettor-password', menu)?.addEventListener('click', async () => {
      accountMenu(false);
      const old = window.prompt('Current betting password');
      if (old == null) return;
      const nw = window.prompt('New betting password (4 to 64 characters)');
      if (nw == null) return;
      try { await api('/api/bettor/password', { method: 'POST', body: JSON.stringify({ old, new: nw }) }); toast('Password changed', 'good'); }
      catch (e) { toast(e.message, 'bad'); }
    });
    $$('a.acct-item', menu).forEach((a) => a.addEventListener('click', () => accountMenu(false)));
  }
  function bindAccountMenu() {
    const menu = $('#account-menu'), chip = $('#me-chip');
    chip.addEventListener('click', () => accountMenu(!accountOpen));
    menu.addEventListener('keydown', (e) => { if (e.key === 'Escape') { accountMenu(false); chip.focus(); } });
    document.addEventListener('click', (e) => {
      const ask = e.target.closest('[data-signin]');
      if (ask) {
        e.preventDefault();
        window.FiveShop.closePreview(); // the shop's preview window would cover the menu
        accountMenu(true);
        return;
      }
      if (accountOpen && !e.target.closest('#account-menu, #me-chip')) accountMenu(false);
    });
  }

  async function init() {
    const shop = window.FiveShop;
    shop.init({ state, $, $$, api, draw, esc, fmt, kpi, toast, confetti, onShop: () => checkTheme() });
    window.FiveBets.init({ state, $, $$, api, bettorSlot, draw, esc, fmt, kpi, memberIndex, plainName, toast, nameHtml: shop.nameHtml, ticketClass: shop.ticketClass, ticketExtras: shop.ticketExtras });
    window.FiveArcade.init({ state, $, $$, api, draw, esc, fmt, toast, nameHtml: shop.nameHtml, loadMe });
    window.FiveSlots.init({ state, $, $$, api, draw, esc, fmt, loadMe, confetti, plainName, holdBalance, releaseBalance, displayBalance });
    window.FiveCasino.init({ esc, state, nameHtml: shop.nameHtml, confetti });
    window.FiveBlackjack.init({ state, $, api, draw, esc, fmt, loadMe, confetti, plainName });
    window.FivePoker.init({ state, $, api, draw, esc, fmt, loadMe, confetti, plainName });
    window.FiveWheel.init({ state, $, $$, api, draw, esc, fmt, loadMe, confetti, plainName, toast, holdBalance, releaseBalance });
    window.FiveHunt.init({ state, $, api, draw, esc, fmt, kpi, renderMe, toast, plainName });
    window.FiveOnkey.init({ state, fmt, esc });
    window.FiveOnkey.start();
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
    bindAccountMenu();
    window.addEventListener('hashchange', route);
    try {
      await Promise.all([loadStatus(), loadMe(), shop.loadTroop().catch(() => {})]); // the troop's looks style names everywhere
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
