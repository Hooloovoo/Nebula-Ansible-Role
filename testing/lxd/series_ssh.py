"""SSH-over-Nebula rotation acceptance, reusing the prototype's SSH monitor."""
import json
import copy
from pathlib import Path
import re
import subprocess
import tarfile
import time
import urllib.request

HERE = Path(__file__).parent
BASE = '/root/nebula-test'


def seed_v1_ca(s):
    archive = s.path / 'release.tar.gz'
    urllib.request.urlretrieve(f'https://github.com/slackhq/nebula/releases/download/v{s.version}/nebula-linux-amd64.tar.gz', archive)
    binary = s.path / 'nebula-cert'
    with tarfile.open(archive) as tar:
        binary.write_bytes(tar.extractfile('nebula-cert').read())
    s.guest('lh', 'mkdir', '-p', '/opt/nebula')
    s.command(s.lxc('file', 'push', str(binary), s.args.remote + ':' + s.names['lh'] + '/opt/nebula/nebula-cert', '--mode', '0700'))
    s.guest('lh', '/opt/nebula/nebula-cert', 'ca', '-name', s.run_id, '-version', '1', '-ips', '10.193.0.0/24', '-duration', '87600h',
            '-out-crt', '/opt/nebula/ca.crt', '-out-key', '/opt/nebula/ca.key')


def push(s, source, destination):
    s.command(s.lxc('file', 'push', str(source), s.args.remote + ':' + s.names['client'] + destination))


def observed_play(s, label, playbook='rotation.yml', limit='all', extra=()):
    s.command(['lxc', 'exec', s.args.remote + ':' + s.names['client'], '--project', s.args.project,
               '--', 'python3', BASE + '/probe.py', playbook, limit, *extra], timeout=1200)
    for name in ('result.json', 'ansible.log'):
        s.command(s.lxc('file', 'pull', s.args.remote + ':' + s.names['client'] + BASE + '/' + name,
                        str(s.path / (label + '-' + name))))
    result = json.loads((s.path / (label + '-result.json')).read_text())
    print(label, 'exit', result['ansible_exit'], 'SSH probe failures', sum(not p['ok'] for p in result['probes']), flush=True)
    return result


def certificate(s, host, filename=None):
    value = json.loads(s.guest(host, '/opt/nebula/nebula-cert', 'print', '-json', '-path', '/opt/nebula/' + (filename or host + '.crt')).stdout)
    return value[0] if isinstance(value, list) else value


