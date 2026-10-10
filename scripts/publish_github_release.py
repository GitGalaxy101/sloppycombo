"""Publish the requested GitHub prototype after CI builds and tests it."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[1]
TAG = 'v0.1.0-dev'
REPOSITORY = 'GitGalaxy101/sloppycombo'
RELEASE_ID = 408543744


def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', path], text=True))


def main():
    if os.environ.get('GITHUB_REPOSITORY') != REPOSITORY:
        raise SystemExit('This publisher is restricted to the requested repository.')
    if os.environ.get('GITHUB_REF') != f'refs/tags/{TAG}':
        raise SystemExit('This publisher must run for the requested prototype tag.')

    archive = ROOT / 'dist' / f'DeltaDash-{TAG}-windows.zip'
    checksum = archive.with_suffix(archive.suffix + '.sha256')
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if checksum.read_text().split() != [digest, archive.name]:
        raise SystemExit('The package checksum does not match its sidecar.')
    with zipfile.ZipFile(archive) as package:
        if package.testzip() is not None:
            raise SystemExit('Package ZIP verification failed.')
        manifest = json.loads(package.read('DeltaDash/BUILD-MANIFEST.json'))
        if manifest['version'] != TAG.removeprefix('v'):
            raise SystemExit('Package version does not match the release tag.')
        for name, expected in manifest['files'].items():
            if hashlib.sha256(package.read('DeltaDash/' + name)).hexdigest() != expected:
                raise SystemExit(f'Package manifest verification failed: {name}')

    endpoint = f'repos/{REPOSITORY}/releases/{RELEASE_ID}'
    release = api(endpoint)
    if release['tag_name'] != TAG or not release['draft'] or not release['prerelease']:
        raise SystemExit('Only the existing development prerelease draft may be published.')
    names = {asset['name'] for asset in release['assets']}
    if names.intersection((archive.name, checksum.name)):
        raise SystemExit('Release already has an asset with this name; review it before replacing.')
    subprocess.run(
        ['gh', 'release', 'upload', TAG, str(archive), str(checksum), '--repo', REPOSITORY],
        check=True,
    )
    assets = {asset['name']: asset for asset in api(endpoint)['assets']}
    for path in (archive, checksum):
        asset = assets.get(path.name)
        if not asset or asset['state'] != 'uploaded' or asset['size'] != path.stat().st_size:
            raise SystemExit('Uploaded asset verification failed; leaving the release as a draft.')
        with path.open('rb') as stream:
            expected = 'sha256:' + hashlib.file_digest(stream, 'sha256').hexdigest()
        if asset.get('digest') and asset['digest'] != expected:
            raise SystemExit('GitHub asset checksum mismatch; leaving the release as a draft.')

    notes = Path(os.environ['RUNNER_TEMP']) / 'deltadash-release-notes.md'
    notes.write_text(
        (ROOT / 'docs' / 'GITHUB-RELEASE.md').read_text()
        + f'\nSHA-256: `{digest}`.\n',
        encoding='utf-8',
    )
    subprocess.run(
        ['gh', 'release', 'edit', TAG, '--repo', REPOSITORY, '--notes-file', str(notes),
         '--draft=false', '--prerelease', '--latest=false'],
        check=True,
    )
    published = api(endpoint)
    if published['draft']:
        raise SystemExit('GitHub did not publish the prerelease.')
    print(published['html_url'])


if __name__ == '__main__':
    main()
