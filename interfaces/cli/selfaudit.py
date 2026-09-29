"""Runs iV's self-audit from the command line:

    python -m interfaces.cli.selfaudit [--no-model] [--output PATH] [--json]

Same engine the HTTP trigger uses (core/selfaudit), same auditor role,
same persistence into memories/projects/tasks — this is a second door
onto one implementation, not a second implementation. It exists because
the first audit of a system is usually run by someone standing at the
machine, before the server is something they trust to be up.

--no-model runs only the deterministic checks: fast, fully reproducible,
and the right mode when no provider API key is configured.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.observability.logging import configure_logging
from core.selfaudit.engine import render_markdown_report
from interfaces.api.runtime import build_runtime
from interfaces.api.selfaudit_runner import run_api_self_audit
from interfaces.cli.main import load_environment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run iV's self-audit.")
    parser.add_argument("--no-model", action="store_true", help="deterministic checks only, no model review")
    parser.add_argument("--output", type=Path, help="write a markdown report to this path")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON on stdout")
    args = parser.parse_args(argv)

    load_environment()
    # stderr, so --json (and the markdown report) own stdout cleanly.
    configure_logging(stream=sys.stderr)

    runtime = build_runtime()
    try:
        result = run_api_self_audit(runtime, use_model=not args.no_model)
    finally:
        runtime.storage.close()

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(render_markdown_report(result), encoding="utf-8")
        print(f"report written to {args.output}", file=sys.stderr)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(render_markdown_report(result))

    # A non-zero exit for CRITICAL findings makes this usable as a gate in
    # CI or a pre-deploy check without anyone having to parse the report.
    return 1 if result.counts.get("CRITICAL", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
