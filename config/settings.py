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
# Pinned explicitly (Stockfish's default today) so a default change can't move results
STOCKFISH_HASH_MB: int = 16

# Input bounds for MCP tools (untrusted input; each ply costs two searches)
MAX_PGN_CHARS: int = 100_000
MAX_PGN_PLIES: int = 400
MAX_FLAGS: int = MAX_PGN_PLIES  # only slices the output; can't exceed one flag per ply
MAX_MULTIPV: int = 5


def get_search_timeout() -> float:
    """Wall-clock cap for ONE engine search (seconds). A node-limited search has no
    built-in timeout; exceeding this kills the engine and returns a typed error."""
    return float(os.environ.get("STOCKFISH_SEARCH_TIMEOUT_S", "60"))


def get_tool_timeout() -> float:
    """Agent-side cap for one whole MCP tool call (seconds), incl. a full-game analysis."""
    return float(os.environ.get("PROPHYLAX_TOOL_TIMEOUT_S", "900"))

# Model configuration — single source of truth.
# ────────────────────────────────────────────────
# Narrator models that have passed the narration gate (pytest -v -m narration).
APPROVED_NARRATORS: list[str] = [
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    # certified 2026-09-28: 9/9 narration cases (artifacts/junit_certify_gptoss120b*.xml)
    "groq/openai/gpt-oss-120b",
]

_DEFAULT_NARRATOR: str = "gemini-3.5-flash"
_DEFAULT_JUDGE: str = "gemini-3.1-flash-lite"


def resolve_model(name: str):
    """What an ADK Agent's `model` takes: Gemini names pass through as strings;
    provider-prefixed names (e.g. "groq/llama-3.3-70b-versatile") go through
    LiteLLM, which reads that provider's key from the environment (GROQ_API_KEY)."""
    if "/" not in name:
        return name
    from google.adk.models.lite_llm import LiteLlm
    return LiteLlm(model=name)


def model_name(model) -> str:
    """Inverse of resolve_model: the configured name of an Agent's model."""
    return getattr(model, "model", model)


def get_narrator_model() -> str:
    """Resolve the narrator model with startup validation.

    Precedence: NARRATOR_MODEL env var > _DEFAULT_NARRATOR.
    Hard-fails if the resolved model is not in APPROVED_NARRATORS, unless
    PROPHYLAX_CERTIFY_NARRATOR names exactly that model (the certification run).
    """
    model = os.environ.get("NARRATOR_MODEL", _DEFAULT_NARRATOR)
    if model not in APPROVED_NARRATORS and os.environ.get("PROPHYLAX_CERTIFY_NARRATOR") != model:
        raise ValueError(
            f"Narrator model '{model}' is not certified. "
            f"Approved models: {APPROVED_NARRATORS}. "
            f"To certify a new model, run: "
            f"PROPHYLAX_CERTIFY_NARRATOR={model} NARRATOR_MODEL={model} pytest -v -m narration"
        )
    return model


def get_fallback_narrators(primary: str) -> list[str]:
    """Return the ordered fallback list (APPROVED_NARRATORS minus the primary).

    Used at runtime when the primary model returns a quota/rate-limit error.
    """
    if os.environ.get("PROPHYLAX_CERTIFY_NARRATOR"):
        return []  # a certification run must be judged on the candidate's own output
    return [m for m in APPROVED_NARRATORS if m != primary]


def get_narration_input() -> str:
    """What the narrator is given (M13): "flag" (the server payload, today's default) or
    "claims" (the verified coaching claims only)."""
    value = os.environ.get("PROPHYLAX_NARRATION_INPUT", "flag")
    if value not in ("flag", "claims"):
        raise ValueError(f"PROPHYLAX_NARRATION_INPUT must be 'flag' or 'claims', got {value!r}")
    return value


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
