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
 * him for HUSH_MIN minutes (sessionStorage `fs.onkeyHush`).
 * Plain JS, no dependencies; loaded before app.js, which calls init() and start().
 */
window.FiveOnkey = (() => {
  'use strict';

  let state = null, fmt = null, esc = (s) => String(s);
  const IDLE_MIN = 70, IDLE_MAX = 140; // seconds between idle lines
  const FIRST_IDLE = 18; // seconds before the first one
  const HUSH_MIN = 15;
  const AWAY_MS = 3 * 60 * 1000, POKE_GAP = 45000; // welcome back after 3 minutes away; a poke at most every 45 s
  const BUBBLE_MS = 5500, BUBBLE_PER_WORD = 280; // how long a line stays up: a 10-word line about 8 seconds
  // On a phone (style.css's phone block) the bubble covers the top bar's chips, so a line stays half as long.
  const PHONE_BUBBLE = 0.5;
  const onPhone = () => matchMedia('(max-width: 640px)').matches;
  const OMINOUS_CHANCE = 0.12, OMINOUS_NIGHT = 0.35; // share of idle lines that are OMINOUS (more after midnight)
  const QUIET_VIEWS = new Set(['blackjack', 'poker', 'roulette']); // the dealer talks there
  const SCI_CHANCE = 0.03; // share of idle lines the scientist takes over (docs/onkey-lore.md); kept rare
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
    'Onkey just peeled a banana with one foot. Nobody clapped.',
    'Psst. The good picks are the ones with the little flames. Probably.',
    'Onkey has been sitting in this corner all day. Somebody bring snacks.',
    'If you need Onkey, Onkey will be right here. Forever. In the corner.',
    'Onkey practised his dealer shuffle. Only dropped the deck twice.',
    'Another day, another banana. Onkey lives simply.',
    'Somebody whiffed a Judge at point blank once. Onkey still thinks about it.',
    'Onkey has strong opinions about Bind. Mostly about the teleporters.',
    'Rotate! Rotate! Sorry, Onkey was dreaming about a retake.',
    'Onkey would main Sova. Onkey likes throwing things.',
    'Remember: one more game always means three more games.',
    'Onkey polished the slot machine. It\'s very shiny now.',
    'Onkey\'s tail is tingling. Big night incoming.',
    'Have you checked your forecasts lately? The numbers are lonely.',
    'Onkey is humming the Onkey Says song again. Sorry.',
    'The Golden Onkey hasn\'t been seen in a while. Onkey is worried about him.',
    'Onkey bought himself a hat. Onkey can\'t show you. It\'s too powerful.',
    'Ook ook ook. That one doesn\'t translate. Monkey thing.',
    'Every time someone places a parlay, Onkey gets a little thrill.',
    'Onkey tried counting all the credits once. Fell asleep at 4,000.',
    'Your bet slip looks empty. Onkey is just saying.',
    'An eco round is just a full buy with extra hope.',
    'Onkey believes in you. Onkey also believes in the house edge.',
    'Onkey checked: the squad still hasn\'t learned to trade kills. Classic.',
    'Banana break! Back in five. Onkey is always back in five.',
    'Onkey\'s cousin is gold. Onkey is brown. Onkey is fine with it. Mostly.',
    'Did you know Onkey can see your cursor? Onkey is following it. Ook.',
    'The standings shuffle every game night. Onkey loves the chaos.',
    'Onkey called the last game. He called it wrong, but he called it.',
    'Somewhere a smoke is landing in the wrong spot. Onkey can sense it.',
    'Onkey\'s banana stash is at an all-time high. Thanks for asking.',
    'Onkey asked the wheel for advice. The wheel just spun.',
    'Onkey dealt a blackjack to himself once. Then ate the cards.',
    'Tip: stretch your wrists. Onkey stretches his tail.',
    'If the squad goes 13-0 tonight, Onkey will do a backflip. Probably.',
    'Onkey keeps the shop tidy. Mostly by eating the clutter.',
  ];
  // Now and then, instead of an idle line: something Onkey maybe shouldn't have said. Shown in a dark bubble.
  // The scientist cuts in on Onkey's corner: his face takes the logo for one line, then Onkey shoves back in. His
  // lines, and only his, are in his accent ("th" becomes "z"). By occasion: idle, a loan, being broke, a bad run.
  const SCIENTIST = {
    idle: ['Twenty-five zousand. Ze offer stands.', 'I have plans for ze monkey. Zat is all you need to know.', 'You cannot afford him forever. Zink about it.',
      'Zis is not over.', 'Man Strudel will be upgraded. Zen we shall see.'],
    bank: ['A loan? Zere is an easier way to pay zis back. Sell me ze monkey.', 'Debt is a terrible zing. I can make it disappear.', 'Every loan brings you closer to my offer.'],
    broke: ['Nozing left? Twenty-five zousand would fix zat.', 'You look hungry. Ze monkey looks expensive.', 'I am a patient man. Your wallet is not.'],
    losing: ['Anozer loss. Zis cannot go on. My offer can.', 'Bad luck? Or is it ze monkey? Zink.'],
    refused: ['You will regret zis. Zey always do.', 'Sentiment. How... expensive.'],
  };
  const OMINOUS = [
    'Onkey has seen how this season ends. Onkey won\'t say.',
    // from the lore (docs/onkey-lore.md): Onkey's own voice, so no accent
    'The man with the glasses called again. Onkey didn\'t answer.',
    'Twenty-five thousand. That\'s what he offered. Onkey heard.',
    'Somebody is in the bushes. They can\'t read Onkey. Onkey checked.',
    'The floorboards are having a party tonight. You would not fit.',
    'Man Strudel waved from across the street. Onkey waved back.',
    'If the candy has an E on it, don\'t eat it.',
    'He says he has plans for Onkey. He won\'t say what they are.',
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
    'Onkey sees one more tab open than you think you have.',
    'The reels stop when Onkey says they stop.',
    'Last night the scoreboard showed a sixth player. Just for a second.',
    'Onkey isn\'t smiling. That\'s just the shape of the logo.',
    'There is a banana in the ledger that nobody bought.',
    'The arcade machines play by themselves after you close the tab.',
    'Onkey knows what you\'re going to bet. Onkey always knows.',
    'Don\'t check the standings at 3 AM. They look different.',
    'Something in the shop has been bought by no one. It\'s still sold out.',
    'The Golden Onkey blinked. Golden Onkeys don\'t blink.',
    'Greg left a note. Onkey hasn\'t opened it. Onkey never will.',
    'Every bet you cancel goes to the same place.',
    'Ook. Ook. Ook. Onkey is counting down. To what, Onkey can\'t say.',
    'Shh. If you listen closely, you can hear the house edge.',
    'Onkey was a different colour yesterday. Nobody noticed.',
  ];
  // Reactions: one line is picked at random; {name}-style blanks are filled from the event.
  const SAY = {
    greet_morning: ['Morning, {name}! Coffee first, parlays second.', 'Up early, {name}? Onkey respects that.',
      'Rise and shine, {name}. The bananas are fresh.', 'Good morning, {name}! Onkey has been up for hours. Eating.',
      'Morning, {name}. The odds woke up before you did.'],
    greet_day: ['Ook! Welcome back, {name}.', 'Look who it is. Hi, {name}.', '{name}! Onkey saved you a banana.',
      'Afternoon, {name}. The odds missed you.', 'Hey {name}! Onkey was just talking about you. Nice things. Mostly.',
      '{name}! Onkey\'s favourite human. Don\'t tell the others.'],
    greet_night: ['Evening, {name}. Game night?', 'Ook ook, {name}. The squad\'s online, Onkey can feel it.',
      'There you are, {name}. Lines are up, bananas are ripe.', 'Evening, {name}. Onkey put the good lights on.',
      '{name}! Perfect timing. The night is young and so are the odds.'],
    greet_late: ['It\'s {time}, {name}. Onkey respects the grind.', 'Still up, {name}? One more game. Onkey knows.',
      'The night shift, {name}? Onkey never sleeps either.', '{time}? {name}, your bed misses you. Onkey doesn\'t judge.'],
    welcome_back: ['Oh! You\'re back. Onkey totally wasn\'t asleep.', 'Welcome back, {name}. Onkey kept your seat warm.',
      'There you are! Onkey thought you\'d gone to bet somewhere else.', 'Back already? Onkey missed you. A little.',
      '{name} returns! Onkey did not touch anything while you were gone.'],
    poke: ['Ook? You looking at Onkey?', 'Hey! That tickles.', 'Onkey sees you hovering.', 'Yes? Onkey is listening.',
      'Pat pat. Onkey accepts pats.', 'Careful, Onkey bites. Gently.', 'Onkey is busy. Doing monkey stuff.',
      'Click Onkey to go to the shop. Or just admire him.'],
    bet_token: ['Token on {desc}? Onkey likes a monkey with a plan.', 'Using a token! Onkey approves of free stuff.',
      'A token on {desc}. Free stuff tastes better.'],
    bet: ['{stake} on {desc}? Bold.', 'Onkey has written down your bet. In crayon.', 'Locked in at {odds}. No take-backs after a minute.',
      '{desc}. Onkey likes it. Onkey likes everything.', '{desc} at {odds}. Onkey would have done the same. Probably.',
      'Bet received. Onkey will pretend he isn\'t watching.', '{desc}? Onkey is putting a banana on that too.',
      'Ooh, {desc}. Onkey has a good feeling about this one.', 'Ticket stamped. {stake} credits riding on {desc}.'],
    bet_big: ['{stake} credits?! Onkey needs to sit down.', '{stake} on one pick. Onkey is sweating for you.',
      '{stake}! Onkey is clutching his banana very tightly now.'],
    parlay: ['A {legs}-leg parlay. Onkey admires the optimism.', 'Parlays: where dreams go to... sometimes win!', '{legs} legs. That\'s more legs than Onkey has.',
      '{legs} legs, one dream. Onkey is rooting for every one.', 'Parlay locked. Onkey is lighting a candle.'],
    bets: ['{n} bets in. The slip is empty and your heart is full.', '{n} picks placed. Onkey will be watching every one.',
      '{n} tickets! Onkey needs a bigger clipboard.'],
    slip_add: ['{desc}? Ooh.', 'Onkey sees you eyeing {desc}.', 'In the slip it goes.', '{desc}. Onkey nods slowly.',
      'Adding {desc}? Onkey is intrigued.', 'That\'s {n} in the slip. Onkey is counting.'],
    slip_many: ['{n} picks in the slip. Parlay? Onkey whispers parlay.', '{n} picks! The slip is getting heavy.'],
    slip_clear: ['Cleared! A fresh start.', 'Slip wiped. Onkey respects second thoughts.', 'All gone. Onkey didn\'t like those either.'],
    won: ['Cha-ching! {desc} paid {net}.', 'You won {net}. Onkey is happy. The house is not.', 'Called it. +{net} on {desc}.',
      '{desc} came in. +{net}. Do a little dance.', 'Winner! Onkey always believed in {desc}. Always.',
      '+{net}! Onkey is doing the banana dance.', '{desc} delivered. +{net}. Onkey told you so. Didn\'t he?'],
    won_long: ['A long shot came in! +{net}! Onkey is screaming!', 'At {odds}?! +{net}! Somebody hold Onkey\'s banana!',
      '{odds}! Onkey fell off the logo! +{net}!'],
    lost: ['{desc} didn\'t hit. Onkey felt that.', 'Lost one. Shake it off.', '{desc}... Onkey isn\'t going to talk about it.',
      'Not this time. The next one\'s yours.', '{desc} let you down. Onkey will have a word with it.',
      'Oof. {desc}. Onkey brings you a consolation banana.', '{desc} said no. Rude.'],
    lose_run: ['That\'s {n} losing bets in a row. Maybe bet the other side?', '{n} straight losses. The comeback starts now. Probably.',
      '{n} in a row. Onkey is rubbing his lucky banana for you.'],
    lose_run_big: ['{n} losses in a row. Onkey is holding your bananas for safekeeping.', '{n} straight. Onkey is hiding the parlay button.',
      '{n} straight losses. Onkey has seen worse. Onkey has not seen worse.'],
    squad_won: ['The squad won! Onkey is doing laps around the logo.', 'W! That\'s {wins}-{losses} now. Onkey is proud.',
      'Another win for the squad. Onkey called it. Loudly.', 'GG! The squad takes it. Bananas for everyone.'],
    squad_lost: ['The squad lost one. Onkey is staring at the scoreboard.', 'L. {wins}-{losses}. Onkey needs a minute.',
      'A loss. Onkey blames the teleporter. Every time.', 'That one hurt. Shake it off, squad.'],
    squad_games: ['{n} new games came in! The recaps are hot.', '{n} games just landed. Onkey is reading the scoreboard.'],
    sync: ['Fetching the latest games. Onkey is pressing all the buttons.', 'Sync started. Onkey is jogging to the server.',
      'Checking for new games. Onkey loves fresh data.'],
    slots_lose: ['The reels hate you today. Onkey doesn\'t. Mostly.', 'So close. Not really. But so close.', 'Spin it again. Onkey dares you.',
      'The machine says thank you.', 'Nothing. The reels are being shy.', 'The reels went for a walk.',
      'Hmm. The machine is thinking about it.', 'Not that one. The next one, maybe.'],
    slots_run: ['{n} spins without a win. The machine is just warming up.', '{n} dry spins. Onkey can hear the machine laughing.',
      '{n} spins. Onkey is checking the machine is plugged in.'],
    slots_win: ['A little win! {payout} back.', 'Ding ding! +{net}.', 'Three {symbol}s. Onkey approves.',
      'A hit! The machine coughs up {payout}.', '{symbol}s! Small, but Onkey will take it.', 'Ding! {symbol}s in a row.',
      'The machine blinked first. +{net}.'],
    slots_big: ['{mult}x! Onkey heard that from up here!', '{symbol}s across! +{net}!', 'Somebody call security, {name} is robbing the machine!',
      '{mult}x! The machine is crying. Onkey is not.', '+{net}! Onkey is buying you a hat. With your money.'],
    slots_golden: ['THE GOLDEN ONKEY! That\'s Onkey\'s cousin!', 'GOLDEN ONKEY!! +{net}! Onkey is crying!',
      'All three golden cousins?! Onkey is calling his mum!'],
    slots_spotted: ['Golden Onkey spotted! +{net}!', 'Was that Onkey\'s golden cousin? +{net}!', 'A golden Onkey sighting! Onkey waves at him.',
      'Cousin! Over here! +{net}!', 'There he is! The Golden Onkey says hi. +{net}.'],
    slots_wild: ['The Golden Onkey doubled it! +{net}!', '{symbol}s, times {factor}! Onkey\'s cousin is generous. +{net}!',
      'Wild! The Golden Onkey filled in and doubled up. +{net}!', 'Times {factor}?! Onkey owes his cousin a banana. +{net}!'],
    slots_tease: ['Ooh ooh ooh...', 'Come on, come on...', 'Onkey can\'t look!', 'Is it? Is it?!', 'Stop. STOP. Please stop.',
      'Onkey is holding his breath...'],
    slots_max: ['500 a spin? Onkey loves a high roller.', 'Max bet! The machine just sat up straight.', 'Big stakes. Onkey is watching closely.'],
    slots_down: ['You\'re down {net} on slots this visit. Onkey suggests a snack break.',
      'Down {net} this visit. The bet page misses you.'],
    wheel_jackpot: ['THE JACKPOT! {amount} credits! Onkey is fainting!', 'You took the whole jackpot! Onkey has to sit down.'],
    wheel_big: ['{amount} credits from the wheel! Onkey spun it with love.', '{amount}! Big wheel energy.'],
    wheel_credits: ['{amount} free credits. Don\'t spend them all at once.', 'Free money! {amount} credits.', '{amount} credits. The wheel provides.'],
    wheel_bananas: ['{amount} bananas! Onkey would like a few.', 'Bananas! Onkey approves of this prize.', '{amount} bananas! Onkey is drooling.'],
    wheel_token: ['A {label}. Use it wisely. Or don\'t.', '{label}! Tap it on a pick in your bet slip.'],
    wheel_item: ['Free swag! Onkey picked it himself.', 'A free cosmetic! Go put it on.'],
    wheel_again: ['Two more spins! The wheel likes you.', 'A respin, twice over! Onkey didn\'t see that coming.'],
    transfer: ['Sending {amount} to {to}? The generous monkey gets rewarded.', '{amount} to {to}. Onkey hopes they say thanks.',
      '{amount} credits for {to}. Onkey is touched. Genuinely.'],
    buy: ['Nice {item}. Very you.', '{item}! Onkey approves of this purchase.', 'Ooh, {item}. Fancy.',
      '{item}! You look fantastic. Onkey would know.', 'Bananas well spent. {item} suits you.'],
    prank: ['Pranking {to}? Onkey saw nothing. Nothing!', 'Ook ook! {to} won\'t know what hit them.',
      'Onkey loves monkey business. Poor {to}.', '{item} on {to}! Onkey is giggling.'],
    equip: ['Wearing {item}. Strut it.', '{item} on. Onkey is impressed.', 'Ooh, {item}. The troop will notice.'],
    unequip: ['Taken off. Onkey liked it, but okay.', 'Back to basics. Classic.'],
    theme_dark: ['Lights off. Onkey can see in the dark. Mostly.', 'Dark mode. Very mysterious.'],
    theme_light: ['Light mode! Onkey needs sunglasses.', 'So bright. Onkey\'s eyes!'],
    'theme_th-greg': ['GREG MODE. Onkey has mixed feelings.', 'Greg is here. Greg is everywhere now.', 'Hi Greg. Onkey says hi, Greg.'],
    'theme_th-onkey': ['Onkey Mode! Finally, good taste.', 'An Onkey theme? Onkey is blushing.'],
    'theme_th-jungle': ['Welcome to the jungle. Onkey\'s home turf.', 'Ah, the jungle. Smells like bananas.'],
    arcade_best: ['A new best! {score} on {game}! Onkey is singing!', '{score}?! Onkey\'s record is in danger!',
      'Personal best on {game}! Onkey bows.'],
    arcade_champ: ['NUMBER ONE on {game}! Onkey bows down!', 'The {game} crown is yours! {score}!'],
    arcade_done: ['{score} on {game}. Onkey has seen better. Onkey has also seen worse.', '{score}! Another banana, another go?',
      'Game over. Onkey had fun watching.', '{score} on {game}. Not bad for a human.'],
    arcade_zero: ['Zero? Onkey won\'t tell anyone.', 'That was a warm-up. Right?'],
    view_shop: ['Welcome to Onkey\'s Shop. Touch everything.', 'Browsing again? Onkey has bananas to take.',
      'New stock! Onkey says that every time.', 'Onkey recommends the hat. Any hat.'],
    view_arcade: ['The arcade! Onkey\'s high score is untouchable.', 'Insert banana to play.', 'Five bananas a go. Onkey takes exact change.',
      'Onkey Says is Onkey\'s favourite. Obviously.'],
    view_slots: ['Pull the lever. Onkey dares you.', 'The machine is hungry. Feed it.', 'Somewhere in there is a Golden Onkey. Go find him.',
      'Space bar spins. Onkey checked.', 'The Golden Onkey is wild now. Onkey taught him that.'],
    view_crash: ['Onkey built the rocket. Onkey did not test the rocket.', 'It goes up. Then it stops going up. Get out before that.',
      'Onkey has never seen it land.', 'The rocket is a banana. Onkey sees no problem with this.'],
    crash_out: ['Out at {mult}. {amount} credits. Onkey would have held. Onkey would have been wrong.', '{mult} and safe. Sensible. Onkey respects it.',
      'You got out at {mult}. The rocket did not.'],
    crash_big: ['{mult}! Onkey could barely see you up there.', 'Out at {mult}! {amount} credits! Onkey is waving from the ground.'],
    crash_lost: ['It crashed at {mult}. Onkey heard it from here.', 'Gone at {mult}. Onkey will build another one.',
      'The rocket is fine. The rocket is not fine.', 'Onkey said get out. Onkey did not say it out loud.'],
    crash_pad: ['It never left the pad. Onkey is looking into it.', 'That one was a practice rocket.'],
    crash_moon: ['All the way up! Onkey did not know it could do that.'],
    view_stampede: ['The stampede machine. Onkey ran through it once. On purpose.', 'Five reels, four rows, one Onkey. Mostly Onkey.',
      'Onkey can hear the jackpots growing. They hum.', 'Watch for fireballs. Onkey lit them himself.'],
    st_win: ['+{net}. The herd approves.', 'A little stampede of credits. +{net}.', 'Ways! So many ways! +{net}.'],
    st_big: ['{mult}x! Onkey felt that in his feet!', 'The whole machine is shaking. That was you. +{net}!', '+{net}! Onkey is telling everyone.'],
    st_free: ['Free spins! Onkey brought the bongos.', 'Spikes! Onkey is drumming already.', 'Three spikes. Onkey knew. Onkey always knows.'],
    st_hold: ['Fireballs! Hold them! HOLD THEM!', 'Hold and spin! Onkey is not breathing until it\'s over.', 'Onkey loves a fireball. From a distance.'],
    st_jackpot: ['The {jackpot} jackpot! +{net}! Onkey saw the whole thing!', '{jackpot} jackpot! Onkey is screaming into a banana!'],
    st_grand: ['THE GRAND!!! Onkey needs to sit down.', 'The Grand. Onkey will tell this story forever.', 'GRAND JACKPOT. Onkey is crying. Happy crying.'],
    st_stampede: ['That was Onkey running through. Sorry. You\'re welcome.', 'Stampede! Onkey left some wilds behind.'],
    st_inferno: ['Onkey breathed on it. Times {inferno}.', 'Inferno! Onkey had spicy bananas.'],
    st_lose: ['The herd went the other way.', 'Not this time. The reels are still warm.'],
    st_golden: ['The Golden Onkey! Onkey has never looked better.', 'Did you see him? Golden. Shining. Onkey, basically.', 'Golden Onkey spotted. Onkey says keep it quiet. +{net}.'],
    st_detonated: ['Spike planted, spike detonated. Onkey covered his ears.', 'BOOM. Third spike. Onkey knew it was coming.'],
    st_defused: ['Defused. Onkey was so sure.', 'Somebody defused it. Not Onkey. Onkey was rooting for the boom.'],
    view_wheel: ['Round and round she goes.', 'The wheel is shiny today.', 'Onkey greased the wheel. For luck.'],
    view_hunt: ['Onkey is throwing bananas. Again. Pick them up?', 'Bananas everywhere! Onkey will pay. One credit each, 250 a day.'],
    hunt: ['{n} bananas picked. Onkey\'s arms are tired just watching.', 'Ook! {n} already? Keep going.', 'That\'s {n}. Onkey could do it faster. Probably.'],
    hunt_found: ['You found {label}! Onkey hid that one himself.', 'Ook! {label}. Onkey forgot he buried it there.'],
    sci_back: ['Onkey is back. Ignore the man with the glasses.', 'Don\'t listen to him. Onkey is staying.', 'Ook. He does that. Hang up next time.', 'Onkey is not for sale.'],
    sci_refused: ['Onkey heard that. Thank you.', 'Not for sale. Onkey knew you\'d say it.'],
    hunt_claw: ['That was his claw. Onkey knows that claw.', 'He took a banana. He wants more than bananas.'],
    hunt_done: ['{today} credits of bananas! Onkey is full. Come back tomorrow.', 'That\'s the lot for today. Onkey needs a nap.'],
    view_bettors: ['The standings. Find yourself. Onkey will wait.', 'Who\'s on top? Onkey already knows.',
      'Leaderboard time. Onkey loves a rivalry.'],
    view_matches: ['Recaps! Relive the glory. Or the pain.', 'Onkey watched every round. Twice.', 'Pick a game. Onkey remembers them all.'],
    view_players: ['The stats don\'t lie. Onkey sometimes does.', 'Somebody\'s carrying. Find out who.', 'Look at those numbers. Onkey is impressed. Mostly.'],
    view_viz: ['Charts! Onkey loves a line that goes up.', 'Pretty colours. Onkey picked them himself.'],
    view_troop: ['The monkeys. Onkey knows every one of them.', 'Check out everyone\'s drip.', 'The troop! Onkey\'s whole family.'],
    view_forecasts: ['Forecasts: like weather, but for frags.', 'Onkey predicts... bananas. Always bananas.'],
    view_odds: ['Place bets. Onkey\'s watching.', 'The odds are fresh. Onkey baked them himself.', 'Look for the flames. Hot picks run hot. Sometimes.',
      'Frosty buttons are cold picks. Onkey shivers.'],
    view_overview: ['The front page. Onkey reads it every morning.', 'Everything at a glance. Onkey glances a lot.'],
    view_squad: ['The squad! Drag them around. They don\'t mind.', 'Who\'s in, who\'s benched? Onkey has opinions.'],
    view_setup: ['The settings. Don\'t break anything. Onkey is watching.', 'Ooh, the knobs and dials.'],
    hush: ['Fine. Onkey will be quiet for a bit.', 'Okay, okay. Shh. Onkey is shushing.', 'Onkey zips his lips. For now.'],
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
    () => { const m = Math.floor((Date.now() - startedAt) / 60000); return m >= 45 ? `You've been here ${m} minutes. Onkey is honoured.` : null; },
    () => { const n = state?.slip?.length; return n > 0 && state.view !== 'odds' ? `${n} pick${n === 1 ? '' : 's'} waiting in your slip. They're getting cold.` : null; },
    () => (state && !me() ? 'Sign in so Onkey knows who to cheer for.' : null),
    () => (state?.view === 'slots' ? pick(['The Golden Onkey is somewhere in there. Onkey can smell him.', 'Space bar spins. Your thumb deserves it.']) : null),
    () => (state?.view === 'arcade' ? 'Onkey Says is the hardest one. Onkey made it that way.' : null),
    () => (state?.view === 'shop' ? 'Preview anything. Onkey won\'t charge for looking.' : null),
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
  const fmtN = (v) => (fmt && fmt.n0 ? fmt.n0(v) : String(Math.round(v)));
  const startedAt = Date.now();
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
  const memory = { slotLosses: 0, slotNet: 0, slotDownMark: -SLOT_DOWN_FIRST, slotNagAt: 0, settledSeen: null, record: null };

  // ---- talking -------------------------------------------------------------------------------------------------------
  let el = null, hideTimer = null, idleTimer = null, lastSaid = '', lastAt = 0;
  const hushed = () => { try { return Number(sessionStorage.getItem('fs.onkeyHush') || 0) > Date.now(); } catch (e) { return false; } };

  // `table`: a line of the dealer's, which casino.js sends here on a phone, where no dealer sits on the felt; it's the
  // one thing he says on the quiet views. `tone` ('gold' / 'red') tints the bubble, as the dealer's does.
  function speak(text, { loud = false, excited = false, ominous = false, table = false, tone = '' } = {}) {
    if (!el || !text || hushed()) return;
    if (QUIET_VIEWS.has(state?.view) && !table) return;
    if (text === lastSaid) return; // never twice in a row
    lastSaid = text; lastAt = Date.now();
    const words = text.split(/\s+/).length;
    const n = Math.max(2, Math.min(6, Math.round(words / 2)));
    const noises = Array.from({ length: n }, (_, i) => (excited ? (i % 3 === 2 ? 'ook' : 'eek') : (i % 3 === 1 ? 'eek' : 'ook')));
    el.querySelector('.ook').textContent = noises.map((x) => x[0].toUpperCase() + x.slice(1) + (excited ? '!' : '')).join(' ');
    el.querySelector('.say').textContent = text;
    el.classList.toggle('tone-gold', tone === 'gold');
    el.classList.toggle('tone-red', tone === 'red');
    const brand = el.closest('.brand');
    brand.classList.remove('onkey-talking', 'onkey-excited', 'onkey-ominous', 'onkey-scientist');
    void brand.offsetWidth; // restart the hop
    brand.classList.add('onkey-talking');
    if (excited) brand.classList.add('onkey-excited');
    if (ominous) brand.classList.add('onkey-ominous');
    if (loud && window.FiveCasino && window.FiveCasino.chatter) window.FiveCasino.chatter(noises);
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => brand.classList.remove('onkey-talking', 'onkey-excited', 'onkey-ominous'), (BUBBLE_MS + words * BUBBLE_PER_WORD) * (onPhone() ? PHONE_BUBBLE : 1));
  }
  // The scientist takes Onkey's corner for one line (`kind`: a SCIENTIST list), then Onkey is back with a word of his own.
  function scientist(kind = 'idle') {
    const text = pick(SCIENTIST[kind] || SCIENTIST.idle);
    if (!el || hushed() || QUIET_VIEWS.has(state?.view) || text === lastSaid) return false;
    lastSaid = text; lastAt = Date.now();
    el.querySelector('.ook').textContent = '*the line crackles*';
    el.querySelector('.say').textContent = text;
    el.classList.remove('tone-gold', 'tone-red');
    const brand = el.closest('.brand');
    brand.classList.remove('onkey-talking', 'onkey-excited', 'onkey-ominous', 'onkey-scientist');
    void brand.offsetWidth;
    brand.classList.add('onkey-talking', 'onkey-scientist');
    clearTimeout(hideTimer);
    const ms = (BUBBLE_MS + text.split(/\s+/).length * BUBBLE_PER_WORD) * (onPhone() ? PHONE_BUBBLE : 1);
    hideTimer = setTimeout(() => {
      brand.classList.remove('onkey-talking', 'onkey-scientist');
      setTimeout(() => react('sci_back', {}, { loud: false }), 500);
    }, ms);
    return true;
  }

  const react = (kind, vars = {}, opts = {}) => {
    const list = SAY[kind];
    if (list) speak(fill(pick(list), { name: me()?.name || 'friend', ...vars }), { loud: true, ...opts });
  };
  // A small reaction (a pick added to the slip, a theme change, a poke): only `chance` of the time, and never within
  // CHIME_GAP of the last thing he said, so he chimes in without talking over himself. Silent unless asked.
  const CHIME_GAP = 12000;
  const chime = (kind, vars = {}, chance = 0.5, opts = {}) => {
    if (Date.now() - lastAt < CHIME_GAP || Math.random() >= chance) return;
    react(kind, vars, { loud: false, ...opts });
  };

  function idle() {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(idle, (IDLE_MIN + Math.random() * (IDLE_MAX - IDLE_MIN)) * 1000);
    if (document.hidden || Date.now() - lastAt < 30000) return;
    const dynamic = DYNAMIC.map((f) => { try { return f(); } catch (e) { return null; } }).filter(Boolean);
    const h = new Date().getHours();
    if (Math.random() < SCI_CHANCE && scientist(me() && me().balance < 100 ? 'broke' : 'idle')) return;
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
        else if (d.wild) react('slots_wild', { ...vars, factor: d.wild }, { excited: true });
        else if (d.spotted) react('slots_spotted', vars, { excited: d.multiplier >= 10 });
        else if (d.multiplier >= 20) react('slots_big', vars, { excited: true });
        else if (d.payout > 0) react('slots_win', vars);
        else if (Math.random() >= SLOT_LOSS_TALK) { /* a losing spin: he keeps quiet most of the time */ }
        else if (down && nag) { memory.slotNagAt = Date.now(); react('slots_down', { net: credits(-memory.slotNet) }); }
        else if (nag && memory.slotLosses % SLOT_RUN_EVERY === 0) { memory.slotNagAt = Date.now(); react('slots_run', { n: memory.slotLosses }); }
        else if (Math.random() < SLOT_LOSE_CHANCE) react('slots_lose'); // not every losing spin
      } else if (kind === 'wheel') {
        const vars = { amount: credits(d.amount || 0), label: d.label };
        if (d.kind === 'jackpot') react('wheel_jackpot', vars, { excited: true });
        else if (d.kind === 'credits') react(d.amount >= 500 ? 'wheel_big' : 'wheel_credits', vars, { excited: d.amount >= 500 });
        else if (d.kind === 'bananas') react('wheel_bananas', vars);
        else if (d.kind === 'boost' || d.kind === 'insurance') react('wheel_token', { label: d.kind === 'boost' ? 'boost token' : 'insurance token' });
        else if (d.kind === 'item') react('wheel_item', vars, { excited: true });
        else if (d.kind === 'again') react('wheel_again');
      } else if (kind === 'crash') {
        if (d.kind === 'out') react(d.big ? 'crash_big' : 'crash_out', { mult: d.mult, amount: d.amount }, { excited: d.big });
        else if (d.kind === 'moon') react('crash_moon', {}, { excited: true });
        else chime(d.kind === 'pad' ? 'crash_pad' : 'crash_lost', { mult: d.mult }, 0.5);
      } else if (kind === 'hunt') {
        react('hunt', { n: d.n, today: d.today });
      } else if (kind === 'scientist') {
        scientist(d.kind);
      } else if (kind === 'sci_refused') {
        react('sci_refused', {}, { excited: true });
      } else if (kind === 'hunt_claw') {
        chime('hunt_claw', {}, 0.6);
      } else if (kind === 'hunt_found') {
        react('hunt_found', { label: d.label }, { excited: true });
      } else if (kind === 'hunt_done') {
        react('hunt_done', { today: d.today }, { excited: true });
      } else if (kind === 'transfer') {
        react('transfer', { amount: credits(d.amount), to: d.to });
      } else if (kind === 'buy') {
        if (d.target) react('prank', { item: d.item, to: d.target }, { excited: true });
        else react('buy', { item: d.item });
      } else if (kind === 'equip') {
        chime(d.item ? 'equip' : 'unequip', { item: d.item }, 0.7);
      } else if (kind === 'slip') {
        if (d.cleared) chime('slip_clear', {}, 0.5);
        else if (d.added) chime(d.n >= 3 ? 'slip_many' : 'slip_add', { desc: desc(d.desc), n: d.n }, d.n >= 3 ? 0.5 : 0.3);
      } else if (kind === 'theme') {
        chime(`theme_${d.theme}`, {}, d.theme.startsWith('th-') ? 0.9 : 0.5, { excited: d.theme === 'th-greg' });
      } else if (kind === 'stampede') {
        const vars = { net: credits(Math.max(0, d.payout - d.stake)), mult: Math.round(d.multiplier), jackpot: d.jackpot, inferno: d.inferno };
        if (d.jackpot === 'Grand') react('st_grand', vars, { excited: true });
        else if (d.jackpot) react('st_jackpot', vars, { excited: true });
        else if (d.golden) react('st_golden', vars, { excited: true });
        else if (d.plant === 'detonated') react('st_detonated', vars, { excited: true });
        else if (d.free_spins) react('st_free', vars, { excited: true });
        else if (d.hold) react('st_hold', vars, { excited: true });
        else if (d.multiplier >= 15) react('st_big', vars, { excited: true });
        else if (d.inferno) chime('st_inferno', vars, 0.7);
        else if (d.plant === 'defused') chime('st_defused', vars, 0.5);
        else if (d.event === 'stampede') chime('st_stampede', vars, 0.5);
        else if (d.payout > d.stake) chime('st_win', vars, 0.3);
        else if (!d.payout) chime('st_lose', vars, 0.06);
      } else if (kind === 'slots_tease') {
        // The tease: he gasps along with the reel (no chatter over its drone), whatever else he said a moment ago.
        speak(pick(SAY.slots_tease), { excited: true });
      } else if (kind === 'slots_stake') {
        if (d.stake >= 500) chime('slots_max', {}, 0.8, { excited: true });
      } else if (kind === 'arcade') {
        const vars = { game: d.game, score: fmtN(d.score) };
        if (d.champion) react('arcade_champ', vars, { excited: true });
        else if (d.best && d.score > 0) react('arcade_best', vars, { excited: true });
        else if (!d.score) chime('arcade_zero', vars, 0.7);
        else chime('arcade_done', vars, 0.6);
      } else if (kind === 'sync') {
        chime('sync', {}, 0.6);
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
    } else if (run >= 5 && Math.random() < 0.4 && scientist('losing')) { /* he has a suggestion */
    } else if (run >= 5) react('lose_run_big', { n: run });
    else if (run >= 3) react('lose_run', { n: run });
    else react('lost', { desc: desc(fresh[0].description) });
  }

  // The squad's record from /api/status (loadStatus() passes it on every poll): when games come in, he cheers a win
  // or mourns a loss (more than one at once, he just says how many). The first look on a page load only remembers it.
  function status(s) {
    const rec = s && s.record;
    if (!rec) return;
    const was = memory.record;
    memory.record = { games: rec.games, wins: rec.wins, losses: rec.losses };
    if (!was || rec.games <= was.games) return;
    const n = rec.games - was.games, vars = { wins: rec.wins, losses: rec.losses, n };
    if (n > 1) react('squad_games', vars, { excited: true });
    else if (rec.wins > was.wins) react('squad_won', vars, { excited: true });
    else if (rec.losses > was.losses) react('squad_lost', vars);
  }

  // ---- walking to the tables ----------------------------------------------------------------------------------------
  // At the blackjack and poker tables Onkey is the dealer, so he gets up from the logo (which stays empty while he's
  // away: html.onkey-away) and waddles across the page to the dealer's seat; the dealer's face stays hidden until he
  // arrives (html.onkey-walking). Leaving the tables, he walks back from where he sat.
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
    const atTable = QUIET_VIEWS.has(view) && !onPhone(); // on a phone he stays in the logo and deals from there
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
          if (!from) { arrive(); return; }
          stride(from, viewRect(seat), arrive);
        }, plan.hold);
      });
    } else {
      root.classList.remove('onkey-walking');
      const to = logoRect();
      if (!seat || !to) { root.classList.remove('onkey-away'); return; }
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
    // Coming back to the tab after AWAY_MS or more: he notices.
    let hiddenAt = 0;
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) { hiddenAt = Date.now(); return; }
      if (hiddenAt && Date.now() - hiddenAt >= AWAY_MS) setTimeout(() => react('welcome_back', {}, { loud: false }), 1200);
      hiddenAt = 0;
    });
    // Hovering over him now and then gets a word (POKE_GAP apart at most).
    let pokedAt = 0;
    brand.querySelector('.logo-link')?.addEventListener('mouseenter', () => {
      if (Date.now() - pokedAt < POKE_GAP) return;
      pokedAt = Date.now();
      chime('poke', {}, 0.5);
    });
    if (state && QUIET_VIEWS.has(state.view)) walk(state.view); // opened straight onto a table
  }

  return { init, start, note, settled, status, speak, scientist, IDLE, SAY, DYNAMIC, OMINOUS, SCIENTIST };
})();
