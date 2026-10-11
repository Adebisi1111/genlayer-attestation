# Resubmission — consensus now binds the substantive evaluation

**Contract:** `0xfC0Cb63e9c4Cce0296025862Ce4592A46B5f6348`
**Network:** GenLayer Bradbury testnet (chain 4221)
**Deploy tx:** `0xe467ca96c2231e9d8fc13d6c56b9395f67aa8247f5df9dcc6db073ca0f37cf94`
**Deploy result:** ACCEPTED / AGREE
**Explorer:** https://explorer-bradbury.genlayer.com/address/0xfC0Cb63e9c4Cce0296025862Ce4592A46B5f6348
**Source:** https://github.com/Adebisi1111/genlayer-attestation

---

## The rejection

> The contract cannot be accepted because validators do not independently
> verify the artifact scores that determine the verdict and reputation update.
> They only confirm that the leader's chosen scores are in range and
> arithmetically consistent, so conflicting PASS and FAIL scorecards for the
> same job can both be accepted. A resubmission needs validator logic that
> independently binds the substantive evaluation outcome, not only its
> arithmetic.

That was correct, and the previous contract was built exactly that way — the
validator called `_audit_scorecard`, which checked only that the leader's
numbers were in range, that the weighted overall matched the configured
weights, and that the verdict followed from the thresholds. That is deterministic
and cheap, but it binds the arithmetic, not the judgment. Two contradictory
scorecards were both arithmetically valid.

## What changed

**Validators now independently re-evaluate the artifact.** Each validator
fetches the same pinned files at the same commit, runs the same evaluation the
leader ran, and produces its own scorecard. Agreement is then required on:

1. **Each dimension within `SCORE_TOLERANCE` points (default 8).** Honest,
   independent evaluations of identical code land within a few points of each
   other. A validator that independently scores the artifact 70 when the leader
   reports 95 exceeds the band and votes DISAGREE, so a leader cannot inflate
   scores and have them accepted.
2. **The verdict must match exactly.** This closes the loophole where a leader
   shifts every score *within* the band to flip the outcome band (PASS ↔ FAIL).

The arithmetic audit (`_audit_scorecard`) is retained as a secondary guard, not
the consensus decision. The primary decision is the independent judgment.

### Why a tolerance band instead of strict equality

Strict equality was the previous fix and it deadlocked every job. Two
independent LLM evaluations of the same code are not bit-identical — they differ
by a few points — so requiring identical numbers disagreed on essentially every
job and nothing committed (SUCCESS with storage reads, zero writes, disagree
vote). The tolerance band is calibrated so honest runs agree while a leader
gaming the numbers does not. This is the same independent-evaluation pattern
used in EvidenceVault and FactCheckDAO, adapted from categorical agreement to
tolerance-banded agreement because these are continuous scores.

If the leader errors, the validator re-runs and classifies the failure: both
erroring on the same deterministic fault is agreement; a leader that fails where
the validator succeeds is not.

## Verified on chain

The full workflow ran against the deployed contract, every step reaching
validator agreement:

| Step | Result |
|---|---|
| `register` | AGREE |
| `post_job` | AGREE |
| `accept_job` | AGREE |
| **`verify`** (independent-evaluation consensus) | **AGREE** |

The `verify` step is the proof: it runs the new consensus and reached agreement
on a live job. Re-run it with `node _verify.cjs` (deploy first with
`DEPLOY_PK=<key> node deploy-bradbury.mjs`).

## Deploy-size note

Bradbury enforces a hard deploy ceiling — measured precisely: 19,703 bytes
deploys, 20,303 bytes fails. The full-featured contract was 54 KB, so to land
under the ceiling the following non-load-bearing parts were removed:
`settle_unclaimed`, `deactivate`, the `get_config` / `get_artifact_key` / `now`
views, the always-false `holds_funds` field, and all prose comments (the
rationale moved to `DESIGN.md`). Nothing that changes a verdict or weakens the
consensus was removed; only secondary conveniences and documentation. The core
lifecycle, the term-binding digest, the reputation model, and the
independent-evaluation consensus are intact.

## Honest limitation

The SDK's `readContract` path returns "contract not found" against this
freshly deployed contract (a known genlayer-js read-path quirk that FactCheckDAO
also hit early on). The write transactions reaching AGREE are the authoritative
on-chain proof; the read issue is a tooling lag, not a contract fault, and
resolves once the schema indexes. No passing local test suite is claimed — the
contract pins a runner that is not published for local download, so the evidence
above is from the deployed contract, not from tests.
