"""Adversarial tests for the four issues the steward raised.

1. Unauthorized jobs must not affect reputation or stake.
2. Unaccepted jobs must not permanently reserve artifact keys (squatting).
3. Withdrawal must be safe and replay-resistant.
4. Slashed native funds need an actual auditable disposition.

Each group is written so that reintroducing the defect makes it fail.
"""

import json

from tests.direct.conftest import to_hex, scorecard_dict

ONE_GEN = 1000000000000000000


def _err(fn, *needles):
    try:
        fn()
    except Exception as e:
        msg = str(e).lower()
        for n in needles:
            assert n.lower() in msg, f"expected {n!r} in {msg!r}"
        return msg
    raise AssertionError("expected rejection, but the call succeeded")


def _expire(contract, job_id):
    """Force a job's deadline into the past.

    Direct-mode contracts read the real clock, so an expired-job test cannot
    wait. Rewriting the stored deadline keeps the test deterministic and
    exercises the real settle path.
    """
    job = contract.jobs[job_id]
    job.deadline = type(job.deadline)(1)
    contract.jobs[job_id] = job


def _mock_llm(direct_vm, functional=90, quality=90, security=90, completeness=90):
    direct_vm.mock_llm(
        r".*Evaluate this work deliverable.*",
        json.dumps(scorecard_dict(functional=functional, quality=quality,
                                  security=security, completeness=completeness)),
    )


def contract_min_reputation_cfg(contract):
    """Read deployment configuration from the contract, never a hardcoded value."""
    return json.loads(contract.get_config())


def contract_min_reputation(contract):
    return contract_min_reputation_cfg(contract)["min_reputation"]


def _reg(contract, direct_vm, who, stake=3 * ONE_GEN):
    # register() is NOT payable: there is no deposit and no balance.
    direct_vm.sender = who
    direct_vm.value = 0
    contract.register()


def _post(contract, direct_vm, issuer, agent_hex, rid, commit="abc123", deadline=None):
    direct_vm.sender = issuer
    if deadline is None:
        deadline = int(contract.now()) + 9999
    contract.post_job(
        rid, agent_hex, "https://github.com/acme/repo", commit,
        "pytest", ["has tests"], deadline, ["main.py"],
    )


# ---------------------------------------------------------------------
# 1. Unauthorized jobs
# ---------------------------------------------------------------------

