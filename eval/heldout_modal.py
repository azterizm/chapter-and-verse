"""Held-out battery runner on Modal SGLang (spec heldout-modal, agent R3).

Pre-registered method: reports/heldout-qwen-modal-method.md.
Runner for the Qwen judge on Modal in workspace dpl015 with the measured preset
(Vishva007/Qwen3.8-27B-W4A16-AutoRound-GPTQ, thinking off, STRICT_FIGURES).

Refuses any call without --method-commit <sha>.
Reads MODAL_URL and MODAL_API_KEY from the environment at call time.
Caches replies by prompt sha under $CV_SCRATCH/heldout-modal/.
Writes once-per-detector markers there.
Enforces a dollar cap --max-usd (default 3.00) using the ledger's Modal cost model.
Prints the summary as the Groq runner does.

Offline dry run:
  uv run python -m eval.heldout_modal --dry
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from chapterverse.config import ConfigError
from chapterverse.providers import (
    CallUsage,
    InMemoryLedger,
    ModalAdapter,
    ModalLimits,
    OpenAICompatProvider,
    ProviderRotation,
    RotationJudgeChat,
    judge_request,
)
from eval.judge_gate import (
    MAX_WAIT_S,
    POLL_S,
    SEALED_LAYER,
    VERIFIER_ROOT,
    AttemptFailed,
    RunHalted,
    _append_jsonl,
    _p50,
    _p95,
    _read_jsonl,
    _sha,
    _utc_day,
    _write_json,
    check_seal,
    heldout_counts,
    load_run_battery,
    load_verifier,
    recorded_outcomes,
    run_heldout,
    scrub,
    settings_sent,
)

__all__ = [
    "DEFAULT_MAX_USD",
    "DEFAULT_OUT",
    "DETECTOR",
    "LATENCY_P50_S",
    "DynamicModalAdapter",
    "ModalGateChat",
    "cmd_heldout",
    "estimate_modal_cost",
    "heldout_counts",
    "main",
    "prompt_sha",
]

DETECTOR: Final = "qwen27-ar4-modal"
DETECTOR_CITED: Final = "qwen27-ar4-modal-cited"
"""The detector with the judge on the cited windows only (S5.1b): its own once-per-detector run."""


def detector_name(scope: str) -> str:
    """The detector name a run with ``scope`` reports and marks."""
    return DETECTOR_CITED if scope == "cited" else DETECTOR


DEFAULT_MAX_USD: Final = 3.00
LATENCY_P50_S: Final = 0.58
_STANDARD_TURNS: Final = 2

SCRATCH_ROOT: Final = Path(
    os.environ.get(
        "CV_SCRATCH",
        "/private/tmp/claude-501/-Users-abdiel-Code-chapter-and-verse/"
        "ff48a7a7-4145-4602-b5a5-4fbe1851fa50/scratchpad",
    )
)
DEFAULT_OUT: Final = SCRATCH_ROOT / "heldout-modal"


def estimate_modal_cost(
    num_calls: int,
    latency_s: float = LATENCY_P50_S,
    limits: ModalLimits | None = None,
) -> float:
    """Estimated Modal cost in USD under the ledger's container model.

    A continuous run pays one cold start, each call's latency, and the scaledown window.
    """
    lim = limits or ModalLimits()
    rate = lim.gpu_usd_per_s * lim.overhead
    total_billed_s = lim.cold_start_s + (num_calls * latency_s) + lim.scaledown_s
    return round(total_billed_s * rate, 4)


def prompt_sha(messages: Sequence[Mapping[str, str]]) -> str:
    """The prompt sha256: matches Judge.calls['sha'] for a standard 2-turn judge prompt."""
    if len(messages) == _STANDARD_TURNS and messages[0].get("role") == "system":
        return hashlib.sha256(
            f"{messages[0]['content']}\n{messages[1]['content']}".encode()
        ).hexdigest()
    text = json.dumps(list(messages), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


class DynamicModalAdapter(ModalAdapter):
    """ModalAdapter that resolves MODAL_URL dynamically from the environment at call time."""

    def __init__(self, *, url_env: str = "MODAL_URL", key_env: str = "MODAL_API_KEY") -> None:
        self.url_env = url_env
        self.key_env = key_env
        self._url = ""

    def _get_url(self) -> str:
        url = os.environ.get(self.url_env, "").strip()
        if not url.startswith(("https://", "http://")):
            raise ConfigError(f"{self.url_env} is not an http(s) URL")
        return url.rstrip("/").removesuffix("/v1")

    def base_url(self) -> str:
        return self._get_url() + "/v1"

    def health_url(self) -> str | None:
        return self._get_url() + "/health"


def _body_fn(provider_obj: Any) -> Callable[[list[dict[str, str]]], dict[str, Any]]:
    def body(messages: list[dict[str, str]]) -> dict[str, Any]:
        built, _ = provider_obj.build_body(judge_request(messages))
        return dict(built)

    return body


@dataclass
class ModalGateChat:
    """Judge's chat for Modal: cache by prompt sha, pacing, and dollar cap enforcement.

    Caches replies by prompt sha under out / 'cache.jsonl' (and individual JSON files).
    Enforces dollar cap using the ledger's Modal cost model before and after every call.
    """

    provider: str
    inner: Any
    body: Callable[[list[dict[str, str]]], dict[str, Any]]
    out: Path
    ledger: Any = None
    max_usd: float = DEFAULT_MAX_USD
    cost_spent: Callable[[], bool] = lambda: False
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.time
    max_wait_s: float = MAX_WAIT_S
    cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    hits: int = 0
    waits: list[tuple[str, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        for rec in _read_jsonl(self.cache_path):
            k = rec.get("key") or rec.get("prompt_sha")
            if k:
                self.cache[str(k)] = rec

    @property
    def model(self) -> str:
        return str(getattr(self.inner, "model", "modal"))

    @property
    def cache_path(self) -> Path:
        return self.out / "cache.jsonl"

    @property
    def cache_dir(self) -> Path:
        return self.out / "cache"

    @property
    def log_path(self) -> Path:
        return self.out / "requests.jsonl"

    def _log(self, **record: Any) -> None:
        _append_jsonl(
            self.log_path,
            {
                "provider": self.provider,
                "day": _utc_day(self.clock()),
                "t": round(self.clock(), 3),
                **record,
            },
        )

    def _check_budget(self) -> None:
        if self.cost_spent():
            raise RunHalted(f"{self.provider}: dollar cap ${self.max_usd:.2f} reached")
        if self.ledger is not None and not self.ledger.available(self.provider):
            raise RunHalted(f"{self.provider}: dollar cap ${self.max_usd:.2f} reached")

    def __call__(self, messages: list[dict[str, str]], /) -> dict[str, Any]:
        key = prompt_sha(messages)
        msha = _sha(messages)
        if key in self.cache:
            self.hits += 1
            rec = self.cache[key]
            return {k: rec[k] for k in ("content", "ms", "tokens", "finish")}

        waited = 0.0
        while True:
            self._check_budget()
            try:
                reply = self.inner(messages)
            except Exception as exc:
                if type(exc).__name__ not in {"AllProvidersUnavailable", "ProviderUnavailable"}:
                    raise
                wait = self._on_unavailable(exc, msha)
                if waited + wait > self.max_wait_s:
                    raise RunHalted(
                        f"{self.provider}: waited {waited:.0f} s, next wait {wait:.0f} s"
                    ) from None
                self.waits.append((self._kinds(exc), wait))
                self.sleep(wait)
                waited += wait
                continue
            break

        call = self.inner.calls[-1] if hasattr(self.inner, "calls") and self.inner.calls else None
        lat_ms = (
            round(float(call.latency_ms), 2)
            if call is not None and getattr(call, "latency_ms", None) is not None
            else round(float(reply.get("ms") or 0.0), 2)
        )
        prompt_tokens = (
            int(call.prompt_tokens)
            if call is not None and getattr(call, "prompt_tokens", None) is not None
            else None
        )
        completion_tokens = (
            int(call.completion_tokens)
            if call is not None and getattr(call, "completion_tokens", None) is not None
            else reply.get("tokens")
        )
        model_id = str(
            getattr(call, "model_id", getattr(self.inner, "model", "Vishva007/Qwen3.8-27B"))
        )

        if self.ledger is not None:
            self.ledger.record(
                CallUsage(
                    provider=self.provider,
                    est_tokens=0,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency_s=lat_ms / 1000.0,
                    sent=True,
                )
            )

        spent_usd = (
            round(float(self.ledger.snapshot()["modal"]["usd_session"]), 4)
            if self.ledger is not None
            else None
        )
        meta = {
            "model_id": model_id,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "latency_ms": lat_ms,
            "usd_session": spent_usd,
        }
        self._log(outcome="ok", msha=msha, prompt_sha=key, **meta)
        rec = {
            "key": key,
            "prompt_sha": key,
            "msha": msha,
            "content": reply["content"],
            "ms": reply["ms"],
            "tokens": reply["tokens"],
            "finish": reply["finish"],
            **meta,
        }
        self.cache[key] = rec
        _append_jsonl(self.cache_path, rec)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        _write_json(self.cache_dir / f"{key}.json", rec)
        return {k: rec[k] for k in ("content", "ms", "tokens", "finish")}

    @staticmethod
    def _kinds(exc: Exception) -> str:
        failures = getattr(exc, "failures", (exc,))
        return ",".join(sorted({str(getattr(f, "kind", "?")) for f in failures})) or "?"

    def _on_unavailable(self, exc: Exception, msha: str) -> float:
        failures = list(getattr(exc, "failures", (exc,)))
        if not failures:
            raise RunHalted(f"{self.provider}: no provider in the judge order")
        f = failures[0]
        kind = str(getattr(f, "kind", "unknown"))
        detail = str(getattr(f, "detail", str(f)))
        retry = getattr(f, "retry_after_s", None)
        local = detail in {"local quota ledger refused", "circuit open", "unpinned"}
        if not local or detail == "unpinned":
            self._log(outcome=kind, msha=msha, detail=scrub(detail)[:300], retry_after_s=retry)
        if detail == "unpinned" or kind in {"config", "model_changed"}:
            raise RunHalted(f"{self.provider}: {kind} ({detail[:120]})")
        if kind == "quota" or detail == "local quota ledger refused" or self.cost_spent():
            raise RunHalted(f"{self.provider}: dollar cap ${self.max_usd:.2f} reached")
        if detail == "circuit open" or kind in {"quota", "rate_limit"}:
            return max(POLL_S, float(retry or 0.0))
        raise AttemptFailed(f"{self.provider} {kind}")


class _CountingChat:
    """Offline counting chat: answers not_stated and collects prompts; sends nothing."""

    model = "dry"

    def __init__(self) -> None:
        self.prompts: list[list[dict[str, str]]] = []

    def __call__(self, messages: list[dict[str, str]], /) -> dict[str, Any]:
        self.prompts.append(messages)
        return {"content": '{"label": "not_stated", "passage": 0}', "ms": 0.0, "tokens": 0}


def _seed_modal_ledger(ledger: InMemoryLedger, out: Path) -> float:
    reqs = _read_jsonl(out / "requests.jsonl")
    for r in reqs:
        if r.get("provider") == "modal" and r.get("outcome") == "ok":
            lat_s = float(r.get("latency_ms") or 0.0) / 1000.0
            ledger.record(CallUsage(provider="modal", est_tokens=0, latency_s=lat_s, sent=True))
    return float(ledger.snapshot()["modal"]["usd_session"])


def _request_stats(out: Path, provider: str = "modal") -> dict[str, Any]:
    log = [r for r in _read_jsonl(out / "requests.jsonl") if r.get("provider") == provider]
    ok = [r for r in log if r.get("outcome") == "ok"]
    req_ms = [float(r["latency_ms"]) for r in ok if r.get("latency_ms") is not None]
    return {
        "sent": len(log),
        "ok": len(ok),
        "failures_by_kind": dict(Counter(str(r["outcome"]) for r in log if r["outcome"] != "ok")),
        "latency_ms_p50": _p50(req_ms),
        "latency_ms_p95": _p95(req_ms),
        "prompt_tokens": sum(int(r.get("prompt_tokens") or 0) for r in ok),
        "completion_tokens": sum(int(r.get("completion_tokens") or 0) for r in ok),
        "by_day": dict(Counter(str(r["day"]) for r in log)),
    }


def _resolve_layer_root(args: argparse.Namespace) -> Path:
    if args.layer_root is not None:
        return Path(args.layer_root)
    if (args.out / "sealed-layer").is_dir():
        return Path(args.out / "sealed-layer")
    return Path(SEALED_LAYER)


def _dry_run(
    v: Any,
    rb: Any,
    seal: Mapping[str, Any],
    *,
    layer_root: Path,
    max_usd: float,
    scope: str,
) -> int:
    counter = _CountingChat()
    stats: dict[str, int] = {}
    _, _, calls, enrichment, _ = run_heldout(
        v, rb, counter, layer_root=layer_root, judge_scope=scope, stats=stats
    )
    prompt_chars = sum(len(m["content"]) for msgs in counter.prompts for m in msgs)
    summary = {
        **seal,
        "detector": detector_name(scope),
        "judge_scope": scope,
        "judge_calls": len(calls),
        "rows_with_a_call": len({c["row"] for c in calls}),
        "to_modal": len(calls),
        "enrichment": sorted(str(e) for e in enrichment),
        "estimated_usd": estimate_modal_cost(len(calls)),
        "max_usd": max_usd,
        "judge_prompt_chars": prompt_chars,
        "cited_fallback": stats.get("cited_fallback", 0),
    }
    print(json.dumps(summary))
    return 0


def cmd_heldout(args: argparse.Namespace) -> int:
    v = load_verifier(args.verifier)
    rb = load_run_battery(args.verifier)
    seal = check_seal(args.verifier)
    scope = getattr(args, "judge_scope", "all")
    layer_root = _resolve_layer_root(args)
    if args.dry:
        return _dry_run(v, rb, seal, layer_root=layer_root, max_usd=args.max_usd, scope=scope)

    out: Path = args.out / "cited" if scope == "cited" else args.out
    stats: dict[str, int] = {}

    if not args.method_commit:
        raise SystemExit("a live held-out run needs --method-commit (the committed method)")

    out.mkdir(parents=True, exist_ok=True)
    done = out / f"{seal['seal']}-{detector_name(scope)}.done"
    if done.exists():
        raise SystemExit(f"{seal['seal']} has already run with {detector_name(scope)} ({done})")

    started = out / f"{seal['seal']}-{detector_name(scope)}.started"
    if not started.exists():
        _write_json(
            started,
            {
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "method_commit": args.method_commit,
                **seal,
            },
        )

    # Initialize ledger with session cap and month cap = max_usd
    limits = ModalLimits(usd_per_session=args.max_usd, usd_per_month=args.max_usd)
    ledger = InMemoryLedger(modal=limits)
    _seed_modal_ledger(ledger, out)

    adapter = DynamicModalAdapter()
    provider_obj = OpenAICompatProvider(adapter, ledger=ledger)
    rotation = ProviderRotation([provider_obj], judge=["modal"], ledger=ledger)
    inner = RotationJudgeChat(rotation, model=provider_obj.model_id)

    def cost_spent() -> bool:
        snap = ledger.snapshot()["modal"]
        return bool(
            float(snap["usd_session"]) >= args.max_usd or float(snap["usd_month"]) >= args.max_usd
        )

    chat = ModalGateChat(
        provider="modal",
        inner=inner,
        body=_body_fn(provider_obj),
        out=out,
        ledger=ledger,
        max_usd=args.max_usd,
        cost_spent=cost_spent,
    )
    settings = settings_sent(provider_obj)
    day = _utc_day()

    try:
        outcomes, raw, calls, enrichment, _ = run_heldout(
            v, rb, chat, layer_root=layer_root, judge_scope=scope, stats=stats
        )
    except RunHalted as exc:
        state = {
            "complete": False,
            "halted": str(exc),
            "cached": len(chat.cache),
            "day": day,
            "waits": len(chat.waits),
        }
        _write_json(out / "state.json", state)
        print(json.dumps(state))
        return 3

    client = [float(c["ms"]) for c in calls]
    secondary = {
        name: heldout_counts(rb, recorded_outcomes(rb, v.root, name)) for name in ("base", "none")
    }
    session_usd = float(ledger.snapshot()["modal"]["usd_session"])
    summary = {
        **seal,
        "detector": detector_name(scope),
        "method_commit": args.method_commit,
        "model_id": provider_obj.model_id,
        "settings_sent": settings,
        "enrichment": sorted(str(e) for e in enrichment),
        "counts": heldout_counts(rb, outcomes),
        "comparison": {
            "qwen": heldout_counts(rb, outcomes),
            **secondary,
        },
        "calls_by_provider": {"modal": len(calls)},
        "judge_calls": len(calls),
        "judge_errors": sum(1 for c in calls if c.get("label") == "error"),
        "attempts_over_1": sum(1 for c in calls if int(c.get("attempts") or 1) > 1),
        "client_ms_p50": _p50(client),
        "client_ms_p95": _p95(client),
        "requests": {"modal": _request_stats(out, "modal")},
        "cost": {
            "estimated_usd": round(session_usd, 4),
            "max_usd": args.max_usd,
        },
        **(
            {"judge_scope": scope, "cited_fallback": stats.get("cited_fallback", 0)}
            if scope == "cited"
            else {}
        ),
    }
    with (out / "raw.jsonl").open("w", encoding="utf-8") as f:
        for r in raw:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (out / "calls.jsonl").open("w", encoding="utf-8") as f:
        for c in calls:
            f.write(json.dumps({**c, "provider": "modal"}, ensure_ascii=False) + "\n")
    _write_json(out / "summary.json", summary)
    _write_json(done, {"at": datetime.now(UTC).isoformat(timespec="seconds")})
    _write_json(out / "state.json", {"complete": True, "day": day})
    print(json.dumps(summary["counts"]["summarise"]["headline"]))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verifier", type=Path, default=VERIFIER_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--layer-root", type=Path, default=None)
    parser.add_argument("--dry", action="store_true", help="count calls offline; send nothing")
    parser.add_argument("--method-commit", default="", help="commit of the pre-registered method")
    parser.add_argument(
        "--max-usd", type=float, default=DEFAULT_MAX_USD, help="Modal cost ceiling in USD"
    )
    parser.add_argument("--judge-scope", choices=["all", "cited"], default="all")
    parser.add_argument("cmd", nargs="?", default=None, help="optional subcommand")
    args = parser.parse_args(argv)
    return cmd_heldout(args)


if __name__ == "__main__":
    raise SystemExit(main())
