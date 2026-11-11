# Commit map after history rewrite

On 2026-11-10 the repository history was rewritten to remove commit-message trailers. File contents,
trees and every recorded SHA-256 hash are unchanged; only commit hashes changed. The frozen predictions,
`MANIFEST.json` and the reports keep the original hashes, which map as follows:

| Role | Original | After rewrite |
|---|---|---|
| Frozen commit | `046bb19` | `0370ce1` |
| Extractor commit | `046bb1996c44060bfb0a1edb22f00420303a1143` | `0370ce17dd83583640a16c57745c681d8f4bceb7` |
| Verifier commit | `b2ac11c1e578ddf065ff8ee8e89739db5dc6ff86` | `b90590b6130ebd98b702499fcfbe054b4886a8b4` |

`FROZEN_COMMIT` in `freeze_predictions.py` points at the new hash, so the frozen-code test still compares
against the same tree.
