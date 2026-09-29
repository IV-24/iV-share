import chess as chess_lib
import pytest

from adapters.games import chess_engine


def test_new_game_fen_is_the_standard_starting_position():
    assert chess_engine.new_game_fen() == chess_lib.STARTING_FEN


def test_side_to_move_starts_white():
    assert chess_engine.side_to_move(chess_engine.new_game_fen()) == "white"


def test_legal_moves_san_lists_all_twenty_opening_moves():
    moves = chess_engine.legal_moves_san(chess_engine.new_game_fen())
    assert len(moves) == 20
    assert "e4" in moves
    assert "Nf3" in moves


def test_apply_move_accepts_san():
    outcome = chess_engine.apply_move(chess_engine.new_game_fen(), "e4")
    assert outcome["san"] == "e4"
    assert outcome["status"] == "active"
    assert outcome["result"] is None
    assert chess_engine.side_to_move(outcome["fen"]) == "black"


def test_apply_move_accepts_uci():
    outcome = chess_engine.apply_move(chess_engine.new_game_fen(), "e2e4")
    assert outcome["san"] == "e4"


def test_apply_move_rejects_illegal_move_and_lists_legal_ones():
    with pytest.raises(chess_engine.IllegalMoveError) as exc_info:
        chess_engine.apply_move(chess_engine.new_game_fen(), "e5")  # black's move, not legal for white to open with
    assert "e4" in exc_info.value.legal_moves
    assert exc_info.value.move == "e5"


def test_apply_move_rejects_nonsense_input():
    with pytest.raises(chess_engine.IllegalMoveError):
        chess_engine.apply_move(chess_engine.new_game_fen(), "not a move")


def test_fools_mate_is_detected_as_checkmate():
    fen = chess_engine.new_game_fen()
    for move in ["f3", "e5", "g4"]:
        fen = chess_engine.apply_move(fen, move)["fen"]
    outcome = chess_engine.apply_move(fen, "Qh4")
    assert outcome["status"] == "checkmate"
    assert outcome["result"] == "0-1"  # black mates white
    assert outcome["in_check"] is True


def test_board_ascii_is_nonempty_and_reflects_position():
    board = chess_engine.board_ascii(chess_engine.new_game_fen())
    assert "r n b q k b n r" in board
