"""Path checks shared by configuration editing, publication and snapshots."""
import stat
from .errors import SnapshotError


def is_excluded(relative_path, exclusions):
    return any(relative_path == path or relative_path.startswith(path + '/') for path in exclusions)


def no_links(path, stop=None):
    """Reject symlinks/reparse points from path through stop, inclusive."""
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


def signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
