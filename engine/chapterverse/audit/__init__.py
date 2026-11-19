"""Audit: prompt builder, verified generation and the verified-sentence stream (agent B4).

* :mod:`.prompt`: the generation prompt (passages ``P1..Pn``) and ``PROMPT_SHA256``.
* :mod:`.refs`: passage references and the citation strings the model must not write.
* :mod:`.verify`: the shared sentence check (``AuditVerifier``) and the judge-call log.
* :mod:`.continuation`: ``ChatContinuationGenerator`` for Groq and Cloudflare (block pattern).
* :mod:`.modal_path`: ``ModalPathGenerator`` for Modal (``InFlightGenerator`` on SGLang).
* :mod:`.policy`: ``build_policy`` and ``verifier_config``.
"""

from chapterverse.audit.continuation import ChatContinuationGenerator, GenerationSettings
from chapterverse.audit.modal_path import BearerPost, ModalPathGenerator, ModalSGLangBackend
from chapterverse.audit.policy import build_policy, verifier_config
from chapterverse.audit.prompt import PROMPT_SHA256, PriorTurn, Prompt, SessionState, build_prompt
from chapterverse.audit.refs import RefCheck, check_refs

__all__ = [
    "PROMPT_SHA256",
    "BearerPost",
    "ChatContinuationGenerator",
    "GenerationSettings",
    "ModalPathGenerator",
    "ModalSGLangBackend",
    "PriorTurn",
    "Prompt",
    "RefCheck",
    "SessionState",
    "build_policy",
    "build_prompt",
    "check_refs",
    "verifier_config",
]
