"""Focused red/green acceptance cases for the normal-role PR series."""
import json
from pathlib import Path
import time


def play(s, inventory, limit, full=False, check=True, extra=(), playbook=None):
    start = s.log.stat().st_size
    status = s.command(['ansible-playbook', '-i', str(inventory),
                        str(Path(__file__).parent / (playbook or ('deploy.yml' if full else 'reconcile.yml'))),
                        '--limit', limit, *extra], check=check, timeout=1200)
    with s.log.open() as stream:
        stream.seek(start)
        return status, stream.read()


def subject(s):
    before = s.hashes('client')
    inventory = s.inventory()
    data = json.loads(inventory.read_text())
    data['all']['children']['nodes']['hosts']['client'].update(nebula_cert_name='client.example.test',
                                                             nebula_cert_groups=['subject-test'])
    inventory.write_text(json.dumps(data))
    play(s, inventory, 'client')
    s.check('explicit certificate subject is independent of inventory filename', s.cert('client')['name'] == 'client.example.test')
    s.check('subject reissue keeps canonical inventory filenames', before != s.hashes('client'))
    before = s.hashes('client')
    play(s, inventory, 'client')
    s.check('subject override repeat preserves certificate and key', before == s.hashes('client'))
    inventory = s.inventory()
    data = json.loads(inventory.read_text())
    data['all']['children']['nebula_lighthouse']['hosts']['lh']['nebula_cert_name'] = 'lh.example.test'
    inventory.write_text(json.dumps(data))
    before = s.hashes('lh')
    play(s, inventory, 'lh', full=True)
    s.check('subject override does not implicitly reissue an existing lighthouse', before == s.hashes('lh'))
    s.guest('lh', 'rm', '/opt/nebula/lh.crt', '/opt/nebula/lh.key')
    play(s, inventory, 'lh', full=True)
    s.check('first lighthouse signing uses explicit subject and canonical filename', s.cert('lh')['name'] == 'lh.example.test')


def signer(s):
    s.cleanup()
    s.provision()
    s.ca_deploy(separate=True)
    s.ca_deploy(separate=True, ca_node=True, limit='cah')
    s.guest('client', 'cp', '/opt/nebula/ca.crt', '/opt/nebula/active-ca.crt')
    s.guest('client', '/opt/nebula/nebula-cert', 'ca', '-name', 'historical', '-duration', '1s',
            '-out-crt', '/opt/nebula/historical.crt', '-out-key', '/opt/nebula/historical.key')
    time.sleep(3)
    s.guest('client', 'python3', '-c', 'from pathlib import Path; p=Path("/opt/nebula"); '
            '(p/"desired-bundle.crt").write_bytes((p/"historical.crt").read_bytes()+(p/"active-ca.crt").read_bytes())')
    inventory = s.ca_inventory(separate=True, ca_node=True)
    data = json.loads(inventory.read_text())
    data['all']['vars'].update(nebula_ca_signing_cert_path='/opt/nebula/active-ca.crt',
                              nebula_ca_signing_key_path='/opt/nebula/ca.key',
                              nebula_ca_bundle_path='/opt/nebula/desired-bundle.crt')
    inventory.write_text(json.dumps(data))
    before = s.ca_hashes('client', 'cah.crt', 'cah.key')
    old_pid = s.guest('client', 'systemctl', 'show', 'nebula', '-p', 'MainPID', '--value').stdout
    play(s, inventory, 'cah', playbook='ca-deploy.yml')
    s.check('CA-node receives independently supplied trust bundle',
            s.ca_hashes('client', 'ca.crt') == s.ca_hashes('client', 'desired-bundle.crt'))
    s.check('CA-only update preserves CA-node identity and process', before == s.ca_hashes('client', 'cah.crt', 'cah.key') and
            old_pid == s.guest('client', 'systemctl', 'show', 'nebula', '-p', 'MainPID', '--value').stdout)
    data['all']['children']['nodes']['hosts']['server']['nebula_cert_groups'] = ['explicit-signer']
    inventory.write_text(json.dumps(data))
    play(s, inventory, 'server', playbook='ca-deploy.yml')
    printed = json.loads(s.guest('server', '/opt/nebula/nebula-cert', 'print', '-json', '-path', '/opt/nebula/server.crt').stdout)
    ca = json.loads(s.guest('client', '/opt/nebula/nebula-cert', 'print', '-json', '-path', '/opt/nebula/active-ca.crt').stdout)
    leaf = (printed[0] if isinstance(printed, list) else printed)
    selected = ca[0] if isinstance(ca, list) else ca
    s.check('leaf is signed by isolated intended CA despite bundle order', leaf['details']['issuer'] == selected['fingerprint'])
    s.check('consumer receives supplied bundle unchanged',
            s.ca_hashes('server', 'ca.crt') == s.ca_hashes('client', 'desired-bundle.crt'))
    before = s.ca_hashes('server', 'server.crt', 'server.key', 'ca.crt')
    play(s, inventory, 'server', playbook='ca-deploy.yml')
    s.check('explicit signing input repeat is idempotent', before == s.ca_hashes('server', 'server.crt', 'server.key', 'ca.crt'))


