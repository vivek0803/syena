"""Pluggable LLM client with model tiers, budget guard and full call logging (ai_calls)."""
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from syena import settings_store
from syena.ai.prompts import ALL as PROMPTS
from syena.config import get_settings
from syena.db import utcnow
from syena.market.calendar import IST
from syena.models import AICall

log = logging.getLogger("syena.ai")


class LLMUnavailable(RuntimeError):
    pass


class BudgetExceeded(RuntimeError):
    pass


class LLMBadOutput(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    data: dict | None
    model: str
    prompt_version: str
    ai_call_id: int
    cost_usd: float
    latency_ms: int


class Backend:
    def complete(self, model: str, system: str, user: str, max_tokens: int) -> tuple[str, int, int]:
        raise NotImplementedError


class AnthropicBackend(Backend):
    def __init__(self, api_key: str):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=60)

    def complete(self, model, system, user, max_tokens):
        r = self.client.messages.create(
            model=model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}]
        )
        text = "".join(getattr(b, "text", "") for b in r.content)
        return text, r.usage.input_tokens, r.usage.output_tokens


class FakeBackend(Backend):
    """Deterministic backend for tests and offline evals: handler(model, system, user) -> text."""

    def __init__(self, handler: Callable[[str, str, str], str]):
        self.handler = handler
        self.calls: list[tuple[str, str]] = []

    def complete(self, model, system, user, max_tokens):
        self.calls.append((model, user))
        out = self.handler(model, system, user)
        return out, len(system + user) // 4, len(out) // 4


_backend: Backend | None = None


def set_backend(b: Backend | None) -> None:
    global _backend
    _backend = b


def backend() -> Backend:
    global _backend
    if _backend is None:
        key = get_settings().anthropic_api_key.get_secret_value()
        if not key:
            raise LLMUnavailable("SYENA_ANTHROPIC_API_KEY is not configured")
        _backend = AnthropicBackend(key)
    return _backend


def available() -> bool:
    try:
        backend()
        return True
    except LLMUnavailable:
        return False


def cost(db: Session, model: str, tin: int, tout: int) -> float:
    pricing = settings_store.get(db, "model_pricing")
    pin, pout = pricing.get(model, [0.0, 0.0])
    return round(tin / 1e6 * pin + tout / 1e6 * pout, 6)


def spent_today(db: Session) -> float:
    n = utcnow().astimezone(IST)
    start = datetime(n.year, n.month, n.day, tzinfo=IST)
    return float(db.scalar(select(func.coalesce(func.sum(AICall.cost_usd), 0.0)).where(AICall.ts >= start)) or 0.0)


def model_for(db: Session, tier: str) -> str | None:
    return settings_store.get(db, "models").get(tier)


def extract_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start = t.find("{")
    depth = 0
    for i in range(start, len(t)) if start >= 0 else []:
        depth += t[i] == "{"
        depth -= t[i] == "}"
        if depth == 0:
            return json.loads(t[start : i + 1])
    raise LLMBadOutput("no JSON object in model output")


def call(db: Session, purpose: str, prompt_name: str, *, tier: str | None = None, model: str | None = None,
         variables: dict | None = None, max_tokens: int = 1200, want_json: bool = True, shadow: bool = False,
         eval_run_id: int | None = None) -> LLMResult:
    prompt = PROMPTS[prompt_name]
    model = model or (model_for(db, tier) if tier else None)
    if not model:
        raise LLMUnavailable(f"no model configured for tier {tier!r}")
    budget = settings_store.get(db, "ai_budget")["daily_usd"]
    if eval_run_id is None and spent_today(db) >= budget:
        raise BudgetExceeded(f"daily AI budget ${budget} reached")
    system, user = prompt.render(**(variables or {}))
    be = backend()
    last_err = None
    for attempt in range(2):
        u = user if attempt == 0 else user + "\n\nReturn ONLY a valid JSON object, no prose."
        t0 = time.monotonic()
        row = AICall(purpose=purpose, tier=tier or "explicit", model=model, prompt_name=prompt.name,
                     prompt_version=prompt.version, prompt=f"[system]\n{system}\n\n[user]\n{u}", shadow=shadow,
                     eval_run_id=eval_run_id)
        try:
            text, tin, tout = be.complete(model, system, u, max_tokens)
            row.response_raw, row.input_tokens, row.output_tokens = text, tin, tout
            row.cost_usd = cost(db, model, tin, tout)
            row.latency_ms = int((time.monotonic() - t0) * 1000)
            data = extract_json(text) if want_json else None
            db.add(row)
            db.commit()
            return LLMResult(text, data, model, prompt.version, row.id, row.cost_usd, row.latency_ms)
        except (LLMBadOutput, json.JSONDecodeError) as e:
            last_err = e
            row.success, row.error = False, f"bad output: {e}"
        except Exception as e:  # API errors
            last_err = e
            row.success, row.error = False, f"{type(e).__name__}: {e}"
            row.latency_ms = int((time.monotonic() - t0) * 1000)
            db.add(row)
            db.commit()
            log.error("AI call failed", extra={"purpose": purpose, "model": model, "error": str(e)})
            raise
        row.latency_ms = int((time.monotonic() - t0) * 1000)
        db.add(row)
        db.commit()
    raise LLMBadOutput(str(last_err))
