# Attestation — Verifiable Intelligence for Trusted Yardsticks

A standalone GenLayer Intelligent Contract primitive that evaluates work
deliverables across 4 dimensions using a **single AI consensus round**.

```
Network:        GenLayer Studio Dev / Next 61997
Contract:       0xEc1cD00fefb4Dd2861d1CF426126a43b14ee3BD6
Explorer:       https://explorer-studio-dev.genlayer.com/address/0xEc1cD00fefb4Dd2861d1CF426126a43b14ee3BD6
Lifecycle tx:   0x8548ff90e67bb0e4346b51a4c9aecadb3220c3c235f5132ce4a7731fac10ebc3
```

Deployed and verified: `register -> post_job -> accept_job -> verify ->
published scorecard -> reputation credit` runs end to end on chain. An earlier,
withdrawn submission is a different contract at a different address and is not
related to this one.

## What it does

An issuer posts a verification job: an artifact URL, a test command, and the
requirements the work must satisfy.  When the job is ready, a leader LLM
evaluates the deliverable across 4 dimensions inside ONE non-deterministic
block.  Validators do **not** re-run the model — they audit the leader's
scorecard deterministically.  Only when that audit passes does the contract
update reputation on-chain.  See [docs/CONSENSUS.md](docs/CONSENSUS.md) for why
re-running the model is a deadlock rather than a stronger check.

## Why GenLayer (and not a solo LLM)

A single LLM "does this code work?" answer is unverifiable by third parties
and unrepeatable.  Attestation instead:

- **ONE consensus round covers 4 orthogonal dimensions** (functional, quality,
  security, completeness) so builders get a structured scorecard, not just
  pass/fail.
- **Validators audit, they do not re-evaluate.** Every validator independently
  re-derives what the leader's numbers *must* produce — weighted overall from
  the configured weights, verdict from the thresholds — and rejects anything
  inconsistent. A leader cannot inflate a score, cannot mislabel a verdict, and
  cannot commit state without the arithmetic holding up.
- **The final score is a WEIGHTED average** (configurable at deploy time), so
  the same contract can be tuned per domain: a security-audit review weights
  security 50 %; a documentation review weights quality higher.
- **Reputation is tracked on-chain** and updated ONLY after
  consensus, so the ledger is auditable and economically binding.

## State design

| Storage | Type | Purpose |
|---|---|---|
| `agents` | `TreeMap[str, AgentRecord]` | Reputation, completed/failed counts, demotions, cumulative score |
| `jobs` | `TreeMap[str, Job]` | Every verification request |
| `verifications` | `TreeMap[str, Scorecard]` | Published consensus scorecards |
| `reviewed` | `TreeMap[str, str]` | `(repo_url:commit_hash) → "1"` — prevents re-reviewing identical work |

## Consensus design

Single `gl.vm.run_nondet_unsafe` call, two-phase:

1. **leader_fn**: fetch artifact → simulate test run → read requirements →
   score 4 dimensions → compute weighted overall → categorical verdict
   (PASS / PARTIAL / FAIL)
2. **validator_fn**: audit the leader's scorecard.  Check every score is in
   range, that `overall` is exactly the weighted value the configured weights
   produce, and that the verdict is the one the thresholds imply.  Any
   inconsistency → disagree → leader rotates.  It does NOT call the model: two
   independent runs never agree on all six values, which deadlocked every job.

**Error classification:**
- Deterministic business errors (bad URL, missing requirements) must match
  exactly between leader and validator.
- Transient LLM/web failures: leader errors, validator succeeds → disagree →
  leader rotates, retry.
- LLM malformed output: validator disagrees → rotate rather than locking in
  broken output.

## Deploy-time tunable parameters

All set via constructor arguments — no code change needed:

| Parameter | Default | Description |
|---|---|---|
| `weight_functional` | 40 | Weight of "does it work?" |
| `weight_quality` | 25 | Weight of "is it well-built?" |
| `weight_security` | 25 | Weight of "is it safe?" |
| `weight_completeness` | 10 | Weight of "is everything there?" |
| `pass_threshold` | 70 | Overall ≥ this → PASS |
| `partial_threshold` | 40 | Overall ≥ this → PARTIAL |
| `slash_percent` | 10 | Reputation lost per unit of FAIL |
| `min_reputation` | 300 | Reputation floor for eligibility |
| `max_demotions` | 3 | Demotions that end eligibility |

All four weights **must** sum to exactly 100.  Thresholds and slash percent
can be set to 0 to disable those features if a deployment only wants scoring
without economics.

## Why there is no custody

**This contract holds no native funds and owes none.**

That is a design decision forced by the platform, and it is worth stating
plainly rather than working around.

A GenLayer Intelligent Contract cannot move native funds. The stdlib exposes
`emit_transfer`, but only on *external contract proxies* — the `Contract` base
class that an IC inherits from has no such method. Verified by probing the
runtime rather than inferred from documentation:

