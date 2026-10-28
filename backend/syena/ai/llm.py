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
    sources: list | None = None  # [{"title", "url"}] when the model searched the web
    searches: int = 0


CACHE_MIN_CHARS = 4000  # ~1,000+ tokens: below the API's minimum a cache mark does nothing anyway
CACHE_READ_FACTOR, CACHE_WRITE_FACTOR = 0.1, 1.25  # of the normal input price


WEB_SEARCH_USD = 0.01  # per search (Anthropic web search tool: $10 per 1,000)
WEB_NOTE = ("\nLIVE MARKET DATA: you have a web_search tool. Use it for anything current that the provided data doesn't "
            "cover: today's or recent price moves, latest results and guidance, analyst views, news, sector and index moves, "
            "commodity/currency/rate levels. Prefer NSE/BSE filings, company releases and established financial news sites. "
            "Say where each outside figure comes from (site and date). Syena's own figures stay the basis wherever they exist; "
            "if the web disagrees with them, say so. Keep the required JSON output format.")


class Backend:
    last_cache: tuple[int, int] = (0, 0)  # (tokens read from cache, tokens written to cache) on the last call
    last_web: tuple[int, list] = (0, [])  # (searches made, sources cited) on the last call

    def complete(self, model: str, system: str, user: str, max_tokens: int, images=None, temperature=None,
                 web_search: int = 0) -> tuple[str, int, int]:
        raise NotImplementedError


class AnthropicBackend(Backend):
    def __init__(self, api_key: str):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=60)

    def complete(self, model, system, user, max_tokens, images=None, temperature=None, web_search=0):
        content = user
        if images:  # [(media_type, bytes)] -> image blocks followed by the text
            import base64

            content = [{"type": "image", "source": {"type": "base64", "media_type": mt, "data": base64.b64encode(b).decode()}}
                       for mt, b in images] + [{"type": "text", "text": user}]
        kw = {"temperature": temperature} if temperature is not None else {}
        if web_search:  # Anthropic's server-side web search: the model searches and reads pages itself
            kw["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": int(web_search),
                            "user_location": {"type": "approximate", "country": "IN", "timezone": "Asia/Kolkata"}}]
        # Long system prompts (the news-analysis playbook) are the same on every call: mark them cacheable so
        # repeat calls within a few minutes pay about a tenth for that part. Short ones are sent as plain text.
        sys_arg = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}] if len(system) >= CACHE_MIN_CHARS else system
        try:
            r = self.client.messages.create(
                model=model, max_tokens=max_tokens, system=sys_arg, messages=[{"role": "user", "content": content}], **kw
            )
        except Exception as e:  # a model that doesn't take a temperature: ask again without it
            if not kw or "temperature" not in str(e).lower():
                raise
            kw.pop("temperature", None)
            r = self.client.messages.create(model=model, max_tokens=max_tokens, system=sys_arg,
                                            messages=[{"role": "user", "content": content}], **kw)
        if getattr(r, "stop_reason", None) == "max_tokens":
            log.warning("AI answer cut off at the token limit", extra={"model": model, "max_tokens": max_tokens})
        # with web search the answer is the text after the last search result; earlier text is the model thinking aloud
        blocks = list(r.content)
        last_tool = max((i for i, b in enumerate(blocks) if getattr(b, "type", "") in ("server_tool_use", "web_search_tool_result")), default=-1)
        text = "".join(getattr(b, "text", "") for b in blocks[last_tool + 1:] if getattr(b, "type", "text") == "text") or \
            "".join(getattr(b, "text", "") for b in blocks)
        sources, seen = [], set()
        for b in blocks:
            for c in getattr(b, "citations", None) or []:
                url = getattr(c, "url", None)
                if url and url not in seen:
                    seen.add(url)
                    sources.append({"title": getattr(c, "title", None) or url, "url": url})
        u = r.usage
        stu = getattr(u, "server_tool_use", None)
        self.last_web = (int(getattr(stu, "web_search_requests", 0) or 0) if stu else 0, sources[:12])
        self.last_cache = (int(getattr(u, "cache_read_input_tokens", 0) or 0), int(getattr(u, "cache_creation_input_tokens", 0) or 0))
        return text, u.input_tokens, u.output_tokens


