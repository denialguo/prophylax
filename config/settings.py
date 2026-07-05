import os
from typing import Optional, Dict

PINNED_STOCKFISH_VERSION: str = "18"

def get_stockfish_path() -> Optional[str]:
    """Retrieves the STOCKFISH_PATH environment variable."""
    return os.environ.get("STOCKFISH_PATH")

def get_limits(interactive: bool = False) -> Dict[str, int]:
    """
    Enforces the precedence rule for Stockfish search limits:
    - The evaluation/golden-dataset path requires STOCKFISH_NODES and must raise
      if only STOCKFISH_MOVETIME is set.
    - STOCKFISH_MOVETIME is only allowed for interactive analysis.
    """
    nodes_str = os.environ.get("STOCKFISH_NODES")
    movetime_str = os.environ.get("STOCKFISH_MOVETIME")

    if not interactive:
        if not nodes_str:
            raise ValueError(
                "STOCKFISH_NODES must be configured for the evaluation/golden-dataset path."
            )
        try:
            return {"nodes": int(nodes_str)}
        except ValueError:
            raise ValueError(f"STOCKFISH_NODES must be an integer, got: {nodes_str}")
    else:
        if movetime_str:
            try:
                return {"movetime": int(movetime_str)}
            except ValueError:
                raise ValueError(f"STOCKFISH_MOVETIME must be an integer, got: {movetime_str}")
        elif nodes_str:
            try:
                return {"nodes": int(nodes_str)}
            except ValueError:
                raise ValueError(f"STOCKFISH_NODES must be an integer, got: {nodes_str}")
        else:
            raise ValueError(
                "Either STOCKFISH_NODES or STOCKFISH_MOVETIME must be configured for interactive analysis."
            )

# Stockfish is single-threaded on the golden/eval path
STOCKFISH_THREADS: int = 1
