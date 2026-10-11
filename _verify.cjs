const gpath = '/home/administrator/node_modules';
const { createClient, createAccount, chains } = require(gpath + '/genlayer-js');
const fs = require('fs');
let PK = fs.readFileSync('/tmp/oracle_pk.txt','utf8').trim(); if(!PK.startsWith('0x')) PK='0x'+PK;
const account = createAccount(PK);
const client = createClient({ chain: chains.testnetBradbury, account });
const ADDR = fs.readFileSync('final_addr.txt','utf8').trim();
const ABI = [ // minimal: just the methods we call
  {"type":"function","name":"register","stateMutability":"nonpayable","inputs":[],"outputs":[]},
  {"type":"function","name":"post_job","stateMutability":"nonpayable","inputs":[{"name":"job_id","type":"string"},{"name":"agent","type":"string"},{"name":"repo_url","type":"string"},{"name":"commit_hash","type":"string"},{"name":"test_command","type":"string"},{"name":"requirements","type":"string[]"},{"name":"deadline","type":"uint256"},{"name":"files","type":"string[]"},{"name":"test_results_url","type":"string"}],"outputs":[]},
  {"type":"function","name":"accept_job","stateMutability":"nonpayable","inputs":[{"name":"job_id","type":"string"}],"outputs":[{"name":"","type":"string"}]},
  {"type":"function","name":"verify","stateMutability":"nonpayable","inputs":[{"name":"job_id","type":"string"}],"outputs":[{"name":"","type":"string"}]},
  {"type":"function","name":"get_agent","stateMutability":"view","inputs":[{"name":"agent","type":"address"}],"outputs":[{"name":"","type":"string"}]},
  {"type":"function","name":"get_scorecard","stateMutability":"view","inputs":[{"name":"job_id","type":"string"}],"outputs":[{"name":"","type":"string"}]},
];
const FEES = { feeValue: '100000000000010352' };
const me = account.address;
async function w(fn, args) {
  const h = await client.writeContract({ address: ADDR, abi: ABI, functionName: fn, args: args || [], value: 0n, fees: FEES, gasLimit: 5000000n });
  return h;
}
async function rd(fn, args) {
  return await client.readContract({ address: ADDR, abi: ABI, functionName: fn, args: args || [] });
}
async function fin(h, label) {
  for (let i=0;i<120;i++){
    const r = await client.waitForTransactionReceipt({ hash: h, waitUntil: 'finalized', retries: 120 });
    if (r && r.resultName) { console.log(`  ${label}: ${r.resultName}`); return r; }
    await new Promise(x=>setTimeout(x,4000));
  }
  return client.waitForTransactionReceipt({ hash: h, waitUntil: 'finalized', retries: 60 });
}
(async () => {
  console.log('addr:', ADDR, 'me:', me);
  console.log('[1] register');
  await fin(await w('register'), 'register');
  console.log('[2] post_job');
  const deadline = Math.floor(Date.now()/1000) + 86400;
  await fin(await w('post_job', ['job-1', me, 'https://github.com/Adebisi1111/send-app', 'master', 'npm test', ['README.md'], deadline, [], '']), 'post_job');
  console.log('[3] accept_job');
  await fin(await w('accept_job', ['job-1']), 'accept_job');
  console.log('[4] verify (runs independent-evaluation consensus)');
  const vh = await w('verify', ['job-1']);
  const vr = await fin(vh, 'verify');
  console.log('  verify resultName:', vr && vr.resultName);
  console.log('[5] get_scorecard');
  try { console.log('  scorecard:', await rd('get_scorecard', ['job-1'])); } catch(e){ console.log('  scorecard err:', (e.shortMessage||e.message).slice(0,80)); }
  console.log('[6] get_agent');
  try { console.log('  agent:', await rd('get_agent', [me])); } catch(e){ console.log('  agent err:', (e.shortMessage||e.message).slice(0,80)); }
})().catch(e => console.log('FAIL:', (e.shortMessage||e.message).slice(0,150)));