def rotation_ssh(s, recovery_only=False):
    s.check('candidate exposes a rotation workflow', (s.args.role_path / 'rotation.yml').is_file())
    s.check('modern binary fixture preserves version-1 leaf issuance', all(certificate(s, h).get('version', 1) == 1 for h in s.names))
    s.command(['lxc', 'exec', s.args.remote + ':' + s.names['client'], '--project', s.args.project, '--', 'apt-get', 'update'], timeout=600)
    s.command(['lxc', 'exec', s.args.remote + ':' + s.names['client'], '--project', s.args.project, '--', 'apt-get', 'install', '-y',
               'ansible', 'python3-jmespath', 'openssh-client'], timeout=900)
    s.guest('client', 'ansible-playbook', '--version')
    s.guest('client', 'mkdir', '-p', BASE)
    s.guest('client', 'ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', BASE + '/id_ed25519')
    public = s.guest('client', 'cat', BASE + '/id_ed25519.pub').stdout.strip()
    for host in ('lh', 'server'):
        s.guest(host, 'python3', '-c', 'from pathlib import Path; import sys; p=Path("/root/.ssh"); p.mkdir(mode=0o700,exist_ok=True); '
                'f=p/"authorized_keys"; f.open("a").write(sys.argv[1]+chr(10)); f.chmod(0o600)', public)
    # Prepare group-controlled SSH and debug endpoints through ordinary role inputs.
    inventory = s.inventory()
    data = json.loads(inventory.read_text())
    hosts = {'lh': data['all']['children']['nebula_lighthouse']['hosts']['lh'],
             **data['all']['children']['nodes']['hosts']}
    for host in ('server', 'client'):
        hosts[host].update(nebula_sshd_enabled=True,
                           nebula_sshd_authorized_users=[{'user': 'debug', 'keys': [public]}])
    hosts['client'].update(nebula_cert_groups=['ssh-clients'], nebula_cert_name='controller.example.test')
    hosts['server'].update(nebula_cert_groups=['service', 'ssh-target'], nebula_cert_name='node.example.test',
                           nebula_inbound_rules=[{'port': 22, 'proto': 'tcp', 'group': 'ssh-clients'},
                                                 {'port': 'any', 'proto': 'icmp', 'host': 'any'}])
    hosts['lh'].update(nebula_cert_groups=['lighthouse-admins'], nebula_cert_name='lh.example.test')
    inventory.write_text(json.dumps(data))
    s.command(['ansible-playbook', '-i', str(inventory), str(HERE / 'deploy.yml')], timeout=1200)
    # Fixture-only CA-host migration: preserve all existing public leaf cache and
    # the signing key without exporting private material into test artifacts.
    keys = subprocess.check_output(['lxc', 'exec', s.args.remote + ':' + s.names['lh'], '--project', s.args.project,
                                    '--', 'tar', '-C', '/opt/nebula', '-czf', '-', 'ca.key', 'lh.crt', 'server.crt'])
    subprocess.run(['lxc', 'exec', s.args.remote + ':' + s.names['client'], '--project', s.args.project,
                    '--', 'tar', '-xzf', '-', '-C', '/opt/nebula'], input=keys, check=True)
    del keys
    s.guest('lh', 'rm', '/opt/nebula/ca.key')
    for source, destination in [('ca.crt', 'active-ca.crt'), ('ca.key', 'active-ca.key'), ('ca.crt', 'desired-ca.crt')]:
        s.guest('client', 'cp', '/opt/nebula/' + source, '/opt/nebula/' + destination)
    s.guest('client', '/opt/nebula/nebula-cert', 'ca', '-name', 'unrelated', '-duration', '87600h',
            '-out-crt', '/opt/nebula/unrelated.crt', '-out-key', '/opt/nebula/unrelated.key')
    s.guest('client', '/opt/nebula/nebula-cert', 'ca', '-name', 'historical', '-version', '1', '-duration', '1s',
            '-out-crt', '/opt/nebula/historical.crt', '-out-key', '/opt/nebula/historical.key')
    time.sleep(3)
    s.guest('client', 'python3', '-c', 'from pathlib import Path; p=Path("/opt/nebula"); '
            '(p/"desired-ca.crt").write_bytes((p/"historical.crt").read_bytes()+(p/"unrelated.crt").read_bytes()+(p/"active-ca.crt").read_bytes())')
    archive = s.path / 'role.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for directory in ('defaults', 'files', 'filter_plugins', 'handlers', 'meta', 'tasks', 'templates', 'vars'):
            source = s.args.role_path / directory
            if source.exists():
                tar.add(source, arcname='nebula-role/' + directory,
                        filter=lambda item: None if '__pycache__' in item.name else item)
    variables = data['all']['vars']
    variables.update(ansible_connection='ssh', ansible_user='root', ansible_ssh_private_key_file=BASE + '/id_ed25519',
                     tested_role=BASE + '/nebula-role', nebula_rotation_role_path=BASE + '/nebula-role',
                     nebula_ca_host='client', nebula_rotation_id='series-test', nebula_rotation_phase='prepare',
                     nebula_ca_signing_cert_path='/opt/nebula/active-ca.crt', nebula_ca_signing_key_path='/opt/nebula/active-ca.key',
                     nebula_ca_bundle_path='/opt/nebula/desired-ca.crt', nebula_apply_timeout=120,
                     nebula_confirmation_command=['python3', BASE + '/confirm-peer.py', '{{ inventory_hostname }}'])
    variables.pop('ansible_lxd_remote', None)
    variables.pop('ansible_lxd_project', None)
    hosts['lh']['ansible_host'] = '10.193.0.1'
    hosts['server']['ansible_host'] = '10.193.0.2'
    hosts['client'].update(ansible_connection='local', ansible_host='localhost')
    data['all']['children']['nebula_rotation'] = {'hosts': {h: {} for h in ('lh', 'server', 'client')}}
    inventory = s.path / 'ssh-inventory.json'

    def save():
        inventory.write_text(json.dumps(data, indent=2))
        push(s, inventory, BASE + '/inventory.json')

    save()
    for source, destination in [(archive, 'role.tar.gz'), (HERE / 'rotation-probe.py', 'probe.py'),
                                (HERE / 'confirm-peer.py', 'confirm-peer.py'), (HERE / 'deploy.yml', 'deploy.yml'),
                                (s.args.role_path / 'rotation.yml', 'rotation.yml')]:
        push(s, source, BASE + '/' + destination)
    s.guest('client', 'tar', '-xzf', BASE + '/role.tar.gz', '-C', BASE)
    if recovery_only:
        return ssh_recovery(s, data, hosts, save)
    before = {h: s.ca_hashes(h, h + '.crt', h + '.key') for h in hosts}
    identities = {h: certificate(s, h) for h in hosts}
    pids = {h: s.guest(h, 'systemctl', 'show', 'lighthouse' if h == 'lh' else 'nebula', '-p', 'MainPID', '--value').stdout for h in hosts}
    s.guest('server', 'cp', '/opt/nebula/server.crt', '/root/original-server.crt')
    variables['nebula_rotation_unmanaged'] = ['phone']

    def rejected(label, phase, message, limit='all', extra=()):
        original = {h: s.ca_hashes(h, h + '.crt', h + '.key', 'ca.crt') for h in hosts}
        signing = s.ca_hashes('client', 'active-ca.crt', 'active-ca.key')
        variables['nebula_rotation_phase'] = phase
        save()
        result = observed_play(s, label, limit=limit, extra=extra)
        log = (s.path / (label + '-ansible.log')).read_text()
        s.check(label + ' refuses unsafe phase progression', result['ansible_exit'] != 0 and message in log)
        s.check(label + ' leaves participant PKI and signer untouched',
                signing == s.ca_hashes('client', 'active-ca.crt', 'active-ca.key') and
                all(original[h] == s.ca_hashes(h, h + '.crt', h + '.key', 'ca.crt') for h in hosts))

    rejected('unprepared-migrate', 'migrate', 'Invalid rotation phase progression')
    for phase in ('prepare', 'migrate', 'finalize'):
        variables['nebula_rotation_phase'] = phase
        save()
        result = observed_play(s, phase)
        s.check(phase + ' completes with Ansible over Nebula SSH', result['ansible_exit'] == 0)
        s.check(phase + ' retains the persistent SSH session', result['persistent_session_responsive_after_play'])
        s.check(phase + ' sampled SSH connections all succeed', result['reconnected'] and bool(result['probes']) and all(p['ok'] for p in result['probes']))
        for host in hosts:
            s.check(phase + ' ' + host + ' retains process and key', pids[host] == s.guest(host, 'systemctl', 'show',
                    'lighthouse' if host == 'lh' else 'nebula', '-p', 'MainPID', '--value').stdout and
                    before[host][1] == s.ca_hashes(host, host + '.key')[0])
        stable = {h: s.ca_hashes(h, h + '.crt', h + '.key', 'ca.crt') for h in hosts}
        repeated = observed_play(s, phase + '-repeat')
        s.check(phase + ' repeat preserves PKI bytes', repeated['ansible_exit'] == 0 and all(stable[h] == s.ca_hashes(h, h + '.crt', h + '.key', 'ca.crt') for h in hosts))
        counts = re.findall(r'changed=(\d+)', (s.path / (phase + '-repeat-ansible.log')).read_text())
        s.check(phase + ' repeat reports zero changes', bool(counts) and all(int(n) == 0 for n in counts))
        if phase == 'prepare':
            rejected('limited-migrate', 'migrate', 'complete Linux participant group', limit='server')
            rejected('check-mode', 'prepare', 'complete Linux participant group', extra=['--check'])
            rejected('premature-finalize', 'finalize', 'Invalid rotation phase progression')
            rejected('unmanaged-trust', 'migrate', 'unmanaged participants')
            variables['nebula_rotation_trust_ready'] = ['phone']
            s.guest('client', 'cp', '/opt/nebula/ca-rotations/series-test/new.key', '/opt/nebula/ca-rotations/series-test/saved-new.key')
            s.guest('client', 'cp', '/opt/nebula/active-ca.key', '/opt/nebula/ca-rotations/series-test/new.key')
            try:
                rejected('mismatched-successor-key', 'migrate', 'Prove successor key matches its certificate')
            finally:
                s.guest('client', 'mv', '/opt/nebula/ca-rotations/series-test/saved-new.key', '/opt/nebula/ca-rotations/series-test/new.key')
            s.guest('client', '/opt/nebula/nebula-cert', 'ca', '-name', 'unexpected-successor', '-version', '1',
                    '-ips', '10.193.0.0/24', '-duration', '87600h', '-out-crt', '/opt/nebula/tamper.crt', '-out-key', '/opt/nebula/tamper.key')
            for name in ('new.crt', 'new.key'):
                s.guest('client', 'cp', '/opt/nebula/ca-rotations/series-test/' + name, '/opt/nebula/ca-rotations/series-test/saved-' + name)
            try:
                s.guest('client', 'cp', '/opt/nebula/tamper.crt', '/opt/nebula/ca-rotations/series-test/new.crt')
                s.guest('client', 'cp', '/opt/nebula/tamper.key', '/opt/nebula/ca-rotations/series-test/new.key')
                rejected('rotation-id-ca-change', 'prepare', 'different CA pair')
            finally:
                for name in ('new.crt', 'new.key'):
                    s.guest('client', 'mv', '/opt/nebula/ca-rotations/series-test/saved-' + name, '/opt/nebula/ca-rotations/series-test/' + name)
            old_ca = s.guest('client', 'cat', '/opt/nebula/ca-rotations/series-test/old.crt').stdout
            s.guest('server', 'cp', '/opt/nebula/ca.crt', '/root/prepared-ca.crt')
            try:
                s.guest('server', 'python3', '-c', 'from pathlib import Path; import sys; Path("/opt/nebula/ca.crt").write_text(sys.argv[1])', old_ca)
                s.guest('server', 'systemctl', 'reload', 'nebula')
                rejected('incomplete-trust', 'migrate', 'confirmed loaded overlap trust')
            finally:
                s.guest('server', 'cp', '/root/prepared-ca.crt', '/opt/nebula/ca.crt')
                s.guest('server', 'systemctl', 'reload', 'nebula')
        if phase == 'migrate':
            command = variables['nebula_confirmation_command']
            variables['nebula_confirmation_command'] = ['/bin/false']
            try:
                rejected('unconfirmed-migration', 'finalize', 'Confirm migrated identities via fresh management handshakes')
            finally:
                variables['nebula_confirmation_command'] = command
            rejected('unmanaged-identity', 'finalize', 'unmanaged participants')
            variables['nebula_rotation_identity_ready'] = ['phone']
    new = certificate(s, 'client', 'active-ca.crt')
    for host in hosts:
        leaf = certificate(s, host)
        s.check(host + ' final leaf uses the new issuer and preserves v1 identity', leaf['details']['issuer'] == new['fingerprint'] and
                leaf.get('version', 1) == 1 and all(leaf['details'].get(k) == identities[host]['details'].get(k)
                                                  for k in ('name', 'networks', 'groups', 'unsafeNetworks')))
    # Independent fresh-handshake rejection after finalization, then recovery.
    s.check('new signing CA preserves v1 format and restrictions', new.get('version', 1) == 1 and
            new['details']['networks'] == ['10.193.0.0/24'])
    old_ca = s.guest('client', 'cat', '/opt/nebula/ca-rotations/series-test/old.crt').stdout
    unrelated = s.guest('client', 'cat', '/opt/nebula/unrelated.crt').stdout
    historical = s.guest('client', 'cat', '/opt/nebula/historical.crt').stdout
    for host in hosts:
        bundle = s.guest(host, 'cat', '/opt/nebula/ca.crt').stdout
        s.check(host + ' final trust retires only the selected old CA', old_ca.strip() not in bundle and unrelated.strip() in bundle and historical.strip() in bundle)
    s.guest('server', 'cp', '/opt/nebula/server.crt', '/root/final-server.crt')
    s.guest('server', 'cp', '/opt/nebula/ca.crt', '/root/final-ca.crt')
    try:
        s.guest('server', 'cp', '/root/original-server.crt', '/opt/nebula/server.crt')
        s.guest('server', 'python3', '-c', 'from pathlib import Path; import sys; p=Path("/opt/nebula/ca.crt"); p.write_text(p.read_text()+sys.argv[1])', old_ca)
        s.guest('server', 'systemctl', 'reload', 'nebula')
        time.sleep(2)
        s.guest('client', 'ssh', '-i', BASE + '/id_ed25519', '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null',
                '-p', '2222', 'debug@127.0.0.1', 'close-tunnel 10.193.0.2')
        rejected = s.guest('client', 'python3', BASE + '/confirm-peer.py', 'server', check=False)
        s.check('retired-CA peer is rejected on a fresh handshake', rejected.returncode != 0)
    finally:
        s.guest('server', 'cp', '/root/final-server.crt', '/opt/nebula/server.crt')
        s.guest('server', 'cp', '/root/final-ca.crt', '/opt/nebula/ca.crt')
        s.guest('server', 'systemctl', 'reload', 'nebula')
    s.guest('client', 'python3', BASE + '/confirm-peer.py', 'server')
    s.check('fresh SSH recovers after restoring the intended peer certificate', True)
    ordinary = observed_play(s, 'ordinary-after', 'deploy.yml', 'all')
    counts = re.findall(r'changed=(\d+)', (s.path / 'ordinary-after-ansible.log').read_text())
    s.check('ordinary role remains idempotent after rotation', ordinary['ansible_exit'] == 0 and bool(counts) and all(int(n) == 0 for n in counts))
    hosts['server']['nebula_cert_groups'].append('after-rotation')
    save()
    ordinary = observed_play(s, 'ordinary-reissue', 'deploy.yml', 'server')
    s.check('ordinary reissue uses the promoted CA key and preserves the host key', ordinary['ansible_exit'] == 0 and
            certificate(s, 'server')['details']['issuer'] == new['fingerprint'] and s.ca_hashes('server', 'server.key')[0] == before['server'][1])


