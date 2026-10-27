"""Zerodha NSE equity charges (as published for 2026). Verify against zerodha.com/charges periodically.

Intraday (MIS): brokerage min(₹20, 0.03%) per executed order; STT 0.025% on sell;
Delivery (CNC): brokerage ₹0; STT 0.1% on buy and sell; DP ₹13.5 + GST per scrip on sell day.
Both: NSE txn 0.00322%; SEBI ₹10/crore; stamp duty on buy (0.003% intraday, 0.015% delivery);
GST 18% on brokerage + exchange txn + SEBI fee.
"""
from dataclasses import asdict, dataclass

RATES = {
    "brokerage_pct_intraday": 0.0003,
    "brokerage_cap": 20.0,
    "stt_intraday_sell": 0.00025,
    "stt_delivery": 0.001,
    "exchange_txn": 0.0000322,
    "sebi": 0.000001,
    "stamp_intraday_buy": 0.00003,
    "stamp_delivery_buy": 0.00015,
    "gst": 0.18,
    "dp_charge": 13.5,
}


@dataclass
class ChargeBreakdown:
    brokerage: float
    stt: float
    exchange_txn: float
    sebi: float
    stamp: float
    gst: float
    dp: float

    @property
    def total(self) -> float:
        return round(self.brokerage + self.stt + self.exchange_txn + self.sebi + self.stamp + self.gst + self.dp, 2)

    def as_dict(self) -> dict:
        return {**asdict(self), "total": self.total}


def leg_charges(side: str, qty: int, price: float, intraday: bool) -> ChargeBreakdown:
    """Charges for one executed order leg."""
    r = RATES
    turnover = qty * price
    brokerage = min(r["brokerage_cap"], turnover * r["brokerage_pct_intraday"]) if intraday else 0.0
    if intraday:
        stt = turnover * r["stt_intraday_sell"] if side == "SELL" else 0.0
        stamp = turnover * r["stamp_intraday_buy"] if side == "BUY" else 0.0
        dp = 0.0
    else:
        stt = turnover * r["stt_delivery"]
        stamp = turnover * r["stamp_delivery_buy"] if side == "BUY" else 0.0
        dp = r["dp_charge"] * (1 + r["gst"]) if side == "SELL" else 0.0
    txn = turnover * r["exchange_txn"]
    sebi = turnover * r["sebi"]
    gst = (brokerage + txn + sebi) * r["gst"]
    return ChargeBreakdown(*(round(x, 4) for x in (brokerage, stt, txn, sebi, stamp, gst, dp)))


def round_trip(qty: int, buy: float, sell: float, intraday: bool) -> float:
    return round(leg_charges("BUY", qty, buy, intraday).total + leg_charges("SELL", qty, sell, intraday).total, 2)


# NSE options (premium turnover). Verify against zerodha.com/charges: STT on the sell side was raised for options in
# 2026; flat ₹20 brokerage per executed order.
OPTION_RATES = {
    "brokerage_flat": 20.0,
    "stt_sell": 0.0015,
    "exchange_txn": 0.0003553,
    "sebi": 0.000001,
    "stamp_buy": 0.00003,
    "gst": 0.18,
}


def option_leg_charges(side: str, qty: int, premium: float) -> ChargeBreakdown:
    """Charges for one executed option order (qty in units, i.e. lots × lot size; premium per unit)."""
    r = OPTION_RATES
    turnover = qty * premium
    brokerage = r["brokerage_flat"] if turnover > 0 else 0.0
    stt = turnover * r["stt_sell"] if side == "SELL" else 0.0
    stamp = turnover * r["stamp_buy"] if side == "BUY" else 0.0
    txn = turnover * r["exchange_txn"]
    sebi = turnover * r["sebi"]
    gst = (brokerage + txn + sebi) * r["gst"]
    return ChargeBreakdown(*(round(x, 4) for x in (brokerage, stt, txn, sebi, stamp, gst, 0.0)))
