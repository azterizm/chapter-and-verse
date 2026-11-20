"""Live smoke test of the provider clients on the free tiers (agent B3). Modal is skipped.

Per provider: the models list, one plain completion (the thinking-off canary) and one prefill
continuation (the French canary), so 3 requests each by default, all with ``max_tokens`` 32.
A hard cap per provider (Groq 8, Cloudflare 5 requests) is enforced by an httpx request hook.

Keys come from the environment (``GROQ_API_KEY``, ``CLOUDFLARE_AI_WORKER_TOKEN``,
``CLOUDFLARE_ACCOUNT_ID``) and are never printed; the account id is never printed either.

    uv run python scripts/smoke_providers.py [--providers groq,cloudflare]
        [--cases plain,prefill] [--skip-models] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from chapterverse.contracts import ProviderUnavailable
from chapterverse.providers.ledger import InMemoryLedger
from chapterverse.providers.openai_compat import (
    OpenAICompatProvider,
    make_cloudflare,
    make_groq,
)
from chapterverse.providers.pinning import (
    CANARY_MAX_TOKENS,
    CANARY_MESSAGES,
    CANARY_PREFILL,
    prefill_continued,
    prefill_ok,
    thinking_was_off,
)
from chapterverse.providers.presets import generation_request

CAPS = {"groq": 8, "cloudflare": 5}


class Budget:
    """Counts requests per provider; refuses past the cap before anything is sent."""

    def __init__(self, provider: str, cap: int) -> None:
        self.provider = provider
        self.cap = cap
        self.sent = 0

    def hook(self, request: httpx.Request) -> None:
        del request
        if self.sent >= self.cap:
            raise RuntimeError(f"{self.provider}: request cap {self.cap} reached")
        self.sent += 1


def _client(budget: Budget, timeout: httpx.Timeout) -> httpx.Client:
    return httpx.Client(
        follow_redirects=False, timeout=timeout, event_hooks={"request": [budget.hook]}
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _run(provider: OpenAICompatProvider, *, models: bool, cases: list[str]) -> list[dict[str, Any]]:
    log: list[dict[str, Any]] = []
    if models:
        log.append(_models(provider))
    prefills = {"plain": None, "prefill": CANARY_PREFILL}
    log.extend(_complete(provider, label, prefills[label]) for label in cases)
    by_case = {e["purpose"]: e for e in log}
    plain, pre = by_case.get("generate/plain"), by_case.get("generate/prefill")
    if plain and pre and plain["status"] == "ok" and pre["status"] == "ok":
        pre["prompt_extra_vs_plain"] = pre["prompt_tokens"] - plain["prompt_tokens"]
        pre["prefill_ok"] = prefill_ok(pre["text"], plain["prompt_tokens"], pre["prompt_tokens"])
    return log


def _models(provider: OpenAICompatProvider) -> dict[str, Any]:
    t0 = time.perf_counter()
    entry: dict[str, Any] = {"time": _now(), "provider": provider.name, "purpose": "models"}
    try:
        ids = provider.list_models()
        entry.update(status="ok", listed=provider.adapter.request_model in ids)
    except ProviderUnavailable as exc:
        entry.update(status=f"{exc.kind}: {exc.detail}")
    entry["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return entry


def _complete(provider: OpenAICompatProvider, label: str, prefill: str | None) -> dict[str, Any]:
    req = generation_request(
        CANARY_MESSAGES, mode="short", max_tokens=CANARY_MAX_TOKENS, prefill=prefill
    )
    entry: dict[str, Any] = {
        "time": _now(),
        "provider": provider.name,
        "purpose": f"generate/{label}",
    }
    t0 = time.perf_counter()
    try:
        result, message = provider.complete_raw(req, native_prefill=prefill is not None)
    except ProviderUnavailable as exc:
        entry.update(
            status=f"{exc.kind}: {exc.detail}",
            latency_ms=round((time.perf_counter() - t0) * 1000, 1),
        )
        return entry
    entry.update(
        status="ok",
        latency_ms=round(result.latency_ms, 1),
        text=result.text[:60],
        finish=result.finish_reason,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    )
    if prefill is None:
        entry["thinking_off"] = thinking_was_off(result.text, message, result.completion_tokens)
    else:
        entry["prefill_continued"] = prefill_continued(result.text)
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--providers", default="groq,cloudflare")
    parser.add_argument("--cases", default="plain,prefill", help="plain and/or prefill")
    parser.add_argument("--skip-models", action="store_true", help="do not list models")
    parser.add_argument("--json", type=Path, default=None, help="write the call log here")
    args = parser.parse_args()
    names = [n.strip() for n in args.providers.split(",") if n.strip()]
    cases = [c.strip() for c in args.cases.split(",") if c.strip()]
    if not set(cases) <= {"plain", "prefill"}:
        parser.error("--cases takes plain and/or prefill")
    ledger = InMemoryLedger()
    log: list[dict[str, Any]] = []
    budgets: dict[str, Budget] = {}
    for name in names:
        if name not in CAPS:
            print(f"skip {name}: not a free-tier provider in this smoke test", file=sys.stderr)
            continue
        budget = Budget(name, CAPS[name])
        budgets[name] = budget
        if name == "groq":
            provider = make_groq(
                ledger=ledger, retries=0, client=_client(budget, httpx.Timeout(60.0, connect=5.0))
            )
        else:
            account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
            if not account:
                print("skip cloudflare: CLOUDFLARE_ACCOUNT_ID is not set", file=sys.stderr)
                continue
            provider = make_cloudflare(
                account,
                ledger=ledger,
                retries=0,
                client=_client(budget, httpx.Timeout(120.0, connect=5.0)),
            )
        log.extend(_run(provider, models=not args.skip_models, cases=cases))

    for entry in log:
        print(json.dumps(entry, ensure_ascii=False))
    print(json.dumps({"requests": {n: b.sent for n, b in budgets.items()}}))
    snap = ledger.snapshot()
    print(
        json.dumps(
            {
                "ledger": {
                    "groq_requests_day": snap["groq"]["requests_day"],
                    "groq_tokens_minute": snap["groq"]["tokens_minute"],
                    "groq_tokens_per_minute_limit": snap["groq"]["tokens_per_minute"],
                    "groq_requests_per_day_limit": snap["groq"]["requests_per_day"],
                    "cloudflare_neurons_day": round(snap["cloudflare"]["neurons_day"], 3),
                }
            }
        )
    )
    if args.json is not None:
        args.json.write_text(json.dumps(log, indent=1, ensure_ascii=False) + "\n")
    ok = all(e["status"] == "ok" for e in log)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
