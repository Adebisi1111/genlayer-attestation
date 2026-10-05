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

## And three more that only the live contract exposed

The rewrite above was not the last of it. Deploying it surfaced failures that
no local test would have caught, because the local suite cannot run against the
2.x runner this contract pins.

4. **`hash()` is salted per process.** The terms digest was built on Python's
   builtin `hash()`, which returns a different value in every transaction.
   `accept_job` stored one, `verify` recomputed another, and every verification
   died with "Job terms changed after acceptance." Now `Keccak256`.

5. **The validator re-ran the LLM and deadlocked.** Requiring two independent
   model runs to agree on all six values meant agreement almost never happened.
   `verify()` reported SUCCESS with 141 storage reads and **zero** writes, a
   scorecard that read back as missing, and reputation unchanged. Validators now
   audit the leader's arithmetic deterministically instead.

6. **The audit then required a field the leader never returns** — the evidence
   hash is derived after consensus, so the validator's calldata has no such key.
   It raised, returned false, and deadlocked identically.

Each was found by reading the leader receipt instead of trusting the success
message. `docs/CONSENSUS.md` records the method, including the two fields that
tell you whether a commit actually happened: `STORAGE_WRITE` in the leader's
`call_counts`, and the validator's `vote`.

One receipt detail that looks like a failure and is not: the validator reports
`execution_result: ERROR` with `CONSENSUS_VALIDATOR_QUORUM_REACHED`. That is a
validator being cancelled *because quorum was already reached*. The round
committed.

## Evidence

**I am not claiming a passing test suite.** The contract pins the 2.x runner
that actually executes on 61997, and that runner is not published for local
download — the newest GenVM build ships only two `py-genlayer` runners and this
is neither. The suite passed against the earlier 1.x draft and has not been
re-run since the rewrite. There is no local workaround, so rather than imply
otherwise, here is what *is* verified against the deployed contract:

- **Full lifecycle, 9/9**, real signed transactions: `register -> post_job ->
  accept_job -> verify -> published scorecard -> reputation credit`.
- **Seven adversarial paths**, all rejected by the deployed contract: duplicate
  `job_id`, the same `(repo, commit)` re-reviewed, `verify` without acceptance,
  `settle_unclaimed` before the deadline, `verify` after `decline_job`, and
  double `verify`. Plus: scores are clamped to 0-100 before consensus, so the
  audit cannot be gamed by inflating raw output, and only
  `raw.githubusercontent.com` is ever fetched, so a posted URL cannot reach
  anything else.
- **One real repository at a real commit**, scored `95 / 96 / 91 / 95`, overall
  `94`, verdict `PASS`. Both derived figures were recomputed independently and
  match: `95*40 + 96*25 + 91*25 + 95*10 = 9425 // 100 = 94`, and `300 + 94 = 394`.

All three scripts are in the repository and re-runnable.

```
Contract:              0xEc1cD00fefb4Dd2861d1CF426126a43b14ee3BD6
Network:               GenLayer Studio Dev / Next 61997
Explorer:              https://explorer-studio-dev.genlayer.com/address/0xEc1cD00fefb4Dd2861d1CF426126a43b14ee3BD6
Lifecycle tx:          0x8548ff90e67bb0e4346b51a4c9aecadb3220c3c235f5132ce4a7731fac10ebc3
Source:                https://github.com/Adebisi1111/genlayer-attestation
Commit:                c33da14c6457bacc2a7917924f91ea47e166516d
contracts/attestation.py: 50623 bytes, sha256 6d26ed6ee2f0278e60d609ac52af9dde70d81d2a72e04f27f3228e7f54f8e63f
```

The withdrawn contract's address is not carried over — this is a different
contract with a different API, and it holds no funds.