"""Rule schema (validated JSON), evaluation over candles, and human-readable rendering."""
from typing import Any

import pandas as pd

from syena.algos import indicators as ind
from syena.algos import levels as lv
from syena.algos import patterns as pat
from syena.algos import zonetrade as zt

# name -> (param defaults in canonical order, outputs or None for single series, warmup multiplier key)
INDICATORS: dict[str, dict[str, Any]] = {
    "sma": {"params": {"n": None}, "outputs": None},
    "ema": {"params": {"n": None}, "outputs": None},
    "wma": {"params": {"n": None}, "outputs": None},
    "rsi": {"params": {"n": 14}, "outputs": None},
    "macd": {"params": {"fast": 12, "slow": 26, "signal": 9}, "outputs": ["macd", "signal", "hist"]},
    "bbands": {"params": {"n": 20, "std": 2.0}, "outputs": ["upper", "middle", "lower"]},
    "atr": {"params": {"n": 14}, "outputs": None},
    "vwap": {"params": {}, "outputs": None},
    "stoch": {"params": {"k": 14, "d": 3, "smooth": 3}, "outputs": ["k", "d"]},
    "adx": {"params": {"n": 14}, "outputs": None},
    "supertrend": {"params": {"n": 10, "mult": 3.0}, "outputs": ["line", "direction"]},
    "volume_sma": {"params": {"n": 20}, "outputs": None},
    "highest": {"params": {"n": None}, "outputs": None},  # swing high of the previous n bars
    "lowest": {"params": {"n": None}, "outputs": None},  # swing low of the previous n bars
    "fib": {"params": {"n": 50, "level": None}, "outputs": None},  # retracement, level 0-100 (%)
    "fib_ext": {"params": {"n": 50, "level": None}, "outputs": None},  # extension above the high, level > 100 (%)
    "trendline_support": {"params": {"n": 50}, "outputs": None},  # line through the last 2 swing lows, extended
    "trendline_resistance": {"params": {"n": 50}, "outputs": None},  # line through the last 2 swing highs, extended
    "support_zone": {"params": {}, "outputs": None},     # top of the nearest horizontal support zone below price
    "resistance_zone": {"params": {}, "outputs": None},  # bottom of the nearest horizontal resistance zone above price
}
FLOAT_PARAMS = {"std", "mult", "level"}
LOOKBACK_PARAMS = {"n", "k", "d", "smooth", "fast", "slow", "signal"}
PRICE_FIELDS = {"open", "high", "low", "close", "volume"}
CMPS = {">", ">=", "<", "<="}
BOOL_OPS = {"and", "or"}
CROSS_OPS = {"cross_above", "cross_below"}


class RuleError(ValueError):
    pass


# ---------------------------------------------------------------- validation / canonical form
def _num(v, name):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise RuleError(f"{name} must be a number")
    return v


def canon_operand(o: dict) -> dict:
    if not isinstance(o, dict) or "kind" not in o:
        raise RuleError(f"bad operand {o!r}")
    k = o["kind"]
    if k == "value":
        return {"kind": "value", "value": float(_num(o.get("value"), "value"))}
    if k == "price":
        f = o.get("field", "close")
        if f not in PRICE_FIELDS:
            raise RuleError(f"unknown price field {f!r}")
        return {"kind": "price", "field": f}
    if k == "indicator":
        name = o.get("name")
        spec = INDICATORS.get(name)
        if spec is None:
            raise RuleError(f"unsupported indicator {name!r}")
        params = {}
        given = o.get("params") or {}
        for p in given:
            if p not in spec["params"]:
                raise RuleError(f"{name} has no parameter {p!r}")
        for p, default in spec["params"].items():
            v = given.get(p, default)
            if v is None:
                raise RuleError(f"{name} needs parameter {p!r}")
            v = _num(v, f"{name}.{p}")
            if v <= 0 or v > 1000:
                raise RuleError(f"{name}.{p}={v} out of range")
            params[p] = int(v) if p not in FLOAT_PARAMS else float(v)
        if name == "fib" and not 0 < params["level"] <= 100:
            raise RuleError("Fibonacci retracement level must be between 0 and 100 (use fib_ext for 127.2, 161.8 ...)")
        if name == "fib_ext" and not 100 < params["level"] <= 500:
            raise RuleError("Fibonacci extension level must be above 100 (e.g. 127.2, 161.8)")
        out = o.get("output")
        if spec["outputs"]:
            out = out or spec["outputs"][0]
            if out not in spec["outputs"]:
                raise RuleError(f"{name} has no output {out!r}")
        else:
            out = None
        res = {"kind": "indicator", "name": name, "params": params}
        if out:
            res["output"] = out
        return res
    raise RuleError(f"unknown operand kind {k!r}")


