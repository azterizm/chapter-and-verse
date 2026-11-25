"""Record real-engine SSE streams into ``web/mock/streams/engine/``.

Runs a fixed set of questions in-process through ``chapterverse.app.build_runtime`` with
``DATABASE_URL`` and all Modal settings removed from the environment (Groq and Cloudflare only).
Writes each stream's exact SSE bytes via ``chapterverse.contracts.encode_sse`` to
``web/mock/streams/engine/<name>.sse`` and writes ``web/mock/streams/engine/index.json``.

Usage (from repo root):
    uv run python web/mock/record_engine.py [--only NAME] [--pace SECONDS]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from chapterverse.app import build_runtime
from chapterverse.contracts import Event, encode_sse, validate_order
from chapterverse.pipeline import AskRequest

DEFAULT_OUT: Final = Path(__file__).resolve().parent / "streams" / "engine"
DEFAULT_PACE_S: Final = 20.0
PROVIDERS: Final = ("groq", "cloudflare")


@dataclass(frozen=True, slots=True)
class QuestionSpec:
    """One recorded question and the mock keywords that trigger it."""

    name: str
    question: str
    keywords: tuple[str, ...]


QUESTIONS: Final[tuple[QuestionSpec, ...]] = (
    QuestionSpec(
        name="tort",
        question=(
            "What is the limitation period for bringing a claim in tort under the "
            "Limitation Act 1980?"
        ),
        keywords=("limitation period", "limitation act 1980"),
    ),
    QuestionSpec(
        name="tort-clarify",
        question="How long do I have to sue in tort?",
        keywords=("sue in tort",),
    ),
    QuestionSpec(
        name="tort-chosen",
        question="How long do I have to sue in tort? (s.2 Limitation Act 1980)",
        keywords=("s.2 limitation act 1980",),
    ),
    QuestionSpec(
        name="quote-172",
        question=(
            "Quote the exact statutory wording of section 172(1)(a) to (d) of the "
            "Companies Act 2006."
        ),
        keywords=("172(1)", "s.172", "companies act 2006"),
    ),
    QuestionSpec(
        name="reject-30",
        question="How long is the short-term right to reject under the Consumer Rights Act 2015?",
        keywords=("short-term right to reject", "consumer rights act 2015"),
    ),
    QuestionSpec(
        name="era-124",
        question=(
            "What is the limit of the compensatory award under the Employment Rights Act 1996?"
        ),
        keywords=("compensatory award",),
    ),
    QuestionSpec(
        name="not-in-act",
        question=(
            "What is the national minimum wage hourly rate under the Employment Rights Act 1996?"
        ),
        keywords=("national minimum wage",),
    ),
    QuestionSpec(
        name="out-of-coverage",
        question="Code civil article 1240",
        keywords=("code civil",),
    ),
)

QUESTIONS_BY_NAME: Final[dict[str, QuestionSpec]] = {q.name: q for q in QUESTIONS}


def _strip_modal_providers(raw: str | None) -> str:
    if not raw:
        return ",".join(PROVIDERS)
    kept = [
        p.strip().lower() for p in raw.split(",") if p.strip() and p.strip().lower() in PROVIDERS
    ]
    return ",".join(kept) if kept else ",".join(PROVIDERS)


def clean_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an env copy with DATABASE_URL and Modal settings removed (Groq/Cloudflare only)."""
    source = os.environ if environ is None else environ
    env = {k: v for k, v in source.items() if k != "DATABASE_URL" and not k.startswith("MODAL_")}
    env["GEN_PROVIDERS"] = _strip_modal_providers(env.get("GEN_PROVIDERS"))
    env["JUDGE_PROVIDERS"] = _strip_modal_providers(env.get("JUDGE_PROVIDERS"))
    # Local offline recording does not need a client IP cap salt.
    env["DAILY_ASK_CAP"] = "0"
    return env


def record_question(runtime: Any, question: str) -> bytes:
    """Run one question through ``runtime.pipeline.run`` and return exact SSE bytes."""
    events: list[Event] = []
    chunks: list[bytes] = []

    def emit(event: Event) -> None:
        events.append(event)
        chunks.append(encode_sse(event))

    runtime.pipeline.run(AskRequest(query=question), emit)
    types = [e.type for e in events]
    if not validate_order(types):
        raise RuntimeError(f"illegal event order for {question!r}: {types}")
    return b"".join(chunks)


def _load_existing_index(index_path: Path) -> dict[str, dict[str, Any]]:
    if not index_path.is_file():
        return {}
    try:
        raw = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}


def record_all(
    *,
    only: str | None = None,
    pace: float = DEFAULT_PACE_S,
    out_dir: Path = DEFAULT_OUT,
    environ: Mapping[str, str] | None = None,
    runtime_factory: Callable[[Mapping[str, str]], Any] = build_runtime,
    sleep: Callable[[float], None] = time.sleep,
    now_utc: Callable[[], datetime] | None = None,
) -> dict[str, dict[str, Any]]:
    """Record selected questions to ``out_dir`` and write ``index.json``."""
    if pace < 0:
        raise ValueError("pace must be non-negative")
    if only is not None:
        if only not in QUESTIONS_BY_NAME:
            valid = ", ".join(q.name for q in QUESTIONS)
            raise ValueError(f"unknown scenario {only!r}; expected one of: {valid}")
        selected = (QUESTIONS_BY_NAME[only],)
    else:
        selected = QUESTIONS

    clock = now_utc or (lambda: datetime.now(UTC))
    env = clean_env(environ)
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = out_dir / "index.json"
    index = _load_existing_index(index_path) if only is not None else {}

    runtime = runtime_factory(env)
    try:
        policy_hash = str(runtime.pipeline.policy.policy_hash)
        for idx, spec in enumerate(selected):
            if idx > 0 and pace > 0:
                sleep(pace)
            sse_bytes = record_question(runtime, spec.question)
            (out_dir / f"{spec.name}.sse").write_bytes(sse_bytes)
            index[spec.name] = {
                "question": spec.question,
                "keywords": list(spec.keywords),
                "recorded_at": clock().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "policy_hash": policy_hash,
            }
    finally:
        runtime.shutdown()

    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record real-engine mock SSE streams.")
    parser.add_argument(
        "--only",
        default=None,
        help="Record only the named question (e.g. tort, era-124).",
    )
    parser.add_argument(
        "--pace",
        type=float,
        default=DEFAULT_PACE_S,
        help="Seconds to sleep between questions (default: 20).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="Output directory (default: web/mock/streams/engine).",
    )
    args = parser.parse_args(argv)
    try:
        index = record_all(only=args.only, pace=args.pace, out_dir=args.out)
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    sys.stdout.write(f"recorded {len(index)} stream(s) to {args.out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
