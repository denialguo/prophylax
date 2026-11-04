#!/usr/bin/env bash
# Reruns run_queen until all held-out outputs exist: each token in turn, then a wait. A quota
# refusal is expected and just moves on. MAX_ERRORS other failures in a row (network, server) or a
# changed Space/model revision stops the loop. Generation only: freezing predictions stays manual.
#
# Usage: caffeinate -i evals/queen_heldout/keep_trying.sh
cd "$(dirname "$0")/../.." || exit 1
RETRY_SECONDS="${RETRY_SECONDS:-1800}"
MAX_ERRORS="${MAX_ERRORS:-6}"
set -a; . "${ENV_FILE:-app/.env}"; set +a
errors=0

while true; do
    for token in "$HF_TOKEN" "$HF_TOKEN_SCHOOL" "$HF_TOKEN_FRIEND"; do
        [ -n "$token" ] || continue
        HF_TOKEN="$token" uv run python -m evals.queen_heldout.run_queen
        code=$?
        case "$code" in
            0) echo "all outputs complete"; exit 0 ;;
            3) errors=0 ;;
            1) errors=$((errors + 1))
               if [ "$errors" -ge "$MAX_ERRORS" ]; then echo "stopping: $errors errors in a row"; exit 1; fi ;;
            *) echo "stopping: run_queen exited $code"; exit "$code" ;;
        esac
    done
    echo "$(date '+%F %T') waiting ${RETRY_SECONDS}s"
    sleep "$RETRY_SECONDS"
done
