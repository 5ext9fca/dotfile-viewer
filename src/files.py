"""Local file observations and single-file, backed-up publication."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
from .errors import CoreError
from .paths import no_links, signature


class ConflictError(CoreError):
    exit_code = 4


class ConfirmationRequired(ConflictError):
    pass


def state_home():
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) if os.name == 'nt' else Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state')))
    # Preserve the existing backup location across the project rename.
    return base / 'dotfiles-viewer'


def observe(path):
    observation, _ = _read_observed(path)
    return observation


def _read_observed(path, content_limit=None):
    """Return the observation and optionally the bounded content used for its digest."""
    path = Path(path)
    no_links(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {'kind': 'missing', 'version': 'missing'}, None
    if stat.S_ISDIR(info.st_mode):
        return {'kind': 'directory', 'version': hashlib.sha256(repr(signature(info)).encode()).hexdigest()}, None
    if not stat.S_ISREG(info.st_mode):
        raise CoreError(f'Unsupported file type: {path}')
    content = bytearray() if content_limit is not None and info.st_size <= content_limit else None
    digest = hashlib.sha256()
    with path.open('rb') as source:
        if signature(os.fstat(source.fileno())) != signature(info):
            raise ConflictError(f'File changed: {path}')
        chunk_size = max(8192, min(info.st_size, 1024 * 1024))
        while chunk := source.read(chunk_size):
            digest.update(chunk)
            if content is not None:
                if len(content) + len(chunk) > content_limit:
                    raise ConflictError(f'File changed: {path}')
                content.extend(chunk)
        if signature(os.fstat(source.fileno())) != signature(info):
            raise ConflictError(f'File changed: {path}')
    if content_limit is not None:
        no_links(path)
    if signature(path.stat()) != signature(info):
        raise ConflictError(f'File changed: {path}')
    sha = digest.hexdigest()
    return dict(kind='file', version=hashlib.sha256((repr(signature(info)) + sha).encode()).hexdigest(),
                identity=[info.st_dev, info.st_ino], sha256=sha, size=info.st_size, executable=info.st_mode & 0o111, links=info.st_nlink), content


def atomic_json(path, value):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.report-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def protected_state(base, targets):
    base = Path(base).absolute()
    no_links(base)
    for target in targets:
        target = Path(target).absolute()
        if base == target or base.is_relative_to(target) or target.is_relative_to(base):
            raise CoreError('Backup/report directory overlaps managed configuration')
    return base


def new_session(base=None):
    base = Path(base or state_home() / 'backups')
    no_links(base)
    session = base / uuid.uuid4().hex
    session.mkdir(parents=True, mode=0o700)
    return session


def _observe_output(target, staged, staged_stat):
    current = observe(target)
    # A rename can change ctime. Check the remaining signature, file identity,
    # content and link count against the file observed before the rename.
    if (current['kind'] != 'file' or not staged_stat.st_ino
            or current['identity'] != staged['identity'] or current['sha256'] != staged['sha256']
            or current['links'] != staged['links']
            or signature(target.lstat())[:-1] != signature(staged_stat)[:-1]):
        raise ConflictError(f'Published file changed or cannot be identified: {target}')
    return current


def _recover_output(target, backup, record, staged_stat, published):
    def check_target():
        current = _observe_output(target, record['after'], staged_stat)
        if published and current['version'] != published['version']:
            raise ConflictError(f'Published file changed: {target}')

    check_target()
    if backup is None:
        target.unlink()
        return 'removed'
    fd, recovery = tempfile.mkstemp(prefix='.dotfiles-recovery-', dir=target.parent)
    os.close(fd)
    try:
        shutil.copy2(backup, recovery)
        if observe(recovery)['sha256'] != record['before']['sha256']:
            raise ConflictError(f'Backup contents changed: {backup}')
        check_target()
        os.replace(recovery, target)
    finally:
        Path(recovery).unlink(missing_ok=True)
    return 'restored'


def _backup_file(target, backup, before):
    # An independent copy also protects against edits through another hard link.
    shutil.copy2(target, backup)
    if observe(target)['version'] != before['version'] or observe(backup)['sha256'] != before['sha256']:
        raise ConflictError(f'File changed during backup: {target}')


def _stage_file(temp, source, backup, executable, preserve_permissions):
    if isinstance(source, bytes):
        Path(temp).write_bytes(source)
    else:
        shutil.copyfile(source, temp)
    if backup:
        shutil.copystat(backup, temp)
    if not backup or not preserve_permissions:
        os.chmod(temp, 0o600)
    if executable is not None:
        mode = stat.S_IMODE(os.stat(temp).st_mode)
        os.chmod(temp, (mode & ~0o111) | executable)


def _report_publish_failure(exc, target, backup, report, record, staged_stat, published):
    record.update(status='failed', error=str(exc))
    try:
        record['recovery'] = _recover_output(target, backup, record, staged_stat, published)
    except (OSError, CoreError) as recovery_error:
        record.update(recovery='manual', recovery_error=str(recovery_error))
    detail = f"{exc}; recovery={record['recovery']}; report={report}; backup={backup}"
    try:
        atomic_json(report, record)
    except OSError as report_error:
        raise CoreError(f'{detail}; could not write failure report: {report_error}') from exc
    if isinstance(exc, (OSError, CoreError)):
        failure = ConflictError if isinstance(exc, ConflictError) else CoreError
        raise failure(detail) from exc


def publish_file(target, source, expected, session, *, detach=False, executable=None, preserve_permissions=True):
    """Caller holds the deployment lease. Never overwrite a changed target."""
    target, session = Path(target), Path(session)
    before = observe(target)
    if before['version'] != expected:
        raise ConflictError(f'File changed: {target}')
    if before['kind'] not in ('file', 'missing'):
        raise CoreError(f'Cannot replace a directory: {target}')
    if before.get('links', 1) > 1 and not detach:
        raise ConfirmationRequired(f'Independent save requires --detach-hardlink: {target}')
    identifier = uuid.uuid4().hex
    backup = session / (identifier + '.original') if before['kind'] == 'file' else None
    record = dict(target=str(target), before=before, backup=str(backup) if backup else None, status='preparing')
    report = session / (identifier + '.json')
    if backup:
        _backup_file(target, backup, before)
    atomic_json(report, record)
    fd, temp = tempfile.mkstemp(prefix='.dotfile-viewer-', dir=target.parent)
    os.close(fd)
    replaced = False
    published = None
    try:
        _stage_file(temp, source, backup, executable, preserve_permissions)
        if observe(target)['version'] != expected:
            raise ConflictError(f'File changed before save: {target}')
        after = observe(temp)
        staged_stat = os.stat(temp)
        record.update(status='prepared', after=after)
        atomic_json(report, record)
        os.replace(temp, target)
        replaced = True
        published = _observe_output(target, after, staged_stat)
        record.update(status='saved')
        atomic_json(report, record)
    except BaseException as exc:
        if not replaced:
            raise
        _report_publish_failure(exc, target, backup, report, record, staged_stat, published)
        raise
    finally:
        Path(temp).unlink(missing_ok=True)
    return dict(target=str(target), status='saved', backup=str(backup) if backup else None, version=published['version'])
