# Regression integration verification

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
