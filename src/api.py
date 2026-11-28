"""Public Python API v1; shared by CLI and dotfile-syncronizer.

Snapshots use manifest v2. Catalog declarations retain v1 and profiles v3.
No API in this package installs software or executes declaration contents.
"""
from .catalog import catalog_root, load_catalog, selection
from .software import installation, installation_roots, select, write_catalog, profile_selection
from .snapshot import Platform, Snapshot, capture, verify_archive
from .application import SnapshotSession
from .editor import Editor
from .lease import deployment_lease
from .host import host, host_roots
from .errors import CoreError, SnapshotError
from .files import ConflictError, ConfirmationRequired

API_VERSION = 1


def export_snapshot(output, *, catalog=None, profile=None, software=None, platform=None, roots=None, dry=False):
    names = selection(catalog, profile=profile, software=software)
    with deployment_lease(dry=dry):
        return capture(load_catalog(catalog), platform or host(), names, roots if roots is not None else host_roots(), output, dry)


def apply_snapshot(path, *, dry=False, detach=False, expected_version=None, lease=None, **context):
    with SnapshotSession(path, **context) as session:
        return session.apply(dry=dry, detach=detach, expected_version=expected_version, lease=lease)
