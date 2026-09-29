"""Runs iV's REAL API with simulated model providers.

Uses interfaces.api.main.create_app's existing `runtime_factory` hook — the
same seam the project's own tests use — so every layer except the provider
HTTP call is production code. Nothing in core/, adapters/, or interfaces/ is
modified or monkeypatched.

    IV_AUDIT_DB=audit/phase3.db python -m uvicorn audit.sim_server:app --port 8024
"""

import os

from interfaces.api.main import create_app
from interfaces.api.runtime import build_runtime
from core.configuration.settings import Settings, StorageConfig, WorkspaceConfig

from audit.sim_provider import register_simulated_providers


def factory():
    settings = Settings(
        storage=StorageConfig(local_db_path=os.environ.get("IV_AUDIT_DB", "audit/phase3.db")),
        workspace=WorkspaceConfig(workspace_root=os.environ.get("IV_AUDIT_WORKSPACE", "audit/workspace")),
    )
    runtime = build_runtime(settings)
    names = register_simulated_providers(runtime.models)
    print(f"[sim] simulated providers registered: {', '.join(names)}", flush=True)
    print(f"[sim] db={settings.storage.local_db_path} tools={len(runtime.tools.list_tools())}", flush=True)
    return runtime


app = create_app(factory)


# The audit harness needs a correlation id so trace records can be grouped
# into turns. iV provides none -- there is no run_id anywhere in the system
# -- so the harness carries its own out-of-band. This endpoint exists only
# to set it, and only in the simulated server; it is not part of iV.
@app.get("/__sim_turn__")
def _sim_turn(id: str = "unset", scenario: str = "unset"):
    from audit.sim_provider import CURRENT_TURN
    CURRENT_TURN["id"] = id
    CURRENT_TURN["scenario"] = scenario
    return {"sim_turn_id": id, "scenario": scenario}



@app.get("/__sim_fault__")
def _sim_fault(provider: str = "", kind: str = "", times: int = 1, detail: str = "", clear: bool = False):
    """Arms a fault on the next N calls to a provider. Audit harness only."""
    from audit.sim_provider import set_fault, clear_faults, FAULTS
    if clear:
        clear_faults()
        return {"cleared": True}
    set_fault(provider, kind, times, detail)
    return {"provider": provider, "kind": kind, "times": times,
            "armed": {k: v.kind for k, v in FAULTS.items()}}
