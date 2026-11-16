# Apply PKI changes safely on certificate consumers

## Summary

Back up changed PKI remotely, validate the complete installed certificate/key
set, activate with reload or restart as appropriate, and restore on a handled
failure. This applies to non-CA nodes and lighthouses using a separate CA host.

Compatible certificate/group/trust changes use SIGHUP. Certificate network or
prefix changes require restart and actual interface-address verification. Keep
the existing CA-node path unchanged in this PR; pre-sign snapshots follow in B.

## Why this boundary

The installer still sees the original live files, so it owns the transaction.
This change does not alter signing or introduce rotation orchestration. The
shared mechanisms also support later trust-bundle changes in the normal role.

## Compatibility and limits

- First installation and inactive-service paths install/start normally.
- No-op runs do not activate; check mode does not mutate files or signal services.
- Config/unit/binary and Windows behavior retain their existing paths.
- IP changes interrupt connections and require independent management.
- Local leaf/config validation does not prove peer acceptance of a changed CA.
- Copies are not atomic against external restarts; controller-loss recovery is
  a separate feature, not provided by Ansible rescue alone.

## Dependencies and testing

Base: submitted PR #25 (`a0527eb`) for newer certificate JSON. Branch
`pr/pki-install`, head `2d4b0c4`. Use `--scenario safe-pki
--pki-mode install`, fresh and with an upstream+PR25 baseline.

Reference evidence (not yet evidence for the extracted candidate): integrated
`8a2c46b` passed 137 safe-PKI and 77 restart assertions per release on 1.8.0 and
1.11.2. Selector rehearsal on 1.8.0 passed 95 install-mode reload/rollback and
60 install-mode IP/restart assertions. Include candidate red/green results,
changed counts, hash/PID preservation and remaining gaps before submission.

Actual candidate: **98 reload/upgrade and 60 IP/restart assertions passed on
each of 1.8.0 and 1.12.0**, using upstream+PR25 as baseline. Run IDs: 1.8.0
`nebtest-69c1ce8146`, `nebtest-141a752b7a`; 1.12.0 `nebtest-ee0f4ae627`,
`nebtest-5ea711e6cb`. The base lacks the required feature and is rejected by
the acceptance preflight. Peer-ordering rejection, autonomous rollback after
controller loss and watchdog races remain separate acceptance work.
