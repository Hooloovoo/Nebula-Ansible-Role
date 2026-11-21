# Allow inline Nebula debug SSH keys without a key-file registry

**Branch:** `pr/inline-debug-keys` (`7b381d8`).
**Base:** PR25, `a0527eb`. Independent of A–I.

Default an absent `nebula_sshd_key_registry` to an empty mapping in the node
template's file-key lookup. A user supplying only inline public keys should not
need to define the optional file-key registry. The rendered configuration for
existing deployments with that registry is unchanged.

This is a one-line template fix. It supports ordinary debug-console usage and
the integration test's fresh-handshake/certificate probe. It adds no production
probe and does not enable SSH debugging by default.

The focused 1.12.0 case failed without the fix and passed with it, verifying the
rendered inline key while leaving the registry undefined:

```sh
python testing/lxd/run.py --remote REMOTE --project PROJECT \
  --role-path /path/to/inline-debug-keys --scenario role-feature \
  --feature-case debug-keys --nebula-version 1.12.0
```
