// Deploy Attestation to Studio Dev (61997) using genlayer-js directly.
// deployContract({ code }) takes the source BYTES. The `genlayer deploy` CLI
// submits an empty type-0 payload on this network and creates nothing, so the
// SDK path is the only one that works here.
import { createClient, createAccount } from 'genlayer-js';
import { studioDevnet } from 'genlayer-js/chains';
import fs from 'fs';

const PK = process.env.DEPLOY_PK;
if (!PK) { console.error('DEPLOY_PK not set'); process.exit(1); }

const CONTRACT = '/home/administrator/attestation/contracts/attestation.py';

async function main() {
  const account = createAccount(PK);
  const client = createClient({ chain: studioDevnet, account });

  const code = fs.readFileSync(CONTRACT);
  console.log('Contract bytes:', code.length);

  // The network rejects a deployment with no fee attached
  // (FeeValueMustBeNonZero), and a deploy without it reverts before executing:
  // receipt status 0, gasUsed 0, no contract created. Fees must be estimated
  // explicitly rather than left to the client.
  const fees = await createClient({ chain: studioDevnet }).estimateTransactionFees({});
  console.log('feeValue:', String(fees.feeValue), 'distribution:', String(fees.distribution));

  const result = await client.deployContract({
    code: new Uint8Array(code),
    args: [],
    fees: { distribution: fees.distribution, feeValue: fees.feeValue },
  });
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

  // The SDK reports status 5 even when the method raised, so read the leader
  // execution result from the explorer rather than trusting the status.
  try {
    const r = await fetch(`https://explorer-studio-dev.genlayer.com/api/transactions/${result}`);
    const j = await r.json();
    const lr = j?.transaction?.consensus_data?.leader_receipt;
    const e = Array.isArray(lr) ? lr[0] : lr;
    console.log('Leader execution:', e?.execution_result);
  } catch (e) {
    console.log('Leader execution: (unavailable)', e.message);
  }

  if (ca) fs.writeFileSync('/tmp/attestation_new.txt', ca);
}

main().catch((e) => { console.error('FAIL:', e.message); process.exit(1); });