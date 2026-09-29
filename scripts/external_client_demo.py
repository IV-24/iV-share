#!/usr/bin/env python3
"""Drives iV as an agent runtime over HTTP, importing NOTHING from the repo.

This is the Phase 2 test: could another application use iV without knowing
anything about its agents, models, or tools?

    pip install requests
    IV_API_SECRET=... python scripts/external_client_demo.py
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("IV_API_URL", "http://127.0.0.1:8024")
SECRET = os.environ.get("IV_API_SECRET") or sys.exit("set IV_API_SECRET")


def _call(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"X-API-Secret": SECRET}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def submit_goal(goal, conversation_id=None):
    payload = {"message": goal}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    return _call("POST", "/api/chat", payload)


def fetch_run(run_id):
    return _call("GET", f"/api/runs/{run_id}")


def fetch_transcript(conversation_id):
    return _call("GET", f"/api/conversations/{conversation_id}")


if __name__ == "__main__":
    print(f"iV runtime at {BASE}\n")

    status, health = _call("GET", "/health")
    print(f"1. health           -> HTTP {status}, status={health.get('status')}")

    goal = "Create a project called 'External Caller Demo' and add one task to it."
    print(f"\n2. submitting goal  -> {goal!r}")
    t0 = time.time()
    status, result = submit_goal(goal)
    elapsed = time.time() - t0

    print(f"   HTTP {status} in {elapsed:.2f}s")
    if status != 200:
        sys.exit(f"   failed: {result}")

    run_id = result["run_id"]
    conversation_id = result["conversation_id"]
    print(f"\n3. RESULT")
    print(f"   run id                : {run_id}")
    print(f"   conversation id       : {conversation_id}")
    print(f"   model that served it  : {result.get('model_used')}")
    print(f"   response              : {result['response'][:200]}")

    print(f"\n4. retrieving the full execution trace by run id")
    status, run = _call("GET", f"/api/runs/{run_id}")
    print(f"   HTTP {status}  status={run['status']}  ended={run['ended_at']}")
    for step in run["steps"]:
        print(f"     {step['at'][11:23]}  {step['actor']:<14} {step['action']:<34} {step['outcome']}")

    print(f"\n5. retrieving the conversation by its own handle")
    status, transcript = fetch_transcript(conversation_id)
    print(f"   HTTP {status}, {len(transcript.get('messages', []))} messages, title={transcript.get('title')!r}")

    print(f"\n6. reading the resulting state (no agent/tool knowledge needed)")
    status, projects = _call("GET", "/api/projects")
    print(f"   GET /api/projects -> HTTP {status}, {len(projects)} project(s)")
    for p in projects[-3:]:
        print(f"     - {p['name']} (id={p['id'][:8]}, status={p['status']})")

    print(f"\nRUN ID: {run_id}")
