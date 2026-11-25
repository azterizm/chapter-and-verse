"""Build the clarify ranking prior artefact from the router's harvest and batteries.

Usage:
    uv run python scripts/build_clarify_prior.py [--router-repo PATH] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Final

import orjson
from legal_rag_verifier.corpus import instrument_coordinate

DEFAULT_ROUTER_REPO: Final = Path(
    os.environ.get("LRR_ROOT", str(Path.home() / "Code" / "legal-rag-router"))
)
DEFAULT_OUT: Final = Path("artefacts/clarify_prior.json")
_YEAR_PART_INDEX: Final = 2
_CALENDAR_INST_PARTS: Final = 4
_REGNAL_INST_PARTS: Final = 5


def _section_coordinate(coord: str) -> str:
    parts = coord.split("/")
    base = (
        _CALENDAR_INST_PARTS
        if (len(parts) > _YEAR_PART_INDEX and parts[_YEAR_PART_INDEX].isdigit())
        else _REGNAL_INST_PARTS
    )
    if len(parts) <= base:
        return coord
    if parts[base].startswith("sch") and len(parts) > base + 1:
        return "/".join(parts[: base + 2])
    return "/".join(parts[: base + 1])


def _collect_section_citers(harvest_path: Path) -> dict[str, int]:
    sec_citers: dict[str, set[str]] = defaultdict(set)
    with harvest_path.open("rb") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = orjson.loads(line)
            if row.get("in_commentary") or row.get("in_amendment"):
                continue
            target = row.get("target_coordinate")
            if not target:
                continue
            t_coord = str(target)
            t_inst = instrument_coordinate(t_coord)
            src = row.get("source_coordinate") or ""
            s_inst = instrument_coordinate(str(src)) if src else ""
            if not s_inst or s_inst == t_inst:
                continue
            t_sec = _section_coordinate(t_coord)
            if t_sec != t_inst:
                sec_citers[t_sec].add(s_inst)
    return {k: len(v) for k, v in sorted(sec_citers.items()) if v}


def _collect_battery_instruments(batteries_dir: Path) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for path in sorted(batteries_dir.glob("*.jsonl")):
        with path.open("rb") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = orjson.loads(line)
                if row.get("split") == "test":
                    continue
                for coord in row.get("expected_coordinates") or ():
                    counts[instrument_coordinate(str(coord))] += 1
    return dict(sorted(counts.items()))


def build_prior(router_repo: Path) -> dict[str, Any]:
    manifest_path = router_repo / "data" / "index" / "index-manifest.json"
    snapshot = ""
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        snapshot = str(manifest.get("snapshot", ""))

    harvest_path = router_repo / "data" / "harvest" / "uk_citations.jsonl"
    sections = _collect_section_citers(harvest_path) if harvest_path.is_file() else {}

    batteries_dir = router_repo / "batteries" / "uk"
    instruments = _collect_battery_instruments(batteries_dir) if batteries_dir.is_dir() else {}

    return {
        "format_version": 1,
        "snapshot": snapshot,
        "sections": sections,
        "instruments": instruments,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--router-repo", type=Path, default=DEFAULT_ROUTER_REPO)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    t0 = time.perf_counter()
    repo = args.router_repo.expanduser().resolve()
    payload = build_prior(repo)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS))
    elapsed = time.perf_counter() - t0
    print(
        f"Wrote {args.out} in {elapsed:.2f}s "
        f"({len(payload['sections'])} sections, {len(payload['instruments'])} instruments, "
        f"{args.out.stat().st_size / 1024:.1f} KB)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
