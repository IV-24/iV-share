"use client";

import { useState, useEffect, useCallback } from "react";
import { fetchWithTimeout } from "@/utils/fetchTimeout";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const api = (path) => (API_BASE ? `${API_BASE}${path}` : `/api/iv${path}`);

function gameLabel(game) {
  if (game.status === "active") return `vs iV — you play ${game.human_color}`;
  const winner = game.result === "1-0" ? "White" : game.result === "0-1" ? "Black" : null;
  return winner ? `Finished — ${winner} won` : "Finished — draw";
}

// Sits alongside RecentConversations in the sidebar: a list of chess
// games plus two "New game" buttons. Creating a game hits the API
// directly (POST /api/chess/games) rather than asking iV to start one
// via chat -- deterministic and instant, same reasoning
// interfaces/api/main.py's create_chess_game route gives for existing as
// a plain endpoint alongside the start_chess_game tool a model can also
// call from ordinary chat.
export default function ChessGamesList({ activeGameId, onSelectGame, refreshToken }) {
  const [games, setGames] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [creating, setCreating] = useState(false);

  const fetchGames = useCallback(async () => {
    try {
      const res = await fetchWithTimeout(api("/chess/games"), { cache: "no-store" }, 15000);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setGames(await res.json());
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchGames();
  }, [fetchGames, refreshToken]);

  async function startGame(humanColor) {
    setCreating(true);
    setError(null);
    try {
      const res = await fetchWithTimeout(
        api("/chess/games"),
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ human_color: humanColor }) },
        45000, // may run iV's opening move (as black) in the same request
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      await fetchGames();
      onSelectGame?.(data.game);
    } catch (err) {
      setError(err.message);
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="chess-games-section">
      <h3>Chess</h3>
      <div className="chess-new-game-actions">
        <button type="button" className="chess-new-game-btn" onClick={() => startGame("white")} disabled={creating}>
          New game — play white
        </button>
        <button type="button" className="chess-new-game-btn" onClick={() => startGame("black")} disabled={creating}>
          New game — play black
        </button>
      </div>
      {loading ? (
        <p className="sidebar-muted">Loading games…</p>
      ) : error ? (
        <p className="sidebar-error">Couldn&apos;t load chess games: {error}</p>
      ) : games.length === 0 ? (
        <p className="sidebar-muted">No games yet.</p>
      ) : (
        <ul className="chess-game-list">
          {games.map((game) => (
            <li key={game.id}>
              <button
                type="button"
                className={`chess-game-item${game.id === activeGameId ? " selected" : ""}`}
                onClick={() => onSelectGame?.(game)}
              >
                {gameLabel(game)}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
