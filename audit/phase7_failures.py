"""Phase 7: failure injection. Nine failure modes, each executed.

For each: surfaced or swallowed? recoverable? state corrupted? recorded in
telemetry? do retries cause duplicate side effects?
"""
import json, os, sqlite3, time, urllib.request, urllib.error

API="http://127.0.0.1:8024"; DB=os.environ["IV_AUDIT_DB"]; SECRET=os.environ["API_ACCESS_SECRET"]

def arm(**kw):
    q="&".join(f"{k}={urllib.request.quote(str(v))}" for k,v in kw.items())
    return json.loads(urllib.request.urlopen(f"{API}/__sim_fault__?{q}", timeout=10).read())

def turn(n): urllib.request.urlopen(f"{API}/__sim_turn__?id={n}&scenario={n}", timeout=10).read()

def chat(m, timeout=180):
    r=urllib.request.Request(API+"/api/chat", data=json.dumps({"message":m}).encode(),
        headers={"Content-Type":"application/json","X-API-Secret":SECRET}, method="POST")
    t0=time.time()
    try:
        with urllib.request.urlopen(r,timeout=timeout) as resp:
            return resp.status, json.loads(resp.read()), time.time()-t0
    except urllib.error.HTTPError as e:
        try: body=json.loads(e.read() or b"{}")
        except Exception: body={}
        return e.code, body, time.time()-t0

def snapshot():
    c=sqlite3.connect(DB)
    out={}
    for (col,n) in c.execute("SELECT collection, COUNT(*) FROM records GROUP BY collection"):
        out[col]=n
    audits=[json.loads(d) for (d,) in c.execute("SELECT data FROM records WHERE collection='audit_log'")]
    c.close()
    out["_chat_turns"]=len([a for a in audits if a["action"]=="chat.turn"])
    out["_denied"]=len([a for a in audits if a["outcome"]=="denied"])
    out["_error"]=len([a for a in audits if a["outcome"]=="error"])
    return out

def delta(a,b): return {k:b.get(k,0)-a.get(k,0) for k in set(a)|set(b) if b.get(k,0)-a.get(k,0)}

def case(n, title, fn):
    print("\n"+"="*78); print(f"F{n}: {title}"); print("="*78)
    before=snapshot()
    fn()
    after=snapshot()
    d=delta(before,after)
    print(f"  persistence delta: {d or 'nothing written'}")
    return d

def main():
    arm(clear=True)

    # F1 ------------------------------------------------------------------
    def f1():
        arm(provider="gemini", kind="unavailable", times=1)
        turn("f1"); s,b,t=chat("What is the capital of France?")
        print(f"  gemini armed unavailable (coordinator order = gemini,mistral,groq)")
        print(f"  HTTP {s} in {t:.2f}s")
        print(f"  model_used: {b.get('model_used')}   <- which provider actually served it")
        print(f"  reply: {(b.get('response') or '')[:120]}")
    case(1,"Provider unavailable (429/quota) — does fallback routing work?",f1)

    # F2 ------------------------------------------------------------------
    def f2():
        arm(provider="gemini", kind="unavailable", times=1)
        arm(provider="mistral", kind="unavailable", times=1)
        arm(provider="groq", kind="unavailable", times=1)
        turn("f2"); s,b,t=chat("What is the capital of France?")
        print(f"  ALL THREE of the coordinator's providers armed unavailable")
        print(f"  HTTP {s} in {t:.2f}s")
        print(f"  reply: {(b.get('response') or '')[:150]}")
    case(2,"Every provider down — graceful message or 500?",f2)

    # F3 ------------------------------------------------------------------
    def f3():
        arm(provider="gemini", kind="malformed_args", times=1)
        turn("f3"); s,b,t=chat("What is the capital of France?")
        print(f"  gemini armed to raise JSONDecodeError (mirrors openai_compatible.py:77,")
        print(f"  which parses tool-call JSON OUTSIDE its try/except)")
        print(f"  HTTP {s} in {t:.2f}s")
        print(f"  body: {str(b)[:150]}")
        print(f"  -> did it fail over to mistral/groq? (model_used={b.get('model_used')})")
    case(3,"Malformed model response — surfaced, swallowed, or fatal?",f3)

    # F4 ------------------------------------------------------------------
    def f4():
        arm(provider="gemini", kind="bad_tool_args", times=1)
        turn("f4"); s,b,t=chat("anything")
        print(f"  model emits create_task(project_id=12345, title=None, nonexistent_kwarg=...)")
        print(f"  HTTP {s} in {t:.2f}s")
        print(f"  reply: {(b.get('response') or '')[:200]}")
    case(4,"Bad tool arguments from the model",f4)

    # F5 ------------------------------------------------------------------
    def f5():
        arm(provider="gemini", kind="unknown_tool", times=1)
        turn("f5"); s,b,t=chat("anything")
        print(f"  model calls a tool that was never registered")
        print(f"  HTTP {s} in {t:.2f}s")
        print(f"  reply: {(b.get('response') or '')[:200]}")
    case(5,"Model hallucinates a tool that does not exist",f5)

    # F6 ------------------------------------------------------------------
    def f6():
        arm(provider="gemini", kind="empty", times=1)
        turn("f6"); s,b,t=chat("What is the capital of France?")
        print(f"  provider returns empty text, no tool calls")
        print(f"  HTTP {s}  reply={repr((b.get('response') or ''))[:120]}")
    case(6,"Empty model response",f6)

    # F7 ------------------------------------------------------------------
    def f7():
        msg="Create a project for this audit and then break it into tasks."
        turn("f7a"); s1,b1,_=chat(msg)
        turn("f7b"); s2,b2,_=chat(msg)
        print(f"  submitted the SAME goal twice (no idempotency key exists)")
        print(f"  conv 1: {b1.get('conversation_id')}")
        print(f"  conv 2: {b2.get('conversation_id')}")
        print(f"  same conversation? {b1.get('conversation_id')==b2.get('conversation_id')}")
        c=sqlite3.connect(DB)
        n=len([1 for (d,) in c.execute("SELECT data FROM records WHERE collection='projects'")
               if json.loads(d)["name"]=="Audit Trial Project"])
        c.close()
        print(f"  projects named 'Audit Trial Project' now in the DB: {n}")
        print(f"  -> duplicate submission created duplicate side effects: {n>1}")
    case(7,"Duplicate request — idempotency",f7)

    # F8 ------------------------------------------------------------------
    def f8():
        turn("f8")
        s,b,t=chat("x"*40000)
        print(f"  40,000-character message")
        print(f"  HTTP {s} in {t:.2f}s   reply={(b.get('response') or str(b))[:100]}")
    case(8,"Oversized input",f8)

    # F9 ------------------------------------------------------------------
    def f9():
        turn("f9")
        for payload,label in [
            ('{"message": 12345}',"message is a number"),
            ('{"msg":"wrong field"}',"missing required field"),
            ('{"message":"ok","conversation_id":"not-a-real-id"}',"nonexistent conversation_id"),
            ('not json at all',"malformed JSON body"),
        ]:
            r=urllib.request.Request(API+"/api/chat", data=payload.encode(),
                headers={"Content-Type":"application/json","X-API-Secret":SECRET}, method="POST")
            try:
                with urllib.request.urlopen(r,timeout=60) as resp:
                    code, body = resp.status, resp.read()[:120]
            except urllib.error.HTTPError as e:
                code, body = e.code, e.read()[:120]
            print(f"  {label:<28} -> HTTP {code}  {body.decode(errors='replace')[:90]}")
    case(9,"Malformed external requests",f9)

    arm(clear=True)

if __name__=="__main__":
    main()
