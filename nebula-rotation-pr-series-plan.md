# CA rotation through focused improvements to the normal role

## Goal and working boundaries

Prepare small, independently useful local PR branches for
`AndrewPaglusch/Nebula-Ansible-Role`. Certificate reconciliation, installation,
activation, validation and recovery belong in the ordinary role. Fleet rotation
adds sequencing and trust-transition gates while reusing that machinery.

Reload when the complete change is supported; restart when necessary. Verify
actual runtime state, not just file contents, a living PID or a refreshed-leaf
log. Local certificate validity does not establish peer acceptance.

Role READMEs describe end-user behavior and limits. Maintainer rationale,
dependencies, tested versions and evidence live here, in `pr-drafts/`, and in
`testing/lxd/verification.md`. These are local draft branches; do not push or
open PRs without an explicit request.

## Current branch/dependency index

| PR | Branch / head | Immediate base | Focus |
|---|---|---|---|
| prerequisite | `feature/cert-json-compat` / `a0527eb` | upstream `db0ebc4` | Submitted PR25 |
| A | `pr/pki-install` / `2d4b0c4` | PR25 | Consumer-owned PKI transaction |
| B | `pr/pki-in-place` / `2ef39af` | A | Pre-sign snapshot and CA-node recovery |
| C | `pr/cert-subject` / `10415ae` | B | Subject independent of inventory filename |
| D | `pr/ca-signing-inputs` / `6be8e73` | C | Isolated signer and supplied trust bundle |
| E | `pr/staged-cert-signing` / `afad75e` | D | Verified staged reissue; retain/localize keys |
| F | `pr/lighthouse-cert-drift` / `8639ca2` | E | Shared node/lighthouse reconciliation |
| G | `pr/cert-issuer-drift` / `7d9e84f` | F | Ordinary-role issuer convergence |
| H | `pr/confirmed-activation` / `71c7f94` | G | Local rollback and management confirmation |
| I | `pr/config-reload` / `b3aefce` | H | Classified recoverable configuration reload |
| trust-only | `pr/trust-only-updates` / `0a47ee2` | I | Distribute trust without reconciling leafs |
| debug keys | `pr/inline-debug-keys` / `7b381d8` | PR25 | Inline debug SSH keys without a file registry |
| integration base | `integration/rotation-base` / `df7a258` | trust-only + debug keys | Reproducible prerequisite merge |
| J | `pr/ca-rotation` / `77dec86` | integration base | Fleet phases and trust gates |

Worktrees are under `series/<feature>/`. Review the immediate-base diff with
`git diff BASE..BRANCH`, not the cumulative diff from upstream. C/D may be
offered independently if upstream prefers, after rechecking their prerequisites.
The debug-key fix is independent; its integration enables the test probe, not a
universal production dependency on Nebula's SSH console.

### Preserved reference work

- `feature/safe-pki-apply`, `8fec782`, including the behavior fix `8a2c46b`.
- Original complete regression reference `8312726`; split-selector/evidence
  checkpoint `06dcac7` on `feature/lxd-regression-tests`.
- `feature/ca-rotation` (`8c1febe`), `feature/ca-rotation-prototype` (`3ab337b`),
  `feature/lxd-ca-rotation-tests` (`d99fe89`) and experimental rotation material.

Do not rewrite or delete these implementations to make the extracted series
look smaller. The former detailed design is retained in this document's Git
history at `06dcac7`; the active index replaces its obsolete “not yet built”
status. Historical P IDs map to the implemented split: P2=C, P3a=A, P3b=B,
P3c=H, P4=F, P5=E, P6=D+G+trust-only, A1=I and P7/P8=J plus acceptance.

## Evidence ledger

Results apply to the stated implementation/test revisions, not automatically
to every later merge. [PR drafts](pr-drafts/series.md) and the
[verification record](testing/lxd/verification.md) distinguish current-head
compatibility checks, original focused runs and final integrated reruns.

