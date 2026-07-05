import os
import unittest
import pytest
import chess
import chess.pgn
import chess.engine
from unittest.mock import MagicMock, patch

from mcp_server.features import (
    evaluate_position_features,
    get_feature_deltas,
    get_quiet_concessions,
    compute_king_safety,
    compute_pawn_structure,
    compute_weak_squares,
    compute_piece_activity
)
from mcp_server.server import handle_analyze_pgn, handle_analyze_position

class TestMcpServerFeatures(unittest.TestCase):

    def setUp(self):
        self.original_env = dict(os.environ)
        os.environ["STOCKFISH_PATH"] = "dummy_stockfish_path"
        os.environ["STOCKFISH_NODES"] = "1000"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_env)

    def test_pgn_parse_failure(self):
        # Invalid PGN
        with self.assertRaises(ValueError):
            handle_analyze_pgn({"pgn": "invalid pgn text"})

    def test_illegal_move_pgn(self):
        # PGN with illegal move
        illegal_pgn = "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. Ng5 d5 5. exd5 Nxd5 6. Nxf7 Kxf7 7. Qf3+ Ke6 8. Nc3 Nce7 9. d4 c6 10. Bg5 h6 11. Bxe7 Bxe7 12. O-O-O Rf8 13. Qe4 Bg5+ 14. Kb1 Rf4 15. Qxe5+ Kf7 16. Nxd5 cxd5 17. Bxd5+ Kf8 18. g3 Rf5 19. f4 Rxe5 20. dxe5 Be7 21. Bxb7 Bxb7 22. Rxd8+ Rxd8 23. Re1 Rd2 24. h4 h5 25. Kc1 Rg2 26. Re3 Bc5 27. Rd3 Be4 28. Rd8+ Ke7 29. Rg8 Be3+ 30. Kd1 Bf3+ 31. Ke1 Rg1# 1-0" # Checkmate for Black but moves listed are legal, wait let us make one move illegal
        illegal_pgn_text = "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. d4 exd4 5. e5 d5 6. Bb5 Ne4 7. Nxd4 Bd7 8. Bxc6 bxc6 9. O-O Bc5 10. f3 Ng5 11. f4 Ne4 12. Be3 Bb6 13. Nd2 Nxd2 14. Qxd2 c5 15. Nf3 d4 16. Bf2 Bc6 17. f5 Qd5 18. Bh4 h6 19. e6 f6 20. Qd3 c4 21. Qa3 Bc5 22. Qa5 d3+ 23. Kh1 Bb6 24. Qxd5 Bxd5 25. cxd3 cxd3 26. Rad1 Be4 27. Nd2 Bd5 28. b3 Rd8 29. Nc4 Be4 30. Rf4 Rd4 31. Bf2 Rxc4 32. bxc4 Bxf2 33. Rxf2 Ke7 34. Rf4 Bc6 35. Rxd3 Rd8 36. Rxd8 Kxd8 37. Rg4 Be4 38. Rxe4 Ke7 39. Rd4 g5 40. Rd7+ Ke8 41. Rxc7 a5 42. Rd7 a4 43. c5 a3 44. c6 h5 45. c7 h4 46. c8=Q# 1-0" # Wait, let us change a move to be illegal, like 1. e4 e1
        illegal_pgn_bad = "1. e4 e1"
        with self.assertRaises(ValueError):
            handle_analyze_pgn({"pgn": illegal_pgn_bad})

    def test_weak_squares_camp_exclusion(self):
        # Ranks 1-2 and 7-8 are categorically excluded
        board = chess.Board()
        weak = compute_weak_squares(board)
        
        # Verify no weak squares are on ranks 1-2 (index 0, 1) or 7-8 (index 6, 7)
        for color_key in ["white", "black"]:
            for sq_name, _ in weak[color_key]:
                sq = chess.parse_square(sq_name)
                rank = chess.square_rank(sq)
                self.assertTrue(2 <= rank <= 5) # Ranks 3-6 only (0-indexed 2-5)

    def test_quiet_concessions_trigger_logic(self):
        # Carlsbad move 10...b5 test
        # Let's set up the board before 10...b5
        board_before = chess.Board()
        moves = [
            chess.Move.from_uci("d2d4"), chess.Move.from_uci("d7d5"),
            chess.Move.from_uci("c2c4"), chess.Move.from_uci("e7e6"),
            chess.Move.from_uci("b1c3"), chess.Move.from_uci("g8f6"),
            chess.Move.from_uci("c4d5"), chess.Move.from_uci("e6d5"),
            chess.Move.from_uci("c1g5"), chess.Move.from_uci("c7c6"),
            chess.Move.from_uci("e2e3"), chess.Move.from_uci("f8e7"),
            chess.Move.from_uci("f1d3"), chess.Move.from_uci("e8g8"),
            chess.Move.from_uci("d1c2"), chess.Move.from_uci("b8d7"),
            chess.Move.from_uci("g1e2"), chess.Move.from_uci("f8e8"),
            chess.Move.from_uci("e1g1")
        ]
        for m in moves:
            board_before.push(m)
            
        board_after = board_before.copy()
        # 10...b5
        board_after.push(chess.Move.from_uci("b7b5"))
        
        concessions = get_quiet_concessions(board_before, board_after, chess.BLACK)
        
        # Must report c5 concession and backward c6 pawn
        self.assertIn("new_weak_squares", concessions)
        self.assertIn("c5", concessions["new_weak_squares"])
        self.assertIn("new_backward_pawns", concessions)
        self.assertIn("c6", concessions["new_backward_pawns"])

    @patch("mcp_server.server.get_stockfish_path")
    @patch("chess.engine.SimpleEngine.popen_uci")
    def test_mcp_server_channel_flagging_mocked(self, mock_popen, mock_get_path):
        mock_get_path.return_value = "dummy_path"
        
        # Mock engine instance
        mock_engine = MagicMock()
        mock_popen.return_value = mock_engine
        
        # Mock WDL outputs
        # We return PovWdl objects from analyse
        def mock_analyse(board, limit, multipv=1):
            mock_wdl = chess.engine.PovWdl(chess.engine.Wdl(wins=300, draws=400, losses=300), chess.WHITE)
            legal_move = list(board.legal_moves)[0] if board.legal_moves else None
            return {"wdl": mock_wdl, "pv": [legal_move] if legal_move else []}
            
        mock_engine.analyse.side_effect = mock_analyse

        # Simple PGN text
        pgn_text = "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6"
        res = handle_analyze_pgn({"pgn": pgn_text, "max_flags": 4})
        
        # Verify result contains flags and summary
        self.assertIn("flags", res)
        self.assertIn("summary", res)

