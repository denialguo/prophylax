"""M15 version guard: a change to code a derivation version covers must come with a
version bump in domain/versions.py (the derivation meaning changed; stored history is
re-derived) or, for a pure refactor, a re-recorded source hash. Either way it is a
deliberate, reviewed act."""
import hashlib
import inspect

import domain.claims
import domain.convert
import domain.models
import domain.versions as v
import mcp_server.features
import mcp_server.server as server


def _hash(*parts) -> str:
    return hashlib.sha256("".join(parts).encode()).hexdigest()[:16]


def analyzer_source_hash() -> str:
    return _hash(inspect.getsource(mcp_server.features),
                 *(inspect.getsource(f) for f in (server.derive_game_payload, server.get_game_phase,
                                                  server.get_win_probability, server.get_pv_san)))


def claims_source_hash() -> str:
    return _hash(*(inspect.getsource(m) for m in (domain.claims, domain.convert, domain.models)))


def _msg(name, got):
    return (f"{name}-covered code changed. If what it derives changed, bump the {name} version in "
            f"domain/versions.py and set its hash to {got!r}; stored history will then be re-derived "
            f"(python -m history rederive). If it is a pure refactor, only update the hash.")


def test_analyzer_version_covers_its_code():
    got = analyzer_source_hash()
    assert got == v.ANALYZER_SOURCE_HASH, _msg("ANALYZER", got)


def test_claims_version_covers_its_code():
    got = claims_source_hash()
    assert got == v.CLAIMS_SOURCE_HASH, _msg("CLAIMS", got)
