# The unsupported pawn question

*Prophylax · open design question*

How should a chess-coaching engine describe a pawn that can never again be protected by another pawn? We'd like input from strong players and from other AI reviewers.

## Background

Prophylax is a positional chess coach. Stockfish does all the calculation. A small layer of fixed rules then extracts positional facts from each move (weak squares, backward pawns, king safety), and a language model explains the mistake. The model may only say things those facts support, so every fact must be something a program can check exactly.

These are the definitions in use today:

- **Hole:** a square in your own half (ranks 3–4 for White, 5–6 for Black) that no friendly pawn can ever guard again, because the pawns that could have guarded it have advanced past it or are gone.
- **Squares a pawn move gives up:** the holes a move creates are reported only if they are
  - (a) squares the moved pawn guarded directly before it moved, or
  - (b) farther holes that an enemy pawn already attacks (an *outpost*).
- **Backward pawn:** no friendly pawn behind it on an adjacent file, and the square in front of it is attacked by more enemy pawns than friendly pawns.

Some quiet moves get flagged as positional mistakes even though the engine's evaluation barely changes. A move is flagged this way only if it creates **both** a new hole and a new backward pawn.

## The question

Sometimes a pawn move leaves a square that no friendly pawn can ever guard, but the square has one of your own pawns on it. We agree that isn't a hole or an outpost: no enemy piece can settle there while the pawn stands on it. Still, the pawn on that square has permanently lost pawn protection, which often matters.

> **How should this be classified?** It's sometimes a real weakness and sometimes irrelevant, and a fixed rule can't see the dynamics that decide which.

## Two examples

### Scandinavian, 13.b4: the c3 pawn

```
1. e4 d5 2. Nc3 d4 3. Nce2 e5 4. d3 Nc6 5. Ng3 Nf6 6. Nf3 Bg4 7. Be2 Bxf3
8. Bxf3 Bd6 9. O-O O-O 10. Bg5 h6 11. Bd2 Re8 12. c3 Ne7 13. b4
```

FEN after 13.b4: `r2qr1k1/ppp1npp1/3b1n1p/4p3/1P1pP3/2PP1BN1/P2B1PPP/R2Q1RK1 b - - 0 13`
([Lichess](https://lichess.org/analysis/r2qr1k1/ppp1npp1/3b1n1p/4p3/1P1pP3/2PP1BN1/P2B1PPP/R2Q1RK1_b_-_-_0_13))

| Fact | Value |
|---|---|
| Backward? | No |
| Attacked by | Black's d4 pawn |
| Defended by | Bishop on d2 (no pawn can ever defend it again) |
| Enemy pawn on the c-file? | Yes (c7) |

### Carlsbad, 10...b5: the c6 pawn

```
1. d4 d5 2. c4 e6 3. Nc3 Nf6 4. cxd5 exd5 5. Bg5 c6 6. e3 Be7 7. Bd3 O-O
8. Qc2 Nbd7 9. Nge2 Re8 10. O-O b5
```

FEN after 10...b5: `r1bqr1k1/p2nbppp/2p2n2/1p1p2B1/3P4/2NBP3/PPQ1NPPP/R4RK1 w - - 0 11`
([Lichess](https://lichess.org/analysis/r1bqr1k1/p2nbppp/2p2n2/1p1p2B1/3P4/2NBP3/PPQ1NPPP/R4RK1_w_-_-_0_11))

| Fact | Value |
|---|---|
| Backward? | Yes (already flagged) |
| Attacked by | Nothing yet |
| Defended by | Nothing |
| Enemy pawn on the c-file? | No (the file is half-open for White) |

In the Carlsbad case, the idea that c6 is a target on a half-open file is already captured by the backward-pawn flag. In the Scandinavian case nothing flags c3. It is attacked by a pawn, but it isn't backward, and a piece defends it.

## Options on the table

| Option | What gets reported | Trade-off |
|---|---|---|
| **A. Ignore it** (current code) | Nothing, unless the backward-pawn rule fires. | Simplest. Misses cases like c3 above. |
| **B. Count it as a hole** (previous code) | `c3` listed as a weak square. | Wrong: a square holding your own pawn can't be an outpost, so the coach would describe it badly. |
| **C. Neutral fact** | A separate list, e.g. `new_unsupported_pawns: [c3]`, meaning this pawn can never again be defended by a pawn. | Always true and checkable. Makes no claim about whether it matters; the engine's evaluation decides that. Doesn't change which moves get flagged. |
| **D. Conditional weakness** | Flag it as weak only if extra conditions hold, e.g. attacked by an enemy pawn, on a half-open file, or attacked by more pieces than defend it. | Closer to how players think. The conditions are guesses, and rules like "attacked more than defended" fall apart once exchanges and tactics come in. |

## What we'd like reviewers to weigh in on

1. Is "can never be defended by a pawn again" a meaningful signal on its own, or only in combination with something else?
2. If a condition should be added (option D), which one holds up across many positions? Candidates:
   - attacked by an enemy pawn
   - on a file with no enemy pawn
   - blockaded
   - on the same colour as the side's remaining bishop
3. Should the classification differ by game phase? For example, is an unsupported pawn a bigger deal in an endgame?
4. Is there standard terminology we should use? "Weakened pawn", "isolated from pawn support" and "pawn weakness" all appear in the literature with different meanings.
5. Are there well-known positions where one of these rules gives a clearly wrong answer? Please share a PGN or FEN.

## Constraints any answer must respect

- The rule must use only the board (piece positions and attacks). No search and no evaluation.
- It must behave identically for White and Black.
- Whether it *mattered* in the game is judged by the engine's win-probability change. The rule only states what is true.
- Adding a signal must not change which moves are flagged unless that is intended and tested.
