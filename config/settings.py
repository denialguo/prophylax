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

# Model configuration — single source of truth.
# ────────────────────────────────────────────────
# Narrator models that have passed the narration gate (pytest -v -m narration).
APPROVED_NARRATORS: list[str] = [
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
]

_DEFAULT_NARRATOR: str = "gemini-3.5-flash"
_DEFAULT_JUDGE: str = "gemini-3.1-flash-lite"


def get_narrator_model() -> str:
    """Resolve the narrator model with startup validation.

    Precedence: NARRATOR_MODEL env var > _DEFAULT_NARRATOR.
    Hard-fails if the resolved model is not in APPROVED_NARRATORS.
    """
    model = os.environ.get("NARRATOR_MODEL", _DEFAULT_NARRATOR)
    if model not in APPROVED_NARRATORS:
        raise ValueError(
            f"Narrator model '{model}' is not certified. "
            f"Approved models: {APPROVED_NARRATORS}. "
            f"To certify a new model, run: "
            f"NARRATOR_MODEL={model} pytest -v -m narration"
        )
    return model


def get_fallback_narrators(primary: str) -> list[str]:
    """Return the ordered fallback list (APPROVED_NARRATORS minus the primary).

    Used at runtime when the primary model returns a quota/rate-limit error.
    """
    return [m for m in APPROVED_NARRATORS if m != primary]


def get_judge_model() -> str:
    """Resolve the judge model with collision check.

    Hard-fails if JUDGE_MODEL == the resolved narrator model.
    """
    judge = os.environ.get("JUDGE_MODEL", _DEFAULT_JUDGE)
    narrator = get_narrator_model()
    if judge == narrator:
        raise ValueError(
            f"JUDGE_MODEL ('{judge}') must differ from the narrator model "
            f"('{narrator}') to avoid self-grading. Set JUDGE_MODEL to a "
            f"different model."
        )
    return judge
