"use client";

import { useState, useEffect, useCallback } from "react";
import ChessGamesList from "./ChessGamesList";
import ProjectList from "./ProjectList";
import ProjectSummary from "./ProjectSummary";
import RecentConversations from "./RecentConversations";
import { fetchWithTimeout } from "@/utils/fetchTimeout";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const api = (path) => (API_BASE ? `${API_BASE}${path}` : `/api/iv${path}`);

// The sidebar: active projects (with a summary/task view for whichever
// one is selected) above a recent-conversations list. Every request goes
// through the same same-origin proxy ChatBox uses (app/api/iv/[...path]/
// route.js) so the API secret never reaches the browser -- see that
// route's own comment for why. onAskIV/activeConversationId/
// onSelectConversation are passed through from page.js, which is what
// actually holds the ChatBox ref this sidebar drives.
//
// isOpen/onToggle/onClose are owned by AppShell, not this component: at
// phone widths (see globals.css's 720px media query) this sidebar
// renders as a fixed overlay rather than a flex column, and ChatBox's
// header needs its own button to reopen it -- so the state can't live
// only here.
export default function ProjectSidebar({
  isOpen,
  onToggle,
  onClose,
  onAskIV,
  activeConversationId,
  onSelectConversation,
  conversationsRefreshToken,
  activeChessGameId,
  onSelectChessGame,
  chessGamesRefreshToken,
}) {
  const [selectedProjectId, setSelectedProjectId] = useState(null);
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchProjects = useCallback(async () => {
    try {
      const res = await fetchWithTimeout(api("/projects?status=active"), { cache: "no-store" }, 15000);
      if (!res.ok) throw new Error("Failed to fetch projects");
      setProjects(await res.json());
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchProjects();
  }, [fetchProjects]);

  return (
    <>
      {/* Only visible under the mobile breakpoint (globals.css); tapping
          it dismisses the sidebar the same way tapping outside a modal
          does, so a thumb doesn't have to find the small toggle again. */}
      {isOpen && <div className="sidebar-backdrop" onClick={onClose} aria-hidden="true" />}
      <div className={`project-sidebar ${isOpen ? "open" : "collapsed"}`}>
        <button
          type="button"
          className="sidebar-toggle"
          onClick={onToggle}
          aria-label={isOpen ? "Collapse sidebar" : "Expand sidebar"}
        >
          {isOpen ? "⟨" : "⟩"}
        </button>

        {isOpen && (
          <div className="sidebar-content">
            <h3>Projects</h3>
            {loading ? (
              <p className="sidebar-muted">Loading projects…</p>
            ) : error ? (
              <p className="sidebar-error">Error: {error}</p>
            ) : projects.length === 0 ? (
              <p className="sidebar-muted">No active projects found.</p>
            ) : (
              <ProjectList
                projects={projects}
                selectedProjectId={selectedProjectId}
                onSelectProject={(id) => setSelectedProjectId(id === selectedProjectId ? null : id)}
              />
            )}

            {selectedProjectId && <ProjectSummary projectId={selectedProjectId} onAskIV={onAskIV} />}

            <RecentConversations
              activeConversationId={activeConversationId}
              onSelect={onSelectConversation}
              refreshToken={conversationsRefreshToken}
            />

            <ChessGamesList
              activeGameId={activeChessGameId}
              onSelectGame={onSelectChessGame}
              refreshToken={chessGamesRefreshToken}
            />
          </div>
        )}
      </div>
    </>
  );
}
