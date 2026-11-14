# Regression integration verification

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

### Known failure: IP renumbering

The separate `safe-apply-ip-change` scenario fails on **both 1.8.0 and 1.11.2**
against the tested safe-PKI role. Changing the node from `10.193.0.2/24` to
`10.193.0.4/24` returns a successful Ansible status, but the tunnel still has
the old address and fresh peer traffic to the new address fails. The scenario
collects addresses, certificate JSON, journal and ping results before asserting.
On 1.11.2 the journal explicitly reports `Networks in new cert was different
from old`, while the installed certificate contains the new network and the
interface still has the old one.

Run IDs: `nebtest-e9981a2555` (1.8.0), `nebtest-1bd8c97f2b` (1.11.2).

This is a failing acceptance test, not an expected-failure pass. The role needs
either a working renumbering path or explicit rejection with restoration. A
successful reload log/fingerprint alone does not establish working renumbering.
`all` includes this test and currently exposes the defect; `auto` excludes the
opt-in safe-PKI and upgrade scenarios.

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
