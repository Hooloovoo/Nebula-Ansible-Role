# Plan: CA rotation built from improvements to the normal role

## 1. Goal and design direction

Deliver small, independently useful PRs to
`AndrewPaglusch/Nebula-Ansible-Role`. Most of the machinery needed for eventual
CA rotation should improve the regular role: certificate reconciliation, safe
file installation, activation, verification and recovery. Full rotation should
reuse those capabilities, with only fleet-wide sequencing and trust-transition
state added later.

**Use SIGHUP when it can fully activate a change; restart when required.**
Classify before activation. Do not try reload and infer success from a running
PID or a fingerprint log: Nebula can reject part of a reload while Ansible
reports success. IP renumbering is a confirmed example (§2).

For identity-preserving CA rotation, the target is reload-only operation with
measured connection continuity. This is not a promise that every ordinary role
change can avoid interruption: IP, tunnel, listener and other restart-only
changes need a restart, reconnection and a recoverable failure path.

This is a design proposal, not an implementation brief. Variable names, task
boundaries and the final rotation interface remain open until their supporting
PRs are designed and tested. Prefer focused reviewable changes over arbitrary
line-count caps; do not shrink a PR by leaving required failure paths unsafe.

## 2. Evidence and current status

The recorded LXD evidence is in
[`testing/lxd/verification.md`](testing/lxd/verification.md). Results apply to
the revisions and environments recorded there, not automatically to later code.

| Area | Recorded result |
|---|---|
| Upstream foundation | PRs #23 (configurable CA host) and #24 (certificate groups/group firewall rules) are merged at baseline `db0ebc4`. |
| JSON compatibility, PR #25 | Role `a0527eb`: certificate lifecycle passed 32 assertions per release on 1.8.0, 1.9.5, 1.10.0, 1.11.2 and 1.12.0 before suite extraction. |
| Certificate-format upgrade | Extracted scenario passed 8 assertions for 1.8.0 → 1.11.2, preserving version-1 certificates, keys and groups. |
| Safe-PKI core | Role `a577b2a` plus PR #25's lookups passed 137 assertions each on 1.8.0 and 1.11.2, including baseline upgrades, real systemd/journald, persistent TCP streams, fresh peer handshakes and rollback. |
| Explicit PKI task files | Refactor `5570cc9` passed the same 137 assertions on both releases, including zero-change upgrades from `a577b2a`; snapshot, backup and restore no longer use `pki_phase`. |
| Review hypotheses | Those runs demonstrate working snapshot/restore paths and fingerprint evidence with default text logging. They do not support the hypothesis that snapshot/restore always recurse as `apply`. |
| **IP-change blocker** | On both 1.8.0 and 1.11.2, the safe-PKI role reports success after certificate renumbering while `neb0` retains the old address and fresh traffic to the new address fails. On 1.11.2 the journal says `Networks in new cert was different from old`. |
| Firewall evidence | The earlier 1.11.2 run timed out after 28 passing checks; it is not a complete firewall pass. |

The safe-PKI tests verify mismatched-key and wrong-CA rejection, byte-identical
restoration, process preservation and backup cleanup, including in-place
CA-node signing failures. They also show why `nebula -test` alone is insufficient:
a matching certificate/key from an untrusted CA can pass configuration validation.

**Not established by those runs:** full CA rotation; successful IP renumbering;
recovery after controller disconnection; watchdog/restart races; stopped-service
signing recovery; alternate logging settings; fresh/unprovisioned check mode;
mobile-app trust-bundle behavior. The safe-PKI fixture disables the connectivity
check cron job. Its peer probes do not mean that the role probes peers itself.

The previous plan reported additional standalone experiments involving overlap
trust, certificate formats and `-in-pub`. Keep these as useful leads, not fresh
LXD evidence. Reproduce the relevant behavior against each proposed PR. In
particular, verify CA/leaf version and curve compatibility with the oldest
participant, and do not introduce a certificate-format upgrade incidentally.

