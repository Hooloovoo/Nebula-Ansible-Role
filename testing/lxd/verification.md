# Regression integration verification

The [focused-series and SSH records](#focused-normal-role-series-and-ssh-rotation)
below describe the newer local PR stack. Earlier sections retain revision-scoped
history; their then-missing features are not statements about later branches.

The subsequent [PR A review follow-up](pki-install-review-verification.md)
documents the installer refactor developed on top of `2d4b0c4`, now consolidated
as `pr/pki-install-submission` at `e9bf712`, with its narrowed early-drift-rejection
contract and recovery/probe fixes. Its source hash and new
matrix are recorded separately; the original branch-head results below are not
relabelled as results for the follow-up.

## Split-selector preservation rehearsal

The new `--pki-mode` selectors were rehearsed against the preserved integrated
role `8a2c46b` on Nebula 1.8.0, before extracting separate A/B candidates:

| Mode | Reload/rollback | IP/restart | Run IDs |
|---|---|---|---|
| combined | 134 + 3 baseline checks | 77 | `nebtest-01b965efb4`, `nebtest-957e4617a0` |
| install | 95 | 60 | `nebtest-b67f79c197`, `nebtest-29e38580e8` |
| in-place | 39 + 3 baseline checks | 17 | `nebtest-91c9c8ae8c`, `nebtest-6cf3526e1a` |

All completed. The coverage comparator proved that A+B retains all **211**
combined acceptance assertions, including repeated helper assertions; baseline
checks are excluded from that arithmetic and passed in their respective runs.
Eight fast selection/coverage tests also passed. A source comparison retained
all 108 original assertion call sites from `8312726`.

These results establish preservation and mode isolation on the integrated code,
not correctness of unbuilt extracted PRs. Their actual heads must still be run
fresh, against appropriate baselines, and together. Role code was unchanged in
this rehearsal. The baseline for combined/in-place upgrade checks was `5570cc9`.
All created VMs were removed.

These results describe integration of the focused LXD tests, rather than a
blanket approval of every behavior in the role. Guests were disposable Ubuntu
24.04 LXD VMs, managed with `community.general.lxd` on remote `mythie`, project
`opencode-temp`. The controller used ansible-core 2.21.6 and community.general
13.5.0. Test VMs were removed after each completed or failed run.

## Certificate JSON compatibility (PR #25)

Role revision: `a0527eb`. The extracted `certificate-format-upgrade` scenario
passed all 8 assertions: certificates created using Nebula 1.8.0 retained their
hashes, keys, version and groups after upgrading the binaries to 1.11.2.
Run ID: `nebtest-ffa0dafb47`.

Before extraction, the same certificate lifecycle checks also passed 32
assertions per release on 1.8.0, 1.9.5, 1.10.0, 1.11.2 and 1.12.0. Those are
earlier results, not fresh runs of the final integrated runner. The earlier
1.11.2 firewall run timed out after 28 passing checks and was not a full pass.

## Safe-PKI application

Tested role: `a577b2a`, with only PR #25's IP/group JSON lookups added in a
throwaway checkout. Baseline role: `a0527eb` (upstream role plus PR #25, before
safe-PKI application). No test hooks were added to the production role.

`safe-apply` passed **137 assertions on each of Nebula 1.8.0 and 1.11.2**,
using `--baseline-role-path`. This covered:

- Zero-change upgrades preserving PKI hashes and process IDs.
- Fresh installation and node, separate-CA lighthouse and CA-node paths.
- Reloads preserving process IDs, text-format journal fingerprint evidence,
  persistent TCP streams and fresh peer handshakes.
- No-op and provisioned check/diff runs without file changes or reloads.
- Config-only and combined config/PKI changes retaining restart behavior.
- Mismatched-key and wrong-CA failures with restoration of the original hashes,
  process, fingerprint and removal of temporary backups.
- Interrupted in-place CA-node signing and restoration.

Completed run IDs: `nebtest-50346a5f2c` (1.8.0) and `nebtest-a5f5362228`
(1.11.2). A preceding combined run timed out near the end of 1.11.2; the
separate completed run supersedes that partial result. The 1.8.0 completed run
predates addition of the separate IP-change scenario. These integration runs
did not repeat the safe-PKI matrix on 1.9.5 or 1.12.0.

### Historical failure: IP renumbering (fixed by `8a2c46b`)

The separate `safe-apply-ip-change` scenario fails on **both 1.8.0 and 1.11.2**
against the tested safe-PKI role. Changing the node from `10.193.0.2/24` to
`10.193.0.4/24` returns a successful Ansible status, but the tunnel still has
the old address and fresh peer traffic to the new address fails. The scenario
collects addresses, certificate JSON, journal and ping results before asserting.
On 1.11.2 the journal explicitly reports `Networks in new cert was different
from old`, while the installed certificate contains the new network and the
interface still has the old one.

Run IDs: `nebtest-e9981a2555` (1.8.0), `nebtest-1bd8c97f2b` (1.11.2).

This was a failing acceptance test, not an expected-failure pass. The original
reload-only revisions remain affected; `8a2c46b` fixes the issue as described
below. The strengthened scenario now requires successful renumbering and does
not accept safe rejection as a passing outcome. `all` includes this test;
`auto` excludes the opt-in safe-PKI and upgrade scenarios.

## Interpretation of review concerns

The completed rollback tests exercise snapshot and restore calls under real
Ansible; they do not support the hypothesis that those paths always recurse
as `apply`. Default text logging did provide the fingerprint evidence used by
the assertions. These observations apply to the revisions and controller
listed above, rather than every Ansible/Nebula combination.

The suite disables the connectivity-check cron job. It does not prove atomic
multi-file publication in the presence of concurrent watchdog restarts,
stopped-service signing recovery, alternate logging settings, unprovisioned
check mode or full CA rotation. Provisioned check mode is covered. Peer probes
in the tests do not mean that the role itself performs connectivity checks.

## Explicit PKI task-file refactor

Role revision `5570cc9` splits snapshot, remote backup and restore into explicit
task files, keeping apply in `install_pki.yml` and removing `pki_phase` dispatch.
The node signing caller owns recovery/cleanup for in-place signing; ordinary
installations own their transactions. Snapshot initialization clears old
transaction facts, and ordinary no-op installations defer file backups.

The `safe-apply` scenario passed **137 assertions each on 1.8.0 and 1.11.2**
against the refactor. The baseline was the pre-refactor safe-PKI role `a577b2a`;
both throwaway role checkouts included PR #25's two JSON lookups. Baseline
upgrade reported zero changes and preserved PKI hashes and service PIDs. The
runs also covered node/lighthouse rollback, in-place CA-node signing and
interrupted signing, wrong-CA/key-mismatch diagnostics, backup cleanup,
provisioned check/diff mode, persistent streams and fresh handshakes.

Run IDs: `nebtest-4d9a5dd918` (1.8.0), `nebtest-8edfc881c8` (1.11.2). YAML
parsing and Ansible playbook syntax checks passed. The controller/guest setup
was the same as above, with Ansible pipelining enabled. The test runner was
`27f9306`.

```sh
PATH="/snap/lxd/current/bin:/tmp/opencode/regression-venv/bin:$PATH" \
LXD_CONF="$HOME/snap/lxd/common/config" \
ANSIBLE_COLLECTIONS_PATH=/tmp/opencode/collections ANSIBLE_PIPELINING=true \
/tmp/opencode/regression-venv/bin/python testing/lxd/run.py \
  --remote mythie --project opencode-temp \
  --role-path /tmp/opencode/pki-refactor-test \
  --baseline-role-path /tmp/opencode/regression-role \
  --scenario safe-apply --nebula-version 1.8.0 --nebula-version 1.11.2 --cpus 1
```

This is a structural refactor, not the planned IP-change/restart fix. The known
IP-change defect above remains open; that separate acceptance scenario was not
rerun for this refactor. Same-play repeated-include failure cases and failure
of restoration itself were not independently injected in these runs. The
ordinary default release matrix and other coverage limitations remain as
documented above. All VMs from these runs were removed.

Logs live under `/tmp/nebula-lxd-tests/<run-id>/` while the temporary directory
is retained; run IDs are identifiers, not a promise of permanent artifact
availability. Re-run the README commands for current evidence.

## Network-change restart fix

Role revision **`8a2c46b`** classifies PKI activation using the certificate's
normalized networks/unsafe networks and the current tunnel addresses. Address
or prefix changes restart Nebula; compatible changes still use SIGHUP. It also
repairs the older silent-failure state where the desired certificate files are
already installed but the live tunnel still has the previous address.

After activation the role verifies the service process and enabled tunnel
addresses. Recovery distinguishes validation failures (restore and reload) from
attempted restarts (restore and restart). If tunnel inspection fails, snapshot
processing stops before installing replacement PKI rather than guessing that
the addresses are missing. Local address inspection uses iproute2's `ip -json`.

### Completed LXD runs

| Scenario | Version | Assertions | Run ID |
|---|---|---|---|
| `safe-apply-ip-change` | 1.8.0 | **77 passed** | `nebtest-ca9070bb34` |
| `safe-apply-ip-change` | 1.11.2 | **77 passed** | `nebtest-6623d07ebf` |
| `safe-apply` with baseline upgrade | 1.8.0 | **137 passed** | `nebtest-6825bd1c71` |
| `safe-apply` with baseline upgrade | 1.11.2 | **137 passed** | `nebtest-3db2c0ed15` |

The baseline for these upgrades was `5570cc9`. Tested and baseline throwaway
checkouts both included only PR #25's IP/group JSON lookup changes as an
additional prerequisite. Healthy baseline upgrades reported zero changes,
preserved PKI hashes and process IDs, and caused no unnecessary activation.
The test runner was `33a0d94` plus the accompanying IP-test changes. The
controller/guest setup was as above, with pipelining enabled.

The 77-assertion scenario covers:

- Node address changes and prefix-only changes in both directions (/24 ↔ /25),
  with new processes, exact interface networks, matching certificate fields
  and fresh peer handshakes.
- Zero-change repeat runs, non-mutating proposed-IP check/diff mode and
  group-only reissue that preserves the PID after renumbering.
- An older failed-reload fixture: stage the desired files without activation,
  then require the normal role to repair runtime drift without re-signing.
- In-place CA-node renumbering and installation of a changed-network lighthouse
  certificate through the shared installer. This does not add automatic
  lighthouse re-signing to the regular role.
- Real startup failure injection on node, CA-node and lighthouse. A test-only
  binary wrapper fails exactly one `-config` startup, while `-test` remains
  valid. The play must report an activation failure, restore the original PKI
  hashes, start with the original address, regain fresh connectivity and remove
  transaction backups. The recovered process is new, not the original PID.

Example commands (using the recorded controller setup):

```sh
export PATH="/snap/lxd/current/bin:/tmp/opencode/regression-venv/bin:$PATH"
export LXD_CONF="$HOME/snap/lxd/common/config"
export ANSIBLE_COLLECTIONS_PATH=/tmp/opencode/collections ANSIBLE_PIPELINING=true
python testing/lxd/run.py --remote mythie --project opencode-temp \
  --role-path /tmp/opencode/pki-network-test --scenario safe-apply-ip-change \
  --nebula-version 1.8.0 --nebula-version 1.11.2 --cpus 1
python testing/lxd/run.py --remote mythie --project opencode-temp \
  --role-path /tmp/opencode/pki-network-test \
  --baseline-role-path /tmp/opencode/pki-network-baseline --scenario safe-apply \
  --nebula-version 1.8.0 --nebula-version 1.11.2 --cpus 1
```

Earlier incomplete IP runs exposed fixture issues, not passing evidence: a
restarted lighthouse needed node-initiated registration before peer traffic;
the separate CA had to be provisioned before adding it as a node; and the
CA-node's new address had to be a host variable rather than a global extra
variable that assigned duplicate addresses. One run also stopped on an LXD TLS
verification error before activation. The completed runs above supersede those
partial attempts; none was counted as a pass.

YAML parsing, Python syntax and Ansible syntax checks passed. All test VMs were
removed. Tests on other releases, IPv6, unsafe-network changes, disabled/custom
tunnels, watchdog races and loss of the controller connection were not added
to this run. The pre-existing broader coverage limitations still apply.

Renumbering intentionally interrupts existing overlay connections. These tests
use the independent LXD management transport; they do not establish autonomous
rollback after losing a Nebula SSH management session. Use an independent
management path for renumbering until that separate recovery feature is tested.

## Focused normal-role series and SSH rotation

Current dependencies and heads are indexed in
[`../../nebula-rotation-pr-series-plan.md`](../../nebula-rotation-pr-series-plan.md).
Maintainer-facing scope/dependency descriptions are in `../../pr-drafts/`.
The role's user documentation contains behavior and operational prerequisites,
not this matrix.

### Actual extracted candidates

These tests used the real focused branches, not just the preserved integrated
safe-PKI implementation. Counts include three baseline checks where indicated.

| Candidate | Release | Completed checks | Run IDs |
|---|---|---|---|
| A `2d4b0c4` | 1.8.0 | 98 PKI/upgrade + 60 IP | `nebtest-69c1ce8146`, `nebtest-141a752b7a` |
| A `2d4b0c4` | 1.12.0 | 98 PKI/upgrade + 60 IP | `nebtest-ee0f4ae627`, `nebtest-5ea711e6cb` |
| B `2ef39af` | 1.8.0 | 42 in-place/upgrade + 17 IP | `nebtest-893f3ee16e`, `nebtest-4868c113f4` |
| B `2ef39af` | 1.12.0 | 42 in-place/upgrade + 17 IP | `nebtest-a83fc00357`, `nebtest-ec4e3b553a` |
| C `10415ae` | 1.12.0 | 5 subject/filename checks | `nebtest-2a0aeafb54` |
| D `b9504f4` | 1.10.3 / 1.12.0 | 5 signer checks each | `nebtest-ec29e25d02`, `nebtest-a797450e81` |
| E initial implementation | 1.8.0 / 1.12.0 | 5 staged/key checks each | `nebtest-ccde0fb0a8`, `nebtest-a8e9a39168` |
| F initial implementation | 1.8.0 / 1.12.0 | 6 lighthouse checks each | `nebtest-2ba2fd3cd1`, `nebtest-2ab1101a91` |
| G initial implementation | 1.12.0 | 11 issuer checks | `nebtest-9f2bb1a5bc` |
| H `509cdf7` | 1.12.0 | 7 confirmation/controller-kill/watchdog checks | `nebtest-a7e1b4d86e` |

A adopted PR25's baseline with zero changes, unchanged PKI hashes and PIDs.
B's test failed on A at the active fingerprint assertion (`nebtest-281b3a2d1a`),
then passed on B. Other focused red runs: C on B `nebtest-51a0561a36`; D on C
`nebtest-eab1548bae`; E on D `nebtest-860346a472`; F on E
`nebtest-76e027b36c`; G on F `nebtest-090ee55074`. These are genuine negative
acceptance results, not passing tests of missing features.

The classified-config implementation passed 8 focused assertions each on
1.8.0/1.12.0, trust-only updates passed 3 on 1.12.0, and inline debug SSH keys
passed their focused 1.12.0 red/green case. Those implementation-era results
precede the inherited legacy-bundle changes below; later integration and unit
results are kept distinct.

### Complete integrated implementation before the compatibility follow-up

Role head **`e3e094b`**, based on the combined ordinary-role stack and PR25:

- **57 SSH rotation assertions**, Nebula 1.12.0, `nebtest-01251752e0`.
- **11 SSH recovery assertions**, Nebula 1.12.0, `nebtest-ad5e1fd665`.
- **137 PKI/upgrade** (`nebtest-992ea59d8f`), **77 IP/restart**, **32 certificate**,
  **39 firewall** (`nebtest-c192ed8ce5`) and **28 CA-host**
  (`nebtest-dae8d68e24`) assertions, all on 1.12.0.

The PKI upgrade baseline was upstream+PR25 `a0527eb`; unchanged deployment
adoption reported zero changes. Failure paths retained wrong-CA/key rejection,
restored hashes, intended runtime addresses, service behavior and backup cleanup.
The full firewall pass supersedes the earlier incomplete 1.11.2 attempt only
for its own release/head; it does not retroactively complete that earlier run.

The rotation fixture runs Ansible over Nebula SSH from the CA/node controller,
with separate lighthouse/server participants. It uses v1 CA/leaf format on the
1.12.0 binary, restricted CA networks, explicit FQDN subjects, unrelated valid
trust and an expired historical entry. Successful prepare/migrate/finalize
retained keys and PIDs, a persistent SSH session and all sampled new connections.
All phase repeats preserved PKI bytes and reported zero changes.

Negative phase gates covered unprepared migration, limited/check-mode runs,
premature finalization, unmanaged trust/identity readiness, incomplete overlap,
wrong successor key, altered CA-pair binding and missing migration confirmation.
The debug-console probe verified a fresh tunnel index and exact expected leaf
fingerprint, rather than treating old SSH or ping as proof of new trust. A
retired-issuer peer was rejected on a fresh attempt, the new-issuer control
recovered, and ordinary-role convergence/reissue worked after finalization.

The separate SSH recovery case supplied a locally valid leaf from a CA accepted
by the node but not its management peer. Confirmation failed and restored old
PKI/process state. It then deliberately blocked management SSH through a firewall
reload: the on-host timer restored reachability, recording
`rollback_trigger=watchdog`. Nine failed SSH samples during the deliberate outage
were expected and were not presented as uninterrupted connectivity.

### Legacy shared-bundle compatibility regression and repair

A current-CA-first shared `ca.crt` bundle worked on unmodified PR25 defaults
(`nebtest-03620be692`, 1.12.0, 5 assertions) but failed on the new stack
(`nebtest-79b3b46f0f`). D's strict single-signer validator and G's leaf JSON
normalizer had rejected a formerly supported shared input. On 1.8.0, printing a
bundle additionally emits multiple JSON documents rather than one JSON array
(`nebtest-3d02eda7bf`, failure at runtime trust inspection).

The fix keeps default shared-path first-CA signing, isolates that public CA for
verification/metadata and distributes the full runtime bundle unchanged.
Separate explicit signer inputs must still contain exactly one CA and reject
extra material. Runtime trust membership compares decoded public certificate
bytes, avoiding version-dependent bundle JSON. E uses the isolated CA for staged
issue and verification; G inspects only the selected CA for issuer drift.

| Tested candidate | Case / release | Assertions | Run ID |
|---|---|---|---|
| D follow-up `6be8e73` | legacy / 1.8.0 | 5 | `nebtest-5e23df6eae` |
| D follow-up `6be8e73` | legacy / 1.12.0 | 5 | `nebtest-3441a7ad04` |
| D follow-up `6be8e73` | expired legacy / 1.10.3 | 5 | `nebtest-8cb4805a85` |
| E follow-up `afad75e` | staged failure/key retention / 1.12.0 | 5 | `nebtest-aaab03b164` |
| E follow-up `afad75e` | expired legacy / 1.10.3 | 5 | `nebtest-513252ad32` |
| G follow-up `7d9e84f` | legacy / 1.8.0 | 5 | `nebtest-841ddc3605` |
| G follow-up `7d9e84f` | legacy / 1.12.0 | 5 | `nebtest-604aad17c0` |
| G follow-up `7d9e84f` | expired legacy / 1.10.3 | 5 | `nebtest-8f95dea008` |

Each legacy pass verifies intended issuer, intact bundle distribution,
repeat-run identity preservation and fresh connectivity with the retained
consumer process. The expired 1.10.3 test is important because verifying against
the whole bundle would fail despite the daemon accepting it. Six unit tests
cover first selection, strict explicit inputs, missing/extra material and public
identity membership. Tests ran on the candidate working trees subsequently
committed at the listed revisions; the last strict-extra-material assertion was
also checked by the unit suite.

An initial D expired run (`nebtest-5962003c1c`) encountered an empty Ansible
module response during repeat copy; the clean rerun passed. Runs
`nebtest-1c1bfced86` and `nebtest-2678b000f6` were interrupted by outer command
deadlines and are not completed passes. Interrupted cleanup was completed using
the ownership-checked saved manifest. Saved cleanup now tolerates instances
already removed by a previous partial cleanup while still checking every
remaining instance's ownership marker.

### Final post-compatibility integration

Tested code head **`77dec86`** merges the compatibility fixes through the recorded
immediate bases. Its 21 role unit tests pass (6 CA selection, 7 transaction,
4 configuration classification and 4 rotation). The runner's 10 tests
(8 selector/coverage and 2 cleanup ownership/idempotency checks), Python
compilation and Ansible syntax checks also pass.

Initial concurrent reruns were not complete passes: `nebtest-c37e420049`
reached the wrong-CA lighthouse recovery and then failed backup cleanup after
an empty Ansible module response; `nebtest-57d9400133` passed prepare/migrate,
their repeats and the exercised negative gates but hit the outer 30-minute
deadline before completing finalization. Neither is counted as a completed pass.
Serial reruns with two vCPUs completed successfully:

| Scenario | Release | Assertions | Run ID |
|---|---|---|---|
| PKI with PR25 baseline adoption | 1.12.0 | **137** | `nebtest-5ba2a9e6fd` |
| IP/restart and injected startup recovery | 1.12.0 | **77** | `nebtest-338d6fca5c` |
| Complete rotation over Nebula SSH | 1.12.0 | **57** | `nebtest-9096c49a4e` |
| Shared default bundle with expired historical CA | 1.12.0 | **5** | `nebtest-dff2dbdd1e` |

The successful runs retain the earlier contracts: zero-change baseline adoption,
unchanged healthy identities/PIDs, real interface checks, restored invalid-input
state, backup cleanup and expected restart behavior. Prepare/migrate/finalize
and their repeats preserved keys/PIDs and measured SSH continuity; old-issuer
fresh-handshake rejection and post-rotation ordinary reissue also passed.

### Literal CA-host migration and documentation completion

The default-path migration procedure was followed against an existing PR25
deployment using E's `afad75e` production code and the clarified README committed
as **`b4e0957`**. All **8 assertions** passed on 1.12.0
(`nebtest-9829c9c93d`). Copying the complete CA/leaf certificate/key cache to the
new CA-only signer preserved all signing/cache hashes. Existing lighthouse/node
PKI and process IDs stayed unchanged; each consumer reported zero changes.
The repeat reported zero changes on all three hosts. Cleanup removed the old
CA signing key and cached peer private key while retaining the old lighthouse's
own key and runtime CA bundle, then fresh connectivity succeeded.

The first migration attempt (`nebtest-87b2304ed6`) exposed a fixture argv error:
the LXD project flag was appended after the tar command delimiter. It failed
before copying private material; corrected flag placement produced the complete
pass above. Its retained VMs were removed using ownership-checked cleanup.

Migration guidance now distinguishes signing/cached-peer keys from a running
host's own required private key and includes separately configured inputs.
E describes node-only key preservation; F's shared path extends it to lighthouses.
The documentation was propagated through the dependent branches using ordinary
merges. Final J **`4976a8a`** differs from tested code head `77dec86` only in
`README.md`; no production tasks, filters or helper changed after those passes.
All task-owned test VMs were removed, confirmed against the remote/project list.

The ordinary controller uses ansible-core 2.21.6/community.general 13.5.0 and
the authenticated LXD client. The SSH fixture records its Ubuntu-packaged guest
Ansible version in command output. These are Ubuntu 24.04 Linux/systemd tests,
not Windows/mobile/mixed-version or arbitrary-topology evidence. Public logs
remain temporary; run IDs do not promise indefinite artifact availability.
