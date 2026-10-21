"""Rule-parser evals (17A). Deterministic, no LLM judge.

Golden cases live in evals/parser/*.yaml. Expected rules are written in a tiny independent DSL
(Python-expression syntax), e.g.  entry: and(lt(close, bb(20,2).lower), lt(rsi(14), 30))
Metrics: exact-schema match (order-insensitive), semantic equivalence (both rules evaluated on the same
synthetic candles must fire on the same bars), clarification correctness.
"""
import ast

import numpy as np
import pandas as pd
import yaml
from sqlalchemy.orm import Session

from syena import settings_store
from syena.algos import parser
from syena.algos.rules import canon_node, canon_risk, evaluate, sorted_form
from syena.evals import runner

CMP = {"gt": ">", "ge": ">=", "lt": "<", "le": "<="}
PRICE = {"open", "high", "low", "close", "volume"}
IND = {"sma": ("sma", ["n"]), "ema": ("ema", ["n"]), "wma": ("wma", ["n"]), "rsi": ("rsi", ["n"]), "atr": ("atr", ["n"]),
       "adx": ("adx", ["n"]), "macd": ("macd", ["fast", "slow", "signal"]), "bb": ("bbands", ["n", "std"]),
       "stoch": ("stoch", ["k", "d", "smooth"]), "st": ("supertrend", ["n", "mult"]), "vol_sma": ("volume_sma", ["n"]),
       "hh": ("highest", ["n"]), "ll": ("lowest", ["n"]), "fib": ("fib", ["n", "level"]), "fibx": ("fib_ext", ["n", "level"]),
       "tls": ("trendline_support", ["n"]), "tlr": ("trendline_resistance", ["n"])}


def _operand(n: ast.AST) -> dict:
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
        return {"kind": "value", "value": float(n.value)}
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
        return {"kind": "value", "value": -float(n.operand.value)}
    if isinstance(n, ast.Name):
        if n.id in PRICE:
            return {"kind": "price", "field": n.id}
        if n.id == "vwap":
            return {"kind": "indicator", "name": "vwap", "params": {}}
    output = None
    if isinstance(n, ast.Attribute):
        output, n = n.attr, n.value
    if isinstance(n, ast.Call) and n.func.id in IND:
        name, keys = IND[n.func.id]
        params = {k: a.value for k, a in zip(keys, n.args)}
        d = {"kind": "indicator", "name": name, "params": params}
        if output:
            d["output"] = output
        return d
    raise ValueError(f"bad DSL operand: {ast.dump(n)}")


def _node(n: ast.AST) -> dict:
    f = n.func.id
    if f in CMP:
        return {"op": "compare", "left": _operand(n.args[0]), "cmp": CMP[f], "right": _operand(n.args[1])}
    if f in ("cross_above", "cross_below"):
        return {"op": f, "left": _operand(n.args[0]), "right": _operand(n.args[1])}
    if f in ("and_", "or_", "and", "or"):
        return {"op": f.rstrip("_"), "args": [_node(a) for a in n.args]}
    if f in ("not_", "neg"):
        return {"op": "not", "arg": _node(n.args[0])}
    if f == "pattern":  # pattern(bullish_engulfing)
        return {"op": "pattern", "name": n.args[0].id}
    raise ValueError(f"bad DSL node {f}")


def dsl(expr: str) -> dict:
    src = expr.replace("and(", "and_(").replace("or(", "or_(").replace("not(", "not_(")
    return canon_node(_node(ast.parse(src, mode="eval").body))


def load_cases() -> list[dict]:
    cases = []
    for f in sorted((runner.evals_dir() / "parser").glob("*.yaml")):
        for c in yaml.safe_load(f.read_text()) or []:
            c["file"] = f.name
            cases.append(c)
    return cases


def synthetic_candles(n: int = 600, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-02 03:45", periods=n, freq="15min", tz="UTC")
    c = 1000 * np.exp(np.cumsum(rng.normal(0, 0.006, n) + 0.002 * np.sin(np.arange(n) / 25)))
    o = c * (1 + rng.normal(0, 0.002, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.003, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.003, n)))
    v = rng.integers(50_000, 500_000, n).astype(float)
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": v}, index=idx)


