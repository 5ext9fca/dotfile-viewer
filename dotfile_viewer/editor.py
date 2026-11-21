"""Declared local configuration browsing and optimistic editing."""
from pathlib import Path
import os
import stat
import tempfile
from .catalog import load_catalog, catalog_root
from .errors import CoreError
from .files import observe, new_session, publish_file, protected_state, state_home, ConflictError
from .host import host, host_roots
from .lease import deployment_lease
from .snapshot import no_links
from .software import relative

MAX_TEXT = 2 * 1024 * 1024


class Editor:
    def __init__(self, catalog=None, *, platform=None, roots=None, backup=None):
        self.catalog = catalog
        self.platform = platform or host()
        self.roots = roots if roots is not None else host_roots()
        self.backup = Path(backup or os.environ.get('DOTFILES_BACKUP_DIR') or state_home() / 'backups').resolve()

    def configurations(self):
        result = []
        for identity, data in load_catalog(self.catalog).items():
            configs = [c for c in data.get('config', []) if self.platform.branch in c['platforms'] and self.platform.environment in c.get('environments', ['native', 'wsl'])]
            described = []
            for config in configs:
                record = dict(config)
                try:
                    value = self.roots.get(config['root'])
                    if not value or not Path(value).is_absolute():
                        raise CoreError('Missing absolute configuration root')
                    root = Path(value).resolve()
                    path = root / config['path']
                    record['resolved_path'] = str(path)
                    no_links(path, root)
                    info = path.lstat()
                    record['status'] = 'directory' if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else 'unsupported'
                except FileNotFoundError:
                    record['status'] = 'missing'
                except (CoreError, OSError) as exc:
                    record.update(status='unavailable', error=str(exc))
                described.append(record)
            if configs or self.platform.branch in data.get('install', {}):
                result.append(dict(software=identity, configs=described))
        return result

    def locate(self, software, config, file=''):
        item = next((c for s in self.configurations() if s['software'] == software for c in s['configs'] if c['id'] == config), None)
        if item is None:
            raise CoreError('Unknown or unavailable configuration')
        if file and not relative(file):
            raise CoreError('Invalid relative filename')
        if any(file == p or file.startswith(p + '/') for p in item.get('exclude', [])):
            raise CoreError('File excluded by declaration')
        value = self.roots.get(item['root'])
        if not value or not Path(value).is_absolute():
            raise CoreError('Missing absolute configuration root')
        root = Path(value).resolve()
        base = root / item['path']
        target = base / file if file else base
        no_links(target, root)
        for protected in (Path(__file__).resolve().parent, catalog_root(self.catalog) / 'software', catalog_root(self.catalog) / 'workflow'):
            if target == protected or target.is_relative_to(protected):
                raise CoreError('Managed file overlaps Viewer code or catalog')
        if file and not base.is_dir():
            raise CoreError('A single-file declaration does not accept a filename')
        return target, item

    def files(self, software, config):
        base, item = self.locate(software, config)
        result = []
        def visit(path, rel):
            if any(rel == p or rel.startswith(p + '/') for p in item.get('exclude', [])):
                return
            try:
                no_links(path, base.parent)
                if not path.exists():
                    kind = 'missing'
                elif path.is_dir():
                    kind = 'directory'
                elif path.is_file():
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
        path, _ = self.locate(software, config, file)
        before = observe(path)
        result = dict(before, path=str(path), editable=False)
        if before['kind'] != 'file' or before['size'] > MAX_TEXT:
            return dict(result, reason='Not a text file or exceeds 2 MiB')
        data = path.read_bytes()
        if observe(path)['version'] != before['version']:
            raise ConflictError('File changed during read')
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
            current = self.read(software, config, file)
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
            path, _ = self.locate(software, config, file)
            managed = [Path(self.roots[c['root']]).resolve() / c['path']
                       for s in self.configurations() for c in s['configs'] if self.roots.get(c['root'])]
            protected_state(self.backup, managed)
            session = new_session(self.backup)
            with tempfile.TemporaryDirectory(prefix='viewer-edit-') as tmp:
                source = Path(tmp) / 'content'
                source.write_bytes(data)
                return publish_file(path, source, expected_version, session, detach=detach)
