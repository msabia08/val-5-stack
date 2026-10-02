/* 5-Stack Tracker: Onkey in the top-left corner talks to you.
 *
 * He speaks like the dealer at the casino tables (casino.js): a bubble with his monkey noises ("Ook eek ook") over what
 * they mean, his face jabbering while he talks. Two kinds of lines:
 *  - idle chatter every IDLE_MIN..IDLE_MAX seconds while the tab is visible, some fixed and some built from what the
 *    page knows (your balance, rank, bananas, open bets, a waiting wheel spin, the time of day);
 *  - reactions, when another page calls FiveOnkey.note(kind, detail): a bet placed, bets settling (wins, losses and
 *    losing runs, from /api/bettor/me recent_settled), slot spins (losing runs, big wins, the Golden Onkey), wheel
 *    prizes, credits sent, a shop purchase, and opening some pages. Reactions chatter out loud (the casino's sound
 *    setting); idle lines are silent.
 * He keeps quiet at the blackjack and poker tables, where the dealer Onkey does the talking. Clicking his bubble hushes
 * him for HUSH_MIN minutes (sessionStorage `fs.onkeyHush`). Reduced motion keeps the bubble and drops the hop.
 * Plain JS, no dependencies; loaded before app.js, which calls init() and start().
 */
window.FiveOnkey = (() => {
  'use strict';

  let state = null, fmt = null, esc = (s) => String(s);
  const IDLE_MIN = 70, IDLE_MAX = 140; // seconds between idle lines
  const FIRST_IDLE = 18; // seconds before the first one
  const HUSH_MIN = 15;
  const BUBBLE_MS = 5500, BUBBLE_PER_WORD = 280; // how long a line stays up: a 10-word line about 8 seconds
  const OMINOUS_CHANCE = 0.12, OMINOUS_NIGHT = 0.35; // share of idle lines that are OMINOUS (more after midnight)
  const QUIET_VIEWS = new Set(['blackjack', 'poker']); // the dealer talks there
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const pick = (list) => list[Math.floor(Math.random() * list.length)];
  const fill = (line, vars) => line.replace(/\{(\w+)\}/g, (_, k) => (vars[k] != null ? vars[k] : ''));

  // ---- what he says ---------------------------------------------------------------------------------------------------
  // Fixed lines for when nothing is happening.
  const IDLE = [
    'Ook. Just checking you\'re still here.',
    'Onkey sees all. Onkey judges some.',
    'Did someone say bananas?',
    'Remember: the house always has more bananas than you.',
    'Hydrate. Then bet.',
    'Onkey once bet the under on himself. Never again.',
    'The odds don\'t care about your feelings.',
    'A parlay a day keeps the credits away.',
    'Greg sends his regards.',
    'Onkey and Greg go way back. Onkey still owes him a banana.',
    'Greg Mode is in the shop. Onkey can\'t stop you. Onkey wishes he could.',
    'Click enough times in Greg Mode and it rains Gregs. That\'s just science.',
    'Onkey is not a financial advisor. Onkey is a monkey.',
    'Who\'s carrying tonight? Onkey has theories.',
    'Fun fact: nobody has ever beaten Onkey at Onkey Says. Onkey checked.',
    'Check the forecasts. Or don\'t. Onkey can\'t stop you.',
    'Bottom fragger markets are Onkey\'s favourite. No reason.',
    'Onkey counts cards. Onkey also eats them.',
    'Ook ook. That\'s monkey for "place a bet".',
    'Every great streak starts with one questionable decision.',
    'The jackpot is getting heavy. Someone should win it.',
    'Onkey is watching the scoreboard. Intently.',
    'Is it "gg" yet? Onkey can\'t tell from up here.',
    'Somebody pinged the wrong site. Onkey stays.',
    'Tip of the day: the pistol round matters. Probably.',
    'Onkey tried to play Valorant once. Thumbs too long.',
    'Every bet is a learning experience. Some lessons are expensive.',
    'Onkey\'s lucky number is 13. Like rounds. Like bananas eaten today.',
    'Rumour has it the over is due. Onkey started the rumour.',
    'Somewhere, a parlay is one leg short. Onkey feels it.',
    'Onkey doesn\'t do eco rounds. Full buy, every time.',
    'The leaderboard is just vibes with numbers.',
    'If the squad wins, Onkey takes credit. If they lose, Onkey was asleep.',
    'Onkey has been practising his poker face. :|',
    'A banana a day keeps the tilt away.',
    'You miss 100% of the bets you don\'t place. You also keep the credits.',
    'Onkey is legally required to say: gamble responsibly. Ook.',
    'Somebody top-fragged on Ascent once and never shut up about it.',
    'The charts tab is very pretty. Onkey helped colour it in.',
    'Onkey read the forecasts. Onkey understood about half.',
    'Wheel, slots, blackjack, poker. Onkey has a type.',
    'Onkey\'s favourite agent is whoever bought him a banana.',
    'Clutch or kick. Onkey has opinions.',
    'Be nice to the dealer. The dealer is Onkey.',
    'Onkey once went 0-13 on Onkey Says. It was a bad day.',
    'Remember to stretch between games. Monkeys do.',
    'The over/under on Onkey\'s naps today is 4.5. Take the over.',
  ];
  // Now and then, instead of an idle line: something Onkey maybe shouldn't have said. Shown in a dark bubble.
  const OMINOUS = [
    'Onkey has seen how this season ends. Onkey won\'t say.',
    'The wheel remembers every spin. Every single one.',
    'Something is moving behind the slot machine. Don\'t look.',
    'Onkey counted the bananas. One is missing. Again.',
    'The house always wins. The house is older than you think.',
    'Some bets are never settled. They just... wait.',
    'Onkey hears the reels spinning at night. Nobody is there.',
    'The Golden Onkey isn\'t a symbol. It\'s a warning.',
    'Every credit you lose goes somewhere. Onkey knows where.',
    'Greg isn\'t sleeping. Greg is waiting.',
    'Greg has been in the background this whole time. Literally.',
    'Onkey switched off Greg Mode once. Greg was still there.',
    'The odds are watching you back.',
    'There used to be another member of the squad. We don\'t talk about them.',
    'Onkey was here before the site. Onkey will be here after.',
    'The dealer\'s hole card is always the same card. Think about it.',
    'Don\'t ask what\'s in the banana ledger. Just don\'t.',
    'When the jackpot is won, something else wakes up.',
    'Onkey has a list. You are on it. It\'s probably fine.',
    'Shhh. Listen. The parlays are whispering.',
  ];
  // Reactions: one line is picked at random; {name}-style blanks are filled from the event.
  const SAY = {
    greet_morning: ['Morning, {name}! Coffee first, parlays second.', 'Up early, {name}? Onkey respects that.',
      'Rise and shine, {name}. The bananas are fresh.'],
    greet_day: ['Ook! Welcome back, {name}.', 'Look who it is. Hi, {name}.', '{name}! Onkey saved you a banana.',
      'Afternoon, {name}. The odds missed you.'],
    greet_night: ['Evening, {name}. Game night?', 'Ook ook, {name}. The squad\'s online, Onkey can feel it.',
      'There you are, {name}. Lines are up, bananas are ripe.'],
    greet_late: ['It\'s {time}, {name}. Onkey respects the grind.', 'Still up, {name}? One more game. Onkey knows.',
      'The night shift, {name}? Onkey never sleeps either.'],
    bet_token: ['Token on {desc}? Onkey likes a monkey with a plan.', 'Using a token! Onkey approves of free stuff.'],
    bet: ['{stake} on {desc}? Bold.', 'Onkey has written down your bet. In crayon.', 'Locked in at {odds}. No take-backs after a minute.',
      '{desc}. Onkey likes it. Onkey likes everything.', '{desc} at {odds}. Onkey would have done the same. Probably.',
      'Bet received. Onkey will pretend he isn\'t watching.'],
    bet_big: ['{stake} credits?! Onkey needs to sit down.', '{stake} on one pick. Onkey is sweating for you.'],
    parlay: ['A {legs}-leg parlay. Onkey admires the optimism.', 'Parlays: where dreams go to... sometimes win!', '{legs} legs. That\'s more legs than Onkey has.'],
    bets: ['{n} bets in. The slip is empty and your heart is full.', '{n} picks placed. Onkey will be watching every one.'],
    won: ['Cha-ching! {desc} paid {net}.', 'You won {net}. Onkey is happy. The house is not.', 'Called it. +{net} on {desc}.',
      '{desc} came in. +{net}. Do a little dance.', 'Winner! Onkey always believed in {desc}. Always.'],
    won_long: ['A long shot came in! +{net}! Onkey is screaming!', 'At {odds}?! +{net}! Somebody hold Onkey\'s banana!'],
    lost: ['{desc} didn\'t hit. Onkey felt that.', 'Lost one. Shake it off.', '{desc}... Onkey isn\'t going to talk about it.',
      'Not this time. The next one\'s yours.', '{desc} let you down. Onkey will have a word with it.'],
    lose_run: ['That\'s {n} losing bets in a row. Maybe bet the other side?', '{n} straight losses. The comeback starts now. Probably.'],
    lose_run_big: ['{n} losses in a row. Onkey is holding your bananas for safekeeping.', '{n} straight. Onkey is hiding the parlay button.'],
    slots_lose: ['The reels hate you today. Onkey doesn\'t. Mostly.', 'So close. Not really. But so close.', 'Spin it again. Onkey dares you.',
      'The machine says thank you.', 'Nothing. The reels are being shy.'],
    slots_run: ['{n} spins without a win. The machine is just warming up.', '{n} dry spins. Onkey can hear the machine laughing.'],
    slots_win: ['A little win! {payout} back.', 'Ding ding! +{net}.', 'Three {symbol}s. Onkey approves.',
      'A hit! The machine coughs up {payout}.', '{symbol}s! Small, but Onkey will take it.'],
    slots_big: ['{mult}x! Onkey heard that from up here!', '{symbol}s across! +{net}!', 'Somebody call security, {name} is robbing the machine!'],
    slots_golden: ['THE GOLDEN ONKEY! That\'s Onkey\'s cousin!', 'GOLDEN ONKEY!! +{net}! Onkey is crying!'],
    slots_down: ['You\'re down {net} on slots this visit. Onkey suggests a snack break.'],
    wheel_jackpot: ['THE JACKPOT! {amount} credits! Onkey is fainting!', 'You took the whole jackpot! Onkey has to sit down.'],
    wheel_nothing: ['Onkey ate your prize. It was delicious.', 'Nom. Sorry. Onkey was hungry.'],
    wheel_big: ['{amount} credits from the wheel! Onkey spun it with love.', '{amount}! Big wheel energy.'],
    wheel_credits: ['{amount} free credits. Don\'t spend them all at once.', 'Free money! {amount} credits.'],
    wheel_bananas: ['{amount} bananas! Onkey would like a few.', 'Bananas! Onkey approves of this prize.'],
    wheel_token: ['A {label}. Use it wisely. Or don\'t.', '{label}! Tap it on a pick in your bet slip.'],
    wheel_item: ['Free swag! Onkey picked it himself.', 'A free cosmetic! Go put it on.'],
    wheel_again: ['Spin again! The wheel likes you.', 'Again! Onkey didn\'t see that coming.'],
    transfer: ['Sending {amount} to {to}? The generous monkey gets rewarded.', '{amount} to {to}. Onkey hopes they say thanks.'],
    buy: ['Nice {item}. Very you.', '{item}! Onkey approves of this purchase.', 'Ooh, {item}. Fancy.'],
    view_shop: ['Welcome to Onkey\'s Shop. Touch everything.', 'Browsing again? Onkey has bananas to take.'],
    view_arcade: ['The arcade! Onkey\'s high score is untouchable.', 'Insert banana to play.', 'Five bananas a go. Onkey takes exact change.'],
    view_slots: ['Pull the lever. Onkey dares you.', 'The machine is hungry. Feed it.', 'Somewhere in there is a Golden Onkey. Go find him.'],
    view_wheel: ['Round and round she goes.', 'The wheel is shiny today.', 'Onkey greased the wheel. For luck.'],
    view_bettors: ['The standings. Find yourself. Onkey will wait.', 'Who\'s on top? Onkey already knows.'],
    view_matches: ['Recaps! Relive the glory. Or the pain.', 'Onkey watched every round. Twice.'],
    view_players: ['The stats don\'t lie. Onkey sometimes does.', 'Somebody\'s carrying. Find out who.'],
    view_viz: ['Charts! Onkey loves a line that goes up.', 'Pretty colours. Onkey picked them himself.'],
    view_troop: ['The monkeys. Onkey knows every one of them.', 'Check out everyone\'s drip.'],
    view_forecasts: ['Forecasts: like weather, but for frags.'],
    view_odds: ['Place bets. Onkey\'s watching.', 'The odds are fresh. Onkey baked them himself.', 'Look for the flames. Hot picks run hot. Sometimes.'],
    hush: ['Fine. Onkey will be quiet for a bit.'],
  };
  // Idle lines built from what the page knows; each returns a line or nothing when it doesn't apply.
  const DYNAMIC = [
    () => (me()?.wheel_ready ? 'Your daily spin is ready. The wheel misses you.' : null),
    () => { const b = balance(); return b != null && b >= 3000 ? `${credits(b)} credits? Look at you, Moneybags.` : null; },
    () => { const b = balance(); return b != null && b < 200 ? `Only ${credits(b)} credits left. Onkey believes in you. Mostly.` : null; },
    () => { const r = rank(); return r && r.rank === 1 && r.of > 1 ? 'Top of the standings. Don\'t let it go to your head.' : null; },
    () => { const r = rank(); return r && r.rank === r.of && r.of > 2 ? 'Last place has a great view of everyone else.' : null; },
    () => { const r = rank(); return r && r.rank > 1 && r.rank < r.of ? `You're number ${r.rank} of ${r.of}. Onkey sees a climb coming.` : null; },
    () => { const n = me()?.bananas; return n >= 300 ? `${Math.floor(n)} bananas and no new hat? Onkey is concerned.` : null; },
    () => { const n = me()?.open_bets; return n >= 2 ? `You've got ${n} bets riding. Onkey is nervous for you.` : null; },
    () => { const d = new Date(); return [0, 5, 6].includes(d.getDay()) ? `It's ${d.toLocaleDateString(undefined, { weekday: 'long' })}. Perfect night for a 5-stack.` : null; },
    () => { const h = new Date().getHours(); return h >= 0 && h < 5 ? 'It\'s very late. Onkey won\'t tell anyone.' : null; },
    () => { const d = new Date().getDay(); return d === 1 ? 'Monday. Onkey needs a banana too.' : d === 5 ? 'Friday! Onkey smells a long night of games.' : null; },
    () => { const h = new Date().getHours(); return h >= 12 && h < 14 ? 'Lunch break bet? Onkey won\'t tell anyone.' : null; },
    () => (me()?.tokens?.boost > 0 ? 'You\'ve got a boost token. It does nothing in your pocket.' : null),
    () => (me()?.tokens?.insurance > 0 ? 'An insurance token is waiting. Go bet like nothing can go wrong.' : null),
    () => { const s = me()?.open_stake; return s >= 500 ? `${credits(s)} credits riding on the next game. Onkey's tail is twitching.` : null; },
    () => { const n = me()?.bananas; return n >= 5 && n < 40 ? `${Math.floor(n)} bananas. That's ${Math.floor(n / 5)} arcade games, or one very small hat.` : null; },
    () => (me()?.member && !me().member.active ? 'You\'re on the bench right now. Onkey keeps your seat warm.' : null),
    () => (me()?.member?.active ? `Onkey's watching your stats, ${me().member.nickname || me().name}. No pressure.` : null),
    () => {
      const w = (me()?.recent_wins || [])[0];
      return w && Date.now() / 1000 - w.settled_ts < 3 * 3600 ? `Still glowing from ${desc(w.description)}? Onkey is.` : null;
    },
    () => {
      const r = rank(), list = state?.bettors || [];
      if (!r || r.rank < 2) return null;
      const above = list[r.rank - 2], gap = above.balance - balance();
      return gap > 0 && gap <= 300 ? `Just ${credits(gap)} credits behind ${above.name}. Onkey smells an overtake.` : null;
    },
    () => {
      const r = rank(), list = state?.bettors || [];
      if (!r || r.rank !== 1 || list.length < 2) return null;
      return `${credits(balance() - list[1].balance)} credits clear at the top. Comfy?`;
    },
    () => {
      const r = rank(), lead = (state?.bettors || [])[0];
      return r && r.rank > 2 && lead ? `${lead.name} leads the standings with ${credits(lead.balance)}. Onkey says hunt them down.` : null;
    },
    () => { const roi = mine()?.roi; return roi != null && roi >= 0.15 ? `${Math.round(roi * 100)}% return on your bets this season. Onkey suspects insider bananas.` : null; },
    () => { const c = mine()?.casino; return c >= 200 ? `Up ${credits(c)} at the casino this season. The house is keeping an eye on you.` : null; },
    () => {
      const rec = state?.status?.record;
      if (!rec || rec.games < 5) return null;
      const pct = rec.wins / rec.games;
      return pct >= 0.55 ? `The squad is ${rec.wins}-${rec.losses}. Onkey is a fan.`
        : pct < 0.45 ? `${rec.wins}-${rec.losses}. The squad could use Onkey's banana-based coaching.`
          : `${rec.wins}-${rec.losses}. A coin-flip squad. Onkey loves a coin flip.`;
    },
  ];

  // ---- what he knows -------------------------------------------------------------------------------------------------
  const me = () => state && state.me;
  const balance = () => (me() ? me().balance : null);
  const credits = (v) => (fmt ? fmt.credits(v) : String(Math.round(v)));
  function rank() {
    const list = (state && state.bettors) || [], name = me()?.name;
    if (!name || !list.length) return null;
    const i = list.findIndex((b) => b.name.toLowerCase() === name.toLowerCase());
    return i < 0 ? null : { rank: i + 1, of: list.length };
  }
  const mine = () => { const n = me()?.name?.toLowerCase(); return n ? ((state && state.bettors) || []).find((b) => b.name.toLowerCase() === n) : null; };
  // Slots: losing spins in a row and this visit's net. He brings up a losing run or a losing visit rarely: a run at
  // every SLOT_RUN_EVERY dry spins, a visit the first time it's SLOT_DOWN_FIRST down and then every further
  // SLOT_DOWN_STEP, and either at most once per SLOT_NAG_MS. On top of that, any line about a spin that didn't win only
  // comes out SLOT_LOSS_TALK of the times it otherwise would (wins are always cheered).
  const SLOT_RUN_EVERY = 12, SLOT_DOWN_FIRST = 750, SLOT_DOWN_STEP = 1500, SLOT_NAG_MS = 15 * 60 * 1000, SLOT_LOSE_CHANCE = 0.2;
  const SLOT_LOSS_TALK = 0.6;
  const memory = { slotLosses: 0, slotNet: 0, slotDownMark: -SLOT_DOWN_FIRST, slotNagAt: 0, settledSeen: null };

  // ---- talking -------------------------------------------------------------------------------------------------------
  let el = null, hideTimer = null, idleTimer = null, lastSaid = '', lastAt = 0;
  const hushed = () => { try { return Number(sessionStorage.getItem('fs.onkeyHush') || 0) > Date.now(); } catch (e) { return false; } };

  function speak(text, { loud = false, excited = false, ominous = false } = {}) {
    if (!el || !text || hushed()) return;
    if (QUIET_VIEWS.has(state?.view)) return;
    if (text === lastSaid) return; // never twice in a row
    lastSaid = text; lastAt = Date.now();
    const words = text.split(/\s+/).length;
    const n = Math.max(2, Math.min(6, Math.round(words / 2)));
    const noises = Array.from({ length: n }, (_, i) => (excited ? (i % 3 === 2 ? 'ook' : 'eek') : (i % 3 === 1 ? 'eek' : 'ook')));
    el.querySelector('.ook').textContent = noises.map((x) => x[0].toUpperCase() + x.slice(1) + (excited ? '!' : '')).join(' ');
    el.querySelector('.say').textContent = text;
    const brand = el.closest('.brand');
    brand.classList.remove('onkey-talking', 'onkey-excited', 'onkey-ominous');
    void brand.offsetWidth; // restart the hop
    brand.classList.add('onkey-talking');
    if (excited) brand.classList.add('onkey-excited');
    if (ominous) brand.classList.add('onkey-ominous');
    if (loud && window.FiveCasino && window.FiveCasino.chatter) window.FiveCasino.chatter(noises);
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => brand.classList.remove('onkey-talking', 'onkey-excited', 'onkey-ominous'), BUBBLE_MS + words * BUBBLE_PER_WORD);
  }
  const react = (kind, vars = {}, opts = {}) => {
    const list = SAY[kind];
    if (list) speak(fill(pick(list), { name: me()?.name || 'friend', ...vars }), { loud: true, ...opts });
  };

  function idle() {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(idle, (IDLE_MIN + Math.random() * (IDLE_MAX - IDLE_MIN)) * 1000);
    if (document.hidden || Date.now() - lastAt < 30000) return;
    const dynamic = DYNAMIC.map((f) => { try { return f(); } catch (e) { return null; } }).filter(Boolean);
    const h = new Date().getHours();
    if (Math.random() < (h < 5 ? OMINOUS_NIGHT : OMINOUS_CHANCE)) { speak(pick(OMINOUS), { ominous: true }); return; }
    // About half the time a line about you, when there is one; otherwise one of the fixed ones.
    speak(dynamic.length && Math.random() < 0.5 ? pick(dynamic) : pick(IDLE));
  }

  function greet() {
    const name = me()?.name;
    if (!name) { speak(pick(IDLE)); return; }
    const h = new Date().getHours();
    const kind = h >= 5 && h < 11 ? 'greet_morning' : h >= 11 && h < 18 ? 'greet_day' : h >= 18 && h < 23 ? 'greet_night' : 'greet_late';
    const time = new Date().toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    speak(fill(pick(SAY[kind]), { name, time }));
  }

  // ---- what the other pages tell him ---------------------------------------------------------------------------------
  const desc = (d) => {
    const s = String(d || 'that pick');
    return s.length > 42 ? `${s.slice(0, 40)}…` : s;
  };
  function note(kind, d = {}) {
    try {
      if (kind === 'bet') {
        if (d.parlay) react('parlay', { legs: d.legs });
        else if (d.count > 1) react('bets', { n: d.count });
        else if (d.token) react('bet_token', { desc: desc(d.desc) });
        else if (d.stake >= 500) react('bet_big', { stake: credits(d.stake) }, { excited: true });
        else react('bet', { stake: credits(d.stake), desc: desc(d.desc), odds: d.odds });
      } else if (kind === 'slots') {
        const net = d.payout - d.stake;
        memory.slotNet += net;
        if (d.payout > 0) memory.slotLosses = 0; else memory.slotLosses += 1;
        const vars = { stake: credits(d.stake), payout: credits(d.payout), net: credits(Math.abs(net)), mult: d.multiplier, symbol: d.symbol };
        const down = memory.slotNet <= memory.slotDownMark;
        if (down) memory.slotDownMark -= SLOT_DOWN_STEP; // each mark comes up at most once
        const nag = Date.now() - memory.slotNagAt >= SLOT_NAG_MS;
        if (d.golden) react('slots_golden', vars, { excited: true });
        else if (d.multiplier >= 20) react('slots_big', vars, { excited: true });
        else if (d.payout > 0) react('slots_win', vars);
        else if (Math.random() >= SLOT_LOSS_TALK) { /* a losing spin: he keeps quiet most of the time */ }
        else if (down && nag) { memory.slotNagAt = Date.now(); react('slots_down', { net: credits(-memory.slotNet) }); }
        else if (nag && memory.slotLosses % SLOT_RUN_EVERY === 0) { memory.slotNagAt = Date.now(); react('slots_run', { n: memory.slotLosses }); }
        else if (Math.random() < SLOT_LOSE_CHANCE) react('slots_lose'); // not every losing spin
      } else if (kind === 'wheel') {
        const vars = { amount: credits(d.amount || 0), label: d.label };
        if (d.kind === 'jackpot') react('wheel_jackpot', vars, { excited: true });
        else if (d.kind === 'nothing') react('wheel_nothing');
        else if (d.kind === 'credits') react(d.amount >= 200 ? 'wheel_big' : 'wheel_credits', vars, { excited: d.amount >= 200 });
        else if (d.kind === 'bananas') react('wheel_bananas', vars);
        else if (d.kind === 'boost' || d.kind === 'insurance') react('wheel_token', { label: d.kind === 'boost' ? 'boost token' : 'insurance token' });
        else if (d.kind === 'item') react('wheel_item', vars, { excited: true });
        else if (d.kind === 'again') react('wheel_again');
      } else if (kind === 'transfer') {
        react('transfer', { amount: credits(d.amount), to: d.to });
      } else if (kind === 'buy') {
        react('buy', { item: d.item });
      } else if (kind === 'view') {
        walk(d.view);
        if (SAY[`view_${d.view}`] && Math.random() < 0.4) react(`view_${d.view}`, {}, { loud: false });
      }
    } catch (e) { /* Onkey never breaks the page */ }
  }

  // Settled bets from /api/bettor/me (recent_settled, newest first): react to ones he hasn't seen, and to losing runs.
  // The first look on a page load only remembers them.
  function settled(meNow) {
    const list = (meNow && meNow.recent_settled) || [];
    const ids = new Set(list.map((b) => b.id));
    if (memory.settledSeen === null) { memory.settledSeen = ids; return; }
    const fresh = list.filter((b) => !memory.settledSeen.has(b.id) && b.status !== 'void');
    memory.settledSeen = ids;
    if (!fresh.length) return;
    let run = 0;
    for (const b of list) { if (b.status === 'lost') run += 1; else if (b.status === 'won') break; }
    const best = fresh.filter((b) => b.status === 'won').sort((a, b) => (b.payout - b.stake) - (a.payout - a.stake))[0];
    if (best) {
      const vars = { desc: desc(best.description), net: credits(best.payout - best.stake), odds: best.odds_decimal.toFixed(2) };
      react(best.odds_decimal >= 6 ? 'won_long' : 'won', vars, { excited: true });
    } else if (run >= 5) react('lose_run_big', { n: run });
    else if (run >= 3) react('lose_run', { n: run });
    else react('lost', { desc: desc(fresh[0].description) });
  }

  // ---- walking to the tables ----------------------------------------------------------------------------------------
  // At the blackjack and poker tables Onkey is the dealer, so he gets up from the logo (which stays empty while he's
  // away: html.onkey-away) and waddles across the page to the dealer's seat; the dealer's face stays hidden until he
  // arrives (html.onkey-walking). Leaving the tables, he walks back from where he sat. Reduced motion just swaps them.
  const SEAT = '.onkey-dealer .onkey-face';
  // The tables hear about the walk: 'onkey:walking' when he's about to set off (a listener can set detail.hold, in ms,
  // to keep him in the logo a while first: casino.js does while Greg says his line), 'onkey:seated' when he's in the
  // chair (Greg waits for it).
  const arrive = () => { root.classList.remove('onkey-walking'); document.dispatchEvent(new CustomEvent('onkey:seated')); };
  let away = false, seat = null, walker = null, walkToken = 0;
  const root = document.documentElement;
  // The logo image showing (Onkey's, or Greg's in Greg Mode); still measurable while hidden for the walk.
  const logoRect = () => [...document.querySelectorAll('.topbar .brand .logo-link .logo')].map((i) => i.getBoundingClientRect())
    .find((r) => r.width > 0) || document.querySelector('.topbar .brand .logo-link')?.getBoundingClientRect();
  // Where the dealer sits, in page coordinates (so it still holds after the page scrolls or redraws).
  const pageRect = (r) => ({ x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height });
  const viewRect = (p) => ({ left: p.x - scrollX, top: p.y - scrollY, width: p.w, height: p.h });

  function stride(from, to, done) {
    walker?.remove();
    const token = ++walkToken;
    const img = document.createElement('img');
    img.src = '/assets/onkey-logo.png';
    img.alt = '';
    img.className = 'onkey-walker';
    document.body.appendChild(img);
    walker = img;
    const dx = to.left - from.left, dy = to.top - from.top;
    const dist = Math.hypot(dx, dy), d = Math.max(900, Math.min(1900, dist * 1.6));
    const steps = Math.max(3, Math.round(d / 190));
    const t0 = performance.now();
    const frame = (now) => {
      if (token !== walkToken) return;
      const u = Math.min(1, (now - t0) / d), e = u < 0.5 ? 2 * u * u : 1 - (-2 * u + 2) ** 2 / 2;
      const w = from.width + (to.width - from.width) * e, h = from.height + (to.height - from.height) * e;
      const step = Math.sin(Math.PI * steps * u);
      img.style.width = `${w}px`;
      img.style.height = `${h}px`;
      img.style.left = `${from.left + dx * e}px`;
      img.style.top = `${from.top + dy * e - Math.abs(step) * 12 * (1 - u * 0.4)}px`;
      img.style.transform = `rotate(${(step * 9).toFixed(2)}deg)`;
      if (u < 1) { requestAnimationFrame(frame); return; }
      img.remove();
      if (walker === img) walker = null;
      done();
    };
    requestAnimationFrame(frame);
  }

  // Wait (up to 5 s) for the table to draw its dealer, then call back with his face.
  function whenSeated(cb) {
    const token = walkToken, t0 = Date.now();
    const look = () => {
      if (token !== walkToken || !away) return;
      const face = document.querySelector(SEAT);
      if (face && face.getBoundingClientRect().width) { cb(face); return; }
      if (Date.now() - t0 < 5000) setTimeout(look, 80); else arrive();
    };
    look();
  }

  function walk(view) {
    const atTable = QUIET_VIEWS.has(view);
    if (atTable === away) return;
    const from = logoRect();
    away = atTable;
    el?.closest('.brand')?.classList.remove('onkey-talking', 'onkey-excited');
    if (atTable) {
      root.classList.add('onkey-walking');
      const plan = { hold: 0 };
      document.dispatchEvent(new CustomEvent('onkey:walking', { detail: plan }));
      const token = ++walkToken;
      whenSeated((face) => {
        setTimeout(() => { // he stays in the logo for plan.hold, then gets up
          if (token !== walkToken || !away) return;
          root.classList.add('onkey-away');
          seat = pageRect(face.getBoundingClientRect());
          if (reduced() || !from) { arrive(); return; }
          stride(from, viewRect(seat), arrive);
        }, plan.hold);
      });
    } else {
      root.classList.remove('onkey-walking');
      const to = logoRect();
      if (reduced() || !seat || !to) { root.classList.remove('onkey-away'); return; }
      const start = viewRect(seat);
      // Off screen (scrolled away)? Start him just above the top bar instead.
      const begin = start.top + start.height < 0 || start.top > innerHeight ? { ...start, top: -start.height } : start;
      stride(begin, to, () => { if (!away) root.classList.remove('onkey-away'); });
    }
  }

  // ---- setup ---------------------------------------------------------------------------------------------------------
  function init(ctx) { ({ state, fmt, esc } = ctx); }
  function start() {
    const brand = document.querySelector('.topbar .brand');
    if (!brand || el) return;
    el = document.createElement('div');
    el.className = 'onkey-says';
    el.setAttribute('aria-live', 'off'); // chatter, not news: screen readers aren't interrupted
    el.title = `Click to hush Onkey for ${HUSH_MIN} minutes`;
    el.innerHTML = '<span class="ook" aria-hidden="true"></span><span class="say"></span>';
    brand.appendChild(el);
    el.addEventListener('click', () => {
      react('hush', {}, { loud: false });
      try { sessionStorage.setItem('fs.onkeyHush', String(Date.now() + HUSH_MIN * 60000)); } catch (e) { /* not remembered */ }
    });
    setTimeout(greet, 2500);
    idleTimer = setTimeout(idle, FIRST_IDLE * 1000);
    if (state && QUIET_VIEWS.has(state.view)) walk(state.view); // opened straight onto a table
  }

  return { init, start, note, settled, speak, IDLE, SAY, DYNAMIC, OMINOUS };
})();
