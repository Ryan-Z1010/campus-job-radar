"""Optional LLM-powered analysis for CampusJobRadar.

The deterministic collection and eligibility pipeline remains the source of truth
for hard constraints.  This package only adds bounded semantic analysis.
"""

from .agents import CriticAgent, JDUnderstandingAgent, SemanticMatchingAgent
from .cache import LlmAnalysisCache
from .client import (
    DoubaoChatClient,
    LlmClientError,
    LlmResponse,
    OpenAIResponsesClient,
)
from .orchestrator import (
    DEFAULT_LLM_MAX_JOBS,
    DEFAULT_LLM_NOTIFY_MIN_SCORE,
    LlmRecruitmentOrchestrator,
    LlmRunResult,
    deterministic_review_jobs,
    manual_review_analyses,
    notification_jobs,
    review_notification_analyses,
    send_llm_notification_email,
    send_llm_review_notification_email,
    render_manual_review_digest,
    write_llm_review_preview,
    write_llm_notification_preview,
    write_llm_report,
)
from .multi_user import (
    MultiUserLlmOrchestrator,
    MultiUserLlmRunResult,
    MultiUserLlmUserResult,
)

__all__ = [
    "CriticAgent",
    "DEFAULT_LLM_MAX_JOBS",
    "DEFAULT_LLM_NOTIFY_MIN_SCORE",
    "DoubaoChatClient",
    "JDUnderstandingAgent",
    "LlmAnalysisCache",
    "LlmClientError",
    "LlmRecruitmentOrchestrator",
    "LlmResponse",
    "LlmRunResult",
    "MultiUserLlmOrchestrator",
    "MultiUserLlmRunResult",
    "MultiUserLlmUserResult",
    "deterministic_review_jobs",
    "manual_review_analyses",
    "notification_jobs",
    "review_notification_analyses",
    "OpenAIResponsesClient",
    "send_llm_notification_email",
    "send_llm_review_notification_email",
    "render_manual_review_digest",
    "SemanticMatchingAgent",
    "write_llm_notification_preview",
    "write_llm_review_preview",
    "write_llm_report",
]
