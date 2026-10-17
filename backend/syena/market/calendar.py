"""NSE market hours (IST) and holidays. Holidays are editable in Settings."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
OPEN = time(9, 15)
CLOSE = time(15, 30)


def to_ist(dt: datetime) -> datetime:
    return dt.astimezone(IST)


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def is_trading_day(d: date, holidays: list[str]) -> bool:
    return d.weekday() < 5 and d.isoformat() not in set(holidays)


def is_market_open(now: datetime, holidays: list[str]) -> bool:
    n = to_ist(now)
    return is_trading_day(n.date(), holidays) and OPEN <= n.time() < CLOSE


def before(now: datetime, hhmm: str) -> bool:
    return to_ist(now).time() < parse_hhmm(hhmm)


def next_trading_day(d: date, holidays: list[str]) -> date:
    d = d + timedelta(days=1)
    while not is_trading_day(d, holidays):
        d += timedelta(days=1)
    return d


def add_trading_days(start: datetime, n: int, holidays: list[str]) -> datetime:
    d = to_ist(start).date()
    for _ in range(n):
        d = next_trading_day(d, holidays)
    return datetime.combine(d, CLOSE, IST)


def market_open_at(d: date) -> datetime:
    return datetime.combine(d, OPEN, IST)
