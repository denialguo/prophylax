# Possible false alarms: diagnostic only

The 31 FALSE verdicts from the current V2.1 hybrid with no confirming FALSE label on the 24 previously inspected held-out outputs. Classified mechanically from the labels. No label changed, and nothing here shows whether a verdict was right.

## By class

| Class | Count |
|---|---:|
| B: overlaps a TRUE fact on a different board | 2 |
| C: overlaps UNSUPPORTED text | 8 |
| D: overlaps AMBIGUOUS text | 1 |
| E: has no gold label | 4 |
| F: overlaps a gold concrete claim with a different predicate or args | 16 |

## By predicate

| Predicate | Count |
|---|---:|
| attacks | 7 |
| blocks | 3 |
| defends | 3 |
| is_open_diagonal | 2 |
| is_open_file | 1 |
| is_passed_pawn | 3 |
| material_balance_is | 6 |
| piece_at | 1 |
| piece_count_is | 3 |
| pinned_against | 1 |
| square_empty | 1 |

## By source

| Source | Count |
|---|---:|
| merged | 2 |
| rules | 8 |
| v1 | 6 |
| v2 | 15 |

## By family

| Family | Count |
|---|---:|
| attack | 6 |
| change | 8 |
| count | 3 |
| defend | 2 |
| material | 6 |
| other | 3 |
| passed pawn | 1 |
| pin | 1 |
| placement | 1 |

## By board

| Board | Count |
|---|---:|
| 1 moves in | 2 |
| 1 moves in, differs from label | 1 |
| 2 moves in | 5 |
| 3 moves in | 2 |
| 4 moves in | 5 |
| 4 moves in, differs from label | 1 |
| 6 moves in | 4 |
| 7 moves in | 1 |
| 8 moves in | 2 |
| root | 8 |

## By from a deterministic rule

| From a deterministic rule | Count |
|---|---:|
| False | 23 |
| True | 8 |

## The 6 material candidates

- `h01_tactical` F, rules: "White stands a full queen down" read as {'points': -9}. Verifier: White minus Black is -8 points.
- `h03_tactical` F, rules: "Black stands a full queen down" read as {'points': 9}. Verifier: White minus Black is 7 points.
- `h07_tactical` C, v2: "the material is equal" read as {'points': 0}. Verifier: White minus Black is 1 points.
- `h07_tactical` C, rules: "Black is up a queen" read as {'points': -9}. Verifier: White minus Black is -3 points.
- `h17_structure` C, rules: "White is up a full queen" read as {'points': 9}. Verifier: White minus Black is 4 points.
- `h23_endgame` F, rules: "White is down a queen" read as {'points': -9}. Verifier: White minus Black is -7 points.

## Every candidate

| Case | Class | Claim | Source | Board | Text |
|---|---|---|---|---|---|
| `h18_structure` | B | `attacks` {'source': 'c6', 'target': 'h3'} | v2 | 1 moves in, differs from label | The queen retreats to a safe square where it eyes the white pawn on h3 |
| `h19_structure` | B | `attacks` {'source': 'c7', 'target': 'e7', 'piece': 'rook'} | v2 | 4 moves in, differs from label | attacking the rook |
| `h02_tactical` | C | `piece_at` {'square': 'd2', 'color': 'white', 'piece': 'knight'} | v2 | 2 moves in | the white knight on d2 |
| `h07_tactical` | C | `material_balance_is` {'points': 0} | v2 | 4 moves in | the material is equal |
| `h07_tactical` | C | `material_balance_is` {'points': -9} | rules | 4 moves in | Black is up a queen |
| `h15_quiet` | C | `blocks` {'slider': 'b5', 'blocker': 'b7'} | v1 | 3 moves in | blocks the black pawn on b5 |
| `h15_quiet` | C | `blocks` {'slider': 'b7', 'blocker': 'b5'} | v2 | 3 moves in | blocks the black pawn on b5 |
| `h15_quiet` | C | `defends` {'source': 'd1', 'target': 'd4'} | v2 | 2 moves in | support the white pawn on d4 |
| `h16_quiet` | C | `is_open_diagonal` {'source': 'f4', 'target': 'c7'} | v2 | 4 moves in | the white bishop on f4 gains a clear diagonal |
| `h17_structure` | C | `material_balance_is` {'points': 9} | rules | 6 moves in | White is up a full queen |
| `h16_quiet` | D | `defends` {'source': 'e3', 'target': 'c4', 'piece': 'queen'} | v2 | 2 moves in | supporting the bishop |
| `h02_tactical` | E | `is_passed_pawn` {'square': 'd3'} | v2 | 6 moves in | pushing the passer |
| `h17_structure` | E | `attacks` {'source': 'c4', 'target': 'f7', 'piece': 'bishop'} | v1 | 1 moves in | threatening the black pawn on f7 |
| `h22_endgame` | E | `attacks` {'source': 'd1', 'target': 'e7', 'piece': 'rook'} | v1 | 1 moves in | attack the black pawn on e7 |
| `h23_endgame` | E | `is_open_file` {'file': 'e'} | merged | 2 moves in | opening the e-file |
| `h01_tactical` | F | `material_balance_is` {'points': -9} | rules | root | White stands a full queen down |
| `h03_tactical` | F | `material_balance_is` {'points': 9} | rules | root | Black stands a full queen down |
| `h05_tactical` | F | `defends` {'source': 'a7', 'target': 'b6'} | v2 | root | The black pawn on a7 is the only defender of b6 |
| `h12_quiet` | F | `square_empty` {'square': 'a2'} | v2 | 4 moves in | the white bishop on a2 has been removed from the board |
| `h15_quiet` | F | `blocks` {'slider': 'b7', 'blocker': 'b6'} | v2 | 6 moves in | the bishop on b7 is blocked by its own pawns |
| `h16_quiet` | F | `attacks` {'source': 'c7', 'target': 'c7', 'piece': 'bishop'} | v2 | 4 moves in | White plays white bishop f4-c7, attacking the queen |
| `h17_structure` | F | `is_open_diagonal` {'source': 'a2', 'target': 'g8'} | merged | 2 moves in | opens the a2-g8 |
| `h19_structure` | F | `pinned_against` {'pinner': 'b6', 'pinned': 'd6', 'target': 'd5'} | v2 | 7 moves in | pinning the black rook on d6 |
| `h22_endgame` | F | `is_passed_pawn` {'square': 'g5'} | v1 | root | The white pawn on g5 is a latent passer that restricts the black king on g7 |
| `h22_endgame` | F | `attacks` {'source': 'h5', 'target': 'g5', 'piece': 'pawn'} | v1 | 8 moves in | directly attacking the white pawn on g5 |
| `h22_endgame` | F | `attacks` {'source': 'h5', 'target': 'e7', 'piece': 'pawn'} | v1 | 8 moves in | attacking the black pawn on e7 |
| `h23_endgame` | F | `material_balance_is` {'points': -9} | rules | root | White is down a queen |
| `h23_endgame` | F | `is_passed_pawn` {'square': 'f4'} | v2 | 6 moves in | creating a dangerous passed pawn on the f-file |
| `h24_endgame` | F | `piece_count_is` {'color': 'white', 'piece': 'bishop', 'count': 2} | rules | root | two bishops and a knight |
| `h24_endgame` | F | `piece_count_is` {'color': 'white', 'piece': 'knight', 'count': 1} | rules | root | two bishops and a knight |
| `h24_endgame` | F | `piece_count_is` {'color': 'black', 'piece': 'pawn', 'count': 2} | rules | root | two pawns |
