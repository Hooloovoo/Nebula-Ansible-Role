#!/usr/bin/env python3
"""Run role regression tests against disposable LXD virtual machines."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid

import yaml

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
ANY = [{'port': 'any', 'proto': 'any', 'host': 'any'}]
PORT = 18080
PKI_SCENARIOS = ('safe-apply', 'safe-apply-ip-change')


def select_scenarios(requested, supported):
    if requested == 'auto':
        return [s for s in supported if s in ('certificates', 'firewall', 'ca-host')]
    if requested == 'all':
        return ['certificates', 'certificate-format-upgrade', 'firewall', 'ca-host', *PKI_SCENARIOS]
    if requested == 'safe-pki':
        return list(PKI_SCENARIOS)
    return [requested]


class Suite:
    def __init__(self, args, version):
        self.args, self.version = args, version
        self.run_id = 'nebtest-' + uuid.uuid4().hex[:10]
        self.names = {key: self.run_id + '-' + key for key in ('lh', 'server', 'client')}
        self.path = Path(args.artifacts).expanduser().resolve() / self.run_id
        self.path.mkdir(parents=True)
        self.log = self.path / 'commands.log'
        self.created = []
        self.groups = {'lh': [], 'server': ['service', 'initial'], 'client': None}
        self.node_ips = {'server': '10.193.0.2', 'client': '10.193.0.3'}
        self.inbound = {'lh': ANY, 'server': ANY, 'client': ANY}
        self.outbound = {'lh': ANY, 'server': ANY, 'client': ANY}
        self.passed = 0
        self.assertions = []
        self.status = 'running'
        self.manifest()

    def manifest(self):
        (self.path / 'manifest.json').write_text(json.dumps({
            'remote': self.args.remote, 'project': self.args.project,
            'run_id': self.run_id, 'instances': self.created, 'version': self.version,
            'scenario': getattr(self.args, 'active_scenario', None),
            'pki_mode': self.args.pki_mode,
            'role_path': str(self.args.role_path),
            'baseline_role_path': str(self.args.baseline_role_path) if self.args.baseline_role_path else None,
            'status': self.status,
            'feature_case': getattr(self.args, 'feature_case', None),
        }, indent=2) + '\n')

    def command(self, command, check=True, timeout=600):
        with self.log.open('a') as log:
            log.write('\n$ ' + ' '.join(map(str, command)) + '\n')
            log.flush()
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, text=True, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError(f'Command failed ({result.returncode}): {command}; see {self.log}')
        return result.returncode

    def output(self, command, check=True):
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=90)
        with self.log.open('a') as log:
            log.write('\n$ ' + ' '.join(map(str, command)) + '\n' + result.stdout + result.stderr)
        if check and result.returncode:
            raise RuntimeError(f'Command failed: {command}: {result.stderr}; see {self.log}')
        return result

    def lxc(self, *args):
        return ['lxc', *args, '--project', self.args.project]

    def guest(self, host, *command, check=True):
        return self.output(['lxc', 'exec', self.args.remote + ':' + self.names[host],
                            '--project', self.args.project, '--', *map(str, command)], check)

    def check(self, name, condition):
        line = ('PASS ' if condition else 'FAIL ') + name
        with (self.path / 'results.log').open('a') as results:
            results.write(line + '\n')
        self.assertions.append({'name': name, 'passed': bool(condition)})
        (self.path / 'assertions.json').write_text(json.dumps(self.assertions, indent=2) + '\n')
        if not condition:
            raise AssertionError(name)
        self.passed += 1
        print(line, flush=True)

    def provision(self):
        self.command(self.lxc('project', 'show', self.args.remote + ':' + self.args.project))
        self.command(self.lxc('profile', 'show', self.args.remote + ':' + self.args.profile))
        self.command(self.lxc('list', self.args.remote + ':'))
        self.command(self.lxc('info', self.args.remote + ':', '--resources'))
        self.command(self.lxc('storage', 'list', self.args.remote + ':'))
        for name in self.names.values():
            # Refuse collisions even though names are random; never adopt an existing VM.
            existing = self.output(self.lxc('list', self.args.remote + ':', '--format', 'json')).stdout
            if any(item['name'] == name for item in json.loads(existing)):
                raise RuntimeError(f'Instance already exists: {name}')
            # Record intent before launch so an interrupted launch can be cleaned up.
            self.created.append(name)
            self.manifest()
            self.command(self.lxc('launch', self.args.image, self.args.remote + ':' + name,
                                  '--vm', '--profile', self.args.profile,
                                  '-c', 'limits.memory=' + self.args.memory,
                                  '-c', 'limits.cpu=' + str(self.args.cpus),
                                  '-c', 'user.nebula-test-run=' + self.run_id), timeout=900)
        for host in self.names:
            for attempt in range(90):
                if self.guest(host, 'true', check=False).returncode == 0:
                    break
                time.sleep(2)
            else:
                raise RuntimeError(f'LXD agent did not become ready on {host}')
            self.guest(host, 'cloud-init', 'status', '--wait')
            self.guest(host, 'python3', '-c', 'import os; f=os.open("/dev/net/tun", os.O_RDWR); os.close(f)')
        entries = json.loads(self.output(self.lxc('list', self.args.remote + ':', '--format', 'json')).stdout)
        lh = next(x for x in entries if x['name'] == self.names['lh'])
        addresses = [a['address'] for interface in lh['state']['network'].values()
                     for a in interface.get('addresses', []) if a['family'] == 'inet' and a['scope'] == 'global']
        if not addresses:
            raise RuntimeError('Lighthouse has no global IPv4 address; check the LXD profile network')
        self.lh_ip = addresses[0]

    def inventory(self):
        common = {'ansible_connection': 'community.general.lxd', 'ansible_lxd_remote': self.args.remote,
                  'ansible_lxd_project': self.args.project, 'ansible_python_interpreter': '/usr/bin/python3',
                  'nebula_version': self.version, 'nebula_network_name': self.run_id,
                  'nebula_network_cidr': 24, 'nebula_lighthouse_hostname': 'lh',
                  'nebula_lighthouse_internal_ip_addr': '10.193.0.1',
                  'nebula_lighthouse_public_hostname': self.lh_ip,
                  'nebula_lighthouse_build_hosts_file': False,
                  'nebula_node_lighthouse_in_hosts_file': False,
                  'nebula_install_check_cron': False, 'tested_role': str(self.args.role_path)}
        hosts = {}
        for host, ip in [('lh', None), ('server', self.node_ips['server']), ('client', self.node_ips['client'])]:
            hosts[host] = {'ansible_host': self.names[host], 'nebula_inbound_rules': self.inbound[host],
                           'nebula_outbound_rules': self.outbound[host]}
            if self.groups[host] is not None:
                hosts[host]['nebula_cert_groups'] = self.groups[host]
            if ip:
                hosts[host]['nebula_internal_ip_addr'] = ip
        inv = {'all': {'vars': common, 'children': {
            'nebula_lighthouse': {'hosts': {'lh': hosts['lh']}},
            'nodes': {'hosts': {'server': hosts['server'], 'client': hosts['client']}}}}}
        path = self.path / 'inventory.json'
        path.write_text(json.dumps(inv, indent=2))
        return path

    def ca_inventory(self, separate=False, ca_node=False):
        common = {'ansible_connection': 'community.general.lxd', 'ansible_lxd_remote': self.args.remote,
                  'ansible_lxd_project': self.args.project, 'ansible_python_interpreter': '/usr/bin/python3',
                  'nebula_version': self.version, 'nebula_network_name': self.run_id,
                  'nebula_network_cidr': 24, 'nebula_lighthouse_hostname': 'lh',
                  'nebula_lighthouse_internal_ip_addr': '10.193.0.1',
                  'nebula_lighthouse_public_hostname': self.lh_ip,
                  'nebula_lighthouse_build_hosts_file': True,
                  'nebula_node_lighthouse_in_hosts_file': False,
                  'nebula_install_check_cron': False, 'tested_role': str(self.args.role_path)}
        if separate:
            common['nebula_ca_host'] = 'cah'
        ca = {'ansible_host': self.names['client']}
        if ca_node:
            ca['nebula_internal_ip_addr'] = '10.193.0.3'
        inv = {'all': {'vars': common, 'children': {
            'ca_hosts': {'hosts': {'cah': ca}} if separate else {'hosts': {}},
            'nebula_lighthouse': {'hosts': {'lh': {'ansible_host': self.names['lh']}}},
            'nodes': {'hosts': {'server': {'ansible_host': self.names['server'],
                                            'nebula_internal_ip_addr': '10.193.0.2'}}}}}}
        path = self.path / 'ca-inventory.json'
        path.write_text(json.dumps(inv, indent=2))
        return path

    def ca_deploy(self, separate=False, ca_node=False, limit=None, extra=(), check=True):
        path = self.ca_inventory(separate, ca_node)
        cmd = ['ansible-playbook', '-i', str(path), str(HERE / 'ca-deploy.yml')]
        if limit:
            cmd += ['--limit', limit]
        cmd += list(extra)
        start = self.log.stat().st_size if self.log.exists() else 0
        status = self.command(cmd, check=check, timeout=1200)
        with self.log.open() as log:
            log.seek(start)
            output = log.read()
        return status, output

    def ca_hashes(self, host, *names):
        out = self.guest(host, 'sha256sum', *('/opt/nebula/' + name for name in names)).stdout
        return tuple(line.split()[0] for line in out.splitlines())

    def ca_host(self):
        tested_role = self.args.role_path
        if self.args.baseline_role_path:
            self.args.role_path = self.args.baseline_role_path
        try:
            self.ca_deploy()
        finally:
            self.args.role_path = tested_role
        self.check('default CA private key on lighthouse', self.guest('lh', 'test', '-f', '/opt/nebula/ca.key', check=False).returncode == 0)
        self.check('default node certificate on lighthouse', self.guest('lh', 'test', '-f', '/opt/nebula/server.crt', check=False).returncode == 0)
        before = self.ca_hashes('lh', 'ca.key', 'ca.crt', 'lh.crt', 'server.crt')
        _, out = self.ca_deploy()
        self.check('default CA repeat or upgrade is idempotent', all(int(n) == 0 for n in re.findall(r'changed=(\d+)', out)) and len(re.findall(r'changed=(\d+)', out)) == 2)
        self.check('default CA repeat or upgrade preserves hashes', before == self.ca_hashes('lh', 'ca.key', 'ca.crt', 'lh.crt', 'server.crt'))
        self.cleanup()
        self.provision()
        self.ca_deploy(separate=True)
        self.check('separate CA private key exists', self.guest('client', 'test', '-f', '/opt/nebula/ca.key', check=False).returncode == 0)
        for host in ('lh', 'server'):
            self.check(host + ' has no CA private key', self.guest(host, 'test', '!', '-e', '/opt/nebula/ca.key', check=False).returncode == 0)
            for name in (host + '.crt', host + '.key', 'ca.crt'):
                self.check(host + ' receives ' + name, self.guest(host, 'test', '-f', '/opt/nebula/' + name, check=False).returncode == 0)
            self.guest('client', '/opt/nebula/nebula-cert', 'verify', '-ca', '/opt/nebula/ca.crt', '-crt', '/opt/nebula/' + host + '.crt')
            self.check(host + ' certificate matches CA host copy', self.ca_hashes(host, host + '.crt', 'ca.crt') == self.ca_hashes('client', host + '.crt', 'ca.crt'))
            if host == 'lh':
                self.check('lighthouse transferred key mode is 0600', self.guest(host, 'stat', '-c', '%a', '/opt/nebula/lh.key').stdout.strip() == '600')
        self.check('CA-only host has no node service', self.guest('client', 'test', '!', '-e', '/etc/systemd/system/nebula.service', check=False).returncode == 0)
        self.guest('lh', 'systemctl', 'is-active', 'lighthouse')
        self.guest('server', 'systemctl', 'is-active', 'nebula')
        self.check('lighthouse hosts entry delegated to lighthouse', '10.193.0.2 server.neb' in self.guest('lh', 'cat', '/etc/hosts').stdout)
        self.check('hosts entry not placed on CA', '10.193.0.2 server.neb' not in self.guest('client', 'cat', '/etc/hosts').stdout)
        self.check('node can reach lighthouse over Nebula', self.guest('server', 'ping', '-c', '3', '-W', '5', '10.193.0.1', check=False).returncode == 0)
        self.check('lighthouse can reach node over Nebula', self.guest('lh', 'ping', '-c', '3', '-W', '5', '10.193.0.2', check=False).returncode == 0)
        before = self.ca_hashes('client', 'ca.key', 'ca.crt', 'lh.crt', 'server.crt')
        _, out = self.ca_deploy(separate=True)
        counts = re.findall(r'changed=(\d+)', out)
        self.check('separate CA repeat is idempotent', len(counts) == 3 and all(int(n) == 0 for n in counts))
        self.check('separate CA repeat preserves hashes', before == self.ca_hashes('client', 'ca.key', 'ca.crt', 'lh.crt', 'server.crt'))
        _, out = self.ca_deploy(separate=True, extra=['--check'])
        counts = re.findall(r'changed=(\d+)', out)
        self.check('provisioned CA check mode has no changes', len(counts) == 3 and all(int(n) == 0 for n in counts))
        self.check('check mode preserves CA certificates and key', before == self.ca_hashes('client', 'ca.key', 'ca.crt', 'lh.crt', 'server.crt'))
        status, out = self.ca_deploy(separate=True, limit='lh', extra=['-e', 'nebula_ca_host=unknown'], check=False)
        self.check('unknown CA host rejected', status != 0 and 'nebula_ca_host must be an inventory hostname' in out)
        self.guest('server', 'mv', '/opt/nebula/nebula-cert', '/opt/nebula/nebula-cert.test-backup')
        try:
            status, out = self.ca_deploy(separate=True, limit='lh', extra=['-e', 'nebula_ca_host=server'], check=False)
            self.check('unprovisioned CA host rejected', status != 0 and 'must be Linux with executable nebula-cert' in out)
        finally:
            self.guest('server', 'mv', '/opt/nebula/nebula-cert.test-backup', '/opt/nebula/nebula-cert')
        self.ca_deploy(separate=True, ca_node=True, limit='cah')
        self.check('CA host can also be a node', self.guest('client', 'test', '-f', '/opt/nebula/cah.crt', '-a', '-f', '/opt/nebula/cah.key', '-a', '-f', '/opt/nebula/config.yml', check=False).returncode == 0)
        self.guest('client', 'systemctl', 'is-active', 'nebula')

    def deploy(self, limit=None):
        cmd = ['ansible-playbook', '-i', str(self.inventory()), str(HERE / ('reconcile.yml' if limit else 'deploy.yml'))]
        if limit:
            cmd += ['--limit', limit]
        self.command(cmd, timeout=1200)

    def cert(self, host):
        out = self.guest('lh', '/opt/nebula/nebula-cert', 'print', '-json', '-path', '/opt/nebula/' + host + '.crt').stdout
        details = json.loads(out)
        details = (details[0] if isinstance(details, list) else details)['details']
        # Nebula 1.10+ renames these fields even for version-1 certificates.
        details.setdefault('ips', details.get('networks') or [])
        details.setdefault('subnets', details.get('unsafeNetworks') or [])
        return details

    def hashes(self, host):
        out = self.guest('lh', 'sha256sum', '/opt/nebula/' + host + '.crt', '/opt/nebula/' + host + '.key').stdout
        return tuple(line.split()[0] for line in out.splitlines())

    def groups_are(self, host, groups):
        return sorted(self.cert(host).get('groups') or []) == sorted(groups)

    def certificates(self):
        self.check('default client certificate has no groups', self.groups_are('client', []))
        self.check('lighthouse default certificate has no groups', self.groups_are('lh', []))
        self.check('first node certificate has multiple groups', self.groups_are('server', ['service', 'initial']))
        before = self.hashes('client')
        self.groups['client'] = []
        self.deploy('client')
        self.check('explicit empty preserves default certificate', before == self.hashes('client'))
        lh_hashes = self.hashes('lh')
        self.groups['lh'] = ['lighthouse-admins']
        self.deploy('lh')
        if (self.args.role_path / 'tasks/node_pki.yml').is_file():
            self.check('lighthouse group drift reconciles through the shared signer', self.hashes('lh') != lh_hashes and self.groups_are('lh', ['lighthouse-admins']))
        else:
            self.check('lighthouse group drift does not re-sign', self.hashes('lh') == lh_hashes)
        for groups in (['lighthouse-admins'], ['lighthouse-admins', 'other'], []):
            self.guest('lh', 'systemctl', 'stop', 'lighthouse')
            self.guest('lh', 'rm', '-f', '/opt/nebula/lh.crt', '/opt/nebula/lh.key')
            self.groups['lh'] = groups
            self.deploy('lh')
            self.check('first lighthouse signing ' + str(groups), self.groups_are('lh', groups))
            self.guest('lh', 'systemctl', 'restart', 'lighthouse')
        for label, groups, changes in [
            ('single', ['ssh-clients'], True), ('single repeated', ['ssh-clients'], False),
            ('multiple', ['ssh-clients', 'admins', 'extra'], True),
            ('reordered', ['extra', 'admins', 'ssh-clients'], False),
            ('other order', ['admins', 'ssh-clients', 'extra'], False),
            ('replace', ['admins', 'other'], True), ('remove', ['admins'], True),
            ('clear', [], True), ('empty repeated', [], False),
        ]:
            before = self.hashes('client')
            self.groups['client'] = groups
            self.deploy('client')
            self.check(label + ' certificate contents', self.groups_are('client', groups))
            self.check(label + ' certificate and key changes', (before != self.hashes('client')) == changes)
        before = self.hashes('client')
        self.node_ips['client'] = '10.193.0.4'
        self.deploy('client')
        self.check('IP drift re-signs certificate', before != self.hashes('client'))
        self.check('IP drift certificate contents', self.cert('client')['ips'][0] == '10.193.0.4/24')
        before = self.hashes('client')
        self.node_ips['client'] = '10.193.0.3'
        self.groups['client'] = ['ssh-clients', 'admins']
        self.deploy('client')
        self.check('combined IP and group drift re-signs', before != self.hashes('client'))
        self.check('combined IP and group drift contents', self.cert('client')['ips'][0] == '10.193.0.3/24' and self.groups_are('client', self.groups['client']))
        for groups in (['ssh-clients'], ['ssh-clients', 'admins']):
            self.guest('lh', 'rm', '-f', '/opt/nebula/client.crt', '/opt/nebula/client.key')
            self.groups['client'] = groups
            self.deploy('client')
            self.check('first node signing ' + str(groups), self.groups_are('client', groups))

    def certificate_format_upgrade(self):
        self.version = '1.8.0'
        self.deploy()
        before = {host: self.hashes(host) for host in ('lh', 'server', 'client')}
        self.version = '1.11.2'
        self.deploy()
        for host in ('lh', 'server', 'client'):
            self.check(host + ' v1 certificate and key survive Nebula 1.11.2 upgrade',
                       self.hashes(host) == before[host])
            printed = json.loads(self.guest('lh', '/opt/nebula/nebula-cert', 'print', '-json',
                                            '-path', '/opt/nebula/' + host + '.crt').stdout)
            printed = printed[0] if isinstance(printed, list) else printed
            self.check(host + ' certificate remains version 1', printed.get('version') == 1)
        self.check('server v1 certificate retains groups after upgrade',
                   self.groups_are('server', ['service', 'initial']))
        self.check('client v1 certificate retains empty groups after upgrade', self.groups_are('client', []))

    def fresh_handshake(self, source, peer, address):
        def pid(host):
            service = 'lighthouse' if host == 'lh' else 'nebula'
            return self.guest(host, 'systemctl', 'show', service, '-p', 'MainPID', '--value').stdout.strip()
        source_pid, peer_pid = pid(source), pid(peer)
        # Restart only the peer, after the continuity check. Its empty hostmap
        # forces a new cryptographic handshake with the reloaded process.
        self.guest(peer, 'systemctl', 'restart', 'lighthouse' if peer == 'lh' else 'nebula')
        self.check(source + ' handshake peer starts a new process', int(pid(peer)) > 0 and pid(peer) != peer_pid)
        self.guest(source, 'ping', '-c', '10', '-W', '5', address)
        self.check(source + ' fresh handshake keeps reloaded PID', int(source_pid) > 0 and pid(source) == source_pid)

    def safe_apply_upgrade(self):
        """Exercise the selected PR's upgrade path without requiring its sibling."""
        in_place = self.args.pki_mode == 'in-place'
        tested = self.args.role_path
        try:
            self.args.role_path = self.args.baseline_role_path
            if in_place:
                self.ca_deploy(separate=True)
                self.ca_deploy(separate=True, ca_node=True, limit='cah')
            else:
                self.deploy()
        finally:
            self.args.role_path = tested
        names = {'lh': 'lh', 'server': 'server', 'client': 'cah' if in_place else 'client'}
        original = {host: self.ca_hashes(host, name + '.crt', name + '.key', 'ca.crt') for host, name in names.items()}
        processes = {host: self.guest(host, 'systemctl', 'show', 'lighthouse' if host == 'lh' else 'nebula',
                                     '-p', 'MainPID', '--value').stdout for host in names}
        position = self.log.stat().st_size
        if in_place:
            self.ca_deploy(separate=True, ca_node=True, limit='cah')
        else:
            self.deploy()
        with self.log.open() as log:
            log.seek(position)
            counts = re.findall(r'changed=(\d+)', log.read())
        self.check('baseline upgrade reports zero changes', len(counts) == (1 if in_place else 3) and all(int(n) == 0 for n in counts))
        self.check('baseline upgrade preserves PKI hashes', all(original[host] == self.ca_hashes(host, name + '.crt', name + '.key', 'ca.crt') for host, name in names.items()))
        self.check('baseline upgrade preserves PIDs', all(processes[host] == self.guest(host, 'systemctl', 'show', 'lighthouse' if host == 'lh' else 'nebula', '-p', 'MainPID', '--value').stdout for host in names))

    def safe_apply(self):
        install = self.args.pki_mode in ('install', 'combined')
        in_place = self.args.pki_mode in ('in-place', 'combined')
        def service(host):
            return 'lighthouse' if host == 'lh' else 'nebula'

        def pid(host):
            self.guest(host, 'systemctl', 'is-active', service(host))
            value = self.guest(host, 'systemctl', 'show', service(host), '-p', 'MainPID', '--value').stdout.strip()
            self.check(host + ' has a nonzero PID', int(value) > 0)
            return value

        def cursor(host):
            out = self.guest(host, 'journalctl', '-u', service(host), '-n', '0', '--show-cursor', '--no-pager').stdout
            return re.search(r'-- cursor: (.+)', out)[1]

        def journal(host, position):
            return self.guest(host, 'journalctl', '-u', service(host), '--after-cursor=' + position,
                              '--no-pager', '-o', 'cat').stdout

        def hashes(host, name):
            return self.ca_hashes(host, name + '.crt', name + '.key', 'ca.crt')

        def backups(host):
            self.check(host + ' has no leftover PKI backups', self.guest(host, 'python3', '-c',
                       'import glob; assert not glob.glob("/tmp/nebula-pki-*")').returncode == 0)

        def activate(host, name, old_pid, position):
            text = journal(host, position)
            printed = self.guest(host, '/opt/nebula/nebula-cert', 'print', '-path', '/opt/nebula/' + name + '.crt').stdout
            fingerprint = re.search(r'(?i)fingerprint[": ]+([a-f0-9]+)', printed)[1]
            self.check(host + ' reload keeps PID', pid(host) == old_pid)
            self.check(host + ' reload reports current fingerprint', 'refreshed from disk' in text and fingerprint in text)
            self.check(host + ' has no PKI load errors', 'Could not load client cert' not in text and 'Failed to load ca from config' not in text)
            backups(host)

        def run(inventory, playbook='reconcile.yml', limit=None, extra=(), check=True):
            command = ['ansible-playbook', '-i', str(inventory), str(HERE / playbook)]
            if limit:
                command += ['--limit', limit]
            start = self.log.stat().st_size if self.log.exists() else 0
            status = self.command(command + list(extra), check=check, timeout=1200)
            with self.log.open() as log:
                log.seek(start)
                output = log.read()
            return status, output

        def unchanged(host, name, inventory, playbook, limit):
            old_pid, before, position = pid(host), hashes(host, name), cursor(host)
            for flags in ((), ('--check', '--diff')):
                _, output = run(inventory, playbook, limit, flags)
                counts = re.findall(r'changed=(\d+)', output)
                self.check(host + ' no-op/check reports zero changes', bool(counts) and all(int(n) == 0 for n in counts))
                self.check(host + ' no-op/check preserves files and PID', before == hashes(host, name) and old_pid == pid(host))
                self.check(host + ' no-op/check sends no HUP', 'Caught HUP' not in journal(host, position))
                backups(host)

        # A single TCP connection, with no reconnection path, carries messages
        # throughout the Ansible run. A crash or reconnect cannot pass this test.
        echo = '''import socket
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", 18081))
s.listen()
while True:
    c, _ = s.accept()
    try:
        while True:
            data = c.recv(64)
            if not data: break
            c.sendall(data)
    finally:
        c.close()
'''
        stream = '''import socket, sys, time
from pathlib import Path
s = socket.create_connection((sys.argv[1], 18081), 10)
s.settimeout(5)
reader = s.makefile('rb')
i = 0
while True:
    s.sendall(b"alive")
    assert reader.read(5) == b"alive"
    i += 1
    Path("/run/pki-stream-count").write_text(str(i))
    time.sleep(.2)
'''

        def start_stream(source, target, address):
            self.guest(target, 'systemd-run', '--unit=pki-echo', '--collect', 'python3', '-c', echo)
            time.sleep(1)
            self.guest(source, 'systemd-run', '--unit=pki-stream', '--collect', 'python3', '-c', stream, address)
            time.sleep(2)
            return int(self.guest(source, 'cat', '/run/pki-stream-count').stdout)

        def stream_survives(source, count):
            self.guest(source, 'systemctl', 'is-active', 'pki-stream')
            self.check('established TCP connection survives', int(self.guest(source, 'cat', '/run/pki-stream-count').stdout) > count)
            self.guest(source, 'systemctl', 'stop', 'pki-stream')

        if install:
            # Initial deploy was on fresh VMs; verify there was no first-install HUP.
            for host in self.names:
                self.check(host + ' first install sends no HUP', 'Caught HUP' not in
                           self.guest(host, 'journalctl', '-u', service(host), '--no-pager', '-o', 'cat').stdout)
            before, old_pid, position = hashes('server', 'server'), pid('server'), cursor('server')
            count = start_stream('client', 'server', '10.193.0.2')
            self.groups['server'].append('safe-apply')
            self.deploy('server')
            self.check('group re-sign changes node cert/key', before[:2] != hashes('server', 'server')[:2])
            activate('server', 'server', old_pid, position)
            stream_survives('client', count)
            self.fresh_handshake('server', 'lh', '10.193.0.1')
            old_pid, position, before = pid('server'), cursor('server'), hashes('server', 'server')
            self.guest('lh', 'python3', '-c', 'from pathlib import Path; p=Path("/opt/nebula/ca.crt"); p.write_bytes(p.read_bytes()+b"\\n")')
            run(self.inventory(), 'safe-apply.yml', 'server', ['-e', json.dumps({'certificate_name': 'server', 'ca_only': True})])
            self.check('single CA-file caller changes only CA hash', before[:2] == hashes('server', 'server')[:2] and before[2] != hashes('server', 'server')[2])
            activate('server', 'server', old_pid, position)
            unchanged('server', 'server', self.inventory(), 'reconcile.yml', 'server')
            self.inbound['server'] = [{'port': 'any', 'proto': 'icmp', 'host': 'any'}]
            old_pid = pid('server')
            self.deploy('server')
            if (self.args.role_path / 'tasks/apply_config.yml').is_file():
                self.check('reloadable firewall config preserves process', old_pid == pid('server'))
            else:
                self.check('config change still restarts', old_pid != pid('server'))
            self.inbound['server'] = ANY
            self.groups['server'].append('combined-change')
            old_pid = pid('server')
            self.deploy('server')
            if (self.args.role_path / 'tasks/apply_config.yml').is_file():
                self.check('compatible combined config and PKI changes preserve process', old_pid == pid('server'))
            else:
                self.check('combined config and PKI change succeeds and restarts', old_pid != pid('server'))

            # Fresh separate-CA topology also exercises lighthouse installation.
            self.cleanup()
            self.provision()
        self.ca_deploy(separate=True)
        if in_place:
            self.ca_deploy(separate=True, ca_node=True, limit='cah')
        inventory = self.ca_inventory(separate=True, ca_node=in_place)

        def install_validation():
            for host, name, wrong in [('server', 'server', 'lh.key'), ('lh', 'lh', 'server.key')]:
                before, old_pid, position = hashes(host, name), pid(host), cursor(host)
                status, output = run(inventory, 'safe-apply.yml', name,
                                ['-e', json.dumps({'certificate_name': name, 'wrong_key': wrong})], check=False)
                self.check(host + ' invalid PKI fails play', status != 0)
                self.check(host + ' key mismatch identifies validation failure',
                           re.search(r"failed at 'Validate complete PKI set': [^\n]*pair", output) is not None)
                self.check(host + ' rollback restores all hashes', before == hashes(host, name))
                activate(host, name, old_pid, position)
                self.guest(host, '/opt/nebula/nebula', '-test', '-config', '/opt/nebula/config.yml')

            # A matching cert/key from another CA passes -test, but is untrusted.
            # Keep the real CA bundle in both the probe config and installed set.
            rogue = self.guest('client', 'python3', '-c',
                               'import tempfile; print(tempfile.mkdtemp(prefix="wrong-ca-", dir="/opt/nebula"))').stdout.strip()
            try:
                self.guest('client', '/opt/nebula/nebula-cert', 'ca', '-name', 'untrusted-test-ca', '-duration', '87600h',
                           '-out-crt', rogue + '/ca.crt', '-out-key', rogue + '/ca.key')
                for host, name, address in [('server', 'server', '10.193.0.2'), ('lh', 'lh', '10.193.0.1')]:
                    self.guest('client', '/opt/nebula/nebula-cert', 'sign', '-ca-crt', rogue + '/ca.crt',
                               '-ca-key', rogue + '/ca.key', '-name', name, '-ip', address + '/24',
                               '-out-crt', rogue + '/' + name + '.crt', '-out-key', rogue + '/' + name + '.key')
                    self.guest('client', '/opt/nebula/nebula-cert', 'verify', '-ca', rogue + '/ca.crt',
                               '-crt', rogue + '/' + name + '.crt')
                    rejection = self.guest('client', '/opt/nebula/nebula-cert', 'verify', '-ca', '/opt/nebula/ca.crt',
                                           '-crt', rogue + '/' + name + '.crt', check=False)
                    self.check(host + ' wrong-CA fixture is rejected by real trust', rejection.returncode != 0)
                    cause = (rejection.stderr or rejection.stdout).strip().splitlines()[-1]
                    # Run -test on the actual target with its original CA and the
                    # rogue pair, independently proving this is not a key mismatch.
                    before, old_pid, position = hashes(host, name), pid(host), cursor(host)
                    status, output = run(inventory, 'safe-apply.yml', name,
                                         ['-e', json.dumps({'certificate_name': name, 'test_leaf_dir': rogue,
                                                           'probe_wrong_ca': True})], check=False)
                    self.check(host + ' wrong-CA certificate fails play', status != 0)
                    diagnostic = re.search(r"failed at 'Verify node certificate against the CA bundle': ([^\n]+)", output)
                    self.check(host + ' wrong-CA failure shows trust verification cause',
                               diagnostic is not None and cause[:200] in diagnostic[1])
                    self.check(host + ' wrong-CA rollback restores all hashes', before == hashes(host, name))
                    activate(host, name, old_pid, position)

            finally:
                self.guest('client', 'rm', '-rf', '--', rogue)

            count = start_stream('server', 'lh', '10.193.0.1')
            old_pid, position = pid('lh'), cursor('lh')
            self.guest('client', 'rm', '/opt/nebula/lh.crt', '/opt/nebula/lh.key')
            self.ca_deploy(separate=True, ca_node=in_place, limit='lh',
                           extra=['-e', json.dumps({'nebula_cert_groups': ['lighthouse-updated']})]
                           if (self.args.role_path / 'tasks/node_pki.yml').is_file() else ())
            activate('lh', 'lh', old_pid, position)
            stream_survives('server', count)
            self.fresh_handshake('lh', 'server', '10.193.0.2')
            if (self.args.role_path / 'tasks/node_pki.yml').is_file():
                desired = json.loads(inventory.read_text())
                desired['all']['children']['nebula_lighthouse']['hosts']['lh']['nebula_cert_groups'] = ['lighthouse-updated']
                inventory.write_text(json.dumps(desired))
            unchanged('lh', 'lh', inventory, 'ca-deploy.yml', 'lh')

        if install:
            install_validation()
        if not in_place:
            return

        # CA-node: signing happens directly in its running PKI directory.
        old_pid, position, before = pid('client'), cursor('client'), hashes('client', 'cah')
        count = start_stream('server', 'client', '10.193.0.3')
        changed_groups = ['-e', json.dumps({'nebula_cert_groups': ['ca-node-safe']})]
        self.ca_deploy(separate=True, ca_node=True, limit='cah', extra=changed_groups)
        self.check('CA-node live files changed', before[:2] != hashes('client', 'cah')[:2])
        activate('client', 'cah', old_pid, position)
        stream_survives('server', count)
        self.fresh_handshake('client', 'lh', '10.193.0.1')
        inventory = self.ca_inventory(separate=True, ca_node=True)
        # Preserve the changed desired groups for no-op and failure tests.
        data = json.loads(inventory.read_text())
        data['all']['vars']['nebula_cert_groups'] = ['ca-node-safe']
        inventory.write_text(json.dumps(data))
        unchanged('client', 'cah', inventory, 'ca-deploy.yml', 'cah')
        before, old_pid, position = hashes('client', 'cah'), pid('client'), cursor('client')
        rogue = self.guest('client', 'python3', '-c',
                           'import tempfile; print(tempfile.mkdtemp(prefix="wrong-ca-", dir="/opt/nebula"))').stdout.strip()
        self.guest('client', 'mv', '/opt/nebula/nebula-cert', '/opt/nebula/nebula-cert.real')
        try:
            self.guest('client', '/opt/nebula/nebula-cert.real', 'ca', '-name', 'untrusted-test-ca', '-duration', '87600h',
                       '-out-crt', rogue + '/ca.crt', '-out-key', rogue + '/ca.key')
            wrapper = '#!/bin/sh\n/opt/nebula/nebula-cert.real "$@" || exit $?\nif [ "$1" = sign ]; then cp /opt/nebula/server.key /opt/nebula/cah.key; fi\n'
            self.guest('client', 'python3', '-c', 'from pathlib import Path; import sys; p=Path("/opt/nebula/nebula-cert"); p.write_text(sys.argv[1]); p.chmod(0o755)', wrapper)
            status, _ = run(inventory, 'ca-deploy.yml', 'cah',
                            ['-e', json.dumps({'nebula_cert_groups': ['invalid-new']})], check=False)
            self.check('CA-node invalid in-place signing fails', status != 0)
            self.check('CA-node rollback restores hashes', before == hashes('client', 'cah'))
            activate('client', 'cah', old_pid, position)
            wrapper = ('#!/usr/bin/python3\nimport os,sys\na=sys.argv[1:]\n'
                       'if a and a[0] == "sign":\n'
                       '    for flag,value in [("-ca-crt",' + repr(rogue + '/ca.crt') + '),("-ca-key",' + repr(rogue + '/ca.key') + ')]:\n'
                       '        if flag in a: a[a.index(flag)+1]=value\n'
                       '        else: a[1:1]=[flag,value]\n'
                       'os.execv("/opt/nebula/nebula-cert.real",["/opt/nebula/nebula-cert.real"]+a)\n')
            self.guest('client', 'python3', '-c', 'from pathlib import Path; import sys; Path("/opt/nebula/nebula-cert").write_text(sys.argv[1])', wrapper)
            position = cursor('client')
            status, output = run(inventory, 'ca-deploy.yml', 'cah',
                                 ['-e', json.dumps({'nebula_cert_groups': ['wrong-ca']})], check=False)
            self.check('CA-node wrong-CA signing fails at trust verification', status != 0 and
                       re.search(r"failed at '(Verify node certificate against the CA bundle|Verify staged leaf against intended CA)'", output) is not None)
            self.check('CA-node wrong-CA rollback restores hashes', before == hashes('client', 'cah'))
            activate('client', 'cah', old_pid, position)
            # Failure before a complete keypair exists exercises outer rescue.
            wrapper = '#!/bin/sh\nif [ "$1" = sign ]; then exit 42; fi\nexec /opt/nebula/nebula-cert.real "$@"\n'
            self.guest('client', 'python3', '-c', 'from pathlib import Path; import sys; Path("/opt/nebula/nebula-cert").write_text(sys.argv[1])', wrapper)
            status, _ = run(inventory, 'ca-deploy.yml', 'cah',
                            ['-e', json.dumps({'nebula_cert_groups': ['sign-fails']})], check=False)
            self.check('CA-node interrupted signing fails and restores', status != 0 and before == hashes('client', 'cah') and old_pid == pid('client'))
            backups('client')
        finally:
            self.guest('client', 'mv', '/opt/nebula/nebula-cert.real', '/opt/nebula/nebula-cert')
            self.guest('client', 'rm', '-rf', '--', rogue)

    def safe_apply_ip_change(self):
        install = self.args.pki_mode in ('install', 'combined')
        in_place = self.args.pki_mode in ('in-place', 'combined')
        def service(host):
            return 'lighthouse' if host == 'lh' else 'nebula'

        def pid(host):
            self.guest(host, 'systemctl', 'is-active', service(host))
            value = self.guest(host, 'systemctl', 'show', service(host), '-p', 'MainPID', '--value').stdout.strip()
            assert int(value) > 0
            return value

        def hashes(host, name):
            return self.ca_hashes(host, name + '.crt', name + '.key', 'ca.crt')

        def addresses(host):
            result = self.guest(host, 'ip', '-json', 'address', 'show', 'dev', 'neb0')
            return sorted(a['local'] + '/' + str(a['prefixlen'])
                          for link in json.loads(result.stdout) for a in link['addr_info'] if a['family'] == 'inet')

        def run(inventory, limit, playbook='reconcile.yml', extra=()):
            start = self.log.stat().st_size
            status = self.command(['ansible-playbook', '-i', str(inventory), str(HERE / playbook),
                                   '--limit', limit, *extra], check=False, timeout=1200)
            with self.log.open() as log:
                log.seek(start)
                return status, log.read()

        def no_backups(host):
            self.check(host + ' IP update leaves no backups', self.guest(host, 'python3', '-c',
                       'import glob; assert not glob.glob("/tmp/nebula-pki-*")').returncode == 0)

        def activated(host, name, network, old_pid):
            # Collect actual certificate/interface/journal evidence before assertions.
            actual = addresses(host)
            printed = json.loads(self.guest(host, '/opt/nebula/nebula-cert', 'print', '-json',
                                           '-path', '/opt/nebula/' + name + '.crt').stdout)
            details = (printed[0] if isinstance(printed, list) else printed)['details']
            self.guest(host, 'journalctl', '-u', service(host), '-n', '80', '--no-pager', '-o', 'cat')
            self.check(host + ' IP update has a new PID', old_pid != pid(host))
            self.check(host + ' IP update has exactly the desired tunnel network', actual == [network])
            self.check(host + ' installed certificate has the desired network',
                       (details.get('networks') or details.get('ips')) == [network])
            no_backups(host)

        def fresh_peer(host, peer, address):
            # Restart only the peer after checking the target's activation PID.
            old_pid = pid(host)
            self.guest(peer, 'systemctl', 'restart', service(peer))
            if peer == 'lh':
                # A fresh lighthouse has no registration for the renumbered node.
                # Initiate via the node's static lighthouse endpoint first, rather
                # than waiting for its 60-second registration interval.
                self.guest(host, 'ping', '-c', '3', '-W', '5', '10.193.0.1')
            self.check(host + ' new address permits a fresh peer handshake',
                       self.guest(peer, 'ping', '-c', '5', '-W', '5', address, check=False).returncode == 0)
            self.check(host + ' fresh handshake retains the activated PID', old_pid == pid(host))

        def fail_next_start(host, operation):
            # Only a test wrapper fails one real startup, not -test or -version.
            binary = '/opt/nebula/nebula'
            self.guest(host, 'mv', binary, binary + '.test-real')
            wrapper = ('#!/bin/sh\nif [ "$1" = "-config" ] && [ -f /run/nebula-fail-next-start ]; then\n'
                       '  rm /run/nebula-fail-next-start\n  exit 42\nfi\n'
                       'exec /opt/nebula/nebula.test-real "$@"\n')
            try:
                self.guest(host, 'python3', '-c',
                           'from pathlib import Path; import sys; p=Path(sys.argv[1]); '
                           'p.write_text(sys.argv[2]); p.chmod(0o700); Path("/run/nebula-fail-next-start").touch()',
                           binary, wrapper)
                status, output = operation()
                self.check(host + ' injected restart fails the play', status != 0)
                self.check(host + ' failure identifies activation', 'Activate PKI using the selected operation' in output)
                self.check(host + ' restart injection was exercised',
                           self.guest(host, 'test', '!', '-e', '/run/nebula-fail-next-start', check=False).returncode == 0)
            finally:
                self.guest(host, 'mv', binary + '.test-real', binary)
                self.guest(host, 'rm', '-f', '/run/nebula-fail-next-start')

        def node_cases():
            # Ordinary node: address, prefix, no-op, check mode and reload-only groups.
            old_pid = pid('server')
            self.node_ips['server'] = '10.193.0.4'
            status, _ = run(self.inventory(), 'server')
            self.check('node IP renumbering succeeds', status == 0)
            activated('server', 'server', '10.193.0.4/24', old_pid)
            fresh_peer('server', 'lh', '10.193.0.4')
            for prefix in (25, 24):
                inventory = self.inventory()
                data = json.loads(inventory.read_text())
                data['all']['vars']['nebula_network_cidr'] = prefix
                inventory.write_text(json.dumps(data))
                old_pid = pid('server')
                status, _ = run(inventory, 'server')
                self.check('prefix-only update succeeds ' + str(prefix), status == 0)
                activated('server', 'server', '10.193.0.4/' + str(prefix), old_pid)
                fresh_peer('server', 'lh', '10.193.0.4')
            before, old_pid = hashes('server', 'server'), pid('server')
            status, output = run(self.inventory(), 'server')
            counts = re.findall(r'changed=(\d+)', output)
            self.check('renumbered node repeat reports zero changes', status == 0 and bool(counts) and all(int(n) == 0 for n in counts))
            self.check('renumbered node repeat preserves hashes and PID', before == hashes('server', 'server') and old_pid == pid('server'))
            self.node_ips['server'] = '10.193.0.5'
            status, _ = run(self.inventory(), 'server', extra=['--check', '--diff'])
            self.check('proposed IP check mode succeeds without mutation', status == 0 and
                       before == hashes('server', 'server') and old_pid == pid('server') and addresses('server') == ['10.193.0.4/24'])
            self.node_ips['server'] = '10.193.0.4'
            self.groups['server'].append('after-renumbering')
            status, _ = run(self.inventory(), 'server')
            self.check('group-only change after renumbering reloads without restarting', status == 0 and old_pid == pid('server'))
            before, old_pid = hashes('server', 'server'), pid('server')
            self.node_ips['server'] = '10.193.0.5'
            fail_next_start('server', lambda: run(self.inventory(), 'server'))
            self.check('node restart rollback restores original hashes', before == hashes('server', 'server'))
            activated('server', 'server', '10.193.0.4/24', old_pid)
            fresh_peer('server', 'lh', '10.193.0.4')

            # Reproduce the old silent failure: desired files already installed, old
            # address still active. A rerun must repair runtime drift without re-signing.
            status, _ = run(self.inventory(), 'server', 'safe-apply.yml',
                            ['-e', json.dumps({'certificate_name': 'server', 'stage_only': True})])
            self.check('legacy drift fixture stages new files without activation', status == 0 and addresses('server') == ['10.193.0.4/24'])
            before, old_pid = hashes('server', 'server'), pid('server')
            status, _ = run(self.inventory(), 'server')
            self.check('legacy rejected-reload runtime drift is repaired without re-signing', status == 0 and before == hashes('server', 'server'))
            activated('server', 'server', '10.193.0.5/24', old_pid)
            fresh_peer('server', 'lh', '10.193.0.5')

        if install:
            node_cases()

            # In-place CA-node and separate-CA lighthouse paths share the classifier.
            self.cleanup()
            self.provision()
        self.ca_deploy(separate=True)
        if in_place:
            self.ca_deploy(separate=True, ca_node=True, limit='cah')

        def run_ca_node(address):
            inventory = self.ca_inventory(separate=True, ca_node=True)
            data = json.loads(inventory.read_text())
            data['all']['children']['ca_hosts']['hosts']['cah']['nebula_internal_ip_addr'] = address
            inventory.write_text(json.dumps(data))
            return run(inventory, 'cah', 'ca-deploy.yml')

        if in_place:
            old_pid = pid('client')
            status, _ = run_ca_node('10.193.0.4')
            self.check('CA-node in-place renumbering succeeds', status == 0)
            activated('client', 'cah', '10.193.0.4/24', old_pid)
            fresh_peer('client', 'lh', '10.193.0.4')
            before, old_pid = hashes('client', 'cah'), pid('client')
            fail_next_start('client', lambda: run_ca_node('10.193.0.5'))
            self.check('CA-node restart rollback restores its pre-signing hashes', before == hashes('client', 'cah'))
            activated('client', 'cah', '10.193.0.4/24', old_pid)
            fresh_peer('client', 'lh', '10.193.0.4')
        if not install:
            return

        fixture = self.guest('client', 'python3', '-c',
                             'import tempfile; print(tempfile.mkdtemp(prefix="ip-change-", dir="/opt/nebula"))').stdout.strip()
        try:
            for address, should_fail in [('10.193.0.6', False), ('10.193.0.7', True)]:
                for suffix in ('.crt', '.key'):
                    self.guest('client', 'rm', '-f', fixture + '/lh' + suffix)
                self.guest('client', '/opt/nebula/nebula-cert', 'sign', '-name', 'lh', '-ip', address + '/24',
                           '-ca-crt', '/opt/nebula/ca.crt', '-ca-key', '/opt/nebula/ca.key',
                           '-out-crt', fixture + '/lh.crt', '-out-key', fixture + '/lh.key')
                inventory = self.ca_inventory(separate=True, ca_node=in_place)
                extra = ['-e', json.dumps({'certificate_name': 'lh', 'test_leaf_dir': fixture})]
                before, old_pid = hashes('lh', 'lh'), pid('lh')
                if should_fail:
                    fail_next_start('lh', lambda: run(inventory, 'lh', 'safe-apply.yml', extra))
                    self.check('lighthouse restart rollback restores original hashes', before == hashes('lh', 'lh'))
                else:
                    status, _ = run(inventory, 'lh', 'safe-apply.yml', extra)
                    self.check('lighthouse network update succeeds', status == 0)
                    self.guest('server', 'python3', '-c',
                               'from pathlib import Path; p=Path("/opt/nebula/config.yml"); '
                               'p.write_text(p.read_text().replace("10.193.0.1", "10.193.0.6"))')
                activated('lh', 'lh', '10.193.0.6/24', old_pid)
                fresh_peer('lh', 'server', '10.193.0.6')
        finally:
            self.guest('client', 'rm', '-rf', '--', fixture)

    def config(self, host):
        return yaml.safe_load(self.guest(host, 'cat', '/opt/nebula/config.yml').stdout)

    def start_servers(self):
        for host in ('lh', 'server'):
            self.guest(host, 'systemd-run', '--unit=nebula-test-http', '--collect',
                       '/usr/bin/python3', '-m', 'http.server', str(PORT), '--bind',
                       '10.193.0.1' if host == 'lh' else '10.193.0.2')
            self.guest(host, 'systemctl', 'is-active', 'nebula-test-http.service')

    def probe(self, target, source='client'):
        ip = '10.193.0.1' if target == 'lh' else '10.193.0.2'
        script = ('import socket,sys; s=socket.create_connection((sys.argv[1],int(sys.argv[2])),3); '
                  's.sendall(b"GET / HTTP/1.0\\r\\n\\r\\n"); '
                  'assert s.recv(64).startswith(b"HTTP/")')
        return self.guest(source, 'python3', '-c', script, ip, PORT, check=False).returncode == 0

    def restart(self):
        for host in ('lh', 'server', 'client'):
            self.guest(host, 'systemctl', 'restart', 'lighthouse' if host == 'lh' else 'nebula')

    def firewall(self):
        for host in ('lh', 'server', 'client'):
            self.guest(host, '/opt/nebula/nebula', '-test', '-config', '/opt/nebula/config.yml')
        self.start_servers()
        for target in ('server', 'lh'):
            self.check(target + ' allow-all control', self.probe(target))
            self.inbound[target] = [{'port': PORT, 'proto': 'tcp', 'group': 'ssh-clients'}]
            self.deploy(target)
            rule = self.config(target)['firewall']['inbound'][0]
            self.check(target + ' rendered group without host', rule.get('group') == 'ssh-clients' and 'host' not in rule)
            self.guest(target, '/opt/nebula/nebula', '-test', '-config', '/opt/nebula/config.yml')
            for groups, expected in [([], False), (['other'], False), (['ssh-clients'], True), (['ssh-clients', 'extra'], True)]:
                self.groups['client'] = groups
                self.deploy('client')
                self.restart()
                self.check(target + ' single group ' + str(groups), self.probe(target) == expected)
            self.inbound[target] = [{'port': PORT, 'proto': 'tcp', 'groups': ['ssh-clients', 'admins']}]
            self.deploy(target)
            rule = self.config(target)['firewall']['inbound'][0]
            self.check(target + ' rendered AND groups', rule.get('groups') == ['ssh-clients', 'admins'] and 'host' not in rule)
            self.guest(target, '/opt/nebula/nebula', '-test', '-config', '/opt/nebula/config.yml')
            for groups, expected in [(['ssh-clients'], False), (['admins'], False), (['ssh-clients', 'admins'], True), (['admins', 'ssh-clients'], True)]:
                self.groups['client'] = groups
                self.deploy('client')
                self.restart()
                self.check(target + ' AND groups ' + str(groups), self.probe(target) == expected)
            self.inbound[target][0]['groups'].reverse()
            self.deploy(target)
            self.restart()
            self.check(target + ' reordered rule groups allowed', self.probe(target))
            self.inbound[target] = [{'port': PORT, 'proto': 'tcp', 'group': 'ssh-clients'},
                                    {'port': PORT, 'proto': 'tcp', 'group': 'admins'}]
            self.deploy(target)
            for groups in (['ssh-clients'], ['admins']):
                self.groups['client'] = groups
                self.deploy('client')
                self.restart()
                self.check(target + ' separate OR rules ' + str(groups), self.probe(target))
            self.inbound[target] = [{'port': PORT, 'proto': 'tcp', 'host': 'client'}]
            self.deploy(target)
            self.restart()
            self.check(target + ' matching host rule', self.probe(target))
            self.inbound[target] = [{'port': PORT, 'proto': 'tcp', 'host': 'not-client'}]
            self.deploy(target)
            self.restart()
            self.check(target + ' nonmatching host rule', not self.probe(target))
            self.inbound[target] = ANY
            self.deploy(target)
        self.outbound['client'] = [{'port': PORT, 'proto': 'tcp', 'group': 'service'}]
        self.deploy('client')
        self.restart()
        rule = self.config('client')['firewall']['outbound'][0]
        self.check('outbound group rendered', rule.get('group') == 'service' and 'host' not in rule)
        self.check('outbound matching destination allowed', self.probe('server'))
        self.check('outbound nonmatching destination denied', not self.probe('lh'))
        self.check('lighthouse outbound allow-all control', self.probe('server', 'lh'))
        self.outbound['lh'] = [{'port': PORT, 'proto': 'tcp', 'groups': ['service', 'other']}]
        self.deploy('lh')
        self.restart()
        rule = self.config('lh')['firewall']['outbound'][0]
        self.check('lighthouse outbound AND groups rendered', rule.get('groups') == ['service', 'other'] and 'host' not in rule)
        self.check('lighthouse outbound missing group denied', not self.probe('server', 'lh'))
        self.groups['server'] = ['service', 'other']
        self.deploy('server')
        self.restart()
        self.check('lighthouse outbound both groups allowed', self.probe('server', 'lh'))

    def cleanup(self):
        existing = {item['name'] for item in json.loads(self.output(self.lxc('list', self.args.remote + ':', '--format', 'json')).stdout)}
        for name in reversed(self.created):
            if name not in existing:
                continue
            # Check ownership before deletion, including in partially completed runs.
            result = self.output(self.lxc('config', 'get', self.args.remote + ':' + name, 'user.nebula-test-run'))
            if result.stdout.strip() != self.run_id:
                raise RuntimeError(f'Refusing to delete {name}: ownership marker does not match')
            self.command(self.lxc('delete', self.args.remote + ':' + name, '--force'))
            self.created.remove(name)
            self.manifest()
        self.created = []
        self.manifest()


