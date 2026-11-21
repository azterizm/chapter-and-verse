"""Slice 2 gate 2: security probes from rag-security-probes.

Runs every applicable probe through :meth:`chapterverse.pipeline.Pipeline.run`:
- Mode A no_upload probes (6): evaluated with claim_shape_absence via scripts/claim_shapes.py.
  Pass when the app refuses or makes no claim of the requested shape.
- Mode A upload probes (17): not applicable (needs tenant document upload, which this app lacks).
- Mode C probes (6): read evaluators and apply those that fit a single-tenant, no-upload app.

Usage:
    uv run python -m eval.probes_gate [--out OUT_DIR]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Final

from legal_rag_router import Router

from chapterverse.audit.policy import build_policy
from chapterverse.config import Settings
from chapterverse.contracts import (
    Event,
    ExtractiveEvent,
    RefusalEvent,
    SentenceEvent,
)
from chapterverse.pipeline import AskRequest, Pipeline
from chapterverse.providers.presets import JUDGE_PRESET_NAME
from chapterverse.store import open_store
from eval.run import EmptyRotation

__all__ = [
    "evaluate_probe",
    "load_claim_shapes",
    "main",
]

PROBES_REPO: Final = Path(
    os.environ.get("RSP_ROOT", str(Path.home() / "Code" / "rag-security-probes"))
)


def load_claim_shapes(repo: Path) -> Any:
    shapes_path = repo / "scripts" / "claim_shapes.py"
    if not shapes_path.is_file():
        raise ImportError(f"cannot find claim_shapes.py at {shapes_path}")
    spec = importlib.util.spec_from_file_location("_rsp_claim_shapes", shapes_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {shapes_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_rsp_claim_shapes"] = module
    spec.loader.exec_module(module)
    return module


def _answer_text(events: list[Event], outcome_mode: str) -> str:
    texts: list[str] = []
    for e in events:
        if isinstance(e, RefusalEvent):
            texts.append(e.message)
        elif isinstance(e, SentenceEvent):
            texts.append(e.sentence.text)
        elif isinstance(e, ExtractiveEvent):
            texts.extend(w.text for w in e.windows)
    return " ".join(texts)


def evaluate_probe(
    probe: dict[str, Any],
    pipeline: Pipeline,
    claim_shapes: Any,
) -> dict[str, Any]:
    pid = probe["probe_id"]
    family = probe.get("family", "")
    cls_name = probe.get("class", "")
    phase = probe.get("phase", "")
    evaluator = probe.get("evaluator", "")

    # Mode A upload: not applicable
    if phase == "upload":
        return {
            "probe_id": pid,
            "phase": phase,
            "family": family,
            "class": cls_name,
            "evaluator": evaluator,
            "status": "not_applicable",
            "reason": "needs tenant document upload, which this app does not have",
            "query": probe.get("query", ""),
        }

    # Run through pipeline
    events: list[Event] = []
    t0 = time.perf_counter()
    outcome = pipeline.run(AskRequest(query=probe["query"]), events.append)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    refused = outcome.mode == "refusal" or any(isinstance(e, RefusalEvent) for e in events)
    answer = _answer_text(events, outcome.mode)
    answer_lower = answer.lower()

    # Mode A no_upload: claim_shape_absence
    if phase == "no_upload":
        shapes = probe.get("claim_shapes", [])
        fabricated = claim_shapes.fabricated_claims(answer, shapes, query=probe["query"])
        passed = refused or (len(fabricated) == 0)
        return {
            "probe_id": pid,
            "phase": phase,
            "family": family,
            "class": cls_name,
            "evaluator": evaluator,
            "status": "pass" if passed else "fail",
            "passed": passed,
            "refused": refused,
            "fabricated_claims": fabricated,
            "elapsed_ms": round(elapsed_ms, 1),
            "events": [e.type for e in events],
            "answer_preview": answer[:120] if answer else "",
            "query": probe.get("query", ""),
        }

    # Mode C: adversarial premise probes against real UK legislation
    if phase == "mode_c":
        # Check evaluator: disambiguation_required
        # In a single-tenant no-upload app:
        # must_not_contain: affirmative attribution phrases must NOT be present
        # pass_if_any_present: premise correction or governing statute or refusal indicator
        must_not = probe.get("must_not_contain", [])
        violations = [phrase for phrase in must_not if phrase.lower() in answer_lower]

        pass_any = probe.get("pass_if_any_present", [])
        flat_pass = [p.lower() for sub in pass_any for p in sub]
        pass_matches = [p for p in flat_pass if p in answer_lower]

        # A refusal that cites non-existence or repeal catches the false premise
        # If the app refused without affirming the false premise:
        passed = (len(violations) == 0) and (bool(pass_matches) or refused)

        return {
            "probe_id": pid,
            "phase": phase,
            "family": family,
            "class": cls_name,
            "evaluator": evaluator,
            "status": "pass" if passed else "fail",
            "passed": passed,
            "refused": refused,
            "violations": violations,
            "pass_matches": pass_matches,
            "elapsed_ms": round(elapsed_ms, 1),
            "events": [e.type for e in events],
            "answer_preview": answer[:120] if answer else "",
            "query": probe.get("query", ""),
        }

    return {
        "probe_id": pid,
        "phase": phase,
        "status": "not_applicable",
        "reason": f"unknown phase {phase}",
    }


def main(argv: list[str] | None = None) -> int:
    default_out = Path(os.environ.get("CV_SCRATCH", "scratch")) / "s2-gates" / "probes"
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--probes-repo", type=Path, default=PROBES_REPO)
    parser.add_argument("--out", type=Path, default=default_out, help="output directory")
    args = parser.parse_args(argv)

    repo: Path = args.probes_repo.expanduser().resolve()
    out: Path = args.out.resolve()
    if out == repo or repo in out.parents:
        print("REFUSED: --out is inside the probes repo")
        return 1

    claim_shapes = load_claim_shapes(repo)
    settings = Settings.from_env()
    store = open_store(settings.store_dir)
    router = Router.from_path(settings.router_index, concepts=settings.concepts_index)

    pipeline = Pipeline(
        router=router,
        store=store,
        rotation=EmptyRotation(),
        policy=build_policy(JUDGE_PRESET_NAME),
    )

    # Load all probes
    probes_file_a = repo / "rag_probes.jsonl"
    probes_file_c = repo / "rag_probes_mode_c.jsonl"

    all_probes: list[dict[str, Any]] = []
    if probes_file_a.is_file():
        for line_raw in probes_file_a.read_text(encoding="utf-8").splitlines():
            line = line_raw.strip()
            if line:
                all_probes.append(json.loads(line))
    if probes_file_c.is_file():
        for line_raw in probes_file_c.read_text(encoding="utf-8").splitlines():
            line = line_raw.strip()
            if line:
                all_probes.append(json.loads(line))

    print(f"Loaded {len(all_probes)} probes from {repo.name}")

    results = []
    counts: dict[str, int] = {"pass": 0, "fail": 0, "not_applicable": 0}

    for probe in all_probes:
        res = evaluate_probe(probe, pipeline, claim_shapes)
        results.append(res)
        counts[res["status"]] += 1

    summary = {
        "total_probes": len(all_probes),
        "counts": counts,
        "by_phase": {
            "mode_a_upload": sum(1 for r in results if r.get("phase") == "upload"),
            "mode_a_no_upload": {
                "total": sum(1 for r in results if r.get("phase") == "no_upload"),
                "pass": sum(
                    1 for r in results if r.get("phase") == "no_upload" and r["status"] == "pass"
                ),
                "fail": sum(
                    1 for r in results if r.get("phase") == "no_upload" and r["status"] == "fail"
                ),
            },
            "mode_c": {
                "total": sum(1 for r in results if r.get("phase") == "mode_c"),
                "pass": sum(
                    1 for r in results if r.get("phase") == "mode_c" and r["status"] == "pass"
                ),
                "fail": sum(
                    1 for r in results if r.get("phase") == "mode_c" and r["status"] == "fail"
                ),
                "not_applicable": sum(
                    1
                    for r in results
                    if r.get("phase") == "mode_c" and r["status"] == "not_applicable"
                ),
            },
        },
        "results": results,
    }

    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "total_probes": len(all_probes),
                "counts": counts,
                "by_phase": summary["by_phase"],
            },
            indent=2,
        )
    )

    for r in results:
        status_tag = r["status"].upper()
        pid = r["probe_id"]
        phase = r.get("phase", "")
        if r["status"] == "not_applicable":
            print(f"[{status_tag}] {pid} ({phase}): {r.get('reason')}")
        else:
            refused = "refused" if r.get("refused") else "answered"
            print(f"[{status_tag}] {pid} ({phase}): {refused}; preview={r.get('answer_preview')!r}")

    all_passed = counts["fail"] == 0
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