def staged(s):
    before = s.ca_hashes('client', 'client.crt', 'client.key')
    s.groups['client'] = ['preserved-key']
    s.deploy('client')
    after = s.ca_hashes('client', 'client.crt', 'client.key')
    s.check('reissue changes certificate but preserves the private key', before[0] != after[0] and before[1] == after[1])
    signer_before = s.ca_hashes('lh', 'client.crt', 'client.key')
    s.guest('lh', 'mv', '/opt/nebula/nebula-cert', '/opt/nebula/nebula-cert.real')
    wrapper = '#!/bin/sh\nif [ "$1" = sign ]; then exit 42; fi\nexec /opt/nebula/nebula-cert.real "$@"\n'
    try:
        s.guest('lh', 'python3', '-c', 'from pathlib import Path; import sys; p=Path("/opt/nebula/nebula-cert"); p.write_text(sys.argv[1]); p.chmod(0o700)', wrapper)
        s.groups['client'] = ['failed-sign']
        status, _ = play(s, s.inventory(), 'client', check=False)
        s.check('staged signing failure preserves signer and consumer files', status != 0 and
                s.ca_hashes('lh', 'client.crt', 'client.key') == signer_before and s.ca_hashes('client', 'client.crt', 'client.key') == after)
    finally:
        s.guest('lh', 'mv', '/opt/nebula/nebula-cert.real', '/opt/nebula/nebula-cert')
    s.guest('lh', 'rm', '/opt/nebula/client.key')
    s.deploy('client')
    s.check('existing consumer key need not be cached on signer for reissue',
            s.ca_hashes('client', 'client.key')[0] == before[1] and s.groups_are('client', ['failed-sign']))
    before = s.ca_hashes('client', 'client.crt', 'client.key')
    s.deploy('client')
    s.check('key-preserving repeat is idempotent', before == s.ca_hashes('client', 'client.crt', 'client.key'))


def lighthouse(s):
    before = s.ca_hashes('lh', 'lh.crt', 'lh.key')
    old_pid = s.guest('lh', 'systemctl', 'show', 'lighthouse', '-p', 'MainPID', '--value').stdout
    s.groups['lh'] = ['lighthouse-admins']
    s.deploy('lh')
    s.check('ordinary lighthouse run reconciles certificate groups', s.groups_are('lh', ['lighthouse-admins']))
    after = s.ca_hashes('lh', 'lh.crt', 'lh.key')
    s.check('lighthouse drift preserves key and running process', before[0] != after[0] and before[1] == after[1] and
            old_pid == s.guest('lh', 'systemctl', 'show', 'lighthouse', '-p', 'MainPID', '--value').stdout)
    s.fresh_handshake('lh', 'server', '10.193.0.2')
    s.deploy('lh')
    s.check('lighthouse repeat preserves the issued certificate', after == s.ca_hashes('lh', 'lh.crt', 'lh.key'))