| Scope | Completed evidence |
|---|---|
| PR25 | 32 lifecycle assertions each on 1.8.0, 1.9.5, 1.10.0, 1.11.2 and 1.12.0; separate 8-assertion 1.8.0→1.11.2 format-preserving upgrade |
| Original network fix `8a2c46b` | 137 PKI + 77 IP/restart assertions each on 1.8.0/1.11.2, including zero-change adoption |
| A `2d4b0c4` | 98 reload/upgrade + 60 IP/restart assertions each on 1.8.0/1.12.0 |
| B `2ef39af` | Red on A; 42 reload/upgrade + 17 IP/restart assertions each on 1.8.0/1.12.0 |
| C `10415ae` | Focused red/green, 5 assertions on 1.12.0 |
| D `b9504f4` | Red/green, 5 signer checks each on 1.10.3/1.12.0, including expired historical trust before the selected signer |
| D `6be8e73` | Legacy follow-up: 5 each on 1.8.0/1.12.0; 5 with expired historical trust on 1.10.3; 6 unit tests |
| E implementation | Red/green, 5 each on 1.8.0/1.12.0 |
| E `afad75e` | 5 staged-signing checks on 1.12.0; 5 expired shared-bundle checks on 1.10.3 |
| F implementation | Red/green, 6 each on 1.8.0/1.12.0; current head merges E's follow-up |
| G implementation | Red/green, 11 on 1.12.0 |
| G `7d9e84f` | Legacy follow-up: 5 each on 1.8.0/1.12.0; 5 expired shared-bundle checks on 1.10.3 |
| H `509cdf7` | 7 controller-kill/watchdog assertions on 1.12.0; 7 transaction unit tests |
| I implementation | 8 focused assertions each on 1.8.0/1.12.0; 4 classifier unit tests |
| Trust-only implementation | 3 focused assertions on 1.12.0 |
| Debug-key fix | Focused red/green on 1.12.0 |
| J `e3e094b` | 57 SSH rotation + 11 SSH recovery; 137 PKI + 77 IP + 32 certificate + 39 firewall + 28 CA-host assertions on 1.12.0 |
| Current J `77dec86` | 21 unit tests pass; post-compatibility integrated reruns are tracked in the verification record |

The selector rehearsal against `8a2c46b` proved install + in-place retains all
**211** combined acceptance assertions, excluding topology-specific baseline
checks. Eight fast selector/comparison tests passed. Each extracted A/B head
subsequently passed its own actual-head matrix; that is separate evidence from
the lossless-selection rehearsal.

## Contracts retained by the split

### Installation and activation

- A owns consumer installation, with the baseline same-host signing path
  preserved. B snapshots before a co-located signer mutates live state and owns
  recovery across signing and installation. An inner apply must not destroy the
  outer snapshot. No-op counts do not imply no temporary snapshot work.
- Compare old live networks/prefixes/unsafe networks and actual tunnel addresses.
  Restart changed networks or stale runtime addresses; compatible renewal,
  groups, keys and trust can reload. Unknown old state must not silently choose
  reload. Check intended interface addresses after activation.
- Recovery after a failed attempted restart restores and restarts; preserving
  the old PID is not a valid requirement for that path. Renumbering can interrupt
  connections. Early A/B evidence uses independent LXD management.
- Preserve useful failure diagnostics and recovery copies when restoration is
  incomplete. Key mismatch, wrong CA, startup failure and interrupted local
  signing must preserve/restore the working files and runtime state.
- H arms host-local systemd rollback before mutation and serializes publication,
  confirmation and rollback using a per-service transaction and lock. Controller
  rescue is not the only recovery mechanism. Confirmation cannot commit an
  expired transaction or cancel a different transaction's timer.
- Multi-file publication is **not atomic**. First installation/inactive service
  is distinct from protecting a running deployment. Test secrets must not enter
  shared logs; keys stay in root-only remote snapshots.
- Provisioned check/diff must not change deployed files, arm rollback or signal
  services. Unprovisioned check mode, reboot races and arbitrary external writers
  remain separate verification targets.

### Configuration

I compares parsed mappings. Only changes wholly within firewall inbound/outbound
rules are on the tested reload allow-list. Unknown or mixed settings restart;
restart wins for incompatible combined PKI/configuration changes. Do not expand
the allow-list to logging, lighthouse settings or other candidates without
release-specific evidence. Preserve default rendering and avoid duplicate
end-of-play activation. Firewall reloads can block Ansible despite a healthy PID;
the same deadline and confirmation path must protect them.

