# LXD regression tests

These tests deploy a role checkout to three disposable Ubuntu LXD VMs (lighthouse, server, client or CA host). They use the real Nebula binaries, services, role tasks and templates. Certificate checks read real certificates; firewall and CA checks use traffic over the Nebula overlay. The suite runs Nebula 1.8.0 and 1.9.5 by default. It can take a substantial amount of time, particularly on a remote LXD host.

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

`LXD_REMOTE` and `LXD_PROJECT` supply defaults for the corresponding options. `--image` defaults to `ubuntu:24.04`; `--profile` to `default`; `--memory` to `1GiB`; `--cpus` to `2`. Use `--artifacts` to choose the parent directory for logs and manifests. `--scenario` accepts `auto`, `all`, `certificates`, `certificate-format-upgrade`, `firewall`, or `ca-host`. `all` requires the corresponding features in the selected checkout; explicit scenarios fail fast if their feature is absent. The feature branches can therefore be tested independently before merging. The version option may be repeated. See `--help` for the complete interface.

Every run uses random instance names and a per-run ownership marker. Normal cleanup removes only VMs created by that run. The manifest allows cleanup after an interruption; retained instances must still have the expected marker. Do not edit manifests or reuse instance names. If the client loses access or a launch fails before its marker is written, inspect the LXD project and resolve cleanup manually.

## What is checked

The certificate scenario checks empty, single and multiple groups; group additions, removals, replacement and clearing; order-insensitive idempotence; and actual certificate/key hashes. The firewall scenario checks actual allowed and denied TCP connections, template output and `nebula -test`. It exercises node and lighthouse inbound group rules and client outbound group rules.

The CA-host scenario checks the default lighthouse CA and a fresh separate CA-only host: private-key placement, certificate verification and distribution, hosts-file delegation, live services and overlay traffic in both directions, idempotency and hash preservation, provisioned check mode, invalid and unprovisioned CA errors, and use of the CA as a node. With `--baseline-role-path`, it first deploys that checkout with the default CA and verifies that applying the tested role leaves the deployment unchanged. Without it, the default deployment and repeat both use the tested role. The scenario recreates its VMs between the default and separate-CA cases.

`group: ssh-clients` matches one group. `groups: [ssh-clients, admins]` requires **both** ssh-clients **AND** admins; either alone is insufficient. Separate rules are alternatives (OR). A `host: any` selector in a group rule would also match peers outside that group.

The suite tests Linux guests; it does not establish Windows runtime compatibility. It changes no existing instances and does not run on the LXD host itself. Management uses the authenticated LXD connection; test traffic uses Nebula addresses.

## Adding tests

Add assertions and reusable helpers to `run.py`, using the existing inventory, deployment, certificate and probe helpers. Use named `check()` assertions and ensure every negative connectivity case has a working listener and a positive control. Update this document when coverage or prerequisites change. `deploy.yml` performs the initial full-role installation; `reconcile.yml` reuses the role's real node and lighthouse task files for later state transitions.
