"""Tests for slice-2 startup fix (requirements R1, R2, R3)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from chapterverse.app import _load_slice2_dictionary, build_runtime, create_app
from chapterverse.contracts import TextStore

if TYPE_CHECKING:
    from tests.test_pipeline_fakes import parse_sse
else:
    from test_pipeline_fakes import parse_sse

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def built_store(tmp_path_factory: pytest.TempPathFactory, fixture_data_root: Path) -> Path:
    out = tmp_path_factory.mktemp("store") / "store"
    cmd = [
        sys.executable,
        str(REPO / "build" / "textstore.py"),
        "build",
        "--data",
        str(fixture_data_root),
        "--out",
        str(out),
        "--workers",
        "1",
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=REPO)  # noqa: S603
    return out


def test_ensure_scratch_measure_script() -> None:
    """Ensure measurement script is written to $CV_SCRATCH/s2-startup-fix/measure.py for R4."""
    scratch = os.environ.get("CV_SCRATCH")
    if not scratch:
        return
    scratch_dir = Path(scratch) / "s2-startup-fix"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    script_path = scratch_dir / "measure.py"
    script_content = '''"""Measure startup time and RSS of Chapter & Verse engine."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def get_process_rss_mb(pid: int) -> float:
    try:
        res = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, check=True
        )
        kb = float(res.stdout.strip())
        return kb / 1024.0
    except Exception:
        return 0.0


def main() -> None:
    port = find_free_port()
    cwd = os.getcwd()
    env = os.environ.copy()
    env["STORE_DIR"] = "artefacts/store"
    env["LEGAL_DATA_ROOT"] = os.path.expanduser("~/Code/legal-rag-router/data")
    env["CAP_SALT"] = "a" * 32
    env.pop("SYMSPELL_DICT", None)

    t0 = time.perf_counter()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "chapterverse.app:app",
            "--port",
            str(port),
            "--host",
            "127.0.0.1",
        ],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}/api/health"
    healthy = False
    startup_seconds = 0.0
    deadline = t0 + 120.0
    while time.perf_counter() < deadline:
        if proc.poll() is not None:
            _, err = proc.communicate()
            print("uvicorn exited prematurely:", err.decode("utf-8", errors="replace"))
            sys.exit(1)
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    startup_seconds = time.perf_counter() - t0
                    healthy = True
                    break
        except Exception:
            time.sleep(0.05)

    if not healthy:
        proc.kill()
        print("Timeout waiting for /api/health")
        sys.exit(1)

    rss_mb = get_process_rss_mb(proc.pid)
    proc.terminate()
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.kill()

    print(f"startup_seconds: {startup_seconds:.2f}")
    print(f"rss_mb: {rss_mb:.2f}")


if __name__ == "__main__":
    main()
'''
    script_path.write_text(script_content, encoding="utf-8")


def test_startup_succeeds_without_symspell_dict_over_fixture_store(
    fixture_data_root: Path,
    built_store: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R3: build runtime from env with NO SYMSPELL_DICT, repo artefacts/ not consulted.

    Startup succeeds over fixture store and a misspelled uncited query gets corrections notice.
    """
    caplog.set_level(logging.INFO)
    # Run with temporary directory as current working directory so repo artefacts/ is not consulted
    monkeypatch.chdir(tmp_path)

    env = {
        "LEGAL_DATA_ROOT": str(fixture_data_root),
        "STORE_DIR": str(built_store),
        "GEN_PROVIDERS": "groq",
        "JUDGE_PROVIDERS": "groq",
        "CAP_SALT": "a" * 32,
    }
    assert "SYMSPELL_DICT" not in env

    app = create_app(lambda: build_runtime(env))
    with TestClient(app) as client:
        # Check health
        health = client.get("/api/health")
        assert health.status_code == 200
        data = health.json()
        assert data["status"] == "ok"

        # Check logs: R2 logging requirements
        messages = [r.getMessage() for r in caplog.records]
        built_logs = [m for m in messages if "dictionary built from store:" in m]
        assert len(built_logs) == 1, messages
        # Log should state term count and seconds
        match = re.search(r"dictionary built from store: (\d+) terms in ([\d.]+)s", built_logs[0])
        assert match is not None
        term_count = int(match.group(1))
        seconds = float(match.group(2))
        assert term_count > 0
        assert seconds >= 0.0

        # Misspelled uncited query gets a corrections notice
        query = "unfiar dismisal compensatory award cap"
        res = client.post("/api/ask", json={"query": query})
        assert res.status_code == 200
        events = parse_sse(res.content)
        event_types = [t for t, _, _ in events]
        assert event_types == ["route", "notice", "clarify", "done"]

        notice_event = events[1][1]
        assert notice_event["code"] == "corrections_applied"
        assert '"unfiar" read as "unfair"' in notice_event["message"]
        assert '"dismisal" read as "dismissal"' in notice_event["message"]


def test_startup_logs_when_loading_dictionary_from_file(
    fixture_text_store: TextStore,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R2: when SYMSPELL_DICT names an existing file, load it and log dictionary from file."""
    caplog.set_level(logging.INFO)
    dict_file = tmp_path / "custom_dict.txt"
    dict_file.write_text("compensation 100\naward 200\nlimit 50\n", encoding="utf-8")
    dict_bytes = dict_file.read_bytes()
    manifest = {
        "corpus_id": fixture_text_store.corpus_id,
        "files": {
            "custom_dict.txt": {
                "sha256": hashlib.sha256(dict_bytes).hexdigest(),
                "size": len(dict_bytes),
            }
        },
    }
    (tmp_path / "symspell-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    env: Mapping[str, str] = {"SYMSPELL_DICT": str(dict_file)}
    sym = _load_slice2_dictionary(env, fixture_text_store)
    assert sym.word_count == 3

    messages = [r.getMessage() for r in caplog.records]
    file_logs = [m for m in messages if "dictionary from file:" in m]
    assert len(file_logs) == 1
    match = re.search(r"dictionary from file: (\d+) terms in ([\d.]+)s", file_logs[0])
    assert match is not None
    assert int(match.group(1)) == 3
