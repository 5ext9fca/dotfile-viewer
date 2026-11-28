"""Declared local configuration browsing and optimistic editing."""
from pathlib import Path
import os
import stat
import tempfile
from .catalog import load_catalog, catalog_root
from .errors import CoreError
from .files import _read_observed, new_session, publish_file, protected_state, state_home, ConflictError
from .host import host, host_roots
from .lease import deployment_lease
from .paths import is_excluded, no_links
from .software import relative

MAX_TEXT = 2 * 1024 * 1024


class Editor:
    def __init__(self, catalog=None, *, platform=None, roots=None, backup=None):
        self.catalog = catalog
        self.platform = platform or host()
        self.roots = roots if roots is not None else host_roots()
        self.backup = Path(backup or os.environ.get('DOTFILES_BACKUP_DIR') or state_home() / 'backups').resolve()

    def _configs(self, data):
        return (config for config in data.get('config', [])
                if self.platform.branch in config['platforms']
                and self.platform.environment in config.get('environments', ['native', 'wsl']))

    def _root(self, config):
        value = self.roots.get(config['root'])
        if not value or not Path(value).is_absolute():
            raise CoreError('Missing absolute configuration root')
        return Path(value).resolve()

    def _describe(self, config):
        record = dict(config)
        try:
            root = self._root(config)
            path = root / config['path']
            record['resolved_path'] = str(path)
            info = no_links(path, root)
            if info is None:
                record['status'] = 'missing'
                return record
            record['status'] = 'directory' if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else 'unsupported'
        except FileNotFoundError:
            record['status'] = 'missing'
        except (CoreError, OSError) as exc:
            record.update(status='unavailable', error=str(exc))
        return record

    def configurations(self):
        result = []
        for identity, data in load_catalog(self.catalog).items():
            described = [self._describe(config) for config in self._configs(data)]
            if described or self.platform.branch in data.get('install', {}):
                result.append(dict(software=identity, configs=described))
        return result

    def _config(self, catalog, software, config):
        for item in self._configs(catalog.get(software, {})):
            if item['id'] == config:
                return item
        raise CoreError('Unknown or unavailable configuration')

    def _target(self, item, file):
        if file and not relative(file):
            raise CoreError('Invalid relative filename')
        if is_excluded(file, item.get('exclude', [])):
            raise CoreError('File excluded by declaration')
        root = self._root(item)
        base = root / item['path']
        target = base / file if file else base
        no_links(target, root)
        catalog = catalog_root(self.catalog)
        for protected in (Path(__file__).resolve().parent, catalog / 'software', catalog / 'workflow'):
            if target == protected or target.is_relative_to(protected):
                raise CoreError('Managed file overlaps Viewer code or catalog')
        if file and not base.is_dir():
            raise CoreError('A single-file declaration does not accept a filename')
        return target

    def locate(self, software, config, file=''):
        item = self._config(load_catalog(self.catalog), software, config)
        return self._target(item, file), self._describe(item)

    def files(self, software, config):
        base, item = self.locate(software, config)
        result = []
        exclusions = set(item.get('exclude', ()))
        def visit(path, rel):
            if is_excluded(rel, exclusions):
                return
            try:
                info = no_links(path, base.parent)
                if info is None:
                    kind = 'missing'
                elif stat.S_ISDIR(info.st_mode):
                    kind = 'directory'
                elif stat.S_ISREG(info.st_mode):
                    kind = 'file'
                else:
                    kind = 'unsupported'
                result.append(dict(file=rel, path=str(path), kind=kind))
                if kind == 'directory':
                    for child in sorted(path.iterdir()):
                        visit(child, f'{rel}/{child.name}' if rel else child.name)
            except (CoreError, OSError) as exc:
                result.append(dict(file=rel, path=str(path), kind='unavailable', error=str(exc)))
        visit(base, '')
        return result

    def read(self, software, config, file=''):
        item = self._config(load_catalog(self.catalog), software, config)
        return self._read_file(self._target(item, file))

    @staticmethod
    def _read_file(path):
        before, data = _read_observed(path, MAX_TEXT)
        result = dict(before, path=str(path), editable=False)
        if data is None:
            return dict(result, reason='Not a text file or exceeds 2 MiB')
        try:
            text = data.decode('utf-8-sig')
        except UnicodeError:
            return dict(result, reason='Not UTF-8')
        crlf = '\r\n' in text
        rest = text.replace('\r\n', '')
        if '\x00' in text or '\r' in rest or (crlf and '\n' in rest):
            return dict(result, reason='Binary or mixed newline content')
        return dict(result, editable=True, content=text.replace('\r\n', '\n'), bom=data.startswith(b'\xef\xbb\xbf'), newline='crlf' if crlf else 'lf')

    def save(self, software, config, file, content, expected_version, *, detach=False):
        if not isinstance(content, str) or not expected_version:
            raise CoreError('Content and expected version are required')
        with deployment_lease():
            catalog = load_catalog(self.catalog)
            item = self._config(catalog, software, config)
            path = self._target(item, file)
            current = self._read_file(path)
            if current['version'] != expected_version:
                raise ConflictError('File changed; reload and merge your draft')
            if not current['editable']:
                raise CoreError(current['reason'])
            content = content.replace('\r\n', '\n')
            if '\r' in content or '\x00' in content:
                raise CoreError('Invalid text content')
            if content == current['content']:
                return dict(status='unchanged', version=current['version'])
            data = content.replace('\n', '\r\n' if current['newline'] == 'crlf' else '\n').encode('utf-8')
            if current['bom']:
                data = b'\xef\xbb\xbf' + data
            if len(data) > MAX_TEXT:
                raise CoreError('Text exceeds 2 MiB')
            if self._target(item, file) != path:
                raise ConflictError('Configuration path changed during save')
            managed = (Path(self.roots[c['root']]).resolve() / c['path']
                       for data in catalog.values() for c in self._configs(data) if self.roots.get(c['root']))
            protected_state(self.backup, managed)
            session = new_session(self.backup)
            with tempfile.TemporaryDirectory(prefix='viewer-edit-') as tmp:
                source = Path(tmp) / 'content'
                source.write_bytes(data)
                return publish_file(path, source, expected_version, session, detach=detach)
