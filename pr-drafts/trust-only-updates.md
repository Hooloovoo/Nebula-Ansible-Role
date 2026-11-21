# Distribute trust without reconciling leaf identities

**Branch:** `pr/trust-only-updates` (`06194cd`).
**Base:** `pr/config-reload` (`c2acb52`).

Add `nebula_reconcile_certificates`, defaulting to `true`. Setting it to `false`
distributes the desired CA bundle through the ordinary protected installer
without reconciling certificate subject, address, groups or issuer. Existing
leaf/key files remain required. Local verification and activation confirmation
still apply.

This makes overlap preparation independently useful and gives interrupted CA
migrations a trust-only resumption path: a partially migrated leaf must not be
reissued under the old CA merely to restore overlap. The switch is not itself a
fleet trust gate and does not bypass validation or create missing identities.

The focused implementation `0b23f67` passed 3 assertions on 1.12.0, covering
unchanged leaf/key identity, distributed trust and resumed ordinary reconciliation. J's
prepare/repeat and interrupted-state guards exercise the same path in an SSH
rotation; see the integrated evidence. Later head changes merge prerequisites.

```sh
python testing/lxd/run.py --remote REMOTE --project PROJECT \
  --role-path /path/to/trust-only-updates --scenario role-feature \
  --feature-case trust-only --nebula-version 1.12.0
```
