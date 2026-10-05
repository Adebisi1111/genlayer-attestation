import { createClient, createAccount } from 'genlayer-js';
import { studioDevnet } from 'genlayer-js/chains';

const CA = process.env.CA;
const PK = process.env.DEPLOY_PK;
const c = createClient({ chain: studioDevnet });
const w = createClient({ chain: studioDevnet, account: createAccount(PK) });

const read = async (fn, args = []) => {
  const r = await c.readContract({ address: CA, functionName: fn, args });
  return typeof r === 'string' ? JSON.parse(r) : r;
};
const write = async (fn, args) => {
  const t = await w.writeContract({ address: CA, functionName: fn, args });
  return t;
};

const AGENT = '0x61fd0047595A30A067f1F21F3b28C4AE8A8e3Dc3';
let pass = 0, fail = 0;
const check = (name, cond, detail = '') => {
  if (cond) { pass++; console.log(`  PASS  ${name}`); }
  else { fail++; console.log(`  FAIL  ${name}  ${detail}`); }
};

console.log('1. config reads back the non-custodial design');
const cfg = await read('get_config');
check('holds_funds is false', cfg.holds_funds === false, JSON.stringify(cfg.holds_funds));
check('weights sum to 100',
  cfg.weight_functional + cfg.weight_quality + cfg.weight_security + cfg.weight_completeness === 100);
check('no stake/threshold custody fields',
  !('staked' in cfg) && !('stake' in cfg), Object.keys(cfg).join(','));

console.log('\n2. unregistered agent has a public, empty standing');
const s0 = await read('get_standing', [AGENT]);
check('reputation starts at 0', String(s0.reputation) === '0', JSON.stringify(s0));
check('eligible flag is exposed', 'eligible' in s0);
check('holds_funds false for an agent', s0.holds_funds === false);

console.log('\n3. no payable / value-transfer surface');
const src = (await import('fs')).readFileSync('contracts/attestation.py', 'utf8');
check('no payable decorator', !/\.payable/.test(src));
check('no gl.message.value', !/gl\.message\.value/.test(src));
check('no withdraw method', !/def withdraw/.test(src));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);