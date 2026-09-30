-- M15: persistent analysis history (PHASE_C_PLAN.md section 2.3).
-- Immutable: games, engine_runs, engine_positions. Derived and recomputable (no engine):
-- derivations and everything keyed by derivation_id.

CREATE TABLE games (
    game_pk          TEXT PRIMARY KEY,   -- sha256(source identity + movetext)
    content_id       TEXT NOT NULL,      -- GameAnalysis.game_id (claim ids use it)
    source_kind      TEXT NOT NULL,      -- 'player_history' (C10: storage is not profile inclusion)
    source_file      TEXT NOT NULL,
    source_index     INTEGER NOT NULL,
    played_on        TEXT,               -- YYYY-MM-DD
    end_time         TEXT,               -- HH:MM:SS (UTC per Chess.com EndTime)
    player_color     TEXT,               -- 'white' | 'black' | NULL (not the profiled player)
    player_rating    INTEGER,
    opponent_rating  INTEGER,
    time_control     TEXT,
    tc_base_s        INTEGER,
    tc_increment_s   INTEGER,
    tc_class         TEXT,               -- bullet | blitz | rapid | classical | daily | NULL
    result           TEXT,               -- win | draw | loss (player's side) | NULL
    termination      TEXT,               -- normal | time | abandoned | other | NULL
    start_fen        TEXT NOT NULL,
    movetext         TEXT NOT NULL,      -- sanitized movetext only (rule 7)
    plies            INTEGER NOT NULL,
    exclusion_reason TEXT,               -- NULL = profile-eligible (C10), else the first reason
    imported_at      TEXT NOT NULL
);
CREATE INDEX games_content ON games(content_id);
CREATE INDEX games_profile ON games(player_color, tc_class, played_on, end_time);

CREATE TABLE engine_runs (
    run_id      INTEGER PRIMARY KEY,
    game_pk     TEXT NOT NULL REFERENCES games(game_pk),
    engine      TEXT NOT NULL,           -- engine.id name, e.g. 'Stockfish 18'
    nodes       INTEGER NOT NULL,
    threads     INTEGER NOT NULL,
    hash_mb     INTEGER NOT NULL,
    multipv     INTEGER NOT NULL,
    created_at  TEXT NOT NULL,
    code_ref    TEXT,                    -- git short sha, informational
    UNIQUE (game_pk, engine, nodes, threads, hash_mb, multipv)
);

CREATE TABLE engine_positions (
    run_id    INTEGER NOT NULL REFERENCES engine_runs(run_id),
    position  INTEGER NOT NULL,          -- 0 = start, i = after ply i
    wins      INTEGER,                   -- WDL for the side to move (NULL: no WDL)
    draws     INTEGER,
    losses    INTEGER,
    pv_uci    TEXT NOT NULL,             -- space-separated
    PRIMARY KEY (run_id, position)
);

CREATE TABLE derivations (
    derivation_id    INTEGER PRIMARY KEY,
    run_id           INTEGER NOT NULL REFERENCES engine_runs(run_id),
    analyzer_version TEXT NOT NULL,
    claims_version   TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    UNIQUE (run_id, analyzer_version, claims_version)
);

CREATE TABLE move_analyses (
    derivation_id  INTEGER NOT NULL REFERENCES derivations(derivation_id) ON DELETE CASCADE,
    ply            INTEGER NOT NULL,
    move_number    INTEGER NOT NULL,
    side           TEXT NOT NULL,
    san            TEXT NOT NULL,
    uci            TEXT NOT NULL,
    is_player_move INTEGER NOT NULL,
    phase          TEXT NOT NULL,
    fen_before     TEXT NOT NULL,
    wdl_delta      REAL NOT NULL,
    best_move_san  TEXT NOT NULL,
    flag_channel   TEXT,                 -- wdl | quiet_inaccuracy | NULL
    flag_rank      INTEGER,
    wp_before      REAL,                 -- flagged moves only (from the payload)
    wp_after       REAL,
    pv             TEXT,                 -- JSON, flagged moves only
    refutation_pv  TEXT,
    feature_deltas TEXT,
    concessions    TEXT NOT NULL,        -- JSON, every ply, lists sorted (base rates, M16)
    PRIMARY KEY (derivation_id, ply)
);
CREATE INDEX moves_player_flag ON move_analyses(derivation_id, is_player_move, flag_channel);

CREATE TABLE claims (
    derivation_id INTEGER NOT NULL REFERENCES derivations(derivation_id) ON DELETE CASCADE,
    claim_id      TEXT NOT NULL,
    ply           INTEGER NOT NULL,
    type          TEXT NOT NULL,
    subject       TEXT,
    evidence      TEXT NOT NULL,         -- JSON [[provenance, fact, value], ...]
    supports      TEXT NOT NULL,         -- JSON [claim_id, ...]
    PRIMARY KEY (derivation_id, claim_id)
);
CREATE INDEX claims_type ON claims(type, derivation_id);

CREATE TABLE mistake_events (
    event_id       INTEGER PRIMARY KEY,
    derivation_id  INTEGER NOT NULL REFERENCES derivations(derivation_id) ON DELETE CASCADE,
    ply            INTEGER NOT NULL,
    game_pk        TEXT NOT NULL REFERENCES games(game_pk),
    phase          TEXT NOT NULL,
    channel        TEXT NOT NULL,
    wdl_delta      REAL NOT NULL,
    wp_before      REAL,
    position_state TEXT,                 -- winning (>=0.8) | balanced | losing (<=0.2)
    fen_before     TEXT NOT NULL,
    played_san     TEXT NOT NULL,
    best_move_san  TEXT NOT NULL,
    UNIQUE (derivation_id, ply)
);

CREATE TABLE mistake_categories (
    event_id INTEGER NOT NULL REFERENCES mistake_events(event_id) ON DELETE CASCADE,
    category TEXT NOT NULL,              -- a structural claim type, or 'unclassified'
    subject  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (event_id, category, subject)
);
CREATE INDEX categories_category ON mistake_categories(category, event_id);
