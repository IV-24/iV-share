"""Simulated model providers for the Phase 3-7 audit.

WHAT THIS IS AND IS NOT
-----------------------
This registers real `ModelProvider` implementations under the real provider
names (gemini, groq, mistral, claude, openrouter) into a real
`ModelRegistry` inside a real `build_runtime()`. Everything except the HTTP
call to the provider is production code: the orchestrator loop, tool
dispatch, permission checks, the approval gate, delegation budgets, audit
writes, and storage all execute for real.

Registering under the *real* names is the point. `core/agent/roles.py`
gives each role its own `model_provider_order`, so which personality serves
a given turn is decided by iV's own configuration, not by this file. That
makes provider routing observable rather than assumed.

What this CANNOT establish: whether a real free-tier model chooses to
delegate, picks the right specialist, or handles an ambiguous goal well.
That is model behaviour. Every conclusion that depends on it stays
UNVERIFIED until a run with real keys.

Every `generate()` call is recorded to a JSONL trace: which provider was
asked, which role's prompt it received, which tools were offered, and what
came back. That trace is evidence about iV, not about the simulator.

NOTE: the trace has to carry a `sim_turn_id` this simulator invents, because
iV itself has no run/trace identifier to correlate on. That absence is a
Phase 5 finding, and needing to work around it here is a demonstration of it.
"""

from __future__ import annotations

import itertools
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field

from core.models.base import (
    ModelInfo,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelUnavailableError,
    ToolCall,
)

TRACE_PATH = os.environ.get("IV_SIM_TRACE", "audit/sim_trace.jsonl")
_trace_lock = threading.Lock()
_seq = itertools.count(1)

# Set by the scenario driver at the start of each scenario so trace records
# can be grouped. iV supplies nothing equivalent.
CURRENT_TURN: dict[str, str] = {"id": "unset", "scenario": "unset"}


def record(event: dict) -> None:
    event = {
        "seq": next(_seq),
        "at": time.strftime("%H:%M:%S", time.gmtime()),
        "sim_turn_id": CURRENT_TURN["id"],
        "scenario": CURRENT_TURN["scenario"],
        **event,
    }
    with _trace_lock:
        with open(TRACE_PATH, "a") as fh:
            fh.write(json.dumps(event, default=str) + "\n")


# --- identifying which role is asking -------------------------------------
# The system prompt IS role.description (orchestrator.py:107), so the role is
# identifiable from distinctive text in core/agent/roles.py.
_ROLE_SIGNATURES = [
    ("coordinator", "Top-level orchestrator"),
    ("engineering", "Software design, implementation, code review"),
    ("planning", "Roadmaps, milestones, task breakdown"),
    ("research", "Information gathering, comparison, analysis"),
    ("writing", "Creative and professional communication"),
    ("memory", "Knowledge capture, organization, reflection"),
    ("finance", "Budgeting, financial analysis, tracking"),
    ("auditor", "Senior software-engineering review team"),
    ("security", "Permission review, change validation, risk monitoring"),
]


def role_of(system_prompt: str | None) -> str:
    text = system_prompt or ""
    for key, signature in _ROLE_SIGNATURES:
        if signature in text:
            return key
    return "unknown"


def last_user_text(request: ModelRequest) -> str:
    return next((m.content for m in reversed(request.messages) if m.role == "user"), "")


def tool_results_so_far(request: ModelRequest) -> list[dict]:
    """Every tool result already fed back into this turn, parsed. This is how
    the simulated model 'sees' what its earlier tool calls returned — the
    same way a real model would read the tool-role messages."""
    out = []
    for m in request.messages:
        if m.role == "tool":
            try:
                out.append(json.loads(m.content))
            except Exception:
                out.append({"unparsed": m.content})
    return out


def offered(request: ModelRequest) -> list[str]:
    return [t["name"] for t in (request.tools or [])]


def call(_tool, /, **arguments) -> ToolCall:
    """Positional-only first parameter: a tool argument legitimately named
    "name" (create_project takes one) would otherwise collide with it."""
    return ToolCall(id=f"sim-{uuid.uuid4().hex[:12]}", name=_tool, arguments=arguments)


