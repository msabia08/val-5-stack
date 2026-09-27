"""How a game ended: played out, ended early by a surrender (forfeit), or abandoned before it really began.

Stored-match data has no surrender flag, so the ending is read from the final score: a finished game has a
team at the mode's rounds-to-win target (13 in competitive, unrated and premier). A game that stopped short
of it was surrendered, unless it stopped within the first few rounds, which is a remake or an abandoned lobby
("no contest"): the tracker doesn't record those at all, so they never settle bets or count in stats.
"""
from statistics import median

ROUNDS_TO_WIN = {"swiftplay": 5, "spikerush": 4}
DEFAULT_ROUNDS_TO_WIN = 13
NO_CONTEST_MAX_ROUNDS = 4  # ended this early -> remake / abandoned, not a real game
DEFAULT_FULL_ROUNDS = 22.0  # typical competitive length until the squad has its own history
MIN_GAMES_FOR_MEDIAN = 5

COMPLETE, FORFEIT, NO_CONTEST = "complete", "forfeit", "no_contest"


def rounds_to_win(mode):
    return ROUNDS_TO_WIN.get((mode or "").lower(), DEFAULT_ROUNDS_TO_WIN)


def total_rounds(match):
    return (match.get("rounds_won") or 0) + (match.get("rounds_lost") or 0)


def ending(match):
    """COMPLETE, FORFEIT or NO_CONTEST for a match dict with mode / rounds_won / rounds_lost."""
    rw, rl = match.get("rounds_won"), match.get("rounds_lost")
    if rw is None or rl is None:
        return COMPLETE  # nothing to judge by; treat as a normal game
    if max(rw, rl) >= rounds_to_win(match.get("mode")):
        return COMPLETE
    return NO_CONTEST if rw + rl <= NO_CONTEST_MAX_ROUNDS else FORFEIT


def full_game_rounds(matches):
    """Median length of the squad's completed games in the default 13-round modes, or a sensible default."""
    lengths = [total_rounds(m) for m in matches
               if ending(m) == COMPLETE and rounds_to_win(m.get("mode")) == DEFAULT_ROUNDS_TO_WIN and total_rounds(m)]
    return float(median(lengths)) if len(lengths) >= MIN_GAMES_FOR_MEDIAN else DEFAULT_FULL_ROUNDS
