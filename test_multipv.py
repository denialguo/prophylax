import chess
import chess.engine
import os

engine_path = os.environ.get('STOCKFISH_PATH', '/opt/homebrew/bin/stockfish')
engine = chess.engine.SimpleEngine.popen_uci(engine_path)
board = chess.Board()
try:
    results = engine.analyse(board, chess.engine.Limit(nodes=100000), multipv=3)
    print("Success")
except Exception as e:
    print("Error:", repr(e))
engine.quit()