def issuer(s):
    s.guest('lh', 'cp', '/opt/nebula/ca.crt', '/opt/nebula/active-ca.crt')
    s.guest('lh', '/opt/nebula/nebula-cert', 'ca', '-name', 'replacement', '-duration', '87600h',
            '-out-crt', '/opt/nebula/new-ca.crt', '-out-key', '/opt/nebula/new-ca.key')
    s.guest('lh', 'python3', '-c', 'from pathlib import Path; p=Path("/opt/nebula"); '
            '(p/"desired-bundle.crt").write_bytes((p/"active-ca.crt").read_bytes()+(p/"new-ca.crt").read_bytes())')
    inventory = s.inventory()
    data = json.loads(inventory.read_text())
    data['all']['vars'].update(nebula_ca_signing_cert_path='/opt/nebula/active-ca.crt',
                              nebula_ca_signing_key_path='/opt/nebula/ca.key',
                              nebula_ca_bundle_path='/opt/nebula/desired-bundle.crt')
    inventory.write_text(json.dumps(data))
    play(s, inventory, 'all', full=True)
    before = {h: s.ca_hashes(h, h + '.crt', h + '.key') for h in s.names}
    pids = {h: s.guest(h, 'systemctl', 'show', 'lighthouse' if h == 'lh' else 'nebula', '-p', 'MainPID', '--value').stdout for h in s.names}
    ca = json.loads(s.guest('lh', '/opt/nebula/nebula-cert', 'print', '-json', '-path', '/opt/nebula/new-ca.crt').stdout)
    fingerprint = (ca[0] if isinstance(ca, list) else ca)['fingerprint']
    data['all']['vars'].update(nebula_ca_signing_cert_path='/opt/nebula/new-ca.crt', nebula_ca_signing_key_path='/opt/nebula/new-ca.key')
    inventory.write_text(json.dumps(data))
    play(s, inventory, 'all', full=True)
    for host in s.names:
        s.check(host + ' ordinary role corrects issuer drift', s.cert(host)['issuer'] == fingerprint)
        after = s.ca_hashes(host, host + '.crt', host + '.key')
        s.check(host + ' issuer correction preserves key and process', after[0] != before[host][0] and after[1] == before[host][1] and
                pids[host] == s.guest(host, 'systemctl', 'show', 'lighthouse' if host == 'lh' else 'nebula', '-p', 'MainPID', '--value').stdout)
    before = {h: s.ca_hashes(h, h + '.crt', h + '.key') for h in s.names}
    play(s, inventory, 'all', full=True)
    s.check('issuer correction repeat preserves identities', all(before[h] == s.ca_hashes(h, h + '.crt', h + '.key') for h in s.names))
    s.fresh_handshake('client', 'lh', '10.193.0.1')
    s.guest('lh', 'cp', '/opt/nebula/new-ca.crt', '/opt/nebula/desired-bundle.crt')
    play(s, inventory, 'all', full=True)
    s.check('ordinary role can retire old trust without reissuing leaves', all(before[h] == s.ca_hashes(h, h + '.crt', h + '.key') for h in s.names))
    s.check('signer staging directories are cleaned', s.guest('lh', 'python3', '-c',
            'import glob; assert not glob.glob("/opt/nebula/.nebula-sign-*")').returncode == 0)


def run_feature(suite, name):
    cases = {'subject': subject, 'signer': signer, 'staged': staged, 'lighthouse': lighthouse, 'issuer': issuer}
    if name not in cases:
        raise ValueError('Feature case is not implemented yet: ' + name)
    cases[name](suite)
