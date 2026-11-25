"""Start the local real-engine preview server bound to 127.0.0.1 only.

Starts ``uvicorn chapterverse.app:app`` with one worker (matching ``deploy/entrypoint.sh``),
removes ``DATABASE_URL`` and all Modal settings from the environment, disables Cloudflare
Access header enforcement for local Vite proxy requests, and prints the listening URL.

Usage (from repo root):
    uv run python scripts/dev_engine.py [--port 8000]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from typing import Final

LOCAL_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8000
MIN_CAP_SALT_LEN: Final = 32
PROVIDERS: Final = ("groq", "cloudflare")
_MAX_PORT: Final = 65535


def _strip_modal_providers(raw: str | None) -> str:
    if not raw:
        return ",".join(PROVIDERS)
    kept = [
        p.strip().lower() for p in raw.split(",") if p.strip() and p.strip().lower() in PROVIDERS
    ]
    return ",".join(kept) if kept else ",".join(PROVIDERS)


def build_preview_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy ``environ`` with ``DATABASE_URL`` and Modal settings removed for local preview."""
    source = os.environ if environ is None else environ
    env = {
        k: v
        for k, v in source.items()
        if k != "DATABASE_URL" and not k.startswith("MODAL_") and k != "HOST"
    }
    env["GEN_PROVIDERS"] = _strip_modal_providers(env.get("GEN_PROVIDERS"))
    env["JUDGE_PROVIDERS"] = _strip_modal_providers(env.get("JUDGE_PROVIDERS"))
    # Local Vite preview does not pass Cloudflare Access headers.
    env["REQUIRE_ACCESS_HEADERS"] = "0"
    if env.get("DAILY_ASK_CAP") != "0" and len(env.get("CAP_SALT", "").strip()) < MIN_CAP_SALT_LEN:
        env["DAILY_ASK_CAP"] = "0"
    return env


def build_uvicorn_command(
    port: int,
    *,
    python_executable: str | None = None,
    log_level: str = "info",
    limit_concurrency: int = 64,
) -> list[str]:
    """Return the container-style single-worker uvicorn command bound to 127.0.0.1."""
    return [
        python_executable or sys.executable,
        "-m",
        "uvicorn",
        "chapterverse.app:app",
        "--host",
        LOCAL_HOST,
        "--port",
        str(port),
        "--workers",
        "1",
        "--limit-concurrency",
        str(limit_concurrency),
        "--timeout-graceful-shutdown",
        "20",
        "--no-server-header",
        "--no-access-log",
        "--log-level",
        log_level,
    ]


def validate_host(host: str) -> None:
    """Refuse any bind host other than 127.0.0.1."""
    if host != LOCAL_HOST:
        raise ValueError(f"refusing to bind to {host!r}; only {LOCAL_HOST} is allowed")


def run_preview(
    *,
    host: str = LOCAL_HOST,
    port: int = DEFAULT_PORT,
    environ: Mapping[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
) -> int:
    """Validate host/port, print the preview URL, and launch uvicorn."""
    validate_host(host)
    if not (1 <= port <= _MAX_PORT):
        raise ValueError(f"port must be between 1 and {_MAX_PORT}, got {port}")
    env = build_preview_env(environ)
    if environ is not None and "HOST" in environ and environ["HOST"] != LOCAL_HOST:
        validate_host(environ["HOST"])
    cmd = build_uvicorn_command(
        port,
        log_level=env.get("LOG_LEVEL", "info").lower(),
    )
    print(f"http://{LOCAL_HOST}:{port}", flush=True)
    exec_fn = runner or subprocess.run
    result = exec_fn(cmd, env=env, check=False)
    return int(result.returncode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Chapter & Verse engine locally on 127.0.0.1.")
    parser.add_argument(
        "--host",
        default=LOCAL_HOST,
        help="Must be 127.0.0.1 (any other host is refused).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", str(DEFAULT_PORT))),
        help="TCP port to bind on 127.0.0.1 (default: 8000).",
    )
    args = parser.parse_args(argv)
    try:
        return run_preview(host=args.host, port=args.port)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
