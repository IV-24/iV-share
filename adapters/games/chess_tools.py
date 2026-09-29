"""Wires adapters/games/chess_engine.py and core.games.chess.ChessGameStore
into the ToolRegistry -- same role adapters/workspace/tools.py plays for
git/file/shell primitives, and the same reason: core/games and the tool
registry never import the third-party `chess` library directly, only
this module does (via chess_engine).

Risk tier, and why: LOW/AUTO for all three. Nothing here is destructive
or externally consequential -- a chess move only ever touches this game's
own row, the same "no deletes, nothing production-affecting yet" reasoning
core/tools/standard.py's module docstring gives for its own tools.
"""

from adapters.games import chess_engine
from core.games.chess import ChessGame, ChessGameStore
from core.permissions.scopes import PermissionScope
from core.tools.base import ExecutionPolicy, RiskLevel, ToolDefinition
from core.tools.registry import ToolRegistry


def _game_to_dict(game: ChessGame) -> dict:
    return {
        "id": game.id,
        "human_color": game.human_color,
        "fen": game.fen,
        "moves": game.moves,
        "status": game.status,
        "result": game.result,
        "conversation_id": game.conversation_id,
    }


def register_chess_tools(registry: ToolRegistry, *, games: ChessGameStore) -> None:
    def start_chess_game(human_color: str = "white"):
        human_color = human_color.strip().lower()
        if human_color not in ("white", "black"):
            return {"error": "human_color must be 'white' or 'black'"}
        game = games.create(human_color=human_color, fen=chess_engine.new_game_fen())
        return _game_to_dict(game)

    def get_chess_board(game_id: str):
        game = games.get(game_id)
        if game is None:
            return {"error": f"no chess game with id '{game_id}'"}
        return {
            **_game_to_dict(game),
            "turn": chess_engine.side_to_move(game.fen),
            "legal_moves": chess_engine.legal_moves_san(game.fen),
            "board": chess_engine.board_ascii(game.fen),
        }

    def make_chess_move(game_id: str, move: str):
        game = games.get(game_id)
        if game is None:
            return {"error": f"no chess game with id '{game_id}'"}
        if game.status != "active":
            return {"error": f"game '{game_id}' already ended ({game.status}, result {game.result})"}

        # Whose move it is comes from the position itself (FEN), never
        # from who's asking -- this is what stops the model from playing
        # a move on the owner's behalf if it's asked to move out of turn.
        turn = chess_engine.side_to_move(game.fen)
        if turn == game.human_color:
            return {"error": "it is the human's turn, not yours -- wait for their move"}

        try:
            outcome = chess_engine.apply_move(game.fen, move)
        except chess_engine.IllegalMoveError as exc:
            return {"error": str(exc), "legal_moves": exc.legal_moves}

        updated = games.record_move(
            game_id,
            fen=outcome["fen"],
            san=outcome["san"],
            status=outcome["status"],
            result=outcome["result"],
        )
        return {**_game_to_dict(updated), "in_check": outcome["in_check"]}

    registry.register(ToolDefinition(
        name="start_chess_game",
        description=(
            "Starts a new chess game against the owner. human_color is which side the owner "
            "plays ('white' or 'black', default 'white') -- you play the other side."
        ),
        input_schema={"type": "object", "properties": {"human_color": {"type": "string"}}},
        output_schema={"type": "object"}, handler=start_chess_game,
        required_permissions=[PermissionScope.DATABASE_WRITE], risk_level=RiskLevel.LOW,
        execution_policy=ExecutionPolicy.AUTO,
    ))
    registry.register(ToolDefinition(
        name="get_chess_board",
        description=(
            "Reads a chess game's current position: whose turn it is, every legal move in "
            "algebraic notation, the move history so far, and an ASCII board. Call this before "
            "deciding a move, and hand its output to any specialist you delegate analysis to -- "
            "they can also call it directly if given the game_id."
        ),
        input_schema={"type": "object", "properties": {"game_id": {"type": "string"}}, "required": ["game_id"]},
        output_schema={"type": "object"}, handler=get_chess_board,
        required_permissions=[PermissionScope.DATABASE_READ], risk_level=RiskLevel.LOW,
        execution_policy=ExecutionPolicy.AUTO,
    ))
    registry.register(ToolDefinition(
        name="make_chess_move",
        description=(
            "Plays your move in an active game. `move` accepts standard algebraic notation "
            "(e.g. 'Nf3', 'e4', 'O-O') or UCI ('g1f3'). Rejected if it isn't your turn, the game "
            "already ended, or the move is illegal in the current position -- the error then lists "
            "every legal move so you can pick a real one and retry."
        ),
        input_schema={"type": "object", "properties": {
            "game_id": {"type": "string"}, "move": {"type": "string"},
        }, "required": ["game_id", "move"]},
        output_schema={"type": "object"}, handler=make_chess_move,
        required_permissions=[PermissionScope.DATABASE_WRITE], risk_level=RiskLevel.LOW,
        execution_policy=ExecutionPolicy.AUTO,
    ))
