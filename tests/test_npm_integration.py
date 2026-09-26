"""Real npm commands against a temporary, dependency-free package; no publishing."""
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from app.core import Runner


@unittest.skipUnless(shutil.which('npm'), 'npm not installed')
class NpmIntegrationTests(unittest.TestCase):
    def test_version_lockfile_and_archive(self):
        with tempfile.TemporaryDirectory(prefix='publishconsole-test-') as d:
            folder = Path(d)
            (folder / 'package.json').write_text(json.dumps({
                'name': 'publishconsole-local-test', 'version': '1.0.0',
                'files': ['index.js'], 'scripts': {'test': 'node -e "process.exit(0)"'}
            }))
            (folder / 'index.js').write_text('module.exports = 42;\n')
            (folder / 'secret.txt').write_text('excluded fixture')
            archive_dir = folder / 'artifacts'
            archive_dir.mkdir()
            runner = Runner()
            npm = shutil.which('npm')
            def run(*args):
                return runner.run([npm, *args], folder, timeout=30)
            run('install', '--offline', '--no-audit', '--no-fund', '--ignore-scripts')
            run('version', '1.0.1', '--no-git-tag-version', '--ignore-scripts')
            self.assertEqual(json.loads((folder / 'package-lock.json').read_text())['version'], '1.0.1')
            run('test')
            code, out = run('pack', '--dry-run', '--json', '--ignore-scripts')
            preview = json.loads(out)[0]
            self.assertEqual(preview['version'], '1.0.1')
            self.assertNotIn('secret.txt', [f['path'] for f in preview['files']])
            run('pack', '--pack-destination', str(archive_dir))
            with tarfile.open(next(archive_dir.glob('*.tgz'))) as tar:
                metadata = json.load(tar.extractfile('package/package.json'))
                self.assertEqual(metadata['version'], '1.0.1')
                self.assertIn('package/index.js', tar.getnames())
                self.assertNotIn('package/secret.txt', tar.getnames())
