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
        self.manifest()

    def manifest(self):
        (self.path / 'manifest.json').write_text(json.dumps({
            'remote': self.args.remote, 'project': self.args.project,
            'run_id': self.run_id, 'instances': self.created, 'version': self.version,
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
        return (details[0] if isinstance(details, list) else details)['details']

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
        self.check('lighthouse group drift does not re-sign', self.hashes('lh') == lh_hashes)
        for groups in (['lighthouse-admins'], ['lighthouse-admins', 'other'], []):
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
    for name in reversed(manifest['instances']):
        if not re.fullmatch(re.escape(run_id) + r'-(lh|server|client)', name):
            raise ValueError('Invalid instance name in manifest')
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
    parser.add_argument('--scenario', choices=['auto', 'all', 'certificates', 'firewall', 'ca-host'], default='auto')
    parser.add_argument('--role-path', type=Path, default=ROOT, help='role checkout to test (default: repository containing the suite)')
    parser.add_argument('--baseline-role-path', type=Path, help='optional upstream checkout for the CA default upgrade test')
    parser.add_argument('--artifacts', default=str(Path(tempfile.gettempdir()) / 'nebula-lxd-tests'))
    parser.add_argument('--keep-on-failure', action='store_true')
    parser.add_argument('--cleanup', type=Path, metavar='RUN_DIRECTORY')
    args = parser.parse_args()
    if args.cleanup:
        cleanup_saved(args.cleanup)
        return
    if not args.remote or not args.project:
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
    if 'nebula_cert_groups' in defaults:
        supported.append('certificates')
        if all('rule.group' in t and 'rule.groups' in t for t in templates):
            supported.append('firewall')
    if 'nebula_ca_host' in defaults:
        supported.append('ca-host')
    if args.scenario == 'auto':
        scenarios = supported
        if not scenarios:
            parser.error('no supported features detected; use --role-path to select a checkout with certificate groups, firewall groups, or nebula_ca_host')
    elif args.scenario == 'all':
        scenarios = ['certificates', 'firewall', 'ca-host']
    else:
        scenarios = [args.scenario]
    missing = [s for s in scenarios if s not in supported]
    if missing:
        parser.error(f'role checkout lacks required features for: {", ".join(missing)}; select another --role-path')
    for version in args.versions or ['1.8.0', '1.9.5']:
        for scenario in scenarios:
            suite = Suite(args, version)
            print(f'Running {scenario}, Nebula {version}; artifacts: {suite.path}', flush=True)
            failed = True
            try:
                suite.provision()
                if scenario == 'ca-host':
                    suite.ca_host()
                else:
                    suite.deploy()
                    if scenario == 'certificates':
                        suite.certificates()
                    else:
                        suite.firewall()
                failed = False
                print(f'PASS {scenario}, Nebula {version}: {suite.passed} assertions', flush=True)
            finally:
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
