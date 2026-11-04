# QUEEN extraction: blind held-out evaluation

## Frozen system

- Frozen commit: `046bb19`
- Extractor commit: `046bb1996c44060bfb0a1edb22f00420303a1143`
- Verifier commit: `b2ac11c1e578ddf065ff8ee8e89739db5dc6ff86`
- Semantic-code override: none
- python-chess: 1.11.2

## Sets (never combined)

- **Development/tuning set:** 18 positions, 180 labels. The grammar was written while reading these, so its numbers are optimistic.
- **Held-out evaluation set:** 24 positions: 8 tactical, 8 quiet, 4 structure, 4 endgame; 12 from real games, 12 from self-play.
- Complete suite: True; missing: []

## QUEEN

- Model: princeton-nlp/queen_hce-4
- Settings: {"max_tokens": 2048, "temperature": 0.6, "seed": 20260823}
- Revisions: Space 9a5483ce0fb50b437fc9f4570e73eb034bb14603 / model a98e8e4e8959090bd772fa82bb64414f7810f35e

### Raw output hashes

| Case | Category | Provenance | Explanation SHA-256 | Prediction SHA-256 |
|---|---|---|---|---|
| h01_tactical | tactical | Stockfish self-play, seed 20261103 (suite_v1 h01_tactical) | `f7d98ad0662076e25ddbc52dd72e37ab35ddcebdd08966ca70b0f276ebc4cbc4` | `a9300f34565850d55592fc903753546c3aa217f54ef3d7db272ce8cc0e30dd74` |
| h02_tactical | tactical | Stockfish self-play, seed 20261103 (suite_v1 h02_tactical) | `e383928926505cd3aa7a422d5e78ba2fcc016c154048c9268221d4a018726f0d` | `0ab49f9737545647e0cbcb387e2aeb735e659233d0adf6dce9d12e5f23d4f306` |
| h03_tactical | tactical | Stockfish self-play, seed 20261103 (suite_v1 h03_tactical) | `28a06931b91f25f213c3486411265b70f98c9cface8447553bb478d889386a04` | `409f4b5120a282ea4f3c0e0bf227367cf27ccc2eca4975cd1165bc62349f7de2` |
| h04_tactical | tactical | Stockfish self-play, seed 20261103 (suite_v1 h04_tactical) | `ff3a0d7fa5a5cbb0400bc61e98d686398fc53f629552e8d3b4f4b812ad93036f` | `ec25153816d994aadf3ccd0000552571804d8a8ab98c2f33a35ede69f837e67d` |
| h05_tactical | tactical | player history, game 69cd2c27, ply 29 | `ae88a52ec057c72b601f3e26baa64817a67bf137b3b01745f09feae79dd568e8` | `4661b77c7a7ce68f1d71ae86956340b1c5057a33378d0596f0f4ef5bb3a5d311` |
| h06_tactical | tactical | player history, game 42293fcd, ply 29 | `a1f9fe401ef799eae8aeb133397f5419c60080f466aa5bc89597135d921f885d` | `bba228dffb05bd227350a58662c42d1740b2d7518cda3b6d36cd84cba44faec4` |
| h07_tactical | tactical | player history, game 376220f4, ply 26 | `4720850b63991b49434aaf09269bedb22727b621ee8751213dabe0b171237195` | `594ab71b61a446024724e75894c2915974a9058862bf94c0316e13bc9e7c5d50` |
| h08_tactical | tactical | player history, game 44aa6c89, ply 55 | `1db8c557e09b82bd4306d87f07c925063e950ab91864101854ccbaacd2a07fc8` | `30e400343faccebf3561d9e62bf603ea001682ea88361cde7519fb7b71968207` |
| h09_quiet | quiet | Stockfish self-play, seed 20261103 (suite_v1 h09_quiet) | `9e541319cd75aa1a79606fd61cfac8ce853072281e153bdc7b3abf6af2826a49` | `cae3cd7dc0f52d9bbf58a344485554cf50a9d51ac71230712fb585f9d0e76063` |
| h10_quiet | quiet | Stockfish self-play, seed 20261103 (suite_v1 h10_quiet) | `8d1b4810b6f3470d036a701a27aaadfbe419cefa698cab8881ed8e2080a621a3` | `ea1dca9057836211577bf57442864c522d9ae15bc51938b0381ceff5fa787b52` |
| h11_quiet | quiet | Stockfish self-play, seed 20261103 (suite_v1 h11_quiet) | `c895ce1d6a4155ee53f8cfc91ce59a9ada0197abf000fd011e5387ff29bcc299` | `8aeb08e857a7107f4b3d728ca89920190b3834d4136968cb275f6ed13c46d374` |
| h12_quiet | quiet | Stockfish self-play, seed 20261103 (suite_v1 h12_quiet) | `9168a92c6829a1c48e9e35609813a66b75d7f9b5b4275ab2c4fd817976fbb49f` | `34740eb0a8a8d93aecfedee9900e5a50fe596ce42deb3f4a07c139732d82b080` |
| h13_quiet | quiet | player history, game d17fef9d, ply 18 | `0bc9ef6035deeb9a51f43e7fa6fa01196c802c38dd93e5994ddf7562f13f9be2` | `b10070fb0a05ba6c2c297f4552bbc4f31ee839490bd051ba9caa2b92613aaf01` |
| h14_quiet | quiet | player history, game b1134b4c, ply 23 | `adbeea9ea9ec69b93a4d2ea096ad91f01253920e27b584aa44a55a7fd0166de4` | `31a1af42a49b02cbac51a5fb93b6c6ba8dba2175f7d7a942f6e5a6eb8e7812a7` |
| h15_quiet | quiet | player history, game 8347b044, ply 19 | `446d0df23581b92b0ccf4f3d35f4fc46e83049aa65ce014c5daf2df4ef9ad31f` | `331fc85d002d90cc76c9e93cf6d6ee9ea2b129ccc572522776bf683ed41dd612` |
| h16_quiet | quiet | player history, game 979bc460, ply 19 | `0c1369631cc95cbf6ef19e7804e1c570ab32c13a1d44530200da6c5180bff8f5` | `8a491e06fd850d10892bc023fc2f560c7d5f1be50146ceb9ed004b1fe6a987d8` |
| h17_structure | structure | Stockfish self-play, seed 20261103 (suite_v1 h17_structure) | `9f314ddeb67f7c80d995e4274628fc4088a9abda8b6876f0154a885f9818eb51` | `0d1366e6f0ae1276c28fe269a057b77a9ed98217434adeb65e77b798c7f81ca2` |
| h18_structure | structure | Stockfish self-play, seed 20261103 (suite_v1 h18_structure) | `e0ee28db5419cdcb3ddd02d5544fe21f76251ff1d088ad972123051668f1fd8f` | `2123480b9477c69d11f2ac4e378672bff95e68a42671d0a9a05db82ed9480e56` |
| h19_structure | structure | player history, game 6681951e, ply 54 | `bd1aeeaef98a18d0674d384b68dfa29d3b9eb6f34fd0f5ca9601b72b2aa9c4e7` | `a04ae6538015ac5c097e7052a41c40c18f491b7f743a3ccf12258f647d672e6b` |
| h20_structure | structure | player history, game 73a67a2b, ply 32 | `303a3bbef6623ba3345cd9510d57ddfaa24823fa4a2f27bc8e3b1ad3d7c9dbdf` | `46c27e7fb7e8630f87b9be4449da6a8d84ac4acfdc16601d31c71824e5733dfe` |
| h21_endgame | endgame | Stockfish self-play, seed 20261103 (suite_v1 h21_endgame) | `88d8f3a784c47f6a5a3eca6a6b14af7fab8efefccabdeb3b690c4ab074a8cd33` | `796145c859a9d269ab301dad3a0745143e734c08d48b74e04754645c5ddc603f` |
| h22_endgame | endgame | Stockfish self-play, seed 20261103 (suite_v1 h22_endgame) | `78c1b4791e76ef396dee0d622d2f1f0086d9df62b1be91820604ac250cace342` | `b03b3d77a48f39d857cad42dba1c2ab571103c906f8101b7e80d39f44b6855dc` |
| h23_endgame | endgame | player history, game 65b3f8f2, ply 76 | `529eaaf6321a96362cb7a55b3e03ea40a77a31f5242a07b65501b2e960d34e71` | `a65d3372ff6a44a86f37fa44f0b5836c6beb890ebd1a1f8d1c976c788c00ca3e` |
| h24_endgame | endgame | player history, game 6073a573, ply 76 | `e3510165df6ea9868fd4c5c619e17a63b72a1554cf8021c42aeb3280704138af` | `af0da3d7b2c94bdc3437ef700c4fcfd6bcb23ecffd53f36426f485c9e28edcf0` |

