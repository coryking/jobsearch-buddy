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

    def resolve_deployment(self, model_key: str) -> str:
        """Return the Azure deployment name, falling back to the model key."""
        return self.deployment or model_key

    def cost(self, input_tokens: int, output_tokens: int) -> float | None:
        """Calculate cost in dollars. Returns None if pricing is not configured."""
        if not self.input_cost_per_m and not self.output_cost_per_m:
            return None
        return (input_tokens * self.input_cost_per_m + output_tokens * self.output_cost_per_m) / 1_000_000


KNOWN_MODELS: dict[str, ModelConfig] = {
    "gpt-4.1-nano": ModelConfig(
        api_params={"temperature": 1.0},
        input_cost_per_m=0.10, output_cost_per_m=0.40,
    ),
    "gpt-4.1-mini": ModelConfig(
        deployment="gpt-41-mini", api_params={"temperature": 1.0},
        input_cost_per_m=0.40, output_cost_per_m=1.60,
    ),
    "gpt-5-nano": ModelConfig(
        api_params={"reasoning_effort": "low"},
        input_cost_per_m=0.05, output_cost_per_m=0.40,
    ),
    "gpt-5-mini": ModelConfig(
        api_params={"reasoning_effort": "low"},
        input_cost_per_m=0.30, output_cost_per_m=1.00,
    ),
    "DeepSeek-V3.2": ModelConfig(
        api_params={"temperature": 1.0},
        input_cost_per_m=0.28, output_cost_per_m=0.42,
    ),
    "DeepSeek-R1-0528": ModelConfig(
        api_params={"temperature": 1.0},
        input_cost_per_m=0.55, output_cost_per_m=2.19,
    ),
    "grok-3-mini": ModelConfig(
        api_params={"temperature": 1.0},
        input_cost_per_m=0.30, output_cost_per_m=0.50,
    ),
    "grok-4-fast-non-reasoning": ModelConfig(
        api_params={"temperature": 1.0},
        input_cost_per_m=0.20, output_cost_per_m=0.50,
    ),
}
