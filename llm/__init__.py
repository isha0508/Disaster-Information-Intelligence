"""Phase 7 grounded LLM and generative decision-support layer."""

from llm.config import LLMConfig
from llm.generator import (
    explain_priority,
    generate_incident_summary,
    generate_intelligence,
    generate_intelligence_batch,
    generate_recommendations,
    generate_situation_report,
)
from llm.grounding import build_grounded_context
from llm.providers import LLMProvider, MockLLMProvider, OpenAICompatibleProvider

__all__ = [
    "LLMConfig", "LLMProvider", "MockLLMProvider", "OpenAICompatibleProvider",
    "build_grounded_context", "generate_intelligence", "generate_intelligence_batch",
    "generate_incident_summary", "explain_priority", "generate_recommendations",
    "generate_situation_report",
]