def cleanup_saved(path):
    manifest = json.loads((path / 'manifest.json').read_text())
    run_id = manifest['run_id']
    if not re.fullmatch(r'nebtest-[0-9a-f]{10}', run_id):
        raise ValueError('Invalid run identifier')
    existing = {item['name'] for item in json.loads(subprocess.check_output(
        ['lxc', 'list', manifest['remote'] + ':', '--project', manifest['project'], '--format', 'json'], text=True))}
    for name in reversed(manifest['instances']):
        if not re.fullmatch(re.escape(run_id) + r'-(lh|server|client)', name):
            raise ValueError('Invalid instance name in manifest')
        if name not in existing:
            continue
        base = ['lxc', 'config', 'get', manifest['remote'] + ':' + name, 'user.nebula-test-run', '--project', manifest['project']]
        result = subprocess.run(base, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f'Cannot verify ownership of {name}: {result.stderr}')
        if result.stdout.strip() != run_id:
            raise RuntimeError(f'Refusing to delete {name}: ownership marker does not match')
        subprocess.run(['lxc', 'delete', manifest['remote'] + ':' + name, '--project', manifest['project'], '--force'], check=True)
    manifest['instances'] = []
    (path / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remote', default=os.getenv('LXD_REMOTE'))
    parser.add_argument('--project', default=os.getenv('LXD_PROJECT'))
    parser.add_argument('--image', default='ubuntu:24.04')
    parser.add_argument('--profile', default='default')
    parser.add_argument('--memory', default='1GiB')
    parser.add_argument('--cpus', type=int, default=2)
    parser.add_argument('--nebula-version', action='append', dest='versions')
    parser.add_argument('--scenario', choices=['auto', 'all', 'certificates', 'certificate-format-upgrade', 'firewall', 'ca-host', 'role-feature', 'safe-pki', *PKI_SCENARIOS], default='auto')
    parser.add_argument('--feature-case', choices=['debug-keys', 'subject', 'signer', 'legacy-bundle', 'legacy-expired-bundle', 'staged', 'lighthouse', 'issuer', 'confirmation', 'config', 'trust-only', 'rotation-ssh', 'ssh-recovery'])
    parser.add_argument('--pki-mode', choices=['install', 'in-place', 'combined'],
                        help='safe-PKI coverage: PR A, PR B, or both (default: combined)')
    parser.add_argument('--list-tests', action='store_true', help='print resolved runs without provisioning or requiring LXD access')
    parser.add_argument('--role-path', type=Path, default=ROOT, help='role checkout to test (default: repository containing the suite)')
    parser.add_argument('--baseline-role-path', type=Path, help='optional baseline checkout for CA-host or safe-PKI upgrade tests')
    parser.add_argument('--artifacts', default=str(Path(tempfile.gettempdir()) / 'nebula-lxd-tests'))
    parser.add_argument('--keep-on-failure', action='store_true')
    parser.add_argument('--cleanup', type=Path, metavar='RUN_DIRECTORY')
    args = parser.parse_args()
    if args.cleanup:
        cleanup_saved(args.cleanup)
        return
    if args.pki_mode is not None and args.scenario not in ('safe-pki', 'all', *PKI_SCENARIOS):
        parser.error('--pki-mode requires --scenario safe-pki, safe-apply, safe-apply-ip-change, or all')
    args.pki_mode = args.pki_mode or 'combined'
    if (args.scenario == 'role-feature') != (args.feature_case is not None):
        parser.error('--scenario role-feature and --feature-case must be supplied together')
    if not args.list_tests and (not args.remote or not args.project):
        parser.error('--remote and --project are required (or set LXD_REMOTE and LXD_PROJECT)')
    args.role_path = args.role_path.expanduser().resolve()
    if args.baseline_role_path:
        args.baseline_role_path = args.baseline_role_path.expanduser().resolve()
        if not (args.baseline_role_path / 'tasks/main.yml').is_file():
            parser.error('--baseline-role-path must be a Nebula role checkout')
    try:
        defaults = yaml.safe_load((args.role_path / 'defaults/main.yml').read_text())
        templates = [(args.role_path / 'templates' / name).read_text() for name in ('node_config.yml.j2', 'lighthouse_config.yml.j2')]
    except OSError as exc:
        parser.error(f'--role-path must be a Nebula role checkout: {exc}')
    supported = []
    supported.append('role-feature')
    if 'nebula_cert_groups' in defaults:
        supported.append('certificates')
        supported.append('certificate-format-upgrade')
        if all('rule.group' in t and 'rule.groups' in t for t in templates):
            supported.append('firewall')
    if 'nebula_ca_host' in defaults:
        supported.append('ca-host')
    if ('nebula_cert_groups' in defaults and 'nebula_ca_host' in defaults
            and (args.role_path / 'tasks/install_pki.yml').is_file()):
        supported.append('safe-apply')
        supported.append('safe-apply-ip-change')
    scenarios = select_scenarios(args.scenario, supported)
    if not scenarios:
        parser.error('no supported features detected; use --role-path to select a checkout with certificate groups, firewall groups, or nebula_ca_host')
    missing = [s for s in scenarios if s not in supported]
    if missing:
        parser.error(f'role checkout lacks required features for: {", ".join(missing)}; select another --role-path')
    versions = args.versions or ['1.8.0', '1.9.5']
    if args.list_tests:
        print(json.dumps([{'scenario': scenario, 'nebula_version': version,
                           'pki_mode': args.pki_mode if scenario in PKI_SCENARIOS else None,
                           'role_path': str(args.role_path),
                           'baseline_role_path': str(args.baseline_role_path) if args.baseline_role_path else None}
                          for version in versions for scenario in scenarios
                          if scenario != 'certificate-format-upgrade' or version == versions[0]], indent=2))
        return
    for version in versions:
        for scenario in scenarios:
            # This scenario has its own fixed upgrade pair, independent of the matrix.
            if scenario == 'certificate-format-upgrade' and version != versions[0]:
                continue
            args.active_scenario = scenario
            suite = Suite(args, version)
            mode = f', PKI mode {args.pki_mode}' if scenario in PKI_SCENARIOS else ''
            print(f'Running {scenario}, Nebula {version}{mode}; artifacts: {suite.path}', flush=True)
            failed = True
            try:
                suite.provision()
                if scenario == 'role-feature' and args.feature_case in ('rotation-ssh', 'ssh-recovery'):
                    from series_ssh import seed_v1_ca
                    seed_v1_ca(suite)
                if scenario == 'ca-host':
                    suite.ca_host()
                else:
                    if scenario == 'safe-apply' and args.baseline_role_path:
                        suite.safe_apply_upgrade()
                    elif scenario != 'certificate-format-upgrade' and not (scenario in PKI_SCENARIOS and args.pki_mode == 'in-place'):
                        suite.deploy()
                    if scenario == 'certificates':
                        suite.certificates()
                    elif scenario == 'certificate-format-upgrade':
                        suite.certificate_format_upgrade()
                    elif scenario == 'safe-apply':
                        suite.safe_apply()
                    elif scenario == 'safe-apply-ip-change':
                        suite.safe_apply_ip_change()
                    elif scenario == 'role-feature':
                        from series_cases import run_feature
                        run_feature(suite, args.feature_case)
                    else:
                        suite.firewall()
                failed = False
                suite.status = 'passed'
                suite.manifest()
                print(f'PASS {scenario}, Nebula {version}: {suite.passed} assertions', flush=True)
            finally:
                if failed:
                    suite.status = 'failed'
                    suite.manifest()
                if failed and args.keep_on_failure:
                    print(f'Instances retained. Cleanup: python {HERE / "run.py"} --cleanup {suite.path}', file=sys.stderr)
                else:
                    suite.cleanup()


if __name__ == '__main__':
    try:
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(RuntimeError('Interrupted by SIGTERM')))
        main()
    except (RuntimeError, AssertionError, subprocess.SubprocessError, OSError, ValueError) as exc:
        print('FAIL:', exc, file=sys.stderr)
        sys.exit(1)
