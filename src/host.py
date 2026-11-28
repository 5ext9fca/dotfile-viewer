from pathlib import Path
import os
import platform as host_platform
import shutil
from .errors import SnapshotError
from .manifest import Platform

def host():
    system = host_platform.system()
    if system == 'Darwin':
        branch = 'macos'
    elif system == 'Windows':
        branch = 'windows'
    elif system == 'Linux':
        manager = next((name for name, command in [('pacman', 'pacman'), ('apt', 'apt-get'), ('dnf', 'dnf')] if shutil.which(command)), None)
        if manager is None:
            raise SnapshotError('No supported Linux package manager')
        branch = 'linux.' + manager
    else:
        raise SnapshotError('Unsupported operating system')
    arch = host_platform.machine().lower()
    arch = {'amd64': 'x86_64', 'aarch64': 'arm64', 'i386': 'x86', 'i686': 'x86'}.get(arch, arch)
    environment = 'wsl' if system == 'Linux' and 'microsoft' in host_platform.release().lower() else 'native'
    return Platform(branch, arch, environment).validate()


def host_roots():
    home = str(Path.home())
    if os.name == 'nt':
        return dict(home=home, app_data=os.environ.get('APPDATA', ''), local_app_data=os.environ.get('LOCALAPPDATA', ''))
    return dict(home=home, config_home=os.environ.get('XDG_CONFIG_HOME', str(Path(home) / '.config')))
