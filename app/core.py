from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import tarfile
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

SEMVER = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$')
NAME = re.compile(r'^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$')


def bump(version, kind):
    m = SEMVER.fullmatch(version)
    if not m:
        raise ValueError('Invalid semantic version.')
    a, b, c = map(int, m.group(1, 2, 3))
    if kind == 'Major':
        return f'{a if m[4] and b == c == 0 else a + 1}.0.0'
    if kind == 'Minor':
        return f'{a}.{b if m[4] and c == 0 else b + 1}.0'
    return f'{a}.{b}.{c if m[4] else c + 1}'


def read_package(folder):
    data = json.loads((Path(folder) / 'package.json').read_text())
    if not isinstance(data, dict):
        raise ValueError('package.json must contain an object.')
    name, version = data.get('name', ''), data.get('version', '')
    if not isinstance(name, str) or not NAME.fullmatch(name) or len(name) > 214 or name in ('node_modules', 'favicon.ico'):
        raise ValueError('Invalid npm package name.')
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise ValueError('Invalid semantic version in package.json.')
    for field in ('scripts', 'publishConfig'):
        if not isinstance(data.get(field, {}), dict):
            raise ValueError(f'{field} must be an object.')
    return data


def validate_options(registry, tag, access):
    u = urlsplit(registry)
    if u.scheme not in ('https', 'http') or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError('Registry must be an HTTP(S) URL without credentials, query, or fragment.')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9._-]*', tag) or tag.lower().startswith('v') and tag[1:2].isdigit():
        raise ValueError('Use a tag such as latest, beta, or next.')
    if access not in ('public', 'restricted'):
        raise ValueError('Invalid access type.')


def redact(text):
    text = re.sub(r'npm_[A-Za-z0-9]+', '[REDACTED]', text)
    text = re.sub(r'(?i)((?:_authToken|_auth|authorization|password)\s*[=:]\s*)\S+', r'\1[REDACTED]', text)
    return re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', text)


class Runner:
    def __init__(self, log=lambda line: None, auth_env=None):
        self.auth_env = auth_env or {}
        self.log = lambda line: log(self.sanitize(line))
        self.cancelled = threading.Event()
        self.process = None

    def sanitize(self, text):
        for secret in self.auth_env.values():
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return redact(text)

    def cancel(self):
        self.cancelled.set()
        self.kill()

    def kill(self):
        p = self.process
        if p and p.poll() is None:
            try:
                if os.name == 'posix':
                    os.killpg(p.pid, signal.SIGKILL)
                else:
                    p.kill()
            except ProcessLookupError:
                pass

    def run(self, args, cwd, check=True, timeout=600, env_extra=None):
        if self.cancelled.is_set():
            raise RuntimeError('Operation cancelled.')
        self.log('> ' + redact(shlex.join(args)))
        process_args = args
        if os.name == 'nt' and (Path(args[0]).stem.lower() == 'npm' or
                    Path(args[0]).suffix.lower() in ('.cmd', '.bat')):
            command_args = ['npm', *args[1:]] if Path(args[0]).stem.lower() == 'npm' else args
            process_args = [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/s', '/c',
                    subprocess.list2cmdline(command_args)]
        env = dict(os.environ, NO_COLOR='1', CI='true', npm_config_color='false', npm_config_progress='false')
        env.update(self.auth_env)
        env.update(env_extra or {})
        expired = threading.Event()
        with subprocess.Popen(process_args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              errors='replace', start_new_session=os.name == 'posix') as p:
            self.process = p
            if self.cancelled.is_set():
                self.kill()
            def expire():
                expired.set()
                self.kill()
            timer = threading.Timer(timeout, expire)
            timer.start()
            lines = []
            try:
                for line in p.stdout:
                    lines.append(line)
                    self.log(redact(line.rstrip()))
                code = p.wait()
            finally:
                timer.cancel()
                self.process = None
        output = ''.join(lines)
        if self.cancelled.is_set():
            raise RuntimeError('Cancelled. Local preparation changes may remain.')
        if expired.is_set():
            raise RuntimeError('Command timed out. Verify registry state before retrying a publish.')
        if check and code:
            raise RuntimeError(self.sanitize(f'{args[1]} failed (exit {code}).\n{output[-4000:]}'))
        return code, output


