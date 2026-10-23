"""Plain-English -> validated JSON rule.

1. A deterministic grammar handles the common forms (free, instant, testable).
2. Anything it cannot parse goes to the LLM (analysis tier), whose output is validated against
   the same schema. 3. Ambiguous or unsupported input returns status=clarify, never a silent guess.
Defaults that are industry-standard (RSI 14, MACD 12/26/9, Bollinger 20/2...) are applied but
listed in `notes` so you see them on the confirmation screen.
"""
import difflib
import re
from dataclasses import dataclass, field

from syena.algos.rules import RuleError, canon_rule, describe_rule

UNSUPPORTED = {
    "ichimoku": "Ichimoku", "pivot": "pivot points",
    "obv": "OBV", "cci": "CCI", "parabolic": "Parabolic SAR", "psar": "Parabolic SAR", "heikin": "Heikin-Ashi",
    "keltner": "Keltner channels", "donchian": "Donchian channels", "williams": "Williams %R", "mfi": "MFI",
    "news": "news conditions (use the News strategy)", "earnings": "earnings conditions",
    "options": "options data", "oi": "open interest", "pe": "fundamentals", "eps": "fundamentals",
}
VAGUE = ["strong", "weak", "significant", "breakout", "breaks out", "uptrend", "downtrend", "trend is up",
         "trend is down", "momentum", "oversold", "overbought", "spike", "surge", "dip", "good", "bad",
         "high volume", "low volume", "big", "huge", "sharp", "near", "around", "approximately", "roughly", "touches",
         "holds", "bounces", "respects", "support", "resistance", "zone"]
VOCAB = sorted({
    "sma", "ema", "wma", "rsi", "macd", "bollinger", "band", "bands", "atr", "vwap", "stochastic", "adx",
    "supertrend", "volume", "average", "crosses", "crossed", "cross", "above", "below", "under", "over", "greater",
    "less", "than", "price", "close", "closes", "closing", "open", "high", "low", "lower", "upper", "middle",
    "signal", "histogram", "bullish", "bearish", "crossover", "and", "or", "not", "buy", "sell", "when", "exit",
    "stop", "loss", "target", "moving", "exponential", "simple", "weighted", "period", "line", "with", "day",
    "between", "relative", "strength", "index", "true", "range", "golden", "death",
    "fib", "fibonacci", "retracement", "extension", "swing", "highest", "lowest", "candles", "candle", "bars", "last",
    "previous", "past", "reaches", "level",
    "candlestick", "pattern", "engulfing", "hammer", "inverted", "doji", "dragonfly", "gravestone", "harami", "marubozu",
    "soldiers", "crows", "tweezer", "tweezers", "piercing", "shooting", "star", "morning", "evening", "hanging", "spinning",
    "trendline", "trend", "inside", "outside", "gap", "forms", "appears", "reversal", "cloud", "cover", "dark", "three",
    "white", "black", "bottom", "top", "support", "resistance", "zone", "bounces", "bounce", "holds",
    "rejected", "breaks", "breakout", "breakdown", "tests", "touches", "near", "srs", "srr",
})

# phrase -> candlestick pattern name (longest phrases first so "inverted hammer" wins over "hammer")
PATTERN_PHRASES = [
    (r"three white soldiers", "three_white_soldiers"), (r"three black crows", "three_black_crows"),
    (r"inverted hammer", "inverted_hammer"), (r"dragonfly doji", "dragonfly_doji"), (r"gravestone doji", "gravestone_doji"),
    (r"bullish engulfing", "bullish_engulfing"), (r"bearish engulfing", "bearish_engulfing"),
    (r"bullish harami", "bullish_harami"), (r"bearish harami", "bearish_harami"),
    (r"bullish marubozu|green marubozu", "bullish_marubozu"), (r"bearish marubozu|red marubozu", "bearish_marubozu"),
    (r"piercing (?:line|pattern)", "piercing_line"), (r"dark cloud cover|dark cloud", "dark_cloud_cover"),
    (r"morning star", "morning_star"), (r"evening star", "evening_star"), (r"shooting star", "shooting_star"),
    (r"hanging man", "hanging_man"), (r"tweezers? bottoms?", "tweezer_bottom"), (r"tweezers? tops?", "tweezer_top"),
    (r"spinning top", "spinning_top"), (r"inside (?:bar|candle)", "inside_bar"), (r"outside (?:bar|candle)", "outside_bar"),
    (r"gap[- ]?up(?: opening)?", "gap_up"), (r"gap[- ]?down(?: opening)?", "gap_down"),
    (r"(?:any )?bullish (?:reversal )?(?:candlestick |candle )?pattern", "any_bullish"),
    (r"(?:any )?bearish (?:reversal )?(?:candlestick |candle )?pattern", "any_bearish"),
    (r"hammer", "hammer"), (r"doji", "doji"),
]


