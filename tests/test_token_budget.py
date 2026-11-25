"""Unit tests for eval/token_budget.py on the fixture store (no real store, no network)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from legal_rag_router import Router

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chapterverse.audit.policy import build_policy  # noqa: E402
from chapterverse.contracts import (  # noqa: E402
    AllProvidersUnavailable,
    ChatMessage,
    ChatRequest,
    Event,
    Rotation,
    TemporalStatus,
    Window,
)
from chapterverse.pipeline import AskRequest, Pipeline  # noqa: E402
from chapterverse.providers.ledger import JUDGE_OUTPUT_RESERVE  # noqa: E402
from chapterverse.providers.presets import JUDGE_PRESET_NAME  # noqa: E402
from eval.run import NoModelRotation  # noqa: E402
from eval.token_budget import (  # noqa: E402
    CapturingRotation,
    answers_per_minute,
    build_judge_request,
    main,
    measure_row,
    render_markdown,
    shape_reservation,
    summarize_measurements,
)

S124_ROW: dict[str, Any] = {
    "id": "F01",
    "split": "dev",
    "question": (
        "What is the limit of the compensatory award under section 124 of the "
        "Employment Rights Act 1996?"
    ),
    "must_reach": ["uk/ukpga/1996/18/s124"],
}


def _fixture_pipeline(router: Router, store: Any, rotation: CapturingRotation) -> Pipeline:
    return Pipeline(
        router=router,
        store=store,
        rotation=rotation,
        policy=build_policy(JUDGE_PRESET_NAME),
    )


def test_capturing_rotation_records_and_fails_like_no_model_rotation(
    fixture_router: Router, fixture_text_store: Any
) -> None:
    rot = CapturingRotation()
    assert isinstance(rot, Rotation)
    assert rot.available()

    req = ChatRequest(
        messages=(ChatMessage(role="user", content="hello"),),
        max_tokens=100,
        temperature=0.7,
        top_p=0.8,
    )
    with pytest.raises(AllProvidersUnavailable) as cap_exc:
        rot.complete(req)
    with pytest.raises(AllProvidersUnavailable) as no_exc:
        NoModelRotation().complete(req)

    assert len(rot.requests) == 1
    assert rot.requests[0] is req
    assert str(cap_exc.value) == str(no_exc.value)

    rot.requests.clear()
    pipeline = _fixture_pipeline(fixture_router, fixture_text_store, rot)
    events: list[Event] = []
    pipeline.run(AskRequest(query=S124_ROW["question"]), events.append)

    assert len(rot.requests) == 1
    captured = rot.requests[0]
    assert captured.purpose == "generate"
    assert any(m["role"] == "system" for m in captured.messages)
    assert any(
        m["role"] == "user" and S124_ROW["question"] in m["content"] for m in captured.messages
    )


def test_cited_scope_produces_fewer_judge_chars_than_all(
    fixture_router: Router, fixture_text_store: Any
) -> None:
    rot_all = CapturingRotation()
    pipe_all = _fixture_pipeline(fixture_router, fixture_text_store, rot_all)
    res_all = measure_row(pipe_all, rot_all, S124_ROW, judge_scope="all")

    rot_cited = CapturingRotation()
    pipe_cited = _fixture_pipeline(fixture_router, fixture_text_store, rot_cited)
    res_cited = measure_row(pipe_cited, rot_cited, S124_ROW, judge_scope="cited")

    assert res_all["windows_sent"]["count"] > 1
    assert res_all["generate"] is not None
    assert res_all["judge"] is not None
    assert res_cited["judge"] is not None

    assert res_cited["judge"]["total_chars"] < res_all["judge"]["total_chars"]
    assert res_cited["judge"]["ledger_tokens"] < res_all["judge"]["ledger_tokens"]
    assert res_cited["judge"]["ledger_reservation"] < res_all["judge"]["ledger_reservation"]

    assert res_all["representative_sentence"] is not None
    assert "[P1]" not in res_all["representative_sentence"]

    gen = res_all["generate"]
    assert 0.0 < gen["premise_share"] < 1.0
    assert gen["ledger_reservation"] == gen["ledger_tokens"] + gen["max_tokens"]
    assert res_all["judge"]["ledger_reservation"] == (
        res_all["judge"]["ledger_tokens"] + JUDGE_OUTPUT_RESERVE
    )


def test_section_scope_keeps_windows_of_p1_section_only() -> None:
    windows = (
        Window(
            passage_id="P1",
            coordinate="uk/ukpga/1996/18/s119/1",
            heading="ERA 1996, s.119(1)",
            text="First subsection of section 119.",
            status=TemporalStatus.IN_FORCE,
            version_date="2026-01-01",
            source_url="https://www.legislation.gov.uk/ukpga/1996/18/section/119/1",
        ),
        Window(
            passage_id="P2",
            coordinate="uk/ukpga/1996/18/s119/2",
            heading="ERA 1996, s.119(2)",
            text="Second subsection of section 119.",
            status=TemporalStatus.IN_FORCE,
            version_date="2026-01-01",
            source_url="https://www.legislation.gov.uk/ukpga/1996/18/section/119/2",
        ),
        Window(
            passage_id="P3",
            coordinate="uk/ukpga/1996/18/s124/1",
            heading="ERA 1996, s.124(1)",
            text="Different section 124 content.",
            status=TemporalStatus.IN_FORCE,
            version_date="2026-01-01",
            source_url="https://www.legislation.gov.uk/ukpga/1996/18/section/124/1",
        ),
    )
    _, req_all = build_judge_request(windows, scope="all")
    _, req_cited = build_judge_request(windows, scope="cited")
    _, req_sec = build_judge_request(windows, scope="section")

    sec_user = req_sec.messages[1]["content"]
    assert "First subsection of section 119." in sec_user
    assert "Second subsection of section 119." in sec_user
    assert "Different section 124 content." not in sec_user
    assert (
        len(req_cited.messages[1]["content"]) < len(sec_user) < len(req_all.messages[1]["content"])
    )


def test_answers_per_minute_arithmetic() -> None:
    gen_res = 2000
    judge_res = 1000

    res_a = shape_reservation(gen_res, judge_res, 1, 2)
    res_b = shape_reservation(gen_res, judge_res, 2, 3)
    res_c = shape_reservation(gen_res, judge_res, 3, 6)

    assert res_a == 2000 + 2 * 1000
    assert res_b == (2000 + 2040) + 3 * 1000
    assert res_c == (2000 + 2040 + 2080) + 6 * 1000

    assert answers_per_minute(res_a) == 2
    assert answers_per_minute(res_b) == 1
    assert answers_per_minute(res_c) == 0
    assert answers_per_minute(4000) == 2
    assert answers_per_minute(4001) == 1
    assert answers_per_minute(8001) == 0
    assert answers_per_minute(0) == 0


def test_main_writes_outputs_on_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fixture_router: Router,
    fixture_text_store: Any,
) -> None:
    battery = tmp_path / "battery.jsonl"
    battery.write_text(
        json.dumps(S124_ROW)
        + "\n"
        + json.dumps({"id": "F02", "split": "dev", "question": "No must reach", "must_reach": []})
        + "\n",
        encoding="utf-8",
    )

    def fake_builder(
        router_repo: Path,
        dense_dir: Path | None = None,
        models_dir: Path = Path("artefacts/models"),
    ) -> Pipeline:
        del router_repo, dense_dir, models_dir
        return _fixture_pipeline(fixture_router, fixture_text_store, CapturingRotation())

    monkeypatch.setattr("eval.token_budget._build_pipeline", fake_builder)

    out_dir = tmp_path / "out"
    rc = main(
        [
            "--split",
            "dev",
            "--battery",
            str(battery),
            "--out",
            str(out_dir),
            "--judge-scope",
            "cited",
        ]
    )
    assert rc == 0

    json_path = out_dir / "token-budget.json"
    md_path = out_dir / "token-budget.md"
    assert json_path.is_file()
    assert md_path.is_file()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["summary"]["rows_total"] == 1
    assert payload["summary"]["rows_measured"] == 1
    assert payload["summary"]["judge_scope"] == "cited"
    assert len(payload["summary"]["answer_shapes"]) == 3

    md_text = md_path.read_text(encoding="utf-8")
    assert "Ledger and rotation mechanics" in md_text
    for forbidden in ("\u2014", "\u2013", "\u2192", "->", "\u2265", "\u2248", "\u00d7"):
        assert forbidden not in md_text

    summary = summarize_measurements(payload["rows"], judge_scope="cited")
    assert summary["answer_shapes"] == payload["summary"]["answer_shapes"]
    assert "Token Budget" in render_markdown(payload)

    sec_dir = tmp_path / "out-section"
    assert (
        main(
            [
                "--split",
                "dev",
                "--battery",
                str(battery),
                "--out",
                str(sec_dir),
                "--judge-scope",
                "section",
            ]
        )
        == 0
    )
    sec_payload = json.loads((sec_dir / "token-budget.json").read_text(encoding="utf-8"))
    assert sec_payload["summary"]["judge_scope"] == "section"
