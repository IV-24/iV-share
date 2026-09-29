"""A deterministic, dependency-free ModelProvider: no API key, no
network. Exists for tests and for running the rest of core (tools,
permissions, approvals, agent orchestration) end-to-end without any real
model configured — proving those layers don't secretly depend on a
specific provider being present."""

from core.models.base import ModelInfo, ModelProvider, ModelRequest, ModelResponse


class NullModelProvider(ModelProvider):
    name = "null"

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(name="null-echo", provider=self.name, supports_tools=False, cost_tier="free")]

    def generate(self, request: ModelRequest) -> ModelResponse:
        last_user_message = next(
            (m.content for m in reversed(request.messages) if m.role == "user"), ""
        )
        return ModelResponse(
            text=f"[null provider echo] {last_user_message}",
            model_name="null-echo",
            provider=self.name,
        )
