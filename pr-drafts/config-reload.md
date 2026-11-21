# I — Reload supported firewall changes through the protected transaction

**Branch:** `pr/config-reload` (`c2acb52`).
**Base:** `pr/confirmed-activation` (`101af7d`).

## Why and scope

Ordinary firewall updates should use Nebula's reload support without an
unnecessary restart, while retaining recovery if the new rules block management.
Reuse H's snapshot, deadline, publication and confirmation path rather than
building a second installer.

Compare parsed configuration mappings. Reload only semantic changes wholly
within `firewall.inbound` and `firewall.outbound`; unchanged mappings need no
activation. Unknown, mixed or other changes conservatively select restart.
Combine compatible PKI/configuration updates and suppress the duplicate handler.
Retain default rendering to avoid incidental restarts on adoption.

The allow-list is intentionally limited to the tested settings. Logging,
lighthouse settings and other plausible reload candidates are not enabled by
this PR. Local validity is insufficient for management confirmation; configure
the controller-side command when the overlay carries Ansible.

## Verification

Implementation `b19e033`, with follow-up `b8df101`, passed 8 focused LXD
assertions each on 1.8.0 and 1.12.0: loaded group policy, conservative restart,
failed-confirmation restoration, invalid-input preservation and idempotence. Four
classifier unit tests cover no change, reloadable rules, unknown/mixed changes
and invalid top-level input. The exact current dependency merge includes the
legacy signing fixes; see the final-stack record for subsequent reruns.

```sh
python testing/lxd/run.py --remote REMOTE --project PROJECT \
  --role-path /path/to/config-reload --scenario role-feature \
  --feature-case config --nebula-version 1.8.0 --nebula-version 1.12.0
```

Integrated `e3e094b` additionally passed the full 39-assertion 1.12.0 firewall
scenario (`nebtest-c192ed8ce5`), and its SSH recovery scenario restored the
management-blocking rule using watchdog rollback. The latter intentionally
causes failed connection samples and is not a continuity pass.