@pytest.mark.golden
class TestGoldenFixtures(unittest.TestCase):

    def setUp(self):
        self.stockfish_path = os.environ.get("STOCKFISH_PATH")
        if not self.stockfish_path:
            self.skipTest("STOCKFISH_PATH not configured")

    def test_fools_mate_golden(self):
        pgn_path = os.path.join(os.path.dirname(__file__), "fixtures", "fools_mate.pgn")
        pgn_text = open(pgn_path).read()
        
        res = handle_analyze_pgn({"pgn": pgn_text, "max_flags": 4})
        
        # Verify 2.g4 was flagged
        g4_flags = [f for f in res["flags"] if f["move_san"] == "g4" and f["move_number"] == 2]
        self.assertTrue(len(g4_flags) > 0)
        self.assertEqual(g4_flags[0]["channel"], "wdl")
        self.assertTrue(g4_flags[0]["wdl_delta"] < 0)

    def test_scandinavian_blitz_golden(self):
        pgn_path = os.path.join(os.path.dirname(__file__), "fixtures", "scandinavian_blitz.pgn")
        pgn_text = open(pgn_path).read()
        
        res = handle_analyze_pgn({"pgn": pgn_text, "max_flags": 10})
        
        # Negative assertion: 1...d5 is never flagged
        d5_flags = [f for f in res["flags"] if f["move_san"] == "d5"]
        self.assertEqual(len(d5_flags), 0)

        # Top-3 flags EXACT at max_flags=3
        res_3 = handle_analyze_pgn({"pgn": pgn_text, "max_flags": 3})
        top3_actual = {(f["move_san"], f["side"], f["move_number"]) for f in res_3["flags"]}
        expected_top3 = {("g4", "white", 22), ("Ne7", "black", 12), ("b4", "white", 13)}
        self.assertEqual(top3_actual, expected_top3)

        # Check for 21.h4 and its top features when max_flags is 10
        h4_flags = [f for f in res["flags"] if f["move_san"] == "h4" and f["move_number"] == 21]
        self.assertTrue(len(h4_flags) > 0)
        h4_flag = h4_flags[0]
        self.assertEqual(h4_flag["side"], "white")
        self.assertEqual(h4_flag["phase"], "middlegame")
        
        # king-safety/pawn-shield top-ranked feature deltas
        top_features = list(h4_flag["feature_deltas"].keys())
        self.assertTrue(len(top_features) > 0)
        self.assertIn(top_features[0], ["king_safety_delta", "pawn_shield_delta"])

    def test_carlsbad_quiet_golden(self):
        pgn_path = os.path.join(os.path.dirname(__file__), "fixtures", "carlsbad_quiet.pgn")
        pgn_text = open(pgn_path).read()
        
        res = handle_analyze_pgn({"pgn": pgn_text, "max_flags": 10})
        
        # 5...c6 and 6.e3 are never flagged on any channel
        c6_flags = [f for f in res["flags"] if f["move_san"] == "c6" and f["move_number"] == 5]
        e3_flags = [f for f in res["flags"] if f["move_san"] == "e3" and f["move_number"] == 6]
        self.assertEqual(len(c6_flags), 0)
        self.assertEqual(len(e3_flags), 0)

        # 10...b5 Carlsbad test
        b5_flags = [f for f in res["flags"] if f["move_san"] == "b5" and f["move_number"] == 10]
        self.assertTrue(len(b5_flags) > 0)
        b5_flag = b5_flags[0]
        
        # Not flagged by Channel 1
        self.assertEqual(b5_flag["channel"], "quiet_inaccuracy")
        
        # Top feature concessions
        concessions = b5_flag.get("concessions", {})
        self.assertIn("c5", concessions.get("new_weak_squares", []))
        self.assertIn("c6", concessions.get("new_backward_pawns", []))

    def test_concession_squares_ranks(self):
        pgn_path = os.path.join(os.path.dirname(__file__), "fixtures", "scandinavian_blitz.pgn")
        pgn_text = open(pgn_path).read()
        res = handle_analyze_pgn({"pgn": pgn_text, "max_flags": 100})
        
        # Verify that all concession squares for White mover lie in ranks 3-4, and for Black in ranks 5-6
        for f in res["flags"]:
            mover_side = f["side"].lower()
            concessions = f.get("concessions", {})
            for sq in concessions.get("new_weak_squares", []):
                rank = int(sq[1])
                if mover_side == "white":
                    self.assertIn(rank, [3, 4])
                else:
                    self.assertIn(rank, [5, 6])
            if f["move_san"] == "b4" and f["move_number"] == 13:
                new_ws = concessions.get("new_weak_squares", [])
                for sq in new_ws:
                    self.assertIn(sq, ["a3", "c3"])
