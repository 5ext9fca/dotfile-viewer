"""Path checks shared by configuration editing, publication and snapshots."""
import stat
from .errors import SnapshotError


def is_excluded(relative_path, exclusions):
    if not exclusions:
        return False
    while relative_path:
        if relative_path in exclusions:
            return True
        relative_path = relative_path.rpartition('/')[0]
    return False


def no_links(path, stop=None):
    """Check through stop, inclusive, and return the leaf lstat or None if missing."""
    current = path
    leaf = None
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError:
            info = None
        if info and (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400):
            raise SnapshotError(f'Links/reparse points are not supported: {current}')
        if current == path:
            leaf = info
        if current == stop or current.parent == current:
            return leaf
        current = current.parent


def signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
