/* Blackjack against Onkey: your own solo table, or the one shared table where up to five play the same dealer hand.
   The server deals and settles everything; this page draws the felt, sends your moves and long-polls the shared
   table. Onkey (FiveCasino) comments on what happens. */
window.FiveBlackjack = (() => {
  'use strict';
  let state, $, api, draw, esc, fmt, loadMe, confetti, plainName;
  const C = window.FiveCasino;
  let which = localStorage.getItem('fs.bjTable') === 'shared' ? 'shared' : 'solo';
  const data = { solo: null, shared: null };
  const seen = { solo: null, shared: null };   // the last log entry Onkey reacted to, per table
  let stake = +(localStorage.getItem('fs.bjStake') || 10), busy = false, error = '', loop = null, owner;
  // Side bets on the next deal ({pairs, plus3}: 0 or a stake no bigger than the main one), remembered like the stake.
  let side = (() => { try { return { pairs: 0, plus3: 0, ...JSON.parse(localStorage.getItem('fs.bjSide') || '{}') }; } catch (_) { return { pairs: 0, plus3: 0 }; } })();
  const SIDE_LABEL = { pairs: 'Pairs', plus3: '21+3' };
  const sidesOpen = () => !!data[which]?.side_bets?.open; // blackjack.py SIDE_BETS_OPEN: off for now
  const sideFor = () => (sidesOpen() ? { pairs: side.pairs <= stake ? side.pairs : 0, plus3: side.plus3 <= stake ? side.plus3 : 0 } : { pairs: 0, plus3: 0 });
  const saveSide = () => { try { localStorage.setItem('fs.bjSide', JSON.stringify(side)); } catch (_) { /* unavailable */ } };
  // Chips that move: a bet slides in, winnings fly over from Onkey, a lost stake is swept away. motion(key, cls, ms,
  // delay) plays an animation once per key; drawn again mid-flight it keeps its place (a negative delay), and once it's
  // over the element gets cls-done. A table just opened shows its chips where they are.
  const motions = new Map();
  let stillUntil = 0;
  function motion(key, cls, ms, delay = 0) {
    const now = performance.now();
    if (!motions.has(key)) motions.set(key, now < stillUntil || C.reduced() ? -1e9 : now + delay);
    const ago = now - motions.get(key);
    if (motions.size > 400) [...motions.keys()].slice(0, 200).forEach((k) => motions.delete(k));
    return ago > ms ? { cls: ` ${cls}-done`, style: '' } : { cls: ` ${cls}`, style: ` style="animation-delay:${Math.round(-ago)}ms"` };
  }
  // Onkey's hint: when it's your move and you haven't made it HINT_MS after the cards land, he suggests what the numbers
  // favour (me.hint from the server: basic strategy, every legal move's value per credit staked) and that move's
  // button lights up. Once per decision.
  const HINT_MS = 3000;
  // He doesn't hint every time: HINT_CHANCE of the pauses, at most once a round, and never in the round after one.
  const HINT_CHANCE = 1 / 3;
  const hintRounds = new Set();
  const MOVE = { hit: 'hit', stand: 'stand', double: 'double down', split: 'split' };
  let hintTimer = null, hintFor = '', hintShown = '';
  // Onkey's peek (me.peek / me.peek_result, see blackjack.py): when the pause comes on a decision he's peeked on, he
  // says the card instead of a hint, and once it shows he gloats or owns up. Only claims he actually said get a follow-up.
  const peeked = new Set(), followed = new Set();
  const peekKey = (d, pk) => `${d.table}:${d.round}:${pk.kind}:${pk.step}`;
  function init(ctx) { ({ state, $, api, draw, esc, fmt, loadMe, confetti, plainName } = ctx); }

  const url = (since) => `/api/blackjack?table=${which}${since != null ? `&since=${since}` : ''}`;
  async function load() {
    const name = state.me?.name || null;
    if (owner !== name) { owner = name; data.solo = data.shared = null; seen.solo = seen.shared = null; C.dealReset(); }
    stillUntil = performance.now() + 600;
    apply(await api(url()));
  }
  // A fresh table state from the server: patch the regions that changed, and let Onkey react to what happened.
  function apply(d) {
    const t = d.table === 'shared' ? 'shared' : 'solo';
    const old = data[t];
    if (old && d.version < old.version && t === 'shared') return; // an older answer arriving late
    data[t] = d;
    C.syncClock(d.server_time);
    C.absorb(d.looks);
    const fresh = (d.log || []).filter((e) => seen[t] != null && e.id > seen[t]);
    seen[t] = d.log?.length ? d.log[d.log.length - 1].id : seen[t] ?? 0;
    if (state.view !== 'blackjack' || t !== which) return;
    if (!$('#bj-root')) { planHint(d, 0); return; } // a table just opened: drawn without dealing
    fresh.filter((e) => e.kind === 'emote').forEach(popEmote); // emotes show at once, not after the cards
    refresh();
    const wait = C.landing();
    if (wait > 0) setTimeout(() => { if (state.view === 'blackjack' && $('#bj-root')) refresh(); }, wait + 30); // controls come back once the cards are down
    const pr = d.me?.peek_result, prKey = pr && peekKey(d, pr);
    const follow = pr && peeked.has(prKey) && !followed.has(prKey);
    if (follow) {
      followed.add(prKey);
      setTimeout(() => { if (state.view === 'blackjack' && $('#bj-root')) C.say($('#bj-onkey'), 'peek', {}, followLine(pr), pr.honest ? 'gold' : 'red'); }, wait + 150);
    }
    if (fresh.length) setTimeout(() => react(fresh, follow), wait);
    planHint(d, wait);
  }
  // A decision is a table, round and step; it gets one hint timer.
  const decision = (d) => (d.me?.hint ? `${d.table}:${d.round}:${d.me.step}` : '');
  function planHint(d, wait) {
    const key = decision(d);
    if (key === hintFor) return;
    clearTimeout(hintTimer);
    hintFor = key;
    if (!key) return;
    hintTimer = setTimeout(() => {
      const now = data[which];
      if (state.view !== 'blackjack' || !$('#bj-root') || busy || decision(now) !== key) return;
      const pk = now.me.peek;
      if (pk) { // a peek replaces the hint, and no button lights up
        peeked.add(peekKey(now, pk));
        C.say($('#bj-onkey'), 'peek', {}, peekLine(now, pk), 'gold');
        return;
      }
      const round = `${now.table}:${now.round}`;
      if (hintRounds.has(round) || hintRounds.has(`${now.table}:${now.round - 1}`) || Math.random() >= HINT_CHANCE) return;
      hintRounds.add(round);
      hintShown = key;
      C.say($('#bj-onkey'), 'hint', {}, hintLine(now), 'gold');
      refresh();
    }, wait + HINT_MS);
  }
  // What Onkey says. He's the dealer, so he talks about himself as "Onkey" (or "I"), never "him" or "the dealer".
  //  - close: a shrug, when the best two moves are within CLOSE of each other;
  //  - obvious: sarcasm (OBVIOUS_SARCASM of the time), when the best move beats the next by OBVIOUS or more and you've
  //    still sat there for HINT_MS;
  //  - weak: a dig at his own upcard, half the time you should stand against his 2-6;
  //  - otherwise secret (the dealer slipping you help he shouldn't), funny or plain, by HINT_MIX.
  // Blanks: {total} (your hand, "soft 18"), {up} (his upcard, "a 6"), {pair} ("eights"), {move} / {Move}.
  const CLOSE = 0.01, OBVIOUS = 0.3, OBVIOUS_SARCASM = 0.6, HINT_MIX = { secret: 0.3, funny: 0.3 };
  const HINTS = {
    plain: {
      hit: ['Psst. Onkey would hit here.', 'Take another card. Trust Onkey.', '{total} won\'t hold up. Hit it.', 'Onkey says hit.'],
      stand: ['Psst. Onkey would stand.', '{total} is enough. Stand pat.', 'Stand. Make Onkey do the work.', 'Happy with {total}? Onkey is. Stand.'],
      double: ['This one\'s a double, friend.', 'Psst. Double it. You\'ll thank Onkey later.', '{total} against {up}? Onkey would double.'],
      split: ['Split them. Two hands are better than one here.', 'Psst. Onkey would split those.', 'Break them up. Split.'],
    },
    funny: {
      hit: ['Hit. Onkey has a good feeling. Onkey always has a good feeling.', 'Another card, please. Onkey\'s hands are bored.',
        'Hit it like it owes you bananas.', '{total}? That\'s not a hand, that\'s a cry for help. Hit.'],
      stand: ['Stand. Onkey is about to bust. Probably. Maybe.', 'Sit tight and let Onkey embarrass himself.',
        'Stand. Greedy monkeys get burned.', 'Don\'t touch it. Just... don\'t touch it.'],
      double: ['Double down. Fortune favours the bold monkey.', 'Double. Onkey would bet the whole banana on this.',
        'Double it. What could go wrong? (A lot. But still.)'],
      split: ['Split! Twice the hands, twice the drama.', 'Split them like a banana. Right down the middle.', 'Split. Onkey loves extra paperwork.'],
    },
    secret: ['Onkey isn\'t supposed to say this, but... {move}.', 'Don\'t tell the house Onkey said this: {move}.',
      'Onkey is the dealer. Onkey should not be helping you. {Move}.', 'If anyone asks, you figured this out yourself. {Move}.',
      'Onkey could get fired for this. {Move}. You didn\'t hear it from Onkey.', '{Move}. Act natural. The house is watching.',
      '*whispers* {move}. *stops whispering* Nice weather today.'],
    obvious: {
      hit: ['{total}. You can\'t bust. Onkey checked. Hit.', 'Is this a trick question? Hit.', 'Onkey has seen bananas make faster decisions. Hit.',
        'Take your time. It\'s only {total}. (Hit.)'],
      stand: ['You\'ve got {total}. You\'re really thinking about this?', 'It\'s {total}. Onkey will wait. Onkey has all day. Stand.',
        '{total}. Onkey is begging you not to hit that.', 'Wow. Big decision. Stand on {total}. Phew.'],
      double: ['{total} against {up}. Onkey is begging you. Double.', 'Onkey won\'t say it twice. Double. Onkey will probably say it twice.',
        'Still thinking? It\'s {total}. The double button is right there.'],
      split: ['Two {pair}. Split them. This isn\'t a test.', 'Split. Onkey can hear the clock ticking.', 'Two {pair}? Onkey doesn\'t even need to look. Split.'],
    },
    weak: ['Onkey\'s showing {up}. Stand and let Onkey sweat.', '{up} up? Onkey is nervous. Stand.'], // stand against a 2-6
    close: ['It\'s a coin flip. Onkey leans {move}, by a whisker.', 'Honestly? Either way. Onkey would {move}.',
      'Tough one. Onkey\'s gut says {move}. Onkey\'s gut is often hungry.'],
  };
  // The peek: the next card in the shoe (would it bust you?) or his own hole card (does it leave him on 17-21?), and
  // the follow-up once it shows, honest or not.
  const PEEK = {
    next_bust: ['Onkey took a little peek. The next card is {card}. That\'s a bust, friend.', 'Don\'t hit. Onkey saw {card} on top of the shoe. Trust Onkey.',
      'Psst. {Card} is next. Onkey would keep his paws off.'],
    next_safe: ['Onkey peeked. The next card is {card}. It won\'t bust you.', 'Psst. {Card} on top of the shoe. You didn\'t hear it from Onkey.',
      'Little secret: the next card is {card}. Onkey is a terrible dealer.'],
    hole_strong: ['Onkey peeked at Onkey\'s own hole card. It\'s {card}. Onkey is sitting pretty.', 'Between us, Onkey has {card} under here. Onkey feels great about it.',
      '{Card} under here. Onkey shouldn\'t have told you that.'],
    hole_weak: ['Between us, Onkey has {card} under here. Onkey is sweating.', 'Onkey peeked: {card} in the hole. Onkey might bust. Don\'t tell anyone.',
      '{Card} under here. Onkey is in trouble. Shhh.'],
    honest: ['Told you. {Actual}. Onkey never lies. Usually.', 'See? {Actual}. Onkey is a monkey of his word.', 'Onkey called it. {Actual}. You\'re welcome.'],
    lie: ['Ha! It was {actual}. Onkey lied. Onkey is the house, what did you expect?', 'Gotcha. {Actual}, not {claimed}. Never trust Onkey.',
      'Oops. Onkey must have misread it. It was {actual}. Totally an accident.'],
  };
  const cardName = (r) => ({ A: 'an ace', K: 'a king', Q: 'a queen', J: 'a jack', T: 'a ten', 8: 'an 8' }[r] || `a ${r}`);
  const cap = (x) => x[0].toUpperCase() + x.slice(1);
  const value = (r) => (r === 'A' ? 1 : 'TJQK'.includes(r) ? 10 : +r);
  const fillIn = (line, vars) => line.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? '');
  function peekLine(d, pk) {
    const card = cardName(pk.card);
    let list;
    if (pk.kind === 'next') {
      const hand = d.seats.find((s) => s.bettor === d.me.name)?.hands[d.turn.hand];
      list = hand && !hand.soft && hand.total + value(pk.card) > 21 ? PEEK.next_bust : PEEK.next_safe;
    } else {
      const up = (d.dealer.cards[0] || '2')[0];
      let tot = value(up) + value(pk.card);
      if ((up === 'A' || pk.card === 'A') && tot + 10 <= 21) tot += 10;
      list = tot >= 17 ? PEEK.hole_strong : PEEK.hole_weak;
    }
    return fillIn(pick(list), { card, Card: cap(card) });
  }
  function followLine(pr) {
    const actual = cardName(pr.actual), claimed = cardName(pr.claimed);
    return fillIn(pick(pr.honest ? PEEK.honest : PEEK.lie), { actual, Actual: cap(actual), claimed });
  }
  const PAIRS = { A: 'aces', 2: 'twos', 3: 'threes', 4: 'fours', 5: 'fives', 6: 'sixes', 7: 'sevens', 8: 'eights', 9: 'nines' };
  const pick = (list) => list[Math.floor(Math.random() * list.length)];
  function hintLine(d) {
    const h = d.me.hint;
    const ranked = Object.entries(h.ev).sort((a, b) => b[1] - a[1]);
    const [[move, ev], next] = ranked;
    const hand = d.seats.find((s) => s.bettor === d.me.name)?.hands[d.turn.hand];
    const r = (d.dealer.cards[0] || '?')[0], first = (hand?.cards[0] || '?')[0];
    const vars = { move: MOVE[move], Move: MOVE[move][0].toUpperCase() + MOVE[move].slice(1),
      total: hand ? `${hand.soft ? 'soft ' : ''}${hand.total}` : 'that',
      up: r === 'A' ? 'an ace' : r === '8' ? 'an 8' : 'TJQK'.includes(r) ? 'a ten' : `a ${r}`, pair: PAIRS[first] || 'tens' };
    const gap = next ? ev - next[1] : 1, roll = Math.random();
    const list = gap < CLOSE ? HINTS.close
      : gap >= OBVIOUS && roll < OBVIOUS_SARCASM ? HINTS.obvious[move]
        : move === 'stand' && '23456'.includes(r) && roll < 0.5 ? HINTS.weak
          : roll < HINT_MIX.secret ? HINTS.secret
            : roll < HINT_MIX.secret + HINT_MIX.funny ? HINTS.funny[move] : HINTS.plain[move];
    const canBust = hand && !hand.soft && hand.total > 11; // "you can't bust" only when you can't
    const lines = list.filter((l) => !(canBust && /can.t bust/.test(l)));
    return pick(lines).replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? '');
  }
  // What happened, once the cards are down: Onkey's line, Table win bursts, your win.
  function react(fresh, quiet = false) {
    if (state.view !== 'blackjack' || !$('#bj-root')) return;
    if (!quiet) C.react($('#bj-onkey'), fresh, (e) => (e.kind === 'emote' ? '' : e.kind === 'tip' ? (e.amount >= 25 ? 'tip_big' : 'tip') : e.kind), (e) => ({ name: e.bettor || 'Onkey', amount: fmt.credits(Math.abs(e.amount || 0)),
      was: e.was ? cardName(e.was[0]) : '', card: e.card ? cardName(e.card[0]) : '', n: e.n, hand: e.hand ? e.hand.toLowerCase() : '' }));
    fresh.filter((e) => e.kind === 'win' && e.bettor).forEach((e) => C.seatBurst($('#bj-spots'), e.bettor)); // bought Table wins
    const mine = fresh.find((e) => e.bettor === state.me?.name && ['win', 'push', 'lose'].includes(e.kind));
    if (mine?.kind === 'win') {
      C.sound('win');
      if (mine.blackjack || mine.amount >= 100) confetti($('#me-credits'), mine.amount >= 250);
    }
    if (mine) loadMe();
  }
  // An emote pops over the sender's seat (or the felt's corner at the solo table); a banana is thrown at Onkey.
  function popEmote(e) {
    const spot = [...document.querySelectorAll('#bj-spots [data-bettor]')].find((el) => el.dataset.bettor === e.bettor) || $('.bj-felt');
    const face = $('#bj-onkey .onkey-face');
    if (!spot) return;
    const r = spot.getBoundingClientRect();
    const el = document.createElement('span');
    el.className = 'emote-pop';
    el.textContent = e.emote;
    el.style.left = `${r.left + r.width / 2}px`;
    el.style.top = `${r.top + 10}px`;
    document.body.appendChild(el);
    if (e.emote !== '🍌' || !face || C.reduced()) { setTimeout(() => el.remove(), 1900); if (e.emote === '🍌') bonk(e); return; }
    el.classList.add('thrown');
    const f = face.getBoundingClientRect();
    const dx = f.left + f.width / 2 - (r.left + r.width / 2), dy = f.top + f.height / 2 - (r.top + 10);
    el.animate([{ transform: 'translate(-50%, 0) rotate(0deg) scale(1)' },
      { transform: `translate(calc(-50% + ${dx / 2}px), ${dy / 2 - 90}px) rotate(400deg) scale(1.25)`, offset: 0.5 },
      { transform: `translate(calc(-50% + ${dx}px), ${dy}px) rotate(760deg) scale(0.9)` }], { duration: 700, easing: 'ease-in', fill: 'forwards' })
      .onfinish = () => { el.remove(); bonk(e); };
  }
  function bonk(e) {
    const dealer = $('#bj-onkey');
    if (!dealer) return;
    dealer.classList.remove('bonked'); void dealer.offsetWidth; dealer.classList.add('bonked');
    C.say(dealer, 'banana', { name: e.bettor || 'Someone' });
  }
  function startLoop() {
    if (which !== 'shared' || (loop && loop.running)) return;
    loop = C.live({
      alive: () => state.view === 'blackjack' && which === 'shared',
      version: () => data.shared?.version ?? -1,
      fetchNext: (v) => api(url(v)),
      apply,
    });
  }

  // ---- drawing -------------------------------------------------------------------------
  function view() {
    const d = data[which];
    if (!d || (d.me?.name || null) !== (state.me?.name || null)) {
      load().then(() => { if (state.view === 'blackjack') draw(); }).catch((e) => {
        const el = $('#bj-loading'); if (el) el.textContent = `Could not load the table: ${e.message}`;
      });
      return '<section class="card" id="bj-loading">Shuffling the shoe…</section>';
    }
    const seats = d.shared_seats;
    const felt = C.deal(() => ({ dealer: dealerHtml(d), spots: spotsHtml(d), status: statusHtml(d) }));
    return `<div id="bj-root" class="casino-page">
      <div class="casino-bar">
        <div class="seg" role="tablist" aria-label="Table">
          <button class="seg-btn ${which === 'solo' ? 'on' : ''}" role="tab" aria-selected="${which === 'solo'}" data-bj-table="solo">Solo table</button>
          <button class="seg-btn ${which === 'shared' ? 'on' : ''}" role="tab" aria-selected="${which === 'shared'}" data-bj-table="shared">Shared table <span class="seg-count">${seats.taken}/${seats.max}</span></button>
        </div>
        <span class="muted small">${which === 'solo' ? 'Just you and Onkey. The deal waits for you.' : 'Everyone at this table plays the same dealer hand.'}</span>
        ${C.speaker()}
      </div>
      <div class="casino-layout">
        <section class="felt bj-felt" aria-label="Blackjack table">
          <div class="felt-top">${C.dealer('bj-onkey')}<div class="bj-tip" id="bj-tip">${tipHtml(d)}</div><div class="bj-dealer" id="bj-dealer">${felt.dealer}</div></div>
          ${feltPrint()}
          <div class="bj-spots ${which}" id="bj-spots">${felt.spots}</div>
          <div class="felt-status" id="bj-status" role="status" aria-live="polite">${felt.status}</div>
          <div class="felt-controls bj-controls" id="bj-controls"><div class="casino-controls">${controlsHtml(d)}</div></div>
          <div class="bj-emotes" id="bj-emotes">${emotesHtml(d)}</div>
        </section>
        <aside class="casino-side" id="bj-side">${sideHtml(d)}</aside>
      </div>
    </div>`;
  }
  function refresh() {
    const d = data[which];
    const felt = C.deal(() => ({ dealer: dealerHtml(d), spots: spotsHtml(d), status: statusHtml(d) }));
    C.patch($('#bj-dealer'), felt.dealer);
    C.patch($('#bj-spots'), felt.spots);
    C.patch($('#bj-status'), felt.status);
    C.patch($('#bj-controls'), `<div${C.after('casino-controls')}>${controlsHtml(d)}</div>`);
    C.patch($('#bj-side'), sideHtml(d));
    C.patch($('#bj-emotes'), emotesHtml(d));
    C.patch($('#bj-tip'), tipHtml(d));
    const count = $('#bj-root .seg-count');
    if (count) count.textContent = `${d.shared_seats.taken}/${d.shared_seats.max}`;
  }
  // The table print, as on a real layout: BLACKJACK PAYS 3 TO 2 in gold along a curved band edged in gold, the
  // dealer's rule under it in a smaller arc.
  // Both lines are arcs of circles round one centre above the table, so the band, the gold lettering on its centre line
  // and the rule under it all curve together (the lower line runs exactly parallel).
  const FP = { cx: 320, cy: -440, band: 520, sub: 551 };
  function arc(r, x1, x2) {
    const y = (x) => (FP.cy + Math.sqrt(r * r - (x - FP.cx) ** 2)).toFixed(1);
    return `M ${x1} ${y(x1)} A ${r} ${r} 0 0 0 ${x2} ${y(x2)}`;
  }
  function feltPrint() {
    const band = arc(FP.band, 130, 510);
    return `<div class="felt-print"><svg viewBox="0 24 640 98" role="img" aria-label="Blackjack pays 3 to 2. Dealer must stand on all 17s.">
      <defs><path id="fp-mid" d="${band}"/><path id="fp-low" d="${arc(FP.sub, 120, 520)}"/></defs>
      <path class="fp-band-edge" d="${band}"/><path class="fp-band" d="${band}"/>
      <text class="fp-main" dominant-baseline="central"><textPath href="#fp-mid" startOffset="50%" text-anchor="middle">BLACKJACK PAYS 3 TO 2</textPath></text>
      <text class="fp-sub" dominant-baseline="central"><textPath href="#fp-low" startOffset="50%" text-anchor="middle">DEALER MUST STAND ON ALL 17s</textPath></text>
    </svg></div>`;
  }
  function totalBadge(total, soft, extra = '') {
    return `<span class="hand-total ${extra}">${soft ? `${total - 10}/${total}` : total}</span>`;
  }
  function dealerHtml(d) {
    const cs = d.dealer.cards;
    if (!cs.length) return '<div class="bj-cards">' + C.card('', { size: 'xl' }) + C.card('', { size: 'xl' }) + '</div><span class="muted">Onkey is waiting for bets</span>';
    const hidden = cs.includes(null);
    const back = which === 'solo' && d.me ? C.style(d.me.name, 'card_back') : '';
    const key = `bj:${d.table}:${d.round}:dealer`;
    const seq = (i) => (i === 0 ? 99 : i === 1 ? (cs[1] ? 9000 : 199) : 10000 + i);
    return `<div class="bj-cards">${cs.map((c, i) => C.card(c, { size: 'xl', cls: c ? '' : back, key: `${key}:${i}`, seq: seq(i) })).join('')}</div>` +
      `<div${C.after('bj-total')}>${d.dealer.blackjack ? '<span class="hand-total bj">Blackjack</span>' : totalBadge(d.dealer.total, false, d.dealer.total > 21 ? 'bust' : '')}${hidden ? '<span class="muted small">showing</span>' : ''}</div>`;
  }
  function handHtml(h, chips = '', key = '', seat = 0, hi = 0, size = 'xl') {
    const badge = h.blackjack ? '<span class="hand-total bj">21</span>' : totalBadge(h.total, h.soft, h.total > 21 ? 'bust' : '');
    // Mid-hand your bet is the chip beside the action buttons. When the hand settles the chips play out beside the total
    // and leave nothing behind: a lost stake pops up and is swept off to Onkey; winnings fly over from him and both chips
    // slide back down to you; a push's stake slides back to you.
    const land = C.landing();
    const lost = h.result === 'lose' || h.result === 'bust';
    const profit = h.payout - h.stake;
    let chipsHtml = '';
    if (h.result) {
      const m = lost ? motion(`${key}:lost`, 'chip-lost', 1300, land + 150) : motion(`${key}:back`, 'chip-back', 1700, land + 150);
      const pay = profit > 0 ? (() => { const p = motion(`${key}:pay`, 'chip-pay', 1700, land + 150);
        return C.chip(profit, { cls: `${chips}${p.cls}`, style: p.style }); })() : '';
      chipsHtml = C.chip(h.stake, { cls: `${chips}${m.cls}`, style: m.style }) + pay;
    }
    return `<div class="bj-hand ${h.turn ? 'active' : ''} ${h.result || ''}">
      <div class="bj-cards">${C.cards(h.cards, { size, key, saved: h.saved, seq: (i) => (hi === 0 && i < 2 ? i * 100 + seat : 5000 + hi * 10 + i) })}</div>
      <div${C.after('bj-hand-meta')}><span></span>${badge}<span class="bj-hand-chips">${chipsHtml}</span></div></div>`;
  }
  // A seat's side bets: the chips placed before the deal, then what each one hit (or didn't).
  function sidesHtml(s, chips, key) {
    const kinds = Object.keys(s.side || {});
    if (!kinds.length) return '';
    const items = kinds.map((k) => {
      const r = (s.side_results || {})[k];
      if (!r) return `<span class="side-res">${SIDE_LABEL[k]} ${C.chip(s.side[k], { cls: `mini ${chips}` })}</span>`;
      if (r.result) return `<span class="side-res win">${SIDE_LABEL[k]}: ${esc(r.name)} +${fmt.credits(r.payout - r.stake)}</span>`;
      const m = motion(`${key}:side:${k}`, 'chip-lost', 700, C.landing() + 200);
      return `<span class="side-res lose">${SIDE_LABEL[k]} <span class="side-lost">−${fmt.credits(r.stake)}</span>${C.chip(r.stake, { cls: `mini ${chips}${m.cls}`, style: m.style })}</span>`;
    });
    return `<div${C.after('bj-sides')}>${items.join('')}</div>`;
  }
  function spotsHtml(d) {
    const meName = d.me?.name;
    const spots = d.seats.map((s) => {
      const turn = d.turn && d.turn.bettor === s.bettor;
      const chips = C.style(s.bettor, 'chips');
      const seat = d.seats.indexOf(s), key = `bj:${d.table}:${d.round}:${s.bettor}`;
      // A split puts the new hand next to its pair, moving the ones after it along, so a hand's cards are keyed by
      // its first card (and how many earlier hands began with the same one), not its place.
      const firsts = {};
      const handKey = (h) => { const c = h.cards[0] || ''; firsts[c] = (firsts[c] || 0) + 1; return `${key}:${c}${firsts[c]}`; };
      const size = which === 'solo' && s.hands.length <= 2 ? 'xl' : s.hands.length > 3 || (which === 'shared' && s.hands.length > 1) ? 'md' : 'lg';
      const bet = s.stake && !s.hands.length ? motion(`${key}:bet`, 'chip-in', 450) : null;
      const hands = s.hands.length ? s.hands.map((h, hi) => handHtml(h, chips, handKey(h), seat, hi, size)).join('')
        : s.stake ? `<div class="bj-hand waiting">${C.chip(s.stake, { cls: `big ${chips}${bet.cls}`, style: bet.style })}<span class="muted small">Bet placed</span></div>`
          : `<div class="bj-hand waiting"><span class="muted small">${d.phase === 'betting' ? 'No bet yet' : 'Sitting this one out'}</span></div>`;
      // A run of wins glows hot, a run of losses goes cold (blackjack.py's streaks).
      const run = s.streak >= 3 ? 'hot' : s.streak <= -3 ? 'cold' : '';
      const runTag = run ? `<span class="streak-tag ${run}" title="${run === 'hot' ? 'Wins' : 'Losses'} in a row">${run === 'hot' ? '🔥' : '🧊'} ${Math.abs(s.streak)}</span>` : '';
      return `<div class="bj-spot ${turn ? 'turn' : ''} ${run} ${s.bettor === meName ? 'me' : ''} ${C.style(s.bettor, 'seat')}" data-bettor="${esc(s.bettor)}">
        <div class="bj-hands">${hands}</div>
        ${sidesHtml(s, chips, key)}
        ${which === 'shared' ? `<div class="bj-spot-name">${C.who(s.bettor, { title: true })}${runTag}${turn ? `<span class="muted small">${s.bettor === meName ? 'Your turn' : 'Their turn'}</span>` : ''}</div>`
          : ''}
        ${turn ? C.timer(d.deadline, d.turn_s) : ''}</div>`;
    });
    if (which === 'shared') for (let i = d.seats.length; i < d.max_seats; i++) spots.push('<div class="bj-spot open"><span class="muted small">Open seat</span></div>');
    if (which === 'solo' && !spots.length) spots.push(`<div class="bj-spot open"><span class="muted small">${state.me ? 'Pick a stake and deal' : 'Sign in to play'}</span></div>`);
    return spots.join('');
  }
  function statusHtml(d) {
    const line = statusLine(d);
    return line && !error ? `<span${C.after()}>${line}</span>` : line;
  }
  const RESULT_WORD = { win: 'Win', lose: 'Lose', push: 'Push', blackjack: 'Blackjack', bust: 'Bust' };
  function statusLine(d) {
    if (error) return `<span class="casino-error">${esc(error)}</span>`;
    if (d.phase === 'playing' && d.turn) {
      const mine = d.turn.bettor === d.me?.name;
      const hands = (d.seats.find((s) => s.bettor === d.turn.bettor)?.hands || []).length;
      const label = hands > 1 ? ` (hand ${d.turn.hand + 1})` : '';
      return mine ? `<b>Your move${label}.</b>${which === 'shared' ? ` ${C.countdown(d.deadline)} left, then Onkey stands for you.` : ''}`
        : `${plainName(d.turn.bettor)} is playing${label}. ${C.countdown(d.deadline)}`;
    }
    if (d.phase === 'done') {
      const mine = d.seats.find((s) => s.bettor === d.me?.name && s.hands.length);
      const line = mine ? (() => {
        // Each hand's result (Win, Lose, Push, Blackjack, Bust; a split lists them all), then the round's net.
        const net = mine.hands.reduce((a, h) => a + h.payout - h.stake, 0) + Object.values(mine.side_results || {}).reduce((a, r) => a + r.payout - r.stake, 0);
        const words = mine.hands.map((h) => RESULT_WORD[h.result] || '').filter(Boolean).join(' · ');
        const amount = net > 0 ? `+${fmt.credits(net)}` : net < 0 ? `−${fmt.credits(-net)}` : 'stake back';
        return `<b class="${net > 0 ? 'up' : net < 0 ? 'down' : ''}">${words} ${amount}.</b>`;
      })() : 'Round over.';
      return which === 'shared' ? `${line} Next round in ${C.countdown(d.next_round_at)}` : line;
    }
    if (which === 'shared') {
      if (!d.seats.length) return 'Nobody is at the shared table yet.';
      const bets = d.seats.filter((s) => s.stake).length;
      return bets ? `Betting closes in ${C.countdown(d.deadline)}, or as soon as everyone seated has bet (${bets} of ${d.seats.length}).`
        : 'Place your bets. Betting closes 15 seconds after the first one.';
    }
    return state.me ? 'Pick a stake and deal.' : 'Sign in to play blackjack with your credits.';
  }
  function stakeKeys(d, disabled) {
    return `<div class="casino-stakes" role="group" aria-label="Stake">${d.stakes.map((s) => { const bought = d.me ? C.style(d.me.name, 'chips') : ''; return `<button type="button" class="stake-key ${C.denom(s)} len-${String(s).length}${bought ? ` ${bought}` : ' drawn'}" data-bj-stake="${s}" aria-pressed="${s === stake}" aria-label="Stake ${s}" ${disabled || (d.me && d.me.balance < s) ? 'disabled' : ''}>${bought ? s : C.chipFace(s)}</button>`; }).join('')}</div>`;
  }
  // A side bet's spot: tap to add a chip (stepping up through the chips, never past the main stake, then off), right-click
  // to clear.
  function sideSpot(d, kind, disabled) {
    if (!sidesOpen()) return '';
    const amount = sideFor()[kind];
    const chip = amount ? C.chip(amount, { cls: `mini ${d.me ? C.style(d.me.name, 'chips') : ''}` }) : '<span class="side-plus" aria-hidden="true">+</span>';
    return `<button type="button" class="side-spot ${amount ? 'on' : ''}" data-bj-side="${kind}" ${disabled ? 'disabled' : ''}
      aria-label="${kind === 'pairs' ? 'Perfect Pairs' : '21+3'} side bet: ${amount || 'none'}" title="${kind === 'pairs' ? 'Perfect Pairs: your first two cards make a pair' : '21+3: your two cards and Onkey’s upcard make a poker hand'}">
      <span class="side-name">${SIDE_LABEL[kind]}</span>${chip}</button>`;
  }
  // The Deal button: a card with gold lettering on a little fanned deck, which spreads when you hover and flicks a card
  // when you press it. The stake is the chip you've picked, so the button just says what it does.
  function dealBtn(label, disabled) {
    return `<button type="button" class="deal-btn" data-bj-bet ${disabled ? 'disabled' : ''} aria-label="${label === 'Deal' ? 'Deal' : label}">
      <span class="deal-fan" aria-hidden="true"><i></i><i></i><i></i></span>
      <span class="deal-face"><span class="deal-pip tl" aria-hidden="true">♠</span><b>${label.toUpperCase()}</b><span class="deal-pip br" aria-hidden="true">♥</span></span></button>`;
  }
  const sideTotal = () => sideFor().pairs + sideFor().plus3;
  function controlsHtml(d) {
    const me = d.me;
    if (!me) return '<button class="btn primary casino-cta" data-signin>Sign in to play</button>';
    const acts = me.actions || [];
    if (acts.length) {
      const hand = d.seats.find((s) => s.bettor === me.name)?.hands[d.turn.hand];
      const pick = hintShown && hintShown === decision(d) ? me.hint.move : '';
      const KEY = { hit: 'H', stand: 'S', double: 'D', split: 'P' };
      const btn = (a, label, sub = '') => `<button class="bj-act act-${a}${a === pick ? ' hinted' : ''}" data-bj-act="${a}" ${busy || !acts.includes(a) ? 'disabled' : ''} title="${a === pick ? 'Onkey’s pick' : `${label} (${KEY[a]})`}">
        <b>${label}</b>${sub ? `<small>${sub}</small>` : ''}</button>`;
      const mine = d.seats.find((s) => s.bettor === me.name);
      const bet = (mine?.hands || []).reduce((a, h) => a + h.stake, 0);
      const w = motion(`bj:${d.table}:${d.round}:wager:${bet}`, 'chip-in', 450, C.landing());
      const wager = `<div class="wager" title="Your bet on this round">${C.chip(bet, { cls: `big ${C.style(me.name, 'chips')}${w.cls}`, style: w.style })}<span class="wager-label">Your bet</span></div>`;
      // The four buttons are centred on the table; your bet hangs off their left and your streak off their right.
      return `<div class="casino-actions bj-actions"><div class="bj-act-group">${wager}${btn('hit', 'Hit')}${btn('stand', 'Stand')}${btn('double', 'Double', hand ? `+${fmt.credits(hand.stake)}` : '')}${btn('split', 'Split', hand ? `+${fmt.credits(hand.stake)}` : '')}${streakTag(d)}</div></div>`;
    }
    if (which === 'shared') {
      if (!me.seated) {
        const full = d.shared_seats.taken >= d.shared_seats.max;
        return `<button class="btn primary casino-cta" data-bj-sit ${full || busy ? 'disabled' : ''}>${full ? 'The table is full' : 'Sit down'}</button>`;
      }
      const canBet = d.phase === 'betting' && !me.bet;
      return `<div class="casino-deck">${stakeKeys(d, !canBet || busy)}${sideSpot(d, 'pairs', !canBet || busy)}
        ${dealBtn(me.bet ? 'Bet in' : 'Bet', !canBet || busy || me.balance < stake + sideTotal())}${sideSpot(d, 'plus3', !canBet || busy)}
        <button class="btn ghost" data-bj-leave ${busy || (d.phase === 'playing' && d.seats.some((s) => s.bettor === me.name && s.hands.some((h) => !h.done))) ? 'disabled' : ''}>Leave table</button></div>`;
    }
    const playing = d.phase === 'playing';
    return `<div class="casino-deck">${stakeKeys(d, playing || busy)}${sideSpot(d, 'pairs', playing || busy)}
      ${dealBtn('Deal', playing || busy || me.balance < stake + sideTotal())}${sideSpot(d, 'plus3', playing || busy)}${streakTag(d)}</div>`;
  }
  // At the solo table your run of wins or losses sits to the right of the buttons (at the shared one it's under each
  // player's name).
  function streakTag(d) {
    if (which !== 'solo' || !d.me) return '';
    const n = d.seats.find((s) => s.bettor === d.me.name)?.streak || 0;
    if (Math.abs(n) < 3) return '';
    const hot = n > 0;
    return `<span class="streak-tag big ${hot ? 'hot' : 'cold'}" title="${hot ? 'Wins' : 'Losses'} in a row">${hot ? '🔥' : '🧊'} ${Math.abs(n)}</span>`;
  }
  // After a round you won: tip Onkey (one of me.tip.amounts, up to what you won, once). He thanks you out loud.
  // The tip pill sits to Onkey's left, once the cards are down.
  const tipHtml = (d) => { const row = tipRow(d); return row ? `<div${C.after()}>${row}</div>` : ''; };
  function tipRow(d) {
    const tip = d.me?.tip;
    if (!tip || (!tip.open && !tip.tipped)) return '';
    if (tip.tipped) return `<div class="tip-row tipped">🍌 You tipped Onkey ${fmt.credits(tip.tipped)}.</div>`;
    return `<div class="tip-row"><span>Tip Onkey</span>${tip.amounts.map((a) => `<button type="button" class="tip-chip" data-bj-tip="${a}" ${a > tip.max || busy ? 'disabled' : ''} aria-label="Tip ${a}" title="${a > tip.max ? `More than you won` : `Tip ${a}`}">${C.chip(a, { cls: 'mini' })}</button>`).join('')}</div>`;
  }
  // Emotes: everyone seated at the shared table can send one; a banana goes at Onkey. None at the solo table.
  function emotesHtml(d) {
    if (!d.me || which !== 'shared' || !d.me.seated) return '';
    return (d.emotes || []).map((x) => `<button type="button" class="emote-btn" data-bj-emote="${x}" title="${x === '🍌' ? 'Throw a banana at Onkey' : 'Send to the table'}">${x}</button>`).join('');
  }
  function sideHtml(d) {
    const s = d.me?.season;
    const tiles = s ? `<div class="casino-tiles">
        <div class="tile"><div class="tile-label">Hands</div><div class="tile-value">${fmt.n0(s.hands)}</div></div>
        <div class="tile"><div class="tile-label">Net</div><div class="tile-value ${s.net > 0 ? 'up' : s.net < 0 ? 'down' : ''}">${fmt.signed(s.net, 0)}</div></div>
        <div class="tile"><div class="tile-label">Won</div><div class="tile-value">${fmt.n0(s.wins)}</div></div>
        <div class="tile"><div class="tile-label">Blackjacks</div><div class="tile-value">${fmt.n0(s.blackjacks)}</div></div></div>`
      : '<p class="muted"><a href="#" data-signin>Sign in</a> to play and see your season.</p>';
    const at = d.shared_seats.names;
    return `<section class="card"><h2>Your season</h2>${tiles}</section>
      <section class="card"><h2>The shared table</h2>${at.length ? `<ul class="casino-names">${at.map((n) => `<li>${C.who(n)}</li>`).join('')}</ul>` : '<p class="muted">Empty. Sit down and Onkey deals for whoever joins.</p>'}
        <p class="muted small">${d.shared_seats.taken} of ${d.shared_seats.max} seats taken.</p></section>
      <section class="card"><h2>House rules</h2><ul class="casino-rules">${d.rules.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>
        ${d.side_bets.open ? `<h3 class="side-rules-h">Side bets</h3>
        <dl class="side-rules">${[['Perfect Pairs', 'pairs', ['perfect', 'coloured', 'mixed']], ['21+3', 'plus3', ['suited_trips', 'straight_flush', 'trips', 'straight', 'flush']]]
          .map(([title, k, keys]) => `<dt>${title} <span class="muted small">(house keeps ${(d.side_bets[k].edge * 100).toFixed(1)}%)</span></dt>${keys.map((x) => `<dd><span>${esc(d.side_bets.names[x])}</span><b>${d.side_bets[k].pays[x]} to 1</b></dd>`).join('')}`).join('')}</dl>
        <p class="muted small">Perfect Pairs is your first two cards; 21+3 is those two and Onkey's upcard, read as a poker hand. Up to your main bet each, decided at the deal.</p>` : ''}
        <details class="how"><summary>The house keeps about ${(d.edge * 100).toFixed(1)}% over time.</summary><p>That edge is the house's cut. Results count in the Casino column in Standings, separate from match-betting profit, and the casino never earns or costs bananas. A hand still open when the server restarts or the season ends is refunded.</p></details></section>`;
  }

  // ---- moves -----------------------------------------------------------------------------
  async function post(path, body) {
    if (busy) return;
    busy = true; error = '';
    refresh();
    try {
      apply(await api(path, { method: 'POST', body: JSON.stringify({ table: which, ...body }) }));
      setTimeout(loadMe, C.landing()); // the credits chip changes when the cards are down, not before
    } catch (e) {
      error = e.message;
      try { apply(await api(url())); } catch (_) { /* keep the old state */ }
    } finally {
      busy = false;
      if ($('#bj-root')) refresh();
    }
  }
  function bind(viewEl) {
    const root = viewEl.querySelector('#bj-root');
    if (!root) return;
    C.bindSpeaker(root);
    root.addEventListener('click', (e) => {
      const t = e.target.closest('button');
      if (!t || t.disabled) return;
      if (t.dataset.bjTable && t.dataset.bjTable !== which) {
        which = t.dataset.bjTable; error = ''; C.dealReset();
        try { localStorage.setItem('fs.bjTable', which); } catch (_) { /* unavailable */ }
        loop?.stop(); loop = null;
        draw();
      } else if (t.dataset.bjStake) {
        stake = +t.dataset.bjStake;
        try { localStorage.setItem('fs.bjStake', String(stake)); } catch (_) { /* unavailable */ }
        refresh();
      } else if (t.dataset.bjSide) {
        const k = t.dataset.bjSide, steps = [0, ...data[which].stakes.filter((s) => s <= stake)];
        side[k] = steps[(steps.indexOf(sideFor()[k]) + 1) % steps.length];
        saveSide(); C.sound('chips'); refresh();
      } else if (t.dataset.bjTip) {
        C.sound('chips');
        post('/api/blackjack/tip', { amount: +t.dataset.bjTip });
      } else if (t.dataset.bjEmote) {
        post('/api/blackjack/emote', { emote: t.dataset.bjEmote });
      } else if (t.hasAttribute('data-bj-bet')) {
        C.sound('chips');
        post('/api/blackjack/bet', { stake, request_id: C.ref(), side: sideFor() });
      } else if (t.dataset.bjAct) {
        post('/api/blackjack/action', { action: t.dataset.bjAct, step: data[which].me.step });
      } else if (t.hasAttribute('data-bj-sit')) {
        post('/api/blackjack/sit', {});
      } else if (t.hasAttribute('data-bj-leave')) {
        post('/api/blackjack/leave', {});
      }
    });
    root.addEventListener('contextmenu', (e) => { // right-click a side spot to take its chip off
      const t = e.target.closest('[data-bj-side]');
      if (!t || t.disabled) return;
      e.preventDefault();
      side[t.dataset.bjSide] = 0; saveSide(); refresh();
    });
    // Keyboard: H hit, S stand, D double, P split, Enter deals.
    if (!bind.keys) {
      bind.keys = true;
      document.addEventListener('keydown', (e) => {
        if (state.view !== 'blackjack' || e.target.closest('input, textarea, select, button, a') || e.metaKey || e.ctrlKey || e.altKey) return;
        const key = { h: 'hit', s: 'stand', d: 'double', p: 'split' }[e.key.toLowerCase()];
        const b = key ? $(`[data-bj-act="${key}"]`) : e.key === 'Enter' ? $('[data-bj-bet]') : null;
        if (b && !b.disabled) { e.preventDefault(); b.click(); }
      });
    }
    if (!bind.greeted) { bind.greeted = true; setTimeout(() => { if (!C.gregHere()) C.say($('#bj-onkey'), 'greet'); }, 500); }
    startLoop();
  }
  return { init, load, view, bind };
})();
