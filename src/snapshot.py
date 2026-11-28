"""Versioned, self-contained software/configuration archives.

This module never installs software or writes application configuration. Archive
members are validated before extraction; capture observes files without following
links and never publishes a partial archive.
"""
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
import errno
import hashlib
import io
import json
import os
import stat
import tempfile
import zipfile

from dotfile_viewer.errors import SnapshotError
from .manifest import Platform, Snapshot, configurations, member_name
from dotfile_viewer.paths import is_excluded, no_links, signature
from dotfile_viewer.software import select, relative, selection_names


MAX_MANIFEST_BYTES = 16 * 1024 * 1024


def targets(catalog, roots):
    return _targets(configurations(catalog), roots)


def _targets(configs, roots):
    result = {}
    destinations = set()
    ancestors = set()
    resolved_roots = {}
    for key, config in configs.items():
        name = config['root']
        if name not in resolved_roots:
            value = roots.get(name, '')
            if not value or not Path(value).is_absolute():
                raise SnapshotError(f'Missing absolute configuration root: {name}')
            resolved_roots[name] = Path(value).resolve()
        root = resolved_roots[name]
        target = root / config['path']
        no_links(target, root)
        folded = str(target).casefold()
        parents = [str(parent).casefold() for parent in target.parents]
        if folded in destinations or folded in ancestors or not destinations.isdisjoint(parents):
            raise SnapshotError('Overlapping configuration destinations')
        destinations.add(folded)
        ancestors.update(parents)
        result[key] = target
    return result


def _inventory(configs, locations):
    for key, path in locations.items():
        exclusions = set(configs[key].get('exclude', ()))
        pending = [(path, '')]
        while pending:
            current, relative_path = pending.pop()
            if relative_path and not relative(relative_path):
                raise SnapshotError(f'Unsupported configuration filename: {current}')
            if is_excluded(relative_path, exclusions):
                continue
            info = no_links(current, path.parent)
            if info is None:
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(current))
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise SnapshotError(f'Unsupported configuration file: {current}')
            yield (*key, relative_path), (current, signature(info), stat.S_ISDIR(info.st_mode))
            if stat.S_ISDIR(info.st_mode):
                for child in sorted(current.iterdir(), reverse=True):
                    rel = relative_path + '/' + child.name if relative_path else child.name
                    pending.append((child, rel))


def json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotError('Duplicate manifest key')
        result[key] = value
    return result


