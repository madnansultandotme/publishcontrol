"""Registry-scoped credentials; persistent secrets live only in the OS keyring."""
from urllib.parse import urlsplit
from app.core import validate_options

SERVICE_NAME = 'PublishControl'
LEGACY_SERVICE_NAME = 'publishconsole'


def registry_key(registry):
    validate_options(registry, 'latest', 'public')
    u = urlsplit(registry)
    if u.scheme != 'https':
        raise ValueError('Saved tokens require an HTTPS registry.')
    return f'https://{u.netloc.lower()}{u.path.rstrip("/")}/'


class Credentials:
    def __init__(self):
        self.session = {}

    def backend(self):
        import keyring
        backend = keyring.get_keyring()
        allowed = ('keyring.backends.SecretService', 'keyring.backends.kwallet',
                   'keyring.backends.macOS', 'keyring.backends.Windows')
        if type(backend).__module__ not in allowed:
            raise RuntimeError('Secure credential storage is unavailable. Unlock your desktop keyring (GNOME Keyring/KWallet), or uncheck Remember to use the token for this session only.')
        return backend

    def save(self, registry, token, remember):
        key = registry_key(registry)
        token = token.strip()
        if not token or any(c.isspace() for c in token):
            raise ValueError('Enter a token without whitespace.')
        if remember:
            self.backend().set_password(SERVICE_NAME, key, token)
        self.session[key] = token

    def get(self, registry, remembered=False):
        key = registry_key(registry)
        if key in self.session:
            return self.session[key]
        if remembered:
            backend = self.backend()
            return (backend.get_password(SERVICE_NAME, key) or
                    backend.get_password(LEGACY_SERVICE_NAME, key))
        return None

    def remove(self, registry, remembered=False):
        key = registry_key(registry)
        if remembered:
            backend = self.backend()
            for service in (SERVICE_NAME, LEGACY_SERVICE_NAME):
                if backend.get_password(service, key) is not None:
                    backend.delete_password(service, key)
        self.session.pop(key, None)


def token_environment(registry, token):
    u = urlsplit(registry_key(registry))
    return {f'npm_config_//{u.netloc}{u.path}:_authToken': token}
