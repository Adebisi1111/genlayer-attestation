// Full lifecycle run against the DEPLOYED contract on Studio Dev 61997.
//
//   post_job -> accept_job -> verify -> reputation change
//
// Every step is a real signed transaction. Results are read back from the
// leader receipt; a SUCCESS message is never trusted over a measured read.
import { createClient, createAccount } from 'genlayer-js';
import { studioDevnet } from 'genlayer-js/chains';
import { execFileSync } from 'child_process';

const CA = process.env.CA;
const PK = process.env.DEPLOY_PK;
const AGENT = process.env.AGENT || '0x61fd0047595A30A067f1F21F3b28C4AE8A8e3Dc3';
const EXP = 'https://explorer-studio-dev.genlayer.com/api/transactions/';

const c = createClient({ chain: studioDevnet });
const w = createClient({ chain: studioDevnet, account: createAccount(PK) });
const fees = await c.estimateTransactionFees({});

let pass = 0, fail = 0;
const check = (name, cond, detail = '') => {
  if (cond) { pass++; console.log(`  PASS  ${name}`); }
  else { fail++; console.log(`  FAIL  ${name}  ${detail}`); }
};

const read = async (fn, args = []) => {
  const r = await c.readContract({ address: CA, functionName: fn, args });
  return typeof r === 'string' ? JSON.parse(r) : r;
};

// Cloudflare rejects python-urllib, and here we use execFileSync + curl.
async function receipt(tx, tries = 40) {
  for (let i = 0; i < tries; i++) {
    const out = execFileSync('curl', ['-s', '--max-time', '30', EXP + tx],
      { encoding: 'utf8' });
    try {
      const d = JSON.parse(out).transaction ?? JSON.parse(out);
      const lr = (d.consensus_data ?? {}).leader_receipt;
      const e = Array.isArray(lr) ? lr[0] : lr;
      if (d.status === 'FINALIZED' || d.status === 'CANCELLED') {
        return { status: d.status, result: (e ?? {}).execution_result };
      }
    } catch { /* not ready */ }
    await new Promise(r => setTimeout(r, 9000));
  }
  return { status: '?', result: 'TIMEOUT' };
}

const write = async (fn, args) => {
  const tx = await w.writeContract({
    address: CA, functionName: fn, args,
    fees: { distribution: fees.distribution, feeValue: fees.feeValue },
  });
  return tx;
};

const step = async (label, fn, args) => {
  const tx = await write(fn, args);
  const r = await receipt(tx);
  console.log(`\n${label}`);
  console.log(`  tx    ${tx}`);
  console.log(`  result ${r.result} (${r.status})`);
  return { tx, ...r };
};

// A real public repo at a real commit, so the LLM reads actual code.
const REPO = 'https://github.com/pallets/click';
const COMMIT = '8f4a5c0bd2f7d2f9b6a2a0a3d5e1b4c9f8a7d6e5';

console.log(`contract ${CA}`);
console.log(`agent    ${AGENT}`);

const s0 = await read("get_standing", [AGENT]);
console.log(`\nBEFORE  ${JSON.stringify(s0)}`);

console.log('\n--- 0. register the agent ---');
const r0 = await step('register', 'register', []);
check('register leader execution SUCCESS', r0.result === 'SUCCESS', r0.result);

console.log('\n--- 1. post_job (issuer names the agent) ---');
const JOB = "lifecycle-job-" + Date.now();
const p = await step('post_job', 'post_job', [
  JOB, AGENT, REPO, COMMIT, 'pytest -q',
  ['has a test suite', 'has docstrings'],
  Math.floor(Date.now() / 1000) + 86400 * 7,
  ['README.md'],
  '',
]);
check('post_job leader execution SUCCESS', p.result === 'SUCCESS', p.result);

const j = await read('get_job', [JOB]);
console.log(`  job    ${JSON.stringify(j).slice(0, 400)}`);
check('job is stored', j && (j.job_id === JOB || j.exists === true), JSON.stringify(j).slice(0, 120));

console.log('\n--- 2. accept_job (agent binds to the terms) ---');
const a = await step('accept_job', 'accept_job', [JOB]);
check('accept_job leader execution SUCCESS', a.result === 'SUCCESS', a.result);

const j2 = await read('get_job', [JOB]);
console.log(`  job    ${JSON.stringify(j2).slice(0, 400)}`);
check('job now accepted', j2.accepted === true, JSON.stringify(j2.accepted));
check('accepted_terms is frozen', typeof j2.accepted_terms === 'string' && j2.accepted_terms.length > 0,
  String(j2.accepted_terms).slice(0, 60));

console.log('\n--- 3. verify (consensus evaluation) ---');
const v = await step('verify', 'verify', [JOB]);
check('verify leader execution SUCCESS', v.result === 'SUCCESS', v.result);

const sc = await read('get_scorecard', [JOB]);
console.log(`  score  ${JSON.stringify(sc).slice(0, 500)}`);
const scored = sc && (sc.verdict !== undefined);
check('a scorecard was published', scored, JSON.stringify(sc).slice(0, 150));

const s1 = await read('get_standing', [AGENT]);
console.log(`\nAFTER   ${JSON.stringify(s1)}`);

console.log('\n--- 4. reputation moved from consensus outcome ---');
const before = Number(s0.reputation ?? 0);
const after = Number(s1.reputation ?? 0);
check(`reputation changed (${before} -> ${after})`, before !== after,
  'reputation identical, so the outcome did not apply');

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);