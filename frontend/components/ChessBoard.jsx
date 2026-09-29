"use client";

import { useMemo, useState } from "react";
import { fetchWithTimeout } from "@/utils/fetchTimeout";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const api = (path) => (API_BASE ? `${API_BASE}${path}` : `/api/iv${path}`);

const PIECE_GLYPHS = {
  K: "♔", Q: "♕", R: "♖", B: "♗", N: "♘", P: "♙",
  k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟",
};

// FEN's piece-placement field: 8 ranks (8 down to 1) separated by '/',
// each rank left-to-right (file a to h); digits are runs of empty
// squares. rows[0] is rank 8, rows[7] is rank 1 -- matching the FEN's own
// order, so callers doing the rank/file math don't have to un-reverse it.
function parseFen(fen) {
  return fen
    .split(" ")[0]
    .split("/")
    .map((rankStr) => {
      const row = [];
      for (const ch of rankStr) {
        if (/\d/.test(ch)) {
          for (let i = 0; i < Number(ch); i += 1) row.push(null);
        } else {
          row.push(ch);
        }
      }
      return row;
    });
}

const isWhitePiece = (piece) => !!piece && piece === piece.toUpperCase();
const squareName = (fileNumber, rankNumber) => `${String.fromCharCode(96 + fileNumber)}${rankNumber}`;

function describeOutcome(game) {
  const winner = game.result === "1-0" ? "White" : game.result === "0-1" ? "Black" : null;
  switch (game.status) {
    case "checkmate":
      return `Checkmate — ${winner} wins.`;
    case "stalemate":
      return "Stalemate — it's a draw.";
    case "insufficient_material":
      return "Draw — insufficient material to checkmate.";
    case "seventyfive_moves":
      return "Draw — 75 moves without a capture or pawn move.";
    case "fivefold_repetition":
      return "Draw — the same position occurred five times.";
    default:
      return "Game over.";
  }
}

// A tap-to-move board: select a square with one of your own pieces, then
// tap a destination -- no drag-and-drop, so the same interaction works
// identically with a mouse or a finger (see the mobile/desktop parity
// review this feature followed). Legality is never checked here; every
// move is submitted to the server and the rules engine there
// (adapters/games/chess_engine.py) is the only thing that decides
// whether it was real. An illegal attempt just comes back as an error to
// show, same as any other rejected request in this app.
export default function ChessBoard({ game, onGameUpdate }) {
  const [selectedSquare, setSelectedSquare] = useState(null);
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const rows = useMemo(() => parseFen(game.fen), [game.fen]);
  const isMyTurn = game.status === "active" && game.turn === game.human_color;
  const perspectiveIsBlack = game.human_color === "black";

  const pieceAt = (file, rank) => rows[8 - rank][file - 1];

  async function submitMove(from, to) {
    setSubmitting(true);
    setError(null);
    let move = from + to;
    const movingPiece = pieceAt(from.charCodeAt(0) - 96, Number(from[1]));
    const destRank = to[1];
    // No promotion picker in this first pass -- a pawn reaching the back
    // rank always promotes to a queen, the overwhelmingly common choice.
    // Under-promotion is a real (rare) chess need; left as a known gap
    // rather than adding a piece-choice UI for it up front.
    if (movingPiece && movingPiece.toLowerCase() === "p" && (destRank === "8" || destRank === "1")) {
      move += "q";
    }

    try {
      const res = await fetchWithTimeout(
        api(`/chess/games/${game.id}/move`),
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ move }) },
        45000, // generous: this one request also runs iV's whole reply turn, delegation included
      );
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        const detail = data?.detail;
        const message = typeof detail === "string" ? detail : detail?.error || `HTTP ${res.status}`;
        setError(message);
        setSelectedSquare(null);
        return;
      }
      setSelectedSquare(null);
      onGameUpdate?.(data.game);
    } catch (err) {
      setError(
        err.name === "TimeoutError"
          ? err.message
          : "Lost the connection making that move -- check the network and try again.",
      );
      setSelectedSquare(null);
    } finally {
      setSubmitting(false);
    }
  }

  function handleSquareClick(square, piece) {
    if (!isMyTurn || submitting) return;
    if (selectedSquare === square) {
      setSelectedSquare(null);
      return;
    }
    if (piece && isWhitePiece(piece) === (game.human_color === "white")) {
      setSelectedSquare(square);
      setError(null);
      return;
    }
    if (selectedSquare) {
      submitMove(selectedSquare, square);
    }
  }

  const rankOrder = perspectiveIsBlack ? [1, 2, 3, 4, 5, 6, 7, 8] : [8, 7, 6, 5, 4, 3, 2, 1];
  const fileOrder = perspectiveIsBlack ? [8, 7, 6, 5, 4, 3, 2, 1] : [1, 2, 3, 4, 5, 6, 7, 8];

  const statusLine =
    game.status !== "active"
      ? describeOutcome(game)
      : submitting
        ? "iV is thinking…"
        : isMyTurn
          ? "Your move"
          : "Waiting on iV's move…";

  return (
    <div className="chess-panel">
      <div className="chess-status-line">
        <span className={`chat-status-dot${submitting || (!isMyTurn && game.status === "active") ? " thinking" : ""}`} aria-hidden="true" />
        {statusLine}
      </div>
      <div className="chess-board">
        {rankOrder.map((rankNumber) => (
          <div className="chess-rank" key={rankNumber}>
            {fileOrder.map((fileNumber) => {
              const square = squareName(fileNumber, rankNumber);
              const piece = pieceAt(fileNumber, rankNumber);
              const dark = (fileNumber + rankNumber) % 2 === 0;
              const selected = selectedSquare === square;
              return (
                <button
                  type="button"
                  key={square}
                  className={`chess-square${dark ? " dark" : " light"}${selected ? " selected" : ""}`}
                  onClick={() => handleSquareClick(square, piece)}
                  aria-label={square}
                >
                  {piece && (
                    <span className={`chess-piece ${isWhitePiece(piece) ? "chess-piece-white" : "chess-piece-black"}`}>
                      {PIECE_GLYPHS[piece]}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        ))}
      </div>
      {error && <div className="chess-error">{error}</div>}
    </div>
  );
}
