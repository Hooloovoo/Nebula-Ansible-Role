# LXD regression tests

These tests deploy a role checkout to three disposable Ubuntu LXD VMs (lighthouse, server, client or CA host). They use the real Nebula binaries, services, role tasks and templates. Certificate checks read real certificates; firewall and CA checks use traffic over the Nebula overlay. The suite runs Nebula 1.8.0, 1.9.5, 1.11.2 and 1.12.0 by default. It can take a substantial amount of time, particularly on a remote LXD host.

## Prerequisites

- Python 3 with venv support, `pip`, and an authenticated `lxc` client on `PATH`. The client must already be able to access the chosen LXD remote and project; the suite does not configure authentication. `LXD_CONF` is inherited, if set.
- An existing LXD project and profile with networking, storage and support for Ubuntu VMs, and sufficient CPU, RAM and disk for three VMs. The guests require systemd, Python 3, `/dev/net/tun`, outbound access to GitHub releases, and network connectivity to each other.
- Network access from the controller to install the Python and Ansible collections below. The suite needs ansible-core 2.18 or newer for its chosen collection; this is a **test controller** requirement, not a change to the role's minimum Ansible version. Python itself must be supported by the selected ansible-core version.

For the Snap LXD client, ensure the `lxc` executable on `PATH` is usable with your authenticated configuration. For example, if required by your environment, put a wrapper on `PATH` that exports `LXD_CONF` and runs the Snap's `lxc` binary.

## Run

From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r testing/lxd/requirements.txt
ansible-galaxy collection install -r testing/lxd/collections.yml
python testing/lxd/run.py --remote myremote --project my-test-project
```

The project and remote must already exist. The current checkout is used by default, including uncommitted changes. By default (`--scenario auto`), the runner detects supported features and runs their scenarios separately on fresh VMs. If none are present, it exits before provisioning. A nonzero exit status indicates a failed assertion or infrastructure error. Named assertions are written to `results.log` and command output to `commands.log` under the system temporary directory in `nebula-lxd-tests/<run-id>/` by default. Logs can contain inventory and host configuration; review them before sharing.

Options include:

```sh
# Test an independent role checkout, including uncommitted edits.
python testing/lxd/run.py --remote myremote --project my-test-project \
  --role-path /path/to/firewall-feature-checkout --nebula-version 1.8.0 --scenario firewall

# Test the CA-host feature and upgrade an existing upstream default-CA deployment.
python testing/lxd/run.py --remote myremote --project my-test-project \
  --role-path /path/to/ca-feature-checkout --baseline-role-path /path/to/upstream-checkout \
  --scenario ca-host --nebula-version 1.8.0

# Keep failing VMs for diagnosis; print the cleanup command on failure.
python testing/lxd/run.py --remote myremote --project my-test-project --keep-on-failure

# Remove retained VMs after examining them.
python testing/lxd/run.py --cleanup /path/to/nebula-lxd-tests/nebtest-...
```

For certificate JSON compatibility (PR #25), select the role checkout containing
the fix and run the release matrix explicitly:

```sh
python testing/lxd/run.py --remote myremote --project my-test-project \
  --role-path /path/to/cert-json-compat --scenario certificates \
  --nebula-version 1.8.0 --nebula-version 1.9.5 --nebula-version 1.10.0 \
  --nebula-version 1.11.2 --nebula-version 1.12.0
python testing/lxd/run.py --remote myremote --project my-test-project \
  --role-path /path/to/cert-json-compat --scenario certificate-format-upgrade
