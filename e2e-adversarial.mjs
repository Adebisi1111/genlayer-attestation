// Live adversarial proof for the non-custodial Attestation contract.
//
// Replaces the withdrawn harness, roughly 60% of which proved a withdrawal and
// disposal lifecycle that no longer exists. The parts that proved something
// real are kept and re-pointed at the new model.
//
// Sections:
//   1. unauthorised jobs cannot touch reputation            (kept)
//   2. unaccepted jobs do not reserve artifact keys        (kept)
//   3. accepted jobs are tracked and block deactivation   (kept, reworded)
//   4. NO CUSTODY - registration takes no value            (new)
//   5. obligations can still be accepted below the floor  (new)
import { createClient, createAccount } from 'genlayer-js';
import { studioDevnet } from 'genlayer-js/chains';

const C = process.env.ON_ADDR;
if (!C) { console.error('ON_ADDR not set'); process.exit(1); }

const cli = {
  issuer: createClient({ chain: studioDevnet, account: createAccount(process.env.KI) }),
  agent: createClient({ chain: studioDevnet, account: createAccount(process.env.KA) }),
  other: createClient({ chain: studioDevnet, account: createAccount(process.env.KB) }),
};
const A = {
  issuer: createAccount(process.env.KI).address,
  agent: createAccount(process.env.KA).address,
  other: createAccount(process.env.KB).address,
};
const REPO = 'https://github.com/genlayerlabs/genlayer-js';
const DEADLINE = 9999999999;

let pass = 0, fail = 0;
const check = (n, c, d = '') => { c ? (pass++, console.log(`  PASS  ${n}`)) : (fail++, console.log(`  FAIL  ${n} ${d}`)); };

async function execResult(hash) {
  // The explorer intermittently serves an HTML error page and may not have
  // indexed the tx yet; retry rather than misreading that as a contract result.
  for (let i = 0; i < 20; i++) {
    try {
      const r = await fetch(`https://explorer-studio-dev.genlayer.com/api/transactions/${hash}`);
      const t = await r.text();
      if (t.trim().startsWith('<')) throw new Error('html error page');
      const j = JSON.parse(t);
      const lr = j?.transaction?.consensus_data?.leader_receipt;
      const e = Array.isArray(lr) ? lr[0] : lr;
      if (e?.execution_result && e.execution_result !== 'UNKNOWN') return e.execution_result;
    } catch (err) { /* retry */ }
    await new Promise((s) => setTimeout(s, 4000));
  }
  return 'UNKNOWN';
}

async function write(k, fn, args, opts = {}) {
  const hash = await cli[k].writeContract({ address: C, functionName: fn, args, ...opts });
  await cli[k].waitForTransactionReceipt({ hash, waitUntil: 'decided', retries: 300, interval: 3000 });
  const exec = await execResult(hash);
  return { hash, exec, ok: exec === 'SUCCESS' };
}

async function j(k, fn, args) {
  for (let i = 0; i < 15; i++) {
    try { return JSON.parse(await cli[k].readContract({ address: C, functionName: fn, args })); }
    catch (e) { await new Promise((s) => setTimeout(s, 3000)); }
  }
  throw new Error(`read ${fn} failed after retries`);
}

const post = (k, id, agent, commit) =>
  write(k, 'post_job', [id, agent, REPO, commit, 'npm test', ['has tests'], DEADLINE, ['README.md'], '']);

