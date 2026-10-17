"""Triage and mapping evals (17C): relevance precision/recall with false negatives (missed material news)
tracked separately from false positives (noise cost), plus article -> stock mapping accuracy.

Labelled data: evals/triage/*.yaml (bootstrap set) + your labels from the News Feed (manual_labels kind=triage).
"""
from datetime import datetime, timezone

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from syena import settings_store
from syena.evals import runner
from syena.models import ManualLabel, NewsItem
from syena.news import pipeline


def load_cases(db: Session) -> list[dict]:
    cases = []
    for f in sorted((runner.evals_dir() / "triage").glob("*.yaml")):
        for c in yaml.safe_load(f.read_text()) or []:
            cases.append({"id": c["id"], "title": c["title"], "body": c.get("body", ""), "relevant": bool(c["relevant"]),
                          "symbols": sorted(c.get("symbols", []))})
    for lab in db.scalars(select(ManualLabel).where(ManualLabel.kind == "triage")):
        item = db.get(NewsItem, lab.ref_id)
        if item:
            cases.append({"id": f"label-{item.id}", "title": item.title, "body": item.body or "",
                          "relevant": bool(lab.label.get("relevant")), "symbols": sorted(lab.label.get("symbols", []))})
    return cases


def score(cases_and_preds: list[tuple[dict, dict]]) -> dict:
    tp = fp = fn = tn = 0
    sym_tp = sym_fp = sym_fn = 0
    for c, p in cases_and_preds:
        if c["relevant"] and p["relevant"]:
            tp += 1
        elif not c["relevant"] and p["relevant"]:
            fp += 1
        elif c["relevant"] and not p["relevant"]:
            fn += 1
        else:
            tn += 1
        exp, got = set(c["symbols"]), set(p.get("symbols", []))
        sym_tp += len(exp & got)
        sym_fp += len(got - exp)
        sym_fn += len(exp - got)
    n = len(cases_and_preds)
    return {
        "n": n,
        "relevance_precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "relevance_recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "false_negatives_missed_material": fn, "false_positives_noise": fp, "true_negatives": tn,
        "mapping_precision": round(sym_tp / (sym_tp + sym_fp), 4) if sym_tp + sym_fp else None,
        "mapping_recall": round(sym_tp / (sym_tp + sym_fn), 4) if sym_tp + sym_fn else None,
    }


def run_suite(db: Session, config: dict | None = None, triggered_by: str = "manual"):
    config = config or {}
    model = config.get("model") or settings_store.get(db, "models")["triage"]
    run = runner.start(db, "triage", {"model": model, "prompt": "news_triage"}, triggered_by)
    try:
        cases = load_cases(db)
        watch = settings_store.get(db, "news")["watchlist"]
        pairs = []
        for c in cases:
            item = NewsItem(id=None, source="eval", url="", title=c["title"], body=c["body"],
                            published_at=datetime.now(timezone.utc), content_hash="")
            try:
                p = pipeline.triage(db, item, watch, model=model, eval_run_id=run.id, persist=False)
            except Exception as e:
                p = {"relevant": False, "symbols": [], "error": str(e)}
            pairs.append((c, p))
            ok = p["relevant"] == c["relevant"] and (not c["relevant"] or set(p["symbols"]) == set(c["symbols"]))
            runner.add_result(db, run, c["id"], ok, input={"title": c["title"]}, expected={"relevant": c["relevant"], "symbols": c["symbols"]},
                              actual=p, detail="MISSED MATERIAL NEWS" if c["relevant"] and not p["relevant"] else None)
        m = score(pairs)
        passed = (m["relevance_recall"] or 0) >= 0.8 and (m["mapping_precision"] or 0) >= 0.8
        return runner.finish(db, run, m, passed, len(cases))
    except Exception as e:
        return runner.fail(db, run, e)
