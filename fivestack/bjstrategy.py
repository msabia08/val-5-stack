"""Blackjack's best move by expected value: the hint Onkey gives when a player pauses (blackjack.py's rules).

Infinite deck: every rank is 1 in 13, a ten-value card 4 in 13. Six decks play very close to that, and the hint never
looks at what's left in the shoe. The dealer has already peeked, so under an ace or a ten-value card his hole card is
drawn knowing it doesn't make blackjack. Every value is per credit of the hand's stake (+1 is winning it, -1 losing it):

- stand: the dealer draws to 17 or more (standing on soft 17);
- hit: one card, then the best of hitting again and standing;
- double: twice the stake on exactly one more card;
- split: two hands from the pair, each getting one card, then doubling allowed, and a new pair split again as often as
  it likes. Split aces take one card each and can't be hit or doubled, but a new ace is split again.

Unlimited re-splitting has a closed form in an infinite deck: a split hand worth V draws its own rank with chance q and
can then split into two hands worth V each, so V = A + q * max(keep, 2V), where A is the value over every other card and
keep the value of playing the pair as it is. `_split_hand` finds that fixed point.

Onkey's save: SAVE_CHANCE of the busts a hit or a double would cause, Onkey changes the card to make 21 instead
(blackjack.py). That's part of the game, so a bust is worth `busted()`, a little better than -1, everywhere here.
"""
from functools import lru_cache

ACE = 11
RANKS = (2, 3, 4, 5, 6, 7, 8, 9, 10, ACE)
P = {r: (4 / 13 if r == 10 else 1 / 13) for r in RANKS}
MOVES = ("hit", "stand", "double", "split")
SAVE_CHANCE = 0.005  # 1 bust in 200 Onkey fixes to 21; blackjack.py uses this one


def add(total, soft, card):
    """A hand (best total, soft) after drawing `card` (2-10, or 11 for an ace)."""
    t = total + card
    aces = int(soft) + (card == ACE)  # aces still counted as 11
    while t > 21 and aces:
        t -= 10
        aces -= 1
    return t, aces > 0


@lru_cache(maxsize=None)
def _dealer_from(total, soft):
    """The dealer's final total from a hand he's still drawing to: {17..21, or 22 for a bust: chance}."""
    if total > 21:
        return {22: 1.0}
    if total >= 17:
        return {total: 1.0}
    out = {}
    for c in RANKS:
        for k, v in _dealer_from(*add(total, soft, c)).items():
            out[k] = out.get(k, 0) + P[c] * v
    return out


@lru_cache(maxsize=None)
def dealer_final(up):
    """The dealer's final totals under upcard `up` (2-11), given he doesn't have blackjack: ((total, chance), ...)."""
    out, norm = {}, 0.0
    for c in RANKS:
        if {up, c} == {10, ACE}:
            continue  # that's blackjack, and the peek ruled it out
        norm += P[c]
        for k, v in _dealer_from(*add(up, up == ACE, c)).items():
            out[k] = out.get(k, 0) + P[c] * v
    return tuple((k, v / norm) for k, v in sorted(out.items()))


@lru_cache(maxsize=None)
def stand(total, up):
    if total > 21:
        return -1.0
    return sum(v if (k > 21 or total > k) else -v if total < k else 0.0 for k, v in dealer_final(up))


def busted(up):
    """A bust from a hit or double, per credit: lost, unless Onkey saves it and you stand on 21."""
    return (1 - SAVE_CHANCE) * -1.0 + SAVE_CHANCE * stand(21, up)


@lru_cache(maxsize=None)
def hit(total, soft, up):
    """Take a card, then play on (hit or stand) as well as possible."""
    ev = 0.0
    for c in RANKS:
        t, s = add(total, soft, c)
        ev += P[c] * (busted(up) if t > 21 else max(stand(t, up), hit(t, s, up)))
    return ev


@lru_cache(maxsize=None)
def double(total, soft, up):
    return 2 * sum(P[c] * (busted(up) if add(total, soft, c)[0] > 21 else stand(add(total, soft, c)[0], up)) for c in RANKS)


@lru_cache(maxsize=None)
def _two_cards(total, soft, up):
    """A two-card hand that may stand, hit or double (not split): a new hand after a split."""
    return max(stand(total, up), hit(total, soft, up), double(total, soft, up))


@lru_cache(maxsize=None)
def _split_hand(rank, up):
    """One hand started from a split `rank`, re-splitting a new pair whenever that's worth more."""
    if rank == ACE:  # one card each; a new ace may split again, otherwise it stands
        others = sum(P[c] * stand(add(ACE, True, c)[0], up) for c in RANKS if c != ACE)
        keep = stand(12, up)
    else:
        others = sum(P[c] * _two_cards(*add(rank, False, c), up) for c in RANKS if c != rank)
        keep = _two_cards(*add(rank, False, rank), up)
    q, v = P[rank], others + P[rank] * keep
    for _ in range(200):  # V = A + q * max(keep, 2V) contracts (2q < 1), so this settles quickly
        v = others + q * max(keep, 2 * v)
    return v


def split(rank, up):
    return 2 * _split_hand(rank, up)


def rank_of(card):
    """A card string ("As", "Td") as 2-10, or 11 for an ace."""
    r = card[0]
    return ACE if r == "A" else 10 if r in "TJQK" else int(r)


def hand_state(cards):
    total, soft = 0, False
    for c in cards:
        total, soft = add(total, soft, rank_of(c))
    return total, soft


def values(cards, upcard, moves):
    """{move: expected value per credit of the hand's stake} for each of `moves` the hand can make now."""
    up = rank_of(upcard)
    total, soft = hand_state(cards)
    calc = {"stand": lambda: stand(total, up), "hit": lambda: hit(total, soft, up),
            "double": lambda: double(total, soft, up), "split": lambda: split(rank_of(cards[0]), up)}
    return {m: round(calc[m](), 4) for m in MOVES if m in moves}


def best(cards, upcard, moves):
    """The move worth most, and every move's value: {"move", "ev": {move: value}}; None when there's nothing to do."""
    ev = values(cards, upcard, moves)
    if not ev:
        return None
    return {"move": max(ev, key=lambda m: (ev[m], -MOVES.index(m))), "ev": ev}


def house_edge():
    """The house's edge on a round under these rules, played by `best` (a dealer blackjack only takes the stake)."""
    total = 0.0
    for up in RANKS:
        dbj = P[ACE if up == 10 else 10] if up in (10, ACE) else 0.0
        ev = 0.0
        for a in RANKS:
            for b in RANKS:
                pair = P[a] * P[b]
                if {a, b} == {10, ACE}:
                    ev += pair * (dbj * 0 + (1 - dbj) * 1.5)
                    continue
                t, s = add(*add(0, False, a), b)
                options = [stand(t, up), hit(t, s, up), double(t, s, up)] + ([split(a, up)] if a == b else [])
                ev += pair * (dbj * -1 + (1 - dbj) * max(options))
        total += P[up] * ev
    return -total
