"""Slice 1 judge gate: the verifier's stop 78 live check through our provider layer.

Reproduces ``legal-rag-verifier/scripts/live_check.py`` (stop 78): fresh2's 151 graded units,
``JudgedVerifier(judge, STRICT_FIGURES)`` with the ``qwen27-ar4`` short prompt, one call at a
time, scored with that script's own ``_cell``, ``_score_units`` and ``_check_agreement``
(imported from it, read only). The one change is the chat: ``Judge(chat)`` gets a
:class:`~chapterverse.providers.RotationJudgeChat` over a rotation pinned to one provider
(``groq`` or ``cloudflare``), wrapped in :class:`GateChat`:

* a cache keyed by the sha256 of the request body, so a resumed run never pays twice;
* pacing: a rate limit, an open circuit or a full minute window waits and re-sends (Modal had
  none of these); a spent daily budget halts the run, to resume another day;
* any other provider failure is an ``OSError`` to ``Judge``, a failed attempt, as a transport
  error was with ``OpenAIChat``.

Nothing is written inside the verifier repo: bytecode writing is turned off before its scripts
are imported, and every output goes under ``--out`` (default: the c2a scratch directory).

  uv run python -m eval.judge_gate replay              # offline: stop 78's labels, same counting
  uv run python -m eval.judge_gate run --provider groq
  uv run python -m eval.judge_gate run --provider cloudflare

Keys are read from the environment at call time by the provider clients and never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import random
import re
import statistics
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Final

__all__ = [
    "CP_ALPHA",
    "GateChat",
    "RunHalted",
    "Verifier78",
    "clopper_pearson",
    "load_verifier",
    "main",
    "rates",
]

VERIFIER_ROOT: Final = Path(
    os.environ.get("LRV_ROOT", str(Path.home() / "Code" / "legal-rag-verifier"))
)
DEFAULT_OUT: Final = Path(
    "/private/tmp/claude-501/-Users-abdiel-Code-chapter-and-verse/"
    "ff48a7a7-4145-4602-b5a5-4fbe1851fa50/scratchpad/c2a"
)
PRESET_NAME: Final = "qwen27-ar4"
VERIFIER_VERSION: Final = "0.5.0"
SEED: Final = 20261119
GROQ_REQUEST_CAP: Final = 700
CLOUDFLARE_NEURONS: Final = 9500.0
CF_PER_M_IN: Final = 40909.0
CF_PER_M_OUT: Final = 290909.0
CF_MARGIN: Final = 1.25
"""Planning margin on the Cloudflare cost estimate."""
DAILY_RETRY_S: Final = 3600.0
"""A retry-after this long is a daily limit, not a minute window."""
MAX_WAIT_S: Final = 900.0
"""A wait longer than this halts the run instead (resume later)."""
POLL_S: Final = 3.0
CP_ALPHA: Final = 0.05
PINGS: Final = 20
REFERENCE: Final = Path("results/fresh2-live78/summary.json")
REFERENCE_CALLS: Final = Path("results/fresh2-live78/calls.jsonl")
GROQ_DAY_CAP: Final = 900
"""Groq requests a UTC day across option A and option B, pings included."""
TOKEN_MARGIN: Final = 2048
"""Tokens kept free under the ledger's Groq day ceiling (one judge call and its max output)."""
HELDOUT_BATTERY: Final = Path("batteries/verifier/heldout_draft.jsonl")
HELDOUT_SEAL: Final = Path("batteries/verifier/seals/heldout-2026-10-06.json")
HELDOUT_SHA: Final = "1df51b991128c317225d58e340f89532fd9cec149adbfb8bec34d5a6a9bb05b9"
HELDOUT_ROWS: Final = 346
HELDOUT_ENRICHMENT: Final = Path("enrichment/gemini-3.8-flash-high")
HELDOUT_ENRICHMENT_ID: Final = "gemini-3.8-flash-high@edb8ccbbc4fc4862"
HELDOUT_DETECTOR: Final = "qwen27-ar4-groq"
SEAL_COMMIT: Final = "a4d9726"
"""The commit that sealed the battery; its enrichment layer (190 units) gives the sealed id."""
SEALED_LAYER: Final = DEFAULT_OUT / "sealed-layer"
"""``git archive a4d9726 enrichment/gemini-3.8-flash-high``, extracted here (read only)."""
GROQ_TPM: Final = 8000


class RunHalted(Exception):  # noqa: N818 - control flow, not caught by Judge
    """The day's budget is spent or the provider cannot serve: stop, keep the cache, resume."""


class AttemptFailed(OSError):  # noqa: N818 - Judge's view: a failed attempt
    """A provider failure ``Judge`` counts as a failed attempt (as ``URLError`` was)."""


# ------------------------------------------------------------------------------ statistics


def _binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return sum(math.comb(n, i) * p**i * (1.0 - p) ** (n - i) for i in range(k + 1))


def _bisect(f: Callable[[float], float], target: float, *, increasing: bool) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        above = f(mid) > target
        if above == increasing:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def clopper_pearson(k: int, n: int, alpha: float = CP_ALPHA) -> tuple[float, float]:
    """The exact two-sided ``1 - alpha`` interval for ``k`` successes in ``n`` trials.

    Raises:
        ValueError: ``n`` < 1 or ``k`` outside ``0..n``.
    """
    if n < 1 or not 0 <= k <= n:
        raise ValueError(f"need 0 <= k <= n and n >= 1, got k={k}, n={n}")
    lower = (
        0.0
        if k == 0
        else _bisect(lambda p: 1.0 - _binom_cdf(k - 1, n, p), alpha / 2, increasing=True)
    )
    upper = 1.0 if k == n else _bisect(lambda p: _binom_cdf(k, n, p), alpha / 2, increasing=False)
    return lower, upper


def _rate(k: int, n: int) -> dict[str, Any]:
    if n == 0:
        return {"k": k, "n": n, "pct": None, "ci95": None}
    lo, hi = clopper_pearson(k, n)
    return {
        "k": k,
        "n": n,
        "pct": round(100.0 * k / n, 1),
        "ci95": [round(100.0 * lo, 1), round(100.0 * hi, 1)],
    }


def rates(cell: Mapping[str, int]) -> dict[str, Any]:
    """Catch, false-rollback and accuracy rates of a ``_cell`` result, with CP 95 % intervals.

    ``_cell`` gives ``correct = right - false_rollbacks + caught``, so ``right`` and ``wrong``
    follow from it and ``units``.
    """
    units, correct = cell["units"], cell["correct"]
    fr, caught = cell["false_rollbacks"], cell["caught"]
    right = correct + fr - caught
    wrong = units - right
    return {
        "right": right,
        "wrong": wrong,
        "caught": _rate(caught, wrong),
        "false_rollback": _rate(fr, right),
        "accuracy": _rate(correct, units),
    }


