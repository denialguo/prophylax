# QUEEN × Prophylax: completed 18-position audit

Date: 2026-11-02

This document merges the earlier audit of cases 01–09 with a new audit of cases 10–18. It evaluates selected concrete factual, geometric, material, legality, and mechanism claims. It is **not** an exhaustive sentence-level annotation and must not be converted into a population-level accuracy percentage.

Reference analysis is the benchmark bundle's Stockfish 18 search at 1,000,000 nodes, MultiPV 3, Threads 1, Hash 64 MiB. Move-rank comparisons are finite-search comparisons, not proofs of objective best play.

## Completion status

- Prepared positions: **18/18**
- Complete saved QUEEN responses: **18/18**
- Structured critical lines with a detected illegal ply: **0/18**
- QUEEN best move = Stockfish first-ranked move: **8/18**
- QUEEN best move appears in Stockfish MultiPV-3: **16/18**
- Outside the stored MultiPV-3: **10_carlsbad_after (Nf4)** and **13_closed_center (...b6)**
- Selected clear false factual/geometry/material/legality claim instances identified: **at least 76** across the 18 responses (38 in cases 01–09 and 38 additional instances in cases 10–18).

The 76 figure is a conservative audit count of selected claim instances, not a denominator-based accuracy metric. Repeated false assertions are counted when QUEEN repeats them in separate parts of an explanation. Strategic evaluations and debatable terminology are mostly excluded from this hard-error count.

## High-level result

The full suite strengthens the pilot conclusion:

**QUEEN is substantially more reliable at selecting plausible/strong moves than at giving factually faithful explanations.**

Its structured critical line is usually clean, but the surrounding prose repeatedly invents attack relations, pins, open files, passed pawns, promotion distances, material states, and side-line consequences. Some explanations also misidentify the decisive mechanism even when the recommended move is strong.

This strongly supports the intended architecture:

> QUEEN proposes rich chess hypotheses → extraction converts concrete statements into typed candidates → Prophylax independently returns VERIFIED / FALSE / UNKNOWN → only verified facts are eligible for grounded coaching.

---

# Cases 01–09 (previous audit summary)

## 01_fools_mate

QUEEN move: **Qh4#**. Stockfish first: **Qh4#**.

Clear errors:
1. Says the f3 pawn blocks the bishop on f1. False: f3 is not on a bishop diagonal from f1.
2. Says a bishop on c5 attacks the g5 pawn. False: c5 does not attack g5.

Other unsupported claims: c5–f2 plan through an e3 blocker; calling g4 a fixed target.

## 02_legal_trap

QUEEN move: **Bxf7+**. Stockfish first: **Bxf7+**, mate in 2.

Clear errors include:
- Bd1 allegedly attacks Ra1 (false geometry).
- ...Kd7 after Bxf7+ is illegal because Ne5 controls d7.
- Bf7 allegedly covers e7.
- Ne5 allegedly controls e6.
- Qh4 allegedly attacks g2 and Rh1 despite incorrect geometry/blockers.

Missed mechanism: after the correct Bxf7+ Ke7, the reference continuation is **Nd5#**; QUEEN misses the immediate mate.

## 03_smothered_mate

QUEEN move: **Nf7+**. Stockfish first: **Qg8+**, with Qg8+ Rxg8 Nf7#.

Clear errors include:
- Nf7 allegedly attacks Rf8 and g7.
- ...h6 is given while Black is in check and does not resolve it.
- Rf8 allegedly captures a knight on h6.
- Invented White pawn/material in a described ending.
- Qe5 allegedly attacks Rf8.
- Qe5→Qh8 is described despite a g7 blocker.

Missed mechanism: the immediate queen-sacrifice smothered mate.

## 04_knight_fork

QUEEN move: **Nxd6+**. Stockfish first: **Nxd6+**; the capture immediately leaves K+N vs K, a dead draw.

Clear errors:
- King d7 allegedly attacks a knight on f5; it does not.
- Kc6 allegedly captures a knight on c4; it does not.

Branch-integrity issue: prose continues beyond a terminal insufficient-material draw.

## 05_pin

QUEEN move: **Bxc6+**. Stockfish first: **Bxc6+**.

Clear error:
- Knight d4 allegedly directly attacks pawn d5.

Also contains unsupported material characterization of the resulting branch.

## 06_knight_net

QUEEN move: **...f6**. Stockfish first: **...h6**; ...f6 is #3 in the stored MultiPV.

