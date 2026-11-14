#!/usr/bin/env python3
"""Probe expired-CA handling inside a disposable Linux VM (requires root/TUN).

Run with --workdir PATH, where PATH does not yet exist. Produces results.json,
public certificate metadata and per-operation logs; generated keys stay in the
root-only work directory. This is a binary compatibility probe, not a role test.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tarfile
import time
import urllib.request


VERSIONS = ('1.10.3', '1.11.2')


def assert_contract(results):
    """Check only these pinned versions; do not infer a release cutoff."""
    records = {r['operation']: r for r in results['operations']}
    checks = 0

    def check(label, condition):
        nonlocal checks
        if not condition:
            raise AssertionError(label)
        checks += 1

    for version in VERSIONS:
        for operation in ('isolated-intended-verify', 'isolated-sign', 'isolated-sign-verify'):
            label = version + '-' + operation
            check(label, records[label]['exit_status'] == 0)
        label = version + '-isolated-wrong-issuer-verify'
        check(label, records[label]['exit_status'] == 1)
        for case in ('valid-only', 'expired-first', 'expired-last', 'expired-only', 'malformed'):
            mixed = case in ('expired-first', 'expired-last')
            usable = case == 'valid-only' or mixed
            label = version + '-verify-' + case
            expected = 0 if case == 'valid-only' or (mixed and version == '1.11.2') else 1
            check(label, records[label]['exit_status'] == expected)
            label = version + '-test-' + case
            check(label, records[label]['exit_status'] == (0 if usable else 1))
            label = version + '-startup-' + case
            check(label, records[label]['startup_alive'] == usable)
            label = version + '-reload-' + case
            record = records[label]
            check(label + ' process survives', record['alive_after_sighup'])
            check(label + ' CA error', ('Failed to load ca from config' in record['reload_log']) == (not usable))
            if case.startswith('expired-'):
                check(label + ' expiry warning', 'expired certificate present in CA pool' in record['reload_log'])
            if mixed:
                check(version + '-startup-' + case + ' expiry warning',
                      'expired certificate present in CA pool' in records[version + '-startup-' + case]['startup_log'])
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--workdir', type=Path)
    mode.add_argument('--check-results', type=Path, help='check saved results without running binaries or requiring TUN')
    args = parser.parse_args()
    if args.check_results:
        print('PASS', assert_contract(json.loads(args.check_results.read_text())), 'contract assertions')
        return
    fd = os.open('/dev/net/tun', os.O_RDWR)
    os.close(fd)
    root = args.workdir.resolve()
    root.mkdir(mode=0o700)
    results = {'versions': {}, 'operations': []}

    def save():
        (root / 'results.json').write_text(json.dumps(results, indent=2) + '\n')

    def command(label, argv, required=False):
        result = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=60)
        (root / (label + '.stdout')).write_text(result.stdout)
        (root / (label + '.stderr')).write_text(result.stderr)
        record = {'operation': label, 'argv': list(map(str, argv)), 'exit_status': result.returncode,
                  'stderr': result.stderr.strip()}
        results['operations'].append(record)
        save()
        if required and result.returncode:
            raise RuntimeError(f'{label}: {result.stderr}')
        return result

    for version in VERSIONS:
        archive = root / (version + '.tar.gz')
        url = f'https://github.com/slackhq/nebula/releases/download/v{version}/nebula-linux-amd64.tar.gz'
        urllib.request.urlretrieve(url, archive)
        binaries = root / version
        binaries.mkdir()
        with tarfile.open(archive) as tar:
            for name in ('nebula', 'nebula-cert'):
                target = binaries / name
                target.write_bytes(tar.extractfile(name).read())
                target.chmod(0o700)
        results['versions'][version] = {
            'url': url, 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
            'binary_sha256': {name: hashlib.sha256((binaries / name).read_bytes()).hexdigest()
                              for name in ('nebula', 'nebula-cert')},
        }
        command(version + '-version', [binaries / 'nebula', '-version'], required=True)

    # All operations below use these identical fixtures, generated once with 1.10.3.
    signer = root / VERSIONS[0] / 'nebula-cert'
    for name, duration in (('intended', '87600h'), ('historical', '1s'), ('unrelated', '87600h')):
        command('create-' + name, [signer, 'ca', '-name', name, '-version', '1',
                                  '-duration', duration, '-out-crt', root / (name + '.crt'),
                                  '-out-key', root / (name + '.key')], required=True)
    for name, ca in (('leaf', 'intended'), ('wrong-issuer', 'unrelated')):
        command('sign-' + name, [signer, 'sign', '-name', name, '-ip', '10.233.250.1/24',
                                '-duration', '8760h', '-ca-crt', root / (ca + '.crt'),
                                '-ca-key', root / (ca + '.key'), '-out-crt', root / (name + '.crt'),
                                '-out-key', root / (name + '.key')], required=True)
    time.sleep(3)
    historical = command('historical-metadata', [signer, 'print', '-json', '-path',
                                               root / 'historical.crt'], required=True)
    metadata = json.loads(historical.stdout)[0]
    not_after = datetime.fromisoformat(metadata['details']['notAfter'].replace('Z', '+00:00'))
    if datetime.now(timezone.utc) <= not_after:
        raise RuntimeError('Historical CA has not expired')
    results['fixture'] = {'generated_by': VERSIONS[0], 'certificate_version': 1,
                          'historical': metadata, 'checked_expired_at': datetime.now(timezone.utc).isoformat()}
    intended = (root / 'intended.crt').read_bytes()
    expired = (root / 'historical.crt').read_bytes()
    cases = {'valid-only': intended, 'expired-first': expired + intended,
             'expired-last': intended + expired, 'expired-only': expired,
             'malformed': b'not a PEM certificate\n'}
    for case, content in cases.items():
        (root / (case + '.bundle')).write_bytes(content)
    results['fixture']['bundle_sha256'] = {
        case: hashlib.sha256(content).hexdigest() for case, content in cases.items()}

    config = root / 'config.yml'
    live = root / 'live-ca.crt'
    config.write_text(f'''pki:
  ca: {live}
  cert: {root / 'leaf.crt'}
  key: {root / 'leaf.key'}
lighthouse:
  am_lighthouse: true
listen:
  host: 127.0.0.1
  port: 4242
tun:
  dev: nexpca
logging:
  level: info
  format: text
firewall:
  inbound:
    - {{port: any, proto: any, host: any}}
  outbound:
    - {{port: any, proto: any, host: any}}
''')

    def daemon(version, case, reload=False):
        label = version + ('-reload-' if reload else '-startup-') + case
        live.write_bytes(intended if reload else cases[case])
        log_path = root / (label + '.log')
        with log_path.open('w') as log:
            process = subprocess.Popen([str(root / version / 'nebula'), '-config', str(config)],
                                       stdout=log, stderr=subprocess.STDOUT)
            try:
                time.sleep(3)
                started = process.poll() is None
                initial = log_path.read_text()
                record = {'operation': label, 'startup_alive': started,
                          'startup_exit_status': process.poll(), 'pid': process.pid}
                if started and reload:
                    live.write_bytes(cases[case])
                    process.send_signal(signal.SIGHUP)
                    time.sleep(3)
                    record.update(alive_after_sighup=process.poll() is None,
                                  reload_log=log_path.read_text()[len(initial):])
                else:
                    record['startup_log'] = initial
                results['operations'].append(record)
                save()
                if (reload or case == 'valid-only') and not started:
                    raise RuntimeError(f'{label}: valid-only startup control failed')
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)

    for version in VERSIONS:
        binary = root / version / 'nebula'
        cert = root / version / 'nebula-cert'
        command(version + '-isolated-intended-verify',
                [cert, 'verify', '-ca', root / 'intended.crt', '-crt', root / 'leaf.crt'], required=True)
        command(version + '-isolated-sign', [cert, 'sign', '-name', 'isolated-sign', '-ip', '10.233.250.2/24',
                                             '-ca-crt', root / 'intended.crt', '-ca-key', root / 'intended.key',
                                             '-out-crt', root / version / 'signed.crt',
                                             '-out-key', root / version / 'signed.key'], required=True)
        command(version + '-isolated-sign-verify',
                [cert, 'verify', '-ca', root / 'intended.crt', '-crt', root / version / 'signed.crt'], required=True)
        wrong = command(version + '-isolated-wrong-issuer-verify',
                        [cert, 'verify', '-ca', root / 'intended.crt', '-crt', root / 'wrong-issuer.crt'])
        if wrong.returncode == 0:
            raise RuntimeError('Isolated intended CA accepted the wrong issuer')
        for case in cases:
            command(version + '-verify-' + case,
                    [cert, 'verify', '-ca', root / (case + '.bundle'), '-crt', root / 'leaf.crt'])
            shutil.copyfile(root / (case + '.bundle'), live)
            command(version + '-test-' + case, [binary, '-test', '-config', config])
            daemon(version, case)
            daemon(version, case, reload=True)
    save()
    for record in results['operations']:
        label = record['operation']
        if '-verify-' in label or '-test-' in label:
            print(label, 'exit=' + str(record['exit_status']), record['stderr'])
        elif '-startup-' in label:
            print(label, 'alive=' + str(record['startup_alive']))
        elif '-reload-' in label:
            text = record.get('reload_log', '')
            print(label, 'alive=' + str(record.get('alive_after_sighup')),
                  'ca_load_error=' + str('Failed to load ca from config' in text),
                  'expired_warning=' + str('expired certificate present in CA pool' in text))
    print('Full results:', root / 'results.json')
    print('PASS', assert_contract(results), 'contract assertions')


if __name__ == '__main__':
    main()