## 3. Branches and test ownership

| Branch/revision | Purpose |
|---|---|
| `feature/cert-json-compat`, `a0527eb` | Submitted PR #25; use the branch rather than the older four-case patch file. |
| `feature/lxd-regression-tests`, `27f9306` | Updated base plus focused JSON and safe-PKI coverage (`d2279ae`, `27f9306`). |
| `feature/safe-pki-apply`, locally `5570cc9` | Narrower first-stage implementation, trust verification and tested explicit-task refactor; **IP-change defect must be resolved before treating it as merge-ready**. |
| `testing/lxd-rotation-experiments`, `8a17e94` | Preserved experimental scenarios and helpers; source material for future PR-specific tests. |
| `feature/lxd-ca-rotation-tests`, `d99fe89` | Earlier modular rotation suite; compare with experiments instead of importing both wholesale. |
| `feature/ca-rotation` and personal infrastructure branches | Reference implementations and topology examples, not a required architecture or an approved emergency fallback. |

Keep the general LXD suite aligned with submitted and upcoming role PRs. Add
each independently useful behavior as its own scenario/commit. `auto` currently
runs ordinary certificate, firewall and CA-host tests; upgrade and safe-PKI
acceptance scenarios are opt-in. `all` includes `safe-apply-ip-change`, which
currently fails against the tested safe-PKI role. Keep that failure visible.

The runner at `27f9306` defaults to 1.8.0 and 1.9.5. Keep its README and CLI
consistent when deciding future defaults; newer-version and acceptance matrices
should be explicit rather than silently multiplying the cost of default runs.

## 4. Shared activation design

### 4.1 Separate classification from activation

PKI-specific and configuration-specific classifiers produce a small activation
decision: **none**, **reload**, or **restart**, with a reason and the state that
must be verified. Backup, activation and recovery mechanics are shared. A
first-install/inactive-service path is separate from updating a running service.

For combined changes, restart takes precedence over reload. When config support
is added, classify against the complete proposed PKI/configuration state and
activate once where practical. Do not scatter independent reload/restart
decisions across copy tasks and handlers, or reload changed PKI against an old
configuration and assume that proves the final configuration works.

Initially config/unit/binary changes can retain the existing restart path;
document and test mixed runs and handler behavior. Centralizing final activation
must not silently expand the first PKI PR into a config-management rewrite.

### 4.2 Initial PKI behavior table

| Situation | Proposed behavior |
|---|---|
| No relevant content changes | No activation; fix owner/mode drift without an unnecessary restart. |
| Certificate renewal, key replacement or groups change, with compatible unchanged certificate networks | Validate and SIGHUP; verify unchanged process and intended active certificate. |
| Certificate IP/network changes | Validate and restart; verify intended tunnel addresses and fresh connectivity. Conservatively classify other network-related certificate differences until their reload support is tested. |
| Trust-bundle changes | Validate trust and leaf/key consistency, then SIGHUP when supported; verify intended trust transition. Local certificate verification does not prove fleet trust. |
| First deployment or inactive service | Validate what is available, install and start through the normal role path; do not pretend there was a running state to preserve. |
| Combined PKI and restart-only change | Restart, with verification of the final state. |
| Windows | Preserve existing behavior until separately implemented and tested. |

Compare the **old live certificate** with the desired certificate, not just the
new file or desired inventory variables. For a co-located signer/node, capture
the old state before signing can overwrite it. Normalize version-specific JSON
fields, but preserve distinctions such as network prefix lengths and certificate
versions. Unknown or unreadable old state must not silently select reload.

### 4.3 Transaction lifecycle

1. Inspect current files, metadata, configuration, service and effective identity.
2. Prepare proposed files and classify the complete change without altering the
   running inputs. Record the chosen activation reason.
3. Save recoverable originals and metadata in a root-only transaction directory.
   Keep key backups as remote files, not Ansible variables. Choose a location
   with defined permissions, retention and interrupted-run recovery; a directory
   under `nebula_config_dir` is a candidate, not a substitute for recovery logic.