def open_manifest(archive):
    members = archive.infolist()
    for info in members:
        # Duplicate names share a lookup key, but have distinct central-directory records.
        if archive.getinfo(info.filename) is not info:
            raise SnapshotError('Duplicate archive members or missing manifest')
        mode = info.external_attr >> 16
        if (info.orig_filename != info.filename or not relative(info.filename) or info.is_dir() or info.flag_bits & 1
                or stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
            raise SnapshotError('Unsafe archive member')
    try:
        manifest = archive.getinfo('manifest.json')
    except KeyError as exc:
        raise SnapshotError('Duplicate archive members or missing manifest') from exc
    if manifest.file_size > MAX_MANIFEST_BYTES:
        raise SnapshotError('Snapshot manifest exceeds 16 MiB')
    snapshot = Snapshot.parse(json.loads(archive.read('manifest.json').decode('utf-8'), object_pairs_hook=json_object))
    expected_count = 1
    for entry in snapshot.entries:
        if entry['kind'] == 'file':
            try:
                archive.getinfo(member_name(entry))
            except KeyError as exc:
                raise SnapshotError('Archive members do not match manifest') from exc
            expected_count += 1
    if len(members) != expected_count:
        raise SnapshotError('Archive members do not match manifest')
    return snapshot


def _hash_payload(source, output=None):
    digest = hashlib.sha256()
    size = 0
    while chunk := source.read(1024 * 1024):
        digest.update(chunk)
        size += len(chunk)
        if output is not None:
            output.write(chunk)
    return size, digest.hexdigest()


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
            try:
                with archive.open(info) as source:
                    size, digest = _hash_payload(source, output)
            finally:
                if output:
                    output.close()
            if size != entry['size'] or digest != entry['sha256']:
                raise SnapshotError('Snapshot content digest mismatch')
            if target:
                target.chmod(0o600 | entry['executable'])
    return snapshot


def _encode_manifest(document, output=None):
    size = 0
    encoder = json.JSONEncoder(ensure_ascii=False, indent=2)
    for chunk in encoder.iterencode(document):
        data = chunk.encode('utf-8')
        size += len(data)
        if size > MAX_MANIFEST_BYTES:
            raise SnapshotError('Snapshot manifest exceeds 16 MiB')
        if output is not None:
            output.write(data)


def _write_manifest(archive, document):
    with archive.open('manifest.json', 'w') as member, io.BufferedWriter(member, 64 * 1024) as stream:
        _encode_manifest(document, stream)


def _collect_snapshot(platform, selection, chosen, configs, locations, roots, archive=None):
    before = dict(_inventory(configs, locations))
    entries = []
    for (identity, config, rel), (path, observed, directory) in before.items():
        entry = dict(software=identity, config=config, path=rel, kind='directory' if directory else 'file',
                     size=0, sha256='', executable=0)
        if not directory:
            no_links(path)
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
            with os.fdopen(os.open(path, flags), 'rb') as source:
                if signature(os.fstat(source.fileno())) != observed:
                    raise SnapshotError(f'Configuration changed during capture: {path}')
                with archive.open(member_name(entry), 'w', force_zip64=True) if archive else nullcontext() as sink:
                    entry['size'], entry['sha256'] = _hash_payload(source, sink)
                if signature(os.fstat(source.fileno())) != observed:
                    raise SnapshotError(f'Configuration changed during capture: {path}')
            entry['executable'] = observed[2] & 0o111
        entries.append(entry)
    for key, observation in _inventory(configs, _targets(configs, roots)):
        if before.pop(key, None) != observation:
            raise SnapshotError('Configuration tree changed during capture; stop writers and retry')
    if before:
        raise SnapshotError('Configuration tree changed during capture; stop writers and retry')
    snapshot = Snapshot(platform, datetime.now(timezone.utc).isoformat(), tuple(selection), chosen, tuple(entries))
    Snapshot.parse(snapshot.document())
    return snapshot


def capture(catalog, platform, selection, roots, output, dry=False):
    platform.validate()
    selection = selection_names(selection)
    chosen = select(catalog, platform.branch, platform.environment, selection)
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise SnapshotError('Snapshot output already exists')
    if not output.parent.is_dir():
        raise SnapshotError('Snapshot output directory does not exist')
    configs = configurations(chosen)
    locations = _targets(configs, roots)
    physical_output = output.parent.resolve() / output.name
    if any(physical_output == p or physical_output.is_relative_to(p) for p in locations.values()):
        raise SnapshotError('Snapshot output overlaps a configuration source')
    if dry:
        snapshot = _collect_snapshot(platform, selection, chosen, configs, locations, roots)
        _encode_manifest(snapshot.document())
        return snapshot
    fd, temporary = tempfile.mkstemp(prefix='.dotfiles-snapshot-', suffix='.zip', dir=output.parent)
    try:
        os.close(fd)
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            snapshot = _collect_snapshot(platform, selection, chosen, configs, locations, roots, archive)
            _write_manifest(archive, snapshot.document())
        verify_archive(temporary)
        os.link(temporary, output)
        return snapshot
    finally:
        Path(temporary).unlink(missing_ok=True)


def verify_archive(path) -> Snapshot:
    return _checked_archive(path, None)


def _checked_archive(path, destination):
    try:
        return _verify_archive(path, destination)
    except SnapshotError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError, zipfile.BadZipFile, RuntimeError) as exc:
        raise SnapshotError('Invalid snapshot archive: ' + str(exc)) from exc
