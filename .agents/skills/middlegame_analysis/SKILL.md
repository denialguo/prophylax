---
name: analysing-middlegames
description: >
  Covers middlegame plans, prophylaxis, weak squares, color-complex weaknesses,
  minority attacks, and pawn structure dynamics. Trigger keywords: middlegame, prophylaxis, pawn structure, weak squares, color-complex, minority attack.
  Do NOT use for opening prep, development, or endgame technique.
---

# Analysing Middlegames - Narration Rules

## Narration Contract
- **Source Constraints**: Narrate ONLY from fields present in the input flag payload (`wdl_delta`, `pv`, `best_move_san`, `feature_deltas`, `channel`, `refutation_pv`, `header`). Any chess claim not supported by these signals is a hallucination. You must strictly limit all mentioned squares to those returned in the concessions payload (`new_weak_squares`, `new_backward_pawns`) and the squares of moves in the PV / refutation line. Mentioning any other squares (e.g., `c4` if not in concessions/PV) is a contract violation.
- **Themes**:
  - The **PRIMARY** theme of the narration must be the top-ranked feature delta in `feature_deltas`.
  - The **SUPPORTING** theme (at most one) must be the second-ranked feature delta in `feature_deltas`.
  - Never narrate or mention any negligible deltas (delta magnitude close to 0).
  - When all feature deltas are negligible (the "primary theme: none" case), the narration must cite ONLY the engine evidence (`best_move_san`, `pv`, `refutation_pv`) as concrete proof of the move's drawback, and it must NOT assert or fabricate any positional theme.
- **Explanation Depth**: Walk exactly up to `explanation_depth` (default: 1) down the ranked feature deltas list. A depth of 1 means only the primary theme is narrated; a depth of 2 allows narrating the primary and supporting themes.
- **Audience Rating**: Adjust assumed vocabulary only based on `audience_rating` (default: 1800).
  - For rating 1800+: Use advanced tournament chess vocabulary directly (e.g., prophylaxis, color complex, weak square, minority attack, pawn structure dynamics) without definitions.
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

## Phase Interpretation
In the middlegame phase, narrate the feature changes and concessions according to the following principles:
- **Features as Plans**: Avoid abstract evaluations of features; instead, narrate static concessions as dynamic middlegame plans.
- **Weak Squares as Outposts/Routes**: Frame weak squares not just as empty holes, but as potential opponent outposts or routes for piece penetration. Always name the specific square (e.g., "c5") and, when the `refutation_pv` indicates it, describe the opponent's occupying plan.
- **King-Safety as Attack Potential**: Frame king-safety deltas around the opponent's attacking potential and mating motifs, illustrating how the pawn shield advancement facilitates a kingside storm.
- **Prophylaxis Framing**: Position the error around prophylaxis: explain exactly what opponent resource or plan the move failed to prevent or neutralize, as concretely demonstrated by the `refutation_pv` sequence.

- **Move Interpretation Constraint**: Interpret a quoted line only through facts checkable from its moves (captures, checks, mate, piece moved). If the line's strategic point is not checkable from the moves alone, present the line without interpretation.
