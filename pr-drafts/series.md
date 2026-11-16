# Focused normal-role PR drafts

## C — Allow certificate subjects independent of inventory names

Branch `pr/cert-subject` (`10415ae`), base B. Add `nebula_cert_name` while keeping
canonical file paths and defaults unchanged. This preserves host-rule identities
when short inventory names differ from certificate FQDNs. An override alone
does not regenerate existing certificates. The focused 1.12.0 test failed on B
(`nebtest-51a0561a36`) and passed 5 assertions on C (`nebtest-2a0aeafb54`).

## D — Separate signing identity from distributed CA trust

Branch `pr/ca-signing-inputs` (`b9504f4`), base C. Add explicit signing cert/key
and supplied-bundle paths with existing defaults. Verify against an isolated
intended CA and require it in the runtime trust bundle. Include mixed desired
bundle/in-place certificate change detection. This is independently useful for
external signers and bundles with historical entries; it does not orchestrate
CA rotation. The test failed on C (`nebtest-eab1548bae`) then passed 5 assertions
on both 1.10.3 (`nebtest-ec29e25d02`) and 1.12.0 (`nebtest-a797450e81`), including
an expired historical CA before the intended signer. Runtime bundle policy is
tested separately from isolated leaf verification.

## E — Stage reissue and preserve private-key identity

Branch `pr/staged-cert-signing` (`6719db2`), base D. Reuse the existing public
key and verify temporary output before publishing. Signing failure preserves
the prior files. Existing Linux consumers no longer need their key cached on
the CA host. Public-key export reuses the prototype's small curve-aware helper;
it introduces no cryptographic reimplementation. The 1.12.0 key-preservation
test failed on D (`nebtest-860346a472`). Five assertions passed on 1.8.0
(`nebtest-ccde0fb0a8`) and 1.12.0 (`nebtest-a8e9a39168`). Windows runtime was not
tested; its transport path remains compatible but is not claimed verified.

## F — Reconcile lighthouse certificates through the normal PKI path

Branch `pr/lighthouse-cert-drift` (`eeb75c5`), base E. Remove duplicated lighthouse
signing/distribution and reuse node reconciliation, including local snapshots
for a co-located CA. IP/group drift now reissues the lighthouse certificate, so
existing deployments must have correct desired values. Rendering is unchanged.
The test failed on E (`nebtest-76e027b36c`), then 6 assertions passed on 1.8.0
(`nebtest-2ba2fd3cd1`) and 1.12.0 (`nebtest-2ab1101a91`): key/PID preservation,
fresh connectivity, repeated convergence and staging cleanup.

## G — Reconcile the selected signing issuer

Branch `pr/cert-issuer-drift` (`e9d397b`), base F. Include the selected CA
fingerprint in certificate drift detection. Ordinary role runs can reissue
under a replacement signer using existing keys. Operators must distribute
overlap trust before switching inputs; this PR does not infer fleet readiness.
The test failed on F (`nebtest-090ee55074`), then passed 11 assertions on 1.12.0
(`nebtest-9f2bb1a5bc`), covering all three identities, process/key preservation,
no-op repeats and removal of old trust through normal role runs. This was LXD
management; SSH-over-Nebula orchestration is a later gate, not implied by it.

## Remaining drafts

H (confirmation/rollback), I (classified config reload) and J (fleet rotation)
will be filled with actual branch heads and red/green evidence as implemented.
Do not copy reference results into a submission as evidence of untested heads.
