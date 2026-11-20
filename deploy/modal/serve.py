"""Modal SGLang server for the ``qwen27-ar4`` judge preset (third provider in the rotation).

Derived from legal-rag-verifier ``scripts/modal_serve.py`` (stop 78, 2026-11-02). The SGLang
command line is the measured one: SGLang 0.5.21 on one L40S, model
``Vishva007/Qwen3.8-27B-W4A16-AutoRound-GPTQ`` at revision
``1b9d56b428e9f841d7ad566f87660b5f47553c97``, ``--mem-fraction-static 0.85``,
``--reasoning-parser qwen3``, ``--context-length 65536``. Measured: ready after 406 s,
91.4 % (138/151) on the held-out sentences, server p50 0.58 s per judge call.

One addition to the measured command: ``--enable-custom-logit-processor``. The engine's
``InFlightGenerator`` (``steering="inject"``) falls back to a token "ban" when a retry has no new
window, and the ban is a server-side logit mask (``legal_rag_verifier.backends._sglang_mask``).
The flag only takes effect for a request that carries ``custom_logit_processor``; judge requests
carry none, so the measured judge path is unchanged. The serialised mask refers to its class by
module path, so the server image also installs ``legal-rag-verifier==0.5.0`` (pure Python, no
dependencies) to load it. ``mask_processor`` returns the serialised mask once, so the engine
host needs neither torch nor sglang: pass it as ``SGLangBackend(..., processor=...)``.

Endpoints (OpenAI-compatible plus SGLang native), all behind ``Authorization: Bearer <key>``:
``/v1/chat/completions``, ``/v1/models``, ``/generate`` (KV-cache rollback), ``/health``.

Guards against runaway spend:

- exactly one container (``max_containers=1``), scale to zero after 120 s idle;
- ``startup_timeout=1800``: SGLang loads weights and captures CUDA graphs for several minutes;
  Modal's 300 s default killed the first stop-78 launch in a crash loop;
- weights come from a Modal Volume with ``HF_HUB_OFFLINE=1``; the server never downloads;
- the workspace budget in the Modal dashboard ($30) is the hard outer cap for traffic;
  operator runs (``fill_weights``, test sessions) are costed in ``ledger.py`` first.

Configuration, read on the machine that runs ``modal`` and baked into the image environment so
the container sees the same values:

====================  =================================================  ======================
CV_MODAL_APP          Modal app name                                     chapter-and-verse-judge
CV_WEIGHTS_VOLUME     Modal Volume holding the Hugging Face cache        chapter-and-verse-hf
CV_MODAL_SECRET       Modal Secret holding ``JUDGE_API_KEY``             chapter-and-verse-judge-key
====================  =================================================  ======================

Commands (run from the repository root; each is a paid action, run only on a go):

  uv run --with modal==1.2.6 modal secret create chapter-and-verse-judge-key JUDGE_API_KEY=...
  uv run --with modal==1.2.6 modal run deploy/modal/serve.py::fill_weights    # once, CPU only
  uv run --with modal==1.2.6 modal deploy deploy/modal/serve.py               # the endpoint
  uv run --with modal==1.2.6 modal run deploy/modal/serve.py::get_mask_processor  # CPU only

The engine's ``MODAL_API_KEY`` must equal the secret's ``JUDGE_API_KEY``, and ``MODAL_URL`` is
the URL ``modal deploy`` prints for ``serve``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import modal

MODEL = "Vishva007/Qwen3.8-27B-W4A16-AutoRound-GPTQ"
REVISION = "1b9d56b428e9f841d7ad566f87660b5f47553c97"
SGLANG_IMAGE = "lmsysorg/sglang:v0.5.21"
VERIFIER = "legal-rag-verifier==0.5.0"
GPU = "L40S"
PORT = 30000
HF_HOME = "/hf"
SNAPSHOT = Path(HF_HOME) / "hub" / f"models--{MODEL.replace('/', '--')}" / "snapshots" / REVISION

APP_NAME = os.environ.get("CV_MODAL_APP", "chapter-and-verse-judge")
VOLUME_NAME = os.environ.get("CV_WEIGHTS_VOLUME", "chapter-and-verse-hf")
SECRET_NAME = os.environ.get("CV_MODAL_SECRET", "chapter-and-verse-judge-key")
CONFIG_ENV = {
    "CV_MODAL_APP": APP_NAME,
    "CV_WEIGHTS_VOLUME": VOLUME_NAME,
    "CV_MODAL_SECRET": SECRET_NAME,
}

if modal.is_local():
    try:  # The pins must match the released judge preset when the verifier is installed.
        from legal_rag_verifier.judge import PRESETS

        if PRESETS["qwen27-ar4"].model != MODEL or PRESETS["qwen27-ar4"].revision != REVISION:
            sys.exit("Error: MODEL/REVISION differ from legal_rag_verifier PRESETS['qwen27-ar4']")
    except ImportError:
        pass

app = modal.App(APP_NAME)
weights = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
api_key = modal.Secret.from_name(SECRET_NAME, required_keys=["JUDGE_API_KEY"])

# The measured SGLang image plus legal-rag-verifier, so the server can load the serialised mask.
serve_image = (
    modal.Image.from_registry(SGLANG_IMAGE)
    .pip_install(VERIFIER)
    .env({"HF_HOME": HF_HOME, "HF_HUB_OFFLINE": "1", **CONFIG_ENV})
)
download_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("huggingface_hub==0.35.3")
    .env({"HF_HOME": HF_HOME, "HF_HUB_DISABLE_TELEMETRY": "1", **CONFIG_ENV})
)


@app.function(image=download_image, volumes={HF_HOME: weights}, cpu=2.0, memory=4096, timeout=3600)
def download_weights() -> dict[str, object]:
    """Fill the weights volume from Hugging Face at the pinned revision. CPU only; resumable."""
    from huggingface_hub import snapshot_download  # noqa: PLC0415 - only in the container

    t0 = time.monotonic()
    path = Path(snapshot_download(repo_id=MODEL, revision=REVISION))
    if path != SNAPSHOT:
        raise RuntimeError(f"snapshot landed at {path}, expected {SNAPSHOT}")
    weights.commit()
    size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return {"snapshot": str(path), "bytes": size, "seconds": round(time.monotonic() - t0, 1)}


@app.function(
    image=serve_image,
    volumes={HF_HOME: weights},
    gpu=GPU,
    max_containers=1,
    scaledown_window=120,
    startup_timeout=1800,
    secrets=[api_key],
)
@modal.web_server(port=PORT, startup_timeout=1500)
@modal.concurrent(max_inputs=100)
def serve() -> None:
    if not SNAPSHOT.is_dir():  # fail fast rather than burn GPU time on a doomed start
        raise RuntimeError(f"weights missing at {SNAPSHOT}; run fill_weights first")
    key = os.environ.get("JUDGE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("JUDGE_API_KEY is empty in the Modal Secret")
    subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "sglang.launch_server",
            "--model-path",
            MODEL,
            "--revision",
            REVISION,
            "--port",
            str(PORT),
            "--mem-fraction-static",
            "0.85",
            "--reasoning-parser",
            "qwen3",
            "--context-length",
            "65536",
            "--host",
            "0.0.0.0",  # noqa: S104
            "--api-key",
            key,
            # Not in the stop-78 command. Used only by requests that carry
            # custom_logit_processor (the generator's ban fallback); judge requests carry none.
            "--enable-custom-logit-processor",
        ]
    )


@app.function(image=serve_image, cpu=1.0, memory=2048, timeout=600)
def mask_processor() -> str:
    """The serialised SGLang logit mask, built where torch and sglang are installed. No GPU."""
    from legal_rag_verifier.backends._sglang_mask import MaskProcessor  # noqa: PLC0415

    return str(MaskProcessor().to_str())


@app.local_entrypoint()
def get_mask_processor(out: str = "") -> None:
    """Costed run of ``mask_processor``; prints the string, or writes it to ``out``."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ledger  # noqa: PLC0415

    ledger.check("CPU", 600)
    t0 = time.monotonic()
    try:
        text = mask_processor.remote()
    finally:
        ledger.record("mask_processor", "CPU", time.monotonic() - t0)
    if out:
        Path(out).write_text(text, encoding="utf-8")
    else:
        print(text)  # noqa: T201


@app.local_entrypoint()
def fill_weights(estimate_seconds: int = 1800) -> None:
    """Costed run of ``download_weights``: checks the ledger cap first, records the spend after."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ledger  # noqa: PLC0415

    ledger.check("CPU", estimate_seconds)
    t0 = time.monotonic()
    note = "failed"
    try:
        result = download_weights.remote()
        note = f"{result['bytes']} bytes"
        print(result)  # noqa: T201
    finally:
        ledger.record("fill_weights", "CPU", time.monotonic() - t0, note)
