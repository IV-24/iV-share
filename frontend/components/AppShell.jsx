"use client";

import { useRef, useState, useCallback, useEffect, useLayoutEffect } from "react";
import ChatBox from "./ChatBox";
import ProjectSidebar from "./ProjectSidebar";

// Wires the project sidebar to ChatBox without either needing to know
// the other exists: ChatBox exposes an imperative handle (askWithMessage,
// switchToConversation) and reports conversation changes upward; this is
// the one place that owns both, so an "Ask iV" click here always ends up
// as a plain message in the same conversation the owner is already
// looking at -- there is no separate channel a sub-feature could use to
// talk to iV.
//
// Sidebar open/closed state is owned here too, not inside ProjectSidebar,
// because on a narrow (phone-width) viewport the sidebar becomes an
// overlay that ChatBox's own header needs a button to open -- two
// components need to read/flip the same piece of state.
const useIsomorphicLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

export default function AppShell() {
  const chatRef = useRef(null);
  const [activeConversationId, setActiveConversationId] = useState(null);
  const [conversationsRefreshToken, setConversationsRefreshToken] = useState(0);
  // Desktop keeps the sidebar open by default (its old behavior). Below
  // the mobile breakpoint (see globals.css's 720px media query) it
  // starts closed, because at phone widths an always-open 280px sidebar
  // leaves no room for the chat composer -- see that media query's
  // comment for the full failure mode this avoids.
  //
  // The initial value is a constant rather than a window measurement, and
  // that matters: reading window.innerWidth in the initialiser made the
  // server render `true` (no window) while a phone's first client render
  // computed `false`, and React refused to hydrate the mismatch. It only
  // ever showed up on a phone, because on a desktop both sides agree.
  // Narrowing is applied below, after mount, where a measurement is
  // legitimate.
  const [sidebarOpen, setSidebarOpen] = useState(true);
  // The chess game currently shown in ChatBox (see ChessBoard.jsx), or
  // null. Owned here rather than in ChatBox or the sidebar because both
  // need it: the sidebar's game list highlights/refreshes it, ChatBox
  // renders the board itself above its own message feed.
  const [activeChessGame, setActiveChessGame] = useState(null);
  const [chessGamesRefreshToken, setChessGamesRefreshToken] = useState(0);

  // Layout effect, not a plain one, so the correction lands before paint:
  // on a phone the sidebar is a fixed overlay with a full-screen backdrop,
  // and letting it show open for a frame is a visible flash. useEffect is
  // substituted during SSR only to avoid React's "useLayoutEffect does
  // nothing on the server" warning -- neither runs there, and no rendered
  // output depends on which one is chosen.
  useIsomorphicLayoutEffect(() => {
    if (window.matchMedia("(max-width: 720px)").matches) {
      setSidebarOpen(false);
    }
  }, []);

  const toggleSidebar = useCallback(() => setSidebarOpen((open) => !open), []);
  const closeSidebar = useCallback(() => setSidebarOpen(false), []);

  const handleConversationChange = useCallback((id) => {
    setActiveConversationId(id);
    setConversationsRefreshToken((token) => token + 1);
  }, []);

  const handleAskIV = useCallback((text) => {
    chatRef.current?.askWithMessage(text);
  }, []);

  const handleSelectConversation = useCallback(
    (id) => {
      chatRef.current?.switchToConversation(id);
      setActiveChessGame(null); // a plain conversation replaces whatever board was showing
      closeSidebar(); // picking a conversation on mobile should also dismiss the overlay
    },
    [closeSidebar],
  );

  const handleSelectChessGame = useCallback(
    (game) => {
      setActiveChessGame(game);
      chatRef.current?.switchToConversation(game.conversation_id);
      closeSidebar();
    },
    [closeSidebar],
  );

  // Bubbled up from ChessBoard (via ChatBox) after every create/move --
  // the single point that keeps the sidebar's game list, the board, and
  // the chat feed showing iV's reply all in sync with the one server
  // response that just came back.
  const handleChessGameUpdate = useCallback((game) => {
    setActiveChessGame(game);
    setChessGamesRefreshToken((token) => token + 1);
    chatRef.current?.switchToConversation(game.conversation_id);
  }, []);

  return (
    <div className="app-shell">
      <ProjectSidebar
        isOpen={sidebarOpen}
        onToggle={toggleSidebar}
        onClose={closeSidebar}
        onAskIV={handleAskIV}
        activeConversationId={activeConversationId}
        onSelectConversation={handleSelectConversation}
        conversationsRefreshToken={conversationsRefreshToken}
        activeChessGameId={activeChessGame?.id ?? null}
        onSelectChessGame={handleSelectChessGame}
        chessGamesRefreshToken={chessGamesRefreshToken}
      />
      <ChatBox
        ref={chatRef}
        onConversationChange={handleConversationChange}
        onOpenSidebar={toggleSidebar}
        activeChessGame={activeChessGame}
        onChessGameUpdate={handleChessGameUpdate}
      />
    </div>
  );
}