def canon_node(n: dict) -> dict:
    if not isinstance(n, dict) or "op" not in n:
        raise RuleError(f"bad node {n!r}")
    op = n["op"]
    if op == "compare":
        if n.get("cmp") not in CMPS:
            raise RuleError(f"bad comparator {n.get('cmp')!r}")
        return {"op": "compare", "left": canon_operand(n["left"]), "cmp": n["cmp"], "right": canon_operand(n["right"])}
    if op in CROSS_OPS:
        return {"op": op, "left": canon_operand(n["left"]), "right": canon_operand(n["right"])}
    if op in BOOL_OPS:
        args = [canon_node(a) for a in n.get("args") or []]
        if len(args) < 2:
            raise RuleError(f"'{op}' needs at least 2 conditions")
        flat = []
        for a in args:  # flatten nested same-op
            flat.extend(a["args"] if a["op"] == op else [a])
        return {"op": op, "args": flat}
    if op == "not":
        return {"op": "not", "arg": canon_node(n["arg"])}
    if op == "pattern":  # a candlestick pattern completes on this bar
        name = n.get("name")
        if name not in pat.PATTERNS:
            raise RuleError(f"unknown candlestick pattern {name!r}. Supported: " + ", ".join(pat.PATTERNS))
        return {"op": "pattern", "name": name}
    if op == "strategy":  # a built-in multi-step strategy fires on this bar (with its own stop and target)
        if n.get("name") != zt.NAME:
            raise RuleError(f"unknown strategy {n.get('name')!r}. Supported: {zt.NAME}")
        try:
            return {"op": "strategy", "name": zt.NAME, "params": zt.canon_params(n.get("params"))}
        except ValueError as e:
            raise RuleError(str(e))
    raise RuleError(f"unknown op {op!r}")


def sorted_form(n: dict) -> dict:
    """Order-insensitive form of a canonical node (for exact-schema comparison in evals)."""
    if n["op"] in BOOL_OPS:
        return {"op": n["op"], "args": sorted((sorted_form(a) for a in n["args"]), key=repr)}
    if n["op"] == "not":
        return {"op": "not", "arg": sorted_form(n["arg"])}
    return n


def canon_risk(r: dict | None) -> dict | None:
    if not r:
        return None
    t = r.get("type")
    if t == "pct":
        v = float(_num(r.get("value"), "pct"))
        if not 0 < v < 50:
            raise RuleError("percent stop/target must be between 0 and 50")
        return {"type": "pct", "value": v}
    if t == "atr":
        m = float(_num(r.get("mult"), "atr mult"))
        if not 0 < m <= 20:
            raise RuleError("ATR multiple must be between 0 and 20")
        return {"type": "atr", "mult": m, "n": int(r.get("n", 14))}
    if t == "strategy":  # stop / target come from the strategy node (beyond the zone / next opposite zone)
        return {"type": "strategy"}
    raise RuleError(f"bad stop/target {r!r}")


def strategy_node(node: dict | None) -> dict | None:
    """The strategy node inside an entry condition, if any."""
    if not node:
        return None
    if node["op"] == "strategy":
        return node
    if node["op"] in BOOL_OPS:
        for a in node["args"]:
            f = strategy_node(a)
            if f:
                return f
    if node["op"] == "not":
        return None
    return None


def strategy_frame(df: pd.DataFrame, node: dict, cache: dict | None = None) -> pd.DataFrame:
    cache = {} if cache is None else cache
    key = f"strategy:{node['name']}:{sorted(node['params'].items())}"
    if key not in cache:
        cache[key] = zt.signals(df, node["params"])
    return cache[key]


def canon_rule(rule: dict) -> dict:
    if rule.get("side", "BUY") != "BUY":
        raise RuleError("only long (BUY) rules are supported")
    entry = canon_node(rule["entry"])
    uses = [r for r in (rule.get("stop_loss"), rule.get("target")) if r and r.get("type") == "strategy"]
    if uses and not strategy_node(entry):
        raise RuleError("a strategy stop/target needs a strategy entry condition")
    return {
        "entry": entry,
        "exit": canon_node(rule["exit"]) if rule.get("exit") else None,
        "side": "BUY",
        "stop_loss": canon_risk(rule.get("stop_loss")),
        "target": canon_risk(rule.get("target")),
    }


