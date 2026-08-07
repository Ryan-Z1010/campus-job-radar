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
    DEFAULT_LLM_NOTIFY_MIN_SCORE,
    LlmRecruitmentOrchestrator,
    LlmRunResult,
    notification_jobs,
    send_llm_notification_email,
    write_llm_notification_preview,
    write_llm_report,
)

__all__ = [
    "CriticAgent",
    "DEFAULT_LLM_NOTIFY_MIN_SCORE",
    "DoubaoChatClient",
    "JDUnderstandingAgent",
    "LlmAnalysisCache",
    "LlmClientError",
    "LlmRecruitmentOrchestrator",
    "LlmResponse",
    "LlmRunResult",
    "notification_jobs",
    "OpenAIResponsesClient",
    "send_llm_notification_email",
    "SemanticMatchingAgent",
    "write_llm_notification_preview",
    "write_llm_report",
]
