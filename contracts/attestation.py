"""Attestation — Verifiable Intelligence for Trusted Yardsticks. Rationale in DESIGN.md."""
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from genlayer import *  # brings in gl, allow_storage, TreeMap, Keccak256, u256, Address
def _join(items: list[str]) -> str:
    return "\n".join(items)
def _split(joined: str) -> list[str]:
    return [x for x in joined.split("\n") if x] if joined else []
def _digest(payload: dict) -> str:
    return Keccak256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
WEIGHT_FUNCTIONAL = 40
WEIGHT_QUALITY = 25
WEIGHT_SECURITY = 25
WEIGHT_COMPLETENESS = 10
PASS_THRESHOLD = 70
PARTIAL_THRESHOLD = 40
SLASH_PERCENT = 10
MIN_REPUTATION = 300
MAX_DEMOTIONS = 3
SCORE_TOLERANCE = 8
@allow_storage
@dataclass
class AgentRecord:
    reputation: u256
    completed: u256
    failed: u256
    demotions: u256
    demoted_total: u256
    total_score: u256
    active: bool
    open_jobs: u256
@allow_storage
@dataclass
class Scorecard:
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
class Attestation(gl.Contract):
    agents: TreeMap[str, AgentRecord]
    jobs: TreeMap[str, Job]
    verifications: TreeMap[str, Scorecard]
    reviewed: TreeMap[str, str]
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
        wf = int(weight_functional); wq = int(weight_quality); ws = int(weight_security); wc = int(weight_completeness)
        if wf + wq + ws + wc != 100:
            raise gl.vm.UserError(f"Weights must sum to 100 (got {wf}+{wq}+{ws}+{wc})")
        pt = int(pass_threshold); pth = int(partial_threshold)
        if not (0 <= pt <= 100):
            raise gl.vm.UserError("pass_threshold must be 0-100")
        if not (0 <= pth <= pt):
            raise gl.vm.UserError("partial_threshold must be 0-pass_threshold")
        self.weight_functional = u256(weight_functional); self.weight_quality = u256(weight_quality); self.weight_security = u256(weight_security); self.weight_completeness = u256(weight_completeness); self.pass_threshold = u256(pass_threshold); self.partial_threshold = u256(partial_threshold); self.slash_percent = u256(slash_percent); self.min_reputation = min_reputation_wei; self.max_demotions = u256(max_demotions_)
    def _artifact_key(self, repo_url: str, commit_hash: str) -> str:
        return f"{repo_url.strip().lower()}@{commit_hash.strip().lower()}"
    def _terms_digest(self, job: Job) -> str:
        return _digest({"artifact": self._artifact_key(job.repo_url, job.commit_hash), "files": _split(job.files_joined), "test_command": job.test_command, "requirements": _split(job.requirements_joined), "test_results_url": job.test_results_url, "deadline": int(job.deadline), "slash_percent": int(self.slash_percent), "min_reputation": int(self.min_reputation), "max_demotions": int(self.max_demotions)})
    def _now(self) -> int:
        return int(datetime.now(timezone.utc).timestamp())
    def _tier(self, rec: AgentRecord) -> str:
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
        rep = int(rec.reputation)
        pct = int(self.slash_percent)
        drop = (rep * pct) // 100
        if drop > rep:
            drop = rep
        rec.reputation = u256(rep - drop)
        rec.demotions += u256(1)
        rec.demoted_total += u256(drop)
        if int(rec.demotions) >= int(self.max_demotions):
            rec.active = False
        return rec
    def _reputation_earned(self, rec: AgentRecord, credit: int) -> AgentRecord:
        rec.reputation += u256(credit)
        if not rec.active and int(rec.reputation) >= int(self.min_reputation):
            rec.active = True
        return rec
    def _final_score(self, functional: int, quality: int, security: int, completeness: int) -> int:
        return (
            functional * int(self.weight_functional)
            + quality * int(self.weight_quality)
            + security * int(self.weight_security)
            + completeness * int(self.weight_completeness)
        ) // 100
    def _verdict(self, overall: int) -> str:
        if overall >= int(self.pass_threshold):
            return "PASS"
        if overall >= int(self.partial_threshold):
            return "PARTIAL"
        return "FAIL"
    def _github_raw_base(self, repo_url: str, commit_hash: str) -> str:
        try:
            u = repo_url.rstrip("/")
            if not u.startswith("https://github.com/"):
                return ""
            p = u[19:].split("/")
            return f"https://raw.githubusercontent.com/{p[0]}/{p[1]}/{commit_hash}/" if len(p) >= 2 else ""
        except Exception:
            return ""
    def _evaluate(self, repo_url: str, commit_hash: str, test_command: str, requirements: list[str], files: list[str], test_results_url: str) -> dict:
        def fetch(u):
            try:
                return gl.nondet.web.render(u, mode="text") or ""
            except Exception:
                return ""
        file_contents = []
        raw_base = self._github_raw_base(repo_url, commit_hash) if files else ""
        for f in files:
            if raw_base:
                c = fetch(raw_base + f)
                if c:
                    file_contents.append(f"--- {f} ---\n{c[:4000]}")
        if not file_contents:
            c = fetch(repo_url)
            if c:
                file_contents.append(f"--- repo page ---\n{c[:4000]}")
        artifact_content = "\n\n".join(file_contents)
        test_results = f"\n\nTest Results:\n{fetch(test_results_url)[:3000]}" if test_results_url else ""
        reqs_text = "\n".join(f"- {r}" for r in requirements)
        head = f"Files ({repo_url}@{commit_hash}):\n{artifact_content}" if artifact_content else f"URL: {repo_url} Commit: {commit_hash}"
        prompt = (f"Score this deliverable 0-100 on 4 dims.\n{head}\nTest: {test_command}{test_results}\nReqs:\n{reqs_text}\n"
            f"1 FUNCTIONAL(w{self.weight_functional}): works?tests?errors? 2 QUALITY(w{self.weight_quality}): organized?docs?names? 3 SECURITY(w{self.weight_security}): secrets?validation?injection? 4 COMPLETENESS(w{self.weight_completeness}): all reqs?edge cases?\n"
            f'JSON exactly: {{"functional":0-100,"quality":0-100,"security":0-100,"completeness":0-100,"reasoning":"brief"}}')
        res = gl.nondet.exec_prompt(prompt, response_format="json")
        functional = max(0, min(100, int(res.get("functional", 0) or 0))); quality = max(0, min(100, int(res.get("quality", 0) or 0))); security = max(0, min(100, int(res.get("security", 0) or 0))); completeness = max(0, min(100, int(res.get("completeness", 0) or 0)))
        overall = self._final_score(functional, quality, security, completeness)
        verdict = self._verdict(overall)
        return {"functional": functional, "quality": quality, "security": security, "completeness": completeness, "overall": overall, "verdict": verdict}
    def _audit_scorecard(self, sc: dict) -> bool:
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
        if overall != (f * wf + q * wq + sec * ws + c * wc) // 100:
            return False
        pt = int(self.pass_threshold)
        pth = int(self.partial_threshold)
        if verdict == "PASS":
            return overall >= pt
        if verdict == "PARTIAL":
            return pth <= overall < pt
        if verdict == "FAIL":
            return overall < pth
        return False
    def _run_consensus(self, repo_url: str, commit_hash: str, test_command: str, requirements: list[str], files: list[str], test_results_url: str) -> dict:
        def leader_work() -> dict:
            return self._evaluate(repo_url, commit_hash, test_command, requirements, files, test_results_url)
        def validator(leaders_res: Any) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                try:
                    leader_work()
                    return False
                except gl.vm.UserError:
                    return True
                except Exception:
                    return False
            try:
                leader_sc = leaders_res.calldata
                mine = leader_work()
            except Exception:
                return False
            if not self._audit_scorecard(leader_sc):
                return False
            for dim in ("functional", "quality", "security", "completeness"):
                if abs(int(mine[dim]) - int(leader_sc[dim])) > SCORE_TOLERANCE:
                    return False
            return mine["verdict"] == leader_sc["verdict"]
        verified = gl.vm.run_nondet_default(leader_work, validator)
        v = verified
        evidence_hash = _digest({"functional": v["functional"], "quality": v["quality"], "security": v["security"], "completeness": v["completeness"], "overall": v["overall"], "verdict": v["verdict"]})
        return {"functional": v["functional"], "quality": v["quality"], "security": v["security"], "completeness": v["completeness"], "overall": v["overall"], "verdict": v["verdict"], "evidence_hash": evidence_hash}
    @gl.public.write
    def register(self) -> None:
        sender = str(gl.message.sender_address)
        rec = self.agents.get(sender, None)
        if rec is None:
            rec = AgentRecord(reputation=u256(self.min_reputation), completed=u256(0), failed=u256(0), demotions=u256(0), demoted_total=u256(0), total_score=u256(0), active=True, open_jobs=u256(0))
        elif not rec.active:
            raise gl.vm.UserError("Agent was deactivated and cannot re-register")
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
        if deadline <= self._now():
            raise gl.vm.UserError("deadline must be in the future")
        self.jobs[job_id] = Job(issuer=sender, agent=agent, repo_url=repo_url, commit_hash=commit_hash, test_command=test_command, requirements_joined=_join(requirements), files_joined=_join(files), test_results_url=test_results_url, deadline=u256(deadline), recorded=False, verdict="", final_score=u256(0), accepted=False, accepted_terms="", cancelled=False)
    @gl.public.write
    def accept_job(self, job_id: str) -> str:
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
        job.accepted = True
        job.accepted_terms = self._terms_digest(job)
        self.jobs[job_id] = job
        rec.open_jobs += u256(1)
        self.agents[sender] = rec
        return job.accepted_terms
    @gl.public.write
    def decline_job(self, job_id: str) -> None:
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
        sender = str(gl.message.sender_address)
        job = self.jobs.get(job_id, None)
        if job is None:
            raise gl.vm.UserError(f"Job {job_id} not found")
        if job.recorded:
            raise gl.vm.UserError(f"Job {job_id} already verified")
        if job.cancelled:
            raise gl.vm.UserError(f"Job {job_id} was cancelled")
        if not job.accepted:
            raise gl.vm.UserError("Job not accepted by the named agent")
        if job.accepted_terms != self._terms_digest(job):
            raise gl.vm.UserError("Job terms changed after acceptance; acceptance is void")
        now = self._now()
        expired = now >= int(job.deadline)
        if not expired and sender != job.agent and sender != job.issuer:
            raise gl.vm.UserError("Before deadline only the agent or issuer may verify")
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
        r = result
        scorecard = Scorecard(functional=u256(r["functional"]), quality=u256(r["quality"]), security=u256(r["security"]), completeness=u256(r["completeness"]), overall=u256(r["overall"]), verdict=r["verdict"], evidence_hash=r["evidence_hash"])
        self.verifications[job_id] = scorecard
        if r["verdict"] == "PASS":
            rec.completed += u256(1); rec.total_score += u256(r["overall"]); rec = self._reputation_earned(rec, r["overall"])
        elif r["verdict"] == "PARTIAL":
            rec.completed += u256(1); rec.total_score += u256(r["overall"]); rec = self._reputation_earned(rec, r["overall"] // 2)
        else:
            rec.failed += u256(1); rec = self._apply_slash(rec)
        job.recorded = True; job.verdict = r["verdict"]; job.final_score = u256(r["overall"])
        self.jobs[job_id] = job
        if int(rec.open_jobs) > 0:
            rec.open_jobs = u256(int(rec.open_jobs) - 1)
        self.agents[job.agent] = rec
        self.reviewed[self._artifact_key(job.repo_url, job.commit_hash)] = "1"
        return r["verdict"]
    @gl.public.view
    def get_standing(self, agent: Any) -> str:
        agent_hex = agent.as_hex if hasattr(agent, "as_hex") else str(agent)
        rec = self.agents.get(agent_hex, None)
        floor = int(self.min_reputation)
        if rec is None:
            return json.dumps({"exists": False, "reputation": 0, "min_reputation": floor, "eligible": False, "demotions": 0, "demoted_total": 0})
        return json.dumps({"exists": True, "reputation": int(rec.reputation), "min_reputation": floor, "eligible": bool(rec.active) and int(rec.reputation) >= floor, "demotions": int(rec.demotions), "demoted_total": int(rec.demoted_total)})
    @gl.public.view
    def get_agent(self, agent: Any) -> str:
        h = agent.as_hex if hasattr(agent, "as_hex") else str(agent)
        r = self.agents.get(h, None)
        if r is None:
            return json.dumps({"agent": h, "exists": False, "reputation": 0, "completed": 0, "failed": 0, "demotions": 0, "demoted_total": 0, "total_score": 0, "active": False, "open_jobs": 0, "tier": "UNVERIFIED", "avg_score": 0})
        t = int(r.completed) + int(r.failed)
        return json.dumps({"agent": h, "exists": True, "reputation": int(r.reputation), "completed": int(r.completed), "failed": int(r.failed), "demotions": int(r.demotions), "demoted_total": int(r.demoted_total), "total_score": int(r.total_score), "active": bool(r.active), "open_jobs": int(r.open_jobs), "tier": self._tier(r), "avg_score": int(r.total_score) // t if t else 0})
    @gl.public.view
    def get_job(self, job_id: str) -> str:
        j = self.jobs.get(job_id, None)
        if j is None:
            return json.dumps({"job_id": job_id, "exists": False})
        return json.dumps({"job_id": job_id, "exists": True, "issuer": j.issuer, "agent": j.agent, "repo_url": j.repo_url, "commit_hash": j.commit_hash, "test_command": j.test_command, "requirements": _split(j.requirements_joined), "deadline": int(j.deadline), "recorded": j.recorded, "accepted": bool(j.accepted), "accepted_terms": j.accepted_terms, "cancelled": bool(j.cancelled), "verdict": j.verdict, "final_score": int(j.final_score), "expired": self._now() >= int(j.deadline)})
    @gl.public.view
    def get_scorecard(self, job_id: str) -> str:
        sc = self.verifications.get(job_id, None)
        if sc is None:
            return json.dumps({"job_id": job_id, "exists": False})
        return json.dumps({"job_id": job_id, "exists": True, "functional": int(sc.functional), "quality": int(sc.quality), "security": int(sc.security), "completeness": int(sc.completeness), "overall": int(sc.overall), "verdict": sc.verdict, "evidence_hash": sc.evidence_hash})