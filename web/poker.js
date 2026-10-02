/* Texas Hold'em at Onkey's table: one shared cash table. Sit down with a buy-in, ready up, and the game starts once
   everyone seated is ready. Anyone seated can change the rules in the lobby (which un-readies everybody). The server
   deals, runs the betting and settles the pots; this page long-polls it and draws the felt. */
window.FivePoker = (() => {
  'use strict';
  let state, $, api, draw, esc, fmt, loadMe, confetti, plainName;
  const C = window.FiveCasino;
  let data = null, seen = null, busy = false, error = '', loop = null, owner;
  let raiseTo = null, raiseKey = '', buyin = null, topup = null, draft = null;
  // Seat spots round the oval (x%, y%), clockwise from the bottom; your own seat is turned to the bottom. Onkey sits
  // at the head of the table, top centre.
  const SPOTS = [[37, 90], [13, 79], [2, 46], [14, 12], [86, 12], [98, 46], [87, 79], [63, 90]];
  const STREET = { preflop: 'Before the flop', flop: 'Flop', turn: 'Turn', river: 'River', done: 'Showdown' };
  function init(ctx) { ({ state, $, api, draw, esc, fmt, loadMe, confetti, plainName } = ctx); }

  async function load() {
    const name = state.me?.name || null;
    if (owner !== name) { owner = name; data = null; seen = null; draft = null; buyin = null; C.dealReset(); }
    apply(await api('/api/poker'));
  }
  function apply(d) {
    if (data && d.version < data.version) return; // an older answer arriving late
    const before = data;
    data = d;
    C.syncClock(d.server_time);
    C.absorb(d.looks);
    const fresh = (d.log || []).filter((e) => seen != null && e.id > seen);
    seen = d.log?.length ? d.log[d.log.length - 1].id : seen ?? 0;
    if (state.view !== 'poker' || !$('#pk-root')) return;
    refresh();
    const wait = C.landing();
    if (wait > 0) setTimeout(() => { if (state.view === 'poker' && $('#pk-root')) refresh(); }, wait + 30); // winners show once the cards are down
    if (fresh.some((e) => ['call', 'bet', 'raise', 'allin'].includes(e.kind))) C.sound('chips');
    // It just became your turn: a nudge.
    const myTurn = d.me?.legal && !(before?.me?.legal);
    if (myTurn && document.hidden) document.title = '♠ Your turn · 5-Stack Tracker';
    if (fresh.length) setTimeout(() => react(fresh), wait);
  }
  // What happened, once the cards are down: Onkey's line, Table win bursts, your win.
  function react(fresh) {
    if (state.view !== 'poker' || !$('#pk-root')) return;
    C.react($('#pk-onkey'), fresh, kindOf, (e) => ({ name: e.bettor || 'Onkey', amount: fmt.credits(e.amount || 0) }));
    fresh.filter((e) => e.kind === 'win' && e.bettor).forEach((e) => C.seatBurst($('#pk-seats'), e.bettor)); // bought Table wins
    const won = fresh.find((e) => e.kind === 'win' && e.bettor === state.me?.name);
    if (won) {
      C.sound('win');
      if (won.net >= 200) confetti($('#me-credits'), won.net >= 500);
    }
  }
  const kindOf = (e) => (e.kind === 'win' ? (e.split ? 'chop' : 'pwin') : e.kind === 'bust' ? 'bust_out' : e.kind);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && document.title.startsWith('♠')) document.title = '5-Stack Tracker'; });
  function startLoop() {
    if (loop && loop.running) return;
    loop = C.live({
      alive: () => state.view === 'poker',
      version: () => data?.version ?? -1,
      fetchNext: (v) => api(`/api/poker?since=${v}`),
      apply,
    });
  }

  // ---- drawing ----------------------------------------------------------------------------
  function view() {
    if (!data || (data.me?.name || null) !== (state.me?.name || null)) {
      load().then(() => { if (state.view === 'poker') draw(); }).catch((e) => {
        const el = $('#pk-loading'); if (el) el.textContent = `Could not load the table: ${e.message}`;
      });
      return '<section class="card" id="pk-loading">Onkey is shuffling…</section>';
    }
    const felt = C.deal(() => ({ center: centerHtml(), seats: seatsHtml(), status: statusHtml() }));
    return `<div id="pk-root" class="casino-page">
      <div class="casino-bar"><div id="pk-head" class="pk-head">${headHtml()}</div>${C.speaker()}</div>
      <div class="casino-layout">
        <div class="pk-main">
          <section class="felt pk-felt" aria-label="Poker table">
            <div class="pk-rail" aria-hidden="true"></div>
            ${C.dealer('pk-onkey', { cls: 'pk-onkey' })}
            <div class="pk-center" id="pk-center">${felt.center}</div>
            <div class="pk-seats" id="pk-seats">${felt.seats}</div>
          </section>
          <div class="felt-status pk-status" id="pk-status" role="status" aria-live="polite">${felt.status}</div>
          <div class="felt-controls pk-controls" id="pk-controls"><div class="casino-controls">${controlsHtml()}</div></div>
        </div>
        <aside class="casino-side" id="pk-side">${sideHtml()}</aside>
      </div>
    </div>`;
  }
  function refresh() {
    C.patch($('#pk-head'), headHtml());
    const felt = C.deal(() => ({ center: centerHtml(), seats: seatsHtml(), status: statusHtml() }));
    C.patch($('#pk-center'), felt.center);
    C.patch($('#pk-seats'), felt.seats);
    C.patch($('#pk-status'), felt.status);
    C.patch($('#pk-controls'), `<div${C.after('casino-controls')}>${controlsHtml()}</div>`);
    C.patch($('#pk-side'), sideHtml());
  }
  const st = () => data.settings;
  const limitName = (s = st()) => data.choices.limits[s.limit];
  function headHtml() {
    const seated = data.seats.filter(Boolean);
    const s = st();
    const rules = `${limitName()} · blinds ${s.small_blind}/${s.big_blind} · buy-in ${s.min_buyin}–${s.max_buyin}`;
    const where = data.phase === 'lobby' ? `<span class="pk-phase lobby">Lobby</span>`
      : `<span class="pk-phase live">Playing</span>${data.hand ? `<span class="muted">Hand #${data.hand.no}</span>` : ''}`;
    return `${where}<span class="pk-rules">${esc(rules)}</span><span class="muted small">${seated.length} of ${data.max_seats} seats</span>`;
  }
  function centerHtml() {
    const h = data.hand;
    const board = h ? h.board : [];
    const slots = [0, 1, 2, 3, 4].map((i) => C.card(board[i] || '', { size: 'xl', key: h ? `pk:${h.no}:board:${i}` : undefined, seq: 1000 + i })).join('');
    let line = '';
    if (h && h.street === 'done' && data.last) {
      line = data.last.pots.map((p) => `<span>${p.winners.map(esc).join(' and ')} ${p.winners.length > 1 ? 'split' : 'wins'} ${fmt.credits(p.amount)}${p.hand ? ` with ${esc(p.hand.toLowerCase())}` : ''}</span>`).join('') +
        (data.last.rake ? `<span class="muted small">House cut ${fmt.credits(data.last.rake)}</span>` : '');
    } else if (!h) {
      line = data.phase === 'lobby' ? '<span class="muted">Waiting for everyone to ready up</span>' : '';
    }
    return `<div class="pk-pot">${h ? `Pot <b>${fmt.credits(h.street === 'done' ? data.last?.pot : h.pot)}</b>` : '&nbsp;'}</div>
      <div class="pk-board">${slots}</div>
      <div${C.after('pk-result')}>${line}</div>`;
  }
  function seatsHtml() {
    const mine = data.me?.seat;
    const turnBy = (i) => (mine == null ? i : (i - mine + 8) % 8);
    const h = data.hand;
    const done = h && h.street === 'done';
    const best = new Map((done && data.last?.players || []).filter((p) => p.hand).map((p) => [p.seat, p]));
    const landed = C.landing() <= 0;
    const winners = new Set((done && landed && data.last?.players || []).filter((p) => p.won > 0).map((p) => p.seat));
    const button = data.seats.findIndex((x) => x && x.button);
    const dealSeq = (seat, j) => (data.seats[seat]?.cards && done ? 500 + seat * 2 + j : j * 10 + ((seat - button - 1 + 16) % 8));
    return data.seats.map((s, i) => {
      const [x, y] = SPOTS[turnBy(i)];
      if (!s) {
        return `<div class="pk-seat empty" style="left:${x}%;top:${y}%"><span class="muted small">Open seat</span></div>`;
      }
      const isMe = s.seat === mine;
      const back = C.style(s.bettor, 'card_back');  // the card backs they bought, for everyone at the table
      const key = h ? `pk:${h.no}:s${i}` : undefined;
      const cardsHtml = s.cards ? C.cards(s.cards, { size: isMe ? 'lg' : 'md', cls: best.has(i) ? 'shown' : '', key, seq: (j) => dealSeq(i, j) })
        : s.hidden ? [0, 1].map((j) => C.card(null, { size: 'md', cls: back, key: key && `${key}:${j}`, seq: dealSeq(i, j) })).join('') : '';
      const badges = `${s.button ? '<span class="pk-btn" title="Dealer button">D</span>' : ''}${s.sb ? '<span class="pk-blind">SB</span>' : ''}${s.bb ? '<span class="pk-blind">BB</span>' : ''}`;
      const sub = data.phase === 'lobby' || !s.dealt
        ? (s.ready ? '<span class="pk-ready on">Ready</span>' : '<span class="pk-ready">Not ready</span>')
        : s.label ? `<span class="pk-label ${s.folded ? 'folded' : ''}">${esc(s.label)}</span>` : '';
      const handName = best.get(i)?.hand ? `<span${C.after('pk-hand-name')}>${esc(best.get(i).hand)}</span>` : '';
      // The chips in front of a seat sit part-way to the middle.
      const bx = x + (50 - x) * 0.42, by = y + (50 - y) * 0.42;
      const bet = s.bet ? `<div class="pk-bet" style="left:${bx}%;top:${by}%"><span class="chip-dot ${C.denom(s.bet)} ${C.style(s.bettor, 'chips')}" aria-hidden="true"></span>${fmt.credits(s.bet)}</div>` : '';
      return `${bet}<div class="pk-seat ${isMe ? 'me' : ''} ${s.to_act ? 'turn' : ''} ${s.folded ? 'folded' : ''} ${winners.has(i) ? 'winner' : ''} ${s.dealt ? '' : 'out'} ${C.style(s.bettor, 'seat')}" data-bettor="${esc(s.bettor)}" style="left:${x}%;top:${y}%">
        <div class="pk-cards">${cardsHtml}</div>
        <div class="pk-plate"><div class="pk-name">${C.who(s.bettor)}${badges}</div>${C.title(s.bettor) ? `<div class="pk-title">${C.title(s.bettor)}</div>` : ''}
          <div class="pk-stack">${s.allin ? '<b class="pk-allin">All in</b>' : fmt.credits(s.stack)}</div>${sub}${handName}
          ${s.to_act && h ? C.timer(h.deadline, st().turn_seconds) : ''}</div></div>`;
    }).join('');
  }
  function statusHtml() {
    if (error) return `<span class="casino-error">${esc(error)}</span>`;
    const h = data.hand, me = data.me;
    if (h && h.street !== 'done') {
      const who = data.seats[h.to_act];
      const mine = me && me.seat === h.to_act;
      return `<span class="muted">${STREET[h.street]}</span> · ${mine ? `<b>Your move.</b> ${C.countdown(h.deadline)}` : who ? `${plainName(who.bettor)} to act · ${C.countdown(h.deadline)}` : ''}`;
    }
    if (data.next_hand_at) return `Next hand in ${C.countdown(data.next_hand_at)}${data.phase === 'playing' ? ' · un-ready to sit out' : ''}`;
    const seated = data.seats.filter(Boolean), ready = seated.filter((s) => s.ready).length;
    if (data.phase === 'lobby') {
      if (seated.length < 2) return 'Waiting for players. The game starts with at least two at the table, everyone ready.';
      return `${ready} of ${seated.length} ready. Onkey deals as soon as everyone seated is ready.`;
    }
    return 'Waiting for the next hand.';
  }
  function controlsHtml() {
    const me = data.me;
    if (!me) return '<button class="btn primary casino-cta" data-signin>Sign in to play</button>';
    const legal = me.legal, h = data.hand;
    if (!legal || !h) {
      if (me.seat == null) return '<span class="muted">Take a seat to play (Your seat, on the right).</span>';
      if (me.in_hand) return '<span class="muted">Waiting for your turn.</span>';
      return `<button class="btn ${me.ready ? 'ghost' : 'primary'} casino-cta" data-pk-ready="${me.ready ? '0' : '1'}" ${busy ? 'disabled' : ''}>${me.ready ? 'Not ready' : 'Ready up'}</button>`;
    }
    const key = `${h.no}:${h.step}`;
    if (key !== raiseKey) { raiseKey = key; raiseTo = legal.raise ? legal.raise.min : null; }
    const r = legal.raise;
    const callBtn = legal.check
      ? `<button class="btn casino-act act-check" data-pk-act="check" ${busy ? 'disabled' : ''}>Check</button>`
      : `<button class="btn casino-act act-call" data-pk-act="call" ${busy ? 'disabled' : ''}>${legal.call >= me.stack ? 'Call all in' : 'Call'}<small>${fmt.credits(legal.call)}</small></button>`;
    let raise = '';
    if (r) {
      const fixed = r.min === r.max;
      const pot = h.pot, owe = legal.call;
      const presets = fixed ? '' : [['Min', r.min], ['½ pot', h.current + Math.round((pot + owe) / 2)], ['Pot', h.current + pot + owe], ['All in', r.max]]
        .map(([label, v]) => [label, Math.max(r.min, Math.min(r.max, v))])
        .filter(([label, v], i, all) => label === 'Min' || label === 'All in' || (v > r.min && v < r.max && all.findIndex((x) => x[1] === v) === i))
        .map(([label, v]) => `<button type="button" class="btn small ghost" data-pk-preset="${v}" ${busy ? 'disabled' : ''}>${label}</button>`).join('');
      const amount = Math.max(r.min, Math.min(r.max, raiseTo ?? r.min));
      raise = `<div class="pk-raise">
        ${fixed ? '' : `<input type="range" id="pk-raise-range" min="${r.min}" max="${r.max}" step="1" value="${amount}" aria-label="${legal.verb} to"><input type="number" id="pk-raise-num" min="${r.min}" max="${r.max}" step="1" value="${amount}" aria-label="${legal.verb} to amount"><div class="pk-presets">${presets}</div>`}
        <button class="btn casino-act act-raise" data-pk-act="raise" ${busy ? 'disabled' : ''}>${amount >= r.max && r.max === me.stack + (data.seats[me.seat]?.bet || 0) ? 'All in' : legal.verb === 'Bet' ? 'Bet' : 'Raise to'}<small>${fmt.credits(amount)}</small></button></div>`;
    }
    return `<div class="casino-actions pk-actions"><button class="btn casino-act act-fold" data-pk-act="fold" ${busy ? 'disabled' : ''}>Fold</button>${callBtn}${raise}</div>`;
  }
  function seatCard() {
    const me = data.me, s = st();
    if (!me) return '<section class="card"><h2>Your seat</h2><p class="muted"><a href="#" data-signin>Sign in</a> to sit down with your credits.</p></section>';
    if (me.seat == null) {
      const full = !data.seats.includes(null);
      const most = Math.min(s.max_buyin, Math.floor(me.balance));
      if (buyin == null || buyin < s.min_buyin || buyin > s.max_buyin) buyin = Math.max(s.min_buyin, Math.min(most, Math.round(s.max_buyin / 2)));
      const short = me.balance < s.min_buyin;
      return `<section class="card"><h2>Your seat</h2>
        <label class="pk-field">Buy-in <input type="number" id="pk-buyin" min="${s.min_buyin}" max="${s.max_buyin}" step="1" value="${buyin}"></label>
        <p class="muted small">${s.min_buyin} to ${s.max_buyin} credits, from your balance of ${fmt.credits(me.balance)}. Whatever's in front of you comes back when you leave.</p>
        <button class="btn primary" data-pk-sit ${full || short || busy ? 'disabled' : ''}>${full ? 'The table is full' : short ? 'Not enough credits' : `Sit down with ${fmt.credits(buyin)}`}</button></section>`;
    }
    const room = me.topup_room;
    if (topup == null || topup < 1 || topup > room) topup = Math.min(room, s.min_buyin);
    const canTop = s.rebuys && !me.in_hand && room > 0;
    return `<section class="card"><h2>Your seat</h2>
      <div class="casino-tiles two"><div class="tile"><div class="tile-label">Chips</div><div class="tile-value">${fmt.credits(me.stack)}</div></div>
        <div class="tile"><div class="tile-label">Status</div><div class="tile-value small">${me.in_hand ? 'In the hand' : me.ready ? 'Ready' : 'Sitting out'}</div></div></div>
      <div class="pk-seat-actions">
        <button class="btn ${me.ready ? 'ghost' : 'primary'}" data-pk-ready="${me.ready ? '0' : '1'}" ${busy || (!me.ready && me.stack <= 0) ? 'disabled' : ''}>${me.ready ? 'Not ready' : 'Ready up'}</button>
        <button class="btn ghost" data-pk-leave ${busy ? 'disabled' : ''}>${me.in_hand ? 'Fold and leave' : 'Leave table'}</button>
      </div>
      ${s.rebuys ? `<div class="pk-topup"><label class="pk-field">Top up <input type="number" id="pk-topup" min="1" max="${room}" step="1" value="${topup}" ${canTop ? '' : 'disabled'}></label>
        <button class="btn small" data-pk-topup ${!canTop || busy || topup < 1 ? 'disabled' : ''}>Add</button></div>
        <p class="muted small">${me.in_hand ? 'Top up between hands.' : room > 0 ? `Up to ${fmt.credits(room)} more (the table's maximum is ${s.max_buyin}).` : 'You have the table\'s maximum.'}</p>` : '<p class="muted small">Top-ups are off at this table.</p>'}
    </section>`;
  }
  function rulesCard() {
    const s = st(), ch = data.choices, me = data.me;
    const canEdit = me && me.seat != null && data.phase === 'lobby';
    const d = draft || s;
    if (!canEdit) {
      draft = null;
      return `<section class="card"><h2>Table rules</h2><dl class="pk-rules-list">
        <dt>Game</dt><dd>Texas Hold'em, ${esc(limitName().toLowerCase())}</dd>
        <dt>Blinds</dt><dd>${s.small_blind} / ${s.big_blind}</dd><dt>Buy-in</dt><dd>${s.min_buyin} to ${s.max_buyin}</dd>
        <dt>Turn timer</dt><dd>${s.turn_seconds} seconds</dd><dt>Top-ups</dt><dd>${s.rebuys ? 'Between hands' : 'Off'}</dd></dl>
        <p class="muted small">${data.phase === 'lobby' ? 'Sit down to change the rules.' : 'The rules can change once the game stops: everyone un-readies, and it stops after the hand.'}</p>
        ${houseNote()}</section>`;
    }
    const opt = (v, label, cur) => `<option value="${v}" ${String(v) === String(cur) ? 'selected' : ''}>${label}</option>`;
    const changed = draft && JSON.stringify(draft) !== JSON.stringify(s);
    return `<section class="card"><h2>Table rules</h2>
      <div class="pk-form">
        <label class="pk-field">Betting <select id="pk-set-limit">${Object.entries(ch.limits).map(([k, v]) => opt(k, v, d.limit)).join('')}</select></label>
        <label class="pk-field">Blinds <select id="pk-set-blinds">${ch.blinds.map(([a, b]) => opt(`${a}/${b}`, `${a} / ${b}`, `${d.small_blind}/${d.big_blind}`)).join('')}</select></label>
        <label class="pk-field">Min buy-in <input type="number" id="pk-set-min" min="${ch.buyin_min}" max="${ch.buyin_max}" step="10" value="${d.min_buyin}"></label>
        <label class="pk-field">Max buy-in <input type="number" id="pk-set-max" min="${ch.buyin_min}" max="${ch.buyin_max}" step="10" value="${d.max_buyin}"></label>
        <label class="pk-field">Turn timer <select id="pk-set-turn">${ch.turns.map((t) => opt(t, `${t} seconds`, d.turn_seconds)).join('')}</select></label>
        <label class="pk-check"><input type="checkbox" id="pk-set-rebuys" ${d.rebuys ? 'checked' : ''}> Top-ups between hands</label>
      </div>
      <div class="pk-form-foot"><button class="btn small primary" data-pk-save ${!changed || busy ? 'disabled' : ''}>Change the rules</button>
        ${changed ? '<button class="btn small ghost" data-pk-cancel>Cancel</button>' : ''}</div>
      <p class="muted small">Anyone seated can change these between games. A change un-readies everyone, so all agree before Onkey deals. The minimum buy-in is at least ${ch.min_buyin_blinds} big blinds; the maximum is ${ch.buyin_max}.</p>
      ${houseNote()}</section>`;
  }
  const houseNote = () => `<details class="how"><summary>The house takes ${Math.round(data.rake.rate * 100)}% of each pot, at most ${data.rake.cap}.</summary><p>Rounded down, and nothing from a hand that ends before the flop. Everything else goes to the players: poker results count in the Casino column in Standings, never in match-betting profit, and the casino never earns or costs bananas. If the server restarts mid-hand, that hand is cancelled and everyone is cashed out at their stack from before it.</p></details>`;
  function lastCard() {
    const l = data.last;
    if (!l) return '';
    const rows = l.players.slice().sort((a, b) => b.net - a.net).map((p) => `<tr><th scope="row">${C.who(p.bettor)}</th>
      <td>${p.cards ? `<span class="pk-mini">${C.cards(p.cards, { size: 'xs' })}</span>` : '<span class="muted small">–</span>'}</td>
      <td class="small">${p.hand ? esc(p.hand) : ''}</td><td class="num ${p.net > 0 ? 'up' : p.net < 0 ? 'down' : ''}">${fmt.signed(p.net, 0)}</td></tr>`).join('');
    return `<section class="card"><h2>Hand #${l.no}</h2>
      <div class="pk-last-board">${C.cards(l.board, { size: 'xs' }) || '<span class="muted small">No flop</span>'}</div>
      <div class="table-wrap"><table class="compact"><tbody>${rows}</tbody></table></div>
      <p class="muted small">Pot ${fmt.credits(l.pot)}${l.rake ? `, house cut ${fmt.credits(l.rake)}` : ', no house cut'}.</p></section>`;
  }
  function logLine(e) {
    const n = e.bettor ? plainName(e.bettor) : '';
    const c = (v) => fmt.credits(v);
    switch (e.kind) {
      case 'sit': return `${n} sat down with ${c(e.amount)}`;
      case 'leave': return `${n} left with ${c(e.amount)}`;
      case 'ready': return `${n} is ready`;
      case 'unready': return `${n} is sitting out`;
      case 'settings': return `${n} changed the rules`;
      case 'start': return 'Everyone was ready: the game started';
      case 'stop': return 'Fewer than two ready: back to the lobby';
      case 'hand': return `<b>Hand #${e.no}</b>, ${e.players} players`;
      case 'fold': return `${n} folded`;
      case 'check': return `${n} checked`;
      case 'call': return `${n} called ${c(e.amount)}`;
      case 'bet': return `${n} bet ${c(e.amount)}`;
      case 'raise': return `${n} raised to ${c(e.amount)}`;
      case 'allin': return `${n} went all in (${c(e.amount)})`;
      case 'flop': case 'turn': case 'river': return `${e.kind[0].toUpperCase() + e.kind.slice(1)}: ${C.cards(e.board.slice(e.kind === 'flop' ? 0 : -1), { size: 'xs' })}`;
      case 'win': return `${n} won ${c(e.amount)}${e.hand ? ` with ${esc(e.hand.toLowerCase())}` : ''}`;
      case 'timeout': return `${n} ran out of time`;
      case 'sit_out': return `${n} timed out twice and is sitting out`;
      case 'bust': return `${n} is out of chips`;
      case 'topup': return `${n} added ${c(e.amount)}`;
      case 'closed': return esc(e.note || 'The table closed');
      default: return '';
    }
  }
  function logCard() {
    const lines = (data.log || []).slice().reverse().map((e) => [e, logLine(e)]).filter(([, l]) => l).slice(0, 14);
    return `<section class="card"><h2>Table talk</h2>${lines.length ? `<ol class="pk-log">${lines.map(([e, l]) => `<li>${l}</li>`).join('')}</ol>` : '<p class="muted">Quiet so far.</p>'}</section>`;
  }
  function seasonCard() {
    const s = data.me?.season;
    if (!s) return '';
    return `<section class="card"><h2>Your season</h2><div class="casino-tiles">
      <div class="tile"><div class="tile-label">Hands</div><div class="tile-value">${fmt.n0(s.hands)}</div></div>
      <div class="tile"><div class="tile-label">Net</div><div class="tile-value ${s.net > 0 ? 'up' : s.net < 0 ? 'down' : ''}">${fmt.signed(s.net, 0)}</div></div>
      <div class="tile"><div class="tile-label">Won</div><div class="tile-value">${fmt.n0(s.won)}</div></div>
      <div class="tile"><div class="tile-label">Best hand</div><div class="tile-value">${s.best > 0 ? '+' + fmt.credits(s.best) : '–'}</div></div></div></section>`;
  }
  const sideHtml = () => seatCard() + rulesCard() + lastCard() + seasonCard() + logCard();

  // ---- moves ----------------------------------------------------------------------------------
  async function post(path, body) {
    if (busy) return;
    busy = true; error = '';
    refresh();
    try {
      apply(await api(path, { method: 'POST', body: JSON.stringify(body) }));
      setTimeout(loadMe, C.landing()); // the credits chip changes when the cards are down, not before
    } catch (e) {
      error = e.message;
      try { apply(await api('/api/poker')); } catch (_) { /* keep the old state */ }
    } finally {
      busy = false;
      if ($('#pk-root')) refresh();
    }
  }
  const act = (action, amount) => post('/api/poker/action', { action, amount, hand: data.hand?.no, step: data.hand?.step });
  function readDraft() {
    const v = (id) => $(`#${id}`);
    const [sb, bb] = v('pk-set-blinds').value.split('/').map(Number);
    draft = { ...st(), limit: v('pk-set-limit').value, small_blind: sb, big_blind: bb, min_buyin: +v('pk-set-min').value,
      max_buyin: +v('pk-set-max').value, turn_seconds: +v('pk-set-turn').value, rebuys: v('pk-set-rebuys').checked };
  }
  function setRaise(v, from) {
    const r = data.me?.legal?.raise;
    if (!r) return;
    raiseTo = Math.max(r.min, Math.min(r.max, Math.round(+v || r.min)));
    if (from !== 'range' && $('#pk-raise-range')) $('#pk-raise-range').value = raiseTo;
    if (from !== 'num' && $('#pk-raise-num')) $('#pk-raise-num').value = raiseTo;
    const label = $('[data-pk-act="raise"] small');
    if (label) label.textContent = fmt.credits(raiseTo);
  }
  function bind(viewEl) {
    const root = viewEl.querySelector('#pk-root');
    if (!root) return;
    C.bindSpeaker(root);
    root.addEventListener('click', (e) => {
      const t = e.target.closest('button');
      if (!t || t.disabled) return;
      if (t.dataset.pkAct) act(t.dataset.pkAct, t.dataset.pkAct === 'raise' ? (data.me.legal.raise.min === data.me.legal.raise.max ? data.me.legal.raise.min : raiseTo) : undefined);
      else if (t.dataset.pkPreset) setRaise(t.dataset.pkPreset);
      else if (t.dataset.pkReady) post('/api/poker/ready', { ready: t.dataset.pkReady === '1' });
      else if (t.hasAttribute('data-pk-sit')) post('/api/poker/sit', { buyin });
      else if (t.hasAttribute('data-pk-leave')) post('/api/poker/leave', {});
      else if (t.hasAttribute('data-pk-topup')) post('/api/poker/topup', { amount: topup });
      else if (t.hasAttribute('data-pk-save')) { const s = draft; draft = null; post('/api/poker/settings', { settings: s }); }
      else if (t.hasAttribute('data-pk-cancel')) { draft = null; refresh(); }
    });
    root.addEventListener('input', (e) => {
      const id = e.target.id;
      if (id === 'pk-raise-range') setRaise(e.target.value, 'range');
      else if (id === 'pk-raise-num') { raiseTo = +e.target.value; const lab = $('[data-pk-act="raise"] small'); if (lab) lab.textContent = fmt.credits(raiseTo); }
      else if (id === 'pk-buyin') { buyin = Math.round(+e.target.value); const b = $('[data-pk-sit]'); if (b && !b.disabled) b.textContent = `Sit down with ${fmt.credits(buyin)}`; }
      else if (id === 'pk-topup') { topup = Math.round(+e.target.value); }
      else if (id.startsWith('pk-set-')) { readDraft(); const save = $('[data-pk-save]'); if (save) save.disabled = JSON.stringify(draft) === JSON.stringify(st()); }
    });
    root.addEventListener('change', (e) => {
      if (e.target.id === 'pk-raise-num') setRaise(e.target.value);
      if (e.target.id?.startsWith('pk-set-')) { readDraft(); refresh(); }
    });
    // Keyboard: F fold, C check or call, R raise or bet.
    if (!bind.keys) {
      bind.keys = true;
      document.addEventListener('keydown', (e) => {
        if (state.view !== 'poker' || e.target.closest('input, textarea, select, button, a') || e.metaKey || e.ctrlKey || e.altKey) return;
        const k = e.key.toLowerCase();
        const b = k === 'f' ? $('[data-pk-act="fold"]') : k === 'c' ? $('[data-pk-act="check"], [data-pk-act="call"]') : k === 'r' ? $('[data-pk-act="raise"]') : null;
        if (b && !b.disabled) { e.preventDefault(); b.click(); }
      });
    }
    if (!bind.greeted) { bind.greeted = true; setTimeout(() => { if (!C.gregHere()) C.say($('#pk-onkey'), 'greet'); }, 500); }
    startLoop();
  }
  return { init, load, view, bind };
})();
