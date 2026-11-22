# PR A independent-review follow-up

## Candidate and agreed scope

Submission: **`pr/pki-install-submission` at `e9bf712`**, one feature commit
directly on PR25 `a0527eb`. The original `pr/pki-install` remains at `2d4b0c4`.
The candidate was developed and tested as a working-tree follow-up to that
original branch, then consolidated without content changes into the submission
commit. No published history was rewritten. B–J retain their recorded heads.

Protect normal certificate-network changes with validation, restart and recovery.
Reject a pre-existing installed-certificate/interface mismatch before replacing
the consumer's files or activating its service. Do not automatically repair it.
The CA-side signing cache remains outside A's installation transaction.

Retain original backups if restoration or its verification fails. If backup
creation itself fails, do not restore partial snapshots over untouched files.
The installer resets its state on every include; only a fully created backup
whose protected publication has begun is eligible for restoration.

## TDD sequence

| Stage | Nebula | Case | Result / run ID |
|---|---|---|---|
| Original `2d4b0c4` | 1.12.0 | `pki-drift` | Red: automatic repair violates early-rejection contract; `nebtest-fb4512eacc` |
| Original `2d4b0c4` | 1.12.0 | `pki-recovery` | Red: failed restore deletes the original-key backup; `nebtest-1068ba68cd` |
| Targeted fixes, before refactor | 1.12.0 | `pki-drift` | 8 passed; `nebtest-79e7e3f751` |
| Targeted fixes, before refactor | 1.12.0 | `pki-recovery` | 6 passed; `nebtest-3f8418b253` |
| Refactored candidate | 1.12.0 | `pki-drift` | 8 passed; `nebtest-33ef74f9c6` |
| Refactored candidate | 1.12.0 | `pki-recovery` | 6 passed; `nebtest-5425e20737` |
| Refactored candidate | 1.12.0 | `pki-validation` | 9 passed; `nebtest-16e5695652` |
| Before strict service-probe validation | 1.12.0 | `pki-probe` | Red: probe error silently selects unvalidated copy; `nebtest-47694ce4a5` |
| Final strict-probe candidate | 1.12.0 | `pki-probe` | 5 passed; `nebtest-f13e2e5334` |
| Final strict-probe candidate | 1.8.0 | `pki-validation` | 9 passed; `nebtest-080a29f642` |

The recovery case was subsequently extended with two partial-backup checks;
final-matrix recovery runs therefore require 8 assertions, not the earlier 6.

### What the focused cases establish

- Drift with unchanged inputs and with replacement PKI fails before target
  publication. File hashes, PID and existing interface address stay unchanged;
  no backup is created.
- A real mismatched-key validation failure reaches a deliberately broken remote
  restore command. The play fails, retains the root-owned mode-0700 snapshot,
  reports its path and original cause, and leaves the original process running.
  The retained original key permits byte-identical independent recovery.
- Partial backup creation cannot trigger restoration of incomplete originals;
  disposable partial snapshots are removed without changing the live files.
- A separate `nebula -test` wrapper rejects an otherwise valid new pair. Its
  stdout diagnostic is preserved, while `-vvv` output contains neither raw nor
  base64-slurped private-key payloads from the new tasks.
- Two installer includes in one play back up/activate only once and retain the
  same process for a compatible update.
- With `tun.disabled: true`, changed certificate networks restart successfully;
  preflight, activation and invalid-PKI recovery skip interface-address queries.
  This is local Linux/systemd evidence, not overlay connectivity for a disabled
  tunnel.
- Unexpected process-probe errors cannot silently select the inactive-service
  copy path. They stop before publication, preserving files/PID and creating no
  backup. A genuinely stopped service still copies valid replacement PKI and
  starts through the normal role path without a transaction backup.

## Full acceptance matrix

The original A matrix was repeated serially on Nebula 1.8.0 and 1.12.0,
using Ubuntu 24.04 LXD VMs, real systemd/journald/interfaces, and PR25 as the
existing-deployment baseline. `safe-pki --pki-mode install` retains both the PKI
and IP/restart scenarios. The stale-runtime fixture now requires rejection on
the narrowed A path; preserved integrated branches continue to require repair.
All ordinary renumbering and injected restart-failure recovery checks remain.

