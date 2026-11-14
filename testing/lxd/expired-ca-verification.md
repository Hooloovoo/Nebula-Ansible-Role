# Expired historical CA: operation-specific compatibility

## Result

The 1.10.3/1.11.2 difference is in **`nebula-cert verify`**, not in daemon startup
or `nebula -test`, for a bundle containing an expired historical CA plus the valid
CA that issued the leaf.

| Input / operation | Nebula 1.10.3 | Nebula 1.11.2 |
|---|---|---|
| Valid intended CA alone: `verify`, `-test`, startup | Pass | Pass |
| Expired CA first + valid intended CA: `verify` | Exit 1, expired-CA pool error | Exit 0 |
| Valid intended CA first + expired CA: `verify` | Exit 1, expired-CA pool error | Exit 0 |
| Either mixed order: `-test` | Exit 0 | Exit 0 |
| Either mixed order: startup | Alive; expired-CA warning | Alive; expired-CA warning |
| Either mixed order: SIGHUP from valid-only trust | Alive; expired-CA warning; no CA-load error | Alive; expired-CA warning; no CA-load error |
| Expired-only trust: `verify` | Exit 1, expired-CA pool error | Exit 1, issuer CA not found |
| Expired-only trust: `-test` / startup | Exit 1 / exits | Exit 1 / exits |
| Malformed input: `verify`, `-test`, startup | All reject | All reject |
| Expired-only or malformed trust: SIGHUP from valid-only trust | Process survives, logs CA-load failure | Process survives, logs CA-load failure |

The 1.10.3 mixed-bundle verification error is:

```text
Error: error while adding ca cert to pool: historical: certificate is expired
```

Failed reloads log `Failed to load ca from config`. For expired-only trust the
cause is `no valid CA certificates present`. A client-certificate-refreshed log
also appears during these failed CA reloads. Therefore neither survival of the
PID nor a certificate-refresh/fingerprint log alone establishes CA activation.

Both versions successfully sign using an isolated intended CA certificate/key
and verify the resulting leaf against that isolated CA. Both reject a leaf
issued by a separate unrelated CA when verification uses only the intended CA.
These are separate controls from the supplied-bundle compatibility checks.

## Fixture and environment

- Disposable Ubuntu 24.04 LXD VM: `nebca-261114-a710`, remote `mythie`, project
  `opencode-temp`; one CPU, 1 GiB RAM. Existing instances were not changed.
- Release binaries downloaded from the official GitHub release URLs; their
  hashes are recorded in `results.json`.
- All shared CA/leaf fixtures generated once using `nebula-cert` 1.10.3.
- Version-1, Curve25519 intended CA, historical CA and unrelated CA. Intended
  and unrelated CAs have 87600h validity; the historical CA has 1s validity.
- The probe waits 3s and confirms expiry from `notAfter` before running cases.
- A valid leaf for `10.233.250.1/24` is signed only by the intended CA, with
  8760h validity. A wrong-issuer leaf is signed only by the unrelated CA.
- The *same bytes* of each fixture are used for both releases. Bundle order is
  tested both ways without filtering out the expired entry.
- Runtime uses a real TUN interface (`nexpca`), lighthouse mode, loopback UDP
  port 4242 and text logging. Each case starts a separate process. Reload cases
  start with valid-only trust, replace the bundle and send that process SIGHUP.

Archive SHA-256 values:

```text
1.10.3  99ac335caeb69d02a6b6b00a3d4b5d0a36ec3971df480a1cc50e6db378342955
1.11.2  6140d33f2ec21ce7f6b655b5bc820e93a684d97e51d0ddcf907324b5b28aac1e
```

## Commands and reproduction

[`expired-ca-probe.py`](expired-ca-probe.py) runs the probe inside a disposable
Linux VM with root access, outbound access to GitHub and a working `/dev/net/tun`.
Pass a work directory that does not already exist:

```sh
python3 /root/expired-ca-probe.py --workdir /root/expired-ca-results-final
```

For each version, the operations are:

```sh
nebula-cert verify -ca CASE.bundle -crt leaf.crt
nebula -test -config config.yml
nebula -config config.yml
# For each reload case, start with valid-only trust, replace live-ca.crt,
# then send SIGHUP to that process (the probe uses process.send_signal).
```

Separate signing/verification controls use:

```sh
nebula-cert sign -name isolated-sign -ip 10.233.250.2/24 \
  -ca-crt intended.crt -ca-key intended.key \
  -out-crt signed.crt -out-key signed.key
nebula-cert verify -ca intended.crt -crt signed.crt
nebula-cert verify -ca intended.crt -crt wrong-issuer.crt
```

The completed run on 2026-11-14 produced `results.json` plus individual stdout,
stderr and process logs. Rechecking the recorded results passed **68 pinned,
operation-specific contract assertions**:

```sh
python3 testing/lxd/expired-ca-probe.py \
  --check-results /tmp/opencode/expired-ca-final-results.json
```

The first attempt used an invalid lighthouse `listen.port: 0`; the valid-only
startup control rejected it before the matrix completed. The configuration was
corrected to port 4242 and the entire matrix was run again with fresh fixtures.
The table above describes only that completed run.

## Scope and implications

This is a binary compatibility/process probe, not an Ansible-role deployment
or a connectivity test. Runtime observations use process status and logs over
short observation windows; fresh peer handshakes and loaded trust contents were
not independently inspected. An expired historical CA appearing in the accepted
bundle does not imply certificates issued by that expired CA would be accepted.
No behavior is inferred for untested releases or certificate formats.

Preserve the supplied bundle for its compatibility test. Signing identity and
leaf-issuer verification should use the isolated intended CA so historical or
unrelated entries cannot select or satisfy the intended-signer check. Runtime
bundle validation is a separate check; an isolated leaf-verification success
does not validate the contents or activation of a broader runtime bundle.

The current safe-PKI role's use of `nebula-cert verify -ca ca.crt` can therefore
reject this mixed bundle on 1.10.3 even when that version's daemon can load it.
Changing that role behavior needs its own reviewed implementation and tests;
this probe does not change production tasks.

The public JSON result was copied to the controller at the path shown above.
Temporary artifacts are not permanent storage. The disposable VM, including
its generated private keys, was removed after collecting evidence.
