"""Playwright browser smoke test for the Chapter & Verse web UI.

Runs against either the mock dev/preview server (``--mock``) or a real-engine preview.
Must import nothing from ``chapterverse`` so it runs standalone with:

    uv run --no-project --with playwright==1.55.0 python eval/web_smoke.py --base URL --out DIR
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

DEFAULT_MOCK_QUERY: Final = (
    "What is the limit of the compensatory award under the Employment Rights Act 1996?"
)
DEFAULT_ENGINE_QUERY: Final = (
    "What is the limit of the compensatory award under the Employment Rights Act 1996?"
)
VIEWPORT_WIDTHS: Final[tuple[int, ...]] = (1280, 768, 360)
VIEWPORT_HEIGHT: Final = 800
COLOR_SCHEMES: Final[tuple[str, ...]] = ("light", "dark")
CSP_NEEDLE: Final = "Refused to"


@dataclass(frozen=True, slots=True)
class SmokeReport:
    base_url: str
    query: str
    screenshots: list[str]
    csp_violations: list[str]
    page_errors: list[str]
    overflows: list[str]
    alert_texts: list[str]

    @property
    def ok(self) -> bool:
        return not (self.csp_violations or self.page_errors or self.overflows or self.alert_texts)


def default_query(*, mock: bool) -> str:
    return DEFAULT_MOCK_QUERY if mock else DEFAULT_ENGINE_QUERY


def screenshot_filename(width: int, scheme: str) -> str:
    return f"smoke-{width}-{scheme}.png"


def is_csp_violation(message_text: str) -> bool:
    return CSP_NEEDLE in message_text


def run_smoke(
    *,
    base_url: str,
    out_dir: Path,
    query: str,
    timeout_s: float = 60.0,
) -> SmokeReport:
    from playwright.sync_api import sync_playwright  # noqa: PLC0415 - standalone uv --with env

    out_dir.mkdir(parents=True, exist_ok=True)
    timeout_ms = int(timeout_s * 1000)
    csp_violations: list[str] = []
    page_errors: list[str] = []
    overflows: list[str] = []
    alert_texts: list[str] = []
    screenshots: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            context = browser.new_context(
                viewport={"width": VIEWPORT_WIDTHS[0], "height": VIEWPORT_HEIGHT}
            )
            page = context.new_page()

            def on_console(msg: object) -> None:
                text = getattr(msg, "text", "")
                if callable(text):
                    text = text()
                text_str = str(text)
                if is_csp_violation(text_str):
                    csp_violations.append(text_str)

            def on_pageerror(err: object) -> None:
                page_errors.append(str(err))

            page.on("console", on_console)
            page.on("pageerror", on_pageerror)

            page.goto(base_url, wait_until="load", timeout=timeout_ms)

            composer = page.locator("#q")
            composer.wait_for(state="visible", timeout=timeout_ms)
            composer.fill(query)
            page.locator('button[type="submit"][aria-label="Send"]').click()

            turn = page.locator('article[id^="turn-"]').first
            turn.wait_for(state="visible", timeout=timeout_ms)
            turn.locator('[aria-busy="false"]').wait_for(state="attached", timeout=timeout_ms)

            alerts = page.locator('[role="alert"]')
            alert_texts.extend(
                alerts.nth(idx).inner_text().strip() for idx in range(alerts.count())
            )

            for scheme in COLOR_SCHEMES:
                page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
                for width in VIEWPORT_WIDTHS:
                    page.set_viewport_size({"width": width, "height": VIEWPORT_HEIGHT})
                    page.evaluate(
                        "() => new Promise(r => "
                        "requestAnimationFrame(() => requestAnimationFrame(r)))"
                    )
                    has_overflow = bool(
                        page.evaluate(
                            "() => document.documentElement.scrollWidth "
                            "> document.documentElement.clientWidth"
                        )
                    )
                    if has_overflow:
                        overflows.append(f"{width}px ({scheme})")
                    shot = out_dir / screenshot_filename(width, scheme)
                    page.screenshot(path=str(shot), full_page=True)
                    screenshots.append(str(shot))
        finally:
            browser.close()

    report = SmokeReport(
        base_url=base_url,
        query=query,
        screenshots=screenshots,
        csp_violations=csp_violations,
        page_errors=page_errors,
        overflows=overflows,
        alert_texts=alert_texts,
    )
    (out_dir / "smoke-report.json").write_text(
        json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8"
    )
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Playwright smoke test for Chapter & Verse web.")
    parser.add_argument("--base", required=True, help="Base URL of the running web app.")
    parser.add_argument("--out", required=True, type=Path, help="Directory to write screenshots.")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use the mock question (against npm run dev/preview).",
    )
    parser.add_argument("--query", default=None, help="Override question to submit.")
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Timeout in seconds waiting for page load and answer done.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    query = args.query or default_query(mock=bool(args.mock))
    report = run_smoke(
        base_url=args.base,
        out_dir=args.out,
        query=query,
        timeout_s=args.timeout,
    )
    print(f"screenshots: {len(report.screenshots)}")
    if report.csp_violations:
        print(f"CSP violations: {report.csp_violations}", file=sys.stderr)
    if report.page_errors:
        print(f"page errors: {report.page_errors}", file=sys.stderr)
    if report.overflows:
        print(f"horizontal overflows: {report.overflows}", file=sys.stderr)
    if report.alert_texts:
        print(f"error alerts: {report.alert_texts}", file=sys.stderr)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