class Clarify(Exception):
    def __init__(self, question: str, reason: str = "ambiguous"):
        super().__init__(question)
        self.question, self.reason = question, reason


class Unparsed(Exception):
    pass


@dataclass
class ParseOutput:
    status: str  # ok | clarify
    rule: dict | None = None
    readable: str | None = None
    question: str | None = None
    reason: str | None = None
    notes: list[str] = field(default_factory=list)
    source: str = "deterministic"  # deterministic | llm
    prompt_version: str | None = None

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


NUM = r"\d+(?:\.\d+)?"


def _ind(name, **params):
    return {"kind": "indicator", "name": name, "params": params}


def _ind_out(name, output, **params):
    return {"kind": "indicator", "name": name, "params": params, "output": output}


# ---------------------------------------------------------------- normalisation
def _fix_typos(text: str, notes: list[str]) -> str:
    def fix(m):
        w = m.group(0)
        if len(w) < 4 or w in VOCAB or not w.isalpha():
            return w
        if w in UNSUPPORTED or any(w == v.split()[0] for v in VAGUE):
            return w
        c = difflib.get_close_matches(w, VOCAB, n=1, cutoff=0.8)
        if c:
            notes.append(f"read '{w}' as '{c[0]}'")
            return c[0]
        return w

    return re.sub(r"[a-z]+", fix, text)


LB = r"(?:candles?|bars?|days?|sessions?|periods?)"
_RANGE_TOK = r"(?:fib_\d+_[\d.]+|fibext_\d+_[\d.]+|hh_\d+|ll_\d+)"


def _range_pass(t: str, notes: list[str]) -> str:
    """Swing high/low and Fibonacci phrases -> tokens: hh_N, ll_N, fib_N_LEVEL, fibext_N_LEVEL."""
    t = re.sub(r"\bfibonacci\b", "fib", t)
    t = re.sub(r"\bfib\s+retracements?\b", "fib", t)
    # "of the last 50 candles" -> "@50" (attached to the swing/fib phrase before it)
    t = re.sub(r"\s*\b(?:of|over|in|from|for)\s+(?:the\s+)?(?:last|past|previous|prior)\s+(\d+)\s*-?\s*" + LB + r"\b", r" @\1", t)

    # "between fib 50% and fib 61.8% of the last 100 candles": the lookback applies to both levels
    t = re.sub(r"\bbetween\s+fib\s+(" + NUM + r")\s*%?\s+and\s+(?:fib\s+)?(" + NUM + r")\s*%?\s*@(\d+)",
               r"between fib \1 @\3 and fib \2 @\3", t)

    def look(n):
        if n:
            return int(n)
        notes.append("swing range defaulted to the last 50 bars")
        return 50

    t = re.sub(r"\b(?:the\s+)?(\d+)\s*-?\s*" + LB + r"\s+(?:swing high|highest high|range high|high)\b", r"hh_\1", t)
    t = re.sub(r"\b(?:the\s+)?(\d+)\s*-?\s*" + LB + r"\s+(?:swing low|lowest low|range low|low)\b", r"ll_\1", t)
    t = re.sub(r"\b(?:the\s+)?(?:swing high|highest high|recent high|range high)(?:\s*@(\d+))?",
               lambda m: f"hh_{look(m.group(1))}", t)
    t = re.sub(r"\b(?:the\s+)?(?:swing low|lowest low|recent low|range low)(?:\s*@(\d+))?",
               lambda m: f"ll_{look(m.group(1))}", t)

    def tok(level, n):
        lv = float(level)
        return f"{'fibext' if lv > 100 else 'fib'}_{look(n)}_{lv:g}"

    t = re.sub(r"\bfib\s*\(\s*(\d+)\s*,\s*(" + NUM + r")\s*\)", lambda m: tok(m.group(2), m.group(1)), t)
    t = re.sub(r"\bfib\s+ext(?:ension)?\s+(" + NUM + r")\s*%?(?:\s*@(\d+))?", lambda m: tok(m.group(1), m.group(2)), t)
    t = re.sub(r"\b(" + NUM + r")\s*%?\s+(?:fib\s+)?ext(?:ension)?(?:\s*@(\d+))?", lambda m: tok(m.group(1), m.group(2)), t)
    t = re.sub(r"\bfib(?:\s+level)?\s+(" + NUM + r")\s*%?(?:\s+(?:level|retracement))?(?:\s*@(\d+))?",
               lambda m: tok(m.group(1), m.group(2)), t)
    t = re.sub(r"\b(" + NUM + r")\s*%?\s+fib(?:\s+(?:level|retracement))?(?:\s*@(\d+))?", lambda m: tok(m.group(1), m.group(2)), t)

    def pos(token: str) -> tuple[int, float]:
        parts = token.split("_")
        if parts[0] == "hh":
            return int(parts[1]), 1.0
        if parts[0] == "ll":
            return int(parts[1]), 0.0
        lv = float(parts[2]) / 100
        return int(parts[1]), (lv if parts[0] == "fibext" else 1 - lv)

    def between(m):
        x, a, b = m.group(1), m.group(2), m.group(3)
        (na, pa), (nb, pb) = pos(a), pos(b)
        if na == nb:
            lo, hi = (a, b) if pa <= pb else (b, a)
            return f"( {x} >= {lo} and {x} <= {hi} )"
        return f"( ( {x} >= {a} and {x} <= {b} ) or ( {x} >= {b} and {x} <= {a} ) )"

    t = re.sub(r"([a-z_]+(?: \d+)?) (?:is |trades |stays )?(?:between|inside|within) (?:the )?(" + _RANGE_TOK +
               r") and (?:the )?(" + _RANGE_TOK + r")(?: zone)?", between, t)
    return t


