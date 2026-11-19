"""Modal spend ledger for operator runs, with a hard monthly cap. Standard library only.

Ported from legal-rag-verifier ``scripts/modal_ledger.py``: per-second list prices plus 15 % for
CPU, memory and idle time. Each run is recorded when it returns, before the next starts;
``check`` refuses a run whose estimate would take the month past the cap.

Scope: runs started by hand (``fill_weights``, test sessions with ``modal serve``, batch evals).
Traffic to the deployed endpoint is not seen here; its hard cap is the Modal workspace budget
($30, set in the dashboard), and the engine counts Modal GPU seconds in the Postgres
``provider_quota`` table.

The ledger file lives outside the repository: ``CV_MODAL_LEDGER``, default
``~/.local/state/chapter-and-verse/modal_ledger.json``. Cap: ``CV_MODAL_CAP_USD`` (default 30).

CLI::

    python deploy/modal/ledger.py status
    python deploy/modal/ledger.py check L40S 1800
    python deploy/modal/ledger.py record "serve smoke" L40S 900 "20 questions"
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import sys
from pathlib import Path
from typing import Any

# Modal list prices, USD per hour (modal.com/pricing, read 2026-11-18): L4 $0.000222/s,
# L40S $0.000542/s. CPU is a rough figure for a 2-core, 4 GiB container.
PER_HOUR = {"L4": 0.80, "L40S": 1.95, "A100-80GB": 2.50, "H100": 3.95, "CPU": 0.10}
OVERHEAD = 1.15
DEFAULT_CAP = 30.0


class ModalBudgetError(RuntimeError):
    """A run would take the month past the cap."""


def ledger_path() -> Path:
    default = Path.home() / ".local/state/chapter-and-verse/modal_ledger.json"
    return Path(os.environ.get("CV_MODAL_LEDGER", default)).expanduser()


def cap() -> float:
    return float(os.environ.get("CV_MODAL_CAP_USD", DEFAULT_CAP))


def month(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.UTC)).strftime("%Y-%m")


def usd(gpu: str, seconds: float) -> float:
    if gpu not in PER_HOUR:
        raise ValueError(f"unknown GPU {gpu!r}; one of {sorted(PER_HOUR)}")
    if seconds < 0:
        raise ValueError("seconds must be non-negative")
    return round(PER_HOUR[gpu] * seconds / 3600 * OVERHEAD, 4)


def entries() -> list[dict[str, Any]]:
    path = ledger_path()
    if not path.exists():
        return []
    data: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    return data


def spent(in_month: str | None = None) -> float:
    m = in_month or month()
    return round(sum(float(e["usd"]) for e in entries() if e.get("month") == m), 4)


def check(gpu: str, estimate_seconds: float) -> None:
    """Raise ModalBudgetError when the estimate would take this month past the cap."""
    so_far, limit = spent(), cap()
    if so_far + usd(gpu, estimate_seconds) > limit:
        estimate = usd(gpu, estimate_seconds)
        raise ModalBudgetError(
            f"Modal {month()}: ${so_far:.2f} spent, run estimate ${estimate:.2f}, cap ${limit:.2f}"
        )


def record(run: str, gpu: str, seconds: float, note: str = "") -> None:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # several processes may share the ledger
        data = entries()
        data.append(
            {
                "month": month(),
                "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                "run": run,
                "gpu": gpu,
                "seconds": round(seconds, 1),
                "usd": usd(gpu, seconds),
                "note": note,
            }
        )
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        tmp.replace(path)


def main(argv: list[str]) -> int:
    match argv:
        case [] | ["status"]:
            print(f"{month()}: ${spent():.2f} of ${cap():.2f} ({ledger_path()})")  # noqa: T201
            return 0
        case ["check", gpu, seconds]:
            try:
                check(gpu, float(seconds))
            except ModalBudgetError as exc:
                print(exc, file=sys.stderr)  # noqa: T201
                return 1
            print(f"ok: estimate ${usd(gpu, float(seconds)):.2f}")  # noqa: T201
            return 0
        case ["record", run, gpu, seconds, *rest] if len(rest) <= 1:
            record(run, gpu, float(seconds), rest[0] if rest else "")
            return 0
        case _:
            print(__doc__, file=sys.stderr)  # noqa: T201
            return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