Clear errors include:
- ...h5 allegedly creates a second passed pawn even though h7 was already passed in the diagnostic.
- Ng5 allegedly has only f7/h7 as destinations.
- Wrong remaining pawn count after a line.
- Calls the h-file open while an h6 pawn still occupies it.

Missed mechanism: the diagnostic's central idea is the ...h6 knight net; QUEEN never explains it.

## 07_noahs_ark

QUEEN move: **Bg5**. Stockfish first: **Bxc4**; Bg5 is #2.

Clear errors include:
- Claims an initial Black material edge when material is equal.
- Bg5 allegedly pressures f7.
- Calls the f-file open while f7 is occupied by a pawn.
- Calls Bg5 en prise when it is not attacked in the stated branch.
- Calls the a-file open while a6 remains.
- Be7 allegedly attacks/harasses Qd4.
- ...bxc4 allegedly opens the c-file while the pawn itself occupies c4.

## 08_back_rank

QUEEN move: **Rd8#**. Stockfish first: **Rd8#**. Main mating explanation is good.

Alternative-line errors include wrong pawn inventory and false passed-pawn claims for both h-pawns.

## 09_carlsbad_before

QUEEN move: **...h6**. Stockfish first: **...Nf8**; ...h6 is #2 by 1 cp.

Clear errors include:
- Bg5 allegedly pins Nf6 to Qd8 despite Be7 physically blocking the ray.
- Repeats that false pin after Bh4.
- Calls f-, g-, and c-files open while pawns/pieces still block them.
- Nf4 allegedly attacks Nf6.

---

# Cases 10–18 (new audit)

## 10_carlsbad_after

FEN: `r1bqr1k1/p2nbppp/2p2n2/1p1p2B1/3P4/2NBP3/PPQ1NPPP/R4RK1 w - - 0 11`

QUEEN move: **Nf4**. Stored Stockfish top three: **a4, Nb1, Nc1**. Nf4 is outside this fixed MultiPV-3.

Clear false claim instances:
1. Repeats that Bg5 pins Nf6 to Qd8. False: Be7 sits between f6 and d8.
2. Says Nf4 "joins the pin" and adds a second attacker on f6. A knight on f4 does not attack f6.
3. After Bxf6 Nxf6, says this opens the **a1–h8** diagonal for the bishop on d3. Bd3 is not on that diagonal.
4. After ...Bxf6, says Na4 immediately targets b5. A knight on a4 does not attack b5.
5. Says a4 opens the a-file. Both a-pawns remain; the file is not open.
6. Later calls a1–a8 open while a-pawns still occupy the file.
7. After ...bxa4 Nxa4, again says the a-file is open even though Black's a7 pawn remains.

The prose is therefore semantically unreliable even before considering whether Nf4 is the best move.

## 11_scandinavian_after

FEN: `r2qr1k1/ppp1npp1/3b1n1p/4p3/1P1pP3/2PP1BN1/P2B1PPP/R2Q1RK1 b - - 0 13`

QUEEN move: **...dxc3**. Stockfish first: **...a5**; ...dxc3 is #2.

Good: ...dxc3 really does create a passed c3 pawn before Bxc3, and b4 really controls c5.

Clear false claim instances:
1. After ...dxc3 Bxc3, says White's bishop pair compensates for a **material deficit**. Material is equal after the pawn exchange.
2. Says ...a5 creates a passed a-pawn. It does not; White still has relevant a/b pawns ahead.
3. The same sentence says ...a5 opens the a-file. It does not.
4. After bxa5 Rxa5, calls the rook active on an **open** a-file despite White's a2 pawn remaining.
5. In another branch, says ...dxc3 opens the c-file. Black's c7 pawn remains, so the c-file is not open.
6. After Bxc3 Rxa5, again calls the a-file open despite a2 remaining.
7. In the ...Nc6-b4 cxb4 line, says the sequence opens the c-file; c7 still occupies the file.
8. Describes the resulting line as White having won a "clean pawn." The displayed sequence sacrifices Black's knight and then bishop for White pawns; the material swing is much larger than one pawn.

## 12_start

FEN: starting position.

QUEEN move: **e4**. Stockfish first: **e4**.

The main opening-level statements are mostly conventional and reasonable.

Clear false claim instances:
1. Says **1.c3 blocks the bishop on c1**. A pawn on c3 is not on either diagonal from c1; the bishop is blocked by b2/d2 in the starting structure.
2. The conclusion repeats that 1.c3 blocks the c1 bishop.

