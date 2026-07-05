import chess
import chess.pgn
import chess.engine
import os
import io

engine_path = os.environ.get("STOCKFISH_PATH", "/opt/homebrew/bin/stockfish")
engine = chess.engine.SimpleEngine.popen_uci(engine_path)
engine.configure({"Threads": 1, "UCI_ShowWDL": True})

def get_wp(wdl, side):
    if not wdl:
        return 0.5
    w = wdl.white().wins
    d = wdl.white().draws
    l = wdl.white().losses
    if side == chess.WHITE:
        return (w + 0.5 * d) / 1000.0
    else:
        return (l + 0.5 * d) / 1000.0

pgn_text = "1. e4 d5 2. Nc3 d4 3. Nce2 e5 4. d3 Nc6 5. Ng3 Nf6 6. Nf3 Bg4 7. Be2 Bxf3 8. Bxf3 Bd6 9. O-O O-O 10. Bg5 h6 11. Bd2 Re8 12. c3 Ne7 13. b4 c5 14. b5 Bc7 15. c4 Ba5 16. a4 Bxd2 17. Qxd2 Ng6 18. Nf5 Nf4 19. g3 Nh3+ 20. Kg2 Ng5 21. h4 Ne6 22. g4 Nf4+ 23. Kh2 g6 24. Ng3 Nxg4+ 25. Bxg4 Qxh4+ 26. Kg1 Qxg4 27. a5 h5 28. Kh1 h4 29. Rg1 Qh3#"
game = chess.pgn.read_game(io.StringIO(pgn_text))
board = game.board()

moves = list(game.mainline_moves())
evals = []

info = engine.analyse(board, chess.engine.Limit(nodes=1000000))
evals.append(info.get("wdl"))

for m in moves:
    board.push(m)
    info = engine.analyse(board, chess.engine.Limit(nodes=1000000))
    evals.append(info.get("wdl"))

board = game.board()
for idx, m in enumerate(moves):
    color = board.turn
    wp_before = get_wp(evals[idx], color)
    wp_after = 1.0 - get_wp(evals[idx+1], not color)
    delta = wp_before - wp_after
    print(f"Ply {idx+1:02d} ({board.san(m)}): Delta={delta:+.4f}")
    board.push(m)

engine.quit()
