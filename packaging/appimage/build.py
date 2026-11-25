#!/usr/bin/env python3
"""Rebuild from a hash-pinned complete AppImage; verify the extracted output.

All outputs must go into a new directory. This never installs or publishes.
The inherited Python/Qt runtime is not a from-source dependency build.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def validate_layout(root):
    required = [
        'AppRun', 'tihulu-satellite-center.desktop', 'tihulu-satellite-center.svg',
        'usr/bin/python3.12', 'usr/lib/python3.12/encodings/__init__.py',
        'usr/lib/python3.12/site-packages/numpy/__init__.py',
        'usr/lib/python3.12/site-packages/PyQt5/QtWebEngineWidgets.abi3.so',
        'usr/lib/python3.12/site-packages/PyQt5/QtWebChannel.abi3.so',
        'usr/lib/python3.12/site-packages/PyQt5/Qt5/libexec/QtWebEngineProcess',
        'usr/share/satellite-center/satellite_center/__main__.py',
    ]
    for name in required:
        if not (root / name).is_file():
            raise RuntimeError(f'Missing required packaged file: {name}')
    for name in ['AppRun', 'usr/bin/python3.12',
                 'usr/lib/python3.12/site-packages/PyQt5/Qt5/libexec/QtWebEngineProcess']:
        if not os.access(root / name, os.X_OK):
            raise RuntimeError(f'Packaged executable is not executable: {name}')
    if any(re.fullmatch(r'usr_\d+', p.name) for p in root.iterdir()):
        raise RuntimeError('Split usr_N layout is forbidden')
    run(['/bin/sh', '-n', str(root / 'AppRun')])


def tree_manifest(root):
    result = {}
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[name] = {'link': os.readlink(path)}
        elif path.is_file():
            result[name] = {'sha256': digest(path), 'executable': bool(path.stat().st_mode & 0o111)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-image', type=Path, required=True)
    parser.add_argument('--base-sha256', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--name', default='Tihulu_Satellite_Center-0.3.0-r12.1-local-x86_64.AppImage')
    args = parser.parse_args()
    if Path(args.name).name != args.name or not args.name.endswith('.AppImage'):
        parser.error('--name must be a plain .AppImage filename')
    source = Path(__file__).resolve().parents[2]
    base = args.base_image.resolve()
    if digest(base) != args.base_sha256.lower():
        raise RuntimeError('Base-image SHA256 mismatch')
    if not os.access(base, os.X_OK):
        raise RuntimeError('Base-image must be executable to query its runtime')
    output = args.output_dir.resolve()
    output.mkdir(mode=0o700)  # Refuse reusing an old payload/build directory.
    offset = int(subprocess.check_output([str(base), '--appimage-offset'], text=True).strip())
    with base.open('rb') as stream:
        stream.seek(offset)
        if stream.read(4) != b'hsqs':
            raise RuntimeError('AppImage offset does not point to squashfs')
        stream.seek(0)
        runtime = stream.read(offset)
    appdir = output / 'AppDir'
    with (output / 'extract-base.log').open('w') as log:
        run(['unsquashfs', '-no-progress', '-d', str(appdir), '-o', str(offset), str(base)],
            stdout=log, stderr=subprocess.STDOUT)
    # Preserve the inherited application outside the packaged tree. Overlay the
    # intended source LAST, so no old source or stale modules can replace it.
    old_app = appdir / 'usr/share/satellite-center'
    old_app.rename(output / 'previous-application')
    paths = subprocess.check_output(['git', '-C', str(source), 'ls-files', '-z', 'usr/']).decode().split('\0')
    application = {}
    for name in filter(None, paths):
        src = source / name
        mapped = name.replace('usr/share/tihulu-satellite-center/', 'usr/share/satellite-center/', 1)
        dst = appdir / mapped
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        application[mapped] = {'source': name, 'sha256': digest(src)}
    expected_app = {name for name in application if name.startswith('usr/share/satellite-center/')}
    actual_app = {p.relative_to(appdir).as_posix() for p in old_app.rglob('*') if p.is_file()}
    if actual_app != expected_app:
        raise RuntimeError('Unexpected or missing application files after source overlay')
    shutil.copy2(source / 'packaging/appimage/AppRun', appdir / 'AppRun')
    shutil.copy2(source / 'usr/share/icons/hicolor/scalable/apps/tihulu-satellite.svg',
                 appdir / 'tihulu-satellite-center.svg')
    desktop = (source / 'usr/share/applications/tihulu-satellite-center.desktop').read_text()
    desktop = re.sub(r'^Exec=.*$', 'Exec=AppRun', desktop, flags=re.M)
    desktop = re.sub(r'^Icon=.*$', 'Icon=tihulu-satellite-center', desktop, flags=re.M)
    desktop += '\nX-AppImage-Version=0.3.0-r12.1-local\n'
    (appdir / 'tihulu-satellite-center.desktop').write_text(desktop)
    (appdir / '.DirIcon').symlink_to('tihulu-satellite-center.svg')
    commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    patch = subprocess.check_output(['git', '-C', str(source), 'diff', '--binary', 'HEAD', '--', 'usr/'])
    (output / 'application.patch').write_bytes(patch)
    info = {'source_commit': commit, 'base_sha256': args.base_sha256.lower(),
            'application_patch_sha256': hashlib.sha256(patch).hexdigest(),
            'application_matches_commit': not bool(patch), 'candidate': args.name,
            'verification': 'Payload fidelity only; see separate runtime/UI test evidence.'}
    (appdir / 'build-info.json').write_text(json.dumps(info, indent=2) + '\n')
    validate_layout(appdir)
    payload = output / 'payload.squashfs'
    epoch = subprocess.check_output(['git', '-C', str(source), 'show', '-s', '--format=%ct', 'HEAD'], text=True).strip()
    env = dict(os.environ, SOURCE_DATE_EPOCH=epoch)
    with (output / 'mksquashfs.log').open('w') as log:
        run(['mksquashfs', str(appdir), str(payload), '-comp', 'zstd', '-b', '131072',
             '-all-root', '-no-xattrs', '-noappend', '-no-progress'], env=env, stdout=log, stderr=subprocess.STDOUT)
    image = output / args.name
    with image.open('xb') as destination, payload.open('rb') as stream:
        destination.write(runtime)
        shutil.copyfileobj(stream, destination)
    image.chmod(0o755)
    verify = output / 'verified'
    verify.mkdir()
    with (output / 'extract-built.log').open('w') as log:
        run([str(image), '--appimage-extract'], cwd=verify, stdout=log, stderr=subprocess.STDOUT)
    extracted = verify / 'squashfs-root'
    validate_layout(extracted)
    expected = tree_manifest(appdir)
    actual = tree_manifest(extracted)
    if expected != actual:
        failures = [name for name in expected.keys() | actual.keys() if expected.get(name) != actual.get(name)]
        raise RuntimeError(f'Final extraction differs: {failures[:20]}')
    for name, record in application.items():
        if digest(extracted / name) != record['sha256']:
            raise RuntimeError(f'Final application does not match source: {name}')
    image_digest = digest(image)
    (output / 'SHA256SUMS').write_text(f'{image_digest}  {image.name}\n')
    (output / 'application-manifest.json').write_text(json.dumps(application, indent=2) + '\n')
    (output / 'payload-manifest.json').write_text(json.dumps(actual, indent=2) + '\n')
    result = {**info, 'sha256': image_digest, 'bytes': image.stat().st_size,
              'application_files_verified': len(application), 'payload_entries_verified': len(actual),
              'runtime_offset': offset, 'image': str(image)}
    (output / 'build-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
