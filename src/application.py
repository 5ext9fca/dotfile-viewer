"""Validated snapshot sessions, preview and same-platform configuration apply."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import tempfile
from .errors import CoreError, SnapshotError
from .files import observe, publish_file, new_session, state_home, protected_state, atomic_json, ConflictError
from .host import host, host_roots
from .lease import deployment_lease
from .paths import no_links
from .snapshot import verify_archive, targets, member_name


class _PreparedSnapshot:
    """Pin an archive in private staging for preview, installation and application."""
    def __init__(self, path, *, platform=None, roots=None, backup=None, protected=()):
        self.path = Path(path).resolve()
        self.platform = platform or host()
        self.roots = roots if roots is not None else host_roots()
        self.backup = Path(backup or os.environ.get('DOTFILES_BACKUP_DIR') or state_home() / 'backups').resolve()
        self.protected = [self.path, Path(__file__).resolve().parent, *map(lambda p: Path(p).resolve(), protected)]
        self._temporary = tempfile.TemporaryDirectory(prefix='viewer-snapshot-')
        self.scratch = Path(self._temporary.name)
        try:
            pinned = self.scratch / 'snapshot.zip'
            shutil.copyfile(self.path, pinned)
            self.snapshot = verify_archive(pinned, self.scratch / 'unpacked')
            if self.snapshot.platform != self.platform:
                raise SnapshotError('Snapshot platform, architecture or environment mismatch')
            self.destinations = targets(self.snapshot.software, self.roots)
            for target in self.destinations.values():
                if any(target == p or target.is_relative_to(p) or p.is_relative_to(target) for p in [*self.protected, self.scratch]):
                    raise CoreError('Configuration overlaps protected application or snapshot path')
            protected_state(self.backup, self.destinations.values())
            # A member name sorts before its descendants, regardless of manifest order.
            self._entries = sorted(self.snapshot.entries, key=member_name)
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self._temporary.cleanup()

    def preview(self):
        rows = []
        for entry in self._entries:
            base = self.destinations[(entry['software'], entry['config'])]
            target = base / entry['path'] if entry['path'] else base
            no_links(target)
            # Reject file ancestors before starting any installation or write.
            parent = target.parent
            while not parent.exists() and parent.parent != parent:
                parent = parent.parent
            if not parent.is_dir():
                raise CoreError(f'Configuration ancestor is not a directory: {parent}')
            before = observe(target)
            if before['kind'] not in ('missing', entry['kind']):
                raise CoreError(f'Configuration type conflict: {target}')
            if entry['kind'] == 'directory':
                action = 'mkdir' if before['kind'] == 'missing' else 'skip'
            else:
                same = before.get('sha256') == entry['sha256'] and before.get('executable') == entry['executable']
                action = 'skip' if same else 'create' if before['kind'] == 'missing' else 'replace'
            rows.append(dict(software=entry['software'], config=entry['config'], file=entry['path'],
                             target=str(target), action=action, version=before['version'],
                             detach_required=action == 'replace' and before.get('links', 1) > 1))
        token = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        return dict(version=token, items=rows)

    def apply(self, *, dry=False, expected_version=None, detach=False, lease=None):
        lease = lease or {}
        with deployment_lease(path=lease.get('path'), token=lease.get('token'), dry=dry):
            plan = self.preview()
            if expected_version and plan['version'] != expected_version:
                raise ConflictError('Targets changed since preview; preview again')
            if dry:
                return dict(status='preview', **plan)
            if not detach and any(row['detach_required'] for row in plan['items']):
                from .files import ConfirmationRequired
                raise ConfirmationRequired('Snapshot replaces hard links; confirm --detach-hardlink')
            if all(row['action'] == 'skip' for row in plan['items']):
                return dict(status='unchanged', **plan)
            session = new_session(self.backup)
            result = dict(status='running', session=str(session), items=[])
            report = session / 'result.json'
            atomic_json(report, result)
            try:
                for entry, row in zip(self._entries, plan['items'], strict=True):
                    target = Path(row['target'])
                    if row['action'] == 'skip':
                        if entry['kind'] == 'file' and observe(target)['version'] != row['version']:
                            raise ConflictError(f'File changed: {target}')
                        result['items'].append(dict(row, status='skipped'))
                    elif row['action'] == 'mkdir':
                        no_links(target)
                        if target.exists():
                            raise ConflictError(f'Target appeared: {target}')
                        target.mkdir(parents=True, mode=0o700)
                        result['items'].append(dict(row, status='created'))
                    else:
                        no_links(target)
                        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        saved = publish_file(target, self.scratch / 'unpacked' / member_name(entry), row['version'], session,
                                             detach=detach, executable=entry['executable'], preserve_permissions=False)
                        result['items'].append(dict(row, **{k: v for k, v in saved.items() if k not in row}))
                    atomic_json(report, result)
                result['status'] = 'applied'
                atomic_json(report, result)
                return result
            except BaseException as exc:
                result.update(status='failed', error=str(exc))
                atomic_json(report, result)
                raise


class SnapshotSession(_PreparedSnapshot):
    """Check destinations immediately, before a caller starts installation."""
    def __init__(self, path, *, platform=None, roots=None, backup=None, protected=()):
        super().__init__(path, platform=platform, roots=roots, backup=backup, protected=protected)
        try:
            self.preview()
        except BaseException:
            self.close()
            raise
