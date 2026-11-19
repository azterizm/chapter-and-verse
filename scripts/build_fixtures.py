"""Rebuild the committed test fixture corpus (tests/fixtures/data/) from the real data.

Build machine only. Needs the full data root (``LEGAL_DATA_ROOT`` or ``--data``) and a
checkout of legal-rag-router with its development environment (``--router``), whose own
``ingest.build_index`` and ``ingest.build_concept_index`` build the fixture indexes. Neither
input is modified. The output mirrors the shape of the real data root:

    tests/fixtures/data/
        uk/ukpga/<year>/<instrument_id>.jsonl   record files, lines copied byte for byte
        catalogue/uk_catalogue.jsonl            coverage sample (first rows per series)
        index/                                  router index over the fixture records
        concepts/                               concept index over the fixture records
        MANIFEST.json                           the source and licence manifest, copied
    tests/fixtures/fixture.json                 what each instrument is for

Deterministic: re-running on the same inputs gives byte-identical files.

Usage: uv run python scripts/build_fixtures.py [--data PATH] [--router PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

REPO: Final = Path(__file__).resolve().parents[1]
OUT: Final = REPO / "tests" / "fixtures"
SNAPSHOT: Final = "2026-09-28"
"""The snapshot date of the real router index the fixture is cut from."""
COVERAGE_PER_SERIES: Final = 2
DEFAULT_ROUTER: Final = Path.home() / "Code" / "legal-rag-router"

ERA: Final = "uk/ukpga/1996/18"
# Part X sections kept from ERA 1996: s.94, s.98 and s.118 to s.126 (s.124A sits in that run).
ERA_SECTIONS: Final = ("s94", "s98", "s118", "s119", "s120", "s121", "s122", "s123", "s124")
ERA_SECTIONS_MORE: Final = ("s124A", "s125", "s126")

ROLES: Final[dict[str, Any]] = {
    "snapshot": SNAPSHOT,
    "era": {
        "instrument": ERA,
        "sections": [*ERA_SECTIONS, *ERA_SECTIONS_MORE],
        "note": "reduced to the instrument record plus these sections and their descendants",
    },
    "title_only": {
        "instrument": "uk/ukpga/1980/15",
        "title": "National Health Service (Invalid Direction) Act 1980",
        "note": "structure metadata_only, provision_count 0: an instrument record and no text",
    },
    "repealed": {
        "instrument": "uk/ukpga/2008/7",
        "title": "European Union (Amendment) Act 2008",
        "provisions": ["uk/ukpga/2008/7/s4", "uk/ukpga/2008/7/s5"],
        "in_force_provision": "uk/ukpga/2008/7/s3",
        "note": "instrument in force; s4 and s5 carry repealed=true and dot-run text",
    },
    "prospective": {
        "instrument": "uk/ukpga/2018/11",
        "title": "Secure Tenancies (Victims of Domestic Abuse) Act 2018",
        "provisions": ["uk/ukpga/2018/11/s1"],
        "in_force_provision": "uk/ukpga/2018/11/s2",
        "note": "instrument in force; s1 and its subsections carry prospective=true",
    },
}

WHOLE_FILES: Final = (
    ROLES["title_only"]["instrument"],
    ROLES["repealed"]["instrument"],
    ROLES["prospective"]["instrument"],
)


def record_path(root: Path, coordinate: str) -> Path:
    """``uk/ukpga/1996/18`` -> ``<root>/uk/ukpga/1996/uk_ukpga_1996_18.jsonl`` (calendar Acts)."""
    parts = coordinate.split("/")
    return root.joinpath(*parts[:3], "_".join(parts) + ".jsonl")


def keep_era_line(line: str) -> bool:
    row = json.loads(line)
    if row.get("record_type") == "instrument":
        return True
    tail = str(row["coordinate"]).removeprefix(ERA + "/").split("/")
    return tail[0] in ERA_SECTIONS or tail[0] in ERA_SECTIONS_MORE


def write_records(data: Path, out_data: Path) -> list[Path]:
    written: list[Path] = []
    source = record_path(data, ERA)
    target = record_path(out_data, ERA)
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open(encoding="utf-8", newline="") as fh:
        kept = [line for line in fh if line.strip() and keep_era_line(line)]
    target.write_text("".join(kept), encoding="utf-8", newline="")
    written.append(target)
    for coordinate in WHOLE_FILES:
        source = record_path(data, coordinate)
        target = record_path(out_data, coordinate)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        written.append(target)
    return written


def write_catalogue(data: Path, out_data: Path) -> Path:
    """The first rows of each series, so coverage answers have something to say in tests."""
    seen: dict[str, int] = {}
    lines: list[str] = []
    with (data / "catalogue" / "uk_catalogue.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            series = str(json.loads(line)["series"])
            if seen.get(series, 0) < COVERAGE_PER_SERIES:
                seen[series] = seen.get(series, 0) + 1
                lines.append(line if line.endswith("\n") else line + "\n")
    target = out_data / "catalogue" / "uk_catalogue.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(lines), encoding="utf-8")
    return target


def run_router_tool(router: Path, module: str, args: list[str]) -> None:
    """Run one of the router's own ingest modules in its checkout, with its environment."""
    python = router / ".venv" / "bin" / "python"
    if not python.exists():
        raise SystemExit(f"{python} not found: run `uv sync` in the router checkout first")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.run(  # noqa: S603 - fixed module names, paths from this script
        [str(python), "-m", module, *args], cwd=router, env=env, check=True
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    default_data = os.environ.get("LEGAL_DATA_ROOT") or str(DEFAULT_ROUTER / "data")
    parser.add_argument("--data", type=Path, default=Path(default_data))
    parser.add_argument("--router", type=Path, default=DEFAULT_ROUTER)
    args = parser.parse_args(argv)
    data: Path = args.data.resolve()
    router: Path = args.router.resolve()

    out_data = OUT / "data"
    if out_data.exists():
        shutil.rmtree(out_data)
    out_data.mkdir(parents=True)
    write_records(data, out_data)
    catalogue = write_catalogue(data, out_data)
    shutil.copyfile(data / "MANIFEST.json", out_data / "MANIFEST.json")

    run_router_tool(
        router,
        "ingest.build_index",
        [
            "--data", str(out_data),
            "--out", str(out_data / "index"),
            "--snapshot", SNAPSHOT,
            "--catalogue", str(catalogue),
            "--aliases", "aliases",
            "--allow-missing-alias-targets",
        ],
    )  # fmt: skip
    run_router_tool(
        router,
        "ingest.build_concept_index",
        [
            "--data", str(out_data),
            "--out", str(out_data / "concepts"),
            "--snapshot", SNAPSHOT,
            "--thesaurus", "thesaurus/uk.toml",
        ],
    )  # fmt: skip
    (OUT / "fixture.json").write_text(
        json.dumps(ROLES, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    size = sum(p.stat().st_size for p in out_data.rglob("*") if p.is_file())
    print(f"fixture corpus written to {out_data} ({size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