def test_job_cannot_affect_stake_before_acceptance(direct_vm, direct_deploy,
                                                   direct_alice, direct_bob):
    """An issuer naming an agent must not be able to slash them."""
    contract = direct_deploy("contracts/attestation.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)
    _post(contract, direct_vm, direct_bob, alice, "j1")

    _err(lambda: contract.verify("j1"), "not accepted")

    rec = json.loads(contract.get_agent(alice))
    floor = contract_min_reputation(contract)
    assert rec["completed"] == 0 and rec["failed"] == 0, rec
    assert rec["demotions"] == 0, rec
    assert rec["reputation"] == floor, rec
    assert rec["active"] is True, rec


def test_expired_unaccepted_job_cannot_slash(direct_vm, direct_deploy,
                                             direct_alice, direct_bob):
    """The griefing vector: settle_unclaimed on a job never accepted."""
    contract = direct_deploy("contracts/attestation.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)

    _post(contract, direct_vm, direct_bob, alice, "j1", commit="abc")
    _expire(contract, "j1")

    got = contract.settle_unclaimed("j1")
    assert got == "EXPIRED_UNACCEPTED", got

    rec = json.loads(contract.get_agent(alice))
    floor = contract_min_reputation(contract)
    assert rec["demotions"] == 0, rec
    assert rec["failed"] == 0, rec
    assert rec["reputation"] == floor, rec


def test_only_named_agent_may_accept(direct_vm, direct_deploy,
                                     direct_alice, direct_bob):
    contract = direct_deploy("contracts/attestation.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)
    _post(contract, direct_vm, direct_bob, alice, "j1")
    direct_vm.sender = direct_bob
    _err(lambda: contract.accept_job("j1"), "only the named agent")


def test_accepted_then_abandoned_still_slashes(direct_vm, direct_deploy,
                                               direct_alice, direct_bob):
    """Acceptance creates a REAL obligation - otherwise it means nothing."""
    contract = direct_deploy("contracts/attestation.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)

    _post(contract, direct_vm, direct_bob, alice, "j1", commit="abc")
    _expire(contract, "j1")
    direct_vm.sender = direct_alice
    contract.accept_job("j1")

    got = contract.settle_unclaimed("j1")
    assert got == "FAIL", got
    rec = json.loads(contract.get_agent(alice))
    assert rec["demotions"] == 1, rec
    floor = contract_min_reputation(contract)
    # reputation is cut by slash_percent of whatever it was
    assert rec["reputation"] == floor - (floor * 10 // 100), rec
    assert rec["demoted_total"] == floor - rec["reputation"], rec


def test_terms_cannot_be_changed_after_acceptance(direct_vm, direct_deploy,
                                                   direct_alice, direct_bob):
    """The digest binds artifact, requirements, deadline and slash exposure."""
    contract = direct_deploy("contracts/attestation.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)
    _post(contract, direct_vm, direct_bob, alice, "j1")
    direct_vm.sender = direct_alice
    terms = contract.accept_job("j1")
    assert terms

    job = json.loads(contract.get_job("j1"))
    assert job["accepted"] is True, job
    assert job["accepted_terms"] == terms, job


def test_unregistered_agent_cannot_accept(direct_vm, direct_deploy,
                                          direct_alice, direct_bob):
    contract = direct_deploy("contracts/attestation.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)
    _post(contract, direct_vm, direct_bob, alice, "j1")
    direct_vm.sender = direct_bob
    _err(lambda: contract.accept_job("j1"), "only the named agent")


def test_declined_job_is_settled_without_effect(direct_vm, direct_deploy,
                                                direct_alice, direct_bob):
    contract = direct_deploy("contracts/attestation.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)
    _post(contract, direct_vm, direct_bob, alice, "j1", commit="abc")
    _expire(contract, "j1")
    direct_vm.sender = direct_alice
    contract.decline_job("j1")

    rec = json.loads(contract.get_agent(alice))
    floor = contract_min_reputation(contract)
    rec = json.loads(contract.get_agent(alice))
    assert rec["demotions"] == 0 and rec["failed"] == 0, rec
    assert rec["reputation"] == floor, rec
    _err(lambda: contract.verify("j1"), "cancelled")


# ---------------------------------------------------------------------
# 2. Artifact-key squatting
# ---------------------------------------------------------------------

def test_posting_a_job_does_not_reserve_the_artifact_key(direct_vm, direct_deploy,
                                                         direct_alice, direct_bob):
    """The squatting vector: post without verifying, keys must stay free."""
    contract = direct_deploy("contracts/attestation.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)

    for i in range(25):
        _post(contract, direct_vm, direct_bob, alice, f"junk{i}", commit=f"c{i}")

    state = json.loads(contract.get_artifact_key("https://github.com/acme/repo", "c7"))
    assert state["reserved"] is False, state


def test_squatter_cannot_block_a_real_job_on_the_same_artifact(direct_vm, direct_deploy,
                                                               direct_alice, direct_bob):
    contract = direct_deploy("contracts/attestation.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)
    _post(contract, direct_vm, direct_bob, alice, "junk", commit="shared")
    # a second issuer may still use the same artifact
    _post(contract, direct_vm, direct_alice, alice, "real", commit="shared")
    job = json.loads(contract.get_job("real"))
    assert job["exists"] is True, job


def test_key_is_reserved_only_after_real_verification(direct_vm, direct_deploy,
                                                      direct_alice, direct_bob):
    contract = direct_deploy("contracts/attestation.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)
    _mock_llm(direct_vm)
    _post(contract, direct_vm, direct_bob, alice, "j1", commit="used")
    direct_vm.sender = direct_alice
    contract.accept_job("j1")
    contract.verify("j1")

    state = json.loads(contract.get_artifact_key("https://github.com/acme/repo", "used"))
    assert state["reserved"] is True, state
    _err(lambda: _post(contract, direct_vm, direct_bob, alice, "j2", commit="used"),
         "already reviewed")


def test_artifact_key_normalises_case(direct_vm, direct_deploy,
                                      direct_alice, direct_bob):
    """Otherwise the same artifact could be reviewed twice by case variation."""
    contract = direct_deploy("contracts/attestation.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)
    _mock_llm(direct_vm)
    _post(contract, direct_vm, direct_bob, alice, "j1", commit="abc123")
    direct_vm.sender = direct_alice
    contract.accept_job("j1")
    contract.verify("j1")

    direct_vm.sender = direct_bob
    _err(
        lambda: contract.post_job("j2", alice, "https://GitHub.com/ACME/repo",
                                  "ABC123", "pytest", ["x"], 99999999999, ["m.py"]),
        "already reviewed",
    )


# ---------------------------------------------------------------------
# 3. Withdrawal
# ---------------------------------------------------------------------


def test_deactivation_is_one_way(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/attestation.py")
    _reg(contract, direct_vm, direct_alice, stake=2 * ONE_GEN)
    contract.deactivate()
    _err(lambda: contract.deactivate(), "already deactivated")
    assert not hasattr(contract, "activate"), "re-activation must not exist"






def test_repeated_failures_end_eligibility_and_are_recoverable(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """max_demotions failures end eligibility; success can win it back.

    One bad verdict must NOT end a career - that was a real defect in the first
    non-custodial draft, where a single 10% penalty off a score-scale
    reputation dropped every agent below the floor immediately.
    """
    contract = direct_deploy("contracts/attestation.py")
    _reg(contract, direct_vm, direct_alice)
    alice = to_hex(direct_alice)
    cfg = contract_min_reputation_cfg(contract)
    floor = cfg["min_reputation"]
    limit = cfg["max_demotions"]

    def run_failing_job(tag):
        job = f"bad-{tag}"
        _post(contract, direct_vm, direct_bob, alice, job, commit=f"c{tag}")
        direct_vm.sender = direct_alice
        contract.accept_job(job)
        direct_vm.mock_llm(
            r".*Evaluate this work deliverable.*",
            json.dumps(
                {
                    "functional": 10,
                    "quality": 5,
                    "security": 0,
                    "completeness": 5,
                    "reasoning": "bad",
                }
            ),
        )
        contract.verify(job)
        return job

    # one failure: demoted, still eligible
    run_failing_job(1)
    rec = json.loads(contract.get_agent(alice))
    assert rec["demotions"] == 1, rec
    assert rec["active"] is True, "one failure must not end eligibility"
    assert rec["reputation"] < floor, rec

    # keep failing until the limit is reached
    for i in range(2, limit + 1):
        run_failing_job(i)

    rec = json.loads(contract.get_agent(alice))
    assert rec["demotions"] == limit, rec
    assert rec["active"] is False, f"{limit} failures must end eligibility"

    # an inactive agent is visibly ineligible to an issuer before they post
    standing = json.loads(contract.get_standing(alice))
    assert standing["eligible"] is False, standing
    assert standing["holds_funds"] is False, standing

    # and may still accept, because recovery requires the chance to work
    _post(contract, direct_vm, direct_bob, alice, "recover", commit="rc")
    direct_vm.sender = direct_alice
    contract.accept_job("recover")

    # and successful work wins the standing back
    good = "recover"
    # Mocks accumulate and the earliest matching one wins, so the FAIL mocks
    # registered above must be dropped before a PASS response can be served.
    direct_vm.clear_mocks()
    direct_vm.mock_llm(
        r".*Evaluate this work deliverable.*",
        json.dumps(
            {
                "functional": 95,
                "quality": 95,
                "security": 95,
                "completeness": 95,
                "reasoning": "excellent",
            }
        ),
    )
    contract.verify(good)

    rec = json.loads(contract.get_agent(alice))
    assert rec["reputation"] >= floor, rec
    assert rec["active"] is True, "success must be able to restore eligibility"

    standing = json.loads(contract.get_standing(alice))
    assert standing["holds_funds"] is False, standing
    assert standing["eligible"] is True, standing

