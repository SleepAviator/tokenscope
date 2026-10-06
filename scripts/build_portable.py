#!/usr/bin/env python3
"""Build Windows and Linux standalone TokenScope bundles on native runners."""
import argparse
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', default=os.environ.get('VERSION'))
    parser.add_argument('--platform', choices=('Windows', 'Linux'),
                        default=os.environ.get('PLATFORM'))
    parser.add_argument('--arch', choices=('x86_64',), default=os.environ.get('ARCH', 'x86_64'))
    args = parser.parse_args()
    if not args.version:
        parser.error('set VERSION or pass --version')
    if not args.platform:
        parser.error('set PLATFORM or pass --platform')
    if not re.fullmatch(r'v?\d+\.\d+\.\d+', args.version):
        parser.error('version must look like v0.2.0')

    root = Path(__file__).resolve().parent.parent
    build_root = root / 'build' / f'{args.platform.lower()}-{args.arch}'
    dist_dir = build_root / 'dist'
    binary_name = 'TokenScopeServer.exe' if args.platform == 'Windows' else 'TokenScopeServer'
    separator = ';' if args.platform == 'Windows' else ':'
    sources = ('update.py', 'collect.py', 'config.example.ini', 'web.html',
               'web.js', 'session_usage.js', 'response_speed.js', 'i18n.js', 'web.css')
    command = [
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
        '--name', 'TokenScopeServer', '--distpath', str(dist_dir),
        '--workpath', str(build_root / 'work'), '--specpath', str(build_root),
        '--hidden-import', 'update', '--hidden-import', 'collect',
    ]
    for filename in sources:
        command.extend(('--add-data', f'{root / filename}{separator}.'))
    command.append(str(root / 'app.py'))
    subprocess.run(command, cwd=root, check=True)

    bundle_name = f'TokenScope-{args.platform}-{args.arch}'
    package_root = build_root / 'package' / bundle_name
    package_root.parent.mkdir(parents=True, exist_ok=True)
    if package_root.exists():
        shutil.rmtree(package_root)
    package_root.mkdir()
    shutil.copytree(dist_dir / 'TokenScopeServer', package_root / 'TokenScopeServer')
    shutil.copy2(root / 'config.example.ini', package_root / 'config.example.ini')

    if args.platform == 'Windows':
        shutil.copy2(root / 'windows' / 'launch-tokenscope.bat', package_root)
        shutil.copy2(root / 'windows' / 'README.txt', package_root)
    else:
        launcher = package_root / 'launch-tokenscope.sh'
        shutil.copy2(root / 'linux' / 'launch-tokenscope.sh', launcher)
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        shutil.copy2(root / 'linux' / 'README.txt', package_root)

    release_dir = root / 'release-output'
    release_dir.mkdir(parents=True, exist_ok=True)
    if args.platform == 'Windows':
        archive = release_dir / f'TokenScope-{args.version}-Windows-{args.arch}.zip'
        shutil.make_archive(str(archive.with_suffix('')), 'zip',
                            root_dir=package_root.parent, base_dir=bundle_name)
    else:
        archive = release_dir / f'TokenScope-{args.version}-Linux-{args.arch}.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            tar.add(package_root, arcname=bundle_name)
    print(f'Packaged {binary_name}: {archive}', flush=True)


if __name__ == '__main__':
    main()
