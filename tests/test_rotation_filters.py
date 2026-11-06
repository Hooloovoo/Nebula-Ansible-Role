import importlib.util
from pathlib import Path
import unittest

from ansible.errors import AnsibleFilterError

spec = importlib.util.spec_from_file_location(
    'nebula_rotation', Path(__file__).resolve().parents[1] / 'filter_plugins' / 'nebula_rotation.py')
rotation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rotation)


class RotationFilterTests(unittest.TestCase):
    def test_normalizes_legacy_and_current_certificate_json(self):
        old = {'details': {'ips': ['192.0.2.1/24'], 'subnets': None, 'groups': None}}
        new = [{'version': 1, 'details': {'networks': ['192.0.2.1/24'],
                                         'unsafeNetworks': [], 'groups': []}}]
        self.assertEqual(rotation.certificate(old)['details']['networks'], ['192.0.2.1/24'])
        self.assertEqual(rotation.certificate(new)['details']['networks'], ['192.0.2.1/24'])
        self.assertEqual(rotation.certificate(old)['details']['groups'], [])
        with self.assertRaises(AnsibleFilterError):
            rotation.certificate([old, old])

    def test_ca_arguments_preserve_restrictions_and_version(self):
        ca = [{'version': 1, 'details': {'isCa': True, 'curve': 'CURVE25519',
                                        'networks': ['192.0.2.0/24'],
                                        'unsafeNetworks': ['10.0.0.0/8'], 'groups': ['ops']}}]
        args = rotation.ca_argv(ca, 'nebula-cert', 'new.crt', 'new.key', 'new CA', '8760h', True)
        self.assertEqual(args[args.index('-version') + 1], '1')
        self.assertEqual(args[args.index('-ips') + 1], '192.0.2.0/24')
        self.assertEqual(args[args.index('-subnets') + 1], '10.0.0.0/8')
        self.assertEqual(args[args.index('-groups') + 1], 'ops')

    def test_leaf_signing_preserves_identity_and_reuses_public_key(self):
        leaf = {'version': 1, 'curve': 'CURVE25519', 'publicKey': '01' * 32,
                'details': {'isCa': False, 'name': 'node.example', 'networks': ['192.0.2.5/24'],
                            'unsafeNetworks': ['10.0.0.0/8'], 'groups': ['ssh-clients']}}
        self.assertIn('NEBULA X25519 PUBLIC KEY', rotation.public_key(leaf))
        args = rotation.leaf_argv(leaf, 'nebula-cert', 'ca.crt', 'ca.key', 'node.pub', 'node.crt', True, True)
        self.assertEqual(args[args.index('-name') + 1], 'node.example')
        self.assertEqual(args[args.index('-groups') + 1], 'ssh-clients')
        self.assertEqual(args[args.index('-version') + 1], '1')
        self.assertNotIn('-out-key', args)

    def test_trust_retirement_matches_certificate_bytes_and_preserves_others(self):
        old = '-----BEGIN NEBULA CERTIFICATE-----\nAQIDBA==\n-----END NEBULA CERTIFICATE-----\n'
        rewrapped = '-----BEGIN NEBULA CERTIFICATE-----\nAQID\nBA==\n-----END NEBULA CERTIFICATE-----\n'
        other = '-----BEGIN NEBULA CERTIFICATE-----\nAQI=\n-----END NEBULA CERTIFICATE-----\n'
        new = '-----BEGIN NEBULA CERTIFICATE-----\nAwQ=\n-----END NEBULA CERTIFICATE-----\n'
        self.assertTrue(rotation.contains_ca(rewrapped, old))
        self.assertEqual(rotation.trust_bundle(rewrapped + other, new, old), new + other)
        self.assertEqual(rotation.trust_bundle(new + other, new, old), new + other)
        with self.assertRaises(AnsibleFilterError):
            rotation.pem_blocks(old + 'unexpected')


if __name__ == '__main__':
    unittest.main()
