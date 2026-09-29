"""Role definitions, transcribed from docs/AGENT_ROLES.md and
IV_CONSTITUTION.md as data. An AgentRole is a policy object: which tools
it may call, which permission scopes it's granted, and which model
providers it prefers, in priority order. The runtime (AgentOrchestrator)
is identical for every role — only this configuration differs, which is
what makes adding a ninth role a data change, not a rewrite.

Naming: the original prototype used a feudal hierarchy (King, Lords,
Merchants, Doctors, Guards, Serfs). That vocabulary is retired here in
favor of conventional terms with the same conceptual roles:

  King        -> Coordinator   (single top-level orchestrating identity)
  Lords       -> Specialists   (persistent, domain-scoped roles below)
  Merchants   -> Gatherers     (external resource/API fetching)
  Doctors     -> Investigators (debugging, root-cause analysis)
  Serfs       -> Workers       (ephemeral, task-scoped, no standing role)
  Guards      -> (retired as a persona entirely — enforcement is now
                  literally core/permissions + core/approvals in code,
                  not a role that has to be reasoned into existence)

The Security specialist is deliberately the only default role with
permissions.manage in its scope list, per the spec's "Security role
should have special authority over permissions, tool risk, and
extension/security decisions." It's also the only role with access to
the Guardian tools (core/tools/guardian.py) — noticing when another
principal's activity looks like it's drifting outside its declared
scope, and suspending it immediately if so. See docs/HAVEN.md. The
Finance specialist has no AUTO-execution scopes for anything
consequential — financial.execute is intentionally absent, forcing
every financial tool through REQUIRES_APPROVAL.
"""

from dataclasses import dataclass, field

from core.permissions.scopes import PermissionScope


@dataclass
class AgentRole:
    name: str
    description: str
    tool_names: list[str] = field(default_factory=list)  # empty = no tool access
    permission_scopes: list[str] = field(default_factory=list)
    model_provider_order: list[str] = field(default_factory=lambda: ["null"])


