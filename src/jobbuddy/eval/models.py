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
    input_cost_per_m: float = 0.0          # $/1M uncached input tokens
    cached_input_cost_per_m: float = 0.0   # $/1M cached input tokens (typically 10-25% of input)
    output_cost_per_m: float = 0.0         # $/1M output tokens (includes reasoning tokens)
    rpm: int = 0                           # Azure deployment RPM limit

    def resolve_deployment(self, model_key: str) -> str:
        """Return the Azure deployment name, falling back to the model key."""
        return self.deployment or model_key

    def cost(
        self,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> float | None:
        """Calculate cost in dollars. Returns None if pricing is not configured.

        ``input_tokens`` is the *total* prompt tokens (matches OpenAI's
        ``usage.prompt_tokens``). ``cached_input_tokens`` is the subset of
        those that hit the prefix cache (``usage.prompt_tokens_details.cached_tokens``)
        and bills at the discounted rate.
        """
        if not self.input_cost_per_m and not self.output_cost_per_m:
            return None
        uncached = max(0, input_tokens - cached_input_tokens)
        return (
            uncached * self.input_cost_per_m
            + cached_input_tokens * self.cached_input_cost_per_m
            + output_tokens * self.output_cost_per_m
        ) / 1_000_000


def find_by_deployment(deployment: str) -> ModelConfig | None:
    """Return a config matching an Azure deployment name, or None.

    Multiple model keys can share a deployment (e.g. -low / -medium / -high
    reasoning variants of gpt-5.4-nano all deploy to ``gpt-5.4-nano``). All
    variants share the same pricing, so the first match is fine for cost
    calculation. Used by sync paths that know only the deployment name from
    settings, not the eval-style ``<model>-<effort>`` key.
    """
    for key, cfg in KNOWN_MODELS.items():
        if cfg.resolve_deployment(key) == deployment:
            return cfg
    return None


# Pricing source: ~/.claude/memory/azure_openai_playground_pricing.md
# (cory-ai-playground-west3, GlobalStandard tier, fetched 2026-05-05)
KNOWN_MODELS: dict[str, ModelConfig] = {
    "gpt-4.1-nano": ModelConfig(
        api_params={"temperature": 1.0},
        input_cost_per_m=0.10, cached_input_cost_per_m=0.025, output_cost_per_m=0.40, rpm=5_000,
    ),
    "gpt-4.1-mini": ModelConfig(
        deployment="gpt-41-mini", api_params={"temperature": 1.0},
        input_cost_per_m=0.70, cached_input_cost_per_m=0.175, output_cost_per_m=2.80, rpm=5_000,
    ),
    "gpt-5-nano": ModelConfig(
        api_params={"reasoning_effort": "low"},
        input_cost_per_m=0.05, cached_input_cost_per_m=0.005, output_cost_per_m=0.40, rpm=5_000,
    ),
    "gpt-5-nano-medium": ModelConfig(
        deployment="gpt-5-nano",
        api_params={"reasoning_effort": "medium"},
        input_cost_per_m=0.05, cached_input_cost_per_m=0.005, output_cost_per_m=0.40, rpm=5_000,
    ),
    "gpt-5-nano-high": ModelConfig(
        deployment="gpt-5-nano",
        api_params={"reasoning_effort": "high"},
        input_cost_per_m=0.05, cached_input_cost_per_m=0.005, output_cost_per_m=0.40, rpm=5_000,
    ),
    "gpt-5-mini": ModelConfig(
        api_params={"reasoning_effort": "low"},
        input_cost_per_m=0.45, cached_input_cost_per_m=0.045, output_cost_per_m=3.60, rpm=1_000,
    ),
    "gpt-5.4-mini": ModelConfig(
        api_params={"reasoning_effort": "low"},
        input_cost_per_m=1.50, cached_input_cost_per_m=0.150, output_cost_per_m=9.00, rpm=5_000,
    ),
    "gpt-5.4-mini-medium": ModelConfig(
        deployment="gpt-5.4-mini",
        api_params={"reasoning_effort": "medium"},
        input_cost_per_m=1.50, cached_input_cost_per_m=0.150, output_cost_per_m=9.00, rpm=5_000,
    ),
    "gpt-5.4-mini-high": ModelConfig(
        deployment="gpt-5.4-mini",
        api_params={"reasoning_effort": "high"},
        input_cost_per_m=1.50, cached_input_cost_per_m=0.150, output_cost_per_m=9.00, rpm=5_000,
    ),
    "gpt-5.4-nano-medium": ModelConfig(
        deployment="gpt-5.4-nano",
        api_params={"reasoning_effort": "medium"},
        input_cost_per_m=0.20, cached_input_cost_per_m=0.020, output_cost_per_m=1.25, rpm=5_000,
    ),
    "gpt-5.4-nano-high": ModelConfig(
        deployment="gpt-5.4-nano",
        api_params={"reasoning_effort": "high"},
        input_cost_per_m=0.20, cached_input_cost_per_m=0.020, output_cost_per_m=1.25, rpm=5_000,
    ),
    "DeepSeek-V3.2": ModelConfig(
        api_params={"temperature": 1.0, "response_format": {"type": "json_object"}},
        input_cost_per_m=0.62, cached_input_cost_per_m=0.310, output_cost_per_m=1.85, rpm=1_000,
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