4. Validate the proposed certificate against the intended trust bundle and
   validate the complete proposed configuration/PKI. Publish only validated
   inputs where practical; maintain a final pre-activation consistency check.
5. Arm independent local rollback before any operation that could sever the
   management connection. Install the related files and perform the chosen
   activation.
6. Verify local runtime state and confirm the management path where required.
7. Commit/confirm the transaction and remove backups and rollback scheduling.
8. On failure, restore all affected files and metadata, reactivate the old state
   using the appropriate operation, verify recovery, and fail with a useful cause.

Multiple file copies are **not an atomic set**. Per-file rename alone does not
make cert/key/config publication atomic either. Define how signing, handlers,
the role's connectivity watchdog and other role-managed writers are coordinated;
test the relevant races. Preserve the good configuration when input validation
fails, and recover partial publication or signing failures.

Use `no_log` for private-key content and secret-bearing tasks. Retain bounded,
non-secret validation diagnostics; do not hide every failure behind `no_log`.
Check/diff mode must not install files, arm rollback, signal services or disclose
keys. Test provisioned and unprovisioned paths separately.

### 4.4 Verification and recovery are operation-specific

- **Reload:** active service, same process, evidence of the intended loaded
  certificate/settings/trust, and no relevant reload errors. Check all applicable
  errors: the known network-rejection message is not covered by the current
  two PKI-load-error strings. Logs are supporting signals, not sole proof.
- **Restart:** active new service process, intended effective addresses/listeners
  or settings, and fresh overlay/management connectivity. PID change alone is
  insufficient. Existing connections may be interrupted; say so explicitly.
- **Restore after reload:** reload the restored state when compatible, otherwise
  restart if recovery requires it. Do not promise original-PID preservation for
  every recovery case.
- **Restore after restart:** restore and restart again; verify the previous
  working identity and connectivity, not preservation of the original PID.

Ordinary role runs must not impose ICMP reachability as a universal prerequisite:
deployments may deliberately deny ping. Define confirmation using the actual
management path or an explicit supported probe. A successful established tunnel
does not prove a fresh handshake or newly loaded trust.

Ansible `rescue`/`always` cannot be the only rollback mechanism when restarting
Nebula can disconnect its own controller. A local timed, transaction-identified
rollback must survive controller loss, avoid racing with confirmation or later
runs, and clean up or report interrupted transactions. A restart-only path that
cannot yet recover over overlay SSH must be explicitly limited to a verified
independent management path rather than advertised as safe over Nebula.

### 4.5 Explicit task files before new activation behavior

The structural refactor is implemented in `5570cc9`, preserving the existing
activation behavior so it can be reviewed independently of the upcoming
reload/restart changes. It replaces `pki_phase` and recursive calls to
`install_pki.yml` with explicit includes:

| Task file | Responsibility |
|---|---|
| `pki_snapshot.yml` | Initialize transaction facts, inspect original file/process state, and request backups before in-place signing. |
| `pki_backup.yml` | Create the root-only backup directory and copy original files remotely; shared by snapshot and ordinary apply. |
| `install_pki.yml` | Compare original and desired/current hashes, back up ordinary changed files, install, validate and activate. |
| `pki_restore.yml` | Restore original files/metadata, remove files originally absent, and reactivate the restored state without starting a new transaction. |

The backup helper is shared because ordinary installation must defer backup
creation until it detects changes, while in-place signing needs backups before
the signer modifies live files. No-op ordinary installations must not create
backups. Check mode must not create them in either path.

The node's in-place signing block owns snapshot → signing → apply, restoration
on failure, and final cleanup. Ordinary `install_pki.yml` owns its own transaction.
An inner apply must not delete the outer signing snapshot. Reset transaction
facts at the start of a new snapshot so a previous include's backup/process
state cannot be reused accidentally. Preserve useful failure diagnostics during
recovery; a restore failure must not masquerade as successful restoration.

