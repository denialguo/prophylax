# Extractor V2 vs V1: post-hoc evaluation on previously inspected held-out set

**This is a post-hoc evaluation on previously inspected held-out set.** These 24 outputs and their labels were read before V2 existed, so these numbers do not measure generalization. Any such claim needs a fresh blind benchmark.

Model: `gemini/gemini-3.1-flash-lite`, prompt `v2-5`, temperature 0, 52 recorded answers. Frozen V1 artifacts were only read.

| Metric | V1 (frozen) | V2 |
|---|---:|---:|
| Exact predicate + args | 91/468 = 19.4% | 204/468 = 43.6% |
| Correct board, given exact | 81/91 = 89.0% | 198/204 = 97.1% |
| Unsupported recall | 438/498 = 88.0% | 293/498 = 58.8% |
| Ambiguous recall | 0/1 = 0.0% | 0/1 = 0.0% |
| Verdict accuracy on correct reads | 81/81 = 100.0% | 198/198 = 100.0% |
| False-claim capture | 32/92 = 34.8% | 32/92 = 34.8% |
| VERIFIED fact precision | 45/52 = 86.5% | 166/224 = 74.1% |
| Variation exact match | 143/175 = 81.7% | 143/175 = 81.7% |
| False variation capture | 18/27 = 66.7% | 18/27 = 66.7% |
| False VERIFIED on correct reads | 0 | 0 |
| False claims VERIFIED end to end, including stray candidates | 0 | 1 |
| False variations VERIFIED | 0 | 0 |
| Extractions on labelled non-facts | 10 | 46 |
| Extractions matching no label | 7 | 16 |

Negative claims read: 0/6. V1 could not represent them. Candidates with the opposite polarity from their label: 0.

## Reading these numbers

- **False-claim capture matches by coincidence.** V1 caught 32, V2 caught 32, and only 18 are the same claims. Together they cover 46/92, which is a set union and not a measured system.
- **Precision, sensitivity only.** The primary figure stays 166/224 = 74.1%. Of the VERIFIED facts it counts against V2, 38 are piece_at mentions inside broad non-fact labels with no child fact. Leaving those out gives 166/186 = 89.2%. This view was chosen after seeing the result.
- **Unsupported recall.** 54 undetected parents hold only extractions matched to child facts. 163 of V2's UNSUPPORTED candidates are sentences the model skipped, marked residual, not its own reading.