def _pattern_pass(t: str, notes: list[str]) -> str:
    """Candlestick patterns -> pat_NAME tokens; trendlines -> tls_N (support) / tlr_N (resistance)."""
    t = re.sub(r"\btrend[\s-]+lines?\b", "trendline", t)

    def n_of(m, group=1):
        if m.group(group):
            return int(m.group(group))
        notes.append("trendline drawn through the last two swing points of the past 50 bars")
        return 50

    t = re.sub(r"\b(?:the )?(?:rising |ascending |falling |descending )?(?:trendline (support|resistance)|(support|resistance) trendline)(?:\s*@(\d+))?",
               lambda m: f"{'tls' if (m.group(1) or m.group(2)) == 'support' else 'tlr'}_{n_of(m, 3)}", t)
    # "breaks (out) above the trendline" = resistance breakout; "breaks below the trendline" = support breakdown
    t = re.sub(r"\b(?:breaks?|broke|crosses|crossed|moves?)(?: out)? (above|over|up through) (?:the )?trendline(?:\s*@(\d+))?",
               lambda m: f"crosses above tlr_{n_of(m, 2)}", t)
    t = re.sub(r"\b(?:breaks?|broke|crosses|crossed|moves?)(?: down)? (below|under|down through) (?:the )?trendline(?:\s*@(\d+))?",
               lambda m: f"crosses below tls_{n_of(m, 2)}", t)
    t = re.sub(r"\btrendline breakout(?:\s*@(\d+))?", lambda m: f"close crosses above tlr_{n_of(m)}", t)
    t = re.sub(r"\btrendline breakdown(?:\s*@(\d+))?", lambda m: f"close crosses below tls_{n_of(m)}", t)
    # horizontal support / resistance zones (whatever is left after the trendline phrases above)
    zone = r"(?:the |a |nearest |key |strong |major )*"
    subj = lambda m: {"price": "close", None: "close"}.get(m.group(1), m.group(1))  # noqa: E731
    t = re.sub(r"\b(?:(close|price|high|low) )?(?:breaks?|broke|crosses|crossed|moves?|closes?)(?: out)? (?:above|over|up through) " + zone
               + r"resistance(?: zone| level)?", lambda m: f"{subj(m)} crosses above srr", t)
    t = re.sub(r"\b(?:(close|price|high|low) )?(?:breaks?|broke|crosses|crossed|moves?|closes?)(?: down)? (?:below|under|down through) " + zone
               + r"support(?: zone| level)?", lambda m: f"{subj(m)} crosses below srs", t)
    t = re.sub(r"\bresistance breakout\b", "close crosses above srr", t)
    t = re.sub(r"\bsupport breakdown\b", "close crosses below srs", t)
    # touching a zone and holding it: low reaches support but the candle closes above; high reaches resistance, closes below
    t = re.sub(r"\b(?:price )?(?:bounces? (?:off|from)|holds?|tests?|is (?:near|at)|near|at|touches?|takes support (?:at|from)) " + zone + r"support(?: zone| level)?",
               " ( low <= srs and close > srs ) ", t)
    t = re.sub(r"\b(?:price )?(?:gets? rejected (?:at|from)|is rejected (?:at|from)|tests?|is (?:near|at)|near|at|touches?) " + zone + r"resistance(?: zone| level)?",
               " ( high >= srr and close < srr ) ", t)
    t = re.sub(r"\b" + zone + r"support (?:zone|level)\b", "srs", t)
    t = re.sub(r"\b" + zone + r"resistance (?:zone|level)\b", "srr", t)
    t = re.sub(r"(?<![a-z_])(?<!fib )(?<!fibonacci )(?:the )?support(?![a-z_])", "srs", t)  # "fib support" stays a Fib question
    t = re.sub(r"(?<![a-z_])(?<!fib )(?<!fibonacci )(?:the )?resistance(?![a-z_])", "srr", t)
    for phrase, name in PATTERN_PHRASES:
        t = re.sub(r"\b(?:a |an |the )?" + phrase + r"(?: candlestick| candle)?(?: pattern)?\b", f" pat_{name} ", t)
    return t


