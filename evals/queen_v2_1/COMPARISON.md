# V1 vs V2 vs V2.1 hybrid: post-hoc evaluation on previously inspected held-out set

**This is a post-hoc evaluation on previously inspected held-out set.** These outputs and labels shaped this design, so the numbers do not measure generalization.

| Metric | V1 | V2 | V2.1 hybrid |
|---|---:|---:|---:|
| Exact predicate + args | 91/468 = 19.4% | 204/468 = 43.6% | 245/468 = 52.4% |
| Correct board, given exact | 81/91 = 89.0% | 198/204 = 97.1% | 232/245 = 94.7% |
| Unsupported recall | 438/498 = 88.0% | 293/498 = 58.8% | 421/498 = 84.5% |
| Ambiguous recall | 0/1 = 0.0% | 0/1 = 0.0% | 0/1 = 0.0% |
| Verdict accuracy on correct reads | 81/81 = 100.0% | 198/198 = 100.0% | 232/232 = 100.0% |
| False-claim capture | 32/92 = 34.8% | 32/92 = 34.8% | 45/92 = 48.9% |
| VERIFIED fact precision | 45/52 = 86.5% | 166/224 = 74.1% | 182/244 = 74.6% |
| Variation exact match | 143/175 = 81.7% | 143/175 = 81.7% | 143/175 = 81.7% |
| False variation capture | 18/27 = 66.7% | 18/27 = 66.7% | 18/27 = 66.7% |
| False VERIFIED on correct reads | 0 | 0 | 0 |
| False claims VERIFIED, including stray candidates | 0 | 1 | 0 |
| False variations VERIFIED | 0 | 0 | 0 |
| Extractions on labelled non-facts | 10 | 46 | 54 |
| Extractions matching no label | 7 | 16 | 23 |

## False claims caught

V1 32, V2 32, measured hybrid 45. V1 and V2 share 18. Union of V1 and V2: 46, which is not a system result.

In that union but lost by the hybrid: ['l0537', 'l0570']. Caught only by the hybrid: ['l1029'].

## FALSE verdicts no label confirms

No metric above counts these. Each is a possible false alarm: the verifier called a reading false, but no FALSE label agrees.

| | V1 | V2 | V2.1 hybrid |
|---|---:|---:|---:|
| On a labelled non-fact | 4 | 14 | 19 |
| Of those, material rules | 0 | 1 | 5 |
| On a TRUE fact, other board | 0 | 1 | 2 |
| With no label | 5 | 4 | 10 |
