"""Playing cards for the casino (blackjack.py, poker.py): decks, shuffling and poker hand ranking.

A card is two characters, rank then suit: "As" (ace of spades), "Td" (ten of diamonds). Shuffles use the system's
secure random source, like slots.
"""
import secrets
from itertools import combinations

RANKS = "23456789TJQKA"
SUITS = "shdc"  # spades, hearts, diamonds, clubs
RANK_NAMES = {2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight", 9: "Nine", 10: "Ten",
              11: "Jack", 12: "Queen", 13: "King", 14: "Ace"}
PLURAL = {6: "Sixes"}
HAND_NAMES = ["High card", "Pair", "Two pair", "Three of a kind", "Straight", "Flush", "Full house", "Four of a kind",
              "Straight flush"]
_rng = secrets.SystemRandom()


def deck(decks=1):
    return [r + s for _ in range(decks) for s in SUITS for r in RANKS]


def shuffled(decks=1):
    """A freshly shuffled shoe of `decks` decks. The self-test patches this to stack the deck."""
    cards = deck(decks)
    _rng.shuffle(cards)
    return cards


def rank(card):
    """2-14 (ace high)."""
    return RANKS.index(card[0]) + 2


def _plural(v):
    return PLURAL.get(v, RANK_NAMES[v] + "s")


def rank5(cards):
    """A comparable score for exactly five cards: (category 0-8, tie-breakers...). Higher is better."""
    vals = sorted((rank(c) for c in cards), reverse=True)
    flush = len({c[1] for c in cards}) == 1
    uniq = sorted(set(vals), reverse=True)
    straight = 0
    if len(uniq) == 5:
        if uniq[0] - uniq[4] == 4:
            straight = uniq[0]
        elif uniq == [14, 5, 4, 3, 2]:  # the wheel: the ace plays low
            straight = 5
    groups = sorted(((vals.count(v), v) for v in uniq), reverse=True)
    shape = [n for n, _ in groups]
    order = [v for _, v in groups]
    if straight and flush:
        return (8, straight)
    if shape == [4, 1]:
        return (7, *order)
    if shape == [3, 2]:
        return (6, *order)
    if flush:
        return (5, *vals)
    if straight:
        return (4, straight)
    if shape == [3, 1, 1]:
        return (3, *order)
    if shape == [2, 2, 1]:
        return (2, *order)
    if shape == [2, 1, 1, 1]:
        return (1, *order)
    return (0, *vals)


def best_hand(cards):
    """The best five of 5-7 cards: (score, the five cards)."""
    return max(((rank5(c), list(c)) for c in combinations(cards, 5)), key=lambda x: x[0])


def describe(score):
    """A score in words: "Pair of Kings", "Queens full of Fives", "Ace-high straight"."""
    cat, top = score[0], score[1]
    if cat == 8:
        return "Royal flush" if top == 14 else f"{RANK_NAMES[top]}-high straight flush"
    if cat == 7:
        return f"Four {_plural(top)}"
    if cat == 6:
        return f"{_plural(top)} full of {_plural(score[2])}"
    if cat == 5:
        return f"{RANK_NAMES[top]}-high flush"
    if cat == 4:
        return f"{RANK_NAMES[top]}-high straight"
    if cat == 3:
        return f"Three {_plural(top)}"
    if cat == 2:
        return f"Two pair, {_plural(top)} and {_plural(score[2])}"
    if cat == 1:
        return f"Pair of {_plural(top)}"
    return f"{RANK_NAMES[top]} high"
