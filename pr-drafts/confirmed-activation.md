# H — Confirm activation and recover after controller loss

**Branch:** `pr/confirmed-activation` (`71c7f94`).
**Base:** `pr/cert-issuer-drift` (`7d9e84f`).

## Why

Ansible rescue cannot restore connectivity after the connection carrying Ansible
has disappeared. A locally valid certificate can also be rejected by peers.
Provide a host-local rollback deadline and an explicit controller confirmation
step, shared by ordinary PKI changes and subsequent configuration updates.

## Changes

- Persist a root-only transaction snapshot and per-service pending marker before
  mutation; schedule systemd rollback independently of the controller.
- Use a small Python helper and `flock` to serialize publication, confirmation
  and recovery. Reject expired transactions and independent file changes.
- Run optional controller-side `nebula_confirmation_command` after local runtime
  verification. Its argv must test the site's required management/peer path.
- Disarm only the matching transaction. Preserve recovery material if restoration
  or cleanup is incomplete; record whether rollback came from controller or timer.
- Protect co-located certificate publication with the same lock. No-op runs report
  zero changes even when transient snapshot work is required.

This is Linux/systemd recovery for existing running services. It does not make
multiple file replacements atomic, reverse a fleet transition, or prove peer
trust without a suitable confirmation command. Restart-only changes can interrupt
sessions. The connectivity-check cron fixture is disabled in these tests.

## Verification

Implementation commits: `b612a55`, `32c0ebd`, `1794ca9`; later merges include
documented prerequisite compatibility fixes. Seven helper unit tests cover
publication/metadata, expired publication, symlinks, independent modifications,
failed disarm, and controller-versus-watchdog rollback origin.

The actual implementation head `509cdf7` passed 7 LXD assertions on 1.12.0
(`nebtest-a7e1b4d86e`), including controller termination, autonomous restoration
and watchdog-origin evidence. The integrated rotation head `e3e094b` also passed
the 11-assertion SSH recovery case (`nebtest-ad5e1fd665`): a locally valid but
peer-untrusted certificate was rejected and restored, and an SSH-blocking
firewall change recovered autonomously. Nine failed SSH samples were expected
during that deliberate outage, not uninterrupted-connectivity evidence.

Reproduce H's standalone case from the regression checkout:

```sh
python testing/lxd/run.py --remote REMOTE --project PROJECT \
  --role-path /path/to/confirmed-activation --scenario role-feature \
  --feature-case confirmation --nebula-version 1.12.0
python -m unittest discover -s /path/to/confirmed-activation/tests -v
```

The SSH fixture requires J's workflow files and is integration evidence for the
shared helper, not a standalone H command. Current head `71c7f94` adds inherited
legacy CA-bundle fixes; final-stack reruns are recorded separately in
`testing/lxd/verification.md`. Windows, reboot during a transaction, unrelated
external writers and every possible systemd race are not established here.
