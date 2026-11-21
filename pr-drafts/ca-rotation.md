# J — Rotate CA trust using ordinary-role reconciliation

**Branch:** `pr/ca-rotation` (`77dec86`; implementation `e3e094b`).
**Base:** `integration/rotation-base` (`df7a258`), combining trust-only updates
with the independent inline-debug-key fix. The latter supports this acceptance
fixture; deployments using another confirmation probe need not enable debugging.

## Design

Add an opt-in playbook with prepare, migrate and finalize phases. Ordinary role
tasks continue to own certificate issue, PKI/configuration installation,
activation, confirmation and per-host rollback. The new code owns fleet gates,
CA-pair binding, selected signing-input promotion and progress records.

- Prepare compatible successor CA material, preserving format, curve and CA
  restrictions, and distribute overlap without changing leaf identities.
- Require confirmed overlap on the complete participant set before migration.
  Switch stable signer inputs and reconcile issuer drift serially, preserving
  subjects, addresses, groups and private keys.
- Require new issuers and fresh controller confirmation before retirement.
  Remove only the selected old CA and retain unrelated/historical entries.
- Reject limited/check-mode phases, missing or stale confirmations, mismatched
  keys and reuse of an ID with a different CA pair. Require operator attestations
  for unmanaged-device preparation and identity migration.
- Make phase repeats preserve PKI bytes and report zero changes. Persist binding
  and confirmation records so interrupted phases can be inspected and resumed.

End-user prerequisites and the exact three-command procedure are in
`docs/ca-rotation.md`. No default role run starts rotation. Local rollback does
not automatically reverse completed participants, and retirement is not
automatically reversible. Required offline devices block progression; app bundle
support must be tested separately. There is no fleet-wide atomic transaction.

## Recorded verification

The implementation head `e3e094b` passed **57 assertions** on Nebula 1.12.0
(`nebtest-01251752e0`). Ansible ran over Nebula SSH from a controller that was
itself a CA/node participant. Successful phases retained the persistent SSH
session, all sampled new SSH connections succeeded, and all participant keys
and PIDs were preserved. Tests retained version-1 CA/leaf format on modern
binaries, explicit FQDN subjects, restrictions, unrelated trust and an expired
historical CA. Each phase repeated with byte-identical PKI and zero changes.

Negative gates covered unprepared/limited/check-mode migration, premature
retirement, unmanaged readiness, incomplete trust, wrong successor key, altered
CA-pair binding and missing migration confirmation. A debug-tunnel probe forced
fresh handshake state and checked the exact expected leaf fingerprint. An old
issuer was rejected after finalize, with a working new-issuer control, and
ordinary-role reissue worked afterward.

At that head, independent integrated runs passed 137 PKI, 77 IP/restart,
32 certificate, 39 firewall and 28 CA-host assertions on 1.12.0. Adoption from
PR25 reported zero changes. Separate SSH recovery passed 11 assertions
(`nebtest-ad5e1fd665`), including peer-untrusted identity and autonomous recovery
from an SSH-blocking firewall change. Deliberate outage samples were counted as
failures, not hidden from continuity results.

Head `77dec86` merges the legacy shared-bundle compatibility fixes. Its final
regression reruns, including incomplete attempts, are distinguished in
`testing/lxd/verification.md`; the earlier complete results are not relabeled
as tests of this later head. The current unit suite passes all 21 tests.

```sh
python testing/lxd/run.py --remote REMOTE --project PROJECT \
  --role-path /path/to/ca-rotation --scenario role-feature \
  --feature-case rotation-ssh --nebula-version 1.12.0
python testing/lxd/run.py --remote REMOTE --project PROJECT \
  --role-path /path/to/ca-rotation --scenario role-feature \
  --feature-case ssh-recovery --nebula-version 1.12.0
```

The SSH fixture needs guest package-network access and installs its controller
dependencies inside the disposable VM. It is Linux/systemd evidence for this
topology and release, not Windows/mobile/mixed-version or all-topology coverage.
