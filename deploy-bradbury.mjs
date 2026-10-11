// Deploy Attestation to GenLayer Bradbury (chain 4221) using genlayer-js.
// Studio Dev (61997) is rejected: its pinned 2.x runner is not published and
// gltest cannot run locally against it (see PROJECT_STATE.md). Bradbury is
// where the contract actually executes and can be verified.
import { createClient, createAccount } from 'genlayer-js';
import { testnetBradbury } from 'genlayer-js/chains';
import fs from 'fs';

const PK = process.env.DEPLOY_PK;
if (!PK) { console.error('DEPLOY_PK not set'); process.exit(1); }

const CONTRACT = '/home/administrator/attestation/contracts/attestation.py';

async function main() {
  const account = createAccount(PK);
  const client = createClient({ chain: testnetBradbury, account });

  const code = fs.readFileSync(CONTRACT, 'utf8');
  console.log('Contract bytes:', code.length);

  // Mirror the verified Bradbury deploy (FactCheckDAO / ContractJudge) exactly:
  // code is passed as a UTF-8 STRING (not a Uint8Array — that makes gas
  // estimation revert), and the SDK handles gas and fees on deploy.
  const result = await client.deployContract({ code });
  console.log('Deploy TX:', result);

  const receipt = await client.waitForTransactionReceipt({
    hash: result,
    waitUntil: 'decided',
    retries: 300,
    interval: 3000,
    fullTransaction: true,
  });

  console.log('Receipt status:', receipt?.status);

  const ca = receipt?.data?.contract_address
    ?? receipt?.contract_address
    ?? receipt?.logs?.[0]?.address;
  console.log('Contract address:', ca);

  try {
    const r = await fetch(`https://explorer-bradbury.genlayer.com/api/transactions/${result}`);
    const j = await r.json();
    const lr = j?.transaction?.consensus_data?.leader_receipt;
    const e = Array.isArray(lr) ? lr[0] : lr;
    console.log('Leader execution:', e?.execution_result);
  } catch (e) {
    console.log('Leader execution: (unavailable)', e.message);
  }

  if (ca) fs.writeFileSync('/tmp/attestation_bradbury.txt', ca);
}

main().catch((e) => { console.error('FAIL:', e.message); process.exit(1); });
