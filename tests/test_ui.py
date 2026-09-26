import json
import os
from pathlib import Path
import tempfile
import time
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtWidgets import QApplication
    from app.ui import MainWindow
except ImportError:
    QApplication = None
from app.core import Prepared, Store


@unittest.skipIf(QApplication is None, 'PySide6 not installed')
class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.window = MainWindow(Store(self.root / 'state'))
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()
        self.tmp.cleanup()

    def test_navigation_and_initial_publish_gate(self):
        for i in range(5):
            self.window.nav.setCurrentRow(i)
            self.app.processEvents()
            self.assertEqual(self.window.pages.currentIndex(), i)
        self.assertFalse(self.window.publish_button.isEnabled())
        self.assertFalse(self.window.prepare_button.isEnabled())

    def test_prepared_options_change_invalidates_archive(self):
        folder = self.root / 'package'
        folder.mkdir()
        data = {'name': 'example-package', 'version': '1.0.1'}
        (folder / 'package.json').write_text(json.dumps(data))
        self.window.folder = folder
        self.window.data = data
        self.window.render_package()
        archive_dir = self.root / 'archive'
        archive_dir.mkdir()
        archive = archive_dir / 'test.tgz'
        archive.write_bytes(b'test')
        p = Prepared(folder, data['name'], '1.0.1', 'https://registry.npmjs.org/', 'latest', 'public', archive, '', (('index.js', 100),), 100)
        self.window.prepared_result(p)
        self.window.update_controls()
        self.assertTrue(self.window.publish_button.isEnabled())
        self.assertEqual(self.window.files.rowCount(), 1)
        self.window.tag.setText('beta')
        self.assertIsNone(self.window.prepared)
        self.assertFalse(self.window.publish_button.isEnabled())
        self.assertFalse(archive.exists())

    def test_background_job_completes(self):
        results = []
        self.window.start('Testing', lambda service: {'result': 42}, results.append)
        deadline = time.monotonic() + 5
        while self.window.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(self.window.busy)
        self.assertEqual(results, [{'result': 42}])

    def test_staged_result_is_not_published(self):
        self.window.details['npm version'].setText('1.0.0')
        self.window.published(dict(status='staged', package='example', version='1.0.1',
                                   review_command='npm stage list example', approval_help='Approve with 2FA'))
        self.assertEqual(self.window.dashboard_badge.text(), 'STAGED')
        self.assertEqual(self.window.details['npm version'].text(), '1.0.0')
        self.assertTrue(self.window.open_package.isHidden())
        self.assertIn('not live', self.window.success.text())
