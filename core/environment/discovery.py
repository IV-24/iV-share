"""Environment discovery: stdlib introspection only, no subprocess calls,
no writes. Reports what's present (OS, Python, whether this looks like a
git repo, which known env var *names* are set) without ever reading a
secret value into the manifest — the manifest is meant to be safe to show
a human for review.
"""

import os
import platform
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EnvironmentManifest:
    os_name: str
    os_version: str
    python_version: str
    hostname: str
    working_directory: str
    is_git_repository: bool
    available_runtimes: dict[str, bool] = field(default_factory=dict)
    configured_env_var_names: list[str] = field(default_factory=list)


# Names discovery reports as "configured" (present) or not — never their
# values. Extend as new integrations are added; this list itself contains
# no secrets.
KNOWN_ENV_VARS = [
    "GEMINI_API_KEY", "MISTRAL_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY", "SUPABASE_URL", "SUPABASE_KEY", "API_ACCESS_SECRET",
]

RUNTIME_EXECUTABLES = ["python3", "node", "npm", "git", "docker"]


def discover(working_directory: str | None = None) -> EnvironmentManifest:
    cwd = Path(working_directory or os.getcwd())
    return EnvironmentManifest(
        os_name=platform.system(),
        os_version=platform.release(),
        python_version=sys.version.split()[0],
        hostname=platform.node(),
        working_directory=str(cwd),
        is_git_repository=(cwd / ".git").exists(),
        available_runtimes={exe: shutil.which(exe) is not None for exe in RUNTIME_EXECUTABLES},
        configured_env_var_names=[name for name in KNOWN_ENV_VARS if os.getenv(name)],
    )
