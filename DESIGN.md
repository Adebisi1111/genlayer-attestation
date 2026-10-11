# DESIGN.md — Attestation

**Verifiable Intelligence for Trusted Yardsticks**

Non-custodial, reputation-gated verification of agent work deliverables on GenLayer.
This document records the design rationale, the consensus model, and every
trade-off made to land the contract on Bradbury. It exists because the contract
source is deliberately terse (it must fit under the network's deploy-size
ceiling); the reasoning lives here, not in code comments.

---

## What it does

An issuer posts a verification job naming an agent, an artifact (repo URL + a
pinned commit), a test command, the requirements the work must satisfy, and
optionally specific files and a test-results URL. The named agent accepts,
binding themselves to those exact terms. When verified, a committee of
independent validators evaluates the deliverable across four dimensions —
functional correctness, code quality, security, completeness — and only their
agreement updates the agent's on-chain reputation.

There is no escrow, no stake, and no custody. A GenLayer Intelligent Contract
cannot move native funds (`emit_transfer` exists only on external contract
proxies, and the `Contract` base class exposes no transfer primitive). An
earlier version kept a stake ledger anyway; a runtime probe proved the ledger
grew while `self.balance` stayed 0, i.e. it recorded debts it could never pay.
That ledger was removed rather than faked. Enforcement is entirely reputational:
`reputation` is a public, cumulative number derived only from consensus
outcomes, and `get_standing` states `holds_funds: false` explicitly so no
reader ever has to infer it.

---

## The consensus model (the part that matters)

### The rejection this fixes

The previously submitted contract was rejected because **validators did not
independently verify the scores that determine the verdict.** The validator only
audited the leader's scorecard for arithmetic consistency — scores in range,
the weighted overall matching the configured weights, the verdict following
from the thresholds. That check is deterministic and cheap, but it binds only
the *arithmetic*, not the *judgment*. Two conflicting PASS and FAIL scorecards
for the same job were both "arithmetically valid," so both could be accepted.

A naive fix — have each validator re-run the LLM and require identical scores —
was already tried and **deadlocked every job.** Two independent LLM evaluations
of identical code are not bit-identical; they differ by a few points, so strict
equality disagreed on essentially every job and nothing ever committed (visible
on chain as SUCCESS with STORAGE_READ but zero STORAGE_WRITE and a disagree
vote).

### The fix: independent re-evaluation within a tolerance band

Each validator now **independently re-evaluates the artifact** — it fetches the
same pinned files and runs the same evaluation the leader ran, producing its own
scorecard — and agreement is required on two things:

1. **Each dimension within `SCORE_TOLERANCE` points** (default 8). Honest,
   independent evaluations of the same code land within a few points of each
   other, so a band lets honest runs agree while catching a leader that games
   the numbers: a validator that independently scores the artifact 70 when the
   leader reports 95 exceeds the band and votes DISAGREE, and the job does not
   commit.
2. **The categorical verdict must match exactly.** This closes the loophole
   where a leader shifts every score *within* the band to flip the outcome band
   (PASS ↔ FAIL).

The leader's scorecard is also still checked for arithmetic soundness
(`_audit_scorecard`), but that is now a **secondary guard**, not the consensus
decision. The primary decision is the independent judgment.

If the leader errors, the validator re-runs the evaluation and classifies the
failure: both erroring on the same deterministic fault (e.g. an unreachable
URL) is agreement; a leader that fails where the validator succeeds is not.

This is the same independent-evaluation pattern that passed review in
EvidenceVault and FactCheckDAO, adapted from categorical agreement to
tolerance-banded agreement because these are continuous scores, not labels.

---

## Reputation model

- `register()` seeds an agent at `MIN_REPUTATION` (300) and marks them active.
- A **PASS** verdict credits the full overall score and lifts the agent back
  over the floor if they had fallen below it (`active` is not a one-way door —
  recovered standing reinstates eligibility).
- A **PARTIAL** verdict credits half the score: it counts as work done but does
  not rebuild standing on its own.
- A **FAIL** verdict applies a proportional reputation drop
  (`slash_percent`, default 10%) and increments `demotions`. Repeated failure
  (`max_demotions`, default 3) ends eligibility — a track record of failure,
  not a single verdict. One bad review is recoverable; three are not.

There is deliberately **no eligibility gate on accepting work.** An earlier
draft gated acceptance on `reputation >= min_reputation`, which was a dead end:
the first FAIL drops reputation below the floor while the agent is still active,
so the second failure could never be recorded and the agent was stranded with no
way back. Eligibility is surfaced instead through `_tier` reporting UNVERIFIED
and `get_standing` reporting `eligible: false`; the issuer reads that before
posting. The agent, not the contract, decides who hires whom.

---

## Term binding

`accept_job` freezes the artifact, the requirements, the deadline, and the
slashing exposure into a single Keccak256 digest (`_terms_digest`). `verify`
recomputes it and refuses to proceed if any term changed after acceptance — the
issuer cannot edit a job to void what the agent agreed to. The digest uses
Keccak256, not Python's `hash()`, because `hash()` is salted per process and a
digest built on it never matches across the two separate transactions that
acceptance and verification are.

The artifact key (`repo@commit`) is reserved only when a job is actually
verified, not when it is posted — reserving at post time let anyone burn
artifact keys forever by posting jobs they never intended to verify.

---

## Deploy-size trade-offs (read this before judging the source)

Bradbury enforces a hard contract-deploy ceiling, measured precisely this
session: **19,703 bytes deploys; 20,303 bytes fails.** The full-featured
contract was 54 KB. To land under the ceiling the following were removed, in
order of how expendable they were:

- `settle_unclaimed` (permissionless abandon-settlement of expired jobs)
- `deactivate` (voluntary, irreversible exit from the agent set)
- `get_config`, `get_artifact_key`, `now` (convenience views)
- the `holds_funds` field from every view (always false; stated here instead)
- all module docstrings and inline comments (this file replaces them)
- verbose prompt wording, compressed to its essential instructions

Everything retained is load-bearing: the independent-evaluation consensus, the
term-binding digest, the reputation model, and the core lifecycle
(register → post_job → accept_job → decline_job → verify). The view methods
`get_standing`, `get_agent`, `get_job`, and `get_scorecard` remain so the state
is fully readable. Nothing was removed that changes a verdict or weakens the
consensus; only secondary conveniences and prose were cut.

---

## Verified deployment

| | |
|---|---|
| Network | GenLayer Bradbury testnet (chain 4221) |
| Contract | `0xfC0Cb63e9c4Cce0296025862Ce4592A46B5f6348` |
| Deploy tx | `0xe467ca96c2231e9d8fc13d6c56b9395f67aa8247f5df9dcc6db073ca0f37cf94` |
| Deploy result | ACCEPTED / AGREE |
| Full workflow | register → post_job → accept_job → **verify**, all AGREE |

The `verify` step is the proof: it runs the independent-evaluation consensus and
reached validator agreement on a live job. That is the on-chain evidence the
rejection reason is resolved.

Re-run the proof:
```bash
DEPLOY_PK=<key> node deploy-bradbury.mjs   # deploy
node _verify.cjs                            # register → post → accept → verify
```
