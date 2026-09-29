"""The only module allowed to `import chess` (the third-party rules
engine) -- same boundary adapters/models keeps around each provider SDK,
so core/games and the tool layer never touch a third-party shape
directly. Every move iV or the owner records goes through apply_move()
first: nothing downstream re-checks legality, so a move that reaches
core.games.chess.ChessGameStore is guaranteed to have actually been legal
in the position it was played in. That guarantee is the whole point of
using a real engine instead of trusting a model's belief about the board.
"""

import chess


class IllegalMoveError(ValueError):
    def __init__(self, move: str, legal_moves: list[str]) -> None:
        self.move = move
        self.legal_moves = legal_moves
        super().__init__(f"'{move}' is not a legal move in this position.")


def new_game_fen() -> str:
    return chess.Board().fen()


def side_to_move(fen: str) -> str:
    return "white" if chess.Board(fen).turn else "black"


def legal_moves_san(fen: str) -> list[str]:
    board = chess.Board(fen)
    return [board.san(move) for move in board.legal_moves]


def board_ascii(fen: str) -> str:
    return str(chess.Board(fen))


def _outcome_status(board: "chess.Board") -> tuple[str, str | None]:
    """(status, result) -- status is "active" while the game continues,
    else the lowercased Termination name (e.g. "checkmate", "stalemate",
    "insufficient_material"). claim_draw=False deliberately: python-chess
    treats the 50-move rule and threefold repetition as *claimable*, not
    automatic, and there is no player here to make that claim -- only
    outcomes the rules force (checkmate, stalemate, insufficient
    material, 75-move rule, fivefold repetition) end the game on their own."""
    outcome = board.outcome(claim_draw=False)
    if outcome is None:
        return "active", None
    return outcome.termination.name.lower(), outcome.result()


def apply_move(fen: str, move: str) -> dict:
    """Applies `move` to the position at `fen`. Accepts standard algebraic
    notation ("Nf3", "e4", "O-O") or UCI ("g1f3") -- a model is far more
    likely to produce SAN, a frontend board is far more likely to produce
    UCI (from-square/to-square), so both are accepted at this one entry
    point rather than pushing the choice onto every caller.

    Raises IllegalMoveError (carrying every legal move in the current
    position) if `move` isn't legal here -- the caller is expected to
    show that list back to whoever's trying to move, human or model.
    """
    board = chess.Board(fen)

    parsed = None
    try:
        parsed = board.parse_san(move)
    except ValueError:
        try:
            candidate = chess.Move.from_uci(move)
        except ValueError:
            candidate = None
        if candidate is not None and candidate in board.legal_moves:
            parsed = candidate

    if parsed is None:
        raise IllegalMoveError(move, legal_moves_san(fen))

    san = board.san(parsed)
    board.push(parsed)
    status, result = _outcome_status(board)
    return {
        "fen": board.fen(),
        "san": san,
        "status": status,
        "result": result,
        "in_check": board.is_check(),
    }