## Labels

- Labels: 1142; corrections logged: 0
- Labeller: OpenAI GPT-6 (blind primary); independently audited by OpenAI Codex; adjudicated from blind artifacts; independent of the grammar's author: True
- Predictions opened before labels froze: False

## Development vs held-out

Rates on different sets, side by side. The development column uses the audited labels.

| Metric | Development/tuning set | Held-out evaluation set |
|---|---|---|
| Exact predicate + args | 83 / 106 | 20 / 468 |
| Correct board, of those | 83 / 83 | 18 / 20 |
| Unsupported detected | 37 / 38 | 387 / 498 |
| Ambiguous detected | 4 / 4 | 0 / 1 |
| Variations exact | 45 / 48 | 143 / 175 |
| False VERIFIED | 0 | 0 |

## Extraction

| Metric | Value |
|---|---|
| Exact predicate | 4.7% |
| Exact args, given predicate | 90.9% |
| Exact predicate + args | 4.3% |
| Correct board, given exact | 90.0% |
| Unsupported classification | 77.7% |
| Ambiguous classification | 0.0% |
| Missed extractable claims | 448 |
| Extractions on labelled non-facts | 78 |
| Extractions matching no label | 7 |
| Negative claims labelled | 6 |
| Negative claims missed (included in missed) | 6 |
| Positive candidate on a negated claim | 0 |
| Variation exact-match rate | 81.7% |

