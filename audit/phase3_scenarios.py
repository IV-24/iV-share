"""Phase 3 driver: the five orchestration scenarios, over real HTTP.

Each scenario POSTs to the running /api/chat, then reconstructs what
happened from (a) iV's own audit_log and (b) the simulator's trace. Both are
printed so the two can be compared — the gap between them IS the Phase 5
finding.
"""
import json, os, sqlite3, sys, time, urllib.request, uuid

API = "http://127.0.0.1:8024"
DB = os.environ.get("IV_AUDIT_DB", "audit/phase3.db")
SECRET = os.environ["API_ACCESS_SECRET"]
TRACE = "audit/sim_trace.jsonl"


def post(path, payload, secret=SECRET, headers=None):
    body = json.dumps(payload).encode()
    h = {"Content-Type": "application/json"}
    if secret: h["X-API-Secret"] = secret
    h.update(headers or {})
    req = urllib.request.Request(API + path, data=body, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


def audit_rows(since_iso=None):
    c = sqlite3.connect(DB)
    rows = []
    for (data,) in c.execute("SELECT data FROM records WHERE collection='audit_log'"):
        d = json.loads(data)
        if since_iso is None or d.get("timestamp", "") >= since_iso:
            rows.append(d)
    c.close()
    return sorted(rows, key=lambda r: r.get("timestamp", ""))


def trace_for(turn_id):
    if not os.path.exists(TRACE): return []
    out = []
    for line in open(TRACE):
        try: e = json.loads(line)
        except Exception: continue
        if e.get("sim_turn_id") == turn_id: out.append(e)
    return out


def set_turn(turn_id, scenario):
    """The simulator needs a correlation id iV does not provide; pass it
    through a header the server-side sim reads."""
    post("/internal/sim-turn", {"id": turn_id, "scenario": scenario}, secret=None)


SCENARIOS = [
    ("A-direct",     "What is the capital of France?"),
    ("B-delegated",  "Please review iV's own source for anything unsafe in the agent code."),
    ("C-multistep",  "Create a project for this audit and then break it into tasks."),
    ("D-failure",    "Push the current branch to the remote on GitHub."),
    ("E-ambiguous",  "make it better"),
]


def run():
    results = []
    for name, message in SCENARIOS:
        turn_id = f"{name}-{uuid.uuid4().hex[:6]}"
        # tell the in-process simulator which turn this is
        urllib.request.urlopen(urllib.request.Request(
            API + "/__sim_turn__?id=" + turn_id + "&scenario=" + name, method="GET"), timeout=10)
        before = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        t0 = time.time()
        status, body = post("/api/chat", {"message": message})
        elapsed = time.time() - t0

        print("=" * 78)
        print(f"SCENARIO {name}")
        print(f"  goal        : {message}")
        print(f"  HTTP        : {status}   ({elapsed:.2f}s)")
        print(f"  model_used  : {body.get('model_used')}")
        print(f"  conversation: {body.get('conversation_id')}")
        print(f"  response    : {(body.get('response') or '')[:400]}")
        print()
        print("  --- iV's own audit_log for this turn ---")
        rows = [r for r in audit_rows(before) if r.get("actor") != "api-bootstrap"]
        if not rows:
            print("    (nothing)")
        for r in rows:
            print(f"    {r['timestamp'][11:19]}  actor={r['actor']:<24} action={r['action']:<28} "
                  f"outcome={r['outcome']:<8} resource={str(r['resource'])[:40]}")
        print()
        print("  --- simulator trace: what the model layer actually saw ---")
        for e in trace_for(turn_id):
            tc = e.get("returned_tool_calls") or []
            print(f"    #{e['seq']} provider={e['provider']:<10} serving_role={e['role_serving']:<12} "
                  f"tools_offered={len(e['tools_offered']):<3} tool_results_seen={e['tool_results_seen']}")
            if tc:
                for c in tc:
                    print(f"        -> tool_call {c['name']}({json.dumps(c['arguments'])[:110]})")
            if e.get("returned_text"):
                print(f"        -> text: {e['returned_text'][:150]}")
            if e.get("fault_injected"):
                print(f"        !! fault injected: {e['fault_injected']}")
        print()
        results.append((name, status, body, rows))
        time.sleep(2.5)  # stay under the 30/min chat rate limit
    return results


if __name__ == "__main__":
    run()