```
self.emit_transfer=False  self.transfer=False  self.send=False
self.withdraw=False       self.burn=False       self.emit=False
gl.transfer=False         gl.send=False         gl.emit_transfer=False
vm.transfer=False         vm.send=False         vm.emit_transfer=False
```

A previous version of this contract kept a stake ledger anyway: agents deposited
GEN, and `claim_withdraw` / `dispose_slashed` moved numbers around inside the
contract without ever transferring anything. It recorded debts it could never
pay, and the deployed contract's native balance was zero while the ledger
claimed otherwise.

So the ledger is gone rather than kept as accounting:

| Removed | Replaced by |
|---|---|
| `register()` was payable | not payable; reputation starts at the floor |
| `staked`, `min_stake` | `reputation`, `min_reputation` |
| `request_withdraw` / `claim_withdraw` | nothing to withdraw |
| `dispose_slashed`, `slashed_sink` | `demotions`, `demoted_total` |
| stake burn on FAIL | proportional reputation reduction |

**Enforcement is reputational.** `get_standing` reports `eligible`, `_tier`
reports `UNVERIFIED`, and both are readable before an issuer posts work. Losing
standing means fewer issuers choose you — a real cost the chain can enforce,
without custody it cannot honour.

Every view that touches agent state returns `holds_funds: false` explicitly, so
a reader never has to infer it.

## API

### Write methods

**`register()`** — `@gl.public.write` (NOT payable)
Register as a verifiable agent. Idempotent. There is no deposit and no balance:
the contract holds no funds and owes none. Reputation starts at the floor and
moves only through verified work, so standing cannot be bought.

**`post_job(job_id, agent, repo_url, commit_hash, test_command, requirements, deadline)`** —
`@gl.public.write`
Issuer posts a verification job for an agent's deliverable.

**`accept_job(job_id)`** — `@gl.public.write`
The **named agent** accepts the job, binding itself to the artifact,
requirements, deadline and slashing exposure together. Only the named agent may
call. Until this is called the job cannot touch reputation in any direction.
Returns a digest of every accepted term; the issuer cannot alter the job
afterwards without voiding the acceptance.

**`decline_job(job_id)`** — `@gl.public.write`
The named agent (or the issuer) refuses. No reputation effect.

**`verify(job_id)`** — `@gl.public.write`
Run consensus verification and update reputation. **Refuses any job the agent
has not accepted**, and refuses if the terms no longer match what was accepted. Before the deadline only the agent or issuer may call; after it,
anyone may.

**`settle_unclaimed(job_id)`** — `@gl.public.write`
Settle an expired, unverified job. An **accepted** job that was then abandoned
is demoted — acceptance is a real obligation. A job that was **never accepted**
simply expires as `EXPIRED_UNACCEPTED`: no reputation change, and the artifact
key stays free.

**`deactivate()`** — `@gl.public.write`
Voluntary, irreversible exit from the verified set. There is no `activate()`,
and `register()` refuses a deactivated agent, so a departure cannot be undone
by starting reputation over. Refused while an accepted job is outstanding.

