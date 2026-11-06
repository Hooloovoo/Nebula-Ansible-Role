# Rotating the Nebula CA

These playbooks rotate a CA for Linux hosts already deployed by this role. They use the role's standard `nebula_config_dir`, its `nebula` and `lighthouse` systemd services, and the configured `nebula_ca_host`. They do not migrate other installations or upgrade Nebula. Windows participants are not supported.

## Inventory and prerequisites

Declare every host that must continue communicating, including lighthouses and the Ansible controller if it is a Nebula participant, in the `nebula_rotation` inventory group. The playbooks reject `--limit` and check mode; an omitted host cannot be discovered automatically. The CA host must be in inventory and have the role's `nebula-cert`, active `ca.crt`, active `ca.key`, and lighthouse certificate in `nebula_config_dir`. The first certificate in the signer CA bundle must be the CA corresponding to `ca.key`.

Set the same `nebula_ca_host`, `nebula_config_dir`, `nebula_network_name`, `nebula_ca_cert_duration`, and `nebula_rotation_id` for all participants. Use a new rotation ID containing only letters, numbers, underscores or hyphens for each new CA. If `nebula_ca_host` is unset, the first lighthouse is used. All participants must have a running role-managed Nebula service, accessible systemd journal, and a working management connection.

For example, reuse existing inventory groups when the signer is also a Nebula participant and the local Ansible controller:

```yaml
all:
  vars:
    nebula_ca_host: controller
    nebula_rotation_id: rotation_2027
    nebula_network_name: my_nebula_network
    nebula_ca_cert_duration: 87600h
  children:
    nebula_lighthouse:
      hosts:
        lighthouse:
    nebula_nodes:
      hosts:
        controller:
          ansible_connection: local
        node:
    nebula_rotation:
      children:
        nebula_lighthouse: {}
        nebula_nodes: {}
```

Add the `nebula_rotation` group to the existing inventory rather than defining the hosts again. Include every group containing a participant; the controller belongs in `nebula_nodes` here because it runs Nebula. A CA-only signer must still be in inventory but need not be in `nebula_rotation`. Check the resulting membership with `ansible-inventory -i inventory --graph nebula_rotation`.

Configure the ordinary role variables for each host too, including certificate groups when using the certificate-groups feature. The example names are inventory aliases; the certificate's existing name is preserved.

## Run the phases

From the role checkout, run each phase with the complete inventory:

```sh
ansible-playbook -i inventory rotation_prepare.yml
ansible-playbook -i inventory rotation_migrate.yml
ansible-playbook -i inventory rotation_finalize.yml
```

Use the same ID for all phases. **Prepare** generates the replacement CA once, preserving the original CA's restrictions, curve and certificate version. It distributes overlapping public trust and reloads every participant. It also verifies the original and replacement signing keys before changing participant trust.

**Migrate** first checks that every participant has the replacement CA in its trust bundle, then processes one host at a time. It signs the host's existing public key, preserving its name, networks, subnets, groups and certificate version. The host private key stays unchanged. A replacement certificate is verified before atomic installation; Nebula reloads without a service restart. A handled activation failure restores the pre-run certificate and stops the play.

**Finalize** first verifies that every participant has loaded a replacement-CA certificate. It then promotes the new signing key, removes the original CA from each public trust bundle and updates the signer's bundle. Unrelated CAs are preserved. The old public CA and private key are retained under `nebula_config_dir/ca-rotations/<id>/`; protect and retain these files until no longer needed for recovery.

Repeat an interrupted phase with the same inventory and ID. Do not run ordinary deployment tasks concurrently with rotation: the signer's active key and public bundle change during finalization. After finalization, ordinary role runs use the promoted CA paths. A certificate's lifetime is limited by the replacement CA's validity.

## Connectivity and recovery

The role validates the generated configuration and checks the service PID and Nebula reload logs. A reload avoids deliberately tearing down existing tunnels, but log checks alone are not a proof that all future Nebula handshakes will succeed. Verify fresh connections across the mesh before finalization. The LXD regression suite forces new handshakes and checks peer issuers, but cannot guarantee connectivity on every network.

Ansible can restore a file after a *handled* activation failure. If it loses access before rescue runs, there is no autonomous rollback timer. Restore the previous file and reload from an independent management path if needed. Nodes accessible only through Nebula require a separately planned recovery mechanism; watchdog timers are not part of this feature.

Snap-to-direct-install migration, arbitrary service layouts, Vault-backed signing, and overlay-IP changes are outside this workflow. Bring such hosts into the role-managed layout separately before running it.
