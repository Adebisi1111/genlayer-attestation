# Withdrawn custody suite

`test_custody_audit.py` from the withdrawn contract, kept as a record of what
was removed and why. **It does not run against this contract** — every test in
it exercises methods that no longer exist (`request_withdraw`, `claim_withdraw`,
`dispose_slashed`) or asserts on a `staked` field that was removed.

## Why it is kept

The steward review rejected the previous submission because withdrawals and
slashed-fund disposal only updated internal accounting. Rather than delete the
evidence that those paths once existed and once appeared covered, the file is
preserved here.

## What it would have caught, and what replaced it

| Old assertion | New equivalent |
|---|---|
| A reserved withdrawal cannot be double-claimed | `test_GivenAFulfilledJob_WhenTheSameResultArrivesAgain_ThenItReverts` — replay is refused |
| Ledger accounts for every wei | `test_register_holds_no_funds` — asserts `holds_funds: false` |
| Pending funds are never slashed | `test_job_cannot_affect_stake_before_acceptance` → reputation is untouched before acceptance |
| Slashed value never returns to stake | `test_demoted_below_floor_...` → demotion is not credited back |
| Stake is releasable once a job settles | no longer applicable — nothing is held |

## The underlying defect

None of those tests was wrong at the time. They all asserted on an internal
ledger, and the ledger was internally consistent — it just did not correspond to
any real funds. A GenLayer Intelligent Contract cannot move native funds:

```
self.emit_transfer=False  self.transfer=False  self.send=False
gl.transfer=False         gl.send=False         gl.emit_transfer=False
vm.transfer=False         vm.send=False         vm.emit_transfer=False
```

The deployed contract's native balance was **zero** while this suite's
predecessor recorded deposits against it. The tests were green and the custody
was never real.

That gap is why this repository asserts `holds_funds: false` on every
agent-facing view instead of asserting that internal accounting balances.
