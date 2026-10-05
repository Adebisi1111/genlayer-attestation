// Live proof of the reputation leg, and of the absence of custody.
//
// Replaces the withdrawn `e2e-slash-live.mjs`, which proved a stake burn and a
// disposal leg that no longer exist. The old harness could not survive the
// non-custodial rewrite, and pretending otherwise would have been the exact
// failure mode this contract was rewritten to remove.
//
// What this proves on a live chain:
//   1. registration takes no value and the contract holds no funds
//   2. an accepted job that is then abandoned is DEMOTED, not slashed
//   3. the demotion is proportional to slash_percent of reputation
//   4. one demotion does NOT end eligibility
//   5. the standing record reports holds_funds: false on-chain
//
// settle_unclaimed needs an EXPIRED deadline and post_job rejects a deadline
// already in the past, so post with a short deadline and actually wait it out.
import { createClient, createAccount } from 'genlayer-js';
import { studioDevnet } from 'genlayer-js/chains';

const C = process.env.ON_ADDR;
if (!C) { console.error('ON_ADDR not set'); process.exit(1); }

const cli = {
  issuer: createClient({ chain: studioDevnet, account: createAccount(process.env.KI) }),
  agent: createClient({ chain: studioDevnet, account: createAccount(process.env.KA) }),
};
const A = {
  issuer: createAccount(process.env.KI).address,
  agent: createAccount(process.env.KA).address,
};
const REPO = 'https://github.com/genlayerlabs/genlayer-js';

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

async function main() {
  const stamp = Date.now();
  const tag = `vrep-${stamp}`;

  const cfg = await j('agent', 'get_config', []);
  console.log(`\n  config -> ${JSON.stringify(cfg)}`);
  check('config reports min_reputation', typeof cfg.min_reputation === 'number', JSON.stringify(cfg));
  check('config reports max_demotions', typeof cfg.max_demotions === 'number', JSON.stringify(cfg));
  check('config states it holds no funds', cfg.holds_funds === false, JSON.stringify(cfg));

  // 1. registration carries NO value. Sending none is the proof that the
  //    payable path is gone; a contract that still expected a deposit would
  //    revert here instead of succeeding.
  const reg = await write('agent', 'register', []);
  check('register() succeeds with no value attached', reg.ok, `exec=${reg.exec}`);

  const rec0 = await j('agent', 'get_agent', [A.agent]);
  console.log(`  agent after register -> ${JSON.stringify(rec0)}`);
  check('registration placed no funds', rec0.holds_funds === false, JSON.stringify(rec0));
  check('reputation starts at the floor', rec0.reputation === cfg.min_reputation,
    `${rec0.reputation} vs ${cfg.min_reputation}`);

  const deadline = Math.floor(Date.now() / 1000) + 90;

  const p = await write('issuer', 'post_job',
    [tag, A.agent, REPO, `commit${stamp}`, 'npm test', ['has tests'], deadline, ['README.md'], '']);
  check('issuer posts a short-deadline job', p.ok, `exec=${p.exec}`);

  const acc = await write('agent', 'accept_job', [tag]);
  check('agent accepts - a real obligation now exists', acc.ok, `exec=${acc.exec}`);

  const early = await write('issuer', 'settle_unclaimed', [tag]);
  check('cannot settle while the deadline is live', early.exec !== 'SUCCESS', `exec=${early.exec}`);

  console.log(`  waiting out the 90s deadline...`);
  await new Promise((r) => setTimeout(r, 100000));

  // 2. an ACCEPTED job that is abandoned is demoted
  const s = await write('issuer', 'settle_unclaimed', [tag]);
  check('settle_unclaimed succeeds once expired', s.ok, `exec=${s.exec}`);

  const rec = await j('agent', 'get_agent', [A.agent]);
  console.log(`  agent after demotion -> ${JSON.stringify(rec)}`);

  const before = cfg.min_reputation;
  const expected = before - (before * cfg.slash_percent / 100n);

  // 3. the demotion is proportional to reputation, not to any balance
  check('agent was demoted once', rec.demotions === 1, JSON.stringify(rec));
  check('reputation reduced by slash_percent', rec.reputation === Number(expected),
    `${rec.reputation} vs ${expected}`);
  check('demotion recorded as a permanent total', rec.demoted_total === before - rec.reputation,
    JSON.stringify(rec));

  // 4. ONE failure must not end eligibility - this was a real defect in the
  //    first non-custodial draft, where a single penalty ended a career
  check('one demotion does NOT end eligibility', rec.active === true, JSON.stringify(rec));

  // 5. the standing record is explicit about holding nothing
  const standing = await j('agent', 'get_standing', [A.agent]);
  console.log(`  standing -> ${JSON.stringify(standing)}`);
  check('standing reports holds_funds: false', standing.holds_funds === false, JSON.stringify(standing));
  check('standing reports the floor', standing.min_reputation === cfg.min_reputation, JSON.stringify(standing));
  check('agent remains eligible after one demotion', standing.eligible === true, JSON.stringify(standing));

  console.log(`\n  tx hashes:`);
  console.log(`    register          ${reg.hash}`);
  console.log(`    post_job          ${p.hash}`);
  console.log(`    accept_job        ${acc.hash}`);
  console.log(`    settle_unclaimed  ${s.hash}`);

  console.log(`\n=== RESULT: ${pass} passed, ${fail} failed ===`);
  process.exit(fail === 0 ? 0 : 1);
}

main().catch((e) => { console.error('HARNESS FAIL:', e.message); console.error(e.stack); process.exit(1); });