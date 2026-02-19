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

    def resolve_deployment(self, model_key: str) -> str:
        """Return the Azure deployment name, falling back to the model key."""
        return self.deployment or model_key


KNOWN_MODELS: dict[str, ModelConfig] = {
    "gpt-4.1-nano": ModelConfig(api_params={"temperature": 1.0}),
    "gpt-4.1-mini": ModelConfig(deployment="gpt-41-mini", api_params={"temperature": 1.0}),
    "gpt-5-nano": ModelConfig(api_params={"reasoning_effort": "low"}),
    "gpt-5-mini": ModelConfig(api_params={"reasoning_effort": "low"}),
    "DeepSeek-V3.2": ModelConfig(api_params={"temperature": 1.0}),
}