Final PKI task-source SHA-256:
`0a92edaef70057956c25d268ef7b9444bf34c293d1718195fc3b53c4ac069535`.
This covers task names and contents, including helpers that were untracked while
the tests ran. The committed submission has the identical task hash. Its full
role tree is `fc30f0aa2789add79d414c41e2404739b0e59e1f`; the snapshot contains only
the role files and end-user README, with test/evidence files committed separately.

Both releases passed **98 PKI/adoption + 60 IP/restart + 30 focused checks** on
the final task source. Before the final service-probe guard, 1.8.0 also passed
98 PKI/adoption (`nebtest-c25627e797`),
60 IP/restart (`nebtest-e72111bf09`), 8 drift (`nebtest-f5453c9a30`) and
8 recovery (`nebtest-04b3d2395a`) assertions. Those runs had task-source hash
`4e5b55d0288d9cae76fbe28a35b29015c291ec298d2095793e0c9ab6e7295a3d`.

The subsequent disabled-tunnel run `nebtest-dab801c03f` passed its first five
checks, then failed because the installer took an inactive path despite a
running service. The original probe's raw result was not captured at normal
verbosity, and a verbose replay worked; the transient's cause is not established.
It is not counted as a complete validation pass. Independently injecting a
service-probe error reproduced the unsafe fallback deterministically, motivating
the strict guard above. Final 1.8.0 validation then passed all nine checks.
The retained failed-run VMs were removed through ownership-checked cleanup.
Both complete core matrices were subsequently repeated on the final strict-probe code.
The first final-code attempt, `nebtest-e3c9d173a0`, completed its first 41 checks
but could not provision its second topology: a new lighthouse VM's LXD agent
never became ready. The role had not been applied to that new VM. This is an
incomplete infrastructure-failed run, not a complete acceptance pass. Its owned
VMs were removed and the unchanged, source-hash-pinned matrix was restarted.
The final-code retry completed all 98 PKI/adoption checks on 1.8.0
(`nebtest-e5eaea4d97`). Its IP run (`nebtest-eeb691b351`) was interrupted by
an LXD API TCP read timeout while observing the interface after a successful
prefix update. The remote was reachable on a subsequent health check. That
partial IP run is not counted as a pass; its owned VMs were removed and only
the interrupted scenario and remaining matrix were rescheduled.

Completed final-code runs:

| Nebula | Scenario | Assertions | Run ID |
|---|---|---:|---|
| 1.8.0 | PKI/adoption | 98 | `nebtest-e5eaea4d97` |
| 1.8.0 | IP/restart | 60 | `nebtest-47ad65aba9` |
| 1.8.0 | Early drift rejection | 8 | `nebtest-07f525365b` |
| 1.8.0 | Partial backup / incomplete recovery | 8 | `nebtest-a9014309c7` |
| 1.8.0 | Probe error / stopped service | 5 | `nebtest-6024d73626` |
| 1.8.0 | Forced validation / same-play / disabled tunnel | 9 | `nebtest-080a29f642` |
| 1.12.0 | PKI/adoption | 98 | `nebtest-46b1347a0a` |
| 1.12.0 | IP/restart | 60 | `nebtest-211f8b7d77` |
| 1.12.0 | Early drift rejection | 8 | `nebtest-806fc3da36` |
| 1.12.0 | Partial backup / incomplete recovery | 8 | `nebtest-1cdf067275` |
| 1.12.0 | Probe error / stopped service | 5 | `nebtest-f13e2e5334` |
| 1.12.0 | Forced validation / same-play / disabled tunnel | 9 | `nebtest-db85d5bc62` |

The final recovery runs explicitly require a marker proving that the corrupt
partial-backup injection executed; they cannot pass merely because some earlier
infrastructure operation failed. `nebtest-a9014309c7` supersedes the earlier
1.8.0 recovery pass `nebtest-36b9195f73` with that stronger predicate.

