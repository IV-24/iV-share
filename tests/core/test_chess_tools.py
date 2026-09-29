from adapters.games import chess_engine
from adapters.games.chess_tools import register_chess_tools
from core.audit.log import AuditLog
from core.approvals.manager import ApprovalManager
from core.games.chess import ChessGameStore
from core.permissions.manager import PermissionManager
from core.permissions.scopes import PermissionScope
from core.storage.memory import InMemoryStorage
from core.tools.registry import ToolRegistry

PRINCIPAL = "coordinator"


def make_registry():
    storage = InMemoryStorage()
    audit = AuditLog(storage)
    permissions = PermissionManager(storage, audit)
    approvals = ApprovalManager(storage, audit)
    registry = ToolRegistry(permissions, approvals, audit)
    games = ChessGameStore(storage)
    register_chess_tools(registry, games=games)
    permissions.grant(PRINCIPAL, PermissionScope.DATABASE_READ, granted_by="test")
    permissions.grant(PRINCIPAL, PermissionScope.DATABASE_WRITE, granted_by="test")
    return registry, games


def test_all_chess_tools_registered_with_low_risk_and_auto_policy():
    registry, _ = make_registry()
    names = {t["name"] for t in registry.list_tools()}
    assert names == {"start_chess_game", "get_chess_board", "make_chess_move"}
    for tool in registry.list_tools():
        assert tool["risk_level"] == "low"
        assert tool["execution_policy"] == "auto"


def test_start_chess_game_defaults_to_human_white():
    registry, _ = make_registry()
    result = registry.execute("start_chess_game", {}, principal=PRINCIPAL)
    assert result.success is True
    assert result.output["human_color"] == "white"
    assert result.output["status"] == "active"
    assert result.output["moves"] == []


def test_start_chess_game_rejects_invalid_color():
    registry, _ = make_registry()
    result = registry.execute("start_chess_game", {"human_color": "purple"}, principal=PRINCIPAL)
    assert result.success is True  # tool ran; the *handler* reports the error in its output
    assert "error" in result.output


def test_get_chess_board_reports_turn_and_legal_moves():
    registry, _ = make_registry()
    game_id = registry.execute("start_chess_game", {}, principal=PRINCIPAL).output["id"]

    board = registry.execute("get_chess_board", {"game_id": game_id}, principal=PRINCIPAL)
    assert board.success is True
    assert board.output["turn"] == "white"
    assert "e4" in board.output["legal_moves"]


def test_get_chess_board_missing_game_reports_error_in_output():
    registry, _ = make_registry()
    result = registry.execute("get_chess_board", {"game_id": "nope"}, principal=PRINCIPAL)
    assert result.success is True
    assert "error" in result.output


def test_make_chess_move_refuses_when_it_is_the_humans_turn():
    """human_color defaults to white, so it's the human's move first --
    iV (whichever principal calls the tool) must not be able to play it."""
    registry, _ = make_registry()
    game_id = registry.execute("start_chess_game", {}, principal=PRINCIPAL).output["id"]

    result = registry.execute("make_chess_move", {"game_id": game_id, "move": "e4"}, principal=PRINCIPAL)
    assert result.success is True
    assert "error" in result.output
    assert "not your turn" in result.output["error"] or "human's turn" in result.output["error"]


def test_make_chess_move_plays_a_legal_move_when_it_is_ivs_turn():
    registry, games = make_registry()
    game_id = registry.execute("start_chess_game", {"human_color": "black"}, principal=PRINCIPAL).output["id"]

    result = registry.execute("make_chess_move", {"game_id": game_id, "move": "e4"}, principal=PRINCIPAL)
    assert result.success is True
    assert "error" not in result.output
    assert result.output["moves"] == ["e4"]

    stored = games.get(game_id)
    assert stored.moves == ["e4"]


def test_make_chess_move_rejects_illegal_move_and_lists_legal_ones():
    registry, _ = make_registry()
    game_id = registry.execute("start_chess_game", {"human_color": "black"}, principal=PRINCIPAL).output["id"]

    result = registry.execute("make_chess_move", {"game_id": game_id, "move": "e5"}, principal=PRINCIPAL)
    assert result.success is True
    assert "error" in result.output
    assert "e4" in result.output["legal_moves"]


def test_make_chess_move_refuses_once_game_is_over():
    """Fool's mate (1. f3 e5 2. g4 Qh4#) with the human playing black --
    iV (white) plays its two losing moves through the tool being tested;
    black's replies, including the mating move, are applied directly
    through the store since make_chess_move would (correctly) refuse to
    let iV play black's move for it. See test_make_chess_move_refuses_when
    _it_is_the_humans_turn for that refusal in isolation."""
    registry, games = make_registry()
    game_id = registry.execute("start_chess_game", {"human_color": "black"}, principal=PRINCIPAL).output["id"]

    for ivs_move, humans_reply in [("f3", "e5"), ("g4", "Qh4")]:
        iv_result = registry.execute(
            "make_chess_move", {"game_id": game_id, "move": ivs_move}, principal=PRINCIPAL
        )
        assert "error" not in iv_result.output, iv_result.output

        game = games.get(game_id)
        if game.status != "active":
            break
        outcome = chess_engine.apply_move(game.fen, humans_reply)
        games.record_move(
            game_id, fen=outcome["fen"], san=outcome["san"],
            status=outcome["status"], result=outcome["result"],
        )

    final = games.get(game_id)
    assert final.status == "checkmate"

    result = registry.execute("make_chess_move", {"game_id": game_id, "move": "e4"}, principal=PRINCIPAL)
    assert result.success is True
    assert "already ended" in result.output["error"]
