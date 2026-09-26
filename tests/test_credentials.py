import tempfile
import unittest
from unittest.mock import Mock, patch
from app.credentials import Credentials, registry_key, token_environment
from app.core import Runner


class CredentialTests(unittest.TestCase):
    def test_persistence_reload_remove(self):
        values = {}
        backend = Mock()
        backend.set_password.side_effect = lambda service, key, value: values.update({key: value})
        backend.get_password.side_effect = lambda service, key: values.get(key)
        backend.delete_password.side_effect = lambda service, key: values.pop(key)
        registry = 'https://registry.npmjs.org/'
        with patch.object(Credentials, 'backend', return_value=backend):
            first = Credentials()
            first.save(registry, 'dummy-secret', True)
            second = Credentials()
            self.assertEqual(second.get(registry, True), 'dummy-secret')
            second.remove(registry, True)
            self.assertIsNone(Credentials().get(registry, True))

    def test_session_only_never_uses_store(self):
        credentials = Credentials()
        with patch.object(credentials, 'backend', side_effect=AssertionError('Unexpected storage')):
            credentials.save('https://registry.npmjs.org/', 'dummy-secret', False)
            self.assertEqual(credentials.get('https://registry.npmjs.org'), 'dummy-secret')
            self.assertIsNone(credentials.get('https://other.example/'))
            credentials.remove('https://registry.npmjs.org/')
            self.assertIsNone(credentials.get('https://registry.npmjs.org/'))

    def test_registry_scope_and_redaction(self):
        env = token_environment('https://registry.example/custom', 'dummy-secret')
        self.assertEqual(env, {'npm_config_//registry.example/custom/:_authToken': 'dummy-secret'})
        lines = []
        runner = Runner(lines.append, env)
        runner.log('output dummy-secret')
        self.assertEqual(lines, ['output [REDACTED]'])
        self.assertNotIn('dummy-secret', runner.sanitize('failure dummy-secret'))
        with self.assertRaises(ValueError):
            registry_key('http://registry.npmjs.org/')

    def test_unavailable_keyring_does_not_silently_save(self):
        c = Credentials()
        with patch.object(c, 'backend', side_effect=RuntimeError('Unavailable')):
            with self.assertRaises(RuntimeError):
                c.save('https://registry.npmjs.org/', 'dummy', True)
        self.assertEqual(c.session, {})