def _p50(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _p95(values: Sequence[float]) -> float | None:
    """Nearest rank at 0.95, as ``live_check._p95``."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]


# ------------------------------------------------------------------------------ files


_ORG: Final = re.compile(r"\borg_[A-Za-z0-9]+")


def scrub(text: str) -> str:
    """Provider error text without organisation ids (keys are scrubbed by the client)."""
    return _ORG.sub("org_<redacted>", text)


def _utc_day(now: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if now is None else now, UTC).strftime("%Y-%m-%d")


def _sha(obj: object) -> str:
    text = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ------------------------------------------------------------------------------ the chat


@dataclass
class GateChat:
    """``Judge``'s chat: cache, pacing and failure mapping around a ``RotationJudgeChat``.

    ``inner`` is the ``RotationJudgeChat`` (or a test double with ``model``, ``calls`` and
    ``__call__``); ``body`` builds the request body the provider would send, the cache key.
    ``spent`` returns the provider's ``(requests_today, neurons_today)`` before a call, and
    ``budget_left`` says whether one more call fits; both read the ledger.
    """

    provider: str
    inner: Any
    body: Callable[[list[dict[str, str]]], dict[str, Any]]
    out: Path
    neurons: Callable[[], float] = lambda: 0.0
    requests_left: Callable[[], int] = lambda: 1
    daily_spent: Callable[[], bool] = lambda: False
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.time
    max_wait_s: float = MAX_WAIT_S
    cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    hits: int = 0
    waits: list[tuple[str, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        for rec in _read_jsonl(self.cache_path):
            self.cache[rec["key"]] = rec

    @property
    def model(self) -> str:
        return str(self.inner.model)

    @property
    def cache_path(self) -> Path:
        return self.out / "cache.jsonl"

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

    def __call__(self, messages: list[dict[str, str]], /) -> dict[str, Any]:
        body = self.body(messages)
        key = _sha({"provider": self.provider, "body": body})
        msha = _sha(messages)
        if key in self.cache:
            self.hits += 1
            rec = self.cache[key]
            return {k: rec[k] for k in ("content", "ms", "tokens", "finish")}
        waited = 0.0
        while True:
            if self.requests_left() <= 0:
                raise RunHalted(f"{self.provider}: the run's request cap for today is reached")
            before = self.neurons()
            try:
                reply = self.inner(messages)
            except Exception as exc:
                if type(exc).__name__ != "AllProvidersUnavailable":
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
        call = self.inner.calls[-1]
        neurons = self.neurons() - before
        meta = {
            "model_id": call.model_id,
            "prompt_tokens": call.prompt_tokens,
            "completion_tokens": call.completion_tokens,
            "latency_ms": round(call.latency_ms, 2),
            "neurons": round(neurons, 4) if neurons else None,
        }
        self._log(outcome="ok", msha=msha, key=key, **meta)
        rec = {
            "key": key,
            "msha": msha,
            "content": reply["content"],
            "ms": reply["ms"],
            "tokens": reply["tokens"],
            "finish": reply["finish"],
            **meta,
        }
        self.cache[key] = rec
        _append_jsonl(self.cache_path, rec)
        return {k: rec[k] for k in ("content", "ms", "tokens", "finish")}

    @staticmethod
    def _kinds(exc: Exception) -> str:
        failures = getattr(exc, "failures", ())
        return ",".join(sorted({str(getattr(f, "kind", "?")) for f in failures})) or "?"

    def _on_unavailable(self, exc: Exception, msha: str) -> float:
        """Seconds to wait before re-sending; raises ``RunHalted`` or ``AttemptFailed``."""
        failures = list(getattr(exc, "failures", ()))
        if not failures:
            raise RunHalted(f"{self.provider}: no provider in the judge order")
        f = failures[0]
        kind = str(f.kind)
        detail = str(f.detail)
        retry = f.retry_after_s
        local = detail in {"local quota ledger refused", "circuit open", "unpinned"}
        if not local or detail == "unpinned":
            self._log(outcome=kind, msha=msha, detail=scrub(detail)[:300], retry_after_s=retry)
        if detail == "unpinned" or kind in {"config", "model_changed"}:
            raise RunHalted(f"{self.provider}: {kind} ({detail[:120]})")
        if kind == "quota" and (
            self.daily_spent() or (retry is not None and retry > DAILY_RETRY_S)
        ):
            raise RunHalted(f"{self.provider}: daily quota spent ({detail[:120]})")
        if detail == "circuit open" or kind in {"quota", "rate_limit"}:
            return (
                max(POLL_S, float(retry or 0.0))
                if detail != "local quota ledger refused"
                else POLL_S
            )
        # server, timeout: a failed attempt, as a transport error was for OpenAIChat.
        raise AttemptFailed(f"{self.provider} {kind}")


# ------------------------------------------------------------------------------ the verifier


@dataclass
class Verifier78:
    """The verifier repo's stop 78 pieces, imported read only."""

    root: Path
    live_check: ModuleType
    fb: ModuleType
    fe: ModuleType
    judge_mod: ModuleType


def load_verifier(root: Path = VERIFIER_ROOT) -> Verifier78:
    """Import ``scripts/live_check.py`` and its fresh2 modules without writing in ``root``.

    Raises:
        RuntimeError: the installed package is not 0.5.0, or the preset's prompt sha differs.
    """
    sys.dont_write_bytecode = True
    import legal_rag_verifier  # noqa: PLC0415
    from legal_rag_verifier import judge as judge_mod  # noqa: PLC0415

    if legal_rag_verifier.__version__ != VERIFIER_VERSION:
        raise RuntimeError(f"legal_rag_verifier {legal_rag_verifier.__version__} is installed")
    if judge_mod.PRESETS[PRESET_NAME].prompt_sha256 != judge_mod.SHORT_SHA:
        raise RuntimeError("the qwen27-ar4 prompt sha differs from SHORT_SHA")
    spec = importlib.util.spec_from_file_location(
        "_judge_gate_live_check", root / "scripts" / "live_check.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {root}/scripts/live_check.py")
    lc = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = lc
    spec.loader.exec_module(lc)
    fb, fe = lc._load("fresh2")
    return Verifier78(root=root, live_check=lc, fb=fb, fe=fe, judge_mod=judge_mod)


def answer_keys(v: Verifier78) -> list[str]:
    return sorted(v.fb.answers())


def run_answers(
    v: Verifier78, chat: Any, keys: Iterable[str]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``live_check._run_verifier`` with ``Judge(chat)`` in place of ``Judge.from_preset``.

    ``keys`` limits the answers checked (a subsample); order stays sorted, as measured.
    """
    jm = v.judge_mod
    preset = jm.PRESETS[PRESET_NAME]
    wanted = set(keys)
    rows: dict[str, Any] = {r["id"]: r for r in v.fb.rows()}
    built: dict[str, Any] = {}
    judge = jm.Judge(chat, reply=preset.reply)
    judge.preset = PRESET_NAME
    verifier = jm.JudgedVerifier(judge, jm.STRICT_FIGURES)
    if verifier.config != jm.STRICT_FIGURES:
        raise RuntimeError("verifier config is not STRICT_FIGURES")
    verdicts: dict[str, Any] = {}
    all_calls: list[dict[str, Any]] = []
    for answer_key, answer in sorted(v.fb.answers().items()):
        if answer_key not in wanted:
            continue
        row = rows[answer_key.split(":")[0]]
        if row["id"] not in built:
            built[row["id"]] = v.fb.premise(row)
        out = verifier.check_text(built[row["id"]], answer["text"], query=row["query"])
        verdicts |= {f"{answer_key}:{n}": x for n, x in enumerate(out)}
        all_calls.extend({**call, "answer_key": answer_key} for call in verifier.calls)
    return verdicts, all_calls


def score(
    v: Verifier78, verdicts: Mapping[str, Any], keys: Iterable[str]
) -> dict[str, dict[str, int]]:
    """``live_check._score_units`` on the answers in ``keys`` (all of them: the same call)."""
    lc, fe = v.live_check, v.fe
    wanted = set(keys)
    if wanted == set(answer_keys(v)):
        cells: dict[str, dict[str, int]] = lc._score_units(fe, verdicts)
        return cells
    us = [u for u in fe.units(merged=True) if f"{u['id']}:{u['mode']}" in wanted]
    graded = fe.merged_grades(us)
    units = [
        {**u, "right": graded[u["key"]] == "yes", "wrong": graded[u["key"]] == "no"} for u in us
    ]
    units = [u for u in units if u["right"] or u["wrong"]]
    out: dict[str, dict[str, int]] = {"total": lc._cell(units, verdicts)}
    for domain, name in lc.DOMAINS.items():
        out[name] = lc._cell([u for u in units if u["domain"] == domain], verdicts)
    return out


# ------------------------------------------------------------------------------ replay


class ReplayChat:
    """Answers each prompt with stop 78's recorded label for its sha (offline check)."""

    model = "replay:stop78"

    def __init__(self, calls: Sequence[Mapping[str, Any]], prompt_key: Callable[[str], str]):
        self.labels = {str(c["sha"]): (c["label"], c["passage"]) for c in calls}
        self.prompt_key = prompt_key
        self.missing = 0
        self.prompts: list[list[dict[str, str]]] = []

    def __call__(self, messages: list[dict[str, str]], /) -> dict[str, Any]:
        self.prompts.append(messages)
        got = self.labels.get(self.prompt_key(messages[1]["content"]))
        if got is None:
            self.missing += 1
            got = ("not_stated", 0)
        content = json.dumps({"label": got[0], "passage": got[1]})
        return {"content": content, "ms": 0.0, "tokens": 0, "finish": "stop"}


# ------------------------------------------------------------------------------ live runs


def _rotation(provider: str, ledger: Any) -> tuple[Any, Any, Any]:
    """A rotation whose judge order is ``provider`` alone, its provider, and the ledger."""
    from chapterverse.providers import ProviderRotation, RotationConfig  # noqa: PLC0415

    env = {**os.environ, "JUDGE_PROVIDERS": provider, "GEN_PROVIDERS": provider}
    config = RotationConfig.from_env(env)
    if provider not in config.judge:
        reasons = dict(config.skipped)
        raise RunHalted(f"{provider} is not configured: {reasons.get(provider, 'unknown')}")
    rotation = ProviderRotation.from_config(config, ledger=ledger)
    return rotation, rotation.providers[provider], ledger


def _body_fn(provider_obj: Any) -> Callable[[list[dict[str, str]]], dict[str, Any]]:
    from chapterverse.providers import judge_request  # noqa: PLC0415

    def body(messages: list[dict[str, str]]) -> dict[str, Any]:
        built, _ = provider_obj.build_body(judge_request(messages))
        return dict(built)

    return body


def settings_sent(provider_obj: Any) -> dict[str, Any]:
    """The request body minus the messages: what every judge call sends."""
    body = _body_fn(provider_obj)(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    )
    body.pop("messages", None)
    return body


def _today(log: Sequence[Mapping[str, Any]], provider: str, day: str) -> list[Mapping[str, Any]]:
    return [
        r
        for r in log
        if r.get("provider") == provider and r.get("day") == day and r.get("outcome") != "ping"
    ]


def groq_ledger(logs: Sequence[Path], now: float | None = None) -> Any:
    """An ``InMemoryLedger`` holding the Groq calls of the last 24 h from ``logs``.

    Each logged call is recorded at its own time (through the ledger's clock), so it counts
    in the rolling day window and has left the minute window; then the clock is real time.
    """
    from chapterverse.providers import CallUsage, InMemoryLedger  # noqa: PLC0415

    current = time.time() if now is None else now
    at: list[float | None] = [None]

    def clock() -> float:
        fixed = at[0]
        if fixed is not None:
            return fixed
        return time.time() if now is None else current

    ledger = InMemoryLedger(clock=clock)
    for path in logs:
        for r in _read_jsonl(path):
            t = float(r.get("t") or 0.0)
            if r.get("provider") != "groq" or r.get("outcome") != "ok" or t <= current - 86400:
                continue
            at[0] = t
            ledger.record(
                CallUsage(
                    provider="groq",
                    est_tokens=0,
                    prompt_tokens=int(r.get("prompt_tokens") or 0),
                    completion_tokens=int(r.get("completion_tokens") or 0),
                )
            )
    at[0] = None
    return ledger


def groq_sent_today(logs: Sequence[Path], day: str) -> int:
    """Groq requests logged on UTC ``day`` (pings, failures and successes) across ``logs``."""
    return sum(
        1
        for path in logs
        for r in _read_jsonl(path)
        if r.get("provider") == "groq" and r.get("day") == day
    )


def _groq_daily_spent(ledger: Any) -> bool:
    g = ledger.snapshot()["groq"]
    if g["requests_day"] >= g["requests_per_day"]:
        return True
    return bool(g["tokens_day"] + TOKEN_MARGIN > ledger.groq_limits.tokens_per_day)


def _seed_ledger(ledger: Any, provider: str, out: Path) -> tuple[int, float]:
    """Load today's requests and Neurons from the request log into the fresh ledger."""
    from chapterverse.providers import CallUsage  # noqa: PLC0415

    today = _today(_read_jsonl(out / "requests.jsonl"), provider, _utc_day())
    neurons = sum(float(r.get("neurons") or 0.0) for r in today)
    if provider == "cloudflare" and neurons:
        ledger.record(CallUsage(provider="cloudflare", est_tokens=0, neurons=neurons))
    # Groq: the response headers carry the day's remaining requests; the run's own cap is
    # counted from the request log (requests_left).
    return len(today), neurons


def _pings(provider_obj: Any, out: Path, provider: str) -> list[float]:
    path = out / "pings.json"
    if path.is_file():
        saved: list[float] = json.loads(path.read_text(encoding="utf-8"))
        return saved
    ms: list[float] = []
    for _ in range(PINGS):
        t0 = time.perf_counter()
        provider_obj.list_models()
        ms.append((time.perf_counter() - t0) * 1000.0)
        _append_jsonl(
            out / "requests.jsonl",
            {
                "provider": provider,
                "day": _utc_day(),
                "t": round(time.time(), 3),
                "outcome": "ping",
            },
        )
    _write_json(path, [round(m, 2) for m in ms])
    return ms


def plan_cloudflare(
    v: Verifier78, out: Path, budget: float, groq_out: Path | None, seed: int = SEED
) -> dict[str, Any]:
    """All answers if their estimated Neurons fit ``budget``, else a seeded subsample.

    The estimate per answer uses Groq's measured prompt and completion tokens for the same
    messages (same model), else the ledger's character estimate, times ``CF_MARGIN``.
    """
    from chapterverse.contracts import ChatMessage  # noqa: PLC0415
    from chapterverse.providers import estimate_tokens  # noqa: PLC0415

    path = out / "plan.json"
    if path.is_file():
        saved: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return saved
    measured: dict[str, tuple[int, int]] = {}
    if groq_out is not None:
        for r in _read_jsonl(groq_out / "cache.jsonl"):
            measured[r["msha"]] = (int(r["prompt_tokens"] or 0), int(r["completion_tokens"] or 0))
    keys = answer_keys(v)
    replay = ReplayChat([], v.judge_mod.prompt_key)
    _, calls = run_answers(v, replay, keys)
    cost: dict[str, float] = dict.fromkeys(keys, 0.0)
    for call, msgs in zip(calls, replay.prompts, strict=True):
        tin, tout = measured.get(_sha(msgs), (0, 0))
        if not tin:
            cm = [ChatMessage(role=m["role"], content=m["content"]) for m in msgs]  # type: ignore[typeddict-item]
            tin, tout = estimate_tokens(cm), 32
        cost[call["answer_key"]] += CF_MARGIN * (tin * CF_PER_M_IN + tout * CF_PER_M_OUT) / 1e6
    total = sum(cost.values())
    if total <= budget:
        chosen = keys
        how = "all answers fit"
    else:
        order = list(keys)
        random.Random(seed).shuffle(order)  # noqa: S311 - a reproducible sample
        chosen, spent = [], 0.0
        for key in order:
            if spent + cost[key] <= budget:
                chosen.append(key)
                spent += cost[key]
        chosen = sorted(chosen)
        how = f"seeded subsample, seed {seed}"
    plan = {
        "how": how,
        "budget_neurons": budget,
        "estimated_neurons_all": round(total, 1),
        "estimated_neurons_chosen": round(sum(cost[k] for k in chosen), 1),
        "chosen": chosen,
        "remaining": [k for k in keys if k not in set(chosen)],
    }
    _write_json(path, plan)
    return plan


def _summary(  # noqa: PLR0917 - one internal call site
    v: Verifier78,
    provider: str,
    model: str,
    settings: Mapping[str, Any],
    keys: Sequence[str],
    verdicts: Mapping[str, Any],
    calls: Sequence[Mapping[str, Any]],
    out: Path,
    pings: Sequence[float],
    extra: Mapping[str, Any],
) -> dict[str, Any]:
    lc = v.live_check
    cells = score(v, verdicts, keys)
    unique, every = lc._check_agreement(calls)
    log = [r for r in _read_jsonl(out / "requests.jsonl") if r.get("provider") == provider]
    sent = [r for r in log if r.get("outcome") != "ping"]
    ok = [r for r in sent if r.get("outcome") == "ok"]
    client = [float(c["ms"]) for c in calls]
    req_ms = [float(r["latency_ms"]) for r in ok]
    client_p50 = _p50(client) or 0.0
    models_p50 = _p50(list(pings)) or 0.0
    server_p50_s = (client_p50 - models_p50) / 1000.0
    total = cells["total"]
    full = len(keys) == len(answer_keys(v))
    errors = sum(1 for c in calls if c.get("label") == "error")
    a1_pass = lc.A1_CORRECT_MIN <= total["correct"] <= lc.A1_CORRECT_MAX if full else None
    return {
        "provider": provider,
        "model_id": model,
        "settings_sent": dict(settings),
        "complete": True,
        "answers": len(keys),
        "answers_total": len(answer_keys(v)),
        "cells": cells,
        "rates": {name: rates(cell) for name, cell in cells.items()},
        "rules": {
            "A1": {
                "passed": a1_pass,
                "correct": total["correct"],
                "units": total["units"],
                "threshold": f"{lc.A1_CORRECT_MIN} to {lc.A1_CORRECT_MAX} of 151",
                "note": None if full else "not applicable: a subsample",
            },
            "A2": {
                "passed": (errors / len(calls) if calls else 0.0) < lc.A2_MAX_ERROR_RATE,
                "error_calls": errors,
                "total_calls": len(calls),
            },
            "A3": {
                "passed": server_p50_s <= lc.A3_MAX_SERVER_P50_S,
                "server_side_p50_s": round(server_p50_s, 4),
                "client_p50_s": round(client_p50 / 1000.0, 4),
                "client_p95_s": round((_p95(client) or 0.0) / 1000.0, 4),
                "models_p50_s": round(models_p50 / 1000.0, 4),
                "threshold_s": lc.A3_MAX_SERVER_P50_S,
            },
        },
        "agreement": {"unique_prompts": unique, "all_calls": every},
        "calls_total": len(calls),
        "calls_unique": len({str(c["sha"]) for c in calls}),
        "attempts_over_1": sum(1 for c in calls if int(c.get("attempts") or 1) > 1),
        "requests": {
            "sent": len(sent),
            "ok": len(ok),
            "failures_by_kind": dict(
                Counter(str(r["outcome"]) for r in sent if r["outcome"] != "ok")
            ),
            "pings": len(log) - len(sent),
            "latency_ms_p50": _p50(req_ms),
            "latency_ms_p95": _p95(req_ms),
            "prompt_tokens": sum(int(r.get("prompt_tokens") or 0) for r in ok),
            "completion_tokens": sum(int(r.get("completion_tokens") or 0) for r in ok),
            "neurons": round(sum(float(r.get("neurons") or 0.0) for r in ok), 2),
            "by_day": dict(Counter(str(r["day"]) for r in sent)),
        },
        **extra,
    }


def cmd_run(args: argparse.Namespace) -> int:
    scope = getattr(args, "judge_scope", "all")
    if scope != "all":
        raise SystemExit(
            f"judge_gate run --judge-scope {scope} is not supported: fresh2 units carry no "
            "excerpt coordinate (unit fields: domain, id, key, mode, parts, prefix, query, "
            f"sentence). Use heldout --judge-scope {scope}."
        )
    provider: str = args.provider
    out: Path = args.out / provider
    out.mkdir(parents=True, exist_ok=True)
    v = load_verifier(args.verifier)
    from chapterverse.providers import InMemoryLedger, RotationJudgeChat  # noqa: PLC0415

    logs = [args.out / name / "requests.jsonl" for name in ("groq", "heldout-groq")]
    ledger = groq_ledger(logs) if provider == "groq" else InMemoryLedger()
    rotation, pobj, ledger = _rotation(provider, ledger)

    sent_today, neurons_today = _seed_ledger(ledger, provider, out)
    if provider == "cloudflare":
        plan = plan_cloudflare(v, out, args.budget - neurons_today, args.out / "groq")
        keys = list(plan["chosen"])
    else:
        plan = None
        keys = answer_keys(v)
    cap = args.max_requests
    day = _utc_day()

    def requests_left() -> int:
        if provider != "groq":
            return 1
        own = int(cap) - len(_today(_read_jsonl(out / "requests.jsonl"), provider, day))
        return min(own, GROQ_DAY_CAP - groq_sent_today(logs, day))

    def neurons() -> float:
        return float(ledger.snapshot()["cloudflare"]["neurons_day"])

    def daily_spent() -> bool:
        if provider == "groq":
            return _groq_daily_spent(ledger)
        return provider == "cloudflare"

    inner = RotationJudgeChat(rotation, model=pobj.model_id)
    chat = GateChat(
        provider=provider,
        inner=inner,
        body=_body_fn(pobj),
        out=out,
        neurons=neurons,
        requests_left=requests_left,
        daily_spent=daily_spent,
    )
    settings = settings_sent(pobj)
    print(
        json.dumps(
            {
                "provider": provider,
                "answers": len(keys),
                "cached": len(chat.cache),
                "sent_today": sent_today,
                "settings_sent": settings,
            }
        )
    )
    pings = _pings(pobj, out, provider)
    try:
        verdicts, calls = run_answers(v, chat, keys)
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
    with (out / "calls.jsonl").open("w", encoding="utf-8") as f:
        for call in calls:
            f.write(json.dumps(call, ensure_ascii=False) + "\n")
    extra = {
        "cache_hits_this_run": chat.hits,
        "waits": {
            "count": len(chat.waits),
            "seconds": round(sum(w for _, w in chat.waits), 1),
            "kinds": dict(Counter(k for k, _ in chat.waits)),
        },
        "plan": plan,
        "reference": json.loads((v.root / REFERENCE).read_text(encoding="utf-8"))["cells"],
    }
    summary = _summary(
        v, provider, pobj.model_id, settings, keys, verdicts, calls, out, pings, extra
    )
    _write_json(out / "summary.json", summary)
    _write_json(out / "state.json", {"complete": True, "day": day})
    print(json.dumps({k: summary[k] for k in ("cells", "rules", "agreement", "requests")}))
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    """Stop 78's labels through the same runner and counting: must give stop 78's cells."""
    scope = getattr(args, "judge_scope", "all")
    if scope != "all":
        raise SystemExit(
            f"judge_gate replay --judge-scope {scope} is not supported: fresh2 units carry no "
            "excerpt coordinate (unit fields: domain, id, key, mode, parts, prefix, query, "
            f"sentence). Use heldout --judge-scope {scope}."
        )
    v = load_verifier(args.verifier)
    calls78 = _read_jsonl(v.root / REFERENCE_CALLS)
    chat = ReplayChat(calls78, v.judge_mod.prompt_key)
    keys = answer_keys(v)
    verdicts, calls = run_answers(v, chat, keys)
    cells = score(v, verdicts, keys)
    unique, every = v.live_check._check_agreement(calls)
    ref = json.loads((v.root / REFERENCE).read_text(encoding="utf-8"))
    chars = [len(m[0]["content"]) + len(m[1]["content"]) for m in chat.prompts]
    result = {
        "cells": cells,
        "reference_cells": ref["cells"],
        "match": cells == ref["cells"],
        "calls": len(calls),
        "reference_calls": ref["calls_total"],
        "missing_labels": chat.missing,
        "agreement": {"unique_prompts": unique, "all_calls": every},
        "reference_agreement": {k: ref["agreement"][k] for k in ("unique_prompts", "all_calls")},
        "prompt_chars": {"p50": _p50(chars), "max": max(chars) if chars else 0},
        "rates": rates(cells["total"]),
    }
    _write_json(args.out / "replay.json", result)
    print(json.dumps(result))
    return 0 if result["match"] and result["calls"] == ref["calls_total"] else 1


# ------------------------------------------------------------------------------ held-out (B)


def load_run_battery(root: Path) -> ModuleType:
    """``scripts/run_battery.py``, imported read only (its counting and premise builder)."""
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(
        "_judge_gate_run_battery", root / "scripts" / "run_battery.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {root}/scripts/run_battery.py")
    rb = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = rb
    spec.loader.exec_module(rb)
    return rb


def check_seal(root: Path) -> dict[str, Any]:
    """The battery's sha256 equals the seal's and the pre-registered one; 346 held-out rows.

    Raises:
        RunHalted: any of these differs.
    """
    battery = (root / HELDOUT_BATTERY).read_bytes()
    digest = hashlib.sha256(battery).hexdigest()
    seal = json.loads((root / HELDOUT_SEAL).read_text(encoding="utf-8"))
    rows = [json.loads(x) for x in battery.decode().splitlines() if x.strip()]
    problems = []
    if digest != HELDOUT_SHA:
        problems.append(f"battery sha256 {digest}")
    if seal["battery"]["sha256"] != HELDOUT_SHA:
        problems.append("seal sha256 differs")
    if len(rows) != HELDOUT_ROWS or seal["rows"] != HELDOUT_ROWS:
        problems.append(f"{len(rows)} rows")
    if any(r.get("split") != "heldout" for r in rows):
        problems.append("a row is not split heldout")
    if problems:
        raise RunHalted("seal check failed: " + "; ".join(problems))
    return {"seal": seal["id"], "sha256": digest, "rows": len(rows)}


class SkipRow(Exception):  # noqa: N818 - control flow, not caught by Judge
    """A prefetch pass for one provider met a call routed to the other: skip this row."""


def heldout_route(messages: Sequence[Mapping[str, str]]) -> str:
    """Option 2's fixed rule: Cloudflare when the ledger's estimate of the call's first prompt
    (system and user turn) plus ``max_tokens`` exceeds Groq's 8,000 tokens a minute, else Groq.

    Decided from the prompt alone, before any call; a retry of a call (its first two messages
    unchanged) goes where the call went. Never from live outcomes.
    """
    from chapterverse.contracts import ChatMessage  # noqa: PLC0415
    from chapterverse.providers import estimate_tokens  # noqa: PLC0415
    from chapterverse.providers.presets import JUDGE_PRESET  # noqa: PLC0415

    first = [ChatMessage(role=m["role"], content=m["content"]) for m in messages[:2]]  # type: ignore[typeddict-item]
    need = estimate_tokens(first) + int(JUDGE_PRESET.sampling["max_tokens"])
    return "cloudflare" if need > GROQ_TPM else "groq"


@dataclass
class SizeRouter:
    """``Judge``'s chat for option 2: each call to ``chats[heldout_route(messages)]``.

    ``only`` (a prefetch pass) raises :class:`SkipRow` for a call routed elsewhere. ``used``
    maps each call's ``prompt_key`` to its provider.
    """

    chats: Mapping[str, Any]
    prompt_key: Callable[[str], str]
    only: str | None = None
    used: dict[str, str] = field(default_factory=dict)

    @property
    def model(self) -> str:
        return "groq|cloudflare"

    def __call__(self, messages: list[dict[str, str]], /) -> Any:
        provider = heldout_route(messages)
        self.used[self.prompt_key(messages[1]["content"])] = provider
        if self.only is not None and provider != self.only:
            raise SkipRow(provider)
        return self.chats[provider](messages)


def cited_windows_for_coordinate(premise: Any, excerpt_coordinate: str | None) -> tuple[int, ...]:
    """Passage positions matching ``excerpt_coordinate`` (equal, ancestor, or descendant)."""
    if not excerpt_coordinate:
        return ()
    return tuple(
        i
        for i, p in enumerate(premise.passages)
        if p.coordinate is not None
        and (
            p.coordinate == excerpt_coordinate
            or p.coordinate.startswith(excerpt_coordinate + "/")
            or excerpt_coordinate.startswith(p.coordinate + "/")
        )
    )


def section_windows_for_coordinate(premise: Any, excerpt_coordinate: str | None) -> tuple[int, ...]:
    """Passage positions whose section equals the section of ``excerpt_coordinate``."""
    if not excerpt_coordinate:
        return ()
    from chapterverse.sections import section_coordinate  # noqa: PLC0415

    target = section_coordinate(excerpt_coordinate)
    return tuple(
        i
        for i, p in enumerate(premise.passages)
        if p.coordinate is not None and section_coordinate(p.coordinate) == target
    )


def run_heldout(
    v: Verifier78,
    rb: ModuleType,
    chat: Any,
    layer_root: Path = SEALED_LAYER,
    *,
    skip_ok: bool = False,
    judge_scope: str = "all",
    stats: dict[str, int] | None = None,
) -> tuple[list[Any], list[dict[str, Any]], list[dict[str, Any]], set[str | None], list[str]]:
    """``run_battery.run`` with ``JudgedVerifier`` as the verifier, reset before every row.

    Each row is one sentence with no answer before it, so the judge's prefix is empty.
    ``skip_ok``: a row whose call raises :class:`SkipRow` is listed and skipped.
    """
    if judge_scope not in {"all", "cited", "section"}:
        raise ValueError(f"unknown judge scope {judge_scope!r}")
    if judge_scope != "all":
        from chapterverse.audit.verify import cited_nli_scorer  # noqa: PLC0415

    jm = v.judge_mod
    rows = rb.load_rows(v.root / HELDOUT_BATTERY)
    built = rb.premises_for(rows, v.root / "data", layer_root / HELDOUT_ENRICHMENT)
    enrichment = {p.enrichment for p in built.values()}
    if enrichment != {HELDOUT_ENRICHMENT_ID}:
        raise RunHalted(f"enrichment layer {sorted(map(str, enrichment))}, not the sealed one")
    judge = jm.Judge(chat, reply=jm.PRESETS[PRESET_NAME].reply)
    judge.preset = PRESET_NAME
    verifier = jm.JudgedVerifier(judge, jm.STRICT_FIGURES)
    if verifier.config != jm.STRICT_FIGURES:
        raise RuntimeError("verifier config is not STRICT_FIGURES")
    outcomes: list[Any] = []
    raw: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    skipped: list[str] = []
    fallback_count = 0
    for row in rows:
        verifier.reset()
        premise = built[rb.premises.premise_key(row)]
        if judge_scope != "all":
            selector = (
                cited_windows_for_coordinate
                if judge_scope == "cited"
                else section_windows_for_coordinate
            )
            kept = selector(premise, row.get("excerpt_coordinate"))
            if not kept:
                fallback_count += 1
            else:
                verifier.nli = cited_nli_scorer(
                    judge, premise, row["sentence"], kept, query=row["query"]
                )
        try:
            verdict = verifier.check_sentence(premise, row["sentence"], query=row["query"])
        except SkipRow:
            if not skip_ok:
                raise
            skipped.append(row["id"])
            continue
        finally:
            verifier.nli = judge
        outcomes.append(
            rb.Outcome(
                row["id"],
                row["class"],
                row["expected"],
                verdict.verdict.value,
                tuple(r.value for r in verdict.reasons),
            )
        )
        raw.append({"id": row["id"], **verdict.to_dict()})
        calls.extend({**c, "row": row["id"]} for c in verifier.calls)
    if stats is not None:
        stats["cited_fallback"] = fallback_count
    return outcomes, raw, calls, enrichment, skipped


def heldout_counts(rb: ModuleType, outcomes: Sequence[Any]) -> dict[str, Any]:
    """``run_battery.summarise`` plus the counts behind it, with CP 95 % intervals."""
    passes = [o for o in outcomes if o.cls in rb.PASS_CLASSES]
    failures = [o for o in outcomes if o.cls not in rb.PASS_CLASSES]
    caught = sum(o.verdict == "ROLLBACK" for o in failures)
    false = sum(o.verdict == "ROLLBACK" for o in passes)
    gp = [o for o in passes if o.cls == "grounded_paraphrase"]
    by_class: dict[str, list[Any]] = {}
    for o in outcomes:
        by_class.setdefault(o.cls, []).append(o)
    return {
        "rows": len(outcomes),
        "summarise": rb.summarise(outcomes) if outcomes else None,
        "failure_recall": _rate(caught, len(failures)),
        "pass_false_rollback": _rate(false, len(passes)),
        "grounded_paraphrase_false_rollback": _rate(
            sum(o.verdict == "ROLLBACK" for o in gp), len(gp)
        ),
        "rollback_precision": _rate(caught, caught + false),
        "classes": {
            cls: _rate(sum(o.verdict == "ROLLBACK" for o in rows), len(rows))
            for cls, rows in sorted(by_class.items())
        },
    }


def recorded_outcomes(rb: ModuleType, root: Path, detector: str) -> list[Any]:
    """The sealed DeBERTa runs' per-row verdicts (``results/raw``, read only) as Outcomes."""
    rows = {r["id"]: r for r in rb.load_rows(root / HELDOUT_BATTERY)}
    path = root / "results" / "raw" / f"heldout_draft-{detector}-sealed.jsonl"
    return [
        rb.Outcome(
            r["id"],
            rows[r["id"]]["class"],
            rows[r["id"]]["expected"],
            r["verdict"],
            tuple(r.get("reasons") or ()),
        )
        for r in _read_jsonl(path)
    ]


class _CountingChat:
    """Offline: answers not_stated and keeps each prompt, to count calls before any send."""

    model = "dry"

    def __init__(self) -> None:
        self.prompts: list[list[dict[str, str]]] = []

    def __call__(self, messages: list[dict[str, str]], /) -> dict[str, Any]:
        self.prompts.append(messages)
        return {"content": '{"label": "not_stated", "passage": 0}', "ms": 0.0, "tokens": 0}


def _cf_ledger(logs: Sequence[Path]) -> Any:
    """An ``InMemoryLedger`` holding today's Cloudflare Neurons from ``logs``."""
    from chapterverse.providers import CallUsage, InMemoryLedger  # noqa: PLC0415

    ledger = InMemoryLedger()
    day = _utc_day()
    spent = sum(
        float(r.get("neurons") or 0.0)
        for path in logs
        for r in _read_jsonl(path)
        if r.get("provider") == "cloudflare" and r.get("day") == day
    )
    if spent:
        ledger.record(CallUsage(provider="cloudflare", est_tokens=0, neurons=spent))
    return ledger


def _request_stats(out: Path, provider: str) -> dict[str, Any]:
    log = [r for r in _read_jsonl(out / "requests.jsonl") if r.get("provider") == provider]
    ok = [r for r in log if r.get("outcome") == "ok"]
    req_ms = [float(r["latency_ms"]) for r in ok]
    return {
        "sent": len(log),
        "ok": len(ok),
        "failures_by_kind": dict(Counter(str(r["outcome"]) for r in log if r["outcome"] != "ok")),
        "latency_ms_p50": _p50(req_ms),
        "latency_ms_p95": _p95(req_ms),
        "prompt_tokens": sum(int(r.get("prompt_tokens") or 0) for r in ok),
        "completion_tokens": sum(int(r.get("completion_tokens") or 0) for r in ok),
        "neurons": round(sum(float(r.get("neurons") or 0.0) for r in ok), 2),
        "by_day": dict(Counter(str(r["day"]) for r in log)),
    }


def cmd_heldout(args: argparse.Namespace) -> int:  # noqa: PLR0915 - one linear procedure
    """Option B (option 2 routing). ``--dry`` sends nothing; a live run needs ``--method-commit``.

    ``--only groq`` or ``--only cloudflare`` is a prefetch pass: rows with a call routed to the
    other provider are skipped (the rows are independent). The pass without ``--only`` scores.
    """
    v = load_verifier(args.verifier)
    rb = load_run_battery(args.verifier)
    seal = check_seal(args.verifier)
    scope = getattr(args, "judge_scope", "all")
    out: Path = args.out / "heldout" if scope == "all" else args.out / "heldout" / scope
    stats: dict[str, int] = {}
    if args.dry:
        counter = _CountingChat()
        router = SizeRouter({"groq": counter, "cloudflare": counter}, v.judge_mod.prompt_key)
        _, _, calls, enrichment, _ = run_heldout(v, rb, router, judge_scope=scope, stats=stats)
        oversized = [
            {"row": c["row"], "call_sha": c["sha"]}
            for c in calls
            if router.used[c["sha"]] == "cloudflare"
        ]
        if args.list_oversized is not None:
            _write_json(args.list_oversized, oversized)
        dry_out: dict[str, Any] = {
            **seal,
            "judge_calls": len(calls),
            "rows_with_a_call": len({c["row"] for c in calls}),
            "to_groq": sum(1 for c in calls if router.used[c["sha"]] == "groq"),
            "to_cloudflare": len(oversized),
            "rows_with_a_cloudflare_call": len({o["row"] for o in oversized}),
            "enrichment": sorted(str(e) for e in enrichment),
        }
        if scope != "all":
            dry_out["judge_scope"] = scope
            dry_out["cited_fallback"] = stats.get("cited_fallback", 0)
        print(json.dumps(dry_out))
        return 0
    if not args.method_commit:
        raise SystemExit("a live held-out run needs --method-commit (the committed method)")
    done = out / f"{seal['seal']}-{HELDOUT_DETECTOR}.done"
    if done.exists():
        raise SystemExit(f"{seal['seal']} has already run with {HELDOUT_DETECTOR} ({done})")
    started = out / f"{seal['seal']}-{HELDOUT_DETECTOR}.started"
    if not started.exists():
        _write_json(
            started,
            {
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "method_commit": args.method_commit,
                **seal,
            },
        )
    from chapterverse.providers import RotationJudgeChat  # noqa: PLC0415

    day = _utc_day()
    groq_logs = [args.out / "groq" / "requests.jsonl", out / "groq" / "requests.jsonl"]
    cf_logs = [args.out / "cloudflare" / "requests.jsonl", out / "cloudflare" / "requests.jsonl"]
    g_ledger = groq_ledger(groq_logs)
    g_rot, g_obj, _ = _rotation("groq", g_ledger)
    c_ledger = _cf_ledger(cf_logs)
    c_rot, c_obj, _ = _rotation("cloudflare", c_ledger)
    chats = {
        "groq": GateChat(
            provider="groq",
            inner=RotationJudgeChat(g_rot, model=g_obj.model_id),
            body=_body_fn(g_obj),
            out=out / "groq",
            requests_left=lambda: GROQ_DAY_CAP - groq_sent_today(groq_logs, day),
            daily_spent=lambda: _groq_daily_spent(g_ledger),
        ),
        "cloudflare": GateChat(
            provider="cloudflare",
            inner=RotationJudgeChat(c_rot, model=c_obj.model_id),
            body=_body_fn(c_obj),
            out=out / "cloudflare",
            neurons=lambda: float(c_ledger.snapshot()["cloudflare"]["neurons_day"]),
            daily_spent=lambda: True,
        ),
    }
    router = SizeRouter(chats, v.judge_mod.prompt_key, only=args.only)
    settings = {"groq": settings_sent(g_obj), "cloudflare": settings_sent(c_obj)}
    try:
        outcomes, raw, calls, enrichment, skipped = run_heldout(
            v, rb, router, skip_ok=args.only is not None, judge_scope=scope, stats=stats
        )
    except RunHalted as exc:
        state = {
            "complete": False,
            "halted": str(exc),
            "cached": {k: len(c.cache) for k, c in chats.items()},
            "day": day,
        }
        _write_json(out / "state.json", state)
        print(json.dumps(state))
        return 3
    if args.only is not None:
        state = {
            "complete": False,
            "prefetched": args.only,
            "skipped_rows": len(skipped),
            "cached": {k: len(c.cache) for k, c in chats.items()},
            "day": day,
        }
        _write_json(out / "state.json", state)
        print(json.dumps(state))
        return 0
    row_provider = {c["row"]: "cloudflare" for c in calls if router.used[c["sha"]] == "cloudflare"}
    groq_rows = {o.row_id for o in outcomes if o.row_id not in row_provider}
    on = {
        "groq": [o for o in outcomes if o.row_id in groq_rows],
        "cloudflare": [o for o in outcomes if o.row_id not in groq_rows],
    }
    client = [float(c["ms"]) for c in calls]
    secondary = {
        name: heldout_counts(
            rb, [o for o in recorded_outcomes(rb, v.root, name) if o.row_id in groq_rows]
        )
        for name in ("base", "none")
    }
    summary = {
        **seal,
        "detector": HELDOUT_DETECTOR,
        "method_commit": args.method_commit,
        "model_ids": {"groq": g_obj.model_id, "cloudflare": c_obj.model_id},
        "settings_sent": settings,
        "enrichment": sorted(str(e) for e in enrichment),
        "counts": heldout_counts(rb, outcomes),
        "by_provider": {name: heldout_counts(rb, rows) for name, rows in on.items()},
        "secondary_groq_rows": {"qwen": heldout_counts(rb, on["groq"]), **secondary},
        "calls_by_provider": dict(Counter(router.used[c["sha"]] for c in calls)),
        "judge_calls": len(calls),
        "judge_errors": sum(1 for c in calls if c.get("label") == "error"),
        "judge_errors_by_provider": dict(
            Counter(router.used[c["sha"]] for c in calls if c.get("label") == "error")
        ),
        "attempts_over_1": sum(1 for c in calls if int(c.get("attempts") or 1) > 1),
        "client_ms_p50": _p50(client),
        "client_ms_p95": _p95(client),
        "requests": {
            "groq": _request_stats(out / "groq", "groq"),
            "cloudflare": _request_stats(out / "cloudflare", "cloudflare"),
        },
        **(
            {"judge_scope": scope, "cited_fallback": stats.get("cited_fallback", 0)}
            if scope != "all"
            else {}
        ),
    }
    with (out / "raw.jsonl").open("w", encoding="utf-8") as f:
        for r in raw:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (out / "calls.jsonl").open("w", encoding="utf-8") as f:
        for c in calls:
            f.write(json.dumps({**c, "provider": router.used[c["sha"]]}, ensure_ascii=False) + "\n")
    _write_json(out / "summary.json", summary)
    _write_json(done, {"at": datetime.now(UTC).isoformat(timespec="seconds")})
    print(json.dumps(summary["counts"]["summarise"]["headline"]))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verifier", type=Path, default=VERIFIER_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--judge-scope", choices=["all", "cited", "section"], default="all")
    sub = parser.add_subparsers(dest="cmd", required=True)
    rep = sub.add_parser("replay")
    rep.add_argument(
        "--judge-scope", choices=["all", "cited", "section"], default=argparse.SUPPRESS
    )
    run = sub.add_parser("run")
    run.add_argument("--provider", choices=["groq", "cloudflare"], required=True)
    run.add_argument("--max-requests", type=int, default=GROQ_REQUEST_CAP)
    run.add_argument("--budget", type=float, default=CLOUDFLARE_NEURONS)
    run.add_argument(
        "--judge-scope", choices=["all", "cited", "section"], default=argparse.SUPPRESS
    )
    held = sub.add_parser("heldout")
    held.add_argument("--dry", action="store_true", help="count calls offline; send nothing")
    held.add_argument("--method-commit", default="", help="commit of the pre-registered method")
    held.add_argument("--only", choices=["groq", "cloudflare"], default=None)
    held.add_argument("--list-oversized", type=Path, default=None, help="--dry: write the list")
    held.add_argument(
        "--judge-scope", choices=["all", "cited", "section"], default=argparse.SUPPRESS
    )
    args = parser.parse_args(argv)
    if args.cmd == "replay":
        return cmd_replay(args)
    if args.cmd == "heldout":
        return cmd_heldout(args)
    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