def ssh_recovery(s, data, hosts, save):
    def state():
        value = s.guest('server', 'python3', '-c',
                        'import json; from pathlib import Path; d=json.loads(Path("/run/nebula-role/nebula.json").read_text()); '
                        'print(json.dumps({k:d.get(k) for k in ["status","directory","activated","rollback_trigger"]}))').stdout
        return json.loads(value)

    warm = observed_play(s, 'ssh-baseline', 'deploy.yml')
    s.check('SSH recovery fixture converges through the ordinary role', warm['ansible_exit'] == 0)
    original_host = copy.deepcopy(hosts['server'])
    before = s.ca_hashes('server', 'server.crt', 'server.key', 'ca.crt')
    old_pid = s.guest('server', 'systemctl', 'show', 'nebula', '-p', 'MainPID', '--value').stdout
    previous = state()['directory']
    # The node will trust this CA; its management peer deliberately will not.
    s.guest('client', '/opt/nebula/nebula-cert', 'ca', '-name', 'unprepared-signer', '-version', '1', '-ips', '10.193.0.0/24',
            '-duration', '87600h', '-out-crt', '/opt/nebula/unprepared.crt', '-out-key', '/opt/nebula/unprepared.key')
    s.guest('client', 'python3', '-c', 'from pathlib import Path; p=Path("/opt/nebula"); '
            '(p/"unprepared-bundle.crt").write_bytes((p/"desired-ca.crt").read_bytes()+(p/"unprepared.crt").read_bytes())')
    hosts['server'].update(nebula_ca_signing_cert_path='/opt/nebula/unprepared.crt',
                           nebula_ca_signing_key_path='/opt/nebula/unprepared.key',
                           nebula_ca_bundle_path='/opt/nebula/unprepared-bundle.crt', nebula_apply_timeout=60)
    save()
    rejected = observed_play(s, 'peer-rejection', 'deploy.yml', 'server')
    time.sleep(65)
    outcome = state()
    s.check('locally valid but peer-untrusted issuer is not committed', rejected['ansible_exit'] != 0 and
            outcome['directory'] != previous and outcome['activated'] and outcome['status'] == 'rolled-back' and
            before == s.ca_hashes('server', 'server.crt', 'server.key', 'ca.crt'))
    s.check('peer rejection recovery keeps the original process', old_pid == s.guest('server', 'systemctl', 'show', 'nebula', '-p', 'MainPID', '--value').stdout)
    for host in ('client', 'server'):
        s.guest(host, 'journalctl', '-u', 'nebula', '-n', '200', '--no-pager', '-o', 'cat')
    s.guest('client', 'python3', BASE + '/confirm-peer.py', 'server')
    s.check('fresh SSH recovers after peer-untrusted PKI rollback', True)
    # Restore the public signer cache after the deliberate failed candidate.
    old_public = s.guest('server', 'cat', '/opt/nebula/server.crt').stdout
    s.guest('client', 'python3', '-c', 'from pathlib import Path; import sys; Path("/opt/nebula/server.crt").write_text(sys.argv[1])', old_public)
    hosts['server'].clear()
    hosts['server'].update(original_host)
    save()
    stable = observed_play(s, 'after-peer-rollback', 'deploy.yml')
    s.check('ordinary role remains usable after rejected peer transition', stable['ansible_exit'] == 0)

    before_config = s.ca_hashes('server', 'config.yml')
    before = s.ca_hashes('server', 'server.crt', 'server.key', 'ca.crt')
    old_pid = s.guest('server', 'systemctl', 'show', 'nebula', '-p', 'MainPID', '--value').stdout
    previous = state()['directory']
    hosts['server'].update(nebula_inbound_rules=[{'port': 'any', 'proto': 'icmp', 'host': 'any'}], nebula_apply_timeout=60)
    save()
    blocked = observed_play(s, 'blocked-ssh', 'deploy.yml', 'server')
    time.sleep(65)
    outcome = state()
    s.check('blocking the management SSH policy fails and restores old configuration', blocked['ansible_exit'] != 0 and
            outcome['directory'] != previous and outcome['activated'] and outcome['status'] == 'rolled-back' and
            before_config == s.ca_hashes('server', 'config.yml'))
    s.check('SSH isolation was recovered by the on-host watchdog', outcome['rollback_trigger'] == 'watchdog')
    s.check('firewall rollback preserves PKI and the service process', before == s.ca_hashes('server', 'server.crt', 'server.key', 'ca.crt') and
            old_pid == s.guest('server', 'systemctl', 'show', 'nebula', '-p', 'MainPID', '--value').stdout)
    s.guest('client', 'python3', BASE + '/confirm-peer.py', 'server')
    s.check('fresh SSH works after autonomous firewall rollback', True)
