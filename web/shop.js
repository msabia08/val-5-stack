/* 5-Stack Tracker: Onkey's Shop. The Shop tab (catalogue, preview window, social items), the Monkeys tab (every
 * bettor's bananas and collection, and one bettor's profile), and the name styling the rest of the site uses to show
 * what people bought (nameHtml / ticketClass).
 *
 * Bananas are earned alongside credits (fivestack/bananas.py) and only buy looks and pranks: nothing here touches
 * credits, bets or the leaderboard. app.js calls FiveShop.init() once with its shared helpers, like FiveBets.
 * Loaded before bets.js and app.js; plain JS, no dependencies.
 */
window.FiveShop = (() => {
  'use strict';

  let state, $, $$, api, draw, esc, fmt, kpi, toast, confetti, onShop; // onShop: app.js re-checks the theme
  const how = (summary, more) => `<details class="how"><summary>${summary}</summary><div class="how-body">${more}</div></details>`;
  const BANANA = '🍌';

  function init(ctx) {
    ({ state, $, $$, api, draw, esc, fmt, kpi, toast, confetti, onShop } = ctx);
    state.shopSlot ||= 'all';
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && $('#shop-modal')) closePreview(); });
  }

  // Bananas are shown to one decimal only when they have one (12.5, 40).
  const bn = (v) => {
    const x = Math.floor((Number(v) || 0) * 10 + 1e-6) / 10;
    return x.toLocaleString(undefined, { maximumFractionDigits: 1 });
  };
  const bananas = (v) => `<span class="bananas">${bn(v)} <span aria-hidden="true">${BANANA}</span><span class="sr-only">bananas</span></span>`;

  // ---- data ---------------------------------------------------------------------
  const loadShop = async () => { state.shop = await api('/api/shop'); if (onShop) onShop(); };
  const loadTroop = async () => { state.troop = await api('/api/troop'); state.looks = state.troop.looks; };
  const loadProfile = async () => {
    state.profile = null;
    if (!state.troopPick) return;
    try { state.profile = await api('/api/troop/profile?name=' + encodeURIComponent(state.troopPick)); }
    catch (e) { state.profile = { error: e.message }; }
  };
  // The signed-in bettor changed (sign in / out): their wallet, items and themes come with the shop.
  let shopFor = null;
  function syncMe() {
    const name = state.me ? state.me.name.toLowerCase() : '';
    if (shopFor === name) return;
    shopFor = name;
    Promise.all([loadShop(), loadTroop()]).then(() => { if (['shop', 'troop'].includes(state.view)) draw(); }).catch(() => {});
  }

  const itemsById = () => {
    const m = new Map();
    const s = state.shop;
    if (s) [...s.catalog, ...s.social].forEach((i) => m.set(i.id, i));
    return m;
  };
  const myShop = () => (state.shop && state.shop.me) || null;
  const owns = (id) => !!myShop() && myShop().owned.includes(id);

  // ---- names everywhere ---------------------------------------------------------------
  const lookOf = (name) => (state.looks || {})[String(name || '').toLowerCase()] || { worn: {} };

  // A bettor's name as the rest of the site shows it: their name colour, badge and any pranks on them.
  // opts.title adds their title (or a Title Swap) on its own line; opts.link makes it open their profile.
  function nameHtml(name, opts = {}) {
    const look = opts.look || lookOf(name), worn = look.worn || {}, pranks = look.pranks || [];
    const cls = ['bname', worn.name_color && worn.name_color.cls, ...pranks.map((p) => p.cls)].filter(Boolean).join(' ');
    const clown = pranks.find((p) => p.avatar); // Clown Makeup takes over the badge
    const badge = clown ? `<span class="bbadge" title="${esc(`${clown.name} from ${clown.by}`)}">${clown.avatar}</span>`
      : worn.badge ? `<span class="bbadge" title="${esc(worn.badge.name)}">${worn.badge.emoji}</span>` : '';
    const marks = pranks.filter((p) => p.mark).map((p) => `<span class="bprank" title="${esc(`${p.name} from ${p.by}`)}">${p.emoji}</span>`).join('');
    const nick = pranks.find((p) => p.kind === 'nick');
    const nickHtml = nick ? `<span class="bnick" title="${esc(`Nickname from ${nick.by}`)}">“${esc(nick.text)}”</span>` : '';
    const label = opts.link
      ? `<a class="${cls} plain-link" href="#monkeys/${encodeURIComponent(name)}">${esc(name)}</a>` : `<span class="${cls}">${esc(name)}</span>`;
    let title = '';
    if (opts.title) {
      const swap = pranks.find((p) => p.item === 'sc-title');
      if (swap) title = `<span class="btitle swapped" title="${esc(`Title Swap from ${swap.by}`)}">${esc(swap.text)}</span>`;
      else if (worn.title) title = `<span class="btitle">${esc(worn.title.text)}</span>`;
    }
    return `<span class="bn">${label}${nickHtml}${badge}${marks}</span>${title}`;
  }

  // The avatar for a look: Clown Makeup, else their badge, else the monkey.
  const avatarOf = (look) => ((look.pranks || []).find((p) => p.avatar) || {}).avatar || ((look.worn || {}).badge || {}).emoji || '🐒';
  // Their avatar, for the top bar's profile chip.
  const badgeOf = (name) => avatarOf(lookOf(name));

  // Classes for a bettor's ticket card: their ticket style, plus any prank that marks tickets (jinx frost, glitter, bounty).
  const ticketClassOf = (look) => [look.worn && look.worn.ticket && look.worn.ticket.cls, ...(look.pranks || []).map((p) => p.ticket_cls)]
    .filter(Boolean).join(' ');
  const ticketClass = (name) => ticketClassOf(lookOf(name));

  // What pranks add inside their ticket cards: a bounty poster and heckle speech bubbles.
  function ticketExtrasOf(look) {
    const pranks = look.pranks || [];
    const bounty = pranks.filter((p) => p.kind === 'bounty').map((p) =>
      `<div class="tk-bounty"><span aria-hidden="true">🎯</span> <b>Wanted</b> · bounty from ${esc(p.by)}</div>`).join('');
    const heckles = pranks.filter((p) => p.kind === 'heckle').map((p) =>
      `<div class="tk-heckle"><span class="tk-heckle-by">${esc(p.by)}</span> ${esc(p.text)}</div>`).join('');
    return bounty + heckles;
  }
  const ticketExtras = (name) => ticketExtrasOf(lookOf(name));

  // The signed-in bettor's win celebration ({colors} or {emoji}), or null for the default confetti.
  function myCelebration() {
    if (!state.me) return null;
    const c = lookOf(state.me.name).worn.celebration;
    return c ? { colors: c.colors, emoji: c.emoji } : null;
  }

  // Themes this bettor unlocked, for the ◐ button's cycle.
  function ownedThemes() {
    const me = myShop();
    if (!me) return [];
    return state.shop.catalog.filter((i) => i.slot === 'theme' && me.owned.includes(i.id)).map((i) => ({ key: i.look.theme, name: i.name }));
  }

  // ---- building blocks ----------------------------------------------------------
  // What the item looks like on its own, for its card in the shop.
  function sampleHtml(item, who) {
    const l = item.look || {};
    const name = esc(who || (state.me && state.me.name) || 'You');
    switch (item.slot) {
      case 'name_color': return `<span class="bname ${l.cls}">${name}</span>`;
      case 'badge': return `<span class="shop-emoji">${l.emoji}</span>`;
      case 'title': return `<span class="btitle big">${esc(l.text)}</span>`;
      case 'banner': return `<span class="shop-banner ${l.cls}"></span>`;
      case 'ticket': return `<span class="shop-ticket bet-slip-card ${l.cls}"><span class="bet-ticket-row small"><b>Win</b><span class="status won">won</span></span></span>`;
      case 'celebration': return `<span class="shop-emoji">${l.emoji ? l.emoji.join('') : l.colors.map((c) => `<i class="shop-dot" style="background:${c}"></i>`).join('')}</span>`;
      case 'theme': return `<span class="shop-theme" data-theme-preview="${l.theme}"><i></i><i></i><i></i></span>`;
      case 'card_back': return `<span class="shop-felt"><span class="pcard back md ${l.cls}"></span><span class="pcard back md ${l.cls}"></span></span>`;
      case 'chips': return `<span class="shop-felt"><span class="chip-stake big ${l.cls}">25</span><span class="chip-stake big ${l.cls}">100</span></span>`;
      case 'seat': return `<span class="shop-felt"><span class="pk-seat static ${l.cls}"><span class="pk-plate"><span class="pk-name">${nameHtml((state.me && state.me.name) || 'You')}</span><span class="pk-stack">500</span></span></span></span>`;
      case 'entrance': return `<span class="shop-quote">“${esc(l.text.replace(/\{name\}/g, (state.me && state.me.name) || 'You'))}”</span>`;
      case 'table_win': return `<span class="shop-emoji">${l.emoji ? l.emoji.join('') : l.colors.map((c) => `<i class="shop-dot" style="background:${c}"></i>`).join('')}</span>`;
      default: return `<span class="shop-emoji">${l.emoji || BANANA}</span>`;
    }
  }

  // A small profile card (banner, badge, name, title): the preview window shows it before and after.
  function miniProfile(name, look) {
    const w = look.worn || {};
    return `<div class="pf-mini"><div class="pf-banner ${w.banner ? w.banner.cls : ''}"></div>
      <div class="pf-mini-body"><span class="pf-avatar">${avatarOf(look)}</span>
        <div class="pf-mini-name">${nameHtml(name, { look, title: true })}</div></div></div>`;
  }

  // One of their bet tickets, styled the way everyone else sees it.
  function sampleTicket(name, look) {
    return `<div class="bet-slip-card ${ticketClassOf(look)}">
      <div class="bet-slip-head">${nameHtml(name, { look })}<span class="muted small right">2 bets · 75 staked</span></div>${ticketExtrasOf(look)}
      <div class="bet-ticket pending"><div class="bet-ticket-row"><div class="bet-desc">Match result: Win</div><span class="status pending">pending</span></div>
        <div class="bet-ticket-row muted small"><span>50 @ -110</span><span>To win 45</span></div></div>
      <div class="bet-ticket won"><div class="bet-ticket-row"><div class="bet-desc">Top fragger: ${esc(name)}</div><span class="status won">won</span></div>
        <div class="bet-ticket-row muted small"><span>25 @ +320</span><span>Return 105</span></div></div></div>`;
  }

  // A seat at Onkey's tables as everyone else sees it: your nameplate, face-down cards, a bet and a stake chip.
  function casinoSample(name, look) {
    const w = look.worn || {}, cls = (slot) => (w[slot] && w[slot].cls) || '';
    return `<div class="casino-sample felt"><div class="pk-seat static ${cls('seat')}" data-bettor="${esc(name)}">
        <div class="pk-cards"><span class="pcard back sm ${cls('card_back')}"></span><span class="pcard back sm ${cls('card_back')}"></span></div>
        <div class="pk-plate"><div class="pk-name">${nameHtml(name, { look })}</div><div class="pk-stack">500</div></div></div>
      <div class="casino-sample-chips"><span class="pk-bet static"><span class="chip-dot ${cls('chips')}"></span>40</span>
        <span class="chip-stake big ${cls('chips')}">25</span></div></div>`;
  }
  // Onkey announcing someone as they sit down: their Entrance line, or his usual welcome.
  function entranceSample(name, look) {
    const e = (look.worn || {}).entrance;
    const line = e ? e.text.replace(/\{name\}/g, name) : `Pull up a stool, ${name}.`;
    return `<div class="casino-sample felt entrance"><div class="onkey-dealer talking static"><img class="onkey-face" src="/assets/onkey-logo.png" alt="" width="88" height="51">
      <div class="onkey-bubble"><span class="ook">${e ? 'Eek! Eek! Ook! Eek!' : 'Ook Eek Ook'}</span><span class="say">${esc(line)}</span></div></div></div>`;
  }

  // A look with one item swapped in (a worn slot, or a prank on top).
  function withItem(look, item, extra = {}) {
    const next = { worn: { ...(look.worn || {}) }, pranks: [...(look.pranks || [])] };
    if (item.slot) next.worn[item.slot] = { ...item.look, id: item.id, name: item.name };
    else next.pranks.push({ item: item.id, name: item.name, by: (state.me && state.me.name) || 'you', ...item.look, ...extra });
    return next;
  }

  // ---- the Shop tab ---------------------------------------------------------------
  function viewShop() {
    const s = state.shop;
    if (!s) return '<div class="loading">Loading…</div>';
    const me = s.me, per = Math.round(1 / (s.rate || 0.1));
    // The exchange rate is in the intro line below, so it doesn't get a tile of its own.
    const tiles = me ? [
      kpi('Your bananas', bananas(me.wallet), 'ready to spend'),
      kpi('Earned this season', bananas(me.season_earned), `from ${fmt.credits(me.season_credits)} credits won`),
      kpi('Spent in all', bananas(me.spent), 'on looks, pranks and the arcade'),
    ] : [];
    const signIn = me ? '' : '<div class="viz-note"><a href="#" data-signin>Sign in</a> to spend bananas. You can still preview everything.</div>';
    const f = state.shopSlot;
    const showSlot = (slot) => f === 'all' || f === slot.key || f === `g:${slot.group || 'looks'}`;
    const slotCard = (slot) => {
      const items = s.catalog.filter((i) => i.slot === slot.key);
      const worn = me && me.worn[slot.key];
      return `<section class="card"><div class="section-head"><h2>${esc(slot.label)}</h2>` +
        `${worn ? `<button class="btn ghost small shop-unequip" data-slot="${slot.key}">Take off</button>` : ''}</div>
        <p class="muted small">${esc(slot.desc)}</p><div class="shop-grid">${items.map((i) => itemCard(i, me)).join('')}</div></section>`;
    };
    const sections = [];
    for (const g of groupsOf(s)) {
      const slots = s.slots.filter((x) => (x.group || 'looks') === g.key && showSlot(x));
      const social = g.key === 'looks' && (f === 'all' || f === 'social' || f === 'g:looks');
      if (!slots.length && !social) continue;
      if (g.key === 'casino') {
        sections.push(`<section class="card shop-group-head casino-head"><div class="shop-group-art" aria-hidden="true">🃏</div><div><h2>${esc(g.label)}</h2>
          ${how(esc(g.desc), '<p>Card backs, chips and your seat style show on your seat at the poker table and your spot at blackjack. Your Entrance is what Onkey announces when you sit down at a shared table, and your Table win bursts out of your seat when you win a pot or a blackjack hand. Your name colour, badge and title come with you to the tables too.</p><p>Like everything in the shop, these are bought with bananas and never change your credits or your chances.</p>')}</div></section>`);
      }
      sections.push(...slots.map(slotCard));
      if (social) {
        sections.push(`<section class="card"><h2>Monkey business</h2>${how('Pranks for your friends. They wear off by themselves.',
          'Most last for the next 3 5-stack games (a week at most). A Wall Note stays on their profile for 3 days; a Nickname or Title Swap lasts 24 hours. Everyone can see who sent what.')}
          <div class="shop-grid">${s.social.map((i) => itemCard(i, me)).join('')}</div></section>`);
      }
    }
    return `${tiles.length ? `<section class="kpis">${tiles.join('')}</section>` : ''}
      <section class="card shop-hero"><div class="shop-hero-art" aria-hidden="true"></div><div>
        <h2>Onkey's Shop</h2>
        ${how(`Every ${per} credits you win pays 1 ${BANANA}. Bananas only buy looks, never credits.`,
          `<p>Bananas are paid on every credit you gain: a won bet's profit and every game reward, at ${per} credits to 1 ${BANANA}. Losing bets never take bananas back, so this season's bananas are always your credits won divided by ${per}.</p>` +
          '<p>Spending bananas never changes your credits, your bets or the leaderboard. A season reset takes bananas back to zero with the credits, but everything you bought stays yours.</p>')}
        ${signIn}</div></section>
      ${collectionMap(s, me)}
      ${sections.join('')}
      ${me ? historyCard(me) : ''}`;
  }

  // The shop at a glance, and its filter: a ring of how much of the catalogue you own (click it for everything), then
  // one card per section with its icon, owned / total, a dot per item (filled = owned, ringed = worn) and its cheapest price.
  const SLOT_ICONS = { name_color: '🎨', badge: '🏅', title: '🏷️', banner: '🖼️', ticket: '🎟️', celebration: '🎉', theme: '🌓', social: '🙈',
    card_back: '🂠', chips: '🪙', seat: '🪑', entrance: '📣', table_win: '💰' };
  // The shop's groups (your looks, Onkey's Casino); an older server sends no groups, which means one.
  const groupsOf = (s) => s.groups || [{ key: 'looks', label: 'Your looks', desc: '' }];
  function collectionMap(s, me) {
    const owned = new Set(me ? me.owned : []), worn = new Set(me ? Object.values(me.worn) : []);
    const have = s.catalog.filter((i) => owned.has(i.id)).length, total = s.catalog.length;
    const pct = total ? have / total : 0;
    const cardOf = (g) => {
      const n = g.social ? 0 : g.items.filter((i) => owned.has(i.id)).length;
      const dots = g.items.map((i) => `<i class="cm-dot ${owned.has(i.id) ? 'have' : ''} ${worn.has(i.id) ? 'worn' : ''}" title="${esc(`${i.name}: ${worn.has(i.id) ? 'wearing' : owned.has(i.id) ? 'owned' : `${i.price} bananas`}`)}"></i>`).join('');
      const low = Math.min(...g.items.map((i) => i.price));
      const count = g.social ? `<span class="cm-count">${g.items.length} <small>pranks</small></span>`
        : `<span class="cm-count">${n}<small>/${g.items.length}</small></span>`;
      const done = !g.social && n === g.items.length;
      return `<button type="button" class="cm-slot shop-slot ${state.shopSlot === g.key ? 'on' : ''} ${done ? 'done' : ''}" data-v="${g.key}" aria-pressed="${state.shopSlot === g.key}">
        <span class="cm-icon" aria-hidden="true">${SLOT_ICONS[g.key] || BANANA}</span>
        <span class="cm-label">${esc(g.label)}</span>${count}
        <span class="cm-dots" aria-hidden="true">${dots}</span>
        <span class="cm-from">${done ? 'Complete ✓' : `from ${bn(low)} ${BANANA}`}</span></button>`;
    };
    const blocks = groupsOf(s).map((grp) => {
      const slots = s.slots.filter((x) => (x.group || 'looks') === grp.key)
        .map((x) => ({ key: x.key, label: x.label, items: s.catalog.filter((i) => i.slot === x.key) }));
      if (grp.key === 'looks') slots.push({ key: 'social', label: 'Monkey business', items: s.social, social: true });
      if (!slots.length) return '';
      const on = state.shopSlot === `g:${grp.key}`;
      return `<div class="cm-group"><button type="button" class="cm-group-label shop-slot ${on ? 'on' : ''}" data-v="g:${grp.key}" aria-pressed="${on}">${esc(grp.label)}</button>
        <div class="cm-grid">${slots.map(cardOf).join('')}</div></div>`;
    });
    const cards = blocks.join('');
    const ring = `<button type="button" class="cm-ring shop-slot ${state.shopSlot === 'all' ? 'on' : ''}" data-v="all" aria-pressed="${state.shopSlot === 'all'}" style="--pct:${(pct * 100).toFixed(1)}%">
        <span class="cm-ring-in"><b>${have}<small>/${total}</small></b><span>${me ? 'collected' : 'items'}</span></span></button>`;
    return `<section class="card cm-card"><div class="section-head"><h2>${me ? 'Your collection' : 'In the shop'}</h2>
        <span class="muted small">${me ? `${Math.round(pct * 100)}% of the shop` : `${total} looks and ${s.social.length} pranks`}. ` +
        `${state.shopSlot === 'all' ? 'Pick a section to show just that one.' : 'Click the ring to show everything again.'}</span></div>
      <div class="cm-wrap">${ring}<div class="cm-groups">${cards}</div></div></section>`;
  }

  function itemCard(item, me) {
    const social = !item.slot, have = !social && owns(item.id), worn = have && me.worn[item.slot] === item.id;
    const afford = me && me.wallet + 1e-9 >= item.price;
    let action;
    if (!me) action = `<button class="btn small shop-preview" data-item="${item.id}">Preview</button>`;
    else if (worn) action = `<button class="btn ghost small shop-unequip" data-slot="${item.slot}">Take off</button>`;
    else if (have) action = `<button class="btn small shop-wear" data-slot="${item.slot}" data-item="${item.id}">Wear</button>`;
    else action = `<button class="btn small shop-preview" data-item="${item.id}">${social ? 'Use…' : 'Preview'}</button>`;
    const price = have ? `<span class="shop-owned">${worn ? 'Wearing' : 'Owned'}</span>`
      : `<span class="shop-price ${me && !afford ? 'short' : ''}">${bn(item.price)} ${BANANA}</span>`;
    return `<div class="shop-item ${worn ? 'worn' : have ? 'owned' : ''}">
      <button type="button" class="shop-sample shop-preview" data-item="${item.id}" aria-label="Preview ${esc(item.name)}">${sampleHtml(item)}</button>
      <div class="shop-item-name">${esc(item.name)}</div>
      <div class="muted small shop-item-desc">${esc(item.desc)}</div>
      <div class="shop-item-foot">${price}${action}</div></div>`;
  }

  function historyCard(me) {
    const why = { bet_win: 'Bet won', reward: 'Game reward', purchase: 'Bought', prank: 'Used', arcade: 'Played', starter: 'Starting bananas', season_reset: 'Season reset', demo: 'Demo grant' };
    const rows = (me.history || []).map((h) => `<li class="banana-row"><span class="num ${h.delta > 0 ? 'up' : 'down'}">${h.delta > 0 ? '+' : '−'}${bn(Math.abs(h.delta))}</span>` +
      `<span><b>${why[h.reason] || esc(h.reason)}</b> <span class="muted">${esc(h.note || '')}</span></span>` +
      `<span class="muted small">${h.credits ? `${fmt.credits(h.credits)} credits · ` : ''}${fmt.ago(h.created_ts)}</span></li>`).join('');
    return `<section class="card"><h2>Your bananas</h2>` +
      (rows ? `<ul class="recent banana-list">${rows}</ul>` : `<p class="muted">No bananas yet. Win a bet or play a 5-stack game.</p>`) + '</section>';
  }

  // ---- preview window ------------------------------------------------------------
  // Before and after for the signed-in bettor (or the target of a prank), then buy from here.
  function openPreview(id) {
    const item = itemsById().get(id);
    if (!item) return;
    closePreview();
    const box = document.createElement('div');
    box.id = 'shop-modal';
    box.className = 'modal-backdrop';
    box.innerHTML = `<div class="modal card" role="dialog" aria-modal="true" aria-labelledby="shop-modal-title">${previewHtml(item)}</div>`;
    document.body.append(box);
    document.body.classList.add('modal-open');
    box.addEventListener('click', (e) => { if (e.target === box) closePreview(); });
    bindPreview(box, item);
    ($('.modal-buy', box) || $('.modal-close', box)).focus();
  }

  function closePreview() {
    const box = $('#shop-modal');
    if (box) box.remove();
    document.body.classList.remove('modal-open');
  }

  const previewTarget = { name: '', text: '' };
  // Items the buyer writes something for: the input's label and a placeholder (also the preview's stand-in text).
  const TEXT_ITEMS = {
    'sc-note': ['Your note', 'Nice clutch. Still owe me a Vandal.'],
    'sc-title': ['Their title', 'Professional Baiter'],
    'sc-heckle': ['Your heckle', 'you whiffed that Op shot on purpose right'],
    'sc-nick': ['Their nickname', 'Bot Frag'],
  };
  function previewHtml(item) {
    const me = myShop(), social = !item.slot;
    const who = social ? previewTarget.name || '' : (me && me.name) || 'You';
    let body;
    if (social) {
      const others = (state.troop ? state.troop.troop : []).filter((b) => !me || b.name.toLowerCase() !== me.name.toLowerCase());
      if ((!previewTarget.name || (me && previewTarget.name.toLowerCase() === me.name.toLowerCase())) && others.length) previewTarget.name = others[0].name;
      const target = previewTarget.name || 'Someone';
      const words = TEXT_ITEMS[item.id] || ['Your text', ''];
      const before = lookOf(target), after = withItem(before, item, { text: previewTarget.text || words[1] });
      const input = item.max_len ? `<label>${words[0]} <span class="muted">(up to ${item.max_len} characters)</span>
        <input id="modal-text" maxlength="${item.max_len}" value="${esc(previewTarget.text)}" placeholder="${esc(words[1])}"></label>` : '';
      const note = item.id === 'sc-note' ? `<div class="wall-note"><span class="wall-pin">📌</span><div>${esc(previewTarget.text || 'Your note here')}<div class="muted small">from ${esc((me && me.name) || 'you')} · just now</div></div></div>` : '';
      const pick = `<label>Use it on<select id="modal-target">${others.map((b) =>
        `<option value="${esc(b.name)}" ${b.name === target ? 'selected' : ''}>${esc(b.name)}</option>`).join('')}</select></label>`;
      body = `<div class="modal-controls">${pick}${input}</div>
        <div class="preview-pair"><div><div class="preview-label">Now</div>${miniProfile(target, before)}${sampleTicket(target, before)}</div>
          <div><div class="preview-label">After</div>${miniProfile(target, after)}${note}${sampleTicket(target, after)}</div></div>`;
    } else {
      // Pranks on you wear off, so the preview leaves them out: it shows what the item looks like for good.
      const base = { worn: { ...(me ? lookOf(me.name).worn : {}) } };
      const after = withItem(base, item);
      if (item.slot === 'theme') {
        body = `<div class="preview-label">The site in ${esc(item.name)}</div>${themeMock(item.look.theme)}` +
          '<p class="muted small">Themes are yours on every device you sign in on: once bought, the ◐ button cycles through them after Dark and Light.</p>';
      } else if (item.slot === 'entrance') {
        body = `<div class="preview-pair"><div><div class="preview-label">Now</div>${entranceSample(who, base)}</div>
          <div><div class="preview-label">With ${esc(item.name)}</div>${entranceSample(who, after)}</div></div>
          <p class="muted small">Onkey says this, with extra squeaks, whenever you sit down at the shared blackjack or poker table.</p>`;
      } else if (item.slot === 'table_win') {
        body = `<div class="preview-celebrate">${casinoSample(who, after)}
          <p class="muted">This bursts out of your seat whenever you win a pot or a blackjack hand, for everyone at the table.</p><button class="btn modal-play">Play it ▶</button></div>`;
      } else if (['card_back', 'chips', 'seat'].includes(item.slot)) {
        body = `<div class="preview-pair"><div><div class="preview-label">Now</div>${casinoSample(who, base)}</div>
          <div><div class="preview-label">With ${esc(item.name)}</div>${casinoSample(who, after)}</div></div>
          <p class="muted small">This is how everyone at Onkey's tables sees you.</p>`;
      } else if (item.slot === 'celebration') {
        body = `<div class="preview-celebrate"><span class="shop-sample big">${sampleHtml(item)}</span>
          <p class="muted">This bursts out of your balance whenever a bet of yours wins.</p><button class="btn modal-play">Play it ▶</button></div>`;
      } else {
        body = `<div class="preview-pair"><div><div class="preview-label">Now</div>${miniProfile(who, base)}${sampleTicket(who, base)}</div>
          <div><div class="preview-label">With ${esc(item.name)}</div>${miniProfile(who, after)}${sampleTicket(who, after)}</div></div>`;
      }
    }
    const have = !social && owns(item.id), afford = me && me.wallet + 1e-9 >= item.price;
    const buy = !me ? '<button type="button" class="btn" data-signin>Sign in to buy</button>'
      : have ? `<button class="btn modal-buy" disabled>You own this</button>`
      : `<button class="btn modal-buy" ${afford ? '' : 'disabled'}>${social ? 'Use' : 'Buy'} for ${bn(item.price)} ${BANANA}</button>`;
    const wallet = me ? `<span class="muted small">You have ${bn(me.wallet)} ${BANANA}${afford || have ? '' : ` · ${bn(item.price - me.wallet)} short`}</span>` : '';
    return `<div class="modal-head"><div><h2 id="shop-modal-title">${esc(item.name)}</h2><div class="muted small">${esc(item.desc)}</div></div>
        <button class="btn ghost icon modal-close" aria-label="Close">✕</button></div>
      ${body}
      <div class="modal-foot">${wallet}<span class="right btn-row">${buy}<button class="btn ghost modal-close">Close</button></span></div>`;
  }

  // A tiny page drawn in the theme's colours (data-theme-preview carries its tokens, see style.css).
  function themeMock(theme) {
    return `<div class="theme-mock" data-theme-preview="${theme}"><div class="tm-bar"><span class="tm-logo"></span><i></i><i class="on"></i><i></i></div>
      <div class="tm-body"><div class="tm-card"><b>Rankings</b><div class="tm-row"><span class="swatch s1"></span>Matt<span class="tm-fill" style="width:80%"></span></div>
      <div class="tm-row"><span class="swatch s2"></span>Jordan<span class="tm-fill" style="width:55%"></span></div></div>
      <div class="tm-card"><b>Odds</b><div class="tm-odds"><span>Over 18.5</span><span class="on">-115</span></div></div></div></div>`;
  }

  function bindPreview(box, item) {
    $$('.modal-close', box).forEach((b) => b.addEventListener('click', closePreview));
    const redraw = () => {
      const focus = document.activeElement && document.activeElement.id, pos = focus === 'modal-text' ? $('#modal-text').selectionStart : null;
      $('.modal', box).innerHTML = previewHtml(item);
      bindPreview(box, item);
      if (focus && $('#' + focus, box)) { $('#' + focus, box).focus(); if (pos != null) $('#modal-text').setSelectionRange(pos, pos); }
    };
    $('#modal-target', box)?.addEventListener('change', (e) => { previewTarget.name = e.target.value; redraw(); });
    $('#modal-text', box)?.addEventListener('input', (e) => { previewTarget.text = e.target.value; redraw(); });
    $('.modal-play', box)?.addEventListener('click', (e) => confetti($('.casino-sample .pk-seat', box) || e.currentTarget, false, { colors: item.look.colors, emoji: item.look.emoji }));
    $('.modal-buy', box)?.addEventListener('click', async (e) => {
      e.currentTarget.disabled = true;
      const body = { item: item.id };
      if (!item.slot) { body.target = previewTarget.name; body.text = previewTarget.text; }
      try {
        const r = await api('/api/shop/buy', { method: 'POST', body: JSON.stringify(body) });
        state.shop = r.shop;
        if (onShop) onShop();
        await loadTroop();
        closePreview();
        previewTarget.text = '';
        if (item.slot === 'theme') setTheme(item.look.theme);
        toast(item.slot ? `${item.name} is yours, and you're wearing it.` : `${item.name} used on ${r.target}.`, 'good');
        window.FiveOnkey?.note('buy', { item: item.name, target: item.slot ? null : r.target });
        if (item.slot === 'celebration' || item.price >= 600) confetti($('#me-bananas') || e.currentTarget, item.price >= 600, myCelebration());
        state.me && (state.me.bananas = r.wallet);
        draw();
      } catch (err) {
        toast(err.message, 'bad');
        e.currentTarget.disabled = false;
      }
    });
  }

  function setTheme(t) {
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem('fs.theme', t); } catch (e) { /* storage blocked */ }
  }

  // ---- the Monkeys tab (view "troop"): everyone's bananas and collections, and one profile -------------
  function viewTroop() {
    if (state.troopPick) return viewProfile();
    const t = state.troop;
    if (!t) return '<div class="loading">Loading…</div>';
    const rows = t.troop;
    if (!rows.length) return '<div class="card empty"><h2>No bettors yet</h2><p>Bettors show up here once they have an account.</p></div>';
    const me = (state.me ? state.me.name : '').toLowerCase();
    const maxCol = Math.max(1, ...rows.map((r) => r.collection));
    const table = rows.map((r, i) => `<tr class="troop-row ${r.name.toLowerCase() === me ? 'me' : ''}" data-name="${esc(r.name)}" tabindex="0">
      <td class="rank">${i < 3 && r.collection ? ['🥇', '🥈', '🥉'][i] : i + 1}</td>
      <td>${nameHtml(r.name, { title: true })}${r.claimed ? '' : ' <span class="muted small">unclaimed</span>'}</td>
      <td class="num">${r.items} <span class="muted small">/ ${t.catalog_size}</span></td>
      <td class="bar-cell"><div class="hbar-track"><div class="hbar-fill banana" style="width:${Math.round((r.collection / maxCol) * 100)}%"></div></div></td>
      <td class="num balance">${bananas(r.wallet)}</td>
      <td class="num">${bn(r.season_earned)}</td>
      <td class="num muted">${fmt.credits(r.season_credits)}</td></tr>`).join('');
    const active = Object.entries(state.looks || {}).flatMap(([, l]) => l.pranks || []);
    const pranked = rows.flatMap((r) => (lookOf(r.name).pranks || []).map((p) => ({ ...p, target: r.name })));
    const business = pranked.length ? `<section class="card"><h2>Monkey business</h2><ul class="recent">${pranked.map((p) =>
      `<li class="banana-row"><span class="shop-emoji sm">${p.emoji}</span><span><b>${esc(p.by)}</b> used ${esc(p.name)} on ${nameHtml(p.target, { link: true })}` +
      `${p.text ? ` <span class="muted">“${esc(p.text)}”</span>` : ''}</span><span class="muted small">${p.games ? `${p.games_left} more game${p.games_left === 1 ? '' : 's'}` : 'ends ' + fmt.date(p.expires_ts * 1000)}</span></li>`).join('')}</ul></section>` : '';
    return `<section class="kpis">
        ${kpi('Monkeys', rows.length, 'bettors')}
        ${kpi('Bananas in hand', bananas(rows.reduce((a, r) => a + r.wallet, 0)), 'unspent, all monkeys')}
        ${kpi('Items bought', rows.reduce((a, r) => a + r.items, 0), `${bn(rows.reduce((a, r) => a + r.collection, 0))} ${BANANA} of swag`)}
        ${kpi('Pranks active', active.length, active.length ? 'someone is getting got' : 'all quiet')}
      </section>
      <section class="card"><h2>Monkey rankings</h2>${how('Ordered by the bananas spent on their collection. Click anyone for their profile.',
        `Bananas this season are the credits they won this season divided by ${Math.round(1 / (t.rate || 0.1))}, so the columns on the right always line up. Pranks don't count toward a collection.`)}
        <div class="table-wrap"><table class="rankings troop"><thead><tr><th class="rank">#</th><th>Monkey</th><th class="num">Items</th><th>Collection</th>
          <th class="num">Bananas</th><th class="num">Earned this season</th><th class="num">From credits won</th></tr></thead><tbody>${table}</tbody></table></div></section>
      ${business}`;
  }

  function viewProfile() {
    const p = state.profile;
    const back = '<a class="small plain-link troop-back" href="#monkeys">‹ All monkeys</a>';
    if (!p) return '<div class="loading">Loading…</div>';
    if (p.error) return `<div class="card error">${back}<h2>Couldn't open that profile</h2><p>${esc(p.error)}</p></div>`;
    const look = p.looks || { worn: {} }, w = look.worn || {};
    const shop = state.shop, byId = itemsById();
    const slots = shop ? shop.slots : [];
    const ownedIds = new Set(p.owned.map((o) => o.item_id));
    const wearing = slots.map((s) => {
      const it = w[s.key];
      return `<div class="pf-slot ${it ? '' : 'empty'}"><div class="ov-tile-label">${esc(s.label)}</div>` +
        `<div class="pf-slot-item">${it ? `${sampleHtml(byId.get(it.id) || { slot: s.key, look: it }, p.name)}${s.key === 'title' ? '' : `<span>${esc(it.name)}</span>`}` : '<span class="muted">Nothing yet</span>'}</div></div>`;
    }).join('');
    const collection = shop ? shop.catalog.map((i) => `<div class="pf-coll ${ownedIds.has(i.id) ? 'have' : 'missing'}" title="${esc(`${i.name}${ownedIds.has(i.id) ? '' : ` · ${i.price} bananas`}`)}">` +
      `${sampleHtml(i, p.name)}</div>`).join('') : '';
    const wanted = p.pranks.filter((x) => x.item_id === 'sc-bounty' && x.active).map((x) =>
      `<div class="wanted-poster"><div class="wanted-head">WANTED</div><div class="pf-avatar big">${avatarOf(look)}</div><b>${esc(p.name)}</b>` +
      `<div class="small">Bounty posted by ${nameHtml(x.bettor, { link: true })}</div></div>`).join('');
    const notes = p.pranks.filter((x) => x.item_id === 'sc-note' && x.active).map((x) =>
      `<div class="wall-note"><span class="wall-pin">📌</span><div>${esc(x.text)}<div class="muted small">from ${nameHtml(x.bettor, { link: true })} · ${fmt.ago(x.created_ts)}</div></div></div>`).join('');
    const got = p.pranks.filter((x) => x.item_id !== 'sc-note').map((x) => `<li class="banana-row"><span class="shop-emoji sm">${x.look.emoji}</span>` +
      `<span>${esc(x.name)} from ${nameHtml(x.bettor, { link: true })}${x.text ? ` <span class="muted">“${esc(x.text)}”</span>` : ''}</span>` +
      `<span class="small ${x.active ? 'up' : 'muted'}">${x.active ? 'active' : fmt.ago(x.created_ts)}</span></li>`).join('');
    const sent = p.pranks_sent.map((x) => `<li class="banana-row"><span class="shop-emoji sm">${(byId.get(x.item_id) || { look: {} }).look.emoji || ''}</span>` +
      `<span>${esc(x.name)} on ${nameHtml(x.target, { link: true })}${x.text ? ` <span class="muted">“${esc(x.text)}”</span>` : ''}</span>` +
      `<span class="muted small">${fmt.ago(x.created_ts)}</span></li>`).join('');
    const bet = p.betting || {};
    const isMe = state.me && state.me.name.toLowerCase() === p.name.toLowerCase();
    const per = Math.round(1 / (p.rate || 0.1));
    return `${back}
      <section class="card pf-card"><div class="pf-banner big ${w.banner ? w.banner.cls : ''}"></div>
        <div class="pf-head"><span class="pf-avatar big">${avatarOf(look)}</span>
          <div class="pf-who"><h2>${nameHtml(p.name, { look, title: true })}</h2>
            <div class="muted small">${p.claimed ? 'Bettor' : 'Unclaimed account'}${p.created_at ? ` since ${fmt.date(p.created_at * 1000)}` : ''}${isMe ? ' · this is you, <a href="#shop">go shopping ›</a>' : ''}</div></div></div>
        <div class="ov-tiles pf-stats">
          <div class="ov-tile"><div class="ov-tile-label">Bananas</div><div class="ov-tile-value">${bananas(p.wallet)}</div><div class="ov-tile-sub">${bn(p.spent)} spent in all</div></div>
          <div class="ov-tile"><div class="ov-tile-label">Earned this season</div><div class="ov-tile-value">${bn(p.season_earned)} ${BANANA}</div><div class="ov-tile-sub">${fmt.credits(p.season_credits)} credits won ÷ ${per}</div></div>
          <div class="ov-tile"><div class="ov-tile-label">Collection</div><div class="ov-tile-value">${p.owned.length} <span class="ov-unit">/ ${p.catalog_size}</span></div><div class="ov-tile-sub">${bn(p.collection)} ${BANANA} of swag</div></div>
          <div class="ov-tile"><div class="ov-tile-label">Credits</div><div class="ov-tile-value">${fmt.credits(bet.balance)}</div><div class="ov-tile-sub">${bet.won ?? 0}-${bet.lost ?? 0} · profit <span class="${bet.profit > 0 ? 'up' : bet.profit < 0 ? 'down' : ''}">${fmt.signed(bet.profit, 0)}</span></div></div>
        </div></section>
      ${wanted ? `<section class="card"><h2>🎯 There's a bounty on them</h2><div class="wanted-row">${wanted}</div></section>` : ''}
      ${notes ? `<section class="card"><h2>Wall</h2>${notes}</section>` : ''}
      <div class="grid-2">
        <section class="card"><h2>Wearing</h2><div class="pf-slots">${wearing}</div></section>
        <section class="card"><h2>Their tickets look like</h2>${sampleTicket(p.name, look)}</section>
      </div>
      <section class="card"><div class="section-head"><h2>Collection</h2><span class="muted small">${p.owned.length} of ${p.catalog_size} · faded ones are still in the shop</span></div>
        <div class="pf-collection">${collection}</div></section>
      <div class="grid-2">
        <section class="card"><h2>Monkey business received</h2>${got ? `<ul class="recent">${got}</ul>` : '<p class="muted">Nobody has messed with them yet.</p>'}</section>
        <section class="card"><h2>Monkey business sent</h2>${sent ? `<ul class="recent">${sent}</ul>` : '<p class="muted">Hasn\'t pranked anyone.</p>'}</section>
      </div>`;
  }

  // ---- events -------------------------------------------------------------------
  async function post(path, body, msg) {
    try {
      const r = await api(path, { method: 'POST', body: JSON.stringify(body) });
      state.shop = r.shop;
      await loadTroop();
      if (msg) toast(msg, 'good');
      if (path === '/api/shop/equip') window.FiveOnkey?.note('equip', { item: body.item ? itemsById().get(body.item)?.name || 'it' : '' });
      draw();
    } catch (e) { toast(e.message, 'bad'); }
  }

  function bind(view) {
    $$('.shop-slot', view).forEach((b) => b.addEventListener('click', () => { state.shopSlot = b.dataset.v; draw(); }));
    $$('.shop-preview', view).forEach((b) => b.addEventListener('click', () => openPreview(b.dataset.item)));
    $$('.shop-wear', view).forEach((b) => b.addEventListener('click', () => {
      const item = itemsById().get(b.dataset.item);
      if (item && item.slot === 'theme') setTheme(item.look.theme);
      post('/api/shop/equip', { slot: b.dataset.slot, item: b.dataset.item }, `Wearing ${item ? item.name : 'it'}.`);
    }));
    $$('.shop-unequip', view).forEach((b) => b.addEventListener('click', () => post('/api/shop/equip', { slot: b.dataset.slot, item: '' }, 'Taken off.')));
    $$('.troop-row', view).forEach((r) => {
      const open = () => { location.hash = '#monkeys/' + encodeURIComponent(r.dataset.name); };
      r.addEventListener('click', (e) => { if (!e.target.closest('a')) open(); });
      r.addEventListener('keydown', (e) => { if (e.key === 'Enter') open(); });
    });
  }

  return { init, bind, loadShop, loadTroop, loadProfile, syncMe, viewShop, viewTroop, nameHtml, badgeOf, ticketClass, ticketExtras, myCelebration, ownedThemes, bn, closePreview };
})();
