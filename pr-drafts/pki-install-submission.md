# Validate received PKI and reload compatible changes

## TL;DR

- Reload compatible certificate/key/CA updates; restart changed certificate networks.
- Back up and validate running-service updates, with recovery on failure.
- Stop before replacing target files if required certificate addresses are missing from the tunnel.
- Enabled-tunnel checks require iproute2's `ip -json`, normally installed on major Linux distributions.
- CA-host-node signing and unconfigured/inactive/Windows paths retain their existing behavior.

## Problem and scope

The role currently copies PKI files and restarts Nebula. A restart interrupts
overlay traffic and invalid files can leave the service unable to start.
Compatible updates can reload, but changed certificate addresses or prefixes
require restart: Nebula refuses to activate them on reload.

Protected updates apply to configured, running Linux nodes and lighthouses
receiving PKI from a separate CA host. Signing, key transfer and configuration
rendering are unchanged. Address verification reads the kernel interface through
iproute2; minimal images may need the package rather than a BusyBox-only `ip`.

## Why stop on pre-existing drift?

This deliberately changes behavior on an already inconsistent host: a PKI
change that previously caused a restart might have healed it incidentally;
this installer stops before replacing target files or restarting instead.

For example, the installed certificate may say `.5/24` while the daemon/interface
still uses `.1/24`. Backing up and restoring the installed files preserves the
`.5` identity, not the old running `.1` identity. A failed repair therefore cannot
promise to restore the previous working state from that backup. Use independent
access to restore a known-good pair or validate and activate the intended files,
then rerun the role. Automatic repair with a different recovery contract is
outside this change.

The check requires **every certificate address/prefix** on the configured tunnel:
the textual CIDR values must match, regardless of order. Extra interface
addresses, including IPv6 link-local addresses, do not cause rejection. Disabled
tunnels skip this check. There is no supported ignore-drift switch. Integration
tests cover the role's single-address IPv4 deployments; IPv6 and multi-address
certificate behavior have not been integration-tested.

## Installation and recovery

For a changed running deployment, back up the original files, copy the supplied
files sequentially, run `nebula-cert verify` and `nebula -test`, then activate.
Choose reload/restart by comparing certificates, not by matching log messages.
Verify process identity, available journal evidence and enabled-tunnel addresses.

On failure, attempt to restore files/metadata and reactivate the original inputs.
If recovery cannot be verified, retain the root-only backup and report its path
and the original failure. New copy/backup tasks hide private payloads with
`no_log`; backups are remote copies rather than another controller-side key read.
Unexpected service-probe errors and transitional states stop before replacement.

## Review guide

Start with `tasks/install_pki.yml`. The helpers fall into three responsibilities:

| Responsibility | Files under `tasks/` |
|---|---|
| Inspect files, service and network consistency | `pki_inspect.yml`, `pki_inspect_networks.yml`, `pki_inspect_certificate.yml` |
| Back up, validate, activate and verify | `pki_backup.yml`, `pki_activate.yml`, `pki_verify.yml` |
| Restore and report failures; retain recovery material | `pki_recover.yml`, `pki_restore.yml`; cleanup is owned by `install_pki.yml` |

`node.yml` and `lighthouse.yml` route received PKI through the installer while
keeping the same-host signing path. The branch depends on #25's certificate JSON
compatibility; until it merges, the diff against `master` includes its two commits.

## Testing

Passed on Ubuntu 24.04 LXD VMs with real Nebula **1.8.0 and 1.12.0**, systemd
and journald, using ansible-core 2.21.6/community.general 13.5.0:

- Fresh/stopped-service installation, unchanged adoption, no-op and check/diff runs.
- Same-process reloads retaining TCP traffic, followed by fresh peer handshakes.
- Address/prefix changes, startup-failure recovery and early drift rejection.
- Wrong-CA, mismatched-key, forced validation, backup and restoration failures.
- Repeated includes, disabled tunnels, probe errors and verbose key-payload hiding.

An additional controller-only test exercises the actual probe task against
synthetic transitional/error outputs; it does not reproduce a real restart race.
The harness is separate from this PR. Without LXD, check basic YAML syntax with:

```sh
python -c 'from pathlib import Path; import yaml; [yaml.safe_load(p.read_text()) for p in Path("tasks").glob("*pki*.yml")]'
```

An existing role playbook can also use
`ansible-playbook --syntax-check -i inventory playbook.yml`. These syntax checks
do not establish runtime recovery.

## Limits

- Local validity does not prove peer trust; coordinate overlap trust and verify
  fresh peer connections when changing issuers.
- Restarts can disconnect overlay management. Use independent access; this PR
  has no autonomous rollback after controller loss.
- Sequential copies are not atomic against external readers/restarts. Concurrent
  watchdog races, arbitrary custom tunnel/logging combinations and Windows
  runtime behavior are not established by these tests.
- Retained backups after incomplete recovery require operator intervention before retrying.
