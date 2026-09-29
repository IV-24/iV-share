"""Bounded context assembly. Two independent bounds, both applied every
turn: a message-count cap (kept from the original foundation) and a
size-based cap, because a single large tool result can blow a context
window well before 20 messages accumulate.

The size bound is a character-count estimate, not a real tokenizer per
model — each provider has its own tokenizer and none of that lives in
core. ~4 characters per token is the standard rough English-text ratio;
it is deliberately conservative (an estimate that runs a little high
costs nothing, one that runs low risks the exact overflow this exists to
prevent). Real per-model token accounting is the natural next step once
that math is worth the dependency; the interface here (`build()`,
`estimate_tokens()`) is what stays stable if that lands later.
"""

from __future__ import annotations

from core.memory.base import Memory
from core.models.base import ModelMessage

CHARS_PER_TOKEN_ESTIMATE = 4


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN_ESTIMATE)


class ContextManager:
    def __init__(self, max_messages: int = 20) -> None:
        self.max_messages = max_messages

    def build(
        self,
        history: list[ModelMessage],
        *,
        memories: list[Memory] | None = None,
        max_tokens: int | None = None,
    ) -> list[ModelMessage]:
        bounded = history[-self.max_messages :] if self.max_messages else list(history)

        preamble = None
        if memories:
            memory_note = "\n".join(f"- ({m.memory_type.value}) {m.content}" for m in memories)
            preamble = ModelMessage(role="system", content=f"Relevant memory:\n{memory_note}")

        if max_tokens is not None:
            bounded = self._fit_to_budget(bounded, reserved=estimate_tokens(preamble.content) if preamble else 0,
                                           max_tokens=max_tokens)

        return ([preamble] + bounded) if preamble else bounded

    def _fit_to_budget(
        self, messages: list[ModelMessage], *, reserved: int, max_tokens: int
    ) -> list[ModelMessage]:
        """Drops the oldest messages first until the estimated total fits
        `max_tokens`. Always keeps the most recent message, even if it
        alone exceeds the budget on its own — trimming it away would
        silently drop the user's actual question, which is a worse
        failure than sending an oversized request and finding out from
        the provider."""
        if not messages:
            return messages

        kept: list[ModelMessage] = [messages[-1]]
        total = reserved + estimate_tokens(messages[-1].content)
        for message in reversed(messages[:-1]):
            cost = estimate_tokens(message.content)
            if total + cost > max_tokens:
                break
            kept.insert(0, message)
            total += cost
        return kept
