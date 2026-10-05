# Why validators audit instead of re-running the model

## The bug this documents

The first version of `verify()` had the validator call `leader_work()` itself
and compare both scorecards field by field. On chain this looked like success
and behaved like nothing happening:

```
verify tx 0x669f1e...   leader: SUCCESS, result "PASS"
                          call_counts: {STORAGE_READ: 141}   <-- no writes
                          validator: vote=disagree
```

`execution_result: SUCCESS` on the leader receipt, 141 storage reads, **zero**
writes, and a published scorecard that read back as `exists: false`. Reputation
never moved. Nothing in the returned value hinted at this — the transaction
succeeded, it simply committed nothing.

## Why re-running deadlocks

Two independent calls to a language model do not return identical numbers. The
same prompt against the same repo produces a different quality score on any
given run, because that is what a model does. Requiring byte-identical
agreement on four scores plus a verdict therefore makes agreement vanishingly
unlikely, on every job, forever.

The design was not conservative. It was a coin flip that always landed on
"reject", so the contract had no reachable path where a verification commits.

## What the validator does now

The validator audits the leader's output with pure arithmetic over values the
leader already returned:

```python
def _audit_scorecard(self, sc: dict) -> bool:
    # 1. every score in 0..100
    # 2. overall == (f*wf + q*wq + s*ws + c*wc) // 100   using configured weights
    # 3. verdict is the one the thresholds imply
    #    PASS if overall >= pass_threshold
    #    PARTIAL if partial_threshold <= overall < pass_threshold
    #    FAIL otherwise
    # 4. weights themselves sum to 100
```

Identical inputs produce identical answers on every honest validator, so
agreement is reachable — and it is a real check, because a leader that inflates
`overall`, mislabels a verdict, or reports scores outside 0-100 is rejected
even though every validator agrees the leader "ran fine".

### One trap worth naming

The audit must not require `evidence_hash`. That field is computed *after*
`run_nondet_default` returns, so the validator's `leaders_res.calldata` never
contains it. An audit that reads `sc["evidence_hash"]` raises `KeyError`,
returns `False`, and deadlocks every job in exactly the same way. A validator
can only ever see the fields the leader function itself returned.

## Verifying a commit actually happened

Do not trust the success message. Two fields in the leader receipt tell you
whether state was written:

```
consensus_data.leader_receipt[0].execution_stats.call_counts.STORAGE_WRITE
consensus_data.leader_receipt[1].vote
```

`STORAGE_WRITE > 0` means state changed. `vote: disagree` on the validator means
nothing was committed no matter what the leader reported.

## Live result

On `0xEc1cD00fefb4Dd2861d1CF426126a43b14ee3BD6` (Studio Dev 61997), a real
repository at a real commit:

```
functional 95  quality 96  security 91  completeness 95
overall 94  verdict PASS  evidence_hash 7eebcf24...
standing 0 -> 300 on registration, 300 -> 394 after the verdict
```

The weighted arithmetic is independently checkable from the published values:
`95*40 + 96*25 + 91*25 + 95*10 = 9425 // 100 = 94`, and `300 + 94 = 394`. Both
match what the contract stored, which is exactly the property the audit
enforces.

## The validator receipt that looks like an error but is not

The successful lifecycle transaction showed:

```
[0] leader    vote=None    exec=SUCCESS
[1] validator vote=idle    exec=ERROR
```

That second line is not a failure. Decoded, the validator's own fields say:

```
result       "\x02idle"
stderr       "Validator execution cancelled after quorum"
error_code   CONSENSUS_VALIDATOR_QUORUM_REACHED
causes       ["VALIDATOR_QUORUM_REACHED"]
```

Once enough validators have agreed, the remaining ones are cancelled to save
work. A cancelled validator reports `execution_result: ERROR` with a quorum
error code and never produces calldata. The transaction committed because
quorum was reached — which the leader receipt and the published scorecard both
confirm independently.

So the check for "did this commit" is, in order:

1. `execution_result` on the **leader** receipt is `SUCCESS`, and
2. `STORAGE_WRITE > 0` in the leader's `call_counts`, and
3. the validator vote is `None`/`idle` **or** `disagree`.

A validator showing `vote=idle` with `CONSENSUS_VALIDATOR_QUORUM_REACHED` is
the expected shape of a healthy multi-validator round. Only
`CONSENSUS_VALIDATOR_QUORUM_REACHED` absent *and* `vote=disagree` means the
deadlock described above.
