"""Versioned, self-contained software/configuration archives.

This module never installs software or writes application configuration. Archive
members are validated before extraction; capture observes files without following
links and never publishes a partial archive.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import stat
import tempfile
import zipfile

from dotfile_viewer.errors import CoreError
from dotfile_viewer.software import validate, select, installation, relative, PLATFORMS


class SnapshotError(CoreError):
    pass


@dataclass(frozen=True)
class Platform:
    branch: str
    architecture: str
    environment: str

    def validate(self):
        if (self.branch not in PLATFORMS or self.environment not in ('native', 'wsl')
                or self.architecture not in ('x86_64', 'arm64', 'x86', 'armv7l', 'ppc64le', 's390x', 'riscv64')
                or (self.environment == 'wsl' and not self.branch.startswith('linux.'))):
            raise SnapshotError('Unsupported snapshot platform')
        return self

    def document(self):
        return dict(branch=self.branch, architecture=self.architecture, environment=self.environment)


@dataclass(frozen=True)
class Snapshot:
    platform: Platform
    created_at: str
    selection: tuple[str, ...]
    software: dict
    entries: tuple[dict, ...]
    schema_version: int = 2

    def document(self):
        return dict(schema_version=self.schema_version, platform=self.platform.document(), created_at=self.created_at,
                    selection=list(self.selection), software=self.software, entries=list(self.entries))

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or set(data) != {'schema_version', 'platform', 'created_at', 'selection', 'software', 'entries'} or type(data['schema_version']) is not int or data['schema_version'] != 2:
            raise SnapshotError('Unsupported snapshot manifest; export a new v2 snapshot')
        if not isinstance(data['platform'], dict) or set(data['platform']) != {'branch', 'architecture', 'environment'}:
            raise SnapshotError('Invalid snapshot platform')
        platform = Platform(**data['platform']).validate()
        if not isinstance(data['created_at'], str) or not datetime.fromisoformat(data['created_at']).tzinfo:
            raise SnapshotError('Invalid snapshot timestamp')
        names = data['selection']
        if not isinstance(names, list) or not names or any(not isinstance(n, str) for n in names) or len(names) != len(set(names)):
            raise SnapshotError('Invalid snapshot selection')
        catalog = data['software']
        if not isinstance(catalog, dict) or not catalog:
            raise SnapshotError('Empty snapshot software set')
        for identity, declaration in catalog.items():
            validate(identity, declaration)
            if (any(p != platform.branch and not p.startswith(platform.branch + '/') for p in declaration.get('install', {}))
                    or any(c['platforms'] != [platform.branch] for c in declaration.get('config', []))
                    or any(p != platform.branch for p in declaration.get('requires', {}))):
                raise SnapshotError('Snapshot contains declarations for another platform')
        selected = select(catalog, platform.branch, platform.environment, names)
        if selected != catalog:
            raise SnapshotError('Snapshot software differs from selection closure')
        installation(catalog, platform.branch)
        entries = data['entries']
        if not isinstance(entries, list):
            raise SnapshotError('Invalid snapshot entries')
        configs = configurations(catalog)
        seen, roots = {}, set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {'software', 'config', 'path', 'kind', 'size', 'sha256', 'executable'}:
                raise SnapshotError('Invalid snapshot entry')
            key = (entry['software'], entry['config'])
            if key not in configs or not isinstance(entry['path'], str) or (entry['path'] and not relative(entry['path'])):
                raise SnapshotError('Invalid snapshot entry path')
            config = configs[key]
            if any(entry['path'] == p or entry['path'].startswith(p + '/') for p in config.get('exclude', [])):
                raise SnapshotError('Excluded content in snapshot')
            member = member_name(entry)
            folded = member.casefold()
            if folded in seen:
                raise SnapshotError('Duplicate or case-conflicting snapshot path')
            seen[folded] = entry
            if entry['path'] == '':
                roots.add(key)
            if type(entry['size']) is not int or entry['size'] < 0 or type(entry['executable']) is not int or entry['executable'] & ~0o111:
                raise SnapshotError('Invalid file size or executable permission')
            if entry['kind'] == 'file':
                if not isinstance(entry['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', entry['sha256']):
                    raise SnapshotError('Invalid content digest')
            elif entry['kind'] != 'directory' or entry['size'] != 0 or entry['sha256'] != '' or entry['executable'] != 0:
                raise SnapshotError('Invalid directory entry')
        if roots != set(configs):
            raise SnapshotError('Missing declared configuration roots')
        for entry in entries:
            if entry['path']:
                parent = entry['path'].rsplit('/', 1)[0] if '/' in entry['path'] else ''
                key = member_name(dict(entry, path=parent)).casefold()
                if key not in seen or seen[key]['kind'] != 'directory' or member_name(seen[key]) != member_name(dict(entry, path=parent)):
                    raise SnapshotError('Missing directory or file/directory conflict')
        return cls(platform, data['created_at'], tuple(names), catalog, tuple(entries))


def configurations(catalog):
    return {(identity, config['id']): config for identity, data in catalog.items() for config in data.get('config', [])}


def member_name(entry):
    return f"payload/{entry['software']}/{entry['config']}/root" + ('/' + entry['path'] if entry['path'] else '')


def no_links(path, stop=None):
    """Reject symlinks/reparse points in the selected tree, including ancestors."""
    current = path
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError:
            info = None
        if info and (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400):
            raise SnapshotError(f'Links/reparse points are not supported: {current}')
        if current == stop or current.parent == current:
            return
        current = current.parent


def targets(catalog, roots):
    result = {}
    for key, config in configurations(catalog).items():
        value = roots.get(config['root'], '')
        if not value or not Path(value).is_absolute():
            raise SnapshotError(f'Missing absolute configuration root: {config["root"]}')
        # Resolve the caller-owned root once (e.g. macOS /var -> /private/var),
        # then reject links within its selected descendants.
        root = Path(value).resolve()
        target = root / config['path']
        no_links(target, root)
        folded = str(target).casefold()
        if any(folded == str(p).casefold() or folded.startswith(str(p).casefold() + os.sep)
               or str(p).casefold().startswith(folded + os.sep) for p in result.values()):
            raise SnapshotError('Overlapping configuration destinations')
        result[key] = target
    return result


def signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def inventory(catalog, roots):
    locations = targets(catalog, roots)
    result = {}
    for key, path in locations.items():
        exclusions = configurations(catalog)[key].get('exclude', [])
        def visit(current, relative_path):
            if any(relative_path == p or relative_path.startswith(p + '/') for p in exclusions):
                return
            no_links(current, path.parent)
            info = current.lstat()  # Missing required paths abort the whole capture.
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise SnapshotError(f'Unsupported configuration file: {current}')
            result[(*key, relative_path)] = (current, signature(info), stat.S_ISDIR(info.st_mode))
            if stat.S_ISDIR(info.st_mode):
                for child in sorted(current.iterdir()):
                    rel = relative_path + '/' + child.name if relative_path else child.name
                    if not relative(rel):
                        raise SnapshotError(f'Unsupported configuration filename: {child}')
                    visit(child, rel)
        visit(path, '')
    return result


def json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotError('Duplicate manifest key')
        result[key] = value
    return result


def open_manifest(archive):
    members = archive.infolist()
    names = [i.filename for i in members]
    if len(names) != len(set(names)) or names.count('manifest.json') != 1:
        raise SnapshotError('Duplicate archive members or missing manifest')
    for info in members:
        mode = info.external_attr >> 16
        if (info.orig_filename != info.filename or not relative(info.filename) or info.is_dir() or info.flag_bits & 1
                or stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
            raise SnapshotError('Unsafe archive member')
    if archive.getinfo('manifest.json').file_size > 16 * 1024 * 1024:
        raise SnapshotError('Snapshot manifest exceeds 16 MiB')
    snapshot = Snapshot.parse(json.loads(archive.read('manifest.json').decode('utf-8'), object_pairs_hook=json_object))
    expected = {'manifest.json'} | {member_name(e) for e in snapshot.entries if e['kind'] == 'file'}
    if set(names) != expected:
        raise SnapshotError('Archive members do not match manifest')
    return snapshot


def _verify_archive(path, destination=None):
    """Validate and optionally stage content; never use ZipFile.extract."""
    with zipfile.ZipFile(path) as archive:
        snapshot = open_manifest(archive)
        for entry in snapshot.entries:
            target = Path(destination) / member_name(entry) if destination else None
            if entry['kind'] == 'directory':
                if target:
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            info = archive.getinfo(member_name(entry))
            if info.file_size != entry['size']:
                raise SnapshotError('Archive file size differs from manifest')
            if target:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            output = target.open('xb') if target else None
            digest, size = hashlib.sha256(), 0
            try:
                with archive.open(info) as source:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                        if output:
                            output.write(chunk)
            finally:
                if output:
                    output.close()
            if size != entry['size'] or digest.hexdigest() != entry['sha256']:
                raise SnapshotError('Snapshot content digest mismatch')
            if target:
                target.chmod(0o600 | entry['executable'])
    return snapshot


def capture(catalog, platform, selection, roots, output, dry=False):
    platform.validate()
    chosen = select(catalog, platform.branch, platform.environment, selection)
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise SnapshotError('Snapshot output already exists')
    if not output.parent.is_dir():
        raise SnapshotError('Snapshot output directory does not exist')
    locations = targets(chosen, roots)
    physical_output = output.parent.resolve() / output.name
    if any(physical_output == p or physical_output.is_relative_to(p) for p in locations.values()):
        raise SnapshotError('Snapshot output overlaps a configuration source')
    before = inventory(chosen, roots)
    entries = []
    # Dry runs still check readability and content, but produce no output archive.
    temporary = None
    archive = None
    try:
        if not dry:
            fd, temporary = tempfile.mkstemp(prefix='.dotfiles-snapshot-', suffix='.zip', dir=output.parent)
            os.close(fd)
            archive = zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED)
        for (identity, config, rel), (path, observed, directory) in before.items():
            entry = dict(software=identity, config=config, path=rel, kind='directory' if directory else 'file',
                         size=0, sha256='', executable=0)
            if not directory:
                no_links(path)
                flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                with os.fdopen(os.open(path, flags), 'rb') as source:
                    if signature(os.fstat(source.fileno())) != observed:
                        raise SnapshotError(f'Configuration changed during capture: {path}')
                    sink = archive.open(member_name(entry), 'w', force_zip64=True) if archive else None
                    digest = hashlib.sha256()
                    try:
                        while chunk := source.read(1024 * 1024):
                            entry['size'] += len(chunk)
                            digest.update(chunk)
                            if sink:
                                sink.write(chunk)
                    finally:
                        if sink:
                            sink.close()
                    if signature(os.fstat(source.fileno())) != observed:
                        raise SnapshotError(f'Configuration changed during capture: {path}')
                entry['sha256'] = digest.hexdigest()
                entry['executable'] = observed[2] & 0o111
            entries.append(entry)
        if inventory(chosen, roots) != before:
            raise SnapshotError('Configuration tree changed during capture; stop writers and retry')
        snapshot = Snapshot(platform, datetime.now(timezone.utc).isoformat(), tuple(selection), chosen, tuple(entries))
        Snapshot.parse(snapshot.document())
        if archive:
            archive.writestr('manifest.json', json.dumps(snapshot.document(), ensure_ascii=False, indent=2))
            archive.close()
            archive = None
            verify_archive(temporary)
            # Atomic no-clobber publication. Failure on filesystems without hard
            # links is explicit; never fall back to a partially visible copy.
            os.link(temporary, output)
        return snapshot
    finally:
        if archive:
            archive.close()
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def verify_archive(path, destination=None):
    try:
        return _verify_archive(path, destination)
    except SnapshotError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError, zipfile.BadZipFile, RuntimeError) as exc:
        raise SnapshotError('Invalid snapshot archive: ' + str(exc)) from exc