The controller used ansible-core 2.21.6, community.general 13.5.0 and Python
3.14.4. Core matrices and most focused runs used `ANSIBLE_PIPELINING=true`.
The final 1.12.0 validation run and the marker-confirmed 1.8.0 recovery run used
Ansible's default non-pipelined mode (`ANSIBLE_PIPELINING=false`); production
role source and acceptance predicates were unchanged. Ten fast runner tests,
Python compilation, YAML parsing and deployment/test-playbook syntax checks
also passed.

The 1.12.0 PKI attempt `nebtest-b3dc75d023` was interrupted by an LXD WebSocket
EOF while setting permissions for a copy. The installer restored the saved files,
passed its original-process and interface checks, cleaned its disposable backup,
and correctly failed the play for the original transport error. This is not a
complete PKI-suite pass. Its owned VMs were removed; only the outstanding 1.12.0
scenarios were restarted on the same pinned source.

The validation attempt `nebtest-f1daf0e2e8` reached its deliberately forced
`nebula -test` failure, but an empty LXD module response interrupted restoration.
The role retained the root-owned mode-0700 backup at `/tmp/nebula-pki-kchx7_qx`
and reported the original cause and recovery location. Independent copies from
that snapshot restored the original certificate/key hashes, unchanged CA hash
and original PID (1498), verified against the pre-run record. Its VM set was
then removed. This incomplete run is not counted as nine passing checks.

A combined non-pipelined retry completed validation on 1.12.0, then the outer
20-minute controller-command deadline interrupted 1.8.0 recovery after seven
passing checks (`nebtest-fff2351c46`). That partial run was cleaned up and not
counted as complete. Running that case alone with a longer outer deadline passed
all eight checks (`nebtest-a9014309c7`).

All task-owned VMs were removed, confirmed by the remote/project instance list.
The verified source was subsequently committed as `e9bf712` on the separate
submission branch. GitHub publication remains an outside-workshop owner action.
Dependent branches need deliberate integration of A's helper-layout changes;
their older recorded results do not certify this submission or vice versa.

## Reproduction

From the regression-harness checkout, with the authenticated LXD client and
controller dependencies available:

```sh
python testing/lxd/run.py --remote REMOTE --project PROJECT \
  --role-path /path/to/pr-a --baseline-role-path /path/to/pr25 \
  --scenario safe-pki --pki-mode install \
  --nebula-version 1.8.0 --nebula-version 1.12.0 --cpus 2
python testing/lxd/run.py --remote REMOTE --project PROJECT \
  --role-path /path/to/pr-a --scenario role-feature --feature-case pki-drift \
  --nebula-version 1.8.0 --nebula-version 1.12.0 --cpus 2
python testing/lxd/run.py --remote REMOTE --project PROJECT \
  --role-path /path/to/pr-a --scenario role-feature --feature-case pki-recovery \
  --nebula-version 1.8.0 --nebula-version 1.12.0 --cpus 2
python testing/lxd/run.py --remote REMOTE --project PROJECT \
  --role-path /path/to/pr-a --scenario role-feature --feature-case pki-validation \
  --nebula-version 1.8.0 --nebula-version 1.12.0 --cpus 2
python testing/lxd/run.py --remote REMOTE --project PROJECT \
  --role-path /path/to/pr-a --scenario role-feature --feature-case pki-probe \
  --nebula-version 1.8.0 --nebula-version 1.12.0 --cpus 2
```

## Readability remeasurement

### Whole-role scale

Against the PR25 base `a0527eb`, the original task directory has 8 files and
588 lines. The candidate has 17 files and 932 lines: 345 new PKI lines and a
net one-line reduction in the callers, for **344 net added lines / 58.5% growth**.
The upstream pre-PR25 figure of 587 differs by the JSON-compatibility comment.
The shorter main installer does not remove this overall review cost.

Reproduce with `python testing/lxd/measure-pki.py ROLE [--revision 2d4b0c4]`.
The counts include blank/comment lines in the PKI task files, excluding README,
callers and tests. The script also parses all measured YAML.