async function main() {
  const stamp = Date.now();

  const cfg = await j('agent', 'get_config', []);
  console.log(`\n  config -> ${JSON.stringify(cfg)}`);

  console.log('\n=== NO CUSTODY: registration takes no value ===');
  const reg = await write('agent', 'register', []);
  check('register() succeeds with nothing attached', reg.ok, `exec=${reg.exec}`);
  const reg2 = await write('other', 'register', []);
  check('second agent registers the same way', reg2.ok, `exec=${reg2.exec}`);

  const r0 = await j('agent', 'get_agent', [A.agent]);
  console.log(`  agent -> ${JSON.stringify(r0)}`);
  check('agent record holds no funds', r0.holds_funds === false, JSON.stringify(r0));
  check('reputation begins at the floor', r0.reputation === cfg.min_reputation, JSON.stringify(r0));
  check('no custody field exists at all', r0.staked === undefined, JSON.stringify(r0));

  console.log('\n=== 1. unauthorised jobs cannot touch reputation ===');
  const idA = `a1-${stamp}`;
  const p1 = await post('issuer', idA, A.agent, `c1-${stamp}`);
  check('issuer posts a job naming the agent', p1.ok, `exec=${p1.exec}`);

  const acc3 = await write('other', 'accept_job', [idA]);
  check('a third party cannot accept it', acc3.exec !== 'SUCCESS', `exec=${acc3.exec}`);

  const acc2 = await write('issuer', 'accept_job', [idA]);
  check("the ISSUER cannot accept on the agent's behalf", acc2.exec !== 'SUCCESS', `exec=${acc2.exec}`);

  const ver = await write('issuer', 'verify', [idA]);
  check('verify refuses an unaccepted job', ver.exec !== 'SUCCESS', `exec=${ver.exec}`);

  const rec = await j('agent', 'get_agent', [A.agent]);
  check('reputation untouched', rec.completed === 0 && rec.failed === 0, JSON.stringify(rec));
  check('no demotion recorded', rec.demotions === 0, JSON.stringify(rec));
  check('agent still active', rec.active === true, JSON.stringify(rec));

  console.log('\n=== 2. unaccepted jobs do not reserve artifact keys ===');
  const repoU = `${REPO}/unaccepted-${stamp}`;
  const commitU = `cu-${stamp}`;
  const k0 = await j('agent', 'get_artifact_key', [repoU, commitU]);
  check('artifact key free before posting', k0.reserved === false, JSON.stringify(k0));

  for (let i = 0; i < 3; i++) {
    await post('issuer', `squ-${i}-${stamp}`, A.agent, commitU);
  }
  const k1 = await j('agent', 'get_artifact_key', [repoU, commitU]);
  check('still free after 3 unverified posts', k1.reserved === false, JSON.stringify(k1));

  console.log('\n=== 3. an accepted job is a tracked obligation ===');
  const idB = `a2-${stamp}`;
  const p2 = await post('other', idB, A.other, `c2-${stamp}`);
  check('job posted for the second agent', p2.ok, `exec=${p2.exec}`);
  const ace = await write('other', 'accept_job', [idB]);
  check('second agent accepts', ace.ok, `exec=${ace.exec}`);

  const o1 = await j('other', 'get_agent', [A.other]);
  check('open obligation is tracked on-chain', o1.open_jobs === 1, JSON.stringify(o1));

  const de = await write('other', 'deactivate', []);
  check('cannot deactivate with an accepted job open', de.exec !== 'SUCCESS', `exec=${de.exec}`);

  console.log('\n=== 4. standing is explicit about holding nothing ===');
  const standing = await j('other', 'get_standing', [A.other]);
  console.log(`  standing -> ${JSON.stringify(standing)}`);
  check('holds_funds is false on-chain', standing.holds_funds === false, JSON.stringify(standing));
  check('no pending-withdrawal concept', standing.pending_withdraw === undefined, JSON.stringify(standing));
  check('eligibility is reported', typeof standing.eligible === 'boolean', JSON.stringify(standing));
  check('an agent with no track record is UNVERIFIED', o1.tier === 'UNVERIFIED', JSON.stringify(o1));

  console.log('\n=== 5. obligations can still be accepted below the floor ===');
  // The withdrawn harness could not express this: its acceptance gate blocked
  // exactly this, which made a demotion permanent in practice.
  const limit = cfg.max_demotions;
  let accepted = 0;
  for (let i = 1; i <= limit; i++) {
    const id = `d${i}-${stamp}`;
    const pd = await post('other', id, A.other, `cd${i}-${stamp}`);
    if (!pd.ok) { check(`post ${id}`, false, `exec=${pd.exec}`); break; }
    const ad = await write('other', 'accept_job', [id]);
    if (ad.ok) accepted++;
    else { check(`accept ${id}`, false, `exec=${ad.exec}`); break; }
  }
  const od = await j('other', 'get_agent', [A.other]);
  console.log(`  accepted ${accepted}/${limit}; open_jobs=${od.open_jobs}, active=${od.active}`);
  check(`all ${limit} obligations were accepted`, accepted === limit, `accepted=${accepted}`);
  check('every obligation is tracked', od.open_jobs === limit, JSON.stringify(od));
  check('registration of one agent cannot open another\'s obligations',
    (await j('agent', 'get_agent', [A.agent])).open_jobs === 0, '');

  console.log('\n  tx hashes:');
  for (const [n, r] of Object.entries({ reg, p1, ace, p2 })) {
    console.log(`    ${n.padEnd(6)} ${r.hash}`);
  }

  console.log(`\n=== RESULT: ${pass} passed, ${fail} failed ===`);
  process.exit(fail === 0 ? 0 : 1);
}

main().catch((e) => { console.error('HARNESS FAIL:', e.message); console.error(e.stack); process.exit(1); });