"""Agent runtime: given a role and a message, builds bounded context,
asks the model registry for a response (trying that role's provider order
with fallback), and — if the model asks to call a tool — runs it through
ToolRegistry.execute() (permission + approval enforcement included) and
feeds the result back, repeating until the model returns a final text
answer or max_tool_iterations is hit. This is the one place that loop
exists; every provider adapter only needs to translate its native tool-
call wire format to/from core.models.base.ToolCall — the looping,
permission checks, and approval gating are not the adapter's job.

A role can only reach the tools it's configured with (checked here,
independent of whatever ToolRegistry.list_tools() shows the model); a
model asking for a tool outside that list gets a clean error back as a
tool result, not a crash.

Two batch-level behaviors live here rather than in ToolRegistry, because
they're about how one turn assembles its tool calls, not about any
single tool's execution:

  * delegate_to_agent calls in the same batch run concurrently, bounded
    by MAX_CONCURRENT_DELEGATES — a Coordinator fanning out to three
    specialists waits for the slowest one, not the sum of all three.
    Every other tool call still runs in order, unchanged. Concurrency
    here only works because DelegationBudget (core/agent/delegation.py)
    keeps its per-turn state in a contextvar rather than thread-local
    storage, and copy_context() below carries that same state into each
    worker thread deliberately.
  * A tool result is truncated before it re-enters the conversation. An
    untruncated read_repository_file or self_read_logs on a large file
    can, on its own, exceed a model's context window in a single
    iteration -- and then get resent, still whole, on every later
    iteration of the same turn.
"""

import contextvars
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from core.agent.roles import AgentRole
from core.context.manager import ContextManager
from core.memory.base import Memory, MemoryStore
from core.models.base import ModelMessage, ModelRequest, ModelResponse, ModelUnavailableError
from core.models.registry import ModelRegistry
from core.tools.base import ToolResult
from core.tools.registry import ToolRegistry

DEFAULT_MAX_TOOL_ITERATIONS = 5
MAX_CONCURRENT_DELEGATES = 4
TOOL_RESULT_CHAR_BUDGET = 6000
# A role with no provider whose context_window is known (a misconfigured
# role, or every candidate provider unregistered) falls back to this --
# conservative enough to fit comfortably in the smallest window any
# configured provider here actually has (128k tokens), generous enough
# not to needlessly trim a normal conversation.
DEFAULT_CONTEXT_TOKEN_BUDGET = 24_000
# Reserved out of a role's context budget for the reply itself and for
# whatever the model still has to read beyond the trimmed history (the
# system prompt, the current message, tool schemas).
CONTEXT_REPLY_RESERVE_TOKENS = 4_000

# Deferred import to avoid a module-load cycle: core.agent.delegation
# imports core.agent.roles, not this module, so importing it here at
# call time (rather than at module import time) is a style choice, not a
# correctness requirement -- kept this way so a reader doesn't have to
# check both directions to be sure.
def _delegate_tool_name() -> str:
    from core.agent.delegation import DELEGATE_TOOL_NAME
    return DELEGATE_TOOL_NAME


@dataclass
class AgentTurnResult:
    response: ModelResponse
    role: str
    # "ok"      — the model returned a final answer
    # "partial" — tools ran, then every provider became unavailable before
    #             a final answer could be composed
    # "stuck"   — max_tool_iterations was exhausted without a final answer
    status: str = "ok"
    # Tool names that actually executed during this turn, in order. Set on
    # a partial result so the caller can say what was accomplished rather
    # than reporting that nothing happened.
    completed_tool_calls: list[str] = field(default_factory=list)
    # Provider attempts that failed before one answered, across every model
    # call this turn made.
    failed_provider_attempts: list[dict] = field(default_factory=list)