| Measurement | Original | Refactored |
|---|---:|---:|
| Main installer lines | 140 | 66 |
| All PKI task lines | 302 | 345 |
| Longest line | 360 | 123 |
| Distinct `pki_*` tokens, including inputs/helper filenames | 29 | 43 |
| Declared fact/result/scoped-variable names | 24 | 34 |
| Unique fact-or-result names | 22 | 20 |
| Registered command/module results | 11 | 8 |
| Scoped aliases/parameters | 2 | 14 |

This is not a claimed total-variable-count reduction. Additional scoped names
replace long repeated expressions and make shared verification inputs explicit;
mutable fact/result names decrease slightly. Total task lines increase by 43,
including recovery/probe correctness fixes and shorter line wrapping. We retained
network validation/recovery rather than reduce the diff by falling back to an
unprotected restart handler. The primary readability gain is the short main
transaction flow, separate inspection/activation/recovery responsibilities and
plain named reload/restart process checks.

### Two-minute walkthrough

Read/reset the consumer state and reject an inconsistent enabled interface.
Compare incoming checksums. For a changed running deployment, finish all backup
copies before marking publication as needing recovery. Copy the supplied files;
classify certificate networks, validate and activate only a real live content
change. Verify process, available journal evidence and expected addresses, then
release the backup. On failure, preserve a safe original diagnostic, attempt
restoration only from a completed eligible snapshot, retain that snapshot if
recovery is incomplete, and fail the play. Cleanup removes only disposable
backups. First-install/inactive paths copy/start through the existing role flow.

This walkthrough is an author self-check, not an independent usability test.

## Second-review checks: drift definition and service states

The enabled-tunnel check requires every installed-certificate address/prefix
string to be present on the selected interface, using the existing config's
`tun.dev`. It does not require set equality: extra IPv4/IPv6 interface addresses
are tolerated, and order is irrelevant. A missing address or differing prefix
fails. There is no additional IP canonicalization beyond the CLI outputs and
no supported ignore-drift option. Disabled tunnels skip address checks.
The integration matrix covers single-address IPv4 deployments, not IPv6 or
multi-address certificate behavior; the generic comparison is not proof of
support for those deployments.

`check-pki-probe-states.py` extracted the unchanged production probe and running
flag expression, then executed them with real Ansible against synthetic
`systemctl` output/return-code fixtures. All **15 classifications passed**:

- Active with a positive PID selects the protected path.
- Unconfigured, inactive, failed and unknown-unit results select the existing
  unconfigured/non-running path.
- Activating, reloading, deactivating, maintenance and refreshing results stop;
  the code does not wait for them or treat them as inactive.
- Probe errors, zero PID, failed PID lookup, malformed output and contradictory
  status/output also stop.

Default Ansible failure output showed the raw probe `rc`, `stdout` and `stderr`;
the production probe task is not marked `no_log`. These are isolated controller
classification checks, not a live reproduction of the unexplained transient or
the `RestartSec=42s` hypothesis. No real service was queried or changed.

```sh
python testing/lxd/check-pki-probe-states.py /path/to/pr-a
```

Production task source remains at the recorded SHA-256. No additional LXD matrix
was required for these test/documentation-only changes. An independent cold-read
narration test is still outstanding; this check does not substitute for one.

## Committed submission handoff

- Source branch: `pr/pki-install-submission`, `e9bf712`, parent `a0527eb`.
- Preserved original branch: `pr/pki-install`, `2d4b0c4`.
- Regression/evidence branch: `feature/lxd-regression-tests`.
- Maintainer-facing body: `pr-drafts/pki-install-submission.md` on the regression branch.

The source commit's tree was checked against an isolated index containing the
exact verified README, caller changes and all nine PKI files. The task hash is
unchanged, so the recorded real-VM results apply to the committed code; no new
runtime behavior was introduced merely by consolidating the Git history.
After consolidation, the 10 runner tests, 15 synthetic probe-state checks
against the submission checkout, and deployment-playbook syntax check passed.
The real-VM matrix was not repeated for this identical-source Git handoff.
