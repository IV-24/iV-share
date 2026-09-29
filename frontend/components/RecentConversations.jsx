"use client";

import { useState, useEffect, useCallback } from "react";
import ConversationItem from "./ConversationItem";
import { fetchWithTimeout } from "@/utils/fetchTimeout";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const api = (path) => (API_BASE ? `${API_BASE}${path}` : `/api/iv${path}`);

// A short list of recent conversations, so switching back to yesterday's
// thread doesn't depend on still having the right tab open --
// ConversationStore.list_recent() (core/conversations/base.py) always
// had this data; there was simply no way to see or reach it from the
// page before. activeConversationId highlights the one currently open in
// ChatBox; onSelect asks ChatBox (via its imperative handle in page.js)
// to switch to the chosen one.
export default function RecentConversations({ activeConversationId, onSelect, refreshToken }) {
  const [conversations, setConversations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchConversations = useCallback(async () => {
    try {
      const res = await fetchWithTimeout(api("/conversations"), { cache: "no-store" }, 15000);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setConversations(await res.json());
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchConversations();
    // refreshToken changes whenever ChatBox reports a new/updated
    // conversation (see page.js), so a fresh chat shows up here without
    // a manual reload.
  }, [fetchConversations, refreshToken]);

  return (
    <div className="recent-conversations">
      <h3>Recent</h3>
      {loading ? (
        <p className="sidebar-muted">Loading…</p>
      ) : error ? (
        <p className="sidebar-error">Couldn&apos;t load conversations: {error}</p>
      ) : conversations.length === 0 ? (
        <p className="sidebar-muted">No conversations yet.</p>
      ) : (
        <ul className="conversation-list">
          {conversations.map((conversation) => (
            <ConversationItem
              key={conversation.id}
              conversation={conversation}
              isSelected={conversation.id === activeConversationId}
              onClick={() => onSelect(conversation.id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
