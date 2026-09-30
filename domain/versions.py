"""Derivation versions (M15). Stored history records which code produced it; a
derivation whose versions differ from these is stale and is re-derived from the stored
engine searches (no Stockfish). tests/test_versions.py fails when covered code changes
without a bump here (or a re-recorded hash, for a pure refactor).

The engine dimension (engine name, nodes, Threads, Hash) is recorded per run from the
server's analysis_config, not versioned here."""

# Search results -> payload: phase, win probability, flag bands/channels, features,
# concessions (mcp_server/server.py derive path + mcp_server/features.py)
ANALYZER_VERSION = "a1"
ANALYZER_SOURCE_HASH = "69b513e0b6a048c1"
# Payload -> coaching claims (domain/claims.py, domain/convert.py)
CLAIMS_VERSION = "c1"
CLAIMS_SOURCE_HASH = "b40349b61b274e1b"
