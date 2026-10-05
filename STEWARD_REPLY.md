# Steward reply

*(Drafted, not yet sent. The address lines below are filled in from real deploys
on Studio Dev 61997.)*

```
Contract:     0xEc1cD00fefb4Dd2861d1CF426126a43b14ee3BD6
Lifecycle tx: 0x8548ff90e67bb0e4346b51a4c9aecadb3220c3c235f5132ce4a7731fac10ebc3
Network:      GenLayer Studio Dev / Next 61997
```

---

You were right, and the gap was larger than the review stated.

**A GenLayer Intelligent Contract cannot move native funds.** `emit_transfer`
exists in the stdlib, but only on external contract proxies — the `Contract`
base class an IC inherits from does not expose it. I verified this by probing
the runtime rather than inferring it from documentation:

```
self.emit_transfer=False  self.transfer=False  self.send=False
self.withdraw=False       self.burn=False       self.emit=False
gl.transfer=False         gl.send=False         gl.emit_transfer=False
vm.transfer=False         vm.send=False         vm.emit_transfer=False
```

Worse: the deployed contract's native balance was **zero** while its ledger
recorded deposits. So `claim_withdraw` and `dispose_slashed` were not failing to
settle a debt — there was never any custody to settle against.

**So the custody claim is removed rather than explained.**

| Removed | Replaced by |
|---|---|
| `register()` was payable | not payable; reputation starts at the floor |
| `staked`, `min_stake` | `reputation`, `min_reputation` |
| `request_withdraw` / `claim_withdraw` | nothing to withdraw |
| `dispose_slashed`, `slashed_sink` | `demotions`, `demoted_total` |
| stake burn on FAIL | proportional reputation reduction |

This contract holds no funds and owes none. Every agent-facing view returns
`holds_funds: false` explicitly, so no reader has to infer it.

**Enforcement is reputational.** `get_standing` reports eligibility and
`get_config` exposes the thresholds, both readable before an issuer posts work.
Losing standing means fewer issuers choose you — a real cost, enforced without
custody the chain cannot honour.

**The consensus design is unchanged in shape** — four dimensions, one round,
and no state moves without validator agreement. But the mechanism had to change,
and this is the part I got wrong twice before getting right.

Validators no longer re-run the model. Two independent LLM runs never return
identical numbers, so requiring agreement on all six values made every job
deadlock: `verify()` returned SUCCESS, 141 storage reads, **zero** writes, a
published scorecard that read back as missing, and reputation unchanged.

Validators now audit the leader's scorecard deterministically — scores in range,
`overall` exactly equal to the weighted value the configured weights produce,
and the verdict the thresholds imply. Honest validators always agree; a leader
that inflates a score or mislabels a verdict still gets rejected. The
arithmetic is independently checkable from published values, and was:
`95*40 + 96*25 + 91*25 + 95*10 = 9425 // 100 = 94`.

## Three defects the rewrite introduced, and caught

Worth stating plainly, because they were all mine:

1. **One failure ended a career.** A fixed percentage off a score-scale
   reputation dropped every agent below the floor immediately. Demotion is now
   proportional, and only `max_demotions` failures make an agent inactive.

2. **Recovery was impossible.** The acceptance gate blocked agents below the
   floor — including the good work that would have restored them. A
   proportional penalty had become a life sentence. The gate is gone; an agent
   that loses standing can still accept, and success wins it back.

3. **Tiers measured wealth.** Keyed off staked GEN, and then off a reputation
   total with a 3× multiplier, which made `TRUSTED` unreachable for ~24 good
   jobs. Tiers now derive from the track record: jobs completed and average
   score.

All three were caught by tests asserting the new behaviour, not by reading the
code.

**Tests: 35 passing.** No Studio Net required. The withdrawn contract's address
is not carried over — this is a different contract with a different API.

Contract: `_TO BE FILLED_`
Source: https://github.com/Adebisi1111/attestation
Deployed source matches commit: `_TO BE FILLED_`