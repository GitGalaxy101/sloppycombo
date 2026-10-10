#!/usr/bin/env python3
"""Build a checked, repeatable portable Windows prototype without running Windows.

Usage: python scripts/package_windows.py --output dist/DeltaDash-v0.1.0-dev-windows.zip
Every upstream artifact has a locked SHA-256; TLS verification remains enabled.
The result includes the interpreter and pygame, never either game's files.
"""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = Path(__file__).with_name("windows-runtime.lock.json")
ZIP_TIME = (2026, 1, 1, 0, 0, 0)
MAX_ARTIFACT_SIZE = 100 * 1024 * 1024
MAX_EXPANDED_SIZE = 300 * 1024 * 1024
SYSTEM_DLLS = {
    "advapi32.dll", "bcrypt.dll", "cabinet.dll", "comctl32.dll", "comdlg32.dll",
    "crypt32.dll", "gdi32.dll", "imm32.dll", "iphlpapi.dll", "kernel32.dll",
    "mf.dll", "mfplat.dll", "mfreadwrite.dll", "msi.dll", "msvcrt.dll",
    "netapi32.dll", "ole32.dll", "oleaut32.dll", "propsys.dll", "rpcrt4.dll",
    "setupapi.dll", "shell32.dll", "shlwapi.dll", "user32.dll", "userenv.dll",
    "version.dll", "winmm.dll", "ws2_32.dll",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(artifact: dict, cache: Path, offline: bool) -> Path:
    path = cache / artifact["filename"]
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Unsafe cached artifact: {path}")
        if sha256(path) == artifact["sha256"]:
            return path
        raise ValueError(f"Checksum mismatch in cache: {path}; remove it and retry")
    if offline:
        raise FileNotFoundError(f"Offline cache is missing {path.name}")
    if not artifact["url"].startswith("https://"):
        raise ValueError("Only TLS-protected upstream artifacts are accepted")
    print(f"Downloading {path.name}", flush=True)
    with tempfile.NamedTemporaryFile(dir=cache, delete=False) as temporary:
        candidate = Path(temporary.name)
        try:
            request = urllib.request.Request(
                artifact["url"], headers={"User-Agent": "DeltaDash-portable-builder/0.1"}
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                if not response.geturl().startswith("https://"):
                    raise ValueError("Upstream download redirected to an insecure URL")
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_ARTIFACT_SIZE:
                        raise ValueError("Artifact exceeds the package size limit")
                    temporary.write(chunk)
            temporary.flush()
            if sha256(candidate) != artifact["sha256"]:
                raise ValueError(f"Checksum mismatch downloading {path.name}")
            if size != artifact["size"]:
                raise ValueError(f"Unexpected artifact size: {path.name}")
        except BaseException:
            candidate.unlink(missing_ok=True)
            raise
    candidate.replace(path)
    return path


def safe_relative(name: str) -> PurePosixPath:
    if "\\" in name or ":" in name or "\x00" in name:
        raise ValueError(f"Unsafe archive path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"Unsafe archive path: {name!r}")
    return path


def extract_runtime(archive: Path, destination: Path) -> None:
    """Copy regular runtime files only, stripping the upstream python/ prefix."""
    seen: set[str] = set()
    size = 0
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            path = safe_relative(member.name)
            if path.parts[0] != "python":
                raise ValueError(f"Unexpected runtime archive root: {member.name}")
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError(f"Runtime contains a link or special file: {member.name}")
            relative = PurePosixPath(*path.parts[1:])
            key = str(relative).lower()
            if key in seen:
                raise ValueError(f"Duplicate runtime file: {member.name}")
            seen.add(key)
            size += member.size
            if size > MAX_EXPANDED_SIZE:
                raise ValueError("Runtime exceeds the expanded-size limit")
            # Build tools, tkinter demos, pip, and upstream bytecode are unnecessary.
            if relative.parts[0] in {"include", "libs", "Scripts"}:
                continue
            if relative.parts[:2] == ("Lib", "site-packages"):
                continue
            if "__pycache__" in relative.parts or relative.suffix == ".pyc":
                continue
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = source.extractfile(member)
            if stream is None:
                raise ValueError(f"Cannot read runtime member: {member.name}")
            with stream, target.open("wb") as output:
                shutil.copyfileobj(stream, output)


def extract_wheel(archive: Path, destination: Path) -> None:
    seen: set[str] = set()
    size = 0
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            relative = safe_relative(member.filename)
            if member.is_dir():
                continue
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(f"Wheel contains a symbolic link: {member.filename}")
            key = str(relative).lower()
            if key in seen:
                raise ValueError(f"Duplicate wheel file: {member.filename}")
            seen.add(key)
            size += member.file_size
            if size > MAX_EXPANDED_SIZE:
                raise ValueError("Wheel exceeds the expanded-size limit")
            # The app uses pygame's runtime modules only. Developer examples,
            # test fixtures, and generated documentation contain unrelated assets
            # and nested archives; corresponding sources/notices remain bundled.
            if relative.parts[:2] in {
                ("pygame", "docs"), ("pygame", "examples"), ("pygame", "tests")
            }:
                continue
            # Our renderer supplies its own pixel lettering. Default/example/
            # fixture fonts have separate terms and are not runtime prerequisites.
            if relative.suffix.lower() in {".ttf", ".otf", ".woff", ".woff2"}:
                continue
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(member) as content, target.open("wb") as output:
                shutil.copyfileobj(content, output)


def collect_notices(artifacts: dict[str, Path], destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(artifacts["pygame_source"], "r:gz") as source:
        for member in source:
            path = safe_relative(member.name)
            if not member.isfile():
                continue
            is_notice = (
                "/docs/licenses/" in str(path) or str(path).endswith("/docs/LGPL.txt")
            )
            if is_notice:
                stream = source.extractfile(member)
                if stream is None:
                    raise ValueError(f"Cannot read notice: {member.name}")
                with stream, (destination / ("pygame-" + path.name)).open("wb") as output:
                    shutil.copyfileobj(stream, output)
    for key in ("sdl_ttf_notices", "sdl_mixer_notices"):
        with zipfile.ZipFile(artifacts[key]) as source:
            for name in source.namelist():
                path = safe_relative(name)
                if path.name.startswith("LICENSE") and "/x86/" not in name:
                    target = destination / (key + "-" + path.name)
                    target.write_bytes(source.read(name))
    for key, artifact in artifacts.items():
        if key.startswith("python_notice_"):
            shutil.copyfile(artifact, destination / artifact.name)


def bundle_pygame_source(archive: Path, target: Path) -> None:
    """Include corresponding library sources without unused sample fonts."""
    with target.open("wb") as output, gzip.GzipFile(
        filename="", mode="wb", fileobj=output, mtime=0
    ) as compressed, tarfile.open(
        fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT
    ) as destination, tarfile.open(archive, "r:gz") as source:
        for member in source:
            path = safe_relative(member.name)
            if not (member.isdir() or member.isfile()):
                raise ValueError(f"Unsafe source archive member: {member.name}")
            if path.suffix.lower() in {".ttf", ".otf", ".woff", ".woff2"}:
                continue
            info = tarfile.TarInfo(str(path))
            info.mtime = 0
            info.mode = 0o755 if member.isdir() else 0o644
            info.type = tarfile.DIRTYPE if member.isdir() else tarfile.REGTYPE
            info.size = member.size if member.isfile() else 0
            if member.isfile():
                stream = source.extractfile(member)
                if stream is None:
                    raise ValueError(f"Cannot read source member: {member.name}")
                with stream:
                    destination.addfile(info, stream)
            else:
                destination.addfile(info)


def copy_project(source: Path, destination: Path, suffixes: set[str]) -> None:
    if not source.is_dir():
        raise FileNotFoundError(f"Missing project directory: {source}")
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Refusing to bundle a project symlink: {path}")
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if "__pycache__" in path.parts:
            continue
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)


def pe_imports(content: bytes) -> tuple[int, list[str]]:
    """Read the PE import table without adding a packaging dependency."""
    if content[:2] != b"MZ":
        raise ValueError("Native runtime file has no DOS/PE signature")
    pe = struct.unpack_from("<I", content, 0x3C)[0]
    if content[pe : pe + 4] != b"PE\0\0":
        raise ValueError("Native runtime file has no PE signature")
    machine, sections = struct.unpack_from("<HH", content, pe + 4)
    optional_size = struct.unpack_from("<H", content, pe + 20)[0]
    optional = pe + 24
    if struct.unpack_from("<H", content, optional)[0] != 0x20B:
        raise ValueError("Expected a 64-bit PE executable")
    import_rva, _ = struct.unpack_from("<II", content, optional + 112 + 8)
    mapping = []
    for index in range(sections):
        offset = optional + optional_size + index * 40
        virtual_size, virtual_rva, raw_size, raw_offset = struct.unpack_from(
            "<IIII", content, offset + 8
        )
        mapping.append((virtual_rva, max(virtual_size, raw_size), raw_offset))

    def position(rva: int) -> int:
        for base, size, offset in mapping:
            if base <= rva < base + size:
                return offset + rva - base
        raise ValueError("PE import table points outside its sections")

    dependencies = []
    if import_rva:
        offset = position(import_rva)
        while any(content[offset : offset + 20]):
            name_offset = position(struct.unpack_from("<I", content, offset + 12)[0])
            end = content.index(b"\0", name_offset)
            dependencies.append(content[name_offset:end].decode("ascii").lower())
            offset += 20
    return machine, dependencies


def verify_package(package: Path) -> dict:
    required = (
        "runtime/pythonw.exe", "runtime/python.exe", "runtime/python312.dll",
        "runtime/python312._pth", "runtime/vcruntime140.dll", "runtime/LICENSE.txt",
        "runtime/Lib/encodings/__init__.py", "runtime/Lib/site-packages/pygame/__init__.py",
        "runtime/Lib/site-packages/pygame/SDL2.dll", "launch.py", "launch.cmd",
        "app/deltadash/app.py", "app/deltadash/generated_design.py", "sheets",
        "licenses/pygame-LGPL.txt", "sources/pygame_ce-2.5.8-source-no-font.tar.gz",
    )
    for relative in required:
        if not (package / relative).exists():
            raise ValueError(f"Portable package is incomplete: {relative}")
    native = [
        file for file in package.rglob("*")
        if file.is_file() and file.suffix.lower() in {".exe", ".dll", ".pyd"}
    ]
    bundled_dlls = {file.name.lower() for file in native}
    dependency_count = 0
    for file in native:
        machine, dependencies = pe_imports(file.read_bytes())
        if machine != 0x8664:
            raise ValueError(f"Wrong native architecture: {file}")
        for dependency in dependencies:
            dependency_count += 1
            if (
                dependency not in bundled_dlls
                and dependency not in SYSTEM_DLLS
                and not dependency.startswith(("api-ms-win-", "ext-ms-win-"))
            ):
                raise ValueError(f"Missing native dependency: {file.name} -> {dependency}")
    for file in (package / "app").rglob("*.py"):
        ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
    ast.parse((package / "launch.py").read_text(encoding="utf-8"))
    return {
        "native_files": len(native), "native_imports_checked": dependency_count,
        "windows_execution": "not tested; build host is Linux",
    }


def write_zip(package: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".partial")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for file in sorted(package.rglob("*")):
                if not file.is_file():
                    continue
                info = zipfile.ZipInfo("DeltaDash/" + file.relative_to(package).as_posix(), ZIP_TIME)
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, file.read_bytes(), compresslevel=9)
        with zipfile.ZipFile(temporary) as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError(f"ZIP CRC validation failed: {bad}")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def build(output: Path, cache: Path, offline: bool) -> dict:
    # Sheets are the design source of truth. Stop before downloads or packaging
    # if references are invalid or generated declarations are stale.
    subprocess.run([sys.executable, str(ROOT / "tools" / "preflight.py")], cwd=ROOT, check=True)
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "generate_design.py"), "--check"], cwd=ROOT, check=True
    )
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    cache.mkdir(parents=True, exist_ok=True)
    artifacts = {key: fetch(value, cache, offline) for key, value in lock["artifacts"].items()}
    with tempfile.TemporaryDirectory(prefix="deltadash-build-", dir=cache) as temporary:
        package = Path(temporary) / "DeltaDash"
        runtime = package / "runtime"
        extract_runtime(artifacts["python"], runtime)
        extract_wheel(artifacts["pygame"], runtime / "Lib" / "site-packages")
        (runtime / "python312._pth").write_text(
            ".\nLib\nDLLs\nLib/site-packages\n", encoding="utf-8"
        )
        copy_project(ROOT / "src", package / "app", {".py"})
        copy_project(ROOT / "sheets", package / "sheets", {".csv", ".json", ".md"})
        shutil.copyfile(Path(__file__).with_name("launch_windows.py"), package / "launch.py")
        (package / "launch.cmd").write_bytes(
            b'@echo off\r\n"%~dp0runtime\\pythonw.exe" -I "%~dp0launch.py" %*\r\n'
        )
        collect_notices(artifacts, package / "licenses")
        (package / "sources").mkdir()
        bundle_pygame_source(
            artifacts["pygame_source"], package / "sources" / "pygame_ce-2.5.8-source-no-font.tar.gz"
        )
        shutil.copyfile(LOCK_PATH, package / "sources" / LOCK_PATH.name)
        shutil.copyfile(ROOT / "docs" / "RUNTIME-LICENSES.md", package / "licenses" / "RUNTIME-LICENSES.md")
        copy_project(ROOT / "docs", package / "docs", {".md"})
        for name in ("README.md", "MODLOG.md", "CREDITS.md", "pyproject.toml", "uv.lock", ".python-version", "melty.json"):
            if (ROOT / name).is_file():
                shutil.copyfile(ROOT / name, package / name)
        checks = verify_package(package)
        manifest = {
            "name": "DeltaDash", "version": "0.1.0-dev", "platform": "windows-x86_64",
            "python": lock["python_version"], "pygame_ce": lock["pygame_version"],
            "checks": checks, "upstream": lock["artifacts"],
            "files": {
                path.relative_to(package).as_posix(): sha256(path)
                for path in sorted(package.rglob("*")) if path.is_file()
            },
        }
        (package / "BUILD-MANIFEST.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        write_zip(package, output)
    digest = sha256(output)
    output.with_suffix(output.suffix + ".sha256").write_text(
        digest + "  " + output.name + "\n", encoding="utf-8"
    )
    return {"archive": str(output), "sha256": digest, "size": output.stat().st_size, **checks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "DeltaDash-v0.1.0-dev-windows.zip")
    parser.add_argument("--cache-dir", type=Path, default=Path(tempfile.gettempdir()) / "deltadash-package-cache")
    parser.add_argument("--offline", action="store_true", help="Use only checksum-validated cached downloads")
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.output.resolve(), args.cache_dir.resolve(), args.offline), indent=2))
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        print(f"Packaging failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