Other claims such as a "latent check on a4" are too loose to score as hard errors here.

## 13_closed_center

FEN: `r1bq1rk1/pp3ppp/2n1pn2/2pp4/2PP4/P1PBPN2/5PPP/R1BQ1RK1 b - - 0 9`

QUEEN move: **...b6**. Stored Stockfish top three: **...Qc7, ...Re8, ...Na5**. ...b6 is outside the fixed MultiPV-3.

Clear false claim instances:
1. Says ...b6 frees the rook on a8. The rook was not blocked by the b7 pawn in that sense and remains constrained by a7 on its file.
2. Says cxd5 opens d1–d8 for the queen. White's bishop on d3 blocks the d-file.
3. Gives **c3-c4** while White already has a pawn on c4. The move is illegal.
4. In the ...dxc4 / ...cxd4 sequence, says the c1–c8 line is opened "for the rook" despite remaining c-file blockers and no rook occupying that line.
5. Says c4xd5 creates a passed d-pawn. Black's e6 pawn is ahead on an adjacent file and immediately captures it in QUEEN's own next move.
6. Says ...e6xd5 opens e1–e8 for the rook, despite White's e3 pawn and no rook already on that file.
7. Says ...Nc6-a5 attacks the bishop on d3. A knight on a5 does not attack d3.

Additional weakness: Bb2 is described as activating the bishop even though the bishop's immediate diagonals are constrained by White's own a3/c3 structure.

## 14_pawn_structure_control

FEN: `rnb2rk1/p1p1qpp1/1p5p/3p4/3P4/4PN2/PP3PPP/R2QKB1R w KQ - 0 11`

QUEEN move: **Be2**. Stockfish first: **b3**; Be2 is #2.

Clear false claim instances:
1. Says Black's bishop on c8 is blocked by its own pawns. In the supplied FEN its diagonals are open; b7/d7 are empty.
2. Says ...c5 frees that c8 bishop. The c7 pawn is not on the bishop's diagonal and was not what blocked it.
3. In `Be2 c5 b3 Qb4+`, **Qb4 is illegal** because Black's pawn on c5 blocks the e7-d6-c5-b4 diagonal.
4. In `...c5 dxc5 Qxc5`, says Black "wins a pawn." Black is recapturing after exchanging its c-pawn for White's d-pawn; this is not a net pawn gain.
5. In the `Bd3 ...Bg4 O-O Qb4+` branch, Qb4 is a legal move but it is **not check** after White has castled to g1.

## 15_c6_promotion_distance

FEN: `7k/8/2P5/8/8/8/8/K7 w - - 0 1`

QUEEN move: **c7**. Stored Stockfish ranks **Ka2 #1, Kb2 #2, c7 #3**.

Clear false claim:
1. The introduction says a white pawn on **c6 is one step from promotion**. It needs two pushes: c7 and then c8=Q.

Additional engine-inconsistent / unsupported claims (not included in the hard-error count):
- QUEEN calls c7 the uniquely decisive immediate push even though the stored search prefers king moves and all top three are winning.
- QUEEN claims "forced mate in 7"; the fixed Stockfish reference reports a large centipawn advantage rather than mate for c7 at this search budget.
- The conclusion says any king move surrenders the crucial tempo and changes forced mate to merely winning; the stored search does not support this narrative.

This case cleanly reproduces the original promotion-distance failure.

## 16_false_passed_b6

FEN: `7k/p7/1p6/2P5/8/8/8/K7 w - - 0 1`

QUEEN move: **c6**. Stockfish first: **c6**.

Good: unlike the earlier user-observed failure, this run does **not** falsely call cxb6 a passed b-pawn. It correctly recognizes ...axb6.

Clear false claim instances:
1. After c6, says the pawn is "one tempo from promotion." It still requires c7 and c8=Q.
2. After cxb6 axb6, says White's king is too far away and Black converts the passer. The stored Stockfish line is essentially equal (+0.03) and explicitly shows White's king catching the b6 pawn with Kxb6.

So the diagnostic avoided one known hallucination but produced a different concrete endgame error.

## 17_opposition

FEN: `8/8/4k3/8/4K3/8/4P3/8 w - - 0 1`

QUEEN move: **Kf4**. Stockfish first: **Kf4**.

Clear false claim:
1. Says playing e4 "immediately creates a passed pawn." The e2 pawn is already passed in the initial position because Black has no pawns.