```

`certificate-format-upgrade` creates version-1 certificates with Nebula 1.8.0,
then upgrades to 1.11.2 and checks certificate/key hashes, certificate versions
and groups. It runs once using that fixed pair regardless of `--nebula-version`.
It is opt-in under `auto` and included under `all`; the ordinary default matrix
remains 1.8.0 and 1.9.5. These tests use the selected checkout as-is: apply the
compatibility fix when testing newer binaries, rather than patching the role in
the test runner.

`LXD_REMOTE` and `LXD_PROJECT` supply defaults for the corresponding options. `--image` defaults to `ubuntu:24.04`; `--profile` to `default`; `--memory` to `1GiB`; `--cpus` to `2`. Use `--artifacts` to choose the parent directory for logs and manifests. `--scenario` accepts `auto`, `all`, `certificates`, `certificate-format-upgrade`, `firewall`, `ca-host`, `safe-apply`, or `safe-apply-ip-change`. `auto` selects supported ordinary certificate, firewall and CA-host tests; upgrade and safe-PKI acceptance tests are opt-in. `all` requires every scenario's features in the selected checkout; explicit scenarios fail fast if their feature is absent. The version option may be repeated. See `--help` for the complete interface.

Every run uses random instance names and a per-run ownership marker. Normal cleanup removes only VMs created by that run. The manifest allows cleanup after an interruption; retained instances must still have the expected marker. Do not edit manifests or reuse instance names. If the client loses access or a launch fails before its marker is written, inspect the LXD project and resolve cleanup manually.

## What is checked

The certificate scenario checks empty, single and multiple groups; group additions, removals, replacement and clearing; order-insensitive idempotence; and actual certificate/key hashes. The firewall scenario checks actual allowed and denied TCP connections, template output and `nebula -test`. It exercises node and lighthouse inbound group rules and client outbound group rules.

The CA-host scenario checks the default lighthouse CA and a fresh separate CA-only host: private-key placement, certificate verification and distribution, hosts-file delegation, live services and overlay traffic in both directions, idempotency and hash preservation, provisioned check mode, invalid and unprovisioned CA errors, and use of the CA as a node. With `--baseline-role-path`, it first deploys that checkout with the default CA and verifies that applying the tested role leaves the deployment unchanged. Without it, the default deployment and repeat both use the tested role. The scenario recreates its VMs between the default and separate-CA cases.

The `safe-apply` scenario is detected when the role contains `tasks/install_pki.yml`.
It checks fresh installation, remote-CA node and lighthouse reloads, CA-node
in-place re-signing, PID and fingerprint evidence, persistent TCP streams,
fresh handshakes forced by restarting only the peer after continuity checks,
no-op/check/diff runs, config-only and combined changes, invalid-PKI rollback,
interrupted local signing, byte-identical restoration and temporary-file cleanup.
The separate opt-in `safe-apply-ip-change` acceptance scenario requires either
a successful renumbering with the new tunnel address and a fresh peer handshake,
or an explicit failure preserving the original files, process and connectivity.
It records addresses, certificate JSON and journal evidence before assertions.
This prevents a successful play from hiding a divergence between the certificate
and the running tunnel. It is included in `all`, but excluded from `auto`.
Wrong-CA cases use a matching leaf/key signed by a separate CA while retaining
the deployed trust bundle. They prove `nebula -test` alone accepts that input,
then require CA verification to reject it, expose the cause, restore the old
files and fingerprint, keep the same PID, and remove backups. These cover nodes,
separate-CA lighthouses and in-place CA-node signing; key-mismatch diagnostics
are checked separately.
Pass `--baseline-role-path` to verify a zero-change upgrade from that role first.
For Nebula 1.10+, both tested and baseline roles need upstream PR #25's JSON
compatibility patch in the throwaway test stack; it is not part of safe apply.
The co-located CA/lighthouse has no automatic re-sign path and is excluded.
The role templates use text-format logging; fingerprint assertions exercise
that format. The fixture disables the connectivity-check cron job, so it does
not test concurrent watchdog restarts or atomic publication of multiple files.
Connectivity checks in this suite establish behavior for its test topology;
they do not imply the role itself probes peers before accepting a reload.
CA rotation, stopped-service signing recovery and alternate logging settings
are not established by this scenario.

For example, run the safe-PKI acceptance matrix explicitly:

```sh
python testing/lxd/run.py --remote myremote --project my-test-project \
  --role-path /path/to/safe-pki-test-stack \
  --baseline-role-path /path/to/baseline-with-json-compat \
  --scenario safe-apply --nebula-version 1.8.0 --nebula-version 1.9.5 \
  --nebula-version 1.11.2 --nebula-version 1.12.0

# Separate IP-change acceptance test; currently exposes a known role defect.
python testing/lxd/run.py --remote myremote --project my-test-project \
  --role-path /path/to/safe-pki-test-stack --scenario safe-apply-ip-change \
  --nebula-version 1.11.2
```

The baseline must predate safe-PKI application; both checkouts should include
only the prerequisite fixes needed for the selected Nebula releases. Failure
injection stays in the test playbook and disposable VMs, with no production
test hooks. `safe-apply` requires certificate groups, a configurable CA host,
and `tasks/install_pki.yml`.

## Alignment with role changes

Keep coverage grouped by independently useful changes to the regular role:

- Merged CA-host and certificate-group/firewall features: `ca-host`,
  `certificates`, and `firewall`.
- Certificate JSON compatibility (PR #25): the explicit certificate release
  matrix and `certificate-format-upgrade`.
- Safe PKI application: `safe-apply`, including existing-deployment upgrades,
  connection continuity, trust verification, and rollback.

Full CA rotation remains a later end-to-end acceptance target. Experimental
scenarios and helper playbooks are preserved on
`testing/lxd-rotation-experiments`; the earlier modular rotation suite is on
`feature/lxd-ca-rotation-tests`. Extract general-purpose checks from those
branches as supporting role PRs settle their interfaces. They are not part of
this runner's default or `all` scenarios. Add the final rotation acceptance
scenario when its documented workflow can be exercised literally.

See [verification results](verification.md) for the tested revisions, completed
runs, known failing IP-change case and coverage limitations.

`group: ssh-clients` matches one group. `groups: [ssh-clients, admins]` requires **both** ssh-clients **AND** admins; either alone is insufficient. Separate rules are alternatives (OR). A `host: any` selector in a group rule would also match peers outside that group.

The suite tests Linux guests; it does not establish Windows runtime compatibility. It changes no existing instances and does not run on the LXD host itself. Management uses the authenticated LXD connection; test traffic uses Nebula addresses.

## Adding tests

Add assertions and reusable helpers to `run.py`, using the existing inventory, deployment, certificate and probe helpers. Use named `check()` assertions and ensure every negative connectivity case has a working listener and a positive control. Update this document when coverage or prerequisites change. `deploy.yml` performs the initial full-role installation; `reconcile.yml` reuses the role's real node and lighthouse task files for later state transitions.