## Verification (on correctly extracted claims)

| Metric | Value |
|---|---|
| VERIFIED | 13 |
| FALSE | 5 |
| UNKNOWN | 0 |
| Verdict accuracy | 100.0% |
| False VERIFIED | 0 |
| False verification acceptance rate | 0.0% |

## End-to-end error capture

Of all false concrete QUEEN claims in the labels, including those no predicate can represent. A verifier cannot catch what the extractor never emits.

| Outcome | Facts | Variations |
|---|---|---|
| All false | 92 | 27 |
| Caught as FALSE | 5 | 18 |
| Became UNKNOWN | 0 | 6 |
| Missed by extraction | 86 | 3 |
| Incorrectly VERIFIED | 0 | 0 |
| **Capture rate** | **5.4%** | **66.7%** |
| Capture rate, representable claims only | 5.8% | n/a |

False claims outside every predicate: 6. FALSE on the wrong board, not counted as caught: 1.

## Verified-claim precision

The critical safety metric: of everything the system marks VERIFIED, how much is true.

| | Facts | Variations |
|---|---|---|
| Marked VERIFIED | 52 | 140 |
| Actually true | 13 | 125 |
| **Actually false** | **38** | **0** |
| Not covered by a label (counted against precision) | 1 | 15 |
| Precision | 25.0% | 89.3% |

## Failure taxonomy

| Failure | Layer | Count |
|---|---|---|
| extractable_claim_missed | extraction | 440 |
| hallucinated_on_non_fact | extraction | 78 |
| unsupported_misclassified | extraction | 33 |
| variation_missed | extraction | 32 |
| variation_with_no_label | extraction | 31 |
| extraction_with_no_label | extraction | 7 |
| negative_claim_missed | extraction | 6 |
| board_binding_wrong | extraction | 2 |
| predicate_right_args_wrong | extraction | 2 |
| ambiguous_misclassified | extraction | 1 |

### Known issues left unfixed

| Issue | Labels affected |
|---|---|
| none tagged | |

### What it would take to address the misses

From `miss_review.jsonl`, written after scoring.

- Deterministically addressable: _pending_
- Needs a new chess predicate: _pending_
- Fundamentally ambiguous language: _pending_

## Examples

### False VERIFIED (highest severity)

- none
- none

### Missed extractable claims