# ---------------------------------------------------------------- evaluation
def operand_series(df: pd.DataFrame, o: dict, cache: dict | None = None) -> pd.Series:
    cache = {} if cache is None else cache
    key = repr(o)
    if key in cache:
        return cache[key]
    k = o["kind"]
    if k == "value":
        s = pd.Series(o["value"], index=df.index, dtype=float)
    elif k == "price":
        s = df[o["field"]].astype(float)
    else:
        name, p, out = o["name"], o["params"], o.get("output")
        c = df["close"]
        if name == "sma":
            s = ind.sma(c, p["n"])
        elif name == "ema":
            s = ind.ema(c, p["n"])
        elif name == "wma":
            s = ind.wma(c, p["n"])
        elif name == "rsi":
            s = ind.rsi(c, p["n"])
        elif name == "macd":
            s = ind.macd(c, p["fast"], p["slow"], p["signal"])[out]
        elif name == "bbands":
            s = ind.bbands(c, p["n"], p["std"])[out]
        elif name == "atr":
            s = ind.atr(df, p["n"])
        elif name == "vwap":
            s = ind.vwap(df)
        elif name == "stoch":
            s = ind.stoch(df, p["k"], p["d"], p["smooth"])[out]
        elif name == "adx":
            s = ind.adx(df, p["n"])
        elif name == "supertrend":
            s = ind.supertrend(df, p["n"], p["mult"])[out]
        elif name == "volume_sma":
            s = ind.volume_sma(df, p["n"])
        elif name == "highest":
            s = ind.highest(df, p["n"])
        elif name == "lowest":
            s = ind.lowest(df, p["n"])
        elif name == "fib":
            s = ind.fib(df, p["n"], p["level"])
        elif name == "fib_ext":
            s = ind.fib_ext(df, p["n"], p["level"])
        elif name == "trendline_support":
            s = pat.trendline(df, p["n"], "support")
        elif name == "trendline_resistance":
            s = pat.trendline(df, p["n"], "resistance")
        elif name in ("support_zone", "resistance_zone"):
            if "_zones" not in cache:
                cache["_zones"] = lv.rolling_levels(df)
            s = cache["_zones"]["support" if name == "support_zone" else "resistance"]
        else:
            raise RuleError(name)
    cache[key] = s
    return s


def evaluate(df: pd.DataFrame, node: dict, cache: dict | None = None) -> pd.Series:
    """Boolean Series: True on bars where the condition holds (NaN inputs => False)."""
    cache = {} if cache is None else cache
    op = node["op"]
    if op == "compare":
        lhs, rhs = operand_series(df, node["left"], cache), operand_series(df, node["right"], cache)
        res = {">": lhs > rhs, ">=": lhs >= rhs, "<": lhs < rhs, "<=": lhs <= rhs}[node["cmp"]]
        return (res & lhs.notna() & rhs.notna()).astype(bool)
    if op in CROSS_OPS:
        lhs, rhs = operand_series(df, node["left"], cache), operand_series(df, node["right"], cache)
        valid = lhs.notna() & rhs.notna() & lhs.shift(1).notna() & rhs.shift(1).notna()
        if op == "cross_above":
            res = (lhs > rhs) & (lhs.shift(1) <= rhs.shift(1))
        else:
            res = (lhs < rhs) & (lhs.shift(1) >= rhs.shift(1))
        return (res & valid).astype(bool)
    if op == "and":
        out = pd.Series(True, index=df.index)
        for a in node["args"]:
            out &= evaluate(df, a, cache)
        return out
    if op == "or":
        out = pd.Series(False, index=df.index)
        for a in node["args"]:
            out |= evaluate(df, a, cache)
        return out
    if op == "not":
        return ~evaluate(df, node["arg"], cache)
    if op == "pattern":
        key = f"pattern:{node['name']}"
        if key not in cache:
            cache[key] = pat.detect(df, node["name"])
        return cache[key]
    if op == "strategy":
        return strategy_frame(df, node, cache)["signal"].astype(bool)
    raise RuleError(op)


def operands(node: dict) -> list[dict]:
    if node["op"] in ("pattern", "strategy"):
        return []
    if node["op"] in ("compare", *CROSS_OPS):
        return [node["left"], node["right"]]
    if node["op"] in BOOL_OPS:
        return [o for a in node["args"] for o in operands(a)]
    return operands(node["arg"])


def warmup_bars(rule: dict) -> int:
    longest = 1
    for node in [rule["entry"], rule.get("exit")]:
        if not node:
            continue
        for o in operands(node):
            if o["kind"] == "indicator":
                longest = max(longest, *[int(v) for k, v in o["params"].items() if k in LOOKBACK_PARAMS] or [1])
    if strategy_node(rule["entry"]):
        longest = max(longest, 130)  # zones are built from ~250 candles; 400 bars of history
    return longest * 3 + 10