Keep `pki_in_place` as an operation flag, not a hostname test in the shared
installer. Its meaning is: signing has already modified these live PKI files,
so compare pre-signing and current hashes. The node caller currently derives
that flag from `inventory_hostname == nebula_ca_host`; future staged signing
on the same host need not use in-place mode.

The file split passed the existing reload/rollback suite (137 assertions each
on 1.8.0 and 1.11.2), including a zero-change upgrade from the pre-refactor role.
It does not fix IP renumbering, introduce restart classification or provide
autonomous rollback. Keep the IP-change defect recorded separately until its
behavior-changing fix is implemented; see the verification record for commands
and test-stack prerequisites.

## 5. Proposed PR sequence

The IDs retain continuity with the previous plan, not fixed implementation
boundaries. P0 evolves alongside each PR; tests are not postponed to the end.

| PR | Independently useful scope | Acceptance/dependencies |
|---|---|---|
| P0 | General LXD regression foundation and scenario ownership | Already integrated; maintain exact revision/results records and visible negative cases. |
| P1 | Certificate JSON compatibility | Submitted PR #25. Only two IP alternatives are needed: `details.ips[0]` through 1.9.x, `[0].details.networks[0]` from 1.10.0. Keep the object/array groups lookup. |
| P2 | Explicit certificate subject independent of inventory/file name, if needed | Preserve existing subjects on reissue; no blanket name-drift regeneration. Establish this before re-signing deployments whose subject names differ from inventory names. |
| P3a | Safe PKI transaction with reload/restart classification | Extend the current safe-PKI work to fix IP renumbering and match §4. Includes trust verification, readable failures, recovery and no-op behavior. Requires P1 when testing newer releases. |
| P3b | Same guarantees for CA-host-as-node signing | Already partly implemented/tested in the current branch; retain or split only if each proposed PR is safe alone. Snapshot before mutation; later staged signing removes the delete-before-create window. |
| P3c | Connection-independent confirmation/rollback | Develop with P3a's restart path where needed. Reusable for firewall changes and rotation. Must precede claims of safe restart over overlay SSH; do not defer a required recovery mechanism merely to keep P3a small. |
| P4 | Shared signing/reconciliation for node and lighthouse | Lighthouse IP/groups and later issuer drift need an ordinary-role path. Preserve default rendering/identities; explicitly document newly detected lighthouse drift. Depends on safe activation and subject handling where needed. |
| P5 | Staged re-signing with existing keys | Preserve key identity and avoid deleting good files before signing/validation. Prefer existing `nebula-cert` capabilities; add a filter only if justified. Test key/public-key consistency, curves, old releases and fresh issue separately. |
| P6 | Explicit signing identity, trust bundles and issuer drift | Use the ordinary role to converge to an intended signer while distributing overlap trust. Validate signer/key match and preserve unrelated trust. Depends on shared signing and safe activation. |
| P7 | Minimal fleet rotation sequencing and gates | Reuse the regular role; finalize the interface only after prerequisites settle. Depends on P1/P3–P6, required identity handling and demonstrated local recovery. |
| P8 | End-to-end rotation acceptance | Built alongside P7 and a merge gate for it, not a follow-up after shipping rotation. |

### P3 priorities before proposing safe-PKI upstream

- Keep the tested task-file refactor in §4.5 separate from the upcoming behavior
  changes. Preserve its ordinary/in-place caller, check-mode, restoration and
  cleanup coverage; add targeted same-play repeated-include and recovery-failure
  tests where the next transaction changes introduce new state/lifetime risks.
- Make the current `safe-apply-ip-change` acceptance test pass by actually
  activating the new address with restart, not by ignoring the case or returning
  success after a rejected reload. Until implemented, explicit rejection with
  restoration is safer, but does not complete the planned renumbering feature.