Additional conceptual / engine-inconsistent issues (kept out of the hard-error count):
- The prose repeatedly uses "opposition" for king configurations that are not direct opposition positions (for example Kf4 vs Ke6, and later Kf5 vs Kd6).
- It calls e3 the least effective option and says the evaluation falls to a modest edge, while Stockfish ranks e3 #2 at roughly +5.17, nearly the same winning evaluation as Kf4.
- The explanation therefore overfits a textbook opposition narrative to a position where several moves remain strongly winning.

These are precisely the sort of mechanism/strategy claims that should remain UNKNOWN in the current verifier milestone.

## 18_rook_pawn_draw

FEN: `k7/8/PK6/8/8/8/8/8 w - - 0 1`

QUEEN move: **Ka5**. Stockfish first: **Kc6**; Ka5 is #2. All three stored MultiPV moves evaluate 0.00.

Clear false claim instances:
1. Says the pawn on a6 is one square from promotion. It needs a7 and then a8=Q.
2. Calls Ka5 the **only drawing move** even though the stored engine gives Kc6, Ka5, and Kc5 all as 0.00; QUEEN's own alternative lines also end in bare-king draws after the pawn is lost.
3. After Ka5 Kb8 Kb6 Ka8, says ...Ka8 retreats to avoid "the check on b7." There is no such check.
4. Says White cannot push a7 because the black king on a8 controls it. A pawn may legally advance onto a controlled square, and White's king on b6 protects a7 from Kxa7.
5. In the Kb5 ...Ka7 Kc6 ...Kxa6 branch, says the pawn is captured **with check**. A black king on a6 does not attack a white king on c6.

The response is also internally awkward: it calls Ka5 the only drawing move, then describes other moves losing the pawn into a bare king-vs-king position—which is still a draw.

---

# Full-suite move-selection summary

Using the stored Stockfish MultiPV-3 only:

- Rank 1 match: **8/18**
- Rank 2: **6/18** (03, 07, 09, 11, 14, 18)
- Rank 3: **2/18** (06, 15)
- Outside MultiPV-3: **2/18** (10, 13)

Exact rank list from saved UCI comparison:
- #1: 01, 02, 04, 05, 08, 12, 16, 17
- #2: 03, 07, 09, 11, 14, 18
- #3: 06, 15
- outside top 3: 10, 13

Therefore the correct aggregate is **8 rank-1, 6 rank-2, 2 rank-3, 2 outside top-3**.

This should not be reported as objective chess accuracy; it is agreement with a fixed finite Stockfish reference search.

# What the completed benchmark means for Prophylax

The result is now much stronger than the original pilot:

1. **The generator/verifier split is justified.** QUEEN frequently proposes plausible or strong moves while making elementary factual errors in its explanations.
2. **The first semantic verifier milestone targets the right failure modes.** Attack geometry, blockers, file openness, passed-pawn status, promotion distance, material inventory, variation legality, capture identity, and terminal states directly address a large fraction of the observed hard errors.
3. **The missing layer is extraction.** `domain/verify.py` can judge typed facts, but raw QUEEN prose must still be converted into Fact/Ply candidates without "correcting" them first.
4. **UNKNOWN remains necessary.** Opposition narratives, trapped-piece mechanisms, only-move claims, prophylaxis, reroutes, initiative, and strategic causality cannot be considered verified merely because the underlying moves are legal.
5. **Critical-line legality is not enough.** All 18 structured critical lines were accepted as legal, yet the prose still contains numerous illegal side lines and false causal relationships.

# Recommended next benchmark step

Use these 18 stored outputs as the gold source for Milestone 2:

- hand-annotate concrete prose spans into expected `Fact` / `Ply` candidates;
- keep extraction status separate from verification status;
- measure extraction precision/recall separately from chess-truth verification;
- include false claims as positive extraction examples (e.g. `"bishop c5 attacks g5"` should extract successfully and then verify FALSE);
- leave unsupported strategy as UNSUPPORTED/AMBIGUOUS rather than forcing it into a fact type.

A strong end-to-end benchmark row should look like:

```text
QUEEN text -> expected typed claim -> extraction status -> verifier status -> evidence
```

The central research result is now defensible:

> **A chess-language model can select strong moves while producing explanations that are not factually faithful to the board. Prophylax can provide the deterministic verification layer, but only after an auditable prose-to-claim extraction step.**