def check_case(case: dict, out: parser.ParseOutput, df: pd.DataFrame) -> tuple[bool, dict]:
    if case.get("clarify"):
        ok = out.status == "clarify"
        return ok, {"kind": "clarify", "clarify_ok": ok}
    if out.status != "ok":
        return False, {"kind": "rule", "exact": False, "semantic": False, "error": f"got {out.status}: {out.question}"}
    exp_entry = dsl(case["entry"])
    exp_exit = dsl(case["exit"]) if case.get("exit") else None
    got = out.rule
    exact = sorted_form(got["entry"]) == sorted_form(exp_entry) and (
        (exp_exit is None and got["exit"] is None) or (exp_exit is not None and got["exit"] is not None
                                                        and sorted_form(got["exit"]) == sorted_form(exp_exit)))
    for key in ("stop_loss", "target"):
        if key in case:
            exact = exact and canon_risk(case[key]) == got.get(key)
    sem = bool((evaluate(df, got["entry"]) == evaluate(df, exp_entry)).all())
    if exp_exit is not None and got["exit"] is not None:
        sem = sem and bool((evaluate(df, got["exit"]) == evaluate(df, exp_exit)).all())
    elif (exp_exit is None) != (got["exit"] is None):
        sem = False
    return exact or sem, {"kind": "rule", "exact": exact, "semantic": sem}


def evaluate_cases(cases: list[dict], parse_fn) -> tuple[dict, list[dict]]:
    df = synthetic_candles()
    rows = []
    for c in cases:
        try:
            out = parse_fn(c["text"])
            ok, info = check_case(c, out, df)
            actual = out.rule if out.status == "ok" else {"status": out.status, "question": out.question}
        except Exception as e:  # a crash counts as a failure, not an eval error
            ok, info, actual = False, {"kind": "clarify" if c.get("clarify") else "rule", "error": str(e)}, None
        rows.append({"case": c, "ok": ok, "info": info, "actual": actual})
    rule_rows = [r for r in rows if r["info"]["kind"] == "rule"]
    clar_rows = [r for r in rows if r["info"]["kind"] == "clarify"]
    metrics = {
        "n": len(rows),
        "accuracy": round(sum(r["ok"] for r in rows) / len(rows), 4) if rows else None,
        "exact_schema_match": round(sum(r["info"].get("exact", False) for r in rule_rows) / len(rule_rows), 4) if rule_rows else None,
        "semantic_equivalence": round(sum(r["info"].get("semantic", False) for r in rule_rows) / len(rule_rows), 4) if rule_rows else None,
        "clarification_correct": round(sum(r["ok"] for r in clar_rows) / len(clar_rows), 4) if clar_rows else None,
        "n_rule_cases": len(rule_rows), "n_clarify_cases": len(clar_rows),
        "false_clarifications": sum(1 for r in rule_rows if r["actual"] and isinstance(r["actual"], dict) and r["actual"].get("status") == "clarify"),
    }
    return metrics, rows


def thresholds_pass(metrics: dict, cfg: dict) -> bool:
    return (metrics["accuracy"] or 0) >= cfg["parser_min_accuracy"] and (
        metrics["clarification_correct"] is None or metrics["clarification_correct"] >= cfg["parser_min_clarification_accuracy"])


def run_suite(db: Session, config: dict | None = None, triggered_by: str = "manual"):
    config = config or {}
    use_llm = bool(config.get("use_llm"))
    run = runner.start(db, "parser", {"use_llm": use_llm, **config}, triggered_by)
    try:
        cases = load_cases()
        metrics, rows = evaluate_cases(cases, lambda t: parser.parse(t, db=db, use_llm=use_llm))
        for r in rows:
            c = r["case"]
            runner.add_result(db, run, c["id"], r["ok"], input={"text": c["text"]},
                              expected={"clarify": True} if c.get("clarify") else {"entry": c.get("entry"), "exit": c.get("exit")},
                              actual=r["actual"], detail=str(r["info"]))
        passed = thresholds_pass(metrics, settings_store.get(db, "evals"))
        return runner.finish(db, run, metrics, passed, len(rows))
    except Exception as e:
        return runner.fail(db, run, e)
