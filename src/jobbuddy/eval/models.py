"""Model registry for eval runs.

Maps logical model names to Azure deployment names and API parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelConfig:
    """Configuration for an Azure OpenAI model deployment."""
    deployment: str | None = None  # Azure deployment name; defaults to model key
    api_params: dict = field(default_factory=dict)  # kwargs passed to chat.completions.create
    input_cost_per_m: float = 0.0   # $/1M input tokens
    output_cost_per_m: float = 0.0  # $/1M output tokens (includes reasoning tokens)
    rpm: int = 0                    # Azure deployment RPM limit

    def resolve_deployment(self, model_key: str) -> str:
        """Return the Azure deployment name, falling back to the model key."""
        return self.deployment or model_key

    def cost(self, input_tokens: int, output_tokens: int) -> float | None:
        """Calculate cost in dollars. Returns None if pricing is not configured."""
        if not self.input_cost_per_m and not self.output_cost_per_m:
            return None
        return (input_tokens * self.input_cost_per_m + output_tokens * self.output_cost_per_m) / 1_000_000


# Pricing source: ~/.claude/memory/azure_openai_playground_pricing.md
# (cory-ai-playground-west3, GlobalStandard tier, fetched 2026-05-05)
KNOWN_MODELS: dict[str, ModelConfig] = {
    "gpt-4.1-nano": ModelConfig(
        api_params={"temperature": 1.0},
        input_cost_per_m=0.10, output_cost_per_m=0.40, rpm=5_000,
    ),
    "gpt-4.1-mini": ModelConfig(
        deployment="gpt-41-mini", api_params={"temperature": 1.0},
        input_cost_per_m=0.70, output_cost_per_m=2.80, rpm=5_000,
    ),
    "gpt-5-nano": ModelConfig(
        api_params={"reasoning_effort": "low"},
        input_cost_per_m=0.05, output_cost_per_m=0.40, rpm=5_000,
    ),
    "gpt-5-nano-medium": ModelConfig(
        deployment="gpt-5-nano",
        api_params={"reasoning_effort": "medium"},
        input_cost_per_m=0.05, output_cost_per_m=0.40, rpm=5_000,
    ),
    "gpt-5-nano-high": ModelConfig(
        deployment="gpt-5-nano",
        api_params={"reasoning_effort": "high"},
        input_cost_per_m=0.05, output_cost_per_m=0.40, rpm=5_000,
    ),
    "gpt-5-mini": ModelConfig(
        api_params={"reasoning_effort": "low"},
        input_cost_per_m=0.45, output_cost_per_m=3.60, rpm=1_000,
    ),
    "gpt-5.4-mini": ModelConfig(
        api_params={"reasoning_effort": "low"},
        input_cost_per_m=1.50, output_cost_per_m=9.00, rpm=5_000,
    ),
    "gpt-5.4-mini-high": ModelConfig(
        deployment="gpt-5.4-mini",
        api_params={"reasoning_effort": "high"},
        input_cost_per_m=1.50, output_cost_per_m=9.00, rpm=5_000,
    ),
    "gpt-5.4-nano-medium": ModelConfig(
        deployment="gpt-5.4-nano",
        api_params={"reasoning_effort": "medium"},
        input_cost_per_m=0.20, output_cost_per_m=1.25, rpm=5_000,
    ),
    "gpt-5.4-nano-high": ModelConfig(
        deployment="gpt-5.4-nano",
        api_params={"reasoning_effort": "high"},
        input_cost_per_m=0.20, output_cost_per_m=1.25, rpm=5_000,
    ),
    "DeepSeek-V3.2": ModelConfig(
        api_params={"temperature": 1.0, "response_format": {"type": "json_object"}},
        input_cost_per_m=0.62, output_cost_per_m=1.85, rpm=1_000,
    ),
    "DeepSeek-R1-0528": ModelConfig(
        api_params={"temperature": 1.0},
        # No public meter — likely serverless/managed, set to 0 (cost reports as None)
        rpm=1_000,
    ),
    "grok-3-mini": ModelConfig(
        api_params={"temperature": 1.0},
        input_cost_per_m=0.25, output_cost_per_m=1.27, rpm=1_000,
    ),
    "grok-4-fast-non-reasoning": ModelConfig(
        api_params={"temperature": 1.0},
        input_cost_per_m=0.20, output_cost_per_m=0.50, rpm=1_000,
    ),
    "grok-4-fast-reasoning": ModelConfig(
        api_params={"temperature": 1.0, "response_format": {"type": "json_object"}},
        # grok-4-fast meter is shared across reasoning + non-reasoning variants
        input_cost_per_m=0.20, output_cost_per_m=0.50, rpm=150,
    ),
}
