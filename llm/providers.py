"""Provider abstraction, deterministic mock, and optional OpenAI-compatible HTTP adapter."""

from abc import ABC, abstractmethod
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen



class LLMProvider(ABC):
    """Interface implemented by local mocks and remote text-generation providers."""
    @abstractmethod
    def generate(self, prompt, context, config):
        """Return a provider payload (normally a mapping of narrative fields)."""


class MockLLMProvider(LLMProvider):
    """Deterministic provider for tests, offline use, and demonstrations."""
    name = "mock"

    def generate(self, prompt, context, config):
        evidence = context.get("evidence", {})
        intelligence = context.get("computed_intelligence", {})
        spatial = context.get("spatial", {})
        loc = spatial.get("normalized_location") or spatial.get("location_text")
        dtype = evidence.get("disaster_type")
        dtype_text = ", ".join(map(str, dtype)) if isinstance(dtype, list) else str(dtype) if dtype else None
        statements = []
        if dtype_text:
            statements.append(f"Structured evidence identifies the disaster type as {dtype_text}.")
        if loc:
            statements.append(f"The incident record mentions {loc}.")
        for field, label in (("casualties", "casualty"), ("displaced", "displacement"),
                             ("rescue", "rescue"), ("infrastructure", "infrastructure"),
                             ("requests", "request")):
            values = evidence.get(field) or []
            if values:
                statements.append(f"Structured {label} evidence: " + "; ".join(map(str, values)) + ".")
        summary = " ".join(statements) or "No structured incident evidence was provided."
        levels = []
        for prefix, title in (("severity", "Severity"), ("urgency", "Urgency"),
                              ("priority", "Priority"), ("confidence", "Confidence")):
            score = intelligence.get(f"{prefix}_score")
            level = intelligence.get(f"{prefix}_level")
            levels.append(f"{title}: {level or 'unavailable'}" + (f" ({score}/100)" if score is not None else " (score unavailable)"))
        assessment = "Phase 5 computed intelligence: " + "; ".join(levels) + "."
        explanations = []
        factors = intelligence.get("priority_factors")
        if isinstance(factors, list):
            for factor in factors[:6]:
                if isinstance(factor, dict):
                    label = factor.get("factor", "factor")
                    contribution = factor.get("contribution")
                    explanations.append(f"Phase 5 priority includes the {label} factor" +
                                        (f" (contribution {contribution})" if contribution is not None else "") + ".")
        if not explanations:
            explanations.append("No Phase 5 priority factor breakdown was provided; the priority score is unavailable for further explanation." if intelligence.get("priority_score") is None else
                                f"The supplied Phase 5 priority is {intelligence['priority_score']}/100; no factor breakdown was provided.")
        actions = []
        if evidence.get("rescue"):
            actions.append("Recommended action: coordinate a prompt assessment of the reported rescue situation.")
        if evidence.get("casualties"):
            actions.append("Recommended action: assess reported casualties and coordinate appropriate medical review.")
        if evidence.get("infrastructure"):
            actions.append("Recommended action: verify reported infrastructure damage with the relevant response team.")
        requests = context.get("resources", {}).get("explicit_requests") or []
        if requests:
            raw = [item.get("raw_text") or item.get("category") for item in requests if isinstance(item, dict)]
            raw.extend(item for item in requests if isinstance(item, str))
            if raw:
                actions.append("Recommended action: review and confirm the explicitly recorded request(s): " + "; ".join(map(str, raw)) + ".")
        if evidence.get("displaced"):
            actions.append("Recommended action: assess the reported displacement and related shelter needs.")
        if not actions:
            actions.append("Recommended action: confirm the incident details and collect any missing operational information.")
        if spatial.get("geocoding_status") != "success":
            actions.append("Recommended action: resolve or confirm the location before using geographic coordinates operationally.")
        if not config.recommendations_enabled:
            actions = []
        return {"summary": summary, "situation_assessment": assessment,
                "priority_explanation": explanations, "recommended_actions": actions}


class OpenAICompatibleProvider(LLMProvider):
    """Optional chat-completions adapter using only Python's standard library."""
    name = "openai_compatible"

    def generate(self, prompt, context, config):
        if not config.api_key:
            raise RuntimeError("LLM_API_KEY is required for the openai_compatible provider")
        body = json.dumps({
            "model": config.model,
            "temperature": config.temperature,
            "max_tokens": config.max_output_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
        }).encode("utf-8")
        request = Request(config.api_base_url + "/chat/completions", data=body,
                          headers={"Authorization": "Bearer " + config.api_key,
                                   "Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=config.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"LLM provider returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"LLM provider request failed: {type(exc).__name__}") from exc
        try:
            content = payload["choices"][0]["message"]["content"]
            parsed = json.loads(content) if isinstance(content, str) else content
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("LLM provider returned malformed JSON content") from exc
        if not isinstance(parsed, dict):
            raise ValueError("LLM provider returned a non-object JSON value")
        return parsed


def create_provider(config):
    """Construct the configured provider without requiring remote credentials in mock mode."""
    if config.provider == "mock":
        return MockLLMProvider()
    if config.provider in {"openai", "openai_compatible"}:
        return OpenAICompatibleProvider()
    raise ValueError(f"Unsupported LLM_PROVIDER: {config.provider}")
