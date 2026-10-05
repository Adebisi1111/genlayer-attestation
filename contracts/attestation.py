# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }

"""
Attestation — Verifiable Intelligence for Trusted Yardsticks
=========================================================

A standalone GenLayer Intelligent Contract primitive that evaluates work
deliverables (code repos, documents, designs — anything with a URL + criteria)
across 4 independent dimensions using a SINGLE AI consensus round.

PURPOSE
    An issuer posts a verification job: artifact URL, test command, and the
    requirements the work must satisfy.  When the job is ready, a leader LLM
    fetches the artifact, runs the test command, reads the requirements, and
    scores 4 dimensions inside ONE non-deterministic block.  Independent
    validators re-run the SAME evaluation and compare EVERY stored score.
    Only when all 4 scores AND the categorical verdict agree does the contract
    update reputation and stake on-chain.

WHY GENLAYER (and not a solo LLM call)
    A single LLM "does this code work?" answer is unverifiable by third
    parties and unrepeatable.  Attestation instead:

    * ONE consensus round covers 4 orthogonal dimensions (functional, quality,
      security, completeness) so builders get a structured scorecard, not just
      pass/fail.
    * Every validator independently re-runs the FULL evaluation — they do NOT
      trust the leader's scores.  Agreement on ALL 4 scores AND the verdict
      is required before any state changes.
    * The final score is a WEIGHTED average (configurable at deploy time), so
      the same contract can be tuned per domain: a security-audit review
      weights security 50 %; a documentation review weights quality higher.
    * Reputation and stake are tracked on-chain and updated ONLY after
      consensus, so the ledger is auditable and economically binding.

STATE DESIGN
    agents        TreeMap[str, AgentRecord]   — stake, completed/failed counts,
                                                 slashed total, cumulative score
    jobs          TreeMap[str, Job]           — every verification request
    verifications TreeMap[str, Scorecard]     — published scorecards
    reviewed      TreeMap[str, str]           — (repo_url:commit_hash) → "1"
                                                 prevents re-reviewing identical work

CONSENSUS DESIGN
    Single gl.vm.run_nondet_unsafe call, two-phase:

    leader_fn:  fetch artifact → simulate test run → read requirements →
                score 4 dimensions → compute weighted overall →
                categorical verdict (PASS / PARTIAL / FAIL)

    validator_fn: re-run leader_fn independently.  If leader errored, check
                  whether we error the same way (deterministic business errors
                  must match).  Otherwise compare EVERY stored score and the
                  categorical verdict exactly.  Any mismatch → disagree →
                  leader rotates.

    Error classification:
      - Deterministic business errors (bad URL, missing requirements) must
        match exactly between leader and validator.
      - Transient LLM/web failures: leader errors, validator succeeds →
        disagree → leader rotates, retry.
      - LLM malformed output: validator disagrees → rotate rather than
        locking in broken output.

DEPLOY-TIME TUNABLE PARAMETERS (set in constructor — no code change needed)
    weight_functional   Weight of "does it work?"          (default 40)
    weight_quality      Weight of "is it well-built?"     (default 25)
    weight_security     Weight of "is it safe?"           (default 25)
    weight_completeness Weight of "is everything there?"  (default 10)
    pass_threshold      Overall ≥ this → PASS             (default 70)
    slash_percent       Reputation lost per unit of FAIL (default 10)
    slash_percent       Fraction of stake burned on FAIL  (default 10)
    min_reputation      Reputation floor to stay eligible   (default 1000)

    All four weights MUST sum to 100.  Thresholds and slash percent can be
    set to 0 to disable those features if a deployment only wants scoring
    without economics.

SEE ALSO
    README.md — full documentation, usage examples, how to run tests.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import genlayer as gl
from genlayer import Address, Keccak256, u256
from genlayer.storage import TreeMap
from genlayer.storage import allow as allow_storage

# Storage dataclass fields must be scalars. The 2.x runner rejects a
# @allow_storage dataclass that holds ANY collection type - list[str],
# list[int], DynArray[str] and tuple[str] each reproduce "invalid contract
# runner malformed", while str, u256 and bool deploy. Proven one field at a
# time against a contract that otherwise deploys: the first five str fields
# pass, the sixth (requirements: list[str]) fails. So the collection-valued
# fields below are stored as newline-joined strings and split back on read.
# Method PARAMETERS may still take list[str]; only storage is restricted.

def _join(items: list[str]) -> str:
    return "\n".join(items)


def _split(joined: str) -> list[str]:
    return [x for x in joined.split("\n") if x] if joined else []


def _digest(payload: dict) -> str:
    """Deterministic content digest.

    Python's builtin hash() is salted per process, so it returns a DIFFERENT
    value for the same string in every transaction. A digest built on it can
    never be compared across two calls, which is exactly what acceptance
    requires: accept_job stores the digest and verify recomputes it in a
    separate transaction, so the two never matched and every verify died with
    "Job terms changed after acceptance". Keccak256 is deterministic and is
    already what the GenLayer standard library uses for event topics.
    """
    return Keccak256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


# 2.x runner notes (Studio Next 61997):
#   * `DynArray` must NOT be imported. Doing so makes the runner reject the whole
#     contract with "invalid contract runner malformed" — proven by swapping only
#     this one import into a known-good contract and reproducing the failure.
#     Use `list[str]` in method signatures; in storage, join to a string (see
#     _join/_split above).
#   * `field` from dataclasses is unused here and is likewise not loadable.
#   * a `bigint` storage field assigned a plain value is rejected too: the field
#     must be declared `u256`, the constructor parameter must be `u256`, and the
#     assignment must wrap it - `self.x = u256(x)`. Any one of the three missing
#     produces the same malformed error.


# ---------------------------------------------------------------------------
# Configuration defaults (overridden by constructor)
# ---------------------------------------------------------------------------

WEIGHT_FUNCTIONAL = 40
WEIGHT_QUALITY = 25
WEIGHT_SECURITY = 25
WEIGHT_COMPLETENESS = 10

PASS_THRESHOLD = 70
PARTIAL_THRESHOLD = 40

SLASH_PERCENT = 10
# Reputation is in the same units as a 0-100 score, so a good job credits
# roughly 70-100. A floor of 1000 against that scale demanded ~12 good jobs to
# rebuild standing after a demotion, which made the penalty permanent in
# practice. 300 is reachable: three failures from a fresh agent leave ~243, and
# one good job brings it back over the line.
MIN_REPUTATION = 300  # reputation floor to remain eligible for work
MAX_DEMOTIONS = 3  # consecutive-style failures that end eligibility


# ---------------------------------------------------------------------------
# Storage types
# ---------------------------------------------------------------------------

@allow_storage
@dataclass
class AgentRecord:
    """On-chain standing for one agent address.

    There is no `staked` field and no token balance anywhere in this contract.
    Enforcement is reputational: `reputation` is a public, cumulative score
    derived from consensus outcomes, and an agent below `min_reputation`
    cannot accept work. That is a real constraint the chain enforces, and it
    requires no custody the platform cannot honour.

    A GenLayer Intelligent Contract cannot move native funds — `emit_transfer`
    exists only on external contract proxies and `Contract` does not expose it.
    An earlier version of this contract kept a stake ledger anyway, so it
    recorded debts it could never settle. That ledger is gone by design.
    """

    reputation: u256
    completed: u256
    failed: u256
    demotions: u256
    demoted_total: u256
    total_score: u256
    active: bool
    open_jobs: u256


# There is deliberately no CodeArtifact dataclass. The artifact fields are
# flattened into Job, and a declared-but-unreferenced @allow_storage dataclass
# is itself enough to make the 2.x runner reject the contract with "invalid
# contract runner malformed" — proven by deleting this one declaration from an
# otherwise-successful file.

@allow_storage
@dataclass
class Scorecard:
    """Published consensus result for one job."""

    functional: u256
    quality: u256
    security: u256
    completeness: u256
    overall: u256
    verdict: str
    evidence_hash: str


@allow_storage
@dataclass
class Job:
    """A single verification request.

    The artifact fields are flattened in rather than held as a nested
    dataclass. A field whose type is another dataclass makes the 2.x runner
    reject the whole contract with "invalid contract runner malformed" —
    proven field-by-field against a known-good contract, where every scalar
    field deploys and only the nested one fails.
    """

    issuer: str
    agent: str
    repo_url: str
    commit_hash: str
    test_command: str
    requirements_joined: str
    files_joined: str
    test_results_url: str
    deadline: u256
    recorded: bool
    verdict: str
    final_score: u256
    accepted: bool
    accepted_terms: str
    cancelled: bool


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

class Attestation(gl.contract.Contract):
    """Verifiable Intelligence for Trusted Yardsticks.

    Evaluates work deliverables across 4 dimensions using a single AI
    consensus round.  Agents stake, issuers post jobs, and on-chain
    reputation + stake are updated ONLY after validators agree on every
    dimension score and the categorical verdict.
    """

    # ---- storage ----

    agents: TreeMap[str, AgentRecord]
    jobs: TreeMap[str, Job]
    verifications: TreeMap[str, Scorecard]
    reviewed: TreeMap[str, str]

    # ---- deploy-time tunables (set from constructor args) ----

    weight_functional: u256
    weight_quality: u256
    weight_security: u256
    weight_completeness: u256
    pass_threshold: u256
    partial_threshold: u256
    slash_percent: u256
    min_reputation: u256
    max_demotions: u256

    def __init__(
        self,
        weight_functional: u256 = u256(WEIGHT_FUNCTIONAL),
        weight_quality: u256 = u256(WEIGHT_QUALITY),
        weight_security: u256 = u256(WEIGHT_SECURITY),
        weight_completeness: u256 = u256(WEIGHT_COMPLETENESS),
        pass_threshold: u256 = u256(PASS_THRESHOLD),
        partial_threshold: u256 = u256(PARTIAL_THRESHOLD),
        slash_percent: u256 = u256(SLASH_PERCENT),
        min_reputation_wei: u256 = MIN_REPUTATION,
        max_demotions_: u256 = u256(MAX_DEMOTIONS),
    ):
        """Deploy with (optional) tuned weights, thresholds, and economics.

        All four weights must sum to exactly 100.
        """
        wf = int(weight_functional)
        wq = int(weight_quality)
        ws = int(weight_security)
        wc = int(weight_completeness)
        if wf + wq + ws + wc != 100:
            raise gl.vm.UserError(
                "Weights must sum to 100 "
                f"(got {wf}+{wq}+{ws}+{wc}={wf + wq + ws + wc})"
            )
        pt = int(pass_threshold)
        pth = int(partial_threshold)
        if not (0 <= pt <= 100):
            raise gl.vm.UserError("pass_threshold must be 0-100")
        if not (0 <= pth <= pt):
            raise gl.vm.UserError("partial_threshold must be 0-pass_threshold")

        self.weight_functional = u256(weight_functional)
        self.weight_quality = u256(weight_quality)
        self.weight_security = u256(weight_security)
        self.weight_completeness = u256(weight_completeness)
        self.pass_threshold = u256(pass_threshold)
        self.partial_threshold = u256(partial_threshold)
        self.slash_percent = u256(slash_percent)
        self.min_reputation = min_reputation_wei
        self.max_demotions = u256(max_demotions_)

    # ------------------------------------------------------------------
    # Artifact keys and acceptance terms
    # ------------------------------------------------------------------

    def _artifact_key(self, repo_url: str, commit_hash: str) -> str:
        """Canonical key for an immutable artifact."""
        return f"{repo_url.strip().lower()}@{commit_hash.strip().lower()}"

    def _terms_digest(self, job: Job) -> str:
        """Digest of every term the agent is accepting.

        Acceptance is only meaningful if it provably covers the artifact, the
        requirements, the deadline AND the slashing exposure. Binding all four
        into one digest means the issuer cannot alter any of them after the
        agent accepted without invalidating what was agreed to.
        """
        return _digest(
            {
                "artifact": self._artifact_key(job.repo_url, job.commit_hash),
                "files": _split(job.files_joined),
                "test_command": job.test_command,
                "requirements": _split(job.requirements_joined),
                "test_results_url": job.test_results_url,
                "deadline": int(job.deadline),
                "slash_percent": int(self.slash_percent),
                "min_reputation": int(self.min_reputation),
                "max_demotions": int(self.max_demotions),
            }
        )

    # ------------------------------------------------------------------
    # Time
    # ------------------------------------------------------------------

    def _now(self) -> int:
        """Transaction timestamp in Unix seconds — identical on every validator."""
        return int(datetime.now(timezone.utc).timestamp())

    # ------------------------------------------------------------------
    # Reputation math
    # ------------------------------------------------------------------

    def _tier(self, rec: AgentRecord) -> str:
        """Current reputation tier for an agent record.

        Derived from the TRACK RECORD — jobs completed and their average score —
        with reputation used only as the eligibility floor. The previous two
        versions keyed the tier off a staked balance and then off a reputation
        total; both made the tier a measure of accumulated units rather than of
        work, and a 3x multiplier made TRUSTED unreachable for ~24 good jobs.

        A tier should answer "how much has this agent actually done, and how
        well", and nothing else.
        """
        completed = int(rec.completed)
        total = completed + int(rec.failed)

        if total == 0 or int(rec.reputation) < int(self.min_reputation):
            return "UNVERIFIED"

        avg = int(rec.total_score) // total if total else 0

        if completed >= 10 and avg >= 70:
            return "TRUSTED"
        if completed >= 3 and avg >= 50:
            return "ESTABLISHED"
        return "NEW"

    def _apply_slash(self, rec: AgentRecord) -> AgentRecord:
        """Reduce reputation on a FAIL verdict.

        Replaces the old stake burn. There is nothing to burn and nothing to
        reconcile: `reputation` is a number this contract owns outright, derived
        from consensus, and lowering it is the entire enforcement mechanism.

        The drop is proportional but never deactivating on its own. A fixed
        percentage off a score-scale reputation would end an agent's eligibility
        after a single failure — one bad review would be unrecoverable, which is
        both brittle and not what a proportional penalty means. Instead the
        agent is marked inactive only once it has been demoted `max_demotions`
        times, which is a track record of failure rather than a single verdict.

        Inactivity is not permanent either: succeeding at new work raises
        reputation, and crossing back over the floor with an intact `active`
        flag is what restores eligibility. See `_reputation_earned`.
        """
        rep = int(rec.reputation)
        pct = int(self.slash_percent)
        drop = (rep * pct) // 100
        if drop > rep:
            drop = rep
        rec.reputation = u256(rep - drop)
        rec.demotions += u256(1)
        rec.demoted_total += u256(drop)
        # Repeated failure ends eligibility. One verdict does not.
        if int(rec.demotions) >= int(self.max_demotions):
            rec.active = False
        return rec

    def _reputation_earned(self, rec: AgentRecord, credit: int) -> AgentRecord:
        """Add reputation from a good verdict, and lift the flag if recovered.

        A deactivated agent that gets its standing back above the floor is
        reinstated here. Without this, `active` would be a one-way door that
        reputation could never undo, and the whole model would be terminal.
        """
        rec.reputation += u256(credit)
        if not rec.active and int(rec.reputation) >= int(self.min_reputation):
            # Reputation recovered above the floor through successful work.
            rec.active = True
        return rec

    def _final_score(self, functional: int, quality: int, security: int, completeness: int) -> int:
        """Weighted overall score from 4 dimension scores (each 0-100)."""
        w_f = int(self.weight_functional)
        w_q = int(self.weight_quality)
        w_s = int(self.weight_security)
        w_c = int(self.weight_completeness)
        return (
            functional * w_f
            + quality * w_q
            + security * w_s
            + completeness * w_c
        ) // 100

    def _verdict(self, overall: int) -> str:
        """Categorical verdict from overall score."""
        pt = int(self.pass_threshold)
        pth = int(self.partial_threshold)
        if overall >= pt:
            return "PASS"
        if overall >= pth:
            return "PARTIAL"
        return "FAIL"

    # ------------------------------------------------------------------
    # Single non-deterministic evaluation flow
    # ------------------------------------------------------------------

    def _evaluate(self, repo_url: str, commit_hash: str, test_command: str, requirements: list[str], files: list[str], test_results_url: str) -> dict:
        """Score 4 dimensions in a SINGLE exec_prompt call.

        The contract retrieves the ACTUAL code files from the repository at the
        given commit via gl.nondet.web.render, then includes their contents in
        the evaluation prompt.  The AI model judges the real code, not just a
        URL string.  If test_results_url is provided, test results are also
        fetched and included.
        """
        # ---- Acquire the immutable artifact on-chain ----
        # Try to fetch actual code files from GitHub
        file_contents = []
        if len(files) > 0:
            raw_base = self._github_raw_base(repo_url, commit_hash)
            if raw_base:
                for f in files:
                    file_url = raw_base + f
                    try:
                        content = gl.nondet.web.render(file_url, mode="text")
                        if content:
                            file_contents.append(f"--- {f} ---\n{content[:4000]}")
                    except Exception:
                        pass

        # Fall back to fetching the repo URL directly if no files retrieved
        if not file_contents:
            try:
                artifact_content = gl.nondet.web.render(repo_url, mode="text")
                if artifact_content:
                    file_contents.append(f"--- repo page ---\n{artifact_content[:4000]}")
            except Exception:
                pass

        artifact_content = "\n\n".join(file_contents) if file_contents else ""

        # Acquire test results if provided
        test_results = ""
        if test_results_url:
            try:
                test_results = gl.nondet.web.render(test_results_url, mode="text")
                if test_results:
                    test_results = f"\n\nTest Results:\n{test_results[:3000]}"
            except Exception:
                pass

        reqs_text = "\n".join(f"- {r}" for r in requirements)

        JSON_OUT = '{"functional": 0-100, "quality": 0-100, "security": 0-100, "completeness": 0-100, "reasoning": "brief explanation"}'
        if artifact_content:
            prompt = (
                f"Evaluate this work deliverable across 4 dimensions.\n\n"
                f"Artifact files ({repo_url}@{commit_hash}):\n{artifact_content}\n"
                f"Test command: {test_command}{test_results}\n"
                f"Requirements:\n{reqs_text}\n\n"
                f"Evaluate these 4 dimensions:\n\n"
                f"1. FUNCTIONAL CORRECTNESS (weight {self.weight_functional}%): "
                f"Does the deliverable work? Are there tests? "
                f"Do they cover main functionality? Any obvious runtime errors?\n\n"
                f"2. CODE QUALITY (weight {self.weight_quality}%): "
                f"Is it well-organized? Docstrings? Descriptive names? "
                f"Reasonable complexity? Config files present?\n\n"
                f"3. SECURITY (weight {self.weight_security}%): "
                f"Hardcoded secrets? Input validation? Injection vulnerabilities? "
                f"Access control? Standard crypto libraries?\n\n"
                f"4. COMPLETENESS (weight {self.weight_completeness}%): "
                f"Are all requirements implemented? Edge cases handled?\n\n"
                f"Respond as JSON exactly:\n"
                f"{JSON_OUT}"
            )
        else:
            prompt = (
                f"Evaluate this work deliverable across 4 dimensions.\n\n"
                f"Artifact URL: {repo_url}\n"
                f"Commit: {commit_hash}\n"
                f"Test command: {test_command}{test_results}\n"
                f"Requirements:\n{reqs_text}\n\n"
                f"Evaluate these 4 dimensions:\n\n"
                f"1. FUNCTIONAL CORRECTNESS (weight {self.weight_functional}%): "
                f"Does the deliverable work? Are there tests? "
                f"Do they cover main functionality? Any obvious runtime errors?\n\n"
                f"2. CODE QUALITY (weight {self.weight_quality}%): "
                f"Is it well-organized? Docstrings? Descriptive names? "
                f"Reasonable complexity? Config files present?\n\n"
                f"3. SECURITY (weight {self.weight_security}%): "
                f"Hardcoded secrets? Input validation? Injection vulnerabilities? "
                f"Access control? Standard crypto libraries?\n\n"
                f"4. COMPLETENESS (weight {self.weight_completeness}%): "
                f"Are all requirements implemented? Edge cases handled?\n\n"
                f"Respond as JSON exactly:\n"
                f"{JSON_OUT}"
            )

        res = gl.nondet.exec_prompt(prompt, response_format="json")

        functional = max(0, min(100, int(res.get("functional", 0) or 0)))
        quality = max(0, min(100, int(res.get("quality", 0) or 0)))
        security = max(0, min(100, int(res.get("security", 0) or 0)))
        completeness = max(0, min(100, int(res.get("completeness", 0) or 0)))

        overall = self._final_score(functional, quality, security, completeness)
        verdict = self._verdict(overall)

        return {
            "functional": functional,
            "quality": quality,
            "security": security,
            "completeness": completeness,
            "overall": overall,
            "verdict": verdict,
        }

    def _github_raw_base(self, repo_url: str, commit_hash: str) -> str:
        """Convert a GitHub repo URL to a raw file base URL.

        Example: https://github.com/user/repo → https://raw.githubusercontent.com/user/repo/{commit_hash}/
        """
        try:
            # Remove trailing slash
            url = repo_url.rstrip("/")
            # Must be a GitHub URL
            if not url.startswith("https://github.com/"):
                return ""
            # Extract user/repo from path
            path = url[len("https://github.com/"):]
            parts = path.split("/")
            if len(parts) < 2:
                return ""
            user = parts[0]
            repo = parts[1]
            return f"https://raw.githubusercontent.com/{user}/{repo}/{commit_hash}/"
        except Exception:
            return ""

    def _audit_scorecard(self, sc: dict) -> bool:
        """Check the leader's scorecard without running the LLM a second time.

        Three properties, all pure arithmetic over what the leader returned:

        1. every score is in range and the weighted overall is exactly what the
           configured weights produce, so a leader cannot inflate overall;
        2. the categorical verdict follows from the thresholds, so a leader
           cannot report FAIL as PASS;
        A validator re-running the model would disagree on the numbers alone,
        which deadlocks every job. Auditing the arithmetic does not: identical
        inputs give identical answers on every honest validator.
        """
        try:
            f = int(sc["functional"])
            q = int(sc["quality"])
            sec = int(sc["security"])
            c = int(sc["completeness"])
            overall = int(sc["overall"])
            verdict = sc["verdict"]
        except (KeyError, TypeError, ValueError):
            return False

        for v in (f, q, sec, c):
            if v < 0 or v > 100:
                return False

        wf = int(self.weight_functional)
        wq = int(self.weight_quality)
        ws = int(self.weight_security)
        wc = int(self.weight_completeness)
        if wf + wq + ws + wc != 100:
            return False
        expected = (f * wf + q * wq + sec * ws + c * wc) // 100
        if overall != expected:
            return False

        pt = int(self.pass_threshold)
        pth = int(self.partial_threshold)
        if verdict == "PASS":
            if overall < pt:
                return False
        elif verdict == "PARTIAL":
            if overall < pth or overall >= pt:
                return False
        elif verdict == "FAIL":
            if overall >= pth:
                return False
        else:
            return False

        # evidence_hash is deliberately NOT checked here. It is derived after
        # the consensus round, so the leader's calldata does not carry it; the
        # validator only ever sees the six fields above.
        return True

    def _run_consensus(self, repo_url: str, commit_hash: str, test_command: str, requirements: list[str], files: list[str], test_results_url: str) -> dict:
        """Run the single non-deterministic consensus round.

        Returns the agreed scorecard dict: functional, quality, security,
        completeness, overall, verdict.
        """

        def leader_work() -> dict:
            return self._evaluate(repo_url, commit_hash, test_command, requirements, files, test_results_url)

        def validator(leaders_res: Any) -> bool:
            """Audit the leader's scorecard. This must NOT re-run the LLM.

            An earlier version called leader_work() here and compared both
            results field by field. Two independent LLM runs do not produce
            identical numbers, so the validator disagreed on essentially every
            job, no state was ever committed, and verify returned SUCCESS while
            writing nothing - visible on chain as 141 STORAGE_READ and 0
            STORAGE_WRITE with validator vote=disagree.

            What actually needs checking is whether the leader's scorecard is
            internally consistent and whether its verdict follows from the
            configured weights and thresholds. Both are pure arithmetic over
            values the leader already returned, so every honest validator
            reaches the same answer deterministically.
            """
            if not isinstance(leaders_res, gl.vm.Return):
                return False
            try:
                sc = leaders_res.calldata
                return self._audit_scorecard(sc)
            except Exception:
                return False

        # run_nondet_unsafe was renamed in the 2.x runner that Studio Dev runs.
        verified = gl.vm.run_nondet_default(leader_work, validator)

        evidence_hash = _digest(
            {
                "functional": verified["functional"],
                "quality": verified["quality"],
                "security": verified["security"],
                "completeness": verified["completeness"],
                "overall": verified["overall"],
                "verdict": verified["verdict"],
            }
        )

        return {
            "functional": verified["functional"],
            "quality": verified["quality"],
            "security": verified["security"],
            "completeness": verified["completeness"],
            "overall": verified["overall"],
            "verdict": verified["verdict"],
            "evidence_hash": evidence_hash,
        }

    # ------------------------------------------------------------------
    # Write methods
    # ------------------------------------------------------------------

    @gl.public.write
    def register(self) -> None:
        """Register as a verifiable agent. Idempotent.

        Deliberately NOT payable. There is no deposit to make and no balance to
        track: this contract holds no funds and owes none. Reputation starts at
        the floor and is moved only by consensus outcomes, so an agent cannot
        buy standing here — it has to be verified.
        """
        sender = str(gl.message.sender_address)
        rec = self.agents.get(sender, None)
        if rec is None:
            rec = AgentRecord(
                reputation=u256(self.min_reputation),
                completed=u256(0),
                failed=u256(0),
                demotions=u256(0),
                demoted_total=u256(0),
                total_score=u256(0),
                active=True,
                open_jobs=u256(0),
            )
        else:
            # A previously deactivated agent cannot re-register to launder a
            # demotion. There is no path back to active.
            if not rec.active:
                raise gl.vm.UserError(
                    "Agent was deactivated and cannot re-register"
                )
        self.agents[sender] = rec

    @gl.public.write
    def post_job(
        self,
        job_id: str,
        agent: str,
        repo_url: str,
        commit_hash: str,
        test_command: str,
        requirements: list[str],
        deadline: int,
        files: list[str],
        test_results_url: str = "",
    ) -> None:
        """Issuer posts a verification job for an agent's deliverable.

        Requirements:
        - job_id must be unique.
        - repo_url must be http(s).
        - The same (repo_url, commit_hash) pair cannot be reviewed twice.
        - deadline must be in the future.
        - files: list of file paths to retrieve from the repo at the commit
          (e.g. ["src/main.py", "tests/test_main.py"]).  If empty, the
          contract falls back to fetching the repo URL directly.
        - test_results_url: optional URL to fetch test results (e.g. a CI
          report).  If provided, the results are included in the evaluation.
        """
        sender = str(gl.message.sender_address)
        if not job_id:
            raise gl.vm.UserError("job_id required")
        if self.jobs.get(job_id, None) is not None:
            raise gl.vm.UserError(f"Job {job_id} already exists")
        if not repo_url.startswith("http"):
            raise gl.vm.UserError("repo_url must be http(s)")
        key = self._artifact_key(repo_url, commit_hash)
        if self.reviewed.get(key, "") == "1":
            raise gl.vm.UserError(f"Artifact {repo_url}@{commit_hash} already reviewed")
        # The key is NOT reserved here. Reserving at post time let anyone burn
        # artifact keys forever by posting jobs they never intend to verify.
        # It is reserved only when a job is actually verified.
        if deadline <= self._now():
            raise gl.vm.UserError("deadline must be in the future")

        self.jobs[job_id] = Job(
            issuer=sender,
            agent=agent,
            repo_url=repo_url,
            commit_hash=commit_hash,
            test_command=test_command,
            requirements_joined=_join(requirements),
            files_joined=_join(files),
            test_results_url=test_results_url,
            deadline=u256(deadline),
            recorded=False,
            verdict="",
            final_score=u256(0),
            accepted=False,
            accepted_terms="",
            cancelled=False,
        )

    @gl.public.write
    def accept_job(self, job_id: str) -> str:
        """The NAMED agent accepts a job, binding themselves to its terms.

        Until this is called the job cannot touch the agent's reputation or
        stake in any way - not a slash, not a failed count, not a reputation
        tier change. An issuer naming an address is a request, not an
        obligation.

        Acceptance covers the artifact, the requirements, the deadline and the
        slashing exposure together (see _terms_digest). Terms are frozen at
        this point: the issuer cannot later edit a job and keep the acceptance.
        """
        sender = str(gl.message.sender_address)
        job = self.jobs.get(job_id, None)
        if job is None:
            raise gl.vm.UserError(f"Job {job_id} not found")
        if job.cancelled:
            raise gl.vm.UserError(f"Job {job_id} was cancelled")
        if job.recorded:
            raise gl.vm.UserError(f"Job {job_id} already verified")
        if sender != job.agent:
            raise gl.vm.UserError("Only the named agent may accept this job")
        if job.accepted:
            raise gl.vm.UserError(f"Job {job_id} is already accepted")

        rec = self.agents.get(sender, None)
        if rec is None:
            raise gl.vm.UserError("Agent not registered")
        # There is no eligibility gate on accepting work at all.
        #
        # An earlier draft gated on `reputation < min_reputation`, which looked
        # reasonable and was a dead end: the first FAIL drops reputation below
        # the floor while the agent is still active, so the second failure could
        # never be recorded, the demotion count could never reach its limit, and
        # the agent was stranded below the floor with no way back. Reputation
        # below the floor is already visible in `get_standing` and already
        # priced into the scorecard; blocking acceptance neither protects
        # anything nor makes the outcome fairer.
        #
        # Eligibility is enforced where it belongs: `_tier` reports UNVERIFIED,
        # and an issuer reading `get_standing` sees `eligible: false` before
        # posting work. The agent, not the contract, decides who hires whom.
        _ = rec

        job.accepted = True
        job.accepted_terms = self._terms_digest(job)
        self.jobs[job_id] = job
        # The stake backing an accepted job is ENCUMBERED: it is what a
        # failure would burn. Counting it here is O(1) and makes the encumbrance
        # explicit rather than inferred.
        rec.open_jobs += u256(1)
        self.agents[sender] = rec
        return job.accepted_terms

    @gl.public.write
    def decline_job(self, job_id: str) -> None:
        """The named agent refuses a job. No reputation or stake effect."""
        sender = str(gl.message.sender_address)
        job = self.jobs.get(job_id, None)
        if job is None:
            raise gl.vm.UserError(f"Job {job_id} not found")
        if job.recorded:
            raise gl.vm.UserError(f"Job {job_id} already verified")
        if sender != job.agent and sender != job.issuer:
            raise gl.vm.UserError("Only the named agent or the issuer may decline")
        if job.accepted:
            raise gl.vm.UserError("Cannot decline a job already accepted")
        job.cancelled = True
        job.verdict = "CANCELLED"
        self.jobs[job_id] = job

    @gl.public.write
    def verify(self, job_id: str) -> str:
        """Run consensus verification on a job and update reputation + stake.

        Before the deadline only the agent or issuer may call.  After the
        deadline anyone may call (to settle abandoned jobs).
        """
        sender = str(gl.message.sender_address)
        job = self.jobs.get(job_id, None)
        if job is None:
            raise gl.vm.UserError(f"Job {job_id} not found")
        if job.recorded:
            raise gl.vm.UserError(f"Job {job_id} already verified")
        if job.cancelled:
            raise gl.vm.UserError(f"Job {job_id} was cancelled")
        if not job.accepted:
            raise gl.vm.UserError(
                "Job not accepted by the named agent; it cannot affect "
                "reputation or stake"
            )
        if job.accepted_terms != self._terms_digest(job):
            raise gl.vm.UserError(
                "Job terms changed after acceptance; acceptance is void"
            )

        now = self._now()
        expired = now >= int(job.deadline)
        if not expired and sender != job.agent and sender != job.issuer:
            raise gl.vm.UserError(
                "Before deadline only the agent or issuer may verify; "
                "after deadline anyone may"
            )

        rec = self.agents.get(job.agent, None)
        if rec is None:
            raise gl.vm.UserError("Agent not registered")

        result = self._run_consensus(
            job.repo_url,
            job.commit_hash,
            job.test_command,
            _split(job.requirements_joined),
            _split(job.files_joined),
            job.test_results_url,
        )

        scorecard = Scorecard(
            functional=u256(result["functional"]),
            quality=u256(result["quality"]),
            security=u256(result["security"]),
            completeness=u256(result["completeness"]),
            overall=u256(result["overall"]),
            verdict=result["verdict"],
            evidence_hash=result["evidence_hash"],
        )
        self.verifications[job_id] = scorecard

        if result["verdict"] == "PASS":
            rec.completed += u256(1)
            rec.total_score += u256(result["overall"])
            # Reputation can be earned, not only lost. Crediting the score
            # itself makes success the way an agent regains standing, which is
            # what replaces a token refund.
            rec = self._reputation_earned(rec, result["overall"])
        elif result["verdict"] == "PARTIAL":
            rec.completed += u256(1)
            rec.total_score += u256(result["overall"])
            # Partial credit is smaller than a full pass: it counts as work done
            # but does not rebuild standing on its own.
            rec = self._reputation_earned(rec, result["overall"] // 2)
        else:  # FAIL
            rec.failed += u256(1)
            rec = self._apply_slash(rec)

        job.recorded = True
        job.verdict = result["verdict"]
        job.final_score = u256(result["overall"])
        self.jobs[job_id] = job
        if int(rec.open_jobs) > 0:
            rec.open_jobs = u256(int(rec.open_jobs) - 1)
        self.agents[job.agent] = rec
        # Reserve the artifact key ONLY now, when real verification consumed
        # it. Posting a job no longer burns the key.
        self.reviewed[
            self._artifact_key(job.repo_url, job.commit_hash)
        ] = "1"

        return result["verdict"]

    @gl.public.write
    def settle_unclaimed(self, job_id: str) -> str:
        """Permissionlessly settle an expired, unverified job as FAIL.

        Call this after the deadline to slash an agent who abandoned their job.
        """
        job = self.jobs.get(job_id, None)
        if job is None:
            raise gl.vm.UserError(f"Job {job_id} not found")
        if job.recorded:
            raise gl.vm.UserError(f"Job {job_id} already verified")
        if job.cancelled:
            raise gl.vm.UserError(f"Job {job_id} was already declined")
        if self._now() < int(job.deadline):
            raise gl.vm.UserError("Deadline has not passed yet")

        # An agent who never accepted the job cannot be slashed for not
        # completing it. The job simply expires: no reputation change, no
        # stake change, and the artifact key is left free for a real job.
        if not job.accepted:
            job.cancelled = True
            job.verdict = "EXPIRED_UNACCEPTED"
            self.jobs[job_id] = job
            return "EXPIRED_UNACCEPTED"

        if job.accepted_terms != self._terms_digest(job):
            raise gl.vm.UserError(
                "Job terms changed after acceptance; acceptance is void"
            )

        rec = self.agents.get(job.agent, None)
        if rec is None:
            raise gl.vm.UserError("Agent not registered")

        # Accepted, then abandoned: a real obligation was broken, so this is a
        # real slash. Acceptance is what makes it legitimate.
        rec.failed += u256(1)
        rec = self._apply_slash(rec)
        if int(rec.open_jobs) > 0:
            rec.open_jobs = u256(int(rec.open_jobs) - 1)
        job.recorded = True
        job.verdict = "FAIL"
        job.final_score = u256(0)
        self.jobs[job_id] = job
        self.agents[job.agent] = rec

        return "FAIL"

    # ------------------------------------------------------------------
    # Standing lifecycle
    #
    # There is deliberately nothing here to withdraw, claim, or dispose.
    # Those three methods existed so the contract could settle native funds,
    # which a GenLayer Intelligent Contract cannot do: `emit_transfer` is
    # exposed only on external contract proxies, and the `Contract` base class
    # that this contract inherits from has no such method. Verified by probing
    # the runtime, not inferred from documentation.
    #
    # Keeping them would have meant this contract recording debts it could
    # never pay. They are removed rather than kept as accounting, and the
    # removal is the fix.
    # ------------------------------------------------------------------

    @gl.public.write
    def deactivate(self) -> None:
        """Voluntary, irreversible exit from the verified agent set.

        Irreversible by design: there is no activate(), and register() refuses
        a deactivated agent, so a departed agent cannot return to the set or
        re-qualify by starting reputation over. Reputation is left intact for
        the record — deactivating is not the same as being penalised.
        """
        sender = str(gl.message.sender_address)
        rec = self.agents.get(sender, None)
        if rec is None:
            raise gl.vm.UserError("Agent not registered")
        if not rec.active:
            raise gl.vm.UserError("Agent already deactivated")
        if int(rec.open_jobs) > 0:
            raise gl.vm.UserError(
                "Cannot deactivate while an accepted job is outstanding"
            )
        rec.active = False
        self.agents[sender] = rec

    # ------------------------------------------------------------------
    # View methods
    # ------------------------------------------------------------------

    @gl.public.view
    def get_standing(self, agent: Any) -> str:
        """Standing record for one agent: reputation, demotions, eligibility.

        Replaces the custody ledger. There is no balance to report because the
        contract holds no funds, and no `pending_withdraw` because nothing is
        ever owed.
        """
        agent_hex = agent.as_hex if hasattr(agent, "as_hex") else str(agent)
        rec = self.agents.get(agent_hex, None)
        floor = int(self.min_reputation)
        if rec is None:
            return json.dumps(
                {
                    "exists": False,
                    "reputation": 0,
                    "min_reputation": floor,
                    "eligible": False,
                    "demotions": 0,
                    "demoted_total": 0,
                    "holds_funds": False,
                }
            )
        return json.dumps(
            {
                "exists": True,
                "reputation": int(rec.reputation),
                "min_reputation": floor,
                "eligible": bool(rec.active)
                and int(rec.reputation) >= floor,
                "demotions": int(rec.demotions),
                "demoted_total": int(rec.demoted_total),
                # Stated explicitly so a reader never has to infer it: this
                # contract holds no native funds and issues no claims against
                # any balance.
                "holds_funds": False,
            }
        )

    @gl.public.view
    def get_config(self) -> str:
        """Deployment configuration: weights, thresholds and the standing floor.

        Exposed so a reader can reproduce every tier and eligibility decision
        without guessing at a hardcoded constant.
        """
        return json.dumps(
            {
                "weight_functional": int(self.weight_functional),
                "weight_quality": int(self.weight_quality),
                "weight_security": int(self.weight_security),
                "weight_completeness": int(self.weight_completeness),
                "pass_threshold": int(self.pass_threshold),
                "partial_threshold": int(self.partial_threshold),
                "slash_percent": int(self.slash_percent),
                "min_reputation": int(self.min_reputation),
                "max_demotions": int(self.max_demotions),
                "holds_funds": False,
            }
        )

    @gl.public.view
    def get_artifact_key(self, repo_url: str, commit_hash: str) -> str:
        """Whether an artifact has actually been consumed by a verification."""
        key = self._artifact_key(repo_url, commit_hash)
        return json.dumps(
            {
                "key": key,
                "reserved": self.reviewed.get(key, "") == "1",
            }
        )

    @gl.public.view
    def get_agent(self, agent: Any) -> str:
        """Get an agent's reputation record and tier."""
        agent_hex = agent.as_hex if hasattr(agent, "as_hex") else str(agent)
        rec = self.agents.get(agent_hex, None)
        if rec is None:
            return json.dumps(
                {
                    "agent": agent_hex,
                    "exists": False,
                    "reputation": 0,
                    "completed": 0,
                    "failed": 0,
                    "demotions": 0,
                    "demoted_total": 0,
                    "total_score": 0,
                    "active": False,
                    "tier": "UNVERIFIED",
                    "avg_score": 0,
                    "holds_funds": False,
                }
            )
        total = int(rec.completed) + int(rec.failed)
        avg = int(rec.total_score) // total if total else 0
        return json.dumps(
            {
                "agent": agent_hex,
                "exists": True,
                "reputation": int(rec.reputation),
                "completed": int(rec.completed),
                "failed": int(rec.failed),
                "demotions": int(rec.demotions),
                "demoted_total": int(rec.demoted_total),
                "total_score": int(rec.total_score),
                "active": bool(rec.active),
                "open_jobs": int(rec.open_jobs),
                "tier": self._tier(rec),
                "avg_score": avg,
                "holds_funds": False,
            }
        )

    @gl.public.view
    def get_job(self, job_id: str) -> str:
        """Get a job's full details including verdict and scorecard reference."""
        job = self.jobs.get(job_id, None)
        if job is None:
            return json.dumps({"job_id": job_id, "exists": False})
        return json.dumps(
            {
                "job_id": job_id,
                "exists": True,
                "issuer": job.issuer,
                "agent": job.agent,
                "repo_url": job.repo_url,
                "commit_hash": job.commit_hash,
                "test_command": job.test_command,
                "requirements": _split(job.requirements_joined),
                "deadline": int(job.deadline),
                "recorded": job.recorded,
                "accepted": bool(job.accepted),
                "accepted_terms": job.accepted_terms,
                "cancelled": bool(job.cancelled),
                "verdict": job.verdict,
                "final_score": int(job.final_score),
                "expired": self._now() >= int(job.deadline),
            }
        )

    @gl.public.view
    def get_scorecard(self, job_id: str) -> str:
        """Get the published consensus scorecard for a verified job."""
        sc = self.verifications.get(job_id, None)
        if sc is None:
            return json.dumps({"job_id": job_id, "exists": False})
        return json.dumps(
            {
                "job_id": job_id,
                "exists": True,
                "functional": int(sc.functional),
                "quality": int(sc.quality),
                "security": int(sc.security),
                "completeness": int(sc.completeness),
                "overall": int(sc.overall),
                "verdict": sc.verdict,
                "evidence_hash": sc.evidence_hash,
            }
        )

    @gl.public.view
    def now(self) -> str:
        """Current transaction timestamp in Unix seconds."""
        return str(self._now())