class FakeBackend(Backend):
    """Deterministic backend for tests and offline evals: handler(model, system, user) -> text."""

    def __init__(self, handler: Callable[[str, str, str], str]):
        self.handler = handler
        self.calls: list[tuple[str, str]] = []

    def complete(self, model, system, user, max_tokens, images=None, temperature=None, web_search=0):
        self.calls.append((model, user))
        self.last_web = (1 if web_search else 0, [{"title": "test source", "url": "https://example.com/x"}] if web_search else [])
        out = self.handler(model, system, user + (f"\n[{len(images)} image(s)]" if images else ""))
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


def cost(db: Session, model: str, tin: int, tout: int, cache_read: int = 0, cache_write: int = 0) -> float:
    pricing = settings_store.get(db, "model_pricing")
    pin, pout = pricing.get(model, [0.0, 0.0])
    return round((tin + cache_read * CACHE_READ_FACTOR + cache_write * CACHE_WRITE_FACTOR) / 1e6 * pin + tout / 1e6 * pout, 6)


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
         eval_run_id: int | None = None, images: list | None = None, temperature: float | None = None,
         web_search: int = 0) -> LLMResult:
    """web_search: let the model search the web up to this many times (0 = only the data given)."""
    prompt = PROMPTS[prompt_name]
    model = model or (model_for(db, tier) if tier else None)
    if not model:
        raise LLMUnavailable(f"no model configured for tier {tier!r}")
    budget = settings_store.get(db, "ai_budget")["daily_usd"]
    if eval_run_id is None and spent_today(db) >= budget:
        raise BudgetExceeded(f"daily AI budget ${budget} reached")
    system, user = prompt.render(**(variables or {}))
    if web_search:
        system += WEB_NOTE
    be = backend()
    last_err = None
    for attempt in range(2):
        u = user if attempt == 0 else user + "\n\nReturn ONLY a valid JSON object, no prose."
        t0 = time.monotonic()
        row = AICall(purpose=purpose, tier=tier or "explicit", model=model, prompt_name=prompt.name,
                     prompt_version=prompt.version, prompt=f"[system]\n{system}\n\n[user]\n{u}", shadow=shadow,
                     eval_run_id=eval_run_id)
        try:
            # second attempt gets more room: a truncated answer is the usual cause of unparseable JSON
            text, tin, tout = be.complete(model, system, u, max_tokens if attempt == 0 else max(max_tokens * 2, 1000),
                                          **({"images": images} if images else {}),
                                          **({"temperature": temperature} if temperature is not None else {}),
                                          **({"web_search": web_search} if web_search else {}))
            cread, cwrite = getattr(be, "last_cache", (0, 0)) or (0, 0)
            be.last_cache = (0, 0)
            nsearch, sources = getattr(be, "last_web", (0, [])) or (0, [])
            be.last_web = (0, [])
            row.response_raw, row.input_tokens, row.output_tokens = text, tin + cread + cwrite, tout
            row.cost_usd = round(cost(db, model, tin, tout, cread, cwrite) + nsearch * WEB_SEARCH_USD, 6)
            row.latency_ms = int((time.monotonic() - t0) * 1000)
            data = extract_json(text) if want_json else None
            db.add(row)
            db.commit()
            return LLMResult(text, data, model, prompt.version, row.id, row.cost_usd, row.latency_ms, sources or None, nsearch)
        except (LLMBadOutput, json.JSONDecodeError) as e:
            last_err = e
            row.success, row.error = False, f"bad output: {e}"
            log.warning("AI answer not valid JSON", extra={"purpose": purpose, "model": model, "attempt": attempt + 1,
                                                           "output_tokens": row.output_tokens,
                                                           "snippet": (row.response_raw or "")[:300]})
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