def snapshot(df: pd.DataFrame, rule: dict) -> list[dict]:
    """Latest value of every operand: shown in the Algo Inspector (live values vs thresholds)."""
    out, cache, seen = [], {}, set()
    for node in [rule["entry"], rule.get("exit")]:
        if not node:
            continue
        for o in operands(node):
            if repr(o) in seen:
                continue
            seen.add(repr(o))
            s = operand_series(df, o, cache)
            v = s.iloc[-1] if len(s) else None
            out.append({"operand": describe_operand(o), "value": None if v is None or pd.isna(v) else round(float(v), 4)})
    return out


# ---------------------------------------------------------------- readable form
def describe_operand(o: dict) -> str:
    if o["kind"] == "value":
        v = o["value"]
        return f"{v:g}"
    if o["kind"] == "price":
        return o["field"].capitalize()
    name, p, out = o["name"], o["params"], o.get("output")
    if name in ("sma", "ema", "wma"):
        return f"{name.upper()}({p['n']})"
    if name == "rsi":
        return f"RSI({p['n']})"
    if name == "macd":
        label = {"macd": "MACD line", "signal": "MACD signal", "hist": "MACD histogram"}[out]
        return f"{label}({p['fast']},{p['slow']},{p['signal']})"
    if name == "bbands":
        return f"{out.capitalize()} Bollinger Band({p['n']},{p['std']:g})"
    if name == "stoch":
        return f"Stochastic %{out.upper()}({p['k']},{p['d']},{p['smooth']})"
    if name == "supertrend":
        return f"Supertrend {'direction' if out == 'direction' else 'line'}({p['n']},{p['mult']:g})"
    if name == "volume_sma":
        return f"{p['n']}-bar average volume"
    if name == "vwap":
        return "VWAP"
    if name == "highest":
        return f"{p['n']}-bar swing high"
    if name == "lowest":
        return f"{p['n']}-bar swing low"
    if name == "fib":
        return f"Fib {p['level']:g}% ({p['n']}-bar range)"
    if name == "fib_ext":
        return f"Fib extension {p['level']:g}% ({p['n']}-bar range)"
    if name == "trendline_support":
        return f"Trendline support ({p['n']} bars)"
    if name == "trendline_resistance":
        return f"Trendline resistance ({p['n']} bars)"
    if name == "support_zone":
        return "Support zone"
    if name == "resistance_zone":
        return "Resistance zone"
    return f"{name.upper()}({', '.join(str(v) for v in p.values())})"


def describe(node: dict | None) -> str:
    if node is None:
        return "none"
    op = node["op"]
    if op == "compare":
        return f"{describe_operand(node['left'])} {node['cmp']} {describe_operand(node['right'])}"
    if op == "cross_above":
        return f"{describe_operand(node['left'])} crosses above {describe_operand(node['right'])}"
    if op == "cross_below":
        return f"{describe_operand(node['left'])} crosses below {describe_operand(node['right'])}"
    if op in BOOL_OPS:
        return "(" + f" {op.upper()} ".join(describe(a) for a in node["args"]) + ")"
    if op == "pattern":
        label = pat.PATTERNS[node["name"]][0]
        return label if node["name"].startswith("any_") else f"{label} candle pattern"
    if op == "strategy":
        return zt.describe(node["params"])
    return f"NOT {describe(node['arg'])}"


def describe_risk(r: dict | None, label: str, default: str, strategy: dict | None = None) -> str:
    if not r:
        return f"{label}: default ({default})"
    if r["type"] == "strategy":
        p = (strategy or {}).get("params") or zt.DEFAULTS
        return f"{label}: " + ("just beyond the demand zone and the confirmation candle's low" if label.startswith("Stop")
                               else zt.describe_target(p))
    return f"{label}: {r['value']:g}%" if r["type"] == "pct" else f"{label}: {r['mult']:g} × ATR({r.get('n', 14)})"


def describe_rule(rule: dict) -> str:
    lines = [f"BUY when {describe(rule['entry'])}"]
    if rule.get("exit"):
        lines.append(f"SELL when {describe(rule['exit'])}")
    st = strategy_node(rule["entry"])
    lines.append(describe_risk(rule.get("stop_loss"), "Stop-loss", "ATR-based", st))
    lines.append(describe_risk(rule.get("target"), "Target", "ATR-based", st))
    return "\n".join(lines)
