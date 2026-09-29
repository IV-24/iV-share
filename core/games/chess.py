"""Persisted chess-game state. This module knows nothing about chess
rules -- it stores whatever FEN/move-history/status strings it's handed
and hands them back. Move legality, whose turn it is, and game-over
detection all live in adapters/games/chess_engine.py (the only module
allowed to import the third-party `chess` library), the same split
core/models keeps from each provider SDK.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.storage.base import StorageBackend

COLLECTION = "chess_games"


@dataclass
class ChessGame:
    id: str
    human_color: str  # "white" or "black" -- which side the owner plays
    fen: str
    moves: list[str] = field(default_factory=list)  # SAN history, oldest first
    status: str = "active"  # "active", "checkmate", "stalemate", "insufficient_material", ...
    result: str | None = None  # "1-0", "0-1", "1/2-1/2", or None while active
    conversation_id: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "ChessGame":
        return cls(
            id=record["id"],
            human_color=record.get("human_color", "white"),
            fen=record["fen"],
            moves=list(record.get("moves") or []),
            status=record.get("status", "active"),
            result=record.get("result"),
            conversation_id=record.get("conversation_id"),
            created_at=record["created_at"],
            updated_at=record.get("updated_at", record["created_at"]),
        )


class ChessGameStore:
    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    def create(self, *, human_color: str, fen: str, conversation_id: str | None = None) -> ChessGame:
        stored = self._storage.insert(
            COLLECTION,
            {
                "human_color": human_color,
                "fen": fen,
                "moves": [],
                "status": "active",
                "result": None,
                "conversation_id": conversation_id,
            },
        )
        stored.setdefault("updated_at", stored["created_at"])
        return ChessGame.from_record(stored)

    def get(self, game_id: str) -> ChessGame | None:
        record = self._storage.get(COLLECTION, game_id)
        return ChessGame.from_record(record) if record else None

    def list(self, *, status: str | None = None, limit: int = 20) -> list[ChessGame]:
        filters = {"status": status} if status else None
        records = self._storage.query(
            COLLECTION, filters=filters, order_by="created_at", descending=True, limit=limit
        )
        return [ChessGame.from_record(r) for r in records]

    def record_move(
        self, game_id: str, *, fen: str, san: str, status: str, result: str | None
    ) -> ChessGame | None:
        """Appends one move to the game's history and updates its
        position/outcome. The caller (adapters/games/chess_tools.py) has
        already validated the move against the rules engine -- this is
        just the write, same division of labor as ProjectStore.update_status
        trusting its caller to have decided the new status is valid."""
        existing = self.get(game_id)
        if existing is None:
            return None
        stored = self._storage.update(
            COLLECTION,
            game_id,
            {
                "fen": fen,
                "moves": existing.moves + [san],
                "status": status,
                "result": result,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return ChessGame.from_record(stored) if stored else None
