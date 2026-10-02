"""Onkey's Bank: credits on loan, with interest.

A bettor can borrow up to `loan_max` credits (1000 by default) at `loan_interest` (10%): borrow 500 and you owe
550. What they have out on loan (the open principal) counts against the limit, so once the full 1000 is borrowed
the bank lends nothing more until every open loan is paid back, interest included. Loans are paid back in whole
credits, any amount at a time, oldest loan first; a loan clears when its `owed` is fully repaid. Borrowed credits
land on the balance and can be bet like any other, and the leaderboard keeps them out of profit (`loan_totals().net`)
and shows the debt beside the balance. A season reset archives the loans and forgives the debts with the balances.

Everything money-related happens in `db.take_loan` / `db.repay_loan`, each one transaction.
"""
from .bets import BetError

LOAN_MAX = 1000
LOAN_INTEREST = 0.10
LOAN_MIN = 1  # the smallest loan or repayment, in whole credits


def _whole(amount, what):
    try:
        value = float(amount)
    except (TypeError, ValueError):
        raise BetError(f"Enter {what}.")
    if value != value or value < LOAN_MIN:  # NaN too
        raise BetError(f"The smallest {what} is {LOAN_MIN} credit{'s' if LOAN_MIN != 1 else ''}.")
    if abs(value - round(value)) > 1e-9:
        raise BetError("Whole credits only.")
    return int(round(value))


class BankManager:
    def __init__(self, cfg, db):
        self.db = db
        self.max = max(0, int(cfg.get("loan_max", LOAN_MAX)))
        self.interest = max(0.0, float(cfg.get("loan_interest", LOAN_INTEREST)))

    @property
    def enabled(self):
        return self.max > 0

    def terms(self):
        return {"max": self.max, "interest": self.interest, "min": LOAN_MIN, "enabled": self.enabled}

    def quote(self, amount):
        """What a loan of `amount` costs: the interest (rounded to a whole credit) and the total owed."""
        interest = int(round(amount * self.interest))
        return {"principal": amount, "interest": interest, "owed": amount + interest}

    def status(self, name):
        """One bettor's standing with the bank: open loans, what they owe, what they can still borrow, and recent
        cleared loans."""
        loans = self.db.loans(name, limit=30)
        open_loans = [dict(l, due=round(l["owed"] - l["repaid"], 2)) for l in loans if l["cleared_ts"] is None]
        borrowed = sum(l["principal"] for l in open_loans)
        debt = sum(l["due"] for l in open_loans)
        return {"name": name, "borrowed": round(borrowed, 2), "debt": round(debt, 2),
                "room": max(0, self.max - int(round(borrowed))),
                "open": open_loans, "cleared": [l for l in loans if l["cleared_ts"] is not None][:10]}

    def borrow(self, name, amount):
        """Lend the bettor `amount` whole credits, within what the limit leaves them. Returns the loan."""
        if not self.enabled:
            raise BetError("The bank isn't lending.")
        amount = _whole(amount, "a loan")
        bettor = self.db.get_bettor(name)
        if not bettor:
            raise BetError("Sign in as a bettor first.")
        st = self.status(bettor["name"])
        if st["room"] <= 0:
            raise BetError(f"You've borrowed the full {self.max} credits. Pay it back with interest before borrowing again.")
        if amount > st["room"]:
            raise BetError(f"The bank will lend you {st['room']} more credits ({self.max} at most, with {st['borrowed']:.0f} already out).")
        q = self.quote(amount)
        loan_id = self.db.take_loan(bettor["name"], float(q["principal"]), float(q["interest"]))
        return self.db.query_one("SELECT * FROM loans WHERE id=?", (loan_id,))

    def repay(self, name, amount=None):
        """Pay back `amount` whole credits of what the bettor owes (everything, if `amount` is None or more than the
        debt), oldest loan first. Returns what was paid and which loans cleared."""
        bettor = self.db.get_bettor(name)
        if not bettor:
            raise BetError("Sign in as a bettor first.")
        st = self.status(bettor["name"])
        if st["debt"] <= 0:
            raise BetError("You don't owe the bank anything.")
        amount = int(round(st["debt"])) if amount in (None, "", "all") else _whole(amount, "a repayment")
        amount = min(amount, int(round(st["debt"])))
        result = self.db.repay_loan(bettor["name"], float(amount))
        if result is None:
            raise BetError(f"You only have {self.db.get_bettor(bettor['name'])['balance']:.0f} credits; the bank takes whole credits you have.")
        return result