class Store:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get('PUBLISHCONTROL_DATA_DIR') or
                         os.environ.get('PUBLISHCONSOLE_DATA_DIR') or Path.home() / '.publishcontrol')
        self.root.mkdir(parents=True, exist_ok=True)

    def read(self, name, default):
        path = self.root / f'{name}.json'
        return json.loads(path.read_text()) if path.exists() else default

    def write(self, name, data):
        fd, path = tempfile.mkstemp(dir=self.root, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f, indent=2)
            os.replace(path, self.root / f'{name}.json')
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def record(self, entry):
        entries = self.read('history', [])
        entries.insert(0, entry)
        self.write('history', entries)


@dataclass(frozen=True)
class Prepared:
    folder: Path
    name: str
    version: str
    registry: str
    tag: str
    access: str
    archive: Path
    digest: str
    files: tuple
    size: int
    mode: str = "direct"


class ReleaseService:
    def __init__(self, store, runner):
        self.store, self.runner = store, runner
        self.npm = shutil.which('npm')
        if not self.npm:
            raise ValueError('npm not found on PATH. Launch from your npm-enabled terminal.')
        if os.name == 'nt':
            self.npm = 'npm'

    def run(self, args, folder, **kwargs):
        args = list(args)
        # npm scoped registry settings can otherwise override --registry.
        if args[0] == 'view' and args[1].startswith('@') and '--registry' in args:
            registry = args[args.index('--registry') + 1]
            args.append(f'--{args[1].split("/")[0]}:registry={registry}')
        return self.runner.run([self.npm, *args], folder, **kwargs)

    def inspect(self, folder, registry):
        data = read_package(folder)
        validate_options(registry, 'latest', 'public')
        code, user = self.run(['whoami', '--registry', registry], folder, check=False, timeout=45)
        vcode, version = self.run(['view', data['name'], 'version', '--json', '--registry', registry], folder, check=False, timeout=45)
        try:
            latest = json.loads(version) if vcode == 0 else ('Not published / inaccessible' if 'E404' in version else 'Lookup failed')
        except ValueError:
            latest = 'Unknown'
        branch, dirty = 'Not a Git repository', False
        if shutil.which('git'):
            gcode, value = self.runner.run(['git', 'branch', '--show-current'], folder, check=False, timeout=10)
            if gcode == 0:
                branch = value.strip() or 'Detached HEAD'
                _, status = self.runner.run(['git', 'status', '--porcelain'], folder, check=False, timeout=10)
                dirty = bool(status.strip())
        names = {p.name.lower() for p in Path(folder).iterdir()}
        warnings = ['Uncommitted Git changes detected'] if dirty else []
        for label, found in [('README', any(n.startswith('readme') for n in names)), ('LICENSE', any(n.startswith(('license', 'licence')) for n in names)), ('Repository', bool(data.get('repository')))]:
            if not found:
                warnings.append(f'{label} is missing')
        if data.get('private'):
            warnings.append('private: true blocks publishing')
        return dict(data=data, user=user.strip() if code == 0 else 'Not authenticated / unavailable', authenticated=code == 0,
                    latest=latest, branch=branch, warnings=warnings)

    def prepare(self, folder, version, registry, tag, access, mode="direct"):
        if mode not in ("direct", "stage"):
            raise ValueError("Invalid release mode.")
        folder = Path(folder).resolve()
        data = read_package(folder)
        validate_options(registry, tag, access)
        if not SEMVER.fullmatch(version):
            raise ValueError('Enter a valid semantic version.')
        if data.get('private'):
            raise ValueError('private: true blocks publishing.')
        if access == 'restricted' and not data['name'].startswith('@'):
            raise ValueError('Restricted access requires a scoped package.')
        config = data.get('publishConfig', {})
        for key, value in [('registry', registry), ('access', access), ('tag', tag)]:
            if key in config and str(config[key]).rstrip('/') != value.rstrip('/'):
                raise ValueError(f'publishConfig.{key} conflicts with your selection.')
        if config.get('directory') or data.get('workspaces'):
            raise ValueError('Select an individual package without workspaces or publishConfig.directory.')
        self.run(['whoami', '--registry', registry], folder, timeout=45)
        if mode == 'stage':
            supported, help_text = self.run(['stage', '--help'], folder, check=False, timeout=15)
            if supported or 'stage publish' not in help_text:
                raise ValueError('This npm CLI does not support staging. Install an npm version with npm stage support.')
            exists, result = self.run(['view', data['name'], 'name', '--json', '--registry', registry], folder, check=False, timeout=45)
            if exists:
                if 'E404' in result:
                    raise ValueError('Staging requires an existing package. This package was not found or is inaccessible. For its first release, use an authorized direct publish (interactive 2FA or a direct-capable token), then stage future versions.')
                raise ValueError('Could not verify the package exists for staging. Resolve the registry error first.')
        code, out = self.run(['view', f"{data['name']}@{version}", 'version', '--json', '--registry', registry], folder, check=False, timeout=45)
        if code == 0:
            raise ValueError(f'{data["name"]}@{version} already exists.')
        if 'E404' not in out:
            raise ValueError('Could not check version availability. Resolve the registry error first.')
        if version != data['version']:
            self.run(['version', version, '--no-git-tag-version', '--ignore-scripts'], folder)
        self.run(['install', '--no-audit', '--no-fund'], folder)
        scripts = read_package(folder).get('scripts', {})
        for script in ('test', 'build', 'prepublishOnly'):
            if script in scripts:
                self.run(['run', script], folder)
            else:
                self.runner.log(f'– No {script} script; skipped')
        self.run(['pack', '--dry-run', '--json', '--ignore-scripts'], folder)
        staging = Path(tempfile.mkdtemp(prefix='release-', dir=self.store.root))
        try:
            self.run(['pack', '--pack-destination', str(staging)], folder)
            archives = list(staging.glob('*.tgz'))
            if len(archives) != 1:
                raise ValueError('npm pack did not produce exactly one archive.')
            archive = archives[0]
            with tarfile.open(archive, 'r:gz') as tar:
                metadata = json.load(tar.extractfile('package/package.json'))
                files = tuple((m.name.removeprefix('package/'), m.size) for m in tar.getmembers() if m.isfile())
            if metadata['name'] != data['name'] or metadata['version'] != version:
                raise ValueError('A script changed the package name or version.')
            if metadata.get('publishConfig', {}) != config or metadata.get('private'):
                raise ValueError('A script changed publish configuration.')
            self.runner.log('✓ Checks passed. Review package contents before publishing.')
            return Prepared(folder, data['name'], version, registry, tag, access, archive,
                            hashlib.sha256(archive.read_bytes()).hexdigest(), files, archive.stat().st_size, mode)
        except Exception:
            shutil.rmtree(staging)
            raise

    def command(self, p):
        if p.mode not in ('direct', 'stage'):
            raise ValueError('Invalid release mode.')
        args = [self.npm, *(['stage', 'publish'] if p.mode == 'stage' else ['publish']), str(p.archive), '--registry', p.registry, '--tag', p.tag, '--access', p.access, '--ignore-scripts']
        if p.name.startswith('@'):
            args.append(f'--{p.name.split("/")[0]}:registry={p.registry}')
        return args

    def publish(self, p, otp=''):
        if hashlib.sha256(p.archive.read_bytes()).hexdigest() != p.digest:
            raise ValueError('Archive changed. Prepare the release again.')
        entry = dict(package=p.name, version=p.version, registry=p.registry, tag=p.tag, access=p.access,
                     timestamp=datetime.now(timezone.utc).isoformat(), command=shlex.join(self.command(p)), status='staging' if p.mode == 'stage' else 'publishing', mode=p.mode)
        self.store.record(entry)
        try:
            _, output = self.runner.run(self.command(p), p.folder, env_extra={'npm_config_otp': otp} if otp else {}, timeout=300)
        except Exception:
            entry['status'] = 'failed / verify registry'
            self.finish_record(entry)
            raise
        entry['status'] = 'staged' if p.mode == 'stage' else 'success'
        if p.mode == 'stage':
            entry['stage_output'] = redact(output)
            entry['review_command'] = shlex.join([self.npm, 'stage', 'list', p.name, '--registry', p.registry])
            entry['approval_help'] = 'Review the stage ID, then run npm stage approve <stage-id> --registry ' + p.registry + ' in an interactive terminal with maintainer 2FA.'
        try:
            self.finish_record(entry)
        except Exception as exc:
            self.runner.log(f'Upload succeeded, but history could not be updated: {exc}')
        return entry

    def finish_record(self, entry):
        entries = self.store.read('history', [])
        for i, old in enumerate(entries):
            if old.get('timestamp') == entry['timestamp']:
                entries[i] = entry
                break
        self.store.write('history', entries)
