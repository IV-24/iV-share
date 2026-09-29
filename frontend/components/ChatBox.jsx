"use client";
import { useState, useRef, useEffect, useCallback, forwardRef, useImperativeHandle } from "react";
import ChessBoard from "./ChessBoard";
import { fetchWithTimeout } from "@/utils/fetchTimeout";

// The API is reached through this app's own server-side proxy
// (app/api/iv/[...path]/route.js), so every request is same-origin and
// relative. That is deliberate and load-bearing for remote access: the
// page is served from whatever address the phone used — localhost, the
// LAN IP, or the Tailscale address — and a relative URL is correct for
// all three without the client knowing any of them.
//
// NEXT_PUBLIC_API_URL still works as an override for anyone pointing the
// UI at an API on another host, but it is no longer the normal path and
// it is no longer where the secret lives.
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const api = (path) => (API_BASE ? `${API_BASE}${path}` : `/api/iv${path}`);
const CONVERSATION_STORAGE_KEY = "iv.conversationId";

// Exposes an imperative handle (askWithMessage, switchToConversation) so
// a sibling panel -- the project sidebar's "Ask iV" button, a recent-
// conversations list -- can drive the chat without ChatBox needing to
// know either of those features exists. onConversationChange fires
// whenever the active conversation id changes (a reply arrives, a
// different one is picked), so a parent can keep a "recent" list's
// highlighted entry in sync without polling.
const ChatBox = forwardRef(function ChatBox(
  { onConversationChange, onOpenSidebar, activeChessGame, onChessGameUpdate },
  ref,
) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [conversationId, setConversationId] = useState(null);
  const [health, setHealth] = useState(null);
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  // Runtime status, so the header reports what the backend actually says
  // rather than "online" unconditionally. Polled slowly — this is a
  // status light, not a heartbeat.
  const refreshHealth = useCallback(async () => {
    try {
      const res = await fetchWithTimeout(api("/health"), { cache: "no-store" }, 10000);
      setHealth(res.ok ? await res.json() : { status: "error" });
    } catch {
      setHealth({ status: "unreachable" });
    }
  }, []);

  useEffect(() => {
    refreshHealth();
    const timer = setInterval(refreshHealth, 30000);
    return () => clearInterval(timer);
  }, [refreshHealth]);

  const loadConversation = useCallback(
    async (id) => {
      const res = await fetchWithTimeout(api(`/conversations/${encodeURIComponent(id)}`), { cache: "no-store" }, 15000);
      if (!res.ok) {
        // A conversation that no longer exists (fresh database, cleared
        // file, or one the caller merely guessed at) must not wedge the
        // UI on a stale id.
        window.localStorage.removeItem(CONVERSATION_STORAGE_KEY);
        return false;
      }
      const data = await res.json();
      setConversationId(data.conversation_id);
      setMessages(data.messages.map((m) => ({ role: m.role === "user" ? "user" : "iv", content: m.content })));
      setError(null);
      window.localStorage.setItem(CONVERSATION_STORAGE_KEY, data.conversation_id);
      onConversationChange?.(data.conversation_id);
      return true;
    },
    [onConversationChange],
  );

  // Conversations were always persisted server-side; the UI simply had no
  // way to read one back, so a page reload looked like amnesia. Restore
  // the last conversation on mount.
  useEffect(() => {
    const saved = typeof window !== "undefined" && window.localStorage.getItem(CONVERSATION_STORAGE_KEY);
    if (!saved) return;
    let cancelled = false;

    (async () => {
      try {
        if (cancelled) return;
        await loadConversation(saved);
      } catch {
        // Offline on load is not an error worth showing before the user
        // has done anything — the health indicator already reports it.
      }
    })();

    return () => {
      cancelled = true;
    };
    // Intentionally mount-only: loadConversation is stable in practice
    // (its one dependency, onConversationChange, is expected to be a
    // stable callback from the parent) and re-running this on every
    // render would re-fetch the saved conversation in a loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function startNewConversation() {
    window.localStorage.removeItem(CONVERSATION_STORAGE_KEY);
    setConversationId(null);
    setMessages([]);
    setError(null);
    onConversationChange?.(null);
  }

  const sendMessage = useCallback(
    async (overrideText) => {
      const trimmed = (overrideText ?? input).trim();
      if (!trimmed || loading) return;
      setMessages((prev) => [...prev, { role: "user", content: trimmed }]);
      setInput("");
      setError(null);
      setLoading(true);

      try {
        const res = await fetchWithTimeout(
          api("/chat"),
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: trimmed, conversation_id: conversationId }),
          },
          // Generous: a turn can fan out to several specialist delegations
          // (core/agent/delegation.py caps at 6), each its own model call.
          45000,
        );

        if (!res.ok) {
          let detail = `HTTP ${res.status}`;
          try {
            const body = await res.json();
            if (body?.detail) detail = body.detail;
            // A configuration error carries the paths it checked. Showing
            // them beats making someone read the server log to find out
            // where the app looked.
            if (Array.isArray(body?.looked_in) && body.looked_in.length > 0) {
              detail += `\n\nLooked in:\n${body.looked_in.map((p) => `  ${p}`).join("\n")}`;
            }
          } catch {
            // response wasn't JSON — stick with the plain status code
          }
          throw new Error(detail);
        }

        const data = await res.json();
        setMessages((prev) => [...prev, { role: "iv", content: data.response }]);
        setConversationId(data.conversation_id);
        window.localStorage.setItem(CONVERSATION_STORAGE_KEY, data.conversation_id);
        onConversationChange?.(data.conversation_id);
      } catch (err) {
        if (err.name === "TimeoutError") {
          // Distinct from the network-down case below: the request was
          // sent but never came back in time, which on a phone usually
          // means a slow/cellular path to the tailnet rather than the
          // server being unreachable outright.
          setError(
            "iV didn't respond in time. Your connection may be slow right now -- check the status above and try again.",
          );
        } else if (err instanceof TypeError) {
          // fetch() itself failed — the page's own origin is unreachable,
          // which for a same-origin request means the network dropped
          // (phone left the tailnet, laptop asleep).
          setError(
            "Lost the connection to iV. Check that the laptop is awake and your phone is still on the Tailscale network.",
          );
        } else {
          setError(`iV backend error: ${err.message}`);
        }
        refreshHealth();
      } finally {
        setLoading(false);
      }
    },
    [input, loading, conversationId, onConversationChange, refreshHealth],
  );

  useImperativeHandle(
    ref,
    () => ({
      // Seeds the composer with a prompt and sends it immediately --
      // what the project sidebar's "Ask iV" button drives, so asking
      // about a task looks and behaves exactly like the owner typing
      // the question themselves.
      askWithMessage: (text) => sendMessage(text),
      // Switches the active conversation, e.g. from a recent-
      // conversations list. Errors (a deleted conversation) surface the
      // same way loading one on mount does.
      switchToConversation: (id) => loadConversation(id),
    }),
    [sendMessage, loadConversation],
  );

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }

  const statusLabel = loading
    ? "thinking"
    : health === null
      ? "checking"
      : health.status === "ok"
        ? "online"
        : health.status === "degraded"
          ? "no model"
          : "offline";

  return (
    <div className="chat-shell">
      <div className="chat-header">
        {/* Hidden by default, shown only under the mobile breakpoint
            (globals.css) -- the sidebar becomes an off-canvas overlay
            there, so this is the only way to reopen it once collapsed;
            the sidebar's own toggle button is offscreen at that point. */}
        <button
          type="button"
          className="chat-menu-btn"
          onClick={onOpenSidebar}
          aria-label="Open projects and conversations"
        >
          ☰
        </button>
        <div className="chat-brand">
          <span className="chat-brand-mark" aria-hidden="true" />
          <div>
            <div className="chat-brand-name">iV</div>
            <div className="chat-brand-tag">Local Appliance</div>
          </div>
        </div>
        <div className="chat-status">
          <span className={`chat-status-dot${loading ? " thinking" : ""}`} aria-hidden="true" />
          <span title={health ? JSON.stringify(health.checks || {}, null, 1) : "checking"}>{statusLabel}</span>
          {messages.length > 0 && (
            <button className="chat-suggestion-btn" onClick={startNewConversation} style={{ marginLeft: "0.75rem" }}>
              new
            </button>
          )}
        </div>
      </div>

      {/* Rendered above the message feed rather than replacing it: the
          board is the "what does the position look like" view, the feed
          below is where iV's move commentary (and anything else you ask
          it) shows up -- same conversation, so asking "why did you play
          that" just works. */}
      {activeChessGame && <ChessBoard game={activeChessGame} onGameUpdate={onChessGameUpdate} />}

      <div className="chat-messages">
        {messages.length === 0 && !activeChessGame && (
          <div className="chat-welcome">
            <h2>System ready.</h2>
            <p>Ask a question or pick a prompt:</p>
            <div className="chat-suggestions">
              <button className="chat-suggestion-btn" onClick={() => setInput("What can you do?")}>What can you do?</button>
              <button className="chat-suggestion-btn" onClick={() => setInput("Review system status")}>Review system status</button>
            </div>
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} className={`chat-bubble ${msg.role === "user" ? "chat-bubble-user" : "chat-bubble-iv"}`}>
            <div className="chat-bubble-role">{msg.role === "user" ? "you" : "iv"}</div>
            <div className="chat-bubble-content">{msg.content}</div>
          </div>
        ))}
        {loading && (
          <div className="chat-thinking">
            <span className="chat-status-dot thinking" aria-hidden="true" />
            iV is thinking . . .
          </div>
        )}
        {error && <div className="chat-error" style={{ whiteSpace: "pre-wrap" }}>{error}</div>}
        <div ref={bottomRef} />
      </div>

      <div className="chat-input-area">
        <textarea
          className="chat-textarea"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Talk to iV..."
          rows={1}
          // Some mobile browsers' built-in AI assistants (Gemini's page
          // integration is the one we've seen) inject their own tracking
          // attribute onto form inputs before React hydrates, which
          // otherwise trips a false-positive hydration mismatch here.
          // Scoped to just this element so a real mismatch elsewhere still
          // surfaces normally.
          suppressHydrationWarning
        />
        <button className="chat-send-btn" onClick={() => sendMessage()} disabled={loading || !input.trim()}>Send</button>
      </div>
    </div>
  );
});

export default ChatBox;
