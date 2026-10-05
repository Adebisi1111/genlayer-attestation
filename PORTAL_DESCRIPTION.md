Attestation: non-custodial, reputation-gated verification of agent deliverables.
Deployed 0xEc1cD00fefb4Dd2861d1CF426126a43b14ee3BD6 on Studio Dev 61997.

An issuer posts a job; the named agent accepts, binding to a digest of artifact,
requirements, deadline and slashing exposure. A leader LLM scores the real code
on functional, quality, security and completeness in one consensus round.
Reputation updates only after validator agreement.

NEW contract, not a resubmission. The prior VERITy was rejected for claiming
custody it could not honour: a runtime probe showed its ledger recording
deposits while native balance stayed 0. All token paths were removed, not
explained - no payable method, stake ledger, withdrawal or slash sink.

Verified live with real signed txs: lifecycle 9/9, plus 7 adversarial paths all
rejected. A real repo scored 95/96/91/95, overall 94, PASS; weighted arithmetic
recomputed independently and matches.

No local suite claimed: the pinned 2.x runner is unpublished.