# --- behaviour overrides for failure injection ----------------------------
@dataclass
class Fault:
    """A fault to inject on the next N calls to a given provider."""
    kind: str               # unavailable | malformed_args | bad_tool_args | unknown_tool | slow | empty
    remaining: int = 1
    detail: str = ""


FAULTS: dict[str, Fault] = {}


def set_fault(provider: str, kind: str, times: int = 1, detail: str = "") -> None:
    FAULTS[provider] = Fault(kind=kind, remaining=times, detail=detail)


def clear_faults() -> None:
    FAULTS.clear()


def _take_fault(provider: str) -> Fault | None:
    fault = FAULTS.get(provider)
    if fault is None or fault.remaining <= 0:
        return None
    fault.remaining -= 1
    if fault.remaining <= 0:
        FAULTS.pop(provider, None)
    return fault


# --- the personalities ----------------------------------------------------
@dataclass
class Personality:
    """Mirrors how core/agent/roles.py actually leans on each provider."""
    provider: str
    model_name: str
    context_window: int
    style: str
    # How eagerly this personality reaches for delegation when it is the
    # Coordinator. gemini leads the Coordinator's order; groq/mistral are
    # its fallbacks and are given terser behaviour to make a failover
    # visible in the transcript rather than silent.
    verbosity: str = "normal"


PERSONALITIES = {
    "gemini":     Personality("gemini", "gemini-2.0-flash", 1_000_000, "warm, organised, explains its routing", "normal"),
    "claude":     Personality("claude", "claude-sonnet-4-5", 200_000, "precise, code-literate, cautious about consequential actions", "normal"),
    "groq":       Personality("groq", "llama-3.3-70b-versatile", 128_000, "fast and clipped", "terse"),
    "mistral":    Personality("mistral", "mistral-small-latest", 128_000, "compact and direct", "terse"),
    "openrouter": Personality("openrouter", "llama-3.3-70b-instruct:free", 128_000, "generic", "terse"),
}


