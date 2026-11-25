"""The generation and audit policy: :class:`~chapterverse.contracts.Policy` for one answer.

:func:`build_policy` fills ``prompt_sha256`` from the generation prompt; the retry limits, the
refusal sentence, the steering mode and the verifier config keep the contract's defaults (3 retries
per point, 8 per answer, :data:`ABSTAIN_TEXT`, ``inject``, ``STRICT_FIGURES``).
:func:`verifier_config` turns the policy's config name back into the verifier's config object.
"""

from __future__ import annotations

from typing import Final

from legal_rag_verifier.judge import STRICT_FIGURES
from legal_rag_verifier.verifier import VerifierConfig

from chapterverse.audit.prompt import ABSTAIN_TEXT, PROMPT_SHA256
from chapterverse.contracts import Policy

__all__ = [
    "ABSTAIN_TEXT",
    "ALL_PASSAGES_CONFIG",
    "CITED_PASSAGES_CONFIG",
    "SECTION_PASSAGES_CONFIG",
    "VERIFIER_CONFIGS",
    "build_policy",
    "verifier_config",
]

ALL_PASSAGES_CONFIG: Final = "STRICT_FIGURES"
CITED_PASSAGES_CONFIG: Final = "STRICT_FIGURES+CITED_JUDGE"
SECTION_PASSAGES_CONFIG: Final = "STRICT_FIGURES+SECTION_JUDGE"

VERIFIER_CONFIGS: Final[dict[str, VerifierConfig]] = {
    ALL_PASSAGES_CONFIG: STRICT_FIGURES,
    CITED_PASSAGES_CONFIG: STRICT_FIGURES,
    SECTION_PASSAGES_CONFIG: STRICT_FIGURES,
}
"""Verifier configs a policy may name. All use ``STRICT_FIGURES``; the second restricts the judge
to cited passages and the third to all passages of the cited sections."""


def build_policy(
    judge_preset: str,
    *,
    verifier_config_name: str = ALL_PASSAGES_CONFIG,
) -> Policy:
    """The policy for an answer judged with ``judge_preset`` under the current prompt.

    Raises:
        ValueError: ``judge_preset`` is empty or ``verifier_config_name`` is unknown.
    """
    if not judge_preset.strip():
        raise ValueError("judge_preset must name a judge preset")
    if verifier_config_name not in VERIFIER_CONFIGS:
        raise ValueError(f"unknown verifier config {verifier_config_name!r}")

    return Policy(
        judge_preset=judge_preset,
        prompt_sha256=PROMPT_SHA256,
        refusal_text=ABSTAIN_TEXT,
        verifier_config_name=verifier_config_name,
    )


def verifier_config(policy: Policy) -> VerifierConfig:
    """The verifier config ``policy`` names.

    Raises:
        ValueError: the name is not in :data:`VERIFIER_CONFIGS`.
    """
    try:
        return VERIFIER_CONFIGS[policy.verifier_config_name]
    except KeyError:
        raise ValueError(f"unknown verifier config {policy.verifier_config_name!r}") from None
