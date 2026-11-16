# Recover and activate PKI changed by an in-place signer

## Summary

Snapshot a CA-node's live certificate/key before signing. Compare pre-sign and
post-sign state, then reuse A's validation, reload/restart and restoration path.
The caller owns restoration and cleanup across signing and installation.

Without a pre-sign snapshot, the install step sees identical desired/current
files after signing, although the daemon may still use the previous identity.
The snapshot also allows recovery when signing deletes files and then fails.

## Scope and dependencies

This is a dependent PR on A, not another installer. It changes only the case
where the signer modifies this host's live files before apply. Network changes
still restart; compatible changes reload. A co-located CA/lighthouse's missing
re-sign path is not silently fixed here. Staged signing follows separately.

The snapshot may create temporary backup work on a no-drift run; zero reported
changes and no activation do not mean no temporary files were ever created.
The role README should explain user-visible recovery and operational limits,
not the review stack or version matrix.

## Testing

Run `--scenario safe-pki --pki-mode in-place` against this candidate, fresh and
with `--baseline-role-path` pointing to A. Run `--pki-mode combined` on A+B and
compare assertion manifests with both separate modes.

Reference selector rehearsal against integrated `8a2c46b`, on 1.8.0: 39 in-place
reload/signing-failure assertions, 3 baseline-upgrade assertions and 17 in-place
IP/restart assertions passed. These do not certify the yet-unextracted B head.
Record that head's results and a baseline bug reproducer before submission.

Actual branch: `pr/pki-in-place`, head `2ef39af`, based on A `2d4b0c4`.
The 1.12.0 test on A failed at `client reload reports current fingerprint`
(`nebtest-281b3a2d1a`). After B, 42 reload/upgrade and 17 restart assertions
passed on each of 1.8.0 and 1.12.0. Green run IDs: `nebtest-893f3ee16e`,
`nebtest-4868c113f4`, `nebtest-a83fc00357`, `nebtest-ec4e3b553a`.