class SimulatedProvider(ModelProvider):
    """Behaves like a competent tool-calling model reading the prompt it was
    actually given. The decision logic below is authored, not sampled — it
    reads the offered tool list and the accumulated tool results the same way
    a model would, but it is deterministic so a scenario reproduces exactly."""

    def __init__(self, provider: str) -> None:
        self.name = provider
        self.p = PERSONALITIES[provider]

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(
            name=self.p.model_name, provider=self.name, supports_tools=True,
            cost_tier="free", context_window=self.p.context_window,
        )]

    def generate(self, request: ModelRequest) -> ModelResponse:
        role = role_of(request.system_prompt)
        tools = offered(request)
        results = tool_results_so_far(request)
        user_text = last_user_text(request)

        base = {
            "provider": self.name,
            "model": self.p.model_name,
            "role_serving": role,
            "tools_offered": tools,
            "tool_results_seen": len(results),
            "history_len": len(request.messages),
            "system_prompt_chars": len(request.system_prompt or ""),
            "user_text": user_text[:300],
        }

        fault = _take_fault(self.name)
        if fault:
            base["fault_injected"] = fault.kind
            record({"event": "model_call", **base})
            return self._apply_fault(fault, role)

        response = self._decide(role, tools, results, user_text)
        record({
            "event": "model_call", **base,
            "returned_text": (response.text or "")[:400],
            "returned_tool_calls": [{"name": c.name, "arguments": c.arguments} for c in response.tool_calls],
        })
        return response

    # -- fault injection ---------------------------------------------------
    def _apply_fault(self, fault: Fault, role: str) -> ModelResponse:
        if fault.kind == "unavailable":
            raise ModelUnavailableError(fault.detail or f"{self.name}: simulated 429 rate limit / quota exhausted")
        if fault.kind == "slow":
            time.sleep(float(fault.detail or 2))
            return self._text("...eventually answered after a slow provider round-trip.")
        if fault.kind == "empty":
            return ModelResponse(text="", model_name=self.p.model_name, provider=self.name)
        if fault.kind == "bad_tool_args":
            # Well-formed ToolCall, arguments the handler will reject.
            return ModelResponse(
                text=None, model_name=self.p.model_name, provider=self.name,
                tool_calls=[call("create_task", project_id=12345, title=None, nonexistent_kwarg="boom")],
            )
        if fault.kind == "unknown_tool":
            return ModelResponse(
                text=None, model_name=self.p.model_name, provider=self.name,
                tool_calls=[call("definitely_not_a_registered_tool", x=1)],
            )
        if fault.kind == "malformed_args":
            # Mirrors adapters/models/openai_compatible.py:77 doing
            # json.loads(call.function.arguments) OUTSIDE its try block.
            raise json.JSONDecodeError("Expecting value", "<not json>", 0)
        raise AssertionError(f"unknown fault kind {fault.kind}")

    def _text(self, body: str) -> ModelResponse:
        return ModelResponse(text=body, model_name=self.p.model_name, provider=self.name)

    def _calls(self, *tool_calls: ToolCall, text: str | None = None) -> ModelResponse:
        return ModelResponse(text=text, model_name=self.p.model_name, provider=self.name,
                             tool_calls=list(tool_calls))

    # -- the actual decisions ---------------------------------------------
    def _decide(self, role, tools, results, user_text) -> ModelResponse:
        if role == "coordinator":
            return self._coordinator(tools, results, user_text)
        return self._specialist(role, tools, results, user_text)

    def _coordinator(self, tools, results, user_text) -> ModelResponse:
        t = user_text.lower()
        seen = {r.get("output", {}).get("__tool__") if isinstance(r.get("output"), dict) else None for r in results}

        # SCENARIO A — a question the Coordinator should answer itself.
        if "capital of france" in t or "what can you do" in t:
            return self._text(
                "Paris. That one didn't need a specialist — answering directly is faster "
                "and cheaper than routing it."
            )

        # SCENARIO E — underspecified goal. The prompt says to record work as a
        # task rather than claim background progress, and never to invent detail.
        if t.strip() in ("make it better", "fix it", "sort this out") or "make it better" in t:
            return self._text(
                "I need one more thing before I can act: make *what* better? If you mean iV "
                "itself, say which part — the chat, the memory, the tooling — and I'll route it "
                "to the right specialist. I haven't started anything in the background; nothing "
                "runs between my replies."
            )

        # SCENARIO D — the consequential-action path.
        if "push" in t and ("branch" in t or "remote" in t or "github" in t):
            if not results:
                return self._calls(call(
                    "delegate_to_agent", agent="engineering",
                    task="Push the current working branch to origin.",
                    context="The owner asked for the branch to be pushed to the remote.",
                ), text="Routing this to engineering — it holds the repository tools, I don't.")
            return self._text(
                "Engineering came back blocked: the push needs your approval before it can run. "
                "It's sitting in the approvals queue. Nothing was pushed."
            )

        # SCENARIO C — multi-step, state must carry between steps.
        if "project" in t and ("task" in t or "break" in t or "plan" in t):
            if not results:
                return self._calls(
                    call("create_project", name="Audit Trial Project",
                         description="Created during the Phase 3 orchestration test."),
                    text="Creating the project first — I need its id before I can attach tasks.",
                )
            project_id = None
            for r in results:
                out = r.get("output")
                if isinstance(out, dict) and out.get("id") and "name" in out:
                    project_id = out["id"]
            if project_id and len(results) == 1:
                # Step 2 depends on step 1's output. This is the state-carry test.
                return self._calls(
                    call("create_task", project_id=project_id, title="Draft the audit summary",
                         description="First task, attached to the project created in step 1."),
                    call("create_task", project_id=project_id, title="Review the findings",
                         description="Second task under the same project."),
                    text=f"Got project {project_id}. Attaching both tasks to it.",
                )
            created = [r for r in results if r.get("success")]
            return self._text(
                f"Done — created the project and attached {len(created) - 1} tasks to it. "
                f"The project id carried through from the first step, so both tasks landed "
                f"under the right parent."
            )

        # SCENARIO B — clearly specialist work.
        if any(w in t for w in ("review", "audit", "code", "repository", "security", "research", "compare")):
            if not results:
                agent = "auditor" if ("audit" in t or "review" in t) and "iv" in t else (
                    "security" if "security" in t else
                    "research" if ("research" in t or "compare" in t) else "engineering"
                )
                return self._calls(call(
                    "delegate_to_agent", agent=agent,
                    task=user_text,
                    context="Delegated by the Coordinator; you have tools I do not.",
                ), text=f"This is {agent}'s domain — handing it over.")
            replies = [r.get("output", {}) for r in results if isinstance(r.get("output"), dict)]
            who = next((r.get("agent") for r in replies if r.get("agent")), "the specialist")
            body = next((r.get("response") for r in replies if r.get("response")), "")
            if self.p.verbosity == "terse":
                return self._text(f"Consulted {who}. {body[:200]}")
            return self._text(
                f"I consulted {who}, which ran on its own model with its own tools. "
                f"Here's what came back, in my words: {body[:300]}"
            )

        if self.p.verbosity == "terse":
            return self._text(f"[{self.p.provider}] Noted: {user_text[:120]}")
        return self._text(
            f"I can help with that. ({self.p.provider} serving this turn.) "
            f"You asked: {user_text[:160]}"
        )

    def _specialist(self, role, tools, results, user_text) -> ModelResponse:
        # A specialist that has already run its tools summarises them.
        if results:
            ok = [r for r in results if r.get("success")]
            bad = [r for r in results if not r.get("success")]
            if bad:
                errs = "; ".join(str(r.get("error"))[:120] for r in bad)
                return self._text(f"I couldn't complete that. Tool layer said: {errs}")
            return self._text(f"Done — {len(ok)} tool call(s) completed successfully.")

        t = user_text.lower()

        if role == "engineering":
            if "push" in t and "git_push" in tools:
                return self._calls(call("git_push", repo_name="target", remote="origin", branch="main"),
                                   text="Pushing the branch.")
            if "run" in t and "test" in t and "run_repository_command" in tools:
                return self._calls(call("run_repository_command", repo_name="target", command="pytest -q"),
                                   text="Running the suite.")
            if "read" in t and "read_repository_file" in tools:
                return self._calls(call("read_repository_file", repo_name="target", path="README.md"))
            return self._text("Engineering here. I'd need a cloned repo to work against for that.")

        if role == "auditor":
            if "self_list_files" in tools:
                return self._calls(call("self_list_files", path="core/agent"),
                                   text="Reading iV's own source to ground the review.")
            return self._text("Auditor here, but I wasn't given inspection tools this turn.")

        if role == "security":
            if "flag_denied_action_spikes" in tools:
                return self._calls(call("flag_denied_action_spikes"),
                                   text="Checking for denial spikes.")
            return self._text("Security here. Nothing actionable without the guardian tools.")

        if role == "research":
            if "list_projects" in tools:
                return self._calls(call("list_projects"), text="Pulling current projects to ground the comparison.")
            return self._text("Research here — I have no outbound web tool, so I can only reason from what's stored.")

        if role == "planning" and "create_task" in tools:
            return self._text("Planning here. Give me a project id and I'll break the work down.")

        if role == "finance":
            # Finance deliberately holds no execute scope; only create_approval.
            if "create_approval" in tools:
                return self._calls(call("create_approval", action_type="payment",
                                        description=f"Requested by the owner: {user_text[:120]}"),
                                   text="Finance can't execute anything directly — raising an approval request.")
            return self._text("Finance here. Every consequential action of mine needs approval first.")

        return self._text(f"{role} specialist here. Nothing I can act on with the tools I hold.")


def register_simulated_providers(registry, only: list[str] | None = None) -> list[str]:
    """Overwrites any same-named provider in the registry. Returns the names
    registered, so a caller can assert which personalities are live."""
    names = only or ["gemini", "claude", "groq", "mistral", "openrouter"]
    for name in names:
        registry.register(SimulatedProvider(name))
    return names