- `h01_tactical` «a full queen down» {"class": "EXTRACTED", "label_id": "l0002", "want_args": {"color": "white", "count": 0, "piece": "queen"}, "want_branch": [], "want_chess_truth": "TRUE", "want_polarity": "positive", "want_predicate": "piece_count_is"}
- `h01_tactical` «Black's black queen on d4» {"class": "EXTRACTED", "label_id": "l0003", "want_args": {"color": "black", "piece": "queen", "square": "d4"}, "want_branch": [], "want_chess_truth": "TRUE", "want_polarity": "positive", "want_predicate": "piece_at"}
- `h01_tactical` «black pawn on e4» {"class": "EXTRACTED", "label_id": "l0005", "want_args": {"color": "black", "piece": "pawn", "square": "e4"}, "want_branch": [], "want_chess_truth": "TRUE", "want_polarity": "positive", "want_predicate": "piece_at"}
- `h01_tactical` «black pawn on e4 forming a dangerous central passer» {"class": "EXTRACTED", "label_id": "l0006", "want_args": {"square": "e4"}, "want_branch": [], "want_chess_truth": "FALSE", "want_polarity": "positive", "want_predicate": "is_passed_pawn"}
- `h01_tactical` «the queen sits en prise to the white pawn on c3» {"class": "EXTRACTED", "label_id": "l0008", "want_args": {"piece": "pawn", "source": "c3", "target": "d4"}, "want_branch": [], "want_chess_truth": "TRUE", "want_polarity": "positive", "want_predicate": "attacks"}

### Wrong board

- `h01_tactical` «It opens the d-file» {"class": "EXTRACTED", "got": {"args": {"file": "d"}, "board_moves": ["c3d4", "a8d8", "d4d5", "e4d3"], "predicate": "is_open_file", "status": "EXTRACTED", "verifier_status": "FALSE"}, "label_id": "l0033", "want_args": {"file": "d"}, "want_branch": ["c3d4", "a8d8", "d4d5"], "want_chess_truth": "FALSE", "want_polarity": "positive", "want_predicate": "is_open_file"}
- `h04_tactical` «open c-file» {"class": "EXTRACTED", "got": {"args": {"file": "c"}, "board_moves": ["b2a2", "c3a2", "c2a2", "f8c8", "f3d4"], "predicate": "is_open_file", "status": "EXTRACTED", "verifier_status": "VERIFIED"}, "label_id": "l0155", "want_args": {"file": "c"}, "want_branch": ["b2a2", "c3a2", "c2a2", "f8c8"], "want_chess_truth": "TRUE", "want_polarity": "positive", "want_predicate": "is_open_file"}

### Extractions matching no label

- `h06_tactical` «simultaneously attacking the white pawn on d4» {"args": {"piece": "knight", "source": "d2", "target": "d4"}, "predicate": "attacks", "verifier": "FALSE"}
- `h07_tactical` «attacking the white bishop on f1» {"args": {"piece": "rook", "source": "h1", "target": "f1"}, "predicate": "attacks", "verifier": "VERIFIED"}
- `h17_structure` «threatening the black pawn on f7» {"args": {"piece": "bishop", "source": "c4", "target": "f7"}, "predicate": "attacks", "verifier": "FALSE"}
- `h17_structure` «opens the a2-g8» {"args": {"source": "a2", "target": "g8"}, "predicate": "is_open_diagonal", "verifier": "FALSE"}
- `h22_endgame` «attack the black pawn on e7» {"args": {"piece": "rook", "source": "d1", "target": "e7"}, "predicate": "attacks", "verifier": "FALSE"}

### Successful generalization

_Analyst: quote correct extraction + FALSE and + VERIFIED from the predictions._

## Claims that stay unsupported

_Analyst: list from the UNSUPPORTED labels by category._

## Limitations and contamination risks

- The grammar's author chose the rules that selected the suite, though not the positions themselves.
- If the labeller also wrote the grammar, labels may lean toward what it can read. The development labels did: an audit moved 12 AMBIGUOUS and 3 UNSUPPORTED fact labels and 2 AMBIGUOUS variation labels to extractable, and exact fact matches there fell from 83/83 to 83/106.
- Half the suite comes from real games and half from self-play. The self-play half came from v1 of the suite, which is also where the only outputs obtained before v2 (h01, h02) came from. Those two were not read before predictions froze.
- All outputs come from one model revision, one seed and one template. Results do not cover other models, prompts or formats.
- One labeller and no inter-rater measure, unless the report says otherwise.
- The development labels treat negative claims as UNSUPPORTED; the held-out labels treat them as facts with negative polarity, which count as missed. Held-out fact recall can therefore be lower for that reason alone, and the development column was left unchanged.
- Self-play positions at 50k nodes may differ in style from the positions QUEEN was trained on.
