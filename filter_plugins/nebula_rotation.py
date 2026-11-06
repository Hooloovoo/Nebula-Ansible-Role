"""Certificate helpers shared by the staged CA-rotation tasks."""
import base64
import binascii
import re
import textwrap

from ansible.errors import AnsibleFilterError


def certificate(value):
    if isinstance(value, list):
        if len(value) != 1:
            raise AnsibleFilterError('Expected exactly one Nebula certificate')
        value = value[0]
    if not isinstance(value, dict) or not isinstance(value.get('details'), dict):
        raise AnsibleFilterError('Invalid Nebula certificate JSON')
    details = dict(value['details'])
    for modern, legacy in (('networks', 'ips'), ('unsafeNetworks', 'subnets')):
        details[modern] = details.get(modern, details.get(legacy)) or []
    details['groups'] = details.get('groups') or []
    if any(not isinstance(details[k], list) or not all(isinstance(v, str) for v in details[k])
           for k in ('networks', 'unsafeNetworks', 'groups')):
        raise AnsibleFilterError('Invalid certificate networks, subnets or groups')
    return dict(value, details=details)


def pem_blocks(bundle):
    pattern = r'-----BEGIN (NEBULA CERTIFICATE(?: V2)?)-----\s+.*?-----END \1-----'
    if not isinstance(bundle, str):
        raise AnsibleFilterError('Expected a PEM bundle')
    blocks = list(re.finditer(pattern, bundle, re.DOTALL))
    if not blocks or re.sub(pattern, '', bundle, flags=re.DOTALL).strip():
        raise AnsibleFilterError('Invalid PEM bundle')
    return [m.group(0) + '\n' for m in blocks]


def trust_bundle(bundle, new_ca, old_ca=None):
    blocks = pem_blocks(bundle)
    new = pem_blocks(new_ca)
    if len(new) != 1:
        raise AnsibleFilterError('Expected a single new CA')
    new_id = pem_identity(new[0])
    if old_ca is None:
        return bundle if any(pem_identity(block) == new_id for block in blocks) else bundle.rstrip() + '\n' + new[0]
    old = pem_blocks(old_ca)
    if len(old) != 1:
        raise AnsibleFilterError('Expected a single old CA')
    old_id = pem_identity(old[0])
    return new[0] + ''.join(b for b in blocks if pem_identity(b) not in (old_id, new_id))


def pem_identity(block):
    lines = block.strip().splitlines()
    try:
        return lines[0], base64.b64decode(''.join(lines[1:-1]), validate=True)
    except (ValueError, binascii.Error):
        raise AnsibleFilterError('Invalid Nebula PEM encoding')


def contains_ca(bundle, ca):
    blocks, selected = pem_blocks(bundle), pem_blocks(ca)
    if len(selected) != 1:
        raise AnsibleFilterError('Expected a single CA certificate')
    return any(pem_identity(block) == pem_identity(selected[0]) for block in blocks)


def public_key(value):
    value = certificate(value)
    details = value['details']
    if details.get('isCa') is not False:
        raise AnsibleFilterError('Expected a host certificate')
    curve = value.get('curve', details.get('curve'))
    formats = {'CURVE25519': ('NEBULA X25519 PUBLIC KEY', 32),
               'P256': ('NEBULA P256 PUBLIC KEY', 65)}
    if curve not in formats:
        raise AnsibleFilterError('Unsupported certificate curve')
    try:
        raw = bytes.fromhex(value.get('publicKey', details.get('publicKey')))
    except (TypeError, ValueError):
        raise AnsibleFilterError('Invalid certificate public key')
    banner, length = formats[curve]
    if len(raw) != length:
        raise AnsibleFilterError('Invalid certificate public key length')
    encoded = base64.b64encode(raw).decode('ascii')
    return '-----BEGIN ' + banner + '-----\n' + '\n'.join(textwrap.wrap(encoded, 64)) + '\n-----END ' + banner + '-----\n'


def ca_argv(value, binary, cert_path, key_path, name, duration, has_version):
    value = certificate(value)
    details = value['details']
    if details.get('isCa') is not True:
        raise AnsibleFilterError('Signing certificate is not a CA')
    version = value.get('version', 1)
    if version not in (1, 2) or (version == 2 and not has_version):
        raise AnsibleFilterError('Signer cannot preserve the CA version')
    curve = value.get('curve', details.get('curve', 'CURVE25519'))
    if curve not in ('CURVE25519', 'P256'):
        raise AnsibleFilterError('Unsupported CA curve')
    args = [binary, 'ca', '-name', name, '-duration', duration, '-out-crt', cert_path,
            '-out-key', key_path, '-curve', '25519' if curve == 'CURVE25519' else 'P256']
    if has_version:
        args += ['-version', str(version)]
    for flag, field in (('-ips', 'networks'), ('-subnets', 'unsafeNetworks'), ('-groups', 'groups')):
        if any(',' in x for x in details[field]):
            raise AnsibleFilterError('CA restriction contains a comma')
        if details[field]:
            args += [flag, ','.join(details[field])]
    return args


def leaf_argv(value, binary, ca_cert, ca_key, public_path, output, has_version, has_networks):
    value = certificate(value)
    details = value['details']
    version = value.get('version', 1)
    if details.get('isCa') is not False or not details.get('name') or not details['networks']:
        raise AnsibleFilterError('Expected a named host certificate with networks')
    if version not in (1, 2) or (version == 2 and not has_version):
        raise AnsibleFilterError('Signer cannot preserve the host certificate version')
    if len(details['networks']) != 1 and not has_networks:
        raise AnsibleFilterError('Signer cannot preserve multiple networks')
    if any(',' in x for k in ('networks', 'unsafeNetworks', 'groups') for x in details[k]):
        raise AnsibleFilterError('Certificate field contains a comma')
    args = [binary, 'sign', '-ca-crt', ca_cert, '-ca-key', ca_key, '-name', details['name'],
            '-in-pub', public_path, '-out-crt', output, '-networks' if has_networks else '-ip',
            ','.join(details['networks'])]
    if has_version:
        args += ['-version', str(version)]
    for flag, field in (('-subnets', 'unsafeNetworks'), ('-groups', 'groups')):
        if details[field]:
            args += [flag, ','.join(details[field])]
    return args


class FilterModule:
    def filters(self):
        return {'rotation_certificate': certificate, 'rotation_pem_blocks': pem_blocks,
                'rotation_trust_bundle': trust_bundle, 'rotation_contains_ca': contains_ca,
                'rotation_public_key': public_key,
                'rotation_ca_argv': ca_argv, 'rotation_leaf_argv': leaf_argv}
