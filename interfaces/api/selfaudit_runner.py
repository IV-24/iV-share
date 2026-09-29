"""Adapts core/selfaudit to the API's ApiRuntime.

Its one real job is bootstrapping the auditor role's permissions the same
way interfaces/api/runtime.py bootstraps every other role's — a self-audit
that fails on a missing self.inspect grant would be an infrastructure
detail masquerading as a security finding. The grant is recorded through
PermissionManager (and therefore the audit log) like any other, not
special-cased around it.
"""

from __future__ import annotations

import logging

from core.agent.roles import DEFAULT_ROLES
from core.selfaudit.engine import SelfAuditResult, run_self_audit

logger = logging.getLogger(__name__)

AUDITOR_ROLE_KEY = "auditor"


def run_api_self_audit(runtime, *, use_model: bool = True) -> SelfAuditResult:
    role = DEFAULT_ROLES[AUDITOR_ROLE_KEY]
    for scope in role.permission_scopes:
        if not runtime.permissions.is_granted(role.name, scope):
            runtime.permissions.grant(role.name, scope, granted_by="self-audit-bootstrap")

    logger.info("self-audit starting (model review=%s)", use_model)
    result = run_self_audit(
        reader=runtime.inspector,
        orchestrator=runtime.orchestrator,
        role=role,
        memory=runtime.memory,
        projects=runtime.projects,
        tasks=runtime.tasks,
        improvements=runtime.improvements,
        use_model=use_model,
    )
    logger.info(
        "self-audit finished: counts=%s model_review=%s", result.counts, result.model_review_status
    )
    return result
