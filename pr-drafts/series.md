# Focused normal-role PR drafts

## C — Allow certificate subjects independent of inventory names

Branch `pr/cert-subject` (`10415ae`), base B. Add `nebula_cert_name` while keeping
canonical file paths and defaults unchanged. This preserves host-rule identities
when short inventory names differ from certificate FQDNs. An override alone
does not regenerate existing certificates. The focused 1.12.0 test failed on B
(`nebtest-51a0561a36`) and passed 5 assertions on C (`nebtest-2a0aeafb54`).

## D — Separate signing identity from distributed CA trust

Branch `pr/ca-signing-inputs` (`6be8e73`), base C. Add explicit signing cert/key
and supplied-bundle paths with existing defaults. Verify against an isolated
intended CA and require it in the runtime trust bundle. Include mixed desired
bundle/in-place certificate change detection. This is independently useful for
external signers and bundles with historical entries; it does not orchestrate
CA rotation. The test failed on C (`nebtest-eab1548bae`) then passed 5 assertions
on both 1.10.3 (`nebtest-ec29e25d02`) and 1.12.0 (`nebtest-a797450e81`), including
an expired historical CA before the intended signer. Runtime bundle policy is
tested separately from isolated leaf verification.

The follow-up preserves existing shared-path bundles: isolate their first CA
for leaf verification, but distribute the complete bundle unchanged. Separate
signer inputs must contain exactly one public CA. Trust membership compares
decoded public certificate bytes, avoiding the multiple JSON documents emitted
by older binaries. Five legacy checks passed on 1.8.0 (`nebtest-5e23df6eae`),
1.12.0 (`nebtest-3441a7ad04`) and, with an expired historical entry, 1.10.3
(`nebtest-8cb4805a85`). Six selection/strict-input unit tests pass.

## E — Stage reissue and preserve private-key identity

Branch `pr/staged-cert-signing` (`b4e0957`), base D. Reuse the existing public
key and verify temporary output before publishing. Signing failure preserves
the prior files. Existing Linux nodes no longer need their key cached on
the CA host; F extends the shared path to lighthouses. Public-key export reuses the prototype's small curve-aware helper;
it introduces no cryptographic reimplementation. The 1.12.0 key-preservation
test failed on D (`nebtest-860346a472`). Five assertions passed on 1.8.0
(`nebtest-ccde0fb0a8`) and 1.12.0 (`nebtest-a8e9a39168`). Windows runtime was not
tested; its transport path remains compatible but is not claimed verified.

Current-head follow-up isolates the signing CA for both issue and verification.
The complete staged-failure/key-preservation case passed again on 1.12.0
(`nebtest-aaab03b164`, 5 assertions); a shared bundle containing an expired CA
passed on 1.10.3 (`nebtest-513252ad32`, 5 assertions). The runtime bundle is not
filtered to make either test pass.

The documentation follow-up scopes key preservation to nodes until F adds the
shared lighthouse path, and corrects CA-host migration/cleanup instructions.
The literal default-path migration from PR25 passed 8 assertions on 1.12.0
(`nebtest-9829c9c93d`): complete cache/signing identity retained, consumer hashes
and PIDs unchanged, zero consumer changes, zero-change repeat and retained own
private key after removing old signing/cached-peer keys.

## F — Reconcile lighthouse certificates through the normal PKI path

Branch `pr/lighthouse-cert-drift` (`079e481`), base E. Remove duplicated lighthouse
signing/distribution and reuse node reconciliation, including local snapshots
for a co-located CA. IP/group drift now reissues the lighthouse certificate, so
existing deployments must have correct desired values. Rendering is unchanged.
The test failed on E (`nebtest-76e027b36c`), then 6 assertions passed on 1.8.0
(`nebtest-2ba2fd3cd1`) and 1.12.0 (`nebtest-2ab1101a91`): key/PID preservation,
fresh connectivity, repeated convergence and staging cleanup.

## G — Reconcile the selected signing issuer

Branch `pr/cert-issuer-drift` (`3f67862`), base F. Include the selected CA
fingerprint in certificate drift detection. Ordinary role runs can reissue
under a replacement signer using existing keys. Operators must distribute
overlap trust before switching inputs; this PR does not infer fleet readiness.
The test failed on F (`nebtest-090ee55074`), then passed 11 assertions on 1.12.0
(`nebtest-9f2bb1a5bc`), covering all three identities, process/key preservation,
no-op repeats and removal of old trust through normal role runs. This was LXD
management; SSH-over-Nebula orchestration is a later gate, not implied by it.

Compatibility follow-up `7d9e84f` isolates the same first public CA used by legacy
signing, rather than parsing an entire shared bundle as one leaf. Five checks
passed each on 1.8.0 (`nebtest-841ddc3605`), 1.12.0 (`nebtest-604aad17c0`) and
1.10.3 with an expired historical entry (`nebtest-8f95dea008`).
The final head additionally merges only the migration documentation update.

## H onward

- [H: confirmed activation and autonomous rollback](confirmed-activation.md)
- [I: classified configuration reload](config-reload.md)
- [Trust-only ordinary-role updates](trust-only-updates.md)
- [Inline debug SSH keys](inline-debug-keys.md), independent of A–I
- [J: fleet CA rotation](ca-rotation.md)

The dependency index and current-versus-historical evidence are in
[`../nebula-rotation-pr-series-plan.md`](../nebula-rotation-pr-series-plan.md).
Review a focused diff with `git diff BASE..BRANCH`; use the listed immediate
base, not upstream, to avoid including all prerequisite PRs. These are local
drafts, not opened or approved upstream PRs.
