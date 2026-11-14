# Ansible Role for Nebula

Quickly and easily deploy the [Nebula Overlay VPN](https://github.com/slackhq/nebula) software onto all of your hosts.

# What Is Nebula

> Nebula is a scalable overlay networking tool with a focus on performance, simplicity and security. It lets you seamlessly connect computers anywhere in the world.

You can read more about Nebula [on the official repo](https://github.com/slackhq/nebula)

# Example Playbook
```
---
- name: Deploy Nebula
  hosts: all
  gather_facts: yes
  user: ansible
  become: yes
  vars:
    nebula_version: 1.8.0
    nebula_network_name: "Company Nebula Mgmt Net"
    nebula_network_cidr: 16

    nebula_lighthouse_internal_ip_addr: 10.43.0.1
    nebula_lighthouse_public_hostname: lighthouse.company.com
    nebula_lighthouse_public_port: 4242

    nebula_firewall_drop_action: reject

    nebula_inbound_rules:
      - { port: "any", proto: "icmp", host: "any" }
      - { port: 22, proto: "tcp", host: "any" }
    nebula_outbound_rules:
      - { port: "any", proto: "any", host: "any" }

  roles:
    - role: nebula
```

# Example Inventory
```
[nebula_lighthouse]
lighthouse01.company.com

[servers]
web01.company.com nebula_internal_ip_addr=10.43.0.2
docker01.company.com nebula_internal_ip_addr=10.43.0.3
zabbix01.company.com nebula_internal_ip_addr=10.43.0.4
backup01.company.com nebula_internal_ip_addr=10.43.0.5
pbx01.company.com nebula_internal_ip_addr=10.43.0.6
```

**Note:** More variables can be found in the [role defaults.](defaults/main.yml)

Nebula lives in `nebula_config_dir` (default `/opt/nebula`) on Linux hosts. Windows hosts are supported as nodes
(not as lighthouses): the release zip is installed to `nebula_windows_install_dir` (default `C:\Program Files\Nebula`)
and run as the `Nebula` service. Certificates are signed on the Linux CA host, which defaults to the lighthouse.
Because Windows nodes are usually managed over Nebula itself, the restart handler runs through a scheduled task
and waits for the connection to come back.

# Safe PKI updates

On running Linux nodes and separate-CA lighthouses, changed certificate/key/CA files are backed up remotely, installed as a set, checked with both `nebula-cert verify` (leaf against the CA bundle) and `nebula -test` (configuration and key pair), and reloaded without restarting. Activation checks require the same active PID and, when available, journal evidence of the refreshed certificate and fingerprint with no PKI load errors. Failure restores the original files and reloads them before failing the play; root-only backups are removed automatically. Failure messages name the failed task and include a bounded diagnostic, or state that details are hidden when that task uses `no_log`.

Nodes that also host the CA are snapshotted before signing, so in-place re-signing is detected. Signing still briefly removes the live certificate/key before replacing them; staged signing is a follow-up. A co-located CA/lighthouse has no automatic re-sign path: manually forcing its re-sign requires a manual reload.

Internally, `pki_snapshot.yml` records the pre-update state, `pki_backup.yml` makes remote backups, `install_pki.yml` applies and validates the update, and `pki_restore.yml` restores a failed transaction. The node's signing block owns the snapshot and cleanup for in-place signing; ordinary installations manage their own transaction. The `pki_in_place` flag describes signing that has already modified live files, rather than simply identifying the CA host. No phase selector is needed.

Without an active service and existing config, files are installed normally. Config changes retain validation and restart handling; unit/binary handling and Windows behavior are unchanged. Check mode performs no snapshot or reload. Existing certificate transport still uses registered slurp results; backups stay remote and key-copy/backup tasks suppress content and diffs. The two validation commands expose their non-secret diagnostics.

# Custom CA Host

By default the lighthouse holds the CA private key and signs every certificate. To keep the CA key off a
publicly reachable lighthouse, set `nebula_ca_host` to the inventory hostname of another Linux host:

```yaml
nebula_ca_host: admin-workstation.company.com
```

The CA host signs certificates for all nodes and the lighthouse. Other hosts receive only their own certificate,
key and `ca.crt`. The CA host can also be a node, or can omit `nebula_internal_ip_addr` to act only as the CA.

Use the same `nebula_ca_host` and `nebula_config_dir` on the lighthouse and all nodes. For a fresh install,
include the CA host in the same play batch as the lighthouse and use Ansible's default `linear` strategy, so
`nebula-cert` is installed before signing begins. If using `serial`, `strategy: free`, or a limit that excludes
the CA host, install Nebula on the CA host first. A clean install on the CA host removes its CA key and certificates.

When moving an existing CA, securely copy every `.crt` and `.key` file in `nebula_config_dir` on the old CA host
(the CA, the lighthouse and each node) to the same directory on the new CA host before running the role. If you
copy only `ca.crt` and `ca.key`, the role signs a new certificate and key for the lighthouse and for every node on
its next run, restarting Nebula on each one. After verifying the move, remove the private keys from hosts that
should no longer hold them.

# Certificate Groups

Nebula firewall rules can match groups embedded in a host's certificate. Set `nebula_cert_groups` per host,
for example in `host_vars`:

```yaml
nebula_cert_groups:
  - ssh-clients
  - jellyfin-clients
```

The default is an empty list, which signs certificates without groups. When a node's groups change, its certificate
is re-signed on the next run, just as when its IP address changes. Group order does not matter.
The lighthouse's own certificate is only signed once; changing its groups requires deleting its certificate and
key first.

# Firewall Rules with Certificate Groups

Set certificate groups on the connecting host, for example in `host_vars/admin-laptop.yml`:

```yaml
nebula_cert_groups:
  - ssh-clients
  - admins
```

Then set rules on the receiving host, for example in `host_vars/application-server.yml`:

```yaml
nebula_inbound_rules:
  # peer must be in both groups
  - port: 22
    proto: tcp
    groups: [ssh-clients, admins]
  - port: 8096
    proto: tcp
    group: jellyfin-clients
nebula_outbound_rules:
  - { port: any, proto: any, host: any }
```

`group` matches one group; `groups` requires **every** listed group (AND). Separate rules provide
alternatives (OR). Rules match the peer's certificate: the client for inbound traffic and the
destination for outbound traffic. Omit `host: any` from a group-restricted rule: it would also match
hosts outside the group. Setting `nebula_inbound_rules` replaces the default allow-all list.

# SSH Debug Console

This role supports Nebula's built-in SSH debug console feature. To enable it, set:

```yaml
nebula_sshd_enabled: true
nebula_sshd_listen: "127.0.0.1:2222"  # Optional, defaults to 127.0.0.1:2222
nebula_sshd_authorized_users:
  - user: admin
    keys:
      - "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... admin@host"
    key_files:
      - "/path/to/admin.pub"
  - user: developer
    key_files:
      - "~/.ssh/developer_key.pub"
```

You can specify SSH keys either:
- **Inline** using the `keys` field with the full public key string
- **From files** using the `key_files` field with paths to public key files
- **Both** in the same user entry

The role automatically generates an ED25519 SSH host key at `{{ nebula_config_dir }}/ssh_host_ed25519_key` when the SSH daemon is enabled.

# Running the Playbook
```
ansible-playbook -i inventory nebula.yml
```
