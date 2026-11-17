#!/usr/bin/env python3
"""Package and publish an immutable, reproducible patched mod build."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def api(route):
    return json.loads(subprocess.check_output(['gh', 'api', route]))


def package():
    config = json.loads((ROOT / 'mosslight-build.json').read_text())
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT).strip():
        raise ValueError('Refusing to package an uncommitted source tree')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    jar = ROOT / 'build/libs' / config['artifact']
    with zipfile.ZipFile(jar) as source:
        metadata = json.loads(source.read('fabric.mod.json'))
        if metadata['id'] != config['mod_id'] or metadata['version'] != config['version']:
            raise ValueError('Built mod identity/version differs from the source manifest')
        source.read('MOSSLIGHT-PATCHES.txt')
        if not any('LICENSE' in name.upper() for name in source.namelist()):
            raise ValueError('Built JAR has no license notice')
    output = ROOT / 'release-dist'
    output.mkdir(exist_ok=True)
    (output / jar.name).write_bytes(jar.read_bytes())
    archive = output / (jar.stem + '-source.tar.gz')
    tracked = subprocess.check_output(['git', 'ls-files', '-z', '--recurse-submodules'], cwd=ROOT).decode().split('\0')
    with archive.open('wb') as stream, gzip.GzipFile(fileobj=stream, filename='', mtime=0) as compressed, tarfile.open(fileobj=compressed, mode='w|') as tar:
        for name in sorted(filter(None, tracked)):
            path = ROOT / name
            if not path.is_file() or path.is_symlink():
                raise ValueError('Source archive contains a non-regular file: ' + name)
            entry = tarfile.TarInfo('source/' + name)
            entry.size = path.stat().st_size
            entry.mode = 0o755 if os.access(path, os.X_OK) else 0o644
            with path.open('rb') as content:
                tar.addfile(entry, content)
    provenance = config | {'commit': commit, 'artifacts': {name: digest(output / name) for name in (jar.name, archive.name)}}
    (output / 'BUILD.json').write_text(json.dumps(provenance, sort_keys=True, indent=2) + '\n')
    (output / 'SHA256SUMS').write_text(''.join(digest(output / name) + '  ' + name + '\n' for name in sorted((jar.name, archive.name, 'BUILD.json'))))
    print('Packaged ' + config['version'] + ' at ' + commit)
    return output, provenance


def publish(output, info):
    tag = info['tag']
    if os.environ.get('GITHUB_REF') != 'refs/tags/' + tag:
        raise ValueError('Publication requires the matching immutable build tag')
    repo = os.environ['GITHUB_REPOSITORY']
    if repo != info['repository']:
        raise ValueError('Refusing to publish to a different repository')
    releases = api(f'repos/{repo}/releases?per_page=100')
    existing = next((item for item in releases if item['tag_name'] == tag), None)
    if existing and not existing['draft']:
        print('Build release is already published; leaving it unchanged')
        return
    if existing and existing['target_commitish'] != info['commit']:
        raise ValueError('Release draft belongs to another commit')
    notes = ('Unofficial patched build.\n\n' + info['change'] + '\n\n'
             + 'Upstream base: `' + info['upstream_commit'] + '`.\n\n'
             + 'Source commit: `' + info['commit'] + '`.\n\n'
             + 'The complete source archive includes build files and submodule sources. '
             + 'Two clean builds were compared in CI; checksums and provenance are attached.\n')
    with tempfile.TemporaryDirectory() as temporary:
        body = Path(temporary) / 'notes.txt'
        body.write_text(notes)
        if existing is None:
            subprocess.run(['gh', 'release', 'create', tag, '--repo', repo, '--verify-tag',
                            '--target', info['commit'], '--title', info['name'] + ' ' + info['version'],
                            '--draft', '--prerelease', '--notes-file', str(body)], check=True)
        files = sorted(path for path in output.iterdir() if path.is_file())
        subprocess.run(['gh', 'release', 'upload', tag, '--repo', repo, '--clobber', *(str(path) for path in files)], check=True)
        assets = next(item for item in api(f'repos/{repo}/releases?per_page=100') if item['tag_name'] == tag)['assets']
        expected = {path.name: 'sha256:' + digest(path) for path in files}
        if {asset['name']: asset.get('digest') for asset in assets} != expected:
            raise ValueError('Uploaded artifact set/digests differ from the validated build')
        subprocess.run(['gh', 'release', 'edit', tag, '--repo', repo, '--draft=false'], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    output, info = package()
    if args.publish:
        publish(output, info)
