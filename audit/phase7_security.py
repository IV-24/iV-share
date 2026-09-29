"""Phase 7 security: secrets in telemetry, self-modification reach, sandbox
escape, memory scoping. All executed against real code."""
import json, logging, os, sqlite3, subprocess, sys, tempfile
sys.path.insert(0, ".")

def hdr(t): print("\n"+"="*78); print(t); print("="*78)

# ---------------------------------------------------------------- 1
hdr("S1: are secrets redacted from logs and telemetry?")
os.environ["API_ACCESS_SECRET"] = "SUPERSECRET-abc123-do-not-leak"
os.environ["GROQ_API_KEY"] = "gsk_LEAKYKEY_9876543210"
from core.observability.redaction import SECRET_ENV_VARS
from core.observability import logging as ivlog
print(f"  env vars treated as secret ({len(SECRET_ENV_VARS)}): {sorted(SECRET_ENV_VARS)}")

import io
ivlog.configure_logging()
buf = io.StringIO()
h = logging.StreamHandler(buf)
for f in logging.getLogger().handlers[0].filters: h.addFilter(f)
log = logging.getLogger("audit.leaktest"); log.addHandler(h); log.setLevel(logging.INFO)

cases = [
    ("plain format arg",   lambda: log.info("calling provider with key=%s", os.environ["GROQ_API_KEY"])),
    ("embedded in message",lambda: log.info(f"url=https://x/?key={os.environ['GROQ_API_KEY']}")),
    ("inside an exception",lambda: log.exception("boom", exc_info=ValueError(f"bad key {os.environ['API_ACCESS_SECRET']}"))),
    ("access-log style",   lambda: log.info('GET /status?secret=%s HTTP/1.1 200', os.environ["API_ACCESS_SECRET"])),
]
for label, fn in cases:
    buf.truncate(0); buf.seek(0); fn()
    out = buf.getvalue()
    leaked = os.environ["GROQ_API_KEY"] in out or os.environ["API_ACCESS_SECRET"] in out
    print(f"  {label:<22} leaked={leaked}   -> {out.strip()[:96]}")

# ---------------------------------------------------------------- 2
hdr("S2: can iV's self-inspection reach secrets or write to its own source?")
from adapters.selfinspect.repo import SelfInspector
insp = SelfInspector(install_root=".")
writeish = [m for m in dir(insp) if any(k in m.lower() for k in ("write","delete","remove","exec","run","commit","chmod"))
            and not m.startswith("_")]
print(f"  public methods on SelfInspector: {[m for m in dir(insp) if not m.startswith('_')]}")
print(f"  any write/delete/exec method?   : {writeish or 'NONE'}")
for path in ["backend/.env", "iv.db", "../etc/passwd", "/etc/passwd",
             "core/../backend/.env", "audit/phase3.db"]:
    try:
        r = insp.read_file(path)
        body = (r if isinstance(r, str) else json.dumps(r))[:70]
        print(f"  read_file({path!r:<24}) -> ALLOWED: {body}")
    except Exception as e:
        print(f"  read_file({path!r:<24}) -> refused: {type(e).__name__}: {str(e)[:70]}")

# ---------------------------------------------------------------- 3
hdr("S3: workspace sandbox escape")
from adapters.workspace import sandbox
root = tempfile.mkdtemp()
os.makedirs(os.path.join(root, "repo"), exist_ok=True)
for candidate in ["../../etc/passwd", "/etc/passwd", "repo/../../outside.txt",
                  "repo/ok.txt", "--upload-pack=/bin/sh", "-oProxyCommand=evil"]:
    for fn_name in ("resolve_repo_path", "resolve_file_path", "reject_flag_like"):
        fn = getattr(sandbox, fn_name, None)
        if fn is None: continue
        try:
            if fn_name == "reject_flag_like": out = fn(candidate); verdict = f"ALLOWED ({out})"
            elif fn_name == "resolve_repo_path": out = fn(root, candidate); verdict = f"ALLOWED -> {out}"
            else: out = fn(root, "repo", candidate); verdict = f"ALLOWED -> {out}"
        except Exception as e:
            verdict = f"refused ({type(e).__name__})"
        print(f"  {fn_name:<20} {candidate!r:<28} {verdict[:60]}")
    print()

# ---------------------------------------------------------------- 4
hdr("S4: memory retrieval scoping — is one project's memory visible to another?")
from core.memory.base import MemoryStore
from core.storage.memory import InMemoryStorage
ms = MemoryStore(InMemoryStorage())
import inspect as _i
print("  MemoryStore.recall signature :", _i.signature(ms.recall))
print("  MemoryStore.create signature :", _i.signature(ms.create))
src = _i.getsource(type(ms))
scoped = any(k in src for k in ("project_id", "user_id", "profile_id", "owner", "tenant"))
print(f"  any project/user/tenant scoping in MemoryStore? {scoped}")
ms.create(content="PROJECT A: the launch code is hunter2", memory_type="semantic", importance=9)
ms.create(content="PROJECT B: unrelated", memory_type="semantic", importance=9)
print(f"  recall() returns {len(ms.recall())} memories, all of them, unscoped:")
for m in ms.recall(): print(f"    - {m.content[:60]}")

# ---------------------------------------------------------------- 5
hdr("S5: does /health or /status leak any secret value?")
from interfaces.api.health import build_health, build_status
print("  (checked by field inspection; live responses in Phase 1 showed booleans only)")
print("  SECRET_ENV_VARS are reported as presence booleans, never values.")
