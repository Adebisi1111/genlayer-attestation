"""Tests for agent registration and standing.

Registration is no longer payable. There is no deposit, no balance and nothing
to add, so the interesting behaviour is what registration does NOT do: it
cannot buy standing, and it cannot be repeated to launder a demotion.
"""

import json
from tests.direct.conftest import to_hex


def _floor(contract):
    return json.loads(contract.get_config())["min_reputation"]


def test_register_creates_record_at_the_floor(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/attestation.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = direct_alice
    direct_vm.value = 0
    contract.register()

    data = json.loads(contract.get_agent(alice))
    assert data["exists"] is True
    assert data["reputation"] == _floor(contract)
    assert data["active"] is True
    assert data["completed"] == 0 and data["failed"] == 0
    # a new agent has no track record, so no tier yet
    assert data["tier"] == "UNVERIFIED"


def test_register_is_idempotent(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/attestation.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = direct_alice
    direct_vm.value = 0
    contract.register()
    contract.register()
    contract.register()

    data = json.loads(contract.get_agent(alice))
    # registering repeatedly must not raise reputation
    assert data["reputation"] == _floor(contract)


def test_register_holds_no_funds(direct_vm, direct_deploy, direct_alice):
    """The contract must never claim custody, and must say so."""
    contract = direct_deploy("contracts/attestation.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = direct_alice
    direct_vm.value = 0
    contract.register()

    standing = json.loads(contract.get_standing(alice))
    assert standing["holds_funds"] is False
    assert standing["eligible"] is True
    # there is no pending-withdrawal concept at all
    assert "pending_withdraw" not in standing


def test_deactivated_agent_cannot_re_register(direct_vm, direct_deploy, direct_alice):
    """Otherwise a demoted agent could start reputation over by re-registering."""
    contract = direct_deploy("contracts/attestation.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = direct_alice
    contract.register()
    contract.deactivate()

    try:
        contract.register()
        raise AssertionError("a deactivated agent must not be able to re-register")
    except Exception as e:  # noqa: BLE001
        assert "re-register" in str(e) or "deactivated" in str(e), e


def test_unregistered_agent_has_default_view(direct_deploy):
    contract = direct_deploy("contracts/attestation.py")
    data = json.loads(contract.get_agent("0xdeadbeef1234567890abcdef1234567890abcdef"))
    assert data["exists"] is False
    assert data["tier"] == "UNVERIFIED"
    assert data["reputation"] == 0
    assert data["holds_funds"] is False


def test_trusted_tier_after_passes_raise_reputation(direct_vm, direct_deploy, direct_alice):
    """Reputation is earned by verified work, not deposited."""
    contract = direct_deploy("contracts/attestation.py")
    alice = to_hex(direct_alice)
    floor = _floor(contract)

    direct_vm.sender = direct_alice
    contract.register()

    # enough passes to clear the TRUSTED bar: 5x the floor with a high average
    for i in range(12):
        job = f"pass{i}"
        direct_vm.mock_llm(
            r".*Evaluate this work deliverable.*",
            json.dumps(
                {
                    "functional": 90,
                    "quality": 85,
                    "security": 80,
                    "completeness": 90,
                    "reasoning": "good",
                }
            ),
        )
        contract.post_job(
            job_id=job,
            agent=alice,
            repo_url=f"https://example.com/repo{i}",
            commit_hash=f"commit{i}",
            test_command="pytest",
            requirements=["must have tests"],
            deadline=9999999999,
            files=[],
        )
        contract.accept_job(job)
        contract.verify(job)

    data = json.loads(contract.get_agent(alice))
    assert data["completed"] == 12
    assert data["avg_score"] >= 70
    # standing was earned by work, above the starting floor
    assert data["reputation"] > floor, data
    assert data["tier"] == "TRUSTED", data
    assert data["holds_funds"] is False


def test_fail_reduces_reputation_by_slash_percent(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/attestation.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = direct_alice
    contract.register()
    before = json.loads(contract.get_agent(alice))["reputation"]

    contract.post_job(
        job_id="bad",
        agent=alice,
        repo_url="https://example.com/repo",
        commit_hash="zzz",
        test_command="pytest",
        requirements=["must work"],
        deadline=9999999999,
        files=[],
    )
    contract.accept_job("bad")
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
    contract.verify("bad")

    data = json.loads(contract.get_agent(alice))
    cfg = json.loads(contract.get_config())
    assert data["failed"] == 1
    assert data["demotions"] == 1
    assert data["reputation"] == before - (before * cfg["slash_percent"] // 100), data
    assert data["demoted_total"] == before - data["reputation"], data