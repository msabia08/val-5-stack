/* What the Casino's live tables share (blackjack.js, poker.js): card faces, Onkey the dealer with his speech bubble
   and monkey chatter, the long-poll loop, countdowns and patching a region without losing focus. */
window.FiveCasino = (() => {
  'use strict';
  let esc = (s) => String(s), state = null, nameHtml = null, confetti = null;
  const SUIT = { s: '♠', h: '♥', d: '♦', c: '♣' };
  const SUIT_NAME = { s: 'spades', h: 'hearts', d: 'diamonds', c: 'clubs' };
  const RANK = { T: '10', J: 'J', Q: 'Q', K: 'K', A: 'A' };
  const RANK_NAME = { T: 'Ten', J: 'Jack', Q: 'Queen', K: 'King', A: 'Ace' };
  let muted = localStorage.getItem('fs.casinoMuted') === '1';
  let audio = null;
  function init(ctx) { ({ esc, state, nameHtml, confetti } = ctx); }

  // ---- what players bought (Onkey's Shop) ---------------------------------------------------
  // Tables send the looks of everyone at them; they join the site-wide looks that nameHtml reads.
  function absorb(looks) {
    if (!looks || !state) return;
    state.looks = { ...(state.looks || {}), ...looks };
  }
  const wornBy = (name, slot) => (((state && state.looks) || {})[String(name || '').toLowerCase()] || { worn: {} }).worn[slot] || null;
  // The class an item adds to a player's cards, chips or seat ('' when they wear nothing there).
  const style = (name, slot) => (wornBy(name, slot) || {}).cls || '';
  // A player's name at the table: their name colour, badge and pranks; title on its own line when asked.
  const who = (name, opts = {}) => (nameHtml ? nameHtml(name, opts) : esc(name));
  // Just their title (bought, or a Title Swap prank), for a line of its own; '' when they have none.
  const title = (name) => who(name, { title: true }).slice(who(name).length);
  // Their Table win burst, out of their seat (any element with data-bettor), for everyone watching.
  function seatBurst(root, name) {
    const w = wornBy(name, 'table_win');
    if (!w || !confetti || !root) return;
    const seat = [...root.querySelectorAll('[data-bettor]')].find((el) => el.dataset.bettor.toLowerCase() === String(name).toLowerCase());
    if (seat) confetti(seat, false, { colors: w.colors, emoji: w.emoji });
  }

  // ---- dealing ---------------------------------------------------------------------------
  // The server settles a move at once; the page plays it out. Each card a page draws can carry a `key` (where it sits:
  // table, round, seat, hand, position) and a `seq` (its place in the real deal order). `deal(render)` renders twice:
  // a first pass collects the cards never seen before, sorts them by seq and gives each a start time DEAL_GAP after the
  // last (a face-down card turning over is a flip, FLIP_GAP); the second pass draws them, each flying in from Onkey at
  // its time. A card drawn again before it has landed keeps its place in the animation (a negative delay), so table
  // updates mid-deal don't skip it. `after()` holds results (totals, wins, the status line) until the last card lands.
  const DEAL_GAP = 380, FLIP_GAP = 520, DEAL_MS = 420;
  const FIX_MS = 1900; // a card Onkey fixes (opts.fix) lands as dealt, then he crosses out its number and writes a new one; results wait
  const seen = new Map();          // card key -> { start } (performance.now() time it starts moving)
  let planning = null, landAt = 0, quiet = true;
  const now = () => performance.now();
  function deal(render) {
    planning = [];
    render();
    const fresh = planning.filter((p) => !seen.has(p.key)).sort((a, b) => a.seq - b.seq);
    planning = null;
    let t = now() + 60;
    for (const p of fresh) {
      if (quiet) { seen.set(p.key, { start: -1e9 }); continue; } // first sight of a table: no replay
      seen.set(p.key, { start: t, flip: p.flip, hold: p.hold });
      setTimeout(() => sound('card'), Math.max(0, t - now()));
      landAt = Math.max(landAt, t + (p.flip ? FLIP_GAP : DEAL_MS) + (p.hold || 0));
      t += (p.flip ? FLIP_GAP : DEAL_GAP) + (p.hold || 0); // the next card waits while Onkey fixes this one
    }
    quiet = false;
    if (seen.size > 600) [...seen.keys()].slice(0, seen.size - 400).forEach((k) => seen.delete(k));
    return render();
  }
  // A new table (another tab, a sign-in): its cards on screen now are shown, not dealt.
  const dealReset = () => { quiet = true; };
  // Milliseconds until the last card dealt so far lands.
  const landing = () => Math.max(0, landAt - now());
  // Attributes for something that should wait for the cards: a class and its delay, or nothing once they've landed.
  const after = (cls = '') => {
    const ms = Math.round(landing());
    return ms > 0 ? ` class="${cls} after-deal" style="--after:${ms}ms"` : (cls ? ` class="${cls}"` : '');
  };
  function dealt(key, c, opts) {
    const none = { cls: '', style: '' };
    if (!key) return none;
    const k = `${key}:${c || 'back'}`;
    if (planning) {
      planning.push({ key: k, seq: opts.seq || 0, flip: !!c && seen.has(`${key}:back`), hold: opts.fix ? FIX_MS : 0 });
      return none;
    }
    const e = seen.get(k);
    if (!e || e.start < 0) return none;
    const delay = Math.round(e.start - now());
    if (delay < -((e.flip ? FLIP_GAP : DEAL_MS) + (e.hold || 0))) return none;
    return { cls: e.flip ? ' flip-in' : ' deal-in', style: ` style="--deal-delay:${delay}ms"` };
  }

  // ---- chips -------------------------------------------------------------------------
  // Every chip is drawn in its denomination's design (.dn-5 ... .dn-500 in style.css, plainer at the bottom, fancier at
  // the top); an amount between denominations takes the largest one it covers. A bought chip style (.chp-*) wins.
  const DENOMS = [5, 10, 25, 50, 100, 250, 500];
  const denom = (v) => `dn-${DENOMS.filter((d) => d <= v).pop() || DENOMS[0]}`;
  // A chip with its value in the middle: opts.cls (size: mini / big; a bought chip style; a motion class), opts.style,
  // opts.title. len-N sizes the number so three or four digits still sit inside the inlay.
  const chipLabel = (n) => (Number.isInteger(n) ? n.toLocaleString('en-US') : n.toFixed(1));
  function chip(amount, opts = {}) {
    const n = Number(amount) || 0;
    const label = chipLabel(n);
    const bought = /\bchp-/.test(opts.cls || ''); // a bought chip style draws its own flat face
    return `<span class="chip-stake ${denom(n)} len-${Math.min(5, label.length)}${bought ? '' : ' drawn'}${opts.cls ? ` ${opts.cls}` : ''}"${opts.style || ''}${opts.title ? ` title="${esc(opts.title)}"` : ''}>${bought ? label : chipFace(n)}</span>`;
  }
  // The face of a chip, as a small SVG so it stays sharp at any size: the body, eight edge spots round the rim, a dashed
  // ring, the inlay, and the value centred in it. Colours come from the .dn-* class on the element around it.
  // The edge spots are a dashed ring (eight dashes), so the chip's outline stays a true circle.
  const SPOTS = '<circle cx="50" cy="50" r="41.5" pathLength="80" stroke-dasharray="3 7" stroke-dashoffset="1.5"/>';
  function chipFace(amount) {
    const label = chipLabel(Number(amount) || 0);
    const size = [34, 34, 34, 29, 24, 21][Math.min(5, label.length)];
    return `<svg class="chip-svg" viewBox="0 0 100 100" aria-hidden="true" focusable="false"><circle class="c-body" cx="50" cy="50" r="48"/>` +
      `<g class="c-spots">${SPOTS}</g><circle class="c-ring" cx="50" cy="50" r="35.5"/><circle class="c-inlay" cx="50" cy="50" r="30.5"/>` +
      `<text class="c-val" x="50" y="${(50 + size * 0.355).toFixed(1)}" font-size="${size}">${label}</text></svg>`; // digits sit on the baseline: lift by half their height
  }

  // ---- cards -------------------------------------------------------------------------
  // A face-up card ("As"), a face-down one (null), or an empty spot (''). opts: size, cls, and key / seq to deal it.
  function card(c, opts = {}) {
    const size = opts.size ? ` ${opts.size}` : '';
    const anim = c === '' ? { cls: '', style: '' } : dealt(opts.key, c, opts);
    const extra = (opts.cls ? ` ${opts.cls}` : '') + anim.cls;
    const style = anim.style;
    if (c === '') return `<span class="pcard empty${size}${extra}" aria-hidden="true"></span>`;
    if (!c) return `<span class="pcard back${size}${extra}"${style} role="img" aria-label="Face-down card"></span>`;
    const r = c[0], s = c[1];
    const red = s === 'h' || s === 'd';
    const label = `${RANK_NAME[r] || r} of ${SUIT_NAME[s]}`;
    if (opts.fix) { // Onkey's save: the number in the corner (opts.fix's rank) crossed out, the new one written beside it in pen
      const wr = opts.fix[0];
      return `<span class="pcard fixed${red ? ' red' : ''}${size}${extra}${anim.cls ? ' fix-in' : ''}"${style} role="img" aria-label="${label}, corrected by Onkey from the ${RANK_NAME[wr] || wr} of ${SUIT_NAME[s]}">` +
        `<span class="pc-corner"><b class="pc-was">${RANK[wr] || wr}<span class="pc-scrawl" aria-hidden="true">${RANK[r] || r}</span></b><i>${SUIT[s]}</i></span>` +
        `<span class="pc-pip" aria-hidden="true">${SUIT[s]}</span></span>`;
    }
    return `<span class="pcard${red ? ' red' : ''}${size}${extra}"${style} role="img" aria-label="${label}">` +
      `<span class="pc-corner"><b>${RANK[r] || r}</b><i>${SUIT[s]}</i></span><span class="pc-pip" aria-hidden="true">${SUIT[s]}</span></span>`;
  }
  // A row of cards; with opts.key each is dealt as `${key}:${i}`, seq from opts.seq(i). opts.saved ({index, was}) marks
  // the card Onkey fixed.
  const cards = (list, opts = {}) => (list || []).map((c, i) => card(c, { ...opts, key: opts.key ? `${opts.key}:${i}` : undefined,
    seq: opts.seq ? opts.seq(i) : 0, fix: opts.saved && opts.saved.index === i ? opts.saved.was : undefined })).join('');

  // ---- Onkey the dealer ------------------------------------------------------------------
  // What Onkey says, by log event. {name} and {amount} are filled in; one line is picked at random.
  const QUIPS = {
    side: ['{name} hits a {hand} on the side! +{amount}.', 'Side bet lands: {hand}. Onkey did not see that coming.',
      '{hand}! {name} collects {amount} on the side. Onkey is counting his bananas.'],
    streak: ['That\'s {n} in a row for {name}. Onkey is checking the deck for bananas.', '{n} straight wins? Onkey would like to speak to the manager. Onkey is the manager.',
      'Onkey is starting to think {name} can see through cards.', '{name} has won {n} in a row. Onkey is sweating through his fur.'],
    slump: ['{n} in a row against {name}. Onkey feels bad. Not bad enough to stop.', 'Rough patch, {name}. Onkey brought you a banana.',
      '{name}, the cards owe you one. Onkey will remind them.', '{n} losses straight. Onkey is shuffling extra nicely for you, {name}.'],
    tip: ['Thank you, {name}! Onkey will buy a banana with this.', 'A tip! {name}, you are Onkey\'s favourite. Today.',
      'Onkey accepts your {amount} with great dignity. Eek!', 'Onkey will remember this kindness, {name}. Probably.'],
    tip_big: ['{amount}?! {name}, Onkey is going to cry.', 'Big tipper! Onkey owes you a banana, {name}. Several.',
      'Onkey is framing this tip, {name}. Right next to the jackpot.'],
    banana: ['Hey! No throwing bananas at Onkey!', '{name} threw a banana. Onkey is eating it as evidence.', 'Rude. Delicious, but rude.',
      'Onkey will remember this, {name}.'],
    save: ['Whoops. That card had a typo. Onkey fixed it. 21!', 'Bust? Onkey sees no bust. Onkey sees 21.',
      'That was {was}. Now it\'s {card}. Onkey has a pen and no shame.', 'Don\'t ask questions. Enjoy your 21.',
      'Onkey\'s handwriting says {card}. The handwriting is final.'],
    greet: ['Welcome to Onkey\'s table. No throwing bananas at Onkey.', 'Onkey deals. Onkey judges. Sit down.', 'Step right up. The house always has bananas.'],
    shuffle: ['New shoe. Onkey shuffles like a pro.', 'Shuffling. No peeking, you animals.'],
    deal: ['Cards coming out.', 'Fresh hands, fresh hopes.', 'Here we go. Good luck, you\'ll need it.'],
    blackjack: ['Blackjack! {name} gets paid 3 to 2.', 'Twenty-one, first try. Show-off.', '{name} hits blackjack. Onkey is impressed. Slightly.'],
    dealer_blackjack: ['Onkey has blackjack. Sorry, not sorry.', 'Ace and a face. Onkey wins.'],
    bust: ['Too many! {name} busts.', '{name} went bananas. Bust.', 'Over 21. Onkey saw that coming.'],
    dealer_bust: ['Onkey busts! Everybody still standing gets paid.', 'Onkey went over. Nobody saw that. Right?'],
    double: ['Doubling down? Bold monkey.', '{name} doubles. Onkey respects the confidence.'],
    split: ['Splitting? Two hands, twice the trouble.', '{name} splits. Onkey loves extra work.'],
    win: ['{name} wins {amount}. Onkey is mildly upset.', 'Onkey pays up. {name} takes {amount}.'],
    push: ['A push. Nobody\'s happy, nobody\'s sad.'],
    lose: ['Onkey takes {amount}. Thank you kindly.', 'The house thanks {name} for the {amount}.'],
    timeout: ['{name} fell asleep. Onkey moves on.', 'Tick tock, {name}. Too slow.'],
    sit: ['Pull up a stool, {name}.', 'Welcome, {name}. Leave your bananas at the door.'],
    leave: ['Bye, {name}. Come back with more credits.', '{name} walks away. Smart, or scared?'],
    hand: ['Shuffle up and deal.', 'Blinds are in. Let\'s go.', 'New hand. Onkey sees everything.'],
    start: ['Everyone\'s ready. Onkey deals!', 'Game on. May the best monkey win.'],
    stop: ['Not enough players. Onkey takes a break.'],
    allin: ['All in! {name} is feeling brave.', '{name} shoves! Somebody call the zoo.', 'All the chips. {name} has no fear.'],
    raise: ['{name} raises. Spicy.', 'A raise from {name}. Onkey smells a bluff.'],
    bet: ['{name} bets {amount}.', '{name} leads out.'],
    call: ['{name} calls.'],
    check: ['{name} checks.'],
    fold: ['{name} folds. Wise. Or cowardly.', '{name} lets it go.'],
    flop: ['Here comes the flop.', 'The flop. Onkey likes this one.'],
    turn: ['The turn.', 'Fourth card. Things are heating up.'],
    river: ['The river. Last chance.', 'River card. Read it and weep.'],
    pwin: ['{name} takes the pot of {amount}.', '{name} drags {amount}. Onkey keeps a banana for the house.', 'Ship it to {name}!'],
    chop: ['Chop it up! The pot is split.', 'A split pot. Share your bananas nicely.'],
    sit_out: ['{name} is napping. Sitting them out.'],
    bust_out: ['{name} is out of chips. Top up or go home.'],
    settings: ['New rules. Everyone ready up again.', 'Rules changed. Ready up, monkeys.'],
    ready: ['{name} is ready.', '{name} wants to play.'],
    topup: ['{name} reloads. Onkey approves.'],
  };
  // Lines that excite Onkey get more squeaks (eeks) than grunts (ooks).
  const EXCITED = new Set(['tip', 'tip_big', 'greg_out', 'save', 'side', 'streak', 'banana', 'blackjack', 'dealer_bust', 'allin', 'pwin', 'chop', 'win', 'start', 'double', 'split', 'bust', 'entrance']);
  const pick = (list) => list[Math.floor(Math.random() * list.length)];
  function quip(kind, vars = {}) {
    const list = QUIPS[kind];
    if (!list) return '';
    return pick(list).replace(/\{(\w+)\}/g, (_, k) => (vars[k] != null ? vars[k] : ''));
  }
  function dealer(id, opts = {}) {
    return `<div class="onkey-dealer${opts.cls ? ' ' + opts.cls : ''}" id="${id}">` +
      `<img class="onkey-face" src="/assets/onkey-logo.png" alt="Onkey, the dealer" width="88" height="51">` +
      `<div class="onkey-bubble" role="status" aria-live="polite"><span class="ook" aria-hidden="true"></span><span class="say"></span></div></div>`;
  }
  // Onkey speaks: the bubble shows the monkey noises, then what they mean, while the chatter plays. Greg (kind 'greg')
  // just talks. `tone` tints the bubble: 'gold' for blackjack's hints and peeks, 'red' when a peek turns out a lie.
  // On a phone (style.css's phone block) there's no dealer on the felt: Onkey stays in the top-left logo and says the
  // dealer's lines from there (onkey.js speak()).
  const onPhone = () => matchMedia('(max-width: 640px)').matches;
  const timers = new WeakMap();
  function say(el, kind, vars, line, tone) {
    if (!el) return;
    const text = line || (typeof kind === 'string' && QUIPS[kind] ? quip(kind, vars) : String(kind || ''));
    if (!text) return;
    if (onPhone()) { window.FiveOnkey?.speak(text, { loud: true, excited: EXCITED.has(kind), table: true, tone }); return; }
    const bubble = el.querySelector('.onkey-bubble');
    const words = text.split(/\s+/).length;
    const n = Math.max(2, Math.min(6, Math.round(words / 2)));
    const excited = EXCITED.has(kind);
    const noises = Array.from({ length: n }, (_, i) => (excited ? (i % 3 === 2 ? 'ook' : 'eek') : (i % 3 === 1 ? 'eek' : 'ook')));
    bubble.querySelector('.ook').textContent = kind === 'greg' ? '*Greg clears his throat*' : kind === 'sci' ? '*he adjusts his glasses*'
      : noises.map((x) => x[0].toUpperCase() + x.slice(1) + (excited ? '!' : '')).join(' ');
    bubble.querySelector('.say').textContent = text;
    bubble.classList.toggle('tone-gold', tone === 'gold');
    bubble.classList.toggle('tone-red', tone === 'red');
    bubble.classList.toggle('tone-sci', kind === 'sci');
    el.classList.remove('talking');
    void el.offsetWidth; // restart the animation
    el.classList.add('talking');
    if (kind !== 'greg' && kind !== 'sci') chatter(noises);
    clearTimeout(timers.get(el));
    timers.set(el, setTimeout(() => el.classList.remove('talking'), 4500 + words * 240)); // a 10-word line about 7 seconds
  }
  // The log entries since the last one seen, as one line from Onkey (the most interesting one).
  const PRIORITY = ['tip_big', 'tip', 'save', 'side', 'streak', 'slump', 'dealer_blackjack', 'blackjack', 'allin', 'pwin', 'chop', 'dealer_bust', 'start', 'settings', 'bust_out',
    'sit_out', 'timeout', 'double', 'split', 'bust', 'win', 'raise', 'shuffle', 'hand', 'deal', 'river', 'turn', 'flop',
    'stop', 'sit', 'leave', 'topup', 'ready', 'bet', 'lose', 'push', 'fold', 'call', 'check'];
  function react(el, entries, kindOf = (e) => e.kind, varsOf = (e) => ({ name: e.bettor, amount: e.amount })) {
    let best = null, rank = Infinity;
    for (const e of entries) {
      const k = kindOf(e);
      const r = PRIORITY.indexOf(k);
      if (r >= 0 && r < rank) { best = e; rank = r; }
    }
    if (!best) return;
    // Someone who bought an Entrance is announced with their own line.
    const entrance = best.kind === 'sit' && best.bettor && wornBy(best.bettor, 'entrance');
    if (entrance) say(el, 'entrance', {}, entrance.text.replace(/\{name\}/g, best.bettor));
    else say(el, kindOf(best), varsOf(best));
  }

  // ---- Greg, and the scientist ------------------------------------------------------------------
  // Now and then (an intruder's `chance` of the times Onkey walks to a table, onkey.js's 'onkey:walking') somebody else
  // is sitting in the dealer's chair when you get there: Greg, who has never dealt before, or more rarely the scientist
  // from Onkey's lore (docs/onkey-lore.md), who talks in his accent. He says his piece while Onkey waits in the logo
  // (GREG_TALK_MS), then Onkey walks over; when he arrives ('onkey:seated', or straight away if he's already there), and
  // the intruder has had GREG_MIN_MS to talk, Onkey kicks him out of the chair with a line of his own.
  const GREG_MIN_MS = 3200, GREG_TALK_MS = 2800; // Onkey stays in the logo GREG_TALK_MS while the intruder talks
  const INTRUDERS = [
    { key: 'greg', chance: 0.05, img: '/assets/greg-logo.png', alt: 'Greg, in the dealer\'s chair',
      in: ['Hi! I\'m Greg. I\'ll be your dealer today.', 'Onkey\'s on a banana break. Greg\'s dealing. How hard can it be?',
        'Greg here. Do aces count as one or eleven? Asking for a friend.', 'Welcome to Greg\'s table. Greg has never done this before.'],
      out: ['GREG. Out of Onkey\'s chair. Now.', 'Who let Greg in? Sorry, folks. Onkey is back.', 'Greg, we talked about this. OUT!',
        'Onkey leaves for one banana, and this happens. Shoo, Greg.'] },
    { key: 'sci', chance: 0.02, img: '/assets/scientist-face.png', alt: 'The scientist, in the dealer\'s chair',
      in: ['Sit. I will deal zis hand. Ze monkey is... busy.', 'Good evening. Ze dealer has been replaced. Permanently, I hope.',
        'Do not look for ze monkey. Look at ze cards.', 'I am told zis game is about numbers. I am very good wiz numbers.'],
      out: ['No. Not him. Not at Onkey\'s table. OUT.', 'Onkey knows that lab coat. Out of the chair.', 'Onkey is not for sale. Neither is this seat.',
        'He followed Onkey here. He follows Onkey everywhere. Go.'] },
  ];
  let greg = null;
  const gregHere = () => !!(greg && greg.el.isConnected);
  document.addEventListener('onkey:walking', (e) => {
    let roll = Math.random();
    const who = INTRUDERS.find((x) => { if (roll < x.chance) return true; roll -= x.chance; return false; });
    if (!who) return;
    if (e.detail) e.detail.hold = GREG_TALK_MS; // Onkey waits for the intruder's line before he sets off
    const t0 = Date.now();
    const look = () => {
      const el = document.querySelector('.onkey-dealer');
      if (el) seatGreg(el, true, who);
      else if (Date.now() - t0 < 5000) setTimeout(look, 60);
    };
    look();
  });
  document.addEventListener('onkey:seated', () => { if (greg) kickLater(greg); });
  function seatGreg(el, walking, who = INTRUDERS[0]) {
    const face = el.querySelector('.onkey-face');
    if (!face) return;
    el.classList.add('greg', `intruder-${who.key}`);
    face.src = who.img;
    face.alt = who.alt;
    greg = { el, who, since: Date.now() };
    say(el, who.key === 'sci' ? 'sci' : 'greg', {}, pick(who.in));
    if (!walking || !document.documentElement.classList.contains('onkey-walking')) kickLater(greg);
  }
  function kickLater(g) {
    setTimeout(() => kickGreg(g), Math.max(0, GREG_MIN_MS - (Date.now() - g.since)));
  }
  function kickGreg(g) {
    if (greg !== g) return;
    greg = null;
    const el = g.el, face = el.querySelector('.onkey-face');
    if (!el.isConnected || !face) return;
    const r = face.getBoundingClientRect();
    const fly = document.createElement('img');
    fly.src = g.who.img;
    fly.alt = '';
    fly.className = 'greg-flying';
    Object.assign(fly.style, { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px` });
    document.body.appendChild(fly);
    face.src = '/assets/onkey-logo.png';
    face.alt = 'Onkey, the dealer';
    el.classList.remove('greg', 'bonked', `intruder-${g.who.key}`);
    void el.offsetWidth;
    el.classList.add('bonked');
    fly.animate([{ transform: 'translate(0, 0) rotate(0deg)', opacity: 1 },
      { transform: 'translate(160px, -70px) rotate(220deg)', opacity: 1, offset: 0.45 },
      { transform: 'translate(520px, 260px) rotate(620deg) scale(0.5)', opacity: 0 }], { duration: 1100, easing: 'cubic-bezier(0.25, 0.1, 0.6, 1)', fill: 'forwards' })
      .onfinish = () => fly.remove();
    say(el, 'greg_out', {}, pick(g.who.out));
  }

  // ---- monkey noises (Web Audio, nothing to download) ---------------------------------------------
  function ctx() {
    if (muted) return null;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      if (audio.state === 'suspended') audio.resume();
      return audio;
    } catch (e) { return null; }
  }
  // One syllable: "ook" is a low grunt that falls, "eek" a squeak that rises. A sawtooth through a band-pass filter
  // gives it a throaty, vowel-ish sound.
  function syllable(a, kind, at) {
    const o = a.createOscillator(), f = a.createBiquadFilter(), g = a.createGain();
    const low = kind === 'ook';
    const base = (low ? 190 : 760) * (0.9 + Math.random() * 0.25);
    o.type = low ? 'sawtooth' : 'square';
    o.frequency.setValueAtTime(base, at);
    o.frequency.exponentialRampToValueAtTime(low ? base * 0.7 : base * 1.6, at + (low ? 0.13 : 0.09));
    f.type = 'bandpass';
    f.frequency.setValueAtTime(low ? 650 : 1900, at);
    f.Q.value = low ? 5 : 8;
    g.gain.setValueAtTime(0.0001, at);
    g.gain.exponentialRampToValueAtTime(low ? 0.22 : 0.12, at + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, at + (low ? 0.16 : 0.11));
    o.connect(f); f.connect(g); g.connect(a.destination);
    o.start(at); o.stop(at + 0.2);
  }
  function chatter(noises) {
    const a = ctx();
    if (!a) return;
    let t = a.currentTime + 0.02;
    for (const n of noises) { syllable(a, n, t); t += n === 'ook' ? 0.17 : 0.12; }
  }
  // Small table sounds: a card flick, chips, a win.
  function sound(kind) {
    const a = ctx();
    if (!a) return;
    const t = a.currentTime + 0.01;
    if (kind === 'card') {
      // A card sliding onto felt: a short puff of noise that swells in rather than clicking, kept to the mids (a
      // band round 1.1 kHz, nothing above 3 kHz) so it's a soft "fwip" and not a hiss.
      const len = 0.07, buf = a.createBuffer(1, a.sampleRate * len, a.sampleRate), d = buf.getChannelData(0);
      for (let i = 0; i < d.length; i++) {
        const p = i / d.length;
        d[i] = (Math.random() * 2 - 1) * Math.min(1, p / 0.12) * (1 - p) ** 2;
      }
      const src = a.createBufferSource(), band = a.createBiquadFilter(), top = a.createBiquadFilter(), g = a.createGain();
      src.buffer = buf;
      band.type = 'bandpass'; band.frequency.value = 1100; band.Q.value = 0.7;
      top.type = 'lowpass'; top.frequency.value = 3000;
      g.gain.value = 0.22;
      src.connect(band); band.connect(top); top.connect(g); g.connect(a.destination); src.start(t);
    } else if (kind === 'chips') {
      for (let i = 0; i < 3; i++) {
        const o = a.createOscillator(), g = a.createGain();
        o.type = 'triangle'; o.frequency.value = 2400 + Math.random() * 900;
        g.gain.setValueAtTime(0.08, t + i * 0.045); g.gain.exponentialRampToValueAtTime(0.0001, t + i * 0.045 + 0.06);
        o.connect(g); g.connect(a.destination); o.start(t + i * 0.045); o.stop(t + i * 0.045 + 0.08);
      }
    } else if (kind === 'win') {
      [523, 659, 784, 1047].forEach((hz, i) => {
        const o = a.createOscillator(), g = a.createGain();
        o.type = 'triangle'; o.frequency.value = hz;
        g.gain.setValueAtTime(0.0001, t + i * 0.09); g.gain.exponentialRampToValueAtTime(0.14, t + i * 0.09 + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t + i * 0.09 + 0.3);
        o.connect(g); g.connect(a.destination); o.start(t + i * 0.09); o.stop(t + i * 0.09 + 0.35);
      });
    }
  }
  const speaker = () => `<button type="button" class="btn ghost icon casino-mute" aria-pressed="${!muted}" aria-label="${muted ? 'Turn sound on' : 'Turn sound off'}" title="${muted ? 'Sound off' : 'Sound on'}">${window.speakerIcon(muted, 18)}</button>`;
  function bindSpeaker(root) {
    root.querySelectorAll('.casino-mute').forEach((b) => b.addEventListener('click', () => {
      muted = !muted;
      try { localStorage.setItem('fs.casinoMuted', muted ? '1' : '0'); } catch (e) { /* unavailable */ }
      b.outerHTML = speaker();
      bindSpeaker(root);
      if (!muted) chatter(['ook', 'eek']);
    }));
  }

  // ---- live pages -------------------------------------------------------------------------------
  // Replace a region's HTML only when it changed, keeping keyboard focus (and a text field's caret) where it was.
  function patch(el, html) {
    if (!el || el._html === html) return false;
    const active = document.activeElement;
    const keep = active && el.contains(active) && active.id ? { id: active.id, start: active.selectionStart, end: active.selectionEnd } : null;
    el.innerHTML = html;
    el._html = html;
    if (keep) {
      const again = document.getElementById(keep.id);
      if (again) {
        again.focus();
        try { if (keep.start != null) again.setSelectionRange(keep.start, keep.end); } catch (e) { /* not a text field */ }
      }
    }
    return true;
  }
  // A long-poll loop: `fetchNext(version)` waits on the server for a change; `apply(data)` takes it. It stops when
  // `alive()` turns false (the page was left) and backs off for a few seconds after an error.
  function live({ alive, fetchNext, apply, version }) {
    let running = true;
    (async () => {
      while (running && alive()) {
        try {
          const d = await fetchNext(version());
          if (!running || !alive()) break;
          apply(d);
        } catch (e) {
          await new Promise((r) => setTimeout(r, 3000));
        }
      }
      running = false;
    })();
    return { stop: () => { running = false; }, get running() { return running; } };
  }
  // Countdowns: any element with data-deadline (server time, seconds) shows the seconds left; a .timer-bar with
  // data-deadline and data-span shrinks. `offset` is the server's clock minus ours.
  let offset = 0;
  const syncClock = (serverTime) => { if (serverTime) offset = serverTime - Date.now() / 1000; };
  const secondsLeft = (deadline) => Math.max(0, deadline - (Date.now() / 1000 + offset));
  setInterval(() => {
    document.querySelectorAll('[data-deadline]').forEach((el) => {
      const left = secondsLeft(+el.dataset.deadline);
      if (el.classList.contains('timer-bar')) el.style.setProperty('--left', String(Math.min(1, left / (+el.dataset.span || 30))));
      else el.textContent = Math.ceil(left) + 's';
      el.classList.toggle('urgent', left < 6);
    });
  }, 250);
  const timer = (deadline, span) => (deadline ? `<span class="timer-bar" data-deadline="${deadline}" data-span="${span}" style="--left:${Math.min(1, secondsLeft(deadline) / span)}"></span>` : '');
  const countdown = (deadline) => (deadline ? `<b class="countdown" data-deadline="${deadline}">${Math.ceil(secondsLeft(deadline))}s</b>` : '');
  const ref = () => (crypto.randomUUID ? crypto.randomUUID() : `r${Date.now()}${Math.random().toString(36).slice(2)}`).replace(/[^A-Za-z0-9-]/g, '').padEnd(16, '0');

  return { init, denom, chip, chipFace, gregHere, card, cards, dealer, say, react, quip, sound, chatter, speaker, bindSpeaker, patch, live, syncClock, secondsLeft, timer, countdown, ref,
    absorb, wornBy, style, who, title, seatBurst, deal, dealReset, landing, after };
})();