def normalise(text: str, notes: list[str]) -> str:
    t = " " + text.lower().strip() + " "
    t = t.replace("’", "'").replace("–", "-").replace("—", "-")
    t = re.sub(r"(\d),(\d{3})", r"\1\2", t)  # 1,500 -> 1500
    t = _fix_typos(t, notes)
    t = _range_pass(t, notes)
    t = _pattern_pass(t, notes)
    subs = [
        (r"exponential moving average", "ema"), (r"simple moving average", "sma"), (r"weighted moving average", "wma"),
        (r"relative strength index", "rsi"), (r"average true range", "atr"), (r"bollinger bands?", "bb"),
        (r"stochastic oscillator|stochastics?", "stoch"), (r"super ?trend", "supertrend"),
        (r"\b(sma|ema|wma|rsi|atr|adx)\s*\(\s*(\d+)\s*\)", r"\1 \2"),
        (r"\b(sma|ema|wma|rsi|atr|adx)[-_]?(\d+)\b", r"\1 \2"),
        (r"\bmacd\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", r"macd_\1_\2_\3"),
        (r"\bbb\s*\(\s*(\d+)\s*,\s*(" + NUM + r")\s*\)", r"bb_\1_\2"),
        (r"\bsupertrend\s*\(\s*(\d+)\s*,\s*(" + NUM + r")\s*\)", r"supertrend_\1_\2"),
        (r"\bstoch\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", r"stoch_\1_\2_\3"),
        (r"\b(\d+)\s*-\s*(day|bar|period|candle)\b", r"\1 \2"),
        (r"%k\b", "stoch k"), (r"%d\b", "stoch d"), (r"\bstoch (?:stoch )+", "stoch "),
        (r"\bwen\b|\bwhn\b|\bwhen\s+when\b", "when"), (r"\babv\b", "above"), (r"\bblw\b", "below"),
        (r"\bis not above\b|\bnot above\b", "<="), (r"\bis not below\b|\bnot below\b", ">="),
        (r"₹\s*|\brs\.?\s*(?=\d)|\binr\s*", ""),
        (r"([a-z_]+(?: \d+)?) (?:is )?between (" + NUM + r") and (" + NUM + r")", r"( \1 > \2 and \1 < \3 )"),
        (r"\s+&&?\s+", " and "), (r"\s+\|\|\s+", " or "), (r"\bwhile\b|\bwith\b|\bplus\b", " and "),
    ]
    for pat, rep in subs:
        t = re.sub(pat, rep, t)
    t = re.sub(r"\bmoving average\b", "ma", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


# ---------------------------------------------------------------- risk extraction
def _extract_risk(t: str) -> tuple[str, dict | None, dict | None]:
    stop = target = None
    stop_kw = r"(?:stop[\s-]*loss|stoploss|\bsl\b|\bstop\b)"
    tgt_kw = r"(?:take[\s-]*profit|profit target|\btarget\b|\btp\b)"
    for kw, which in ((stop_kw, "stop"), (tgt_kw, "target")):
        m = re.search(kw + r"\s*(?:of|at|:|=|is)?\s*(" + NUM + r")\s*%", t)
        val = None
        if m:
            val = {"type": "pct", "value": float(m.group(1))}
        else:
            m = re.search(kw + r"\s*(?:of|at|:|=|is)?\s*(" + NUM + r")\s*(?:x|times|×)?\s*atr", t) or \
                re.search(r"(" + NUM + r")\s*(?:x|times|×)?\s*atr\s*" + kw, t)
            if m:
                val = {"type": "atr", "mult": float(m.group(1))}
        if m:
            t = (t[: m.start()] + " " + t[m.end():]).strip()
            if which == "stop":
                stop = val
            else:
                target = val
    t = re.sub(r"(\s*[,;]\s*)+", ", ", t)
    t = re.sub(r",\s*(and|then)\s*,", ",", t)
    t = re.sub(r"\s+(and|then|with)\s*$", "", t.strip())
    t = re.sub(r"[,;.]\s*(and\s*)?$", "", t.strip())
    t = re.sub(r"\s*[,;]\s*(and\s*)?[,;]?\s*$", "", t)
    return t, stop, target


# ---------------------------------------------------------------- operands
def _operand(s: str, notes: list[str], other: dict | None = None) -> dict:
    s = re.sub(r"^(the|its|a|an)\s+", "", s.strip()).strip(" .,;%")
    s = re.sub(r"\s+(line|value|level)$", "", s) if not s.startswith("macd") and s != "signal line" else s
    if re.fullmatch(r"-?" + NUM, s):
        return {"kind": "value", "value": float(s)}
    if s in ("price", "close", "closing price", "ltp", "current price", "last price", "the close", "close price", "stock price", "it"):
        return {"kind": "price", "field": "close"}
    if s in ("open", "opening price", "open price"):
        return {"kind": "price", "field": "open"}
    if s in ("high", "day high", "high price"):
        return {"kind": "price", "field": "high"}
    if s in ("low", "day low", "low price"):
        return {"kind": "price", "field": "low"}
    if s in ("volume", "today's volume", "current volume", "vol"):
        return {"kind": "price", "field": "volume"}
    m = re.fullmatch(r"(sma|ema|wma|ma) (\d+)(?: (?:day|bar|period|candle))?", s) or \
        re.fullmatch(r"(\d+) (?:(?:day|bar|period|candle) )?(sma|ema|wma|ma)", s)
    if m:
        a, b = m.groups()
        kind, n = (a, b) if not a.isdigit() else (b, a)
        if kind == "ma":
            notes.append(f"'moving average {n}' interpreted as SMA({n})")
            kind = "sma"
        return _ind(kind, n=int(n))
    if re.fullmatch(r"(sma|ema|wma|ma)", s):
        raise Clarify(f"Which period for the {s.upper()}? e.g. '{s.upper()} 20'.")
    m = re.fullmatch(r"(rsi|atr|adx)(?: (\d+))?|(\d+) (?:(?:day|bar|period) )?(rsi|atr|adx)", s)
    if m:
        name = m.group(1) or m.group(4)
        n = m.group(2) or m.group(3)
        if not n:
            notes.append(f"{name.upper()} period defaulted to 14")
        return _ind(name, n=int(n or 14))
    if s == "vwap":
        return _ind("vwap")
    m = re.fullmatch(r"(hh|ll)_(\d+)", s)
    if m:
        return _ind("highest" if m.group(1) == "hh" else "lowest", n=int(m.group(2)))
    m = re.fullmatch(r"(tls|tlr)_(\d+)", s)
    if m:
        return _ind("trendline_support" if m.group(1) == "tls" else "trendline_resistance", n=int(m.group(2)))
    if s == "srs":
        return _ind("support_zone")
    if s == "srr":
        return _ind("resistance_zone")
    if s in ("trendline", "trendline support", "trendline resistance"):
        raise Clarify("Which trendline: support (through recent swing lows) or resistance (through recent swing highs)?")
    m = re.fullmatch(r"(fib|fibext)_(\d+)_(" + NUM + r")", s)
    if m:
        return _ind("fib" if m.group(1) == "fib" else "fib_ext", n=int(m.group(2)), level=float(m.group(3)))
    if re.fullmatch(r"fib(?: ext(?:ension)?)?", s):
        raise Clarify("Which Fibonacci level? e.g. 'fib 61.8% of the last 50 candles' or 'fib extension 161.8'.")
    m = re.fullmatch(r"macd_(\d+)_(\d+)_(\d+)(?: (line|signal|signal line|histogram|hist))?", s)
    if m:
        out = {"line": "macd", None: "macd", "signal": "signal", "signal line": "signal", "histogram": "hist", "hist": "hist"}[m.group(4)]
        return _ind_out("macd", out, fast=int(m.group(1)), slow=int(m.group(2)), signal=int(m.group(3)))
    macd_map = {"macd": "macd", "macd line": "macd", "macd signal": "signal", "macd signal line": "signal",
                "signal line": "signal", "signal": "signal", "macd histogram": "hist", "macd hist": "hist", "histogram": "hist"}
    if s in macd_map:
        notes.append("MACD parameters defaulted to 12/26/9")
        return _ind_out("macd", macd_map[s], fast=12, slow=26, signal=9)
    m = re.fullmatch(r"(?:(lower|upper|middle) (?:bb|band|bollinger band|bollinger)|bb (lower|upper|middle)(?: band)?|(lower|upper|middle) band)(?: (\d+))?", s)
    if m:
        out = m.group(1) or m.group(2) or m.group(3)
        n = int(m.group(4) or 20)
        notes.append(f"Bollinger Bands({n}, 2)" + (" (defaults)" if not m.group(4) else ""))
        return _ind_out("bbands", out, n=n, std=2.0)
    m = re.fullmatch(r"(?:(lower|upper|middle) )?bb_(\d+)_(" + NUM + r")(?: (lower|upper|middle))?(?: band)?", s)
    if m:
        out = m.group(1) or m.group(4)
        if not out:
            raise Clarify("Which Bollinger band: upper, middle or lower?")
        return _ind_out("bbands", out, n=int(m.group(2)), std=float(m.group(3)))
    if s in ("bb", "bollinger band", "band"):
        raise Clarify("Which Bollinger band: upper, middle or lower?")
    m = re.fullmatch(r"stoch(?: (k|d))?|stoch_(\d+)_(\d+)_(\d+)(?: (k|d))?", s)
    if m:
        if m.group(2):
            return _ind_out("stoch", m.group(5) or "k", k=int(m.group(2)), d=int(m.group(3)), smooth=int(m.group(4)))
        notes.append("Stochastic defaulted to (14,3,3)")
        return _ind_out("stoch", m.group(1) or "k", k=14, d=3, smooth=3)
    m = re.fullmatch(r"supertrend(?:_(\d+)_(" + NUM + r"))?(?: line)?", s)
    if m:
        if not m.group(1):
            notes.append("Supertrend defaulted to (10, 3)")
        return _ind_out("supertrend", "line", n=int(m.group(1) or 10), mult=float(m.group(2) or 3))
    m = re.fullmatch(r"(?:(\d+) (?:day |bar |period |candle )?)?(?:average|avg|mean) volume(?: (\d+))?|volume (?:sma|average|avg|ma)(?: (\d+))?|(\d+) (?:day |bar |period )?volume (?:average|avg|sma|ma)", s)
    if m:
        n = next((g for g in m.groups() if g), None)
        if not n:
            notes.append("average volume period defaulted to 20 bars")
        return _ind("volume_sma", n=int(n or 20))
    m = re.fullmatch(r"(\d+) (?:day |bar |period |candle )?(?:average|avg|ma|mean)", s)
    if m:
        n = int(m.group(1))
        if other and other.get("kind") == "price" and other.get("field") == "volume":
            return _ind("volume_sma", n=n)
        notes.append(f"'{n}-period average' interpreted as SMA({n}) of close")
        return _ind("sma", n=n)
    if re.fullmatch(r"(average|avg)", s) and other and other.get("field") == "volume":
        notes.append("average volume period defaulted to 20 bars")
        return _ind("volume_sma", n=20)
    raise Unparsed(f"could not understand '{s}'")


# ---------------------------------------------------------------- clauses
CROSS_UP = r"(?:crosses|crossed|cross|crossing|breaks|broke|moves|goes) (?:above|over|up through|up)"
CROSS_DN = r"(?:crosses|crossed|cross|crossing|breaks|broke|moves|goes) (?:below|under|down through|down)"
CMP_PATTERNS = [
    (r"(?:is )?greater than or equal to|>=|(?:is )?at least|(?:reaches|reached|hits|hit)", ">="),
    (r"(?:is )?less than or equal to|<=|(?:is )?at most", "<="),
    (r"(?:closes|closed|close|trades|is trading|stays|remains|is) (?:above|over|higher than)|(?:is )?greater than|(?:is )?higher than|(?:is )?above|(?:is )?over|exceeds|>", ">"),
    (r"(?:closes|closed|close|trades|is trading|stays|remains|is) (?:below|under|lower than)|(?:is )?less than|(?:is )?lower than|(?:is )?below|(?:is )?under|<", "<"),
]


def _clause(c: str, notes: list[str], ctx: dict) -> dict:
    c = c.strip(" ,.;")
    if not c:
        raise Unparsed("empty condition")
    m = re.fullmatch(r"(?:(?:there is|there's|we see|we get|you see|on|with|the|last|previous|today'?s?|daily|candle|candle is|"
                     r"forms?|formed|is|a|an)\s+)*pat_([a-z_]+)(?:\s+(?:candle|candlestick|pattern|forms?|formed|appears|is formed|"
                     r"prints?|printed|occurs|shows up|on the chart|today))*", c)
    if m:
        if m.group(1) in ("doji", "spinning_top", "inside_bar", "outside_bar"):
            notes.append(f"'{m.group(1).replace('_', ' ')}' is an indecision candle: consider pairing it with a trend condition")
        return {"op": "pattern", "name": m.group(1)}
    if re.search(r"\b(equals?|==|=)\b|(?<![<>])=(?!=)", c):
        raise Clarify("Exact equality on prices/indicators almost never triggers. Use above/below or a crossover.")
    m = re.fullmatch(r"macd(?:_(\d+)_(\d+)_(\d+))?(?: line)? (?:(bullish|bearish) (?:crossover|cross)|crossover|crosses (above|below) (?:the )?signal(?: line)?)", c)
    if m:
        p = dict(fast=int(m.group(1) or 12), slow=int(m.group(2) or 26), signal=int(m.group(3) or 9))
        if not m.group(1):
            notes.append("MACD parameters defaulted to 12/26/9")
        direction = m.group(4) or {"above": "bullish", "below": "bearish"}.get(m.group(5) or "")
        if not direction:
            raise Clarify("Bullish (MACD crosses above signal) or bearish (crosses below) crossover?")
        op = "cross_above" if direction == "bullish" else "cross_below"
        return {"op": op, "left": _ind_out("macd", "macd", **p), "right": _ind_out("macd", "signal", **p)}
    m = re.fullmatch(r"(?:a )?(golden|death) cross", c)
    if m:
        notes.append(f"{m.group(1)} cross = SMA(50) crossing {'above' if m.group(1) == 'golden' else 'below'} SMA(200)")
        return {"op": "cross_above" if m.group(1) == "golden" else "cross_below", "left": _ind("sma", n=50), "right": _ind("sma", n=200)}
    m = re.fullmatch(r"supertrend(?:_(\d+)_(" + NUM + r"))? (?:is |turns |flips |gives a |gives )?(bullish|green|buy|up|bearish|red|sell|down)(?: signal)?", c)
    if m:
        n, mult = int(m.group(1) or 10), float(m.group(2) or 3)
        if not m.group(1):
            notes.append("Supertrend defaulted to (10, 3)")
        up = m.group(3) in ("bullish", "green", "buy", "up")
        st = _ind_out("supertrend", "direction", n=n, mult=mult)
        if re.search(r"\b(turns|flips|gives)\b", c):
            return {"op": "cross_above" if up else "cross_below", "left": st, "right": {"kind": "value", "value": 0.0}}
        return {"op": "compare", "left": st, "cmp": ">" if up else "<", "right": {"kind": "value", "value": 0.0}}
    m = re.fullmatch(r"(.+?) (?:is )?between (" + NUM + r") and (" + NUM + r")", c)
    if m:
        left = _operand(m.group(1), notes)
        return {"op": "and", "args": [
            {"op": "compare", "left": left, "cmp": ">", "right": {"kind": "value", "value": float(m.group(2))}},
            {"op": "compare", "left": left, "cmp": "<", "right": {"kind": "value", "value": float(m.group(3))}}]}
    for pat, op in ((CROSS_UP, "cross_above"), (CROSS_DN, "cross_below")):
        m = re.fullmatch(r"(.*?)\s*\b" + pat + r"\b\s*(.+)", c)
        if m:
            left_s = m.group(1).strip()
            left = _operand(left_s, notes) if left_s else ctx.get("left") or {"kind": "price", "field": "close"}
            right = _operand(m.group(2), notes, left)
            ctx["left"] = left
            return {"op": op, "left": left, "right": right}
    for pat, cmp in CMP_PATTERNS:
        m = re.fullmatch(r"(.*?)\s*(?:\b|(?=[<>]))(?:" + pat + r")\s*(.+)", c)
        if m:
            left_s, right_s = m.group(1).strip(), m.group(2).strip()
            if left_s:
                left = _operand(left_s, notes)
            elif re.match(r"(closes|closed|close)", c):
                left = {"kind": "price", "field": "close"}
            elif ctx.get("left"):
                left = ctx["left"]
            else:
                left = {"kind": "price", "field": "close"}
                notes.append("no subject given: compared the closing price")
            right = _operand(right_s, notes, left)
            if left.get("field") == "volume" and right["kind"] == "indicator" and right["name"] == "sma":
                right = _ind("volume_sma", n=right["params"]["n"])
            ctx["left"] = left
            return {"op": "compare", "left": left, "cmp": cmp, "right": right}
    if re.search(r"\bcross(es|ed|over)?\b", c):
        raise Clarify("Crosses above or below? Say e.g. 'SMA 20 crosses above SMA 50'.")
    raise Unparsed(f"could not understand condition '{c}'")


def _tokens(t: str) -> list[str]:
    parts = re.split(r"(\(|\)|\band\b|\bor\b|\bnot\b)", t)
    return [p.strip() for p in parts if p and p.strip()]


def _parse_bool(t: str, notes: list[str]) -> dict:
    toks, pos, ctx = _tokens(t), [0], {}

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None

    def take():
        pos[0] += 1
        return toks[pos[0] - 1]

    def expr():
        args = [term()]
        while peek() == "or":
            take()
            args.append(term())
        return args[0] if len(args) == 1 else {"op": "or", "args": args}

    def term():
        args = [factor()]
        while peek() == "and":
            take()
            args.append(factor())
        return args[0] if len(args) == 1 else {"op": "and", "args": args}

    def factor():
        tok = peek()
        if tok is None:
            raise Unparsed("condition ends unexpectedly")
        if tok == "not":
            take()
            return {"op": "not", "arg": factor()}
        if tok == "(":
            take()
            node = expr()
            if take() != ")":
                raise Unparsed("unbalanced parentheses")
            return node
        if tok in ("and", "or", ")"):
            raise Unparsed(f"unexpected '{tok}'")
        return _clause(take(), notes, ctx)

    node = expr()
    if pos[0] != len(toks):
        raise Unparsed("trailing text")
    return node


# ---------------------------------------------------------------- top level
def parse_deterministic(text: str) -> ParseOutput:
    notes: list[str] = []
    if not text or not text.strip():
        return ParseOutput("clarify", question="Describe when to buy, e.g. 'buy when SMA 20 crosses above SMA 50'.", reason="ambiguous")
    try:
        t = normalise(text, notes)
        words = set(re.findall(r"[a-z]+", t))
        for w, label in UNSUPPORTED.items():
            if w in words:
                raise Clarify(f"{label} is not supported yet. Supported: SMA, EMA, WMA, RSI, MACD, Bollinger, ATR, VWAP, "
                              "Stochastic, ADX, Supertrend, swing high/low, Fibonacci, trendlines, candlestick patterns, "
                              "volume, OHLC.", "unsupported")
        if re.match(r"^(short|sell short|go short|short sell)\b", t) or re.search(r"\bshort (when|if|on)\b", t):
            raise Clarify("Short selling isn't supported; rules are long-only (BUY then SELL).", "unsupported")
        for v in VAGUE:
            if re.search(r"\b" + re.escape(v) + r"\b", t):
                raise Clarify(f"'{v}' is vague. Give a measurable condition (e.g. 'RSI 14 < 30', 'volume above 20-day average').")
        t, stop, target = _extract_risk(t)
        exit_part = None
        m = re.search(r"[,;.]?\s*(?:and\s+|then\s+)?\b(?:sell|exit|close(?: the)? position|square off)\s+(?:when|if|on|once)\s+", t)
        if m:
            exit_part, t = t[m.end():].strip(" ,.;"), t[: m.start()].strip(" ,.;")
        t = re.sub(r"^(?:buy|go long|enter(?: long)?|long|entry|enter a trade)\s*(?:when|if|on|once|:)?\s*", "", t).strip(" ,.;:")
        t = re.sub(r"\s*,\s*", " and ", t)
        if not t:
            raise Clarify("What is the entry condition?")
        rule = {"entry": _parse_bool(t, notes), "exit": _parse_bool(re.sub(r"\s*,\s*", " and ", exit_part), notes) if exit_part else None,
                "side": "BUY", "stop_loss": stop, "target": target}
        rule = canon_rule(rule)
        if not stop or not target:
            notes.append("stop-loss/target not given: ATR-based defaults for the chosen style will be used")
        return ParseOutput("ok", rule=rule, readable=describe_rule(rule), notes=list(dict.fromkeys(notes)))
    except Clarify as c:
        return ParseOutput("clarify", question=c.question, reason=c.reason, notes=notes)
    except RuleError as e:
        return ParseOutput("clarify", question=str(e), reason="unsupported", notes=notes)
    except Unparsed as e:
        return ParseOutput("unparsed", question=str(e), notes=notes)


def parse(text: str, db=None, use_llm: bool = True) -> ParseOutput:
    det = parse_deterministic(text)
    if det.status != "unparsed":
        return det
    if not use_llm or db is None:
        return ParseOutput("clarify", question=f"I couldn't parse that: {det.question}. Try rephrasing with explicit "
                           "indicators and numbers.", reason="ambiguous", notes=det.notes)
    from syena.ai import llm

    if not llm.available():
        return ParseOutput("clarify", question=f"I couldn't parse that ({det.question}) and the AI parser is not configured.",
                           reason="ambiguous", notes=det.notes)
    res = llm.call(db, "rule_parse", "rule_parser", tier="analysis", variables={"text": text[:2000]}, max_tokens=1500)
    data = res.data or {}
    if data.get("status") == "clarify":
        return ParseOutput("clarify", question=data.get("question") or "Please clarify the rule.",
                           reason=data.get("reason", "ambiguous"), source="llm", prompt_version=res.prompt_version)
    try:
        rule = canon_rule(data)
    except (RuleError, KeyError, TypeError) as e:
        return ParseOutput("clarify", question=f"The AI produced an invalid rule ({e}). Please rephrase.",
                           reason="ambiguous", source="llm", prompt_version=res.prompt_version)
    notes = det.notes + ([data["notes"]] if data.get("notes") else []) + ["parsed by AI: please check carefully"]
    return ParseOutput("ok", rule=rule, readable=describe_rule(rule), notes=notes, source="llm", prompt_version=res.prompt_version)
