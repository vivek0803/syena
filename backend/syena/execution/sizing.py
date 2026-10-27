"""Position sizing (hard constraint, spec section 4).

Every position uses between min_amount and max_amount (default ₹7,000-10,000).
qty = floor(max_amount / price). Rejected if one share costs more than max_amount,
or if no whole quantity lands inside the band.
"""
import math
from dataclasses import dataclass

from syena.execution.checks import Check


@dataclass
class SizingResult:
    ok: bool
    qty: int
    amount: float
    reason: str

    def as_check(self) -> Check:
        return Check("sizing", self.ok, self.reason, {"qty": self.qty, "amount": round(self.amount, 2)})


def size_position(price: float, min_amount: float, max_amount: float) -> SizingResult:
    if not (price and price > 0 and math.isfinite(price)):
        return SizingResult(False, 0, 0.0, f"invalid price {price!r}")
    if price > max_amount:
        return SizingResult(False, 0, 0.0, f"one share costs ₹{price:,.2f}, above the ₹{max_amount:,.0f} max per position")
    qty = math.floor(max_amount / price)
    amount = qty * price
    if amount < min_amount:
        return SizingResult(
            False, 0, 0.0,
            f"no whole quantity fits ₹{min_amount:,.0f}-₹{max_amount:,.0f} at ₹{price:,.2f} "
            f"({qty} shares = ₹{amount:,.2f})",
        )
    return SizingResult(True, qty, amount, f"{qty} × ₹{price:,.2f} = ₹{amount:,.2f} (band ₹{min_amount:,.0f}-₹{max_amount:,.0f})")


def size_option(premium: float, lot_size: int, min_amount: float, max_amount: float, max_lots: int,
                lots: int | None = None) -> SizingResult:
    """Options trade in whole lots: as many lots as fit the premium budget (at most max_lots), or a fixed number."""
    if not (premium and premium > 0 and math.isfinite(premium)) or lot_size < 1:
        return SizingResult(False, 0, 0.0, f"invalid premium {premium!r}")
    per_lot = premium * lot_size
    n = lots if lots else min(max_lots, math.floor(max_amount / per_lot))
    amount = n * per_lot
    if n < 1:
        return SizingResult(False, 0, 0.0, f"one lot ({lot_size} × ₹{premium:,.2f} = ₹{per_lot:,.0f}) is above the ₹{max_amount:,.0f} options budget")
    if amount > max_amount:
        return SizingResult(False, 0, 0.0, f"{n} lots cost ₹{amount:,.0f}, above the ₹{max_amount:,.0f} options budget")
    if amount < min_amount:
        return SizingResult(False, 0, 0.0, f"{n} lot{'s' if n > 1 else ''} = ₹{amount:,.0f}, below the ₹{min_amount:,.0f} minimum (premium too small)")
    return SizingResult(True, n * lot_size, amount, f"{n} lot{'s' if n > 1 else ''} × {lot_size} × ₹{premium:,.2f} = ₹{amount:,.0f} (budget ₹{min_amount:,.0f}-₹{max_amount:,.0f})")
