from core.games.chess import ChessGameStore
from core.storage.memory import InMemoryStorage


def test_create_and_get():
    store = ChessGameStore(InMemoryStorage())
    game = store.create(human_color="white", fen="startpos", conversation_id="conv-1")
    fetched = store.get(game.id)
    assert fetched.human_color == "white"
    assert fetched.fen == "startpos"
    assert fetched.moves == []
    assert fetched.status == "active"
    assert fetched.result is None
    assert fetched.conversation_id == "conv-1"


def test_get_missing_returns_none():
    store = ChessGameStore(InMemoryStorage())
    assert store.get("does-not-exist") is None


def test_record_move_appends_and_updates_position():
    store = ChessGameStore(InMemoryStorage())
    game = store.create(human_color="white", fen="startpos")

    updated = store.record_move(game.id, fen="fen-after-e4", san="e4", status="active", result=None)

    assert updated.fen == "fen-after-e4"
    assert updated.moves == ["e4"]
    assert updated.status == "active"

    updated2 = store.record_move(updated.id, fen="fen-after-e5", san="e5", status="active", result=None)
    assert updated2.moves == ["e4", "e5"]


def test_record_move_sets_status_and_result_on_game_over():
    store = ChessGameStore(InMemoryStorage())
    game = store.create(human_color="white", fen="startpos")

    updated = store.record_move(
        game.id, fen="mate-fen", san="Qh4#", status="checkmate", result="0-1"
    )
    assert updated.status == "checkmate"
    assert updated.result == "0-1"


def test_record_move_on_missing_game_returns_none():
    store = ChessGameStore(InMemoryStorage())
    assert store.record_move("nope", fen="x", san="e4", status="active", result=None) is None


def test_list_orders_newest_first_and_filters_by_status():
    store = ChessGameStore(InMemoryStorage())
    first = store.create(human_color="white", fen="startpos")
    second = store.create(human_color="black", fen="startpos")
    store.record_move(first.id, fen="mate-fen", san="Qh4#", status="checkmate", result="0-1")

    all_games = store.list()
    assert [g.id for g in all_games] == [second.id, first.id]

    active_only = store.list(status="active")
    assert [g.id for g in active_only] == [second.id]
