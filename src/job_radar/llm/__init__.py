"""Optional LLM-powered analysis for CampusJobRadar.

The deterministic collection and eligibility pipeline remains the source of truth
for hard constraints.  This package only adds bounded semantic analysis.
"""

from .agents import CriticAgent, JDUnderstandingAgent, SemanticMatchingAgent
from .cache import LlmAnalysisCache
from .client import LlmClientError, LlmResponse, OpenAIResponsesClient
from .orchestrator import (
    LlmRecruitmentOrchestrator,
    LlmRunResult,
    write_llm_report,
)

__all__ = [
    "CriticAgent",
    "JDUnderstandingAgent",
    "LlmAnalysisCache",
    "LlmClientError",
    "LlmRecruitmentOrchestrator",
    "LlmResponse",
    "LlmRunResult",
    "OpenAIResponsesClient",
    "SemanticMatchingAgent",
    "write_llm_report",
]
