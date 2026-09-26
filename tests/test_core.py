import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from app.core import ReleaseService, Runner, Store, bump, read_package, redact, validate_options


class FakeRunner:
    def __init__(self, fail=None):
        self.commands = []
        self.log = lambda line: None
        self.fail = fail

    def run(self, args, cwd, **kwargs):
        self.commands.append(args)
        command = args[1]
        if command == self.fail:
            raise RuntimeError('simulated failure')
        if command == 'view':
            return 1, 'E404'
        path = Path(cwd) / 'package.json'
        if command == 'version':
            data = json.loads(path.read_text())
            data['version'] = args[2]
            path.write_text(json.dumps(data))
        if command == 'pack' and '--pack-destination' in args:
            target = Path(args[args.index('--pack-destination') + 1]) / 'package.tgz'
            with tarfile.open(target, 'w:gz') as tar:
                content = path.read_bytes()
                info = tarfile.TarInfo('package/package.json')
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
        return 0, 'test-user'


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder = self.root / 'package with spaces'
        self.folder.mkdir()
        self.data = dict(name='@test/example', version='1.2.3', scripts={'test': 'test', 'build': 'build'})
        self.save()
        self.store = Store(self.root / 'data')
        self.runner = FakeRunner()
        self.which = patch('app.core.shutil.which', return_value='/usr/bin/npm')
        self.which.start()
        self.service = ReleaseService(self.store, self.runner)

    def tearDown(self):
        self.which.stop()
        self.temp.cleanup()

    def save(self):
        (self.folder / 'package.json').write_text(json.dumps(self.data))

    def prepare(self):
        return self.service.prepare(self.folder, '1.2.4', 'https://registry.npmjs.org/', 'latest', 'public')

    def test_bumps_and_prerelease(self):
        self.assertEqual(bump('1.2.3', 'Patch'), '1.2.4')
        self.assertEqual(bump('1.2.3', 'Minor'), '1.3.0')
        self.assertEqual(bump('1.2.3', 'Major'), '2.0.0')
        self.assertEqual(bump('2.0.0-beta.1', 'Major'), '2.0.0')
        self.assertEqual(bump('1.2.3-beta.1', 'Patch'), '1.2.3')
        with self.assertRaises(ValueError):
            bump('1.2.3-01', 'Patch')

    def test_invalid_package_and_options(self):
        self.data['name'] = '--flag'
        self.save()
        with self.assertRaises(ValueError):
            read_package(self.folder)
        for url, tag in [('https://user:token@example.org', 'latest'), ('file:///tmp', 'latest'), ('https://registry.npmjs.org', '--bad')]:
            with self.assertRaises(ValueError):
                validate_options(url, tag, 'public')

    def test_prepare_never_publishes_and_publish_uses_archive(self):
        p = self.prepare()
        self.assertEqual(p.version, '1.2.4')
        self.assertEqual(len(p.files), 1)
        self.assertNotIn('publish', [c[1] for c in self.runner.commands])
        self.assertIn(['run', 'test'], [c[1:] for c in self.runner.commands])
        self.assertIn(['run', 'build'], [c[1:] for c in self.runner.commands])
        result = self.service.publish(p)
        self.assertEqual(result['status'], 'success')
        command = self.runner.commands[-1]
        self.assertEqual(command[2], str(p.archive))
        self.assertIn('--ignore-scripts', command)
        self.assertIn('--@test:registry=https://registry.npmjs.org/', command)
        self.assertEqual(self.store.read('history', [])[0]['status'], 'success')

    def test_tamper_blocks_publish(self):
        p = self.prepare()
        p.archive.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Archive changed'):
            self.service.publish(p)
        self.assertNotIn('publish', [c[1] for c in self.runner.commands])

    def test_private_and_conflicting_config_block_before_commands(self):
        for extra in [{'private': True}, {'publishConfig': {'registry': 'https://other.example/'}}]:
            self.data.update(extra)
            self.save()
            with self.assertRaises(ValueError):
                self.prepare()
            self.assertEqual(self.runner.commands, [])
            self.data.pop('private', None)

    def test_optional_scripts_skipped(self):
        self.data['scripts'] = {}
        self.save()
        self.prepare()
        self.assertNotIn('run', [c[1] for c in self.runner.commands])

    def test_build_failure_stops_before_pack(self):
        self.runner.fail = 'run'
        with self.assertRaises(RuntimeError):
            self.prepare()
        self.assertNotIn('pack', [c[1] for c in self.runner.commands])

    def test_failed_publish_recorded(self):
        p = self.prepare()
        self.runner.fail = 'publish'
        with self.assertRaises(RuntimeError):
            self.service.publish(p)
        self.assertEqual(self.store.read('history', [])[0]['status'], 'failed / verify registry')

    def test_existing_version_and_network_failure_block(self):
        for response in [(0, '"1.2.4"'), (1, 'ECONNREFUSED')]:
            original = self.runner.run
            def run(args, cwd, **kwargs):
                return response if args[1] == 'view' else original(args, cwd, **kwargs)
            with patch.object(self.runner, 'run', side_effect=run):
                with self.assertRaises(ValueError):
                    self.prepare()
        self.assertNotIn('version', [c[1] for c in self.runner.commands])

    def test_redaction_and_atomic_storage(self):
        self.assertNotIn('secret', redact('_authToken=secret'))
        self.assertNotIn('npm_abc123', redact('token npm_abc123'))
        self.store.write('settings', {'tag': 'beta'})
        self.assertEqual(self.store.read('settings', {}), {'tag': 'beta'})
        self.assertEqual(list(self.store.root.glob('*.tmp')), [])

    def test_staging_upload_and_history(self):
        original = self.runner.run
        def run(args, cwd, **kwargs):
            if args[1:3] == ['stage', '--help']:
                return 0, 'npm stage publish'
            if args[1:4] == ['view', self.data['name'], 'name']:
                return 0, '"@test/example"'
            if args[1:3] == ['stage', 'publish']:
                self.runner.commands.append(args)
                return 0, 'Staged successfully: stage-example-id'
            return original(args, cwd, **kwargs)
        with patch.object(self.runner, 'run', side_effect=run):
            p = self.service.prepare(self.folder, '1.2.4', 'https://registry.npmjs.org/', 'latest', 'public', 'stage')
            self.assertEqual(p.mode, 'stage')
            self.assertNotIn('publish', [c[1] for c in self.runner.commands])
            entry = self.service.publish(p)
        self.assertEqual(self.runner.commands[-1][1:3], ['stage', 'publish'])
        self.assertEqual(self.runner.commands[-1][3], str(p.archive))
        self.assertEqual(entry['status'], 'staged')
        self.assertIn('stage-example-id', entry['stage_output'])
        self.assertEqual(self.store.read('history', [])[0]['status'], 'staged')
        self.assertIn('stage list', entry['review_command'])

    def test_staging_first_release_blocked_before_local_changes(self):
        original = self.runner.run
        def run(args, cwd, **kwargs):
            if args[1:3] == ['stage', '--help']:
                return 0, 'npm stage publish'
            return original(args, cwd, **kwargs)
        with patch.object(self.runner, 'run', side_effect=run):
            with self.assertRaisesRegex(ValueError, 'existing package'):
                self.service.prepare(self.folder, '1.2.4', 'https://registry.npmjs.org/', 'latest', 'public', 'stage')
        self.assertEqual(read_package(self.folder)['version'], '1.2.3')
        self.assertNotIn('install', [c[1] for c in self.runner.commands])

    def test_unsupported_staging_cli(self):
        with self.assertRaisesRegex(ValueError, 'does not support staging'):
            self.service.prepare(self.folder, '1.2.4', 'https://registry.npmjs.org/', 'latest', 'public', 'stage')

    def test_process_timeout(self):
        import sys
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            Runner().run([sys.executable, '-c', 'import time; time.sleep(10)'], self.folder, timeout=0.1)


if __name__ == '__main__':
    unittest.main()
