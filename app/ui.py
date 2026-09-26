from pathlib import Path
import sys
import shlex
import shutil
from urllib.parse import quote, urlsplit

from PySide6.QtCore import QThread, Signal, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)
from app.credentials import Credentials, registry_key, token_environment
from app.core import Store, Runner, ReleaseService, Prepared, bump, read_package, validate_options

STYLE = '''
QWidget { background: #0d1117; color: #f0f6fc; font-family: "DejaVu Sans", "Segoe UI", sans-serif; font-size: 13px; }
QMainWindow { background: #0d1117; }
QFrame#sidebar QLabel { background: transparent; }
QFrame#sidebar { background: #11161e; border-right: 1px solid #30363d; }
QFrame#card { background: #161b22; border: 1px solid #30363d; border-radius: 12px; }
QFrame#card QLabel { background: transparent; border: none; }
QLabel#title { font-size: 28px; font-weight: 700; }
QLabel#section { font-size: 17px; font-weight: 600; }
QLabel#muted { color: #8b949e; }
QLabel#badge { color: #79c0ff; background: #16283f; border-radius: 6px; padding: 6px 12px; }
QLabel#warning { color: #d29922; }
QPushButton { background: #21262d; border: 1px solid #30363d; border-radius: 7px; padding: 10px 16px; font-weight: 600; }
QPushButton:hover { background: #30363d; border-color: #8b949e; }
QPushButton:disabled { color: #58616d; background: #161b22; }
QPushButton#primary { background: #2f81f7; border-color: #2f81f7; color: white; }
QPushButton#primary:hover { background: #4393ff; }
QPushButton#primary:disabled { background: #1b3456; border-color: #1b3456; color: #74869e; }
QLineEdit, QComboBox { background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 9px; min-height: 20px; }
QLineEdit:focus, QComboBox:focus { border-color: #2f81f7; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { padding: 13px 16px; margin: 3px 10px; border-radius: 7px; color: #8b949e; }
QListWidget::item:selected { background: #1c2c43; color: #79c0ff; }
QPlainTextEdit { background: #090d12; border: 1px solid #30363d; border-radius: 8px; padding: 10px; font-family: "DejaVu Sans Mono", monospace; font-size: 12px; }
QTableWidget { background: #161b22; alternate-background-color: #11161e; border: 1px solid #30363d; border-radius: 8px; gridline-color: #21262d; }
QHeaderView::section { background: #1b222c; color: #8b949e; padding: 10px; border: none; }
QTableWidget::item { padding: 8px; }
QScrollArea { border: none; }
QProgressBar { border: none; background: #21262d; border-radius: 3px; max-height: 5px; }
QProgressBar::chunk { background: #2f81f7; }
QCheckBox { spacing: 9px; }
'''

BRAND_NAME = 'PublishControl'
LOGO_PATH = Path(__file__).parent.parent / 'assets' / 'logo.svg'


def label(text, kind=None):
    w = QLabel(text)
    w.setTextFormat(Qt.PlainText)
    w.setWordWrap(True)
    if kind:
        w.setObjectName(kind)
    return w


def button(text, action, primary=False):
    b = QPushButton(text)
    if primary:
        b.setObjectName('primary')
    b.clicked.connect(action)
    return b


def card():
    frame = QFrame()
    frame.setObjectName('card')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(24, 22, 24, 22)
    layout.setSpacing(16)
    return frame, layout


def table(headers):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    t.verticalHeader().hide()
    t.verticalHeader().setDefaultSectionSize(46)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setAlternatingRowColors(True)
    return t


