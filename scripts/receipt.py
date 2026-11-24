"""Read-only receipt and provider quota reader.

Usage:
    uv run python scripts/receipt.py ANSWER_ID_PREFIX [--json]
    uv run python scripts/receipt.py --file PATH [--json]
    uv run python scripts/receipt.py --quota YYYY-MM-DD [--minutes HH:MM-HH:MM]

Options:
    ANSWER_ID_PREFIX       8 to 64 lowercase hex characters
    --file PATH            Read fact JSON from disk instead of database
    --json                 Output raw fact JSON
    --quota YYYY-MM-DD     Read provider quota ledger for UTC day
    --minutes HH:MM-HH:MM  Optional minute window for quota (e.g. 14:00-14:30)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any, Final, NoReturn

from chapterverse.config import read_secret
from chapterverse.contracts import AnswerFactObject

USAGE_LINE: Final = (
    "usage: receipt.py [-h] [--json] [--file PATH] "
    "[--quota YYYY-MM-DD] [--minutes HH:MM-HH:MM] [ANSWER_ID_PREFIX]"
)

HEX_PREFIX_RE: Final = re.compile(r"^[0-9a-f]{8,64}$")
DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MINUTES_RE: Final = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")

SELECT_RECEIPT_SQL: Final = (
    "SELECT answer_id, fact, created_at FROM receipts "
    "WHERE answer_id LIKE %s ORDER BY created_at DESC LIMIT 5"
)

SELECT_QUOTA_DAY_SQL: Final = (
    "SELECT provider, requests, input_tokens, output_tokens, rate_limited, errors "
    "FROM provider_quota "
    "WHERE window_kind = 'day' AND window_start = %s "
    "ORDER BY provider"
)

SELECT_QUOTA_MINUTE_SQL: Final = (
    "SELECT provider, window_start, requests, input_tokens, output_tokens, rate_limited, errors "
    "FROM provider_quota "
    "WHERE window_kind = 'minute' AND window_start >= %s AND window_start <= %s "
    "ORDER BY window_start, provider"
)


def _format_utc(val: datetime | str | None) -> str:
    if val is None:
        return "-"
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=UTC)
        return val.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    val_str = str(val).strip()
    return val_str or "-"


def _find_version_date(fact: AnswerFactObject, coord: str) -> str:
    v = fact.version_dates.get(coord)
    if v:
        return str(v)
    for k, val in fact.version_dates.items():
        if val and (coord == k or coord.startswith(f"{k}/")):
            return str(val)
    return "-"


def _format_passages(fact: AnswerFactObject) -> list[str]:
    if not fact.passages:
        return ["\npassages: none"]
    lines = [
        "\npassages:",
        f"{'passage id':<12} {'coordinate':<32} {'span':<12} {'version date'}",
    ]
    for pid, coord in sorted(fact.passages.items()):
        span = fact.spans.get(coord)
        span_str = f"{span[0]}-{span[1]}" if span else "-"
        vdate = _find_version_date(fact, coord)
        lines.append(f"{pid:<12} {coord:<32} {span_str:<12} {vdate}")
    return lines


def _format_sentences(fact: AnswerFactObject) -> list[str]:
    if not fact.sentences:
        return ["\nsentences: none"]
    lines = ["\nsentences:"]
    for s in fact.sentences:
        pids = ", ".join(s.passage_ids) if s.passage_ids else "none"
        reasons = ", ".join(s.reasons) if s.reasons else "none"
        lines.append(
            f"[{s.index}] verdict: {s.verdict} | rollbacks: {s.rollbacks} | "
            f"passages: {pids} | reasons: {reasons}"
        )
        lines.append(f"    text: {s.text}")
    return lines


def _format_calls(fact: AnswerFactObject) -> list[str]:
    if not fact.calls:
        return ["\nprovider calls: none"]
    lines = ["\nprovider calls:"]
    for c in fact.calls:
        prompt = c.prompt_tokens if c.prompt_tokens is not None else "-"
        comp = c.completion_tokens if c.completion_tokens is not None else "-"
        tokens_str = f"{prompt} prompt / {comp} completion"
        quant = f" ({c.quantisation})" if c.quantisation else ""
        extra = ""
        for attr in ("outcome", "error_kind"):
            val = getattr(c, attr, None)
            if val:
                extra += f" | {attr}: {val}"
        lines.append(
            f"- {c.provider} | purpose: {c.purpose} | model: {c.model_id}{quant} | "
            f"latency: {c.latency_ms:.1f} ms | tokens: {tokens_str}{extra}"
        )
    return lines


def _format_totals(fact: AnswerFactObject) -> list[str]:
    lines = ["\ntotals:", "calls per provider and purpose:"]
    if fact.calls:
        prov_purp: dict[tuple[str, str], int] = {}
        purp_lat: dict[str, float] = {}
        for c in fact.calls:
            key = (c.provider, c.purpose)
            prov_purp[key] = prov_purp.get(key, 0) + 1
            purp_lat[c.purpose] = purp_lat.get(c.purpose, 0.0) + c.latency_ms
        for (prov, purp), count in sorted(prov_purp.items()):
            lines.append(f"  {prov} / {purp}: {count}")
        lines.append("summed latency per purpose:")
        for purp, lat in sorted(purp_lat.items()):
            lines.append(f"  {purp}: {lat:.1f} ms")
    else:
        lines.append("  none")
        lines.append("summed latency per purpose:")
        lines.append("  none")

    rollbacks = sum(s.rollbacks for s in fact.sentences)
    lines.append(f"rollbacks: {rollbacks}")
    return lines


def format_receipt_text(
    fact: AnswerFactObject,
    *,
    created_at: datetime | str | None = None,
    answer_id: str | None = None,
) -> str:
    aid = answer_id or fact.answer_id
    cat = _format_utc(created_at)
    coords = ", ".join(fact.coordinates) if fact.coordinates else "none"

    lines: list[str] = [
        f"answer id:         {aid}",
        f"created_at UTC:    {cat}",
        f"mode:              {fact.mode}",
        f"route status:      {fact.route_status}",
        f"bound coordinates: {coords}",
        f"policy hash:       {fact.policy_hash}",
        f"corpus id:         {fact.corpus_id}",
    ]
    lines.extend(_format_passages(fact))
    lines.extend(_format_sentences(fact))
    lines.extend(_format_calls(fact))
    lines.extend(_format_totals(fact))
    return "\n".join(lines)


def format_quota_text(
    day_rows: Sequence[Sequence[Any]],
    minute_rows: Sequence[Sequence[Any]] | None,
    quota_date: str,
    minutes_range: tuple[str, str] | None,
) -> str:
    lines = [f"provider quota for {quota_date} (UTC day):"]
    if day_rows:
        lines.append(
            f"{'provider':<16} {'requests':<10} {'tokens (in/out)':<20} "
            f"{'rate_limited':<14} {'errors':<8}"
        )
        for r in day_rows:
            tokens_str = f"{r[2]} / {r[3]}"
            lines.append(f"{r[0]:<16} {r[1]:<10} {tokens_str:<20} {r[4]:<14} {r[5]:<8}")
    else:
        lines.append("day rows: none")

    if minutes_range:
        lines.append(f"\nminute rows ({minutes_range[0]} to {minutes_range[1]}):")
        if minute_rows:
            lines.append(
                f"{'time (UTC)':<20} {'provider':<16} {'requests':<10} "
                f"{'tokens (in/out)':<20} {'rate_limited':<14} {'errors':<8}"
            )
            for r in minute_rows:
                t_str = _format_utc(r[1])
                tokens_str = f"{r[3]} / {r[4]}"
                lines.append(
                    f"{t_str:<20} {r[0]:<16} {r[2]:<10} {tokens_str:<20} {r[5]:<14} {r[6]:<8}"
                )
        else:
            lines.append("minute rows: none")

    return "\n".join(lines)


def get_dsn(*, environ: Mapping[str, str] | None = None, config_path: Path | None = None) -> str:
    dsn = read_secret("DATABASE_URL", environ=environ)
    if dsn:
        return dsn
    path = (
        config_path
        if config_path is not None
        else Path.home() / ".config" / "chapter-and-verse" / "database_url"
    )
    if path.is_file():
        try:
            content = path.read_text(encoding="utf-8").strip()
            if content:
                return content
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"config read error: {type(exc).__name__}\n")
            sys.exit(1)
    sys.stderr.write("DATABASE_URL not set and config file not found\n")
    sys.exit(1)


def connect_db(dsn: str, *, connect_fn: Any = None) -> Any:
    if connect_fn is None:
        import psycopg  # noqa: PLC0415

        connect_fn = psycopg.connect
    try:
        return connect_fn(dsn, options="-c default_transaction_read_only=on")
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"database connect failed: {type(exc).__name__}\n")
        sys.exit(1)


def _execute(conn: Any, sql: str, params: Sequence[Any]) -> list[tuple[Any, ...]]:
    try:
        if hasattr(conn, "execute"):
            cur = conn.execute(sql, params)
            return list(cur.fetchall())
        cur = conn.cursor()
        cur.execute(sql, params)
        return list(cur.fetchall())
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"database query failed: {type(exc).__name__}\n")
        sys.exit(1)


def _exit_usage() -> NoReturn:
    sys.stderr.write(f"{USAGE_LINE}\n")
    sys.exit(2)


def _handle_quota(
    args: argparse.Namespace,
    *,
    connect_fn: Any,
    dsn: str | None,
    environ: Mapping[str, str] | None,
    config_path: Path | None,
) -> int:
    if not DATE_RE.match(args.quota):
        _exit_usage()
    try:
        quota_date = date.fromisoformat(args.quota)
    except ValueError:
        _exit_usage()

    day_start = datetime.combine(quota_date, time.min, tzinfo=UTC)

    minutes_range: tuple[str, str] | None = None
    min_start: datetime | None = None
    min_end: datetime | None = None
    if args.minutes is not None:
        m = MINUTES_RE.match(args.minutes)
        if not m:
            _exit_usage()
        h1, m1, h2, m2 = (int(m.group(i)) for i in range(1, 5))
        t1 = time(h1, m1)
        t2 = time(h2, m2)
        if t1 > t2:
            _exit_usage()
        minutes_range = (f"{h1:02d}:{m1:02d}", f"{h2:02d}:{m2:02d}")
        min_start = datetime.combine(quota_date, t1, tzinfo=UTC)
        min_end = datetime.combine(quota_date, t2, tzinfo=UTC)

    effective_dsn = dsn or get_dsn(environ=environ, config_path=config_path)
    conn = connect_db(effective_dsn, connect_fn=connect_fn)
    try:
        day_rows = _execute(conn, SELECT_QUOTA_DAY_SQL, (day_start,))
        minute_rows: list[tuple[Any, ...]] | None = None
        if min_start is not None and min_end is not None:
            minute_rows = _execute(conn, SELECT_QUOTA_MINUTE_SQL, (min_start, min_end))
        out = format_quota_text(day_rows, minute_rows, args.quota, minutes_range)
        sys.stdout.write(f"{out}\n")
    finally:
        if hasattr(conn, "close"):
            conn.close()
    return 0


def _handle_file(args: argparse.Namespace) -> int:
    if not args.file.is_file():
        sys.stderr.write(f"file not found: {args.file}\n")
        sys.exit(1)
    try:
        data = json.loads(args.file.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"invalid JSON file: {type(exc).__name__}\n")
        sys.exit(1)

    raw_fact = data["fact"] if isinstance(data, dict) and "fact" in data else data
    try:
        fact = AnswerFactObject.from_dict(raw_fact)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"invalid fact schema: {type(exc).__name__}\n")
        sys.exit(1)

    if args.json:
        sys.stdout.write(f"{json.dumps(fact.to_dict(), indent=2)}\n")
    else:
        created_at = data.get("created_at") if isinstance(data, dict) else None
        answer_id = (
            data.get("answer_id")
            if isinstance(data, dict) and "answer_id" in data
            else fact.answer_id
        )
        out = format_receipt_text(fact, created_at=created_at, answer_id=answer_id)
        sys.stdout.write(f"{out}\n")
    return 0


def _handle_prefix(
    args: argparse.Namespace,
    *,
    connect_fn: Any,
    dsn: str | None,
    environ: Mapping[str, str] | None,
    config_path: Path | None,
) -> int:
    effective_dsn = dsn or get_dsn(environ=environ, config_path=config_path)
    conn = connect_db(effective_dsn, connect_fn=connect_fn)
    try:
        rows = _execute(conn, SELECT_RECEIPT_SQL, (f"{args.prefix}%",))
        if len(rows) == 0:
            sys.stderr.write("no receipt\n")
            sys.exit(1)
        if len(rows) > 1:
            sys.stderr.write("multiple receipts matched:\n")
            for r_id, _fact_data, r_created in rows:
                sys.stderr.write(f"{r_id}  {_format_utc(r_created)}\n")
            sys.exit(1)

        answer_id, fact_data, created_at = rows[0]
        if isinstance(fact_data, str):
            try:
                fact_dict = json.loads(fact_data)
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write(f"invalid stored JSON: {type(exc).__name__}\n")
                sys.exit(1)
        elif isinstance(fact_data, dict):
            fact_dict = fact_data
        else:
            sys.stderr.write("unexpected fact column type\n")
            sys.exit(1)

        try:
            fact = AnswerFactObject.from_dict(fact_dict)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"invalid fact schema: {type(exc).__name__}\n")
            sys.exit(1)

        if args.json:
            sys.stdout.write(f"{json.dumps(fact.to_dict(), indent=2)}\n")
        else:
            out = format_receipt_text(fact, created_at=created_at, answer_id=answer_id)
            sys.stdout.write(f"{out}\n")
    finally:
        if hasattr(conn, "close"):
            conn.close()
    return 0


def main(
    argv: list[str] | None = None,
    *,
    connect_fn: Any = None,
    dsn: str | None = None,
    environ: Mapping[str, str] | None = None,
    config_path: Path | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="receipt.py",
        description="Read-only receipt and provider quota reader.",
        add_help=True,
    )
    parser.add_argument(
        "prefix",
        nargs="?",
        default=None,
        help="8 to 64 lowercase hex characters",
    )
    parser.add_argument("--json", action="store_true", help="output raw fact JSON")
    parser.add_argument("--file", type=Path, default=None, help="read fact JSON from disk")
    parser.add_argument(
        "--quota",
        type=str,
        default=None,
        help="read provider quota for UTC day (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--minutes",
        type=str,
        default=None,
        help="minute window for quota (HH:MM-HH:MM)",
    )

    args, unknown = parser.parse_known_args(argv)
    if unknown:
        _exit_usage()

    if args.quota is not None:
        if args.prefix is not None or args.file is not None:
            _exit_usage()
        return _handle_quota(
            args,
            connect_fn=connect_fn,
            dsn=dsn,
            environ=environ,
            config_path=config_path,
        )

    if args.minutes is not None:
        _exit_usage()

    if args.file is not None:
        if args.prefix is not None:
            _exit_usage()
        return _handle_file(args)

    if args.prefix is None or not HEX_PREFIX_RE.match(args.prefix):
        _exit_usage()

    return _handle_prefix(
        args,
        connect_fn=connect_fn,
        dsn=dsn,
        environ=environ,
        config_path=config_path,
    )


if __name__ == "__main__":
    sys.exit(main())
