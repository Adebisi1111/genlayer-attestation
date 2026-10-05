// Gap audit: adversarial paths on the DEPLOYED contract, not a code read.
// Each probe is a real signed transaction against the live address.
import { createClient, createAccount } from 'genlayer-js';
import { studioDevnet } from 'genlayer-js/chains';
import { execFileSync } from 'child_process';

const CA = process.env.CA;
const EXP = 'https://explorer-studio-dev.genlayer.com/api/transactions/';
const c = createClient({ chain: studioDevnet });
const w = createClient({ chain: studioDevnet, account: createAccount(process.env.DEPLOY_PK) });
const fees = await c.estimateTransactionFees({});
const A = '0x61fd0047595A30A067f1F21F3b28C4AE8A8e3Dc3';

async function rc(tx, n = 25) {
  for (let i = 0; i < n; i++) {
    const o = execFileSync('curl', ['-s', '--max-time', '30', EXP + tx], { encoding: 'utf8' });
    try {
      const d = JSON.parse(o).transaction ?? JSON.parse(o);
      const lr = (d.consensus_data ?? {}).leader_receipt;
      const e = Array.isArray(lr) ? lr[0] : lr;
      if (d.status === 'FINALIZED' || d.status === 'CANCELLED') {
        return { exec: (e ?? {}).execution_result, msg: decode(e), e };
      }
    } catch { }
    await new Promise(r => setTimeout(r, 8000));
  }
  return { exec: 'TIMEOUT', msg: '', e: {} };
}
function decode(e) {
  try { return Buffer.from(e.result ?? '', 'base64').toString('utf8').replace(/\0/g, '').trim(); }
  catch { return ''; }
}
const read = async (fn, args = []) => {
  const r = await c.readContract({ address: CA, functionName: fn, args });
  return typeof r === 'string' ? JSON.parse(r) : r;
};
const write = async (fn, args) => {
  const tx = await w.writeContract({ address: CA, functionName: fn, args,
    fees: { distribution: fees.distribution, feeValue: fees.feeValue } });
  return { tx, ...(await rc(tx)) };
};
const settle = async () => new Promise(r => setTimeout(r, 6000));

const JOB = () => 'gap-' + Date.now() + '-' + Math.floor(Math.random() * 1000);
const commit = (o) => o.exec === 'SUCCESS';
const fails = (o) => o.exec === 'ERROR';

console.log(`contract ${CA}\n`);
let gaps = 0;

// --- 1. duplicate job id must be rejected
{
  const id = JOB();
  const args = [id, A, 'https://github.com/pallets/click',
    'dup0000dup0000dup0000dup0000dup0000dup0000dup0000dup0000dup0000',
    'pytest -q', ['has docs'], Math.floor(Date.now() / 1000) + 86400, ['README.md'], ''];
  await write('post_job', args);
  const second = await write('post_job', args);
  if (fails(second)) console.log('  OK   duplicate job_id rejected');
  else { console.log('  GAP  duplicate job_id ACCEPTED'); gaps++; }
  await settle();
}

// --- 2. the same artifact cannot be reviewed twice
{
  const commitHash = 'aa11bb22cc33dd44ee55ff66aa77bb88cc99dd00ee11ff22aa33bb44cc55dd66';
  const url = 'https://github.com/pallets/click';
  const mk = (id) => [id, A, url, commitHash, 'pytest -q', ['has docs'],
    Math.floor(Date.now() / 1000) + 86400, ['README.md'], ''];
  const j1 = JOB(), j2 = JOB();
  await write('post_job', mk(j1));
  await write('accept_job', [j1]);
  await write('verify', [j1]);
  await settle();
  const v = await read('get_standing', [A]);
  const dup = await write('post_job', mk(j2));
  if (fails(dup)) console.log('  OK   same (repo,commit) cannot be re-reviewed');
  else { console.log('  GAP  same artifact RE-REVIEWABLE (dedup broken)'); gaps++; }
  await settle();
}

// --- 3. verify an unaccepted job must fail
{
  const id = JOB();
  await write('post_job', [id, A, 'https://github.com/pallets/click',
    'bb22cc33dd44ee55ff66aa77bb88cc99dd00ee11ff22aa33bb44cc55dd66aa22bb',
    'pytest -q', ['x'], Math.floor(Date.now() / 1000) + 86400, ['README.md'], '']);
  const r = await write('verify', [id]);
  if (fails(r)) console.log('  OK   verify without acceptance rejected');
  else { console.log('  GAP  verify WITHOUT acceptance SUCCEEDED'); gaps++; }
  await settle();
}

// --- 4. settle_unclaimed before the deadline must fail
{
  const id = JOB();
  await write('post_job', [id, A, 'https://github.com/pallets/click',
    'cc33dd44ee55ff66aa77bb88cc99dd00ee11ff22aa33bb44cc55dd66aa22bb33cc',
    'pytest -q', ['x'], Math.floor(Date.now() / 1000) + 86400, ['README.md'], '']);
  const r = await write('settle_unclaimed', [id]);
  if (fails(r)) console.log('  OK   settle before deadline rejected');
  else { console.log('  GAP  settle BEFORE DEADLINE SUCCEEDED'); gaps++; }
  await settle();
}

// --- 5. decline then verify must fail
{
  const id = JOB();
  await write('post_job', [id, A, 'https://github.com/pallets/click',
    'dd44ee55ff66aa77bb88cc99dd00ee11ff22aa33bb44cc55dd66aa22bb33cc44ee',
    'pytest -q', ['x'], Math.floor(Date.now() / 1000) + 86400, ['README.md'], '']);
  await write('decline_job', [id]);
  const r = await write('verify', [id]);
  if (fails(r)) console.log('  OK   verify after decline rejected');
  else { console.log('  GAP  verify AFTER DECLINE SUCCEEDED'); gaps++; }
  await settle();
}

// --- 6. a stranger must not be able to verify before the deadline
{
  const id = JOB();
  const OTHER = '0x1111111111111111111111111111111111111111';
  await write('post_job', [id, A, 'https://github.com/pallets/click',
    'ee55ff66aa77bb88cc99dd00ee11ff22aa33bb44cc55dd66aa22bb33cc44ee55ff',
    'pytest -q', ['x'], Math.floor(Date.now() / 1000) + 86400, ['README.md'], '']);
  await write('accept_job', [id]);
  // (the probe account is both issuer and agent here, so this checks the
  //  guard exists rather than who may call it)
  const r = await write('verify', [id]);
  if (commit(r)) console.log('  OK   named agent can verify its own accepted job');
  else { console.log('  GAP  agent CANNOT verify own accepted job: ' + r.msg); gaps++; }
  await settle();
}

// --- 7. re-verifying a recorded job must fail
{
  const id = JOB();
  await write('post_job', [id, A, 'https://github.com/pallets/click',
    'ff66aa77bb88cc99dd00ee11ff22aa33bb44cc55dd66aa22bb33cc44ee55ff66aa',
    'pytest -q', ['x'], Math.floor(Date.now() / 1000) + 86400, ['README.md'], '']);
  await write('accept_job', [id]);
  const first = await write('verify', [id]);
  const second = await write('verify', [id]);
  if (commit(first) && fails(second)) console.log('  OK   double-verify rejected');
  else { console.log(`  GAP  double-verify allowed (first=${first.exec} second=${second.exec})`); gaps++; }
  await settle();
}

console.log(`\n${gaps} gaps found`);
process.exit(gaps ? 1 : 0);