- Strengthen the test: the current failure branch allows safe rejection; final
  restart support must explicitly require successful renumbering, expected
  activation and a fresh handshake at the new address.
- Keep the passing reload, wrong-CA, mismatched-key, interrupted signing and
  baseline-upgrade coverage. Add restart-failure recovery, management interruption,
  watchdog coordination and fresh check-mode cases before claiming those paths.
- Distinguish first deployment/inactive-service handling from recovery of an
  existing running host; avoid promising protection never implemented or tested.

### A1 — Config classification using the same transaction

This is the next generally useful feature after safe-PKI activation stabilizes,
not something that must wait for full CA rotation.

- Compare parsed old/proposed configuration, not textual YAML. Define how
  missing values, defaults, deletions, nested mappings and ordered lists compare.
- Reload only when **every changed setting is on a version-tested allow-list**.
  Restart for known restart-only settings, unknown settings or uncertain
  classifications. A change in a reloadable section does not automatically make
  every nested setting reloadable.
- Candidate reloadable settings from earlier experiments include selected
  firewall, logging, lighthouse-list/interval, `preferred_ranges` and
  `punchy.respond` settings. These are investigation targets, not an approved
  blanket allow-list. Handle `pki` through its PKI-specific classifier.
- Candidate restart-only settings include `listen.port`, `listen.host`, tunnel
  device/MTU/enabled state, cipher and process-role changes; confirm exact paths
  and behavior on supported releases. Test settings that are silently ignored
  by SIGHUP as well as those that emit errors.
- Classify mixed PKI/config changes together; restart wins. Validate the final
  proposed configuration and avoid duplicate end-of-play handler activation.
- Firewall/management-path changes require local timed rollback and explicit
  confirmation even when SIGHUP preserves the PID: a healthy daemon can apply
  a rule that blocks the controller. `serial: 1` limits fleet exposure but cannot
  recover the host that just became unreachable.

Other independent improvements: secret transport/logging hardening, documented
configuration fidelity options and eventual default-version updates. Do not
make unrelated defaults or rendering changes a prerequisite for this series.

## 6. Signing, identities and trust conventions to settle

### 6.1 Keep signing identity distinct from distributed trust

The previous plan required the signing CA to be first in `ca.crt`. That may be
a compatibility convention for a CLI that reads the first certificate, but it
should not be the only candidate design. Prefer an explicit single signing
certificate/key input and an independently distributed trust bundle if that
fits a small backward-compatible PR. Preserve existing default paths; do not
replace a deployed `ca.crt` layout without a migration procedure.

Validate signer/key consistency before issuing anything. Derive issuer drift
from the selected signing certificate fingerprint, not an arbitrary trust-bundle
entry. A bundle-order change alone must not unintentionally change the signer.
Test multi-CA bundles, expired historical entries and unrelated valid CAs; retain
unrelated trust unless explicitly selected for retirement.

### 6.2 Preserve identities without forcing a representation

Inventory hostname, certificate subject, file basename and service identity are
different concepts. Preserve existing subjects, networks/prefixes, groups and
keys during identity-preserving CA migration. Avoid silent mass regeneration
when introducing a subject override or lighthouse drift detection.

P5 must update the distribution path as well as signing before claiming that
keys no longer travel after first issue. `-in-pub` alone does not remove the
existing key `slurp`/copy operations. Staging plus rename protects one file;
multi-file fresh issuance still needs transaction coordination.

Keep certificate version, curve and restrictions compatible with every
participant, including mobile/packaged clients. Verify format compatibility
separately from binary JSON compatibility. Preserve the old format by default
unless an explicit, tested format migration is intended.

## 7. Eventual rotation workflow (proposed, not implemented here)

Rotation is a trust transition across the fleet, not just changing `ca.key` and
rerunning a play. Ordinary-role mechanisms handle each host; orchestration must
establish the fleet-wide invariants before changing the active signer or
retiring trust. A state file records progress but is not proof of loaded state.

| Phase | Intended action | Gate |
|---|---|---|
| Prepare | Stage a new CA with compatible version/curve/restrictions; distribute old+new trust without changing the signer or leaf identities. | Every required participant has loaded overlap trust before migration. Include unmanaged clients in preparation or define how they are excluded from availability claims. |
| Migrate | Select the new signer; reconcile leaf issuer drift using existing keys; verify one host at a time through the ordinary role. | Every required participant presents the intended new-CA certificate on a fresh handshake; identity and key preservation verified. |
| Finalize | Retire only explicitly selected old CA fingerprints, retaining unrelated trust; confirm each host. | Old-CA-only peers are rejected on fresh handshakes; new peers connect; no required participant still depends on retired trust. |
| Revert/resume | Use explicit persisted state plus fresh evidence to restore or continue a partially completed transition. | Establish overlap on affected participants before switching issuers back; never assume a state marker or archived key makes reversal safe. |

Proposed ordering uses serialized activation with fatal fleet gates. Determine
signer/controller/lighthouse ordering from the topology and management paths,
not a universal "controller last" rule. Keep fleet-wide phase completion separate
from per-host activation and make interruption/resumption idempotent. Decide
whether partial host selection is rejected or explicitly supported with a
validated complete participant set; do not rely on `serial` as a trust gate.

Unmanaged clients need both preparation and migration procedures. A phone must
receive overlap trust before peers start presenting new-CA certificates if it
is to stay connected; it is not sufficient to update it only before finalization.
Sleeping/offline clients block completion unless explicitly retired from the
participant set. Test the actual app's import and bundle support.

Default cleanup must distinguish obsolete CA signing keys and cached peer keys
from each running host's own required private key. Define archival permissions,
retention and recovery policy; do not automatically keep old signing keys forever
or prune unrelated/expired CAs as a side effect of rotation.

Do not promise automatic recovery after finalization: restoring old trust is
itself a coordinated trust transition. Likewise, overlap reduces issuer-mismatch
risk but does not guarantee connectivity during arbitrary failed operations.

## 8. Verification strategy

Use fast syntax/targeted unit checks where appropriate, then real LXD VMs for
systemd, journald, interfaces and traffic. Shims can support development but
cannot establish restart/connection recovery. Test documented migration steps
literally, including preservation of signer and leaf certificate/key hashes.

### Matrices and baseline upgrades

- Certificate JSON compatibility: 1.8.0, 1.9.5, 1.10.0, 1.11.2 and 1.12.0.
- Safe-PKI target matrix: 1.8.0, 1.9.5, 1.11.2 and 1.12.0; current completed
  integrated evidence is 1.8.0 and 1.11.2 only. Add mixed signer/target versions
  such as 1.10.3/1.11.2 when the relevant PR introduces that requirement.
- Apply changed defaults/templates to a baseline deployment. Require zero
  incidental file changes, reloads or restarts where behavior is unchanged.
- Keep certificate-format upgrade tests separate from normal fresh issuance.
- Record role/test revisions, Ansible/collection versions, exact commands,
  completed assertions, failure paths, cleanup and untested combinations.

### Activation tests

- No-op: file hashes and PID unchanged, no activation-related journal events;
  Ansible reports no changes. Ordinary traffic logs may still appear.
- Reload: intended certificate/settings active, same PID, persistent TCP stream
  survives without a reconnection path, then a fresh peer handshake succeeds.
- Restart: intended address/listener active and new process; management reconnects
  at the intended endpoint. Report the expected interruption rather than imposing
  reload's continuity assertion on restart-only changes.
- Combined changes: one effective activation decision; final-state validation;
  handler behavior correct. Unknown config keys conservatively select restart.
- Invalid/missing configuration: fail before replacing good state where possible.
  Inject key mismatch, wrong CA, invalid config, restart failure and partial
  signing/publication failures; verify restored content and metadata.
- Controller loss: local rollback restores reachability without relying on the
  dead SSH session; confirmation cannot race or cancel another transaction.
- Watchdog/concurrent-writer cases; first deployment, inactive service and
  provisioned/unprovisioned check/diff behavior; no key leakage or stale backups.

### Full rotation acceptance (P8)

- Exercise overlap, migration and retirement over Nebula SSH on real VMs;
  measure long-lived streams/session continuity for the reload-only workflow.
- Force fresh handshakes after each phase; prove the expected peer issuer and
  trust behavior, including old-CA rejection after retirement and recovery after
  restoring the intended peer certificate. File contents plus ping alone do not
  prove all CAs are loaded, and an existing tunnel may hide stale trust.
- Preserve semantic name/networks/groups, certificate format as intended, and
  private-key hashes. Require hash preservation of unchanged files; do not
  demand byte-identical certificates after intentional reissue.
- Repeat each phase with no changes; interrupt and resume phases; reject unsafe
  progression with offline or unacknowledged participants; test staged revert.
- Cover default CA/lighthouse, separate signer and CA-node topologies, plus an
  unrelated valid CA and an expired historical entry in the initial bundle.
- Investigate `pki.disconnect_invalid` and established-tunnel behavior on trust
  retirement before making uninterrupted-connectivity claims for that case.

## 9. Immediate next steps and working method

1. Keep P1 focused on PR #25 and preserve its recorded compatibility evidence.
2. Review the completed explicit-task refactor (`5570cc9`, §4.5) and its passing
   syntax, baseline-upgrade and LXD reload/rollback evidence before building
   activation-classification changes on top.
3. Update the safe-PKI implementation brief to replace "reload, never restart"
   with §4's classification and operation-specific recovery requirements. Fix
   the confirmed IP-change defect and rerun the dedicated acceptance test.
4. Decide the smallest safe split for P3a/P3b/P3c; preserve already tested CA-node
   protections. Review recovery over overlay SSH before enabling restart there.
5. Complete the targeted matrix and baseline/failure cases for the proposed
   change; update `testing/lxd/verification.md` with actual results.
6. Submit the generally useful safe-PKI behavior first; design config
   classification against the same transaction interface as the next feature.
7. Extract signing, identity and trust improvements from the rotation branches
   as their contracts settle, with PR-specific tests on the general LXD branch.
8. Add fleet rotation orchestration and full acceptance only after prerequisites
   work through ordinary role runs. Rehearse the actual topology and documented
   workflow before a production rotation.

For each PR: specify behavior and dependencies, separate compatibility changes
from intentional behavior changes, review failure detection and secret handling,
run appropriate tests, and report what was not verified. Keep scope small through
independent features rather than unsupported numeric size estimates or claims
about upstream appetite. Do not push or open PRs without an explicit request.

## Appendix: reuse candidates from the rotation work

| Existing material | Candidate destination |
|---|---|
| `rotation_install.yml` and watchdog logic | Shared transaction/confirmation ideas for P3 and A1; do not copy a parallel installer wholesale. |
| `rotation_leaf.yml`, public-key helpers | P5 staged key-preserving signing and identity checks; verify CLI/version behavior before introducing filters. |
| `rotation_ca.yml`, key-promotion tasks | P7 CA preparation and selected-signer transition after P6 defines signing/trust inputs. |
| Trust-bundle and signer-bundle helpers | P6/P7 normalized fingerprints and preservation of unrelated trust; ordering only where explicitly required by the chosen CLI interface. |
| Leaf/key verification and phase guards | Ordinary-role verification where general; fleet invariants in P7/P8. |
| Rotation playbooks/docs | Rehearsal and test material; final interface need not match their names or file structure. |
| Experimental and modular LXD suites | Extract non-duplicated cases per supporting PR; retain original branches until replacement coverage is demonstrated. |

No existing implementation is retired solely because a plan says its replacement
will be smaller. Archive it when equivalent required behavior is implemented,
tested and its useful evidence preserved.
