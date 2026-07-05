from mcp_server.server import handle_analyze_position
try:
    res = handle_analyze_position({"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", "multipv": 1})
    print("Success! Features:", res["features"]["weak_squares"])
except Exception as e:
    import traceback
    traceback.print_exc()