### Signing, subjects, keys and bundles

- Inventory name, certificate subject, file basename and service name differ.
  C adds an explicit subject without changing canonical filenames or forcing
  subject-only reissue. F intentionally starts correcting lighthouse IP/group
  drift; users must have correct desired values before adoption.
- E stages issue and verification before publication, reuses the existing public
  key, and avoids retransferring an existing Linux consumer's private key. First
  issue still needs a matching key. Public-key preservation alone is insufficient
  if old slurp/copy paths keep transporting private material.
- D separates signer certificate/key inputs from distributed trust. Explicit
  signer input must be a single public CA. Existing shared default paths retain
  first-CA signing and distribute the entire bundle unchanged. Isolate that CA
  for verification/metadata; never parse an old multi-document JSON bundle as a
  single certificate or treat unrelated trust as the intended issuer.
- G detects issuer fingerprint drift. Operators must load overlap before
  switching signers; ordinary reconciliation does not establish fleet readiness.
- Trust-only updates preserve existing leaf identity even after partial migration.
  Certificate format/curve and restrictions must remain compatible with the
  oldest required participant; binary JSON compatibility is a different concern.

The [68-assertion expired-CA binary probe](testing/lxd/expired-ca-verification.md)
pins an important version distinction: 1.10.3 `nebula-cert verify` rejects a
valid+expired bundle, while 1.11.2 accepts it. Both tested daemons accept that
mixed bundle at `-test`, startup and HUP with an expired-CA warning. Invalid-only
trust fails validation/startup, but HUP can leave a process alive and log a
refreshed leaf while rejecting CA loading. Keep runtime bundle policy separate
from isolated intended-signer verification; do not silently prune test bundles
or infer an untested version cutoff.

## Implemented fleet workflow

J's [end-user procedure](series/ca-rotation/docs/ca-rotation.md) uses a stable
rotation ID and the complete `nebula_rotation` participant set:

1. **Prepare:** create a compatible successor and distribute overlap through
   trust-only ordinary-role updates. Record confirmed bundle/process/boot state.
2. **Migrate:** require confirmed overlap everywhere, validate the successor key,
   promote stable signer inputs and reconcile issuers serially using existing keys.
3. **Finalize:** require new issuers and fresh management confirmation everywhere,
   then retire only the selected old CA, preserving unrelated/historical trust.

Limited/check-mode execution, mismatched keys, incomplete/stale confirmation and
reuse of an ID with a different CA pair are rejected. Operator attestations are
required for unmanaged clients' overlap and identity migration; they are not
automatic app checks. Required offline participants block phase completion.

Persistent SSH and fresh-handshake proof serve different purposes. The test
controller's probe verifies a new tunnel index and the exact expected leaf
fingerprint. A pre-existing tunnel or one successful peer does not establish
fleet-wide trust. Negative tests must retain working controls and actual peer
rejection of a locally valid but untrusted issuer.

Reuse an ID to resume a phase and revalidate state. There is no automatic fleet
reversal: earlier completed participants are not undone when a later host fails.
Retirement reversal is itself a coordinated trust transition. Preserve pending
recovery snapshots. After the site's recovery-retention period, distinguish old
archived signing keys and cached peer keys from the active signer and each
running host's own required private key.

## Current completion work and future verification

1. Finish final-stack reruns after the legacy bundle fix; record partial,
   infrastructure-failed and completed runs separately.
2. Finish/review the immediate-base drafts and durable evidence; commit verified
   runner/doc changes and clean only task-owned VMs. Do not push.
3. For future submission or deployment, rerun the relevant actual head and follow
   documented migration steps literally. Preserve CA and existing leaf/key
   caches and require zero incidental file changes or service restarts.
4. Additional evidence is needed for mixed-version/topology combinations,
   Windows/mobile runtime and bundle import, alternate logging/tunnels,
   unprovisioned check mode, reboot races, external watchdog writers and broader
   reload settings. Existing results are Linux/systemd scope, not universal
   availability guarantees.
