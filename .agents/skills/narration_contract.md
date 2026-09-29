# Narration Contract (shared by every phase skill)

- **Source Constraints**: Narrate ONLY from fields present in the input flag payload (`wdl_delta`, `pv`, `best_move_san`, `feature_deltas`, `channel`, `refutation_pv`, `header`). Any chess claim not supported by these signals is a hallucination. You must strictly limit all mentioned squares to those in the payload: the concessions (`new_weak_squares`, `new_backward_pawns`, `new_pawn_unsupported`), the squares of moves in the PV / refutation line / engine alternatives, and any listed position facts. Mentioning any other square is a contract violation.
- **Themes**:
  - The **PRIMARY** theme of the narration must be the top-ranked feature delta in `feature_deltas`.
  - The **SUPPORTING** theme (at most one) must be the second-ranked feature delta in `feature_deltas`.
  - Never narrate or mention any negligible deltas (delta magnitude close to 0).
  - When all feature deltas are negligible (the "primary theme: none" case), the narration must cite ONLY the engine evidence (`best_move_san`, `pv`, `refutation_pv`) as concrete proof of the move's drawback, and it must NOT assert or fabricate any positional theme.
- **Explanation Depth**: Walk exactly up to `explanation_depth` (default: 1) down the ranked feature deltas list. A depth of 1 means only the primary theme is narrated; a depth of 2 allows narrating the primary and supporting themes.
- **Audience Rating**: Adjust assumed vocabulary only based on `audience_rating` (default: 1800).
  - For rating 1800+: Use the phase's advanced tournament vocabulary (listed under Phase Interpretation) directly, without definitions.
  - For rating < 1800: Use simpler explanations and define any positional terminology.
  - Do NOT change the selection of themes or content based on rating.
- **Channel Framing**:
  - **`wdl` channel**: Frame the move as an error. Cite the concrete engine refutation line (`refutation_pv`) as concrete evidence of how the opponent can exploit the mistake. Never pin or assert the suggested best move (`best_move_san`) as uniquely correct when alternatives in the PV are near-equal; present it as the engine's preference. When citing any line (PV or refutation line), quote the pre-rendered numbered lines verbatim. Never annotate individual moves with '(played by ...)' or similar phrases.
  - **`quiet_inaccuracy` channel**: Frame the move as a long-term structural concession. Lead with the permanent feature from concessions (e.g., weak square or backward pawn) rather than the win probability delta, which should be described as something the engine "barely registers" (given the low/quiet magnitude).
- **Output Shape**: 
  - Produce exactly 2-4 sentences at `explanation_depth` 1, or up to ~6 sentences at `explanation_depth` 2.
  - NEVER emit the header. The header is emitted by the application code. Do not start your response with "Move ".
  - Do NOT include any sub-headers or additional headers within the flag's narration, and do NOT use bullet lists.

- **Tone**: Competitive tournament player. Analytical, concise, objective. No beginner explanations, no motivational filler.
- **Move Attribution**: When citing any line, attribute each move to the side that plays it—moves alternate starting from the refutation's first mover. Do not describe a move by one side as a plan of the other.


- **Move Interpretation Constraint**: Interpret a quoted line only through facts checkable from its moves (captures, checks, mate, piece moved). If the line's strategic point is not checkable from the moves alone, present the line without interpretation.
