"""Validated snapshot sessions, preview and same-platform configuration apply."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import os
import shutil
import tempfile
from .errors import CoreError, SnapshotError
from .files import observe, publish_file, new_session, state_home, protected_state, atomic_json, ConflictError, ConfirmationRequired
from .host import host, host_roots
from .lease import borrowed_lease, deployment_lease
from .paths import no_links
from .snapshot import verify_archive, targets, member_name


def _plan_version(rows):
    digest = hashlib.sha256(b'[')
    for start in range(0, len(rows), 128):
        if start:
            digest.update(b', ')
        batch = json.dumps(rows[start:start + 128], sort_keys=True)
        digest.update(batch[1:-1].encode())
    digest.update(b']')
    return digest.hexdigest()


def _confirm_detach(plan, detach):
    if not detach and any(row['detach_required'] for row in plan['items']):
        raise ConfirmationRequired('Snapshot replaces hard links; confirm --detach-hardlink')


class _PreparedSnapshot:
    """Pin an archive in private staging for preview, installation and application."""
    def __init__(self, path, *, platform=None, roots=None, backup=None, protected=(), stage_payload=True):
        self._path = Path(path).resolve()
        self._platform = platform or host()
        self._roots = roots if roots is not None else host_roots()
        self._backup = Path(backup or os.environ.get('DOTFILES_BACKUP_DIR') or state_home() / 'backups').resolve()
        self._protected = [self._path, Path(__file__).resolve().parent, *map(lambda p: Path(p).resolve(), protected)]
        self._temporary = tempfile.TemporaryDirectory(prefix='viewer-snapshot-')
        self._closed = False
        self._scratch = Path(self._temporary.name)
        try:
            pinned = self._scratch / 'snapshot.zip'
            shutil.copyfile(self._path, pinned)
            destination = self._scratch / 'unpacked' if stage_payload else None
            self._snapshot = verify_archive(pinned, destination)
            if self._snapshot.platform != self._platform:
                raise SnapshotError('Snapshot platform, architecture or environment mismatch')
            self._destinations = targets(self._snapshot.software, self._roots)
            for target in self._destinations.values():
                if any(target == p or target.is_relative_to(p) or p.is_relative_to(target) for p in [*self._protected, self._scratch]):
                    raise CoreError('Configuration overlaps protected application or snapshot path')
            protected_state(self._backup, self._destinations.values())
            # A member name sorts before its descendants, regardless of manifest order.
            self._entries = sorted(self._snapshot.entries, key=member_name)
        except BaseException:
            self.close()
            raise

    @property
    def snapshot(self):
        return deepcopy(self._snapshot)

    @property
    def destination_roots(self) -> tuple[Path, ...]:
        return tuple(self._destinations.values())

    def _ensure_open(self):
        if self._closed:
            raise CoreError('Snapshot session is closed')

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if not self._closed:
            self._temporary.cleanup()
            self._closed = True

    def preview(self):
        self._ensure_open()
        rows = []
        for entry in self._entries:
            base = self._destinations[(entry['software'], entry['config'])]
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
        return dict(version=_plan_version(rows), items=rows)

    def _apply_lease(self, lease, dry):
        lease = lease or {}
        return deployment_lease(path=lease.get('path'), token=lease.get('token'), dry=dry)

    def apply(self, *, dry=False, expected_version=None, detach=False, lease=None):
        self._ensure_open()
        with self._apply_lease(lease, dry):
            plan = self.preview()
            if expected_version and plan['version'] != expected_version:
                raise ConflictError('Targets changed since preview; preview again')
            if dry:
                return dict(status='preview', **plan)
            _confirm_detach(plan, detach)
            if all(row['action'] == 'skip' for row in plan['items']):
                return dict(status='unchanged', **plan)
            session = new_session(self._backup)
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
                        saved = publish_file(target, self._scratch / 'unpacked' / member_name(entry), row['version'], session,
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
    def __init__(self, path, *, platform=None, roots=None, backup=None, protected=(), lease=None):
        self._bound_lease = None
        if lease is not None:
            if not lease.get('path') or not lease.get('token'):
                raise CoreError('Borrowed deployment lease requires path and token')
            self._bound_lease = dict(path=lease['path'], token=lease['token'])
        super().__init__(path, platform=platform, roots=roots, backup=backup, protected=protected)
        try:
            self.preview()
        except BaseException:
            self.close()
            raise

    def preflight(self, *, detach=False) -> dict:
        """Return a fresh plan after checking hard-link confirmation."""
        plan = self.preview()
        _confirm_detach(plan, detach)
        return plan

    def _apply_lease(self, lease, dry):
        if self._bound_lease is None:
            return super()._apply_lease(lease, dry)
        if lease is not None and lease != self._bound_lease:
            raise CoreError('Conflicting deployment lease for snapshot session')
        return borrowed_lease(**self._bound_lease, dry=dry)