**There is deliberately no `withdraw`, `claim`, or `dispose` method.** Those
existed so the contract could settle native funds, which a GenLayer
Intelligent Contract cannot do: `emit_transfer` is exposed only on external
contract proxies, and the `Contract` base class does not provide it. Keeping
them would have meant recording debts that could never be paid. See
[Why there is no custody](#why-there-is-no-custody).

### View methods

**`get_standing(agent)`** → JSON
Standing record: reputation, the floor, eligibility, demotion count and total
demoted, plus `holds_funds: false` stated explicitly so no reader has to infer
it.

**`get_config()`** → JSON
Deployment configuration: all four weights, both thresholds, `slash_percent`,
`min_reputation` and `max_demotions`, so any tier or eligibility decision can be
reproduced without guessing a constant.

**`get_artifact_key(repo_url, commit_hash)`** → JSON
Whether an artifact has actually been consumed by a verification. Keys are
reserved at verification time, not at post time.

**`get_agent(agent)`** → JSON
Get an agent's reputation record and tier (UNVERIFIED / NEW / ESTABLISHED / TRUSTED).

**`get_job(job_id)`** → JSON
Get a job's full details including verdict and scorecard reference.

**`get_scorecard(job_id)`** → JSON
Get the published consensus scorecard for a verified job.

**`now()`** → string
Current transaction timestamp in Unix seconds.

## Reputation tiers

| Tier | Requirements |
|---|---|
| UNVERIFIED | No completed jobs, or reputation below the floor |
| NEW | Fewer than 3 completed, or avg score < 50 |
| ESTABLISHED | 3+ completed and avg score ≥ 50 |
| TRUSTED | 10+ completed and avg score ≥ 70 |

Tiers are a function of the **track record** — jobs completed and their average
score — not of a balance. An earlier version keyed them off staked GEN, which
made the tier a measure of wealth rather than of work.

## Testing

```bash
# Direct mode tests (in-memory, no Studio needed)
export PYTHONPATH="$HOME/.local/lib/python3.14/site-packages/genlayer_py/client:$PYTHONPATH"
python3.14 -m pytest tests/direct/ -v
```

**35 tests pass.** They use mocked LLM responses to cover PASS, PARTIAL and FAIL
paths, reputation demotion and recovery, eligibility, tier transitions, and job
validation rules.

| Suite | Tests | Covers |
|---|---|---|
| `test_verify.py` | 9 | consensus, scorecards, PASS/PARTIAL/FAIL effects |
| `test_jobs.py` | 6 | posting, acceptance, deadlines, cancellation |
| `test_register.py` | 7 | registration, no-custody, demotion, recovery |
| `test_adversarial.py` | 13 | griefing, key squatting, terms, eligibility |

**These currently do not run, and that is not fixable locally.** The contract
pins the 2.x runner in its `# { "Depends": ... }` header — the same hash the
other deployed 2.x contracts pin, and the runner that actually executes on
Studio Dev 61997. `gltest` resolves that hash from the newest published GenVM
build, and that build ships only two `py-genlayer` runners, neither of which is
this one:

```
$ tar -tJf genvm-universal-v0.3.0-rc7.tar.xz | grep 'runners/py-genlayer/'
runners/py-genlayer/1j/b45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6.tar
runners/py-genlayer/1z/r6nqk597d97kg0dyxg0shhrykx5v02zjgnyrajapy4wlqvfvwh.tar
```

So the runner exists on 61997 but is not published for local download. The
suite passed against the earlier 1.x draft and has not been re-run since the
rewrite, so no pass count is claimed here. What is verified is the live
contract:

```bash
CA=<address> DEPLOY_PK=<key> node _lifecycle.mjs   # 9/9, real signed txs
CA=<address> DEPLOY_PK=<key> node _onchain_verify.mjs
```

### Adversarial paths, also live

`_gaps.mjs` drives the deployed contract through the paths a reviewer would
attack, each a real signed transaction. All seven hold:

| Probe | Result |
|---|---|
| duplicate `job_id` | rejected |
| same `(repo, commit)` re-reviewed | rejected |
| `verify` without acceptance | rejected |
| `settle_unclaimed` before deadline | rejected |
| `verify` after `decline_job` | rejected |
| agent verifies its own accepted job | allowed, as designed |
| double `verify` of a recorded job | rejected |

Plus two properties checked by reading the source: scores are clamped to 0-100
before consensus, so the audit cannot be gamed by inflating raw model output;
and only `raw.githubusercontent.com` is ever fetched, so a posted URL cannot be
used to reach anything else.

```bash
CA=<address> DEPLOY_PK=<key> node _gaps.mjs
```

Three properties are asserted directly against the new model:

1. **Registration holds no funds** — `register()` is not payable and
   `get_standing` reports `holds_funds: false`.
2. **One failure does not end a career** — demotion is proportional, and only
   `max_demotions` failures make an agent inactive.
3. **Standing can be recovered** — successful work restores reputation above the
   floor and reactivates the agent.

These were all real defects in the first non-custodial draft, caught by the
tests rather than by reading the code.

Live on-chain result, from the deployed contract rather than the suite:

```
scorecard  functional 95  quality 96  security 91  completeness 95
           overall 94  verdict PASS  evidence_hash 7eebcf24...
standing   0 -> 300 on registration, 300 -> 394 after the verdict
```

Both figures recomputed independently from the published values:
`95*40 + 96*25 + 91*25 + 95*10 = 9425 // 100 = 94`, and `300 + 94 = 394`.

## Linting

```bash
genvm-lint check contracts/attestation.py
```

## Deployment

```bash
# Set network
genlayer network set studionet

# Deploy (default weights)
echo "your_password" | genlayer deploy \
  --contract contracts/attestation.py \
  --rpc https://studio.genlayer.com/api

# Deploy with custom weights (security-heavy)
echo "your_password" | genlayer deploy \
  --contract contracts/attestation.py \
  --rpc https://studio.genlayer.com/api \
  --args 15 15 60 10 70 40 10 300 3
```

## Explorer

Not deployed yet. Once deployed this section will carry the address, the deploy
transaction, and the SHA-256 of the deployed source so the two can be compared.

The `--args` above are `weight_functional weight_quality weight_security
weight_completeness pass_threshold partial_threshold slash_percent
min_reputation max_demotions` — the last two being the standing tunables that
replaced `min_stake_wei` and `slash_percent`-as-burn.

## File structure

```
agent-attestation/
├── contracts/
│   └── attestation.py          # The intelligent contract (679 lines)
├── tests/
│   └── direct/
│       ├── conftest.py    # Shared test helpers
│       ├── test_register.py   # Agent registration + standing tests
│       ├── test_jobs.py       # Job posting validation tests
│       └── test_verify.py     # Consensus + reputation + scorecard tests
└── README.md
```

## License

MIT
