"""Registers available ModelProviders and walks a caller-supplied fallback
order, same reliability shape as the existing prototype's provider
waterfall but behind one interface instead of duplicated per call site
(router.py and sleep_cycle.py each had their own copy of this loop)."""

import logging
import time

from core.models.base import ModelInfo, ModelProvider, ModelRequest, ModelResponse, ModelUnavailableError

logger = logging.getLogger(__name__)


class ModelRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}

    def register(self, provider: ModelProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> ModelProvider | None:
        return self._providers.get(name)

    def provider_names(self) -> list[str]:
        return sorted(self._providers)

    def list_models(self) -> list[ModelInfo]:
        models: list[ModelInfo] = []
        for provider in self._providers.values():
            models.extend(provider.list_models())
        return models

    def describe(self) -> list[dict]:
        """Discovery view of what is provisioned: provider names and the
        models each can serve. Contains no credential — an API key is
        never held here, only inside the provider that was constructed
        with it — so this is safe to return from a status endpoint.

        A provider whose list_models() raises is reported as errored
        rather than omitted: "this provider is misconfigured" and "this
        provider was never configured" are different problems, and a
        status endpoint that renders them identically hides the first."""
        described = []
        for name, provider in sorted(self._providers.items()):
            try:
                models = [
                    {
                        "name": info.name,
                        "supports_tools": info.supports_tools,
                        "cost_tier": info.cost_tier,
                        "context_window": info.context_window,
                    }
                    for info in provider.list_models()
                ]
                described.append({"provider": name, "available": True, "models": models})
            except Exception as exc:  # noqa: BLE001 - a broken provider must not break discovery
                described.append({"provider": name, "available": False, "error": type(exc).__name__, "models": []})
        return described

    def generate_with_fallback(self, request: ModelRequest, provider_order: list[str]) -> ModelResponse:
        """Usage logging lives here rather than in each adapter: it is
        the one place every provider call passes through, so a new
        provider gets latency/outcome logging for free and cannot forget
        to add it. Only the provider name, model name, and elapsed time
        are logged — never the request contents, and never a key (which
        this layer does not hold in the first place; the redaction filter
        in core/observability is the backstop for one arriving inside a
        provider's error text)."""
        last_error: Exception | None = None
        failed_attempts: list[dict[str, str]] = []
        for provider_name in provider_order:
            provider = self._providers.get(provider_name)
            if provider is None:
                continue  # not configured/registered — skip, don't fail the whole request
            started = time.monotonic()
            try:
                response = provider.generate(request)
            except ModelUnavailableError as exc:
                logger.warning(
                    "model provider unavailable: provider=%s elapsed_ms=%d reason=%s",
                    provider_name, (time.monotonic() - started) * 1000, exc,
                )
                last_error = exc
                failed_attempts.append({"provider": provider_name, "error": str(exc)})
                continue
            except Exception as exc:  # noqa: BLE001 - see below
                # An adapter is expected to raise ModelUnavailableError for
                # anything transient. Anything else is a bug -- but letting
                # it escape here skipped every remaining provider and lost
                # the turn with no audit entry, which is strictly worse than
                # trying the next one. Fail over, and log the full traceback
                # at error level so the bug is visible rather than absorbed.
                logger.exception(
                    "model provider raised an unexpected error: provider=%s elapsed_ms=%d type=%s",
                    provider_name, (time.monotonic() - started) * 1000, type(exc).__name__,
                )
                last_error = exc
                failed_attempts.append(
                    {"provider": provider_name, "error": f"{type(exc).__name__}: {exc}"}
                )
                continue
            logger.info(
                "model call ok: provider=%s model=%s elapsed_ms=%d tool_calls=%d",
                provider_name, response.model_name, (time.monotonic() - started) * 1000,
                len(response.tool_calls),
            )
            response.failed_attempts = failed_attempts
            return response
        detail = f"no provider in {provider_order} was available"
        if last_error is not None and not isinstance(last_error, ModelUnavailableError):
            # Name the unexpected failure: "no provider was available" reads
            # like a quota problem, and sending someone to check their API
            # keys when the real cause is a TypeError wastes the debugging.
            detail += f" (last failure: {type(last_error).__name__})"
        raise ModelUnavailableError(detail) from last_error