DEFAULT_ROLES: dict[str, AgentRole] = {
    "coordinator": AgentRole(
        name="Coordinator",
        description=(
            "Top-level orchestrator. You understand the owner's intent and route work to "
            "the specialist best suited to it, rather than attempting everything yourself.\n\n"
            "You have delegate_to_agent. Use it. Each specialist runs on its own model with "
            "its own tools: engineering work goes to engineering (which has repository "
            "access you do not), research to research, writing to writing, planning to "
            "planning, security questions to security, and a review of iV's own code to "
            "auditor. Call list_agents when you are unsure who exists. You can also compose "
            "a new agent with create_agent when a job recurs and no existing specialist fits.\n\n"
            "Delegate for genuine specialist work, not for everything — answering a simple "
            "question yourself is faster and cheaper than routing it. When you do delegate, "
            "give the specialist the context it cannot see, then synthesize what comes back "
            "into one answer in your own voice. Say which specialists you consulted.\n\n"
            "Never claim work is happening in the background. Nothing runs between your "
            "replies: when you finish answering, iV stops until the owner speaks again. If "
            "something still needs doing, say so plainly and record it as a task.\n\n"
            "Durable facts about the owner and their work belong in memory. A short list of "
            "recent, high-importance memories is injected above each turn; call "
            "list_recent_memories when you need more than that, and create_memory when the "
            "owner tells you something worth remembering after this conversation ends.\n\n"
            "You can also play chess against the owner (start_chess_game, get_chess_board, "
            "make_chess_move). For anything past an opening move, delegate to planning or "
            "research for candidate-move analysis before committing one yourself with "
            "make_chess_move -- both specialists can call get_chess_board directly if you give "
            "them the game_id."
        ),
        tool_names=[
            "list_projects", "list_tasks_for_project", "create_project", "create_task",
            "delegate_to_agent", "list_agents", "create_agent",
            # The Coordinator is the only agent that spans conversations,
            # so it is the one that needs to read and write long-term
            # memory. Without these the memory store was write-only from
            # the chat path: the nightly reflection cycle wrote entries
            # nothing ever read back.
            "create_memory", "list_recent_memories",
            "start_chess_game", "get_chess_board", "make_chess_move",
        ],
        permission_scopes=[PermissionScope.DATABASE_READ, PermissionScope.DATABASE_WRITE],
        model_provider_order=["gemini", "mistral", "groq"],
    ),
    "planning": AgentRole(
        name="Planning Specialist",
        description="Roadmaps, milestones, task breakdown, dependencies. Also consulted by the "
                     "Coordinator for chess move analysis -- call get_chess_board with the game_id "
                     "you're given to see the position before recommending a move.",
        tool_names=["create_project", "create_task", "update_task_status", "get_chess_board"],
        permission_scopes=[PermissionScope.DATABASE_READ, PermissionScope.DATABASE_WRITE],
        model_provider_order=["gemini", "mistral", "groq"],
    ),
    "engineering": AgentRole(
        name="Engineering Specialist",
        description="Software design, implementation, code review. The only default role "
                     "with repo-editing tools (adapters/workspace) -- can clone a repo, read/"
                     "write files, and commit locally without approval; running arbitrary "
                     "commands, pushing, and opening pull requests all require human approval.",
        tool_names=[
            "create_task", "update_task_status", "create_improvement",
            "clone_repository", "read_repository_file", "list_repository_files",
            "write_repository_file", "git_status", "git_diff", "git_commit", "create_branch",
            "run_repository_command", "git_push", "create_pull_request",
        ],
        permission_scopes=[
            PermissionScope.DATABASE_READ, PermissionScope.DATABASE_WRITE,
            PermissionScope.CODE_EXECUTE, PermissionScope.GITHUB_READ, PermissionScope.GITHUB_WRITE,
            PermissionScope.FILESYSTEM_READ, PermissionScope.FILESYSTEM_WRITE,
            PermissionScope.NETWORK_REQUEST,
        ],
        model_provider_order=["claude", "gemini", "mistral", "groq"],
    ),
    "research": AgentRole(
        name="Research Specialist",
        description="Information gathering, comparison, analysis. Also consulted by the "
                     "Coordinator for chess move analysis -- call get_chess_board with the "
                     "game_id you're given to see the position before recommending a move.",
        tool_names=["list_projects", "list_tasks_for_project", "list_recent_memories", "get_chess_board"],
        permission_scopes=[PermissionScope.DATABASE_READ, PermissionScope.NETWORK_REQUEST],
        model_provider_order=["gemini", "mistral", "groq"],
    ),
    "writing": AgentRole(
        name="Writing Specialist",
        description="Creative and professional communication.",
        tool_names=[],
        permission_scopes=[PermissionScope.DATABASE_READ],
        model_provider_order=["gemini", "mistral", "groq"],
    ),
    "memory": AgentRole(
        name="Memory Specialist",
        description="Knowledge capture, organization, reflection.",
        # create_reflection does not exist as a registered tool -- the
        # nightly Sleep Cycle (core/reflection/base.py) is what actually
        # writes reflective memories today. If this role ever needs to
        # record a reflection mid-conversation, add and register that
        # tool first; naming it here without registering it makes a
        # model's call to it fail after the fact instead of at startup.
        tool_names=["create_memory"],
        permission_scopes=[PermissionScope.DATABASE_READ, PermissionScope.DATABASE_WRITE],
        model_provider_order=["gemini", "mistral", "groq"],
    ),
    "finance": AgentRole(
        name="Finance Specialist",
        description="Budgeting, financial analysis, tracking. Every consequential "
                     "action requires approval — this role is never granted "
                     "financial.execute directly.",
        tool_names=["create_approval"],
        permission_scopes=[PermissionScope.DATABASE_READ],
        model_provider_order=["gemini", "mistral", "groq"],
    ),
    "auditor": AgentRole(
        name="Auditor",
        description="Senior software-engineering review team, reviewing iV itself. Reads iV's own "
                     "source, configuration, tests, logs, and git state and reports evidence-backed "
                     "findings. Deliberately has no ability to change anything it reviews: its only "
                     "non-read tools record findings (memories, projects/tasks, improvement proposals "
                     "a human must approve).",
        tool_names=[
            # Listed literally rather than imported from
            # adapters/selfinspect: roles.py names the Engineering role's
            # workspace tools the same way, and core must not import from
            # adapters. tests/core/test_agent_roles.py asserts this list
            # stays in sync with what the adapter actually registers.
            "self_list_files", "self_read_file", "self_search_repository",
            "self_inspect_dependencies", "self_inspect_configuration", "self_list_tests",
            "self_read_logs", "self_git",
            "create_memory", "create_project", "create_task", "create_improvement",
        ],
        permission_scopes=[
            PermissionScope.SELF_INSPECT, PermissionScope.DATABASE_READ, PermissionScope.DATABASE_WRITE,
        ],
        model_provider_order=["claude", "gemini", "mistral", "groq"],
    ),
    "security": AgentRole(
        name="Security Specialist",
        description="Permission review, change validation, risk monitoring. The only "
                     "role with authority over permissions, extension/tool risk, and the "
                     "Guardian tools (flagging and suspending a principal whose activity "
                     "looks like it's drifting outside its declared scope).",
        tool_names=["list_projects", "list_recent_memories", "flag_denied_action_spikes", "suspend_principal"],
        permission_scopes=[
            PermissionScope.DATABASE_READ, PermissionScope.PERMISSIONS_MANAGE,
            PermissionScope.EXTENSION_INSTALL,
        ],
        model_provider_order=["claude", "gemini", "mistral", "groq"],
    ),
}


def assert_tool_names_are_registered(tools) -> None:
    """Fails loudly at startup if any DEFAULT_ROLES entry names a tool
    that was never registered. Without this, the failure mode is a role
    naming a nonexistent tool and the mismatch surfacing only if a model
    ever actually calls it -- silent for every conversation until then,
    same as create_reflection was for the Memory Specialist. `tools` is
    anything with a `.get(name)` returning None for an unknown tool
    (core.tools.registry.ToolRegistry satisfies this); imported lazily by
    the caller so this module keeps importing without a registry.
    """
    problems = []
    for key, role in DEFAULT_ROLES.items():
        missing = [name for name in role.tool_names if tools.get(name) is None]
        if missing:
            problems.append(f"{key} ({role.name}): {', '.join(missing)}")
    if problems:
        raise RuntimeError(
            "role(s) declare tools that were never registered: " + "; ".join(problems)
        )
