"use client";

// One row in the recent-conversations list (see RecentConversations.jsx).
// conversation is a ConversationSummary from GET /api/iv/conversations:
// {id, title, created_at} -- there is no separate "last updated" concept
// in this data model, so this shows when the conversation started, not
// when it was last touched.
export default function ConversationItem({ conversation, isSelected, onClick }) {
  return (
    <li>
      <button
        type="button"
        className={`conversation-item${isSelected ? " selected" : ""}`}
        onClick={onClick}
      >
        <span className="conversation-item-title">{conversation.title}</span>
        {/* suppressHydrationWarning is deliberate and narrow. The date is
            formatted in the viewer's own locale, which is the right
            behaviour for a personal tool, but the server formats it in the
            server process's locale -- so the two legitimately differ and
            React would report every row as a hydration mismatch. The
            alternatives are worse: hardcoding a locale is wrong for a
            non-English owner, and deferring the render to an effect makes
            the list jump. This suppresses exactly one text node. */}
        <span className="conversation-item-date" suppressHydrationWarning>
          {new Date(conversation.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
        </span>
      </button>
    </li>
  );
}