class Job(QThread):
    log = Signal(str)
    result = Signal(object)
    error = Signal(str)

    def __init__(self, store, operation, auth_env=None):
        super().__init__()
        self.store, self.operation = store, operation
        self.runner = Runner(self.log.emit, auth_env)

    def run(self):
        try:
            self.result.emit(self.operation(ReleaseService(self.store, self.runner)))
        except Exception as exc:
            self.error.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self, store=None):
        super().__init__()
        self.store = store or Store()
        self.settings = self.store.read('settings', {})
        self.credentials = Credentials()
        self.folder = None
        self.data = None
        self.prepared = None
        self.job = None
        self.busy = False
        self.operation = ''
        self.setWindowTitle(f'{BRAND_NAME} — npm release desk')
        self.setWindowIcon(QIcon(str(LOGO_PATH.with_suffix('.ico' if sys.platform == 'win32' else '.svg'))))
        self.resize(1180, 870)
        self.setMinimumSize(900, 700)
        self.apply_text_size(self.settings.get('font_size', 16))
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(260)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(10, 30, 10, 20)
        brand_row = QHBoxLayout()
        brand_icon = QLabel()
        brand_icon.setPixmap(QPixmap(str(LOGO_PATH)).scaled(34, 34, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        brand_icon.setFixedSize(38, 38)
        brand_row.addWidget(brand_icon)
        brand_row.addWidget(label(BRAND_NAME, 'section'))
        brand_row.addStretch()
        side.addLayout(brand_row)
        side.addWidget(label('      YOUR NPM RELEASE DESK', 'muted'))
        side.addSpacing(30)
        self.nav = QListWidget()
        self.nav.addItems(['◫   Dashboard', '▣   Package', '↑   Publish', '◷   History', '⚙   Settings'])
        side.addWidget(self.nav)
        side.addWidget(label('LOCAL WORKSPACE', 'muted'))
        self.sidebar_package = label('No package selected', 'muted')
        side.addWidget(self.sidebar_package)
        side.addSpacing(18)
        side.addWidget(label('v1.0  •  Powered by npm', 'muted'))
        outer.addWidget(sidebar)
        content = QVBoxLayout()
        content.setContentsMargins(32, 26, 32, 22)
        content.setSpacing(14)
        self.status = label('Ready · Select a package to get started', 'badge')
        content.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        content.addWidget(self.progress)
        self.pages = QStackedWidget()
        content.addWidget(self.pages, 1)
        outer.addLayout(content, 1)
        self.make_dashboard()
        self.make_package()
        self.make_publish()
        self.make_history()
        self.make_settings()
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self.refresh_history()
        self.update_controls()

    def apply_text_size(self, size):
        size = int(size) if str(size) in ('16', '18', '20') else 16
        style = STYLE.replace('font-size: 13px', f'font-size: {size}px')
        style = style.replace('font-size: 12px', f'font-size: {size}px')
        style = style.replace('font-size: 17px', f'font-size: {size + 4}px')
        style = style.replace('font-size: 28px', f'font-size: {size + 14}px')
        self.setStyleSheet(style)
        for grid in self.findChildren(QTableWidget):
            grid.verticalHeader().setDefaultSectionSize(size * 2 + 14)

    def page(self, title, subtitle):
        area = QScrollArea()
        area.setWidgetResizable(True)
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 4, 4, 8)
        layout.setSpacing(20)
        layout.addWidget(label(title, 'title'))
        layout.addWidget(label(subtitle, 'muted'))
        area.setWidget(widget)
        self.pages.addWidget(area)
        return layout

    def make_dashboard(self):
        layout = self.page('A clear path to your next release.', 'Inspect. Prepare. Review. Publish. All from one place.')
        frame, body = card()
        row = QHBoxLayout()
        self.package_title = label('Your next release starts here', 'title')
        row.addWidget(self.package_title, 1)
        self.dashboard_badge = label('NO PACKAGE', 'badge')
        row.addWidget(self.dashboard_badge)
        body.addLayout(row)
        self.description = label('Choose a local npm package to see its details and prepare a release.', 'muted')
        body.addWidget(self.description)
        self.details = {}
        form = QFormLayout()
        form.setVerticalSpacing(20)
        for key in ['Local version', 'npm version', 'Registry', 'Access', 'Git branch', 'Authentication', 'Package folder']:
            self.details[key] = label('—')
            self.details[key].setTextInteractionFlags(Qt.TextSelectableByMouse)
            form.addRow(label(key, 'muted'), self.details[key])
        body.addLayout(form)
        self.dash_prepare = button('Prepare release  →', lambda: self.nav.setCurrentRow(2), True)
        body.addWidget(self.dash_prepare)
        layout.addWidget(frame)
        frame, body = card()
        body.addWidget(label('A deliberate release workflow', 'section'))
        body.addWidget(label('01  Select package     →     02  Run preflight     →     03  Review & publish', 'muted'))
        body.addWidget(label('Use your existing npm login. Review the exact package archive before it leaves your machine.'))
        self.open_dashboard = button('＋  Choose package folder', self.browse)
        body.addWidget(self.open_dashboard)
        layout.addWidget(frame)
        layout.addStretch()

    def make_package(self):
        layout = self.page('Package', 'Connect a local project. Package information is read directly from package.json.')
        frame, body = card()
        body.addWidget(label('Package directory', 'section'))
        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        self.path.setPlaceholderText('/path/to/your/npm-package')
        self.browse_button = button('Browse…', self.browse)
        row.addWidget(self.path, 1)
        row.addWidget(self.browse_button)
        body.addLayout(row)
        self.package_info = label('No package selected.', 'muted')
        self.package_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.addWidget(self.package_info)
        self.refresh_button = button('Refresh package & npm status', self.inspect)
        body.addWidget(self.refresh_button)
        layout.addWidget(frame)
        frame, body = card()
        body.addWidget(label('Automatic checks', 'section'))
        self.checks = label('Choose a package to run checks.')
        self.warnings = label('', 'warning')
        body.addWidget(self.checks)
        body.addWidget(self.warnings)
        layout.addWidget(frame)
        layout.addStretch()

    def make_publish(self):
        layout = self.page('Prepare a release', 'Build confidence before you publish. Every command runs in the background.')
        self.release_options, body = card()
        form = QFormLayout()
        self.current = label('Select a package first')
        form.addRow('Current version', self.current)
        self.release_mode = QComboBox()
        self.release_mode.addItems(['Direct publish', 'Stage for approval'])
        form.addRow('Release mode', self.release_mode)
        self.bump_kind = QComboBox()
        self.bump_kind.addItems(['Patch', 'Minor', 'Major', 'Custom'])
        self.version = QLineEdit()
        self.version.setPlaceholderText('1.2.5-beta.1')
        version_row = QHBoxLayout()
        version_row.addWidget(self.bump_kind)
        version_row.addWidget(self.version, 1)
        form.addRow('Next version', version_row)
        self.registry = QLineEdit(self.settings.get('registry', 'https://registry.npmjs.org/'))
        self.tag = QLineEdit(self.settings.get('tag', 'latest'))
        self.access = QComboBox()
        self.access.addItems(['public', 'restricted'])
        self.access.setCurrentText(self.settings.get('access', 'public'))
        form.addRow('Registry', self.registry)
        form.addRow('Distribution tag', self.tag)
        form.addRow('Access', self.access)
        body.addLayout(form)
        self.consent = QCheckBox('Run package scripts and update the version / lockfile in this folder')
        body.addWidget(self.consent)
        body.addWidget(label('Preflight runs npm install, available test/build scripts, prepublishOnly, and npm pack. Local changes remain if cancelled or unsuccessful.', 'muted'))
        layout.addWidget(self.release_options)
        row = QHBoxLayout()
        self.prepare_button = button('Run preflight checks', self.prepare, True)
        self.publish_button = button('Publish package…', self.confirm_publish, True)
        self.cancel_button = button('Cancel checks', self.cancel)
        row.addWidget(self.prepare_button)
        row.addWidget(self.publish_button)
        row.addStretch()
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        self.preview_title = label('Package contents · run preflight to generate a preview', 'section')
        layout.addWidget(self.preview_title)
        self.files = table(['File in archive', 'Size'])
        self.files.setMinimumHeight(180)
        layout.addWidget(self.files)
        layout.addWidget(label('Release console', 'section'))
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMinimumHeight(200)
        self.logs.document().setMaximumBlockCount(12000)
        layout.addWidget(self.logs)
        self.success = label('', 'badge')
        self.success.hide()
        layout.addWidget(self.success)
        self.open_package = button('Open npm package ↗', self.open_published)
        self.open_package.hide()
        layout.addWidget(self.open_package)
        self.release_mode.currentIndexChanged.connect(self.invalidate)
        self.bump_kind.currentTextChanged.connect(self.choose_version)
        for field in (self.registry, self.tag, self.version):
            field.textChanged.connect(self.invalidate)
        self.access.currentTextChanged.connect(self.invalidate)
        self.consent.toggled.connect(self.update_controls)

    def make_history(self):
        layout = self.page('Release history', 'A local record of publish attempts from this machine. Timestamps are UTC.')
        self.history = table(['Package', 'Version', 'Status', 'Time (UTC)', 'Registry'])
        layout.addWidget(self.history, 1)
        self.history_note = label('No releases yet. Your first publish will appear here.', 'muted')
        layout.addWidget(self.history_note)
        self.history_detail = QPlainTextEdit()
        self.history_detail.setReadOnly(True)
        self.history_detail.setMaximumHeight(140)
        layout.addWidget(self.history_detail)
        self.history.itemSelectionChanged.connect(self.show_history)

    def make_settings(self):
        layout = self.page('Settings', 'Defaults for new packages. Authentication stays with npm.')
        frame, body = card()
        body.addWidget(label('Appearance', 'section'))
        self.text_size = QComboBox()
        self.text_size.addItems(['16', '18', '20'])
        self.text_size.setCurrentText(str(self.settings.get('font_size', 16)))
        body.addWidget(label('Text size · applies immediately and is saved automatically', 'muted'))
        body.addWidget(self.text_size)
        self.text_size.currentTextChanged.connect(self.save_text_size)
        body.addWidget(label('Release defaults', 'section'))
        form = QFormLayout()
        self.default_registry = QLineEdit(self.registry.text())
        self.default_tag = QLineEdit(self.tag.text())
        self.default_access = QComboBox()
        self.default_access.addItems(['public', 'restricted'])
        self.default_access.setCurrentText(self.access.currentText())
        form.addRow('Registry', self.default_registry)
        form.addRow('Tag', self.default_tag)
        form.addRow('Access', self.default_access)
        body.addLayout(form)
        body.addWidget(button('Save defaults', self.save_settings, True))
        layout.addWidget(frame)
        frame, body = card()
        body.addWidget(label('Authentication & storage', 'section'))
        body.addWidget(label('Enter a granular npm token here. Remembered tokens are saved in your operating system’s credential store, never in settings or history.', 'muted'))
        self.token_registry = QLineEdit(self.default_registry.text())
        self.token_input = QLineEdit()
        self.token_input.setEchoMode(QLineEdit.Password)
        self.token_input.setPlaceholderText('Paste your npm granular access token')
        auth_form = QFormLayout()
        auth_form.addRow('Token registry', self.token_registry)
        auth_form.addRow('Access token', self.token_input)
        body.addLayout(auth_form)
        self.remember_token = QCheckBox('Remember securely for future releases')
        self.remember_token.setChecked(True)
        body.addWidget(self.remember_token)
        row = QHBoxLayout()
        self.save_token_button = button('Save / use token', self.save_token, True)
        self.remove_token_button = button('Remove token', self.remove_token)
        row.addWidget(self.save_token_button)
        row.addWidget(self.remove_token_button)
        body.addLayout(row)
        self.token_status = label('', 'muted')
        body.addWidget(self.token_status)
        self.token_registry.textChanged.connect(self.update_token_status)
        self.update_token_status()
        body.addWidget(label('Direct publishing currently needs package write permission and bypass 2FA, where permitted. Saving a token does not verify its permissions. Without an app token, existing npm authentication is used.', 'muted'))
        body.addWidget(label(f'Local settings, archives, and history:\n{self.store.root}', 'muted'))
        body.addWidget(label('Only open package folders you trust: preparation executes their npm scripts.', 'muted'))
        layout.addWidget(frame)
        layout.addStretch()

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, 'Choose npm package', str(self.folder or Path.home()))
        if not path:
            return
        try:
            data = read_package(path)
        except Exception as exc:
            self.fail(str(exc))
            return
        self.invalidate()
        self.folder, self.data = Path(path), data
        self.path.setText(path)
        config = data.get('publishConfig', {})
        self.registry.setText(str(config.get('registry', self.settings.get('registry', 'https://registry.npmjs.org/'))))
        self.tag.setText(str(config.get('tag', self.settings.get('tag', 'latest'))))
        self.access.setCurrentText(config.get('access', self.settings.get('access', 'public')))
        self.consent.setChecked(False)
        self.render_package()
        self.inspect()

    def render_package(self):
        d = self.data
        self.package_title.setText(d['name'])
        self.sidebar_package.setText(d['name'])
        self.description.setText(str(d.get('description', 'No description provided.')))
        self.current.setText(d['version'])
        self.dashboard_badge.setText('SELECTED')
        self.details['Local version'].setText(d['version'])
        self.details['Package folder'].setText(str(self.folder))
        self.details['Registry'].setText(self.registry.text())
        self.details['Access'].setText(self.access.currentText())
        repo = d.get('repository', '—')
        if isinstance(repo, dict):
            repo = repo.get('url', '—')
        self.package_info.setText(f"Name   {d['name']}\nVersion   {d['version']}\nLicense   {d.get('license', '—')}\nEntry point   {d.get('main', '—')}\nRepository   {repo}\nScripts   {', '.join(d.get('scripts', {})) or 'None'}")
        self.choose_version()

    def choose_version(self):
        custom = self.bump_kind.currentText() == 'Custom'
        self.version.setReadOnly(not custom)
        if self.data and not custom:
            self.version.setText(bump(self.data['version'], self.bump_kind.currentText()))
        self.invalidate()

    def invalidate(self, *_):
        if self.prepared:
            shutil.rmtree(self.prepared.archive.parent, ignore_errors=True)
        self.prepared = None
        if hasattr(self, 'files'):
            self.files.setRowCount(0)
            self.preview_title.setText('Package contents · run preflight to generate a preview')
            self.success.hide()
            self.open_package.hide()
        self.update_controls()

    def update_controls(self, *_):
        if not hasattr(self, 'prepare_button'):
            return
        selected = self.folder is not None
        if hasattr(self, 'save_token_button'):
            self.save_token_button.setEnabled(not self.busy)
            self.remove_token_button.setEnabled(not self.busy)
        for w in (self.browse_button, self.open_dashboard):
            w.setEnabled(not self.busy)
        self.refresh_button.setEnabled(selected and not self.busy)
        self.dash_prepare.setEnabled(selected and not self.busy)
        self.release_options.setEnabled(selected and not self.busy)
        self.prepare_button.setEnabled(selected and not self.busy and self.consent.isChecked())
        self.publish_button.setEnabled(self.prepared is not None and not self.busy)
        self.publish_button.setText('Stage package…' if self.release_mode.currentIndex() == 1 else 'Publish package…')
        self.cancel_button.setEnabled(self.busy and self.operation not in ('Publishing', 'Staging'))
        self.progress.setVisible(self.busy)

    def start(self, name, operation, callback):
        if self.busy:
            return
        auth_env = {}
        registry = self.registry.text().strip()
        try:
            if urlsplit(registry).scheme == 'https':
                key = registry_key(registry)
                remembered = key in self.settings.get('saved_token_registries', [])
                token = self.credentials.get(registry, remembered)
                if token:
                    auth_env = token_environment(registry, token)
                elif remembered:
                    raise ValueError('Saved token is missing. Open Settings and save a replacement or remove its saved entry.')
        except Exception as exc:
            self.fail(str(exc))
            return
        self.busy, self.operation = True, name
        self.status.setText(name + '…')
        self.job = Job(self.store, operation, auth_env)
        self.job.log.connect(self.logs.appendPlainText)
        self.job.result.connect(callback)
        self.job.error.connect(self.fail)
        self.job.finished.connect(self.finished)
        self.update_controls()
        self.job.start()

    def finished(self):
        self.busy = False
        if self.folder:
            try:
                self.data = read_package(self.folder)
                self.current.setText(self.data['version'])
                self.details['Local version'].setText(self.data['version'])
            except Exception:
                pass
        if self.status.text().endswith('…'):
            self.status.setText('Ready')
        self.update_controls()
        self.refresh_history()
        self.job.deleteLater()
        self.job = None

    def inspect(self):
        if not self.folder:
            return
        self.invalidate()
        try:
            self.data = read_package(self.folder)
            self.render_package()
        except Exception as exc:
            self.fail(str(exc))
            return
        folder, registry = self.folder, self.registry.text().strip()
        self.start('Inspecting package', lambda s: s.inspect(folder, registry), self.inspected)

    def inspected(self, result):
        self.details['npm version'].setText(str(result['latest']))
        self.details['Git branch'].setText(result['branch'])
        self.details['Authentication'].setText(result['user'])
        self.checks.setText('✓ package.json found\n✓ Package name valid\n✓ Version valid\n✓ npm detected\n' + ('✓ npm account authenticated' if result['authenticated'] else '✕ npm authentication unavailable — run npm login'))
        self.warnings.setText('\n'.join('⚠ ' + w for w in result['warnings']))
        self.status.setText('Package inspected · Choose release options to continue')

    def prepare(self):
        self.invalidate()
        self.logs.clear()
        args = (self.folder, self.version.text().strip(), self.registry.text().strip(), self.tag.text().strip(), self.access.currentText(), 'stage' if self.release_mode.currentIndex() == 1 else 'direct')
        self.start('Preparing release', lambda s: s.prepare(*args), self.prepared_result)

    def prepared_result(self, result):
        self.prepared = result
        self.data = read_package(self.folder)
        self.current.setText(result.version)
        self.details['Local version'].setText(result.version)
        self.files.setRowCount(len(result.files))
        for row, (name, size) in enumerate(result.files):
            self.files.setItem(row, 0, QTableWidgetItem(name))
            self.files.setItem(row, 1, QTableWidgetItem(f'{size:,} B'))
        warning = ' · Large package' if result.size > 5 * 1024 * 1024 else ''
        self.preview_title.setText(f'{result.name}@{result.version} · {len(result.files)} files · {result.size / 1024:,.1f} KiB packed{warning}')
        self.status.setText('Ready to publish · Preflight passed; review package contents')
        self.dashboard_badge.setText('READY')

    def confirm_publish(self):
        p = self.prepared
        if not p or self.busy:
            return
        staging = p.mode == 'stage'
        title = 'Stage package for approval?' if staging else 'Publish package?'
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumWidth(610)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(18)
        layout.addWidget(label(title, 'title'))
        layout.addWidget(label(f'{p.name}@{p.version}\n\nRegistry  {p.registry}\nTag  {p.tag}\nAccess  {p.access}\nArchive  {p.size:,} bytes'))
        command = shlex.join(ReleaseService(self.store, Runner()).command(p))
        layout.addWidget(label('This will execute:', 'muted'))
        preview = QPlainTextEdit(command)
        preview.setReadOnly(True)
        preview.setMaximumHeight(110)
        layout.addWidget(preview)
        otp = QLineEdit()
        otp.setEchoMode(QLineEdit.Password)
        otp.setPlaceholderText('One-time code, if supported by your npm account')
        layout.addWidget(otp)
        otp.setVisible(not staging)
        layout.addWidget(label('npm login alone does not guarantee publish permission. Browser/security-key 2FA requires an interactive npm terminal; this app cannot complete that prompt. For noninteractive publishing, npm currently accepts an authorized granular token with bypass 2FA configured in Settings or through npm.', 'muted'))
        layout.addWidget(label('The archive will be staged, not published. A maintainer must review and approve it with 2FA.' if staging else 'The reviewed archive will be uploaded. npm versions cannot be overwritten.', 'warning'))
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        publish = buttons.addButton('Stage package' if staging else 'Publish', QDialogButtonBox.AcceptRole)
        publish.setObjectName('primary')
        publish.setAutoDefault(False)
        buttons.button(QDialogButtonBox.Cancel).setDefault(True)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            value = otp.text().strip()
            otp.clear()
            self.start('Staging' if staging else 'Publishing', lambda s: s.publish(p, value), self.published)

    def published(self, entry):
        self.published_entry = entry
        if entry['status'] == 'staged':
            self.success.setText(f'✓ Package staged — awaiting approval\n{entry["package"]}@{entry["version"]}\nThis version is not live yet.\n\n' + entry['review_command'] + '\n' + entry['approval_help'])
            self.success.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.success.show()
            self.open_package.hide()
            self.status.setText('Staged · Maintainer approval with 2FA required')
            self.dashboard_badge.setText('STAGED')
            if self.prepared:
                shutil.rmtree(self.prepared.archive.parent, ignore_errors=True)
            self.prepared = None
            return
        self.success.setText(f'✓  Package published\n{entry["package"]}@{entry["version"]}\nPublished successfully to {entry["registry"]}')
        self.success.show()
        self.open_package.setVisible(urlsplit(entry['registry']).hostname == 'registry.npmjs.org')
        self.status.setText('Published successfully')
        self.dashboard_badge.setText('PUBLISHED')
        self.details['npm version'].setText(entry['version'])
        if self.prepared:
            shutil.rmtree(self.prepared.archive.parent, ignore_errors=True)
        self.prepared = None

    def open_published(self):
        e = self.published_entry
        QDesktopServices.openUrl(QUrl(f'https://www.npmjs.com/package/{quote(e["package"], safe="@/")}/v/{quote(e["version"])}'))

    def cancel(self):
        if self.job and self.operation not in ('Publishing', 'Staging'):
            self.status.setText('Cancelling…')
            self.job.runner.cancel()

    def fail(self, message):
        self.status.setText('Action failed · See release console for details')
        self.logs.appendPlainText('✕ ' + message)
        if self.operation in ('Publishing', 'Staging') and self.prepared:
            self.invalidate()
        if 'E_STAGE_REQUIRED' in message:
            message = ('npm says this token is stage-only. If the package already exists, select Publish → Release mode → Stage for approval. '
                       'If the package does not exist yet, its first release must use an authorized direct publish; staging cannot create it.\n\n' + message)
        auth_required = 'two-factor authentication or granular access token' in message.lower() or 'EOTP' in message
        if auth_required:
            self.status.setText('Publishing blocked · npm requires additional authentication')
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle('npm publishing authentication required')
            box.setText('The package was packed, but npm rejected the upload.')
            box.setInformativeText(
                'Being logged in does not guarantee permission to publish.\n\n'
                'For browser or security-key 2FA, publish through an interactive npm terminal. '
                'This app currently cannot complete browser-based npm authentication.\n\n'
                'For this app’s noninteractive workflow, configure an npm granular token with '
                'package write permission and bypass 2FA enabled, if your package policy permits it. '
                'Enter the token in Settings → Authentication & storage, or configure it through npm.\n\n'
                'After resolving authentication, prepare the same version again and review before publishing.')
            box.setDetailedText(message)
            box.exec()
        else:
            QMessageBox.warning(self, BRAND_NAME, message)

    def refresh_history(self):
        try:
            self.entries = self.store.read('history', [])
            self.history.setRowCount(len(self.entries))
            for row, e in enumerate(self.entries):
                values = [e.get(k, '') for k in ('package', 'version', 'status', 'timestamp', 'registry')]
                for col, v in enumerate(values):
                    self.history.setItem(row, col, QTableWidgetItem(str(v)))
            self.history_note.setText(f'{len(self.entries)} publish attempt(s) · Select a row for details.' if self.entries else 'No releases yet. Your first publish will appear here.')
        except Exception as exc:
            self.history_note.setText(f'Could not read history: {exc}')

    def show_history(self):
        row = self.history.currentRow()
        if 0 <= row < len(self.entries):
            e = self.entries[row]
            self.history_detail.setPlainText('\n'.join(f'{k}: {v}' for k, v in e.items()))

    def update_token_status(self, *_):
        try:
            key = registry_key(self.token_registry.text().strip())
            saved = key in self.settings.get('saved_token_registries', [])
            active = key in self.credentials.session
            message = 'Saved securely · automatically used for this registry' if saved else ('Token active for this session' if active else 'No app token · existing npm authentication will be used')
            self.token_status.setText(message)
        except ValueError:
            self.token_status.setText('Enter an HTTPS registry URL.')

    def save_token(self):
        if self.busy:
            return
        try:
            registry = registry_key(self.token_registry.text().strip())
            saved = set(self.settings.get('saved_token_registries', []))
            token = self.token_input.text().strip()
            if not token or any(c.isspace() for c in token):
                raise ValueError('Enter a token without whitespace.')
            remember = self.remember_token.isChecked()
            if not remember and registry in saved:
                self.credentials.remove(registry, True)
                saved.discard(registry)
            self.credentials.save(registry, self.token_input.text(), remember)
            if remember:
                saved.add(registry)
            self.settings['saved_token_registries'] = sorted(saved)
            self.store.write('settings', self.settings)
            self.token_input.clear()
            self.invalidate()
            self.update_token_status()
            self.status.setText('Token saved securely' if remember else 'Token active for this session')
        except Exception as exc:
            self.fail(str(exc))

    def remove_token(self):
        if self.busy:
            return
        try:
            registry = registry_key(self.token_registry.text().strip())
            saved = set(self.settings.get('saved_token_registries', []))
            self.credentials.remove(registry, registry in saved)
            saved.discard(registry)
            self.settings['saved_token_registries'] = sorted(saved)
            self.store.write('settings', self.settings)
            self.token_input.clear()
            self.invalidate()
            self.update_token_status()
            self.status.setText('App token removed · existing npm authentication will be used')
        except Exception as exc:
            self.fail(str(exc))

    def save_text_size(self, size):
        self.apply_text_size(size)
        self.settings['font_size'] = int(size)
        try:
            self.store.write('settings', self.settings)
        except Exception as exc:
            self.fail(str(exc))

    def save_settings(self):
        try:
            registry, tag, access = self.default_registry.text().strip(), self.default_tag.text().strip(), self.default_access.currentText()
            validate_options(registry, tag, access)
            self.settings.update(registry=registry, tag=tag, access=access)
            self.store.write('settings', self.settings)
            self.status.setText('Settings saved · Defaults apply when selecting a package')
        except Exception as exc:
            self.fail(str(exc))

    def closeEvent(self, event):
        if self.busy:
            QMessageBox.information(self, f'{BRAND_NAME} — operation in progress', 'Wait for the operation to finish, or cancel preparation before closing.')
            event.ignore()
            return
        if self.prepared:
            shutil.rmtree(self.prepared.archive.parent, ignore_errors=True)
        event.accept()