class AgentOrchestrator:
    def __init__(
        self,
        models: ModelRegistry,
        tools: ToolRegistry,
        memory: MemoryStore,
        context: ContextManager | None = None,
    ) -> None:
        self._models = models
        self._tools = tools
        self._memory = memory
        self._context = context or ContextManager()

    def handle_message(
        self,
        role: AgentRole,
        message: str,
        history: list[ModelMessage] | None = None,
        *,
        relevant_memories: list[Memory] | None = None,
        max_tool_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS,
    ) -> AgentTurnResult:
        token_budget = self._context_token_budget(role)
        messages = self._context.build(history or [], memories=relevant_memories, max_tokens=token_budget)
        messages = [*messages, ModelMessage(role="user", content=message)]

        available_tools = [t for t in self._tools.list_tools() if t["name"] in role.tool_names] or None

        completed: list[str] = []
        provider_failures: list[dict] = []

        for _ in range(max_tool_iterations):
            request = ModelRequest(messages=messages, system_prompt=role.description, tools=available_tools)
            try:
                response = self._models.generate_with_fallback(request, role.model_provider_order)
            except ModelUnavailableError:
                # Providers dying partway through is the normal case on a
                # free tier: the longer a turn runs, the more likely it is
                # to hit a limit late. Letting this propagate discarded
                # `messages` -- every tool call and result accumulated so
                # far -- and the caller reported that nothing had happened.
                # The tools had really run; only the account of them was
                # lost, and the longer and more valuable the turn, the more
                # there was to lose. With nothing yet accomplished there is
                # nothing to report, so the caller's own no-model handling
                # stays the right path and this re-raises.
                if not completed:
                    raise
                return AgentTurnResult(
                    response=_partial_response(completed), role=role.name,
                    status="partial", completed_tool_calls=completed,
                    failed_provider_attempts=provider_failures,
                )

            provider_failures.extend(response.failed_attempts)

            if not response.requests_tool_calls:
                return AgentTurnResult(
                    response=response, role=role.name, status="ok", completed_tool_calls=completed,
                    failed_provider_attempts=provider_failures,
                )

            messages.append(ModelMessage(role="assistant", content=response.text, tool_calls=response.tool_calls))
            results = self._run_tool_calls(role, response.tool_calls)
            for call, result in zip(response.tool_calls, results):
                completed.append(call.name)
                messages.append(
                    ModelMessage(role="tool", content=_serialize_tool_result(result), tool_call_id=call.id)
                )

        stuck = ModelResponse(
            text="iV got stuck in a tool-calling loop and didn't reach a final answer "
                 "after several attempts. Try again, or ask a simpler version of the request.",
            model_name="", provider="",
        )
        return AgentTurnResult(
            response=stuck, role=role.name, status="stuck", completed_tool_calls=completed,
            failed_provider_attempts=provider_failures,
        )

    def run_tool(self, role: AgentRole, tool_name: str, arguments: dict, *, approval_id: str | None = None) -> ToolResult:
        """The enforcement boundary: a role may only invoke tools listed
        in its own tool_names, regardless of what a model response asks
        for. ToolRegistry.execute() separately enforces permission scopes
        and approval requirements underneath this."""
        if tool_name not in role.tool_names:
            self._tools.record_refusal(role.name, tool_name, reason="outside_role_tool_names")
            return ToolResult(
                tool_name=tool_name, success=False,
                error=f"role '{role.name}' is not configured with access to tool '{tool_name}'",
            )
        return self._tools.execute(tool_name, arguments, principal=role.name, approval_id=approval_id)

    def _run_tool_calls(self, role: AgentRole, calls) -> list[ToolResult]:
        """Runs one batch of tool calls, returning results in the same
        order as `calls` regardless of completion order. delegate_to_agent
        calls run concurrently (bounded); everything else runs in order,
        exactly as before -- the common case (no delegation in the batch)
        never touches a thread pool at all."""
        delegate_name = _delegate_tool_name()
        results: list[ToolResult | None] = [None] * len(calls)
        delegate_indices = [i for i, call in enumerate(calls) if call.name == delegate_name]
        other_indices = [i for i in range(len(calls)) if i not in delegate_indices]

        for i in other_indices:
            results[i] = self.run_tool(role, calls[i].name, calls[i].arguments)

        if delegate_indices:
            # copy_context() carries the calling thread's DelegationBudget
            # state into each worker deliberately -- see the module
            # docstring. A worker thread that started this context fresh
            # would see an empty budget and let fan-out run unbounded.
            #
            # One copy per submission, not one copy shared across all of
            # them: a single contextvars.Context object cannot be entered
            # by more than one thread at once (Python raises "cannot enter
            # context: ... is already entered" the moment a second worker
            # tries), even though every independent copy still resolves
            # the same underlying _TurnState -- copy_context() shares the
            # bound value, not the Context object itself.
            with ThreadPoolExecutor(max_workers=min(len(delegate_indices), MAX_CONCURRENT_DELEGATES)) as pool:
                futures = {
                    pool.submit(contextvars.copy_context().run, self.run_tool, role, calls[i].name, calls[i].arguments): i
                    for i in delegate_indices
                }
                for future, i in futures.items():
                    results[i] = future.result()

        return results

    def _context_token_budget(self, role: AgentRole) -> int:
        """The smallest context window among this role's configured
        candidate providers, minus a reserve for the reply -- conservative
        on purpose, since the same built prompt is reused unchanged if
        the first candidate fails over to the next (see
        ModelRegistry.generate_with_fallback), and the smaller provider's
        window is the one that actually has to fit it."""
        windows = [
            info.context_window
            for info in self._models.list_models()
            if info.provider in role.model_provider_order and info.context_window
        ]
        budget = min(windows) if windows else DEFAULT_CONTEXT_TOKEN_BUDGET
        return max(budget - CONTEXT_REPLY_RESERVE_TOKENS, CONTEXT_REPLY_RESERVE_TOKENS)


def _partial_response(completed: list[str]) -> ModelResponse:
    """What the owner sees when the work happened but the summary could
    not. Names the tools that ran, in order, so the reply is an account of
    real state changes rather than an apology."""
    ran = ", ".join(completed)
    return ModelResponse(
        text=(
            "I ran out of model capacity partway through this one, so I can't give you a "
            f"written summary — but the work did happen. Completed, in order: {ran}. "
            "Nothing after that ran. Ask me to continue and I'll pick up from there."
        ),
        model_name="", provider="",
    )


def _serialize_tool_result(result: ToolResult) -> str:
    output = json.dumps(
        {"success": result.success, "output": result.output, "error": result.error, "approval_id": result.approval_id},
        default=str,
    )
    if len(output) > TOOL_RESULT_CHAR_BUDGET:
        omitted = len(output) - TOOL_RESULT_CHAR_BUDGET
        output = output[:TOOL_RESULT_CHAR_BUDGET] + f"... [truncated, {omitted} characters omitted]"
    return output
