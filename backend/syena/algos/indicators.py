"""Indicator library (pure pandas; formulas match TA-Lib / pandas-ta conventions, Wilder smoothing where standard).

Every function takes a candle DataFrame with columns open, high, low, close, volume and a tz-aware index.
"""
import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wma(s: pd.Series, n: int) -> pd.Series:
    w = np.arange(1, n + 1, dtype=float)
    return s.rolling(n, min_periods=n).apply(lambda x: float(np.dot(x, w) / w.sum()), raw=True)


def _wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up, down = d.clip(lower=0), -d.clip(upper=0)
    rs = _wilder(up, n) / _wilder(down, n)
    out = 100 - 100 / (1 + rs)
    return out.where(_wilder(down, n) != 0, 100.0)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    line = line.where(close.expanding().count() >= slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bbands(close: pd.Series, n: int = 20, std: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return pd.DataFrame({"upper": mid + std * sd, "middle": mid, "lower": mid - std * sd})


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return _wilder(true_range(df), n)


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session VWAP, reset each IST trading day (for daily candles this equals the typical price)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.tz_convert("Asia/Kolkata").date
    pv = (tp * df["volume"]).groupby(day).cumsum()
    vol = df["volume"].groupby(day).cumsum()
    return (pv / vol.replace(0, np.nan)).fillna(tp)


def stoch(df: pd.DataFrame, k: int = 14, d: int = 3, smooth: int = 3) -> pd.DataFrame:
    ll = df["low"].rolling(k, min_periods=k).min()
    hh = df["high"].rolling(k, min_periods=k).max()
    raw = 100 * (df["close"] - ll) / (hh - ll).replace(0, np.nan)
    kline = raw.rolling(smooth, min_periods=smooth).mean()
    return pd.DataFrame({"k": kline, "d": kline.rolling(d, min_periods=d).mean()})


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = _wilder(true_range(df), n)
    plus_di = 100 * _wilder(plus_dm, n) / tr
    minus_di = 100 * _wilder(minus_dm, n) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return _wilder(dx, n)


def supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0) -> pd.DataFrame:
    a = atr(df, n)
    hl2 = (df["high"] + df["low"]) / 2
    upper_basic, lower_basic = hl2 + mult * a, hl2 - mult * a
    close = df["close"].to_numpy()
    ub, lb = upper_basic.to_numpy().copy(), lower_basic.to_numpy().copy()
    line = np.full(len(df), np.nan)
    direction = np.zeros(len(df))
    for i in range(len(df)):
        if np.isnan(ub[i]):
            continue
        if i > 0 and not np.isnan(ub[i - 1]):
            ub[i] = ub[i] if (ub[i] < ub[i - 1] or close[i - 1] > ub[i - 1]) else ub[i - 1]
            lb[i] = lb[i] if (lb[i] > lb[i - 1] or close[i - 1] < lb[i - 1]) else lb[i - 1]
            prev_dir = direction[i - 1] or 1
            if prev_dir == 1 and close[i] < lb[i]:
                direction[i] = -1
            elif prev_dir == -1 and close[i] > ub[i]:
                direction[i] = 1
            else:
                direction[i] = prev_dir
        else:
            direction[i] = 1
        line[i] = lb[i] if direction[i] == 1 else ub[i]
    return pd.DataFrame({"line": line, "direction": direction}, index=df.index).replace({"direction": {0: np.nan}})


def volume_sma(df: pd.DataFrame, n: int = 20) -> pd.Series:
    return sma(df["volume"], n)


# ---------------------------------------------------------------- swing range and Fibonacci
# The range uses the PREVIOUS n bars (not the current one), so "close crosses above the 20-bar high"
# can actually happen and nothing looks ahead.
def highest(df: pd.DataFrame, n: int) -> pd.Series:
    """Highest high of the previous n bars (swing high)."""
    return df["high"].rolling(n, min_periods=n).max().shift(1)


def lowest(df: pd.DataFrame, n: int) -> pd.Series:
    """Lowest low of the previous n bars (swing low)."""
    return df["low"].rolling(n, min_periods=n).min().shift(1)


def fib(df: pd.DataFrame, n: int, level: float) -> pd.Series:
    """Fibonacci retracement of the previous-n-bar range, measured down from the swing high:
    level 0 = swing high, 61.8 = high - 0.618 x range, 100 = swing low."""
    hi, lo = highest(df, n), lowest(df, n)
    return hi - (hi - lo) * level / 100.0


def fib_ext(df: pd.DataFrame, n: int, level: float) -> pd.Series:
    """Fibonacci extension above the swing high: level 127.2 = low + 1.272 x range (= high + 0.272 x range)."""
    hi, lo = highest(df, n), lowest(df, n)
    return lo + (hi - lo) * level / 100.0
