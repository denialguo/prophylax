import chess.pgn
import chess.engine
import os

# 1. Load your pinned Stockfish binary
engine_path = os.environ.get("STOCKFISH_PATH")
if not engine_path:
    raise ValueError("STOCKFISH_PATH is not set. Run: export STOCKFISH_PATH=$(which stockfish)")

engine = chess.engine.SimpleEngine.popen_uci(engine_path)

# 2. Configuration
PGN_FILE = "my_games.pgn"
TARGET_MOVES = ["b4", "b5", "g4", "g5"]
DEPTH = 16 # Fast enough to scan in bulk, deep enough to catch structural issues
MIN_EVAL_DROP = 40  # Minimum 0.40 pawn drop (centipawns)
MAX_EVAL_DROP = 150 # Maximum 1.50 pawn drop (ignore full piece blunders)

print(f"Scanning {PGN_FILE} for {TARGET_MOVES} inaccuracies...")

with open(PGN_FILE) as pgn:
    game_count = 0
    while True:
        game = chess.pgn.read_game(pgn)
        if game is None:
            break
        
        game_count += 1
        board = game.board()
        
        # Iterate through the game, up to move 15 (ply 30)
        for ply, move in enumerate(game.mainline_moves()):
            if ply >= 30: 
                break 
                
            san_move = board.san(move)
            
            if san_move in TARGET_MOVES:
                # We found a target move! Let's evaluate BEFORE the move
                info_before = engine.analyse(board, chess.engine.Limit(depth=DEPTH))
                score_before = info_before["score"].pov(board.turn).score(mate_score=10000)
                
                # Push the move to the board
                board.push(move)
                
                # Evaluate AFTER the move
                info_after = engine.analyse(board, chess.engine.Limit(depth=DEPTH))
                # Note: after the move, it's the opponent's turn, so we negate to keep perspective
                score_after = -info_after["score"].pov(board.turn).score(mate_score=10000)
                
                # Calculate the drop in evaluation
                if score_before is not None and score_after is not None:
                    eval_drop = score_before - score_after
                    
                    if MIN_EVAL_DROP <= eval_drop <= MAX_EVAL_DROP:
                        print("\n" + "="*50)
                        print(f"🎯 GOLDEN DATASET CANDIDATE FOUND! (Game #{game_count})")
                        print(f"White: {game.headers.get('White')} vs Black: {game.headers.get('Black')}")
                        print(f"Move: {int(ply/2) + 1}. {san_move}")
                        print(f"Eval Before: {score_before/100:.2f} | Eval After: {score_after/100:.2f} | Drop: -{eval_drop/100:.2f}")
                        print(f"FEN After: {board.fen()}")
                        print("="*50)
                continue # Move already pushed

            # If not a target move, just push it and continue
            board.push(move)

engine.quit()
print("\nScan complete.")