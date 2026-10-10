"""Portable Windows entry point; copied to DeltaDash/launch.py by the packager."""

from __future__ import annotations

import os
from pathlib import Path
import re
import sys
import traceback


class DiscoveryError(ValueError):
    """A required installed game could not be located in Steam's manifests."""


def parse_keyvalues(text: str) -> dict:
    """Parse bounded Valve KeyValues, keeping Unicode paths and literal slashes."""
    if len(text) > 2 * 1024 * 1024 or "\x00" in text:
        raise ValueError("Steam manifest exceeds the supported size or contains NUL")
    tokens = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline + 1
            continue
        if char in "{}":
            tokens.append(("brace", char))
            index += 1
        elif char == '"':
            index += 1
            value = []
            while index < len(text) and text[index] != '"':
                if text[index] == "\\" and index + 1 < len(text) and text[index + 1] in {'"', "\\"}:
                    index += 1
                value.append(text[index])
                index += 1
            if index == len(text):
                raise ValueError("Unterminated quoted Steam manifest value")
            tokens.append(("string", "".join(value)))
            index += 1
        else:
            start = index
            while index < len(text) and not text[index].isspace() and text[index] not in '{}"':
                index += 1
            tokens.append(("string", text[start:index]))
        if len(tokens) > 100_000:
            raise ValueError("Steam manifest has too many fields")
    position = 0

    def object_value(nested: bool = False, depth: int = 0) -> dict:
        nonlocal position
        if depth > 16:
            raise ValueError("Steam manifest nesting exceeds its supported limit")
        result = {}
        while position < len(tokens):
            kind, key = tokens[position]
            if kind == "brace" and key == "}":
                if not nested:
                    raise ValueError("Unexpected closing Steam manifest brace")
                position += 1
                return result
            if kind != "string" or not key:
                raise ValueError("Expected a Steam manifest field name")
            key = key.casefold()
            if key in result:
                raise ValueError("Duplicate Steam manifest field")
            position += 1
            if position >= len(tokens):
                raise ValueError("Steam manifest field has no value")
            kind, value = tokens[position]
            position += 1
            if kind == "brace" and value == "{":
                result[key] = object_value(True, depth + 1)
            elif kind == "string":
                result[key] = value
            else:
                raise ValueError("Expected a Steam manifest field value")
        if nested:
            raise ValueError("Unclosed Steam manifest object")
        return result

    return object_value()


def read_manifest(path: Path) -> dict:
    # Only caller-selected libraryfolders.vdf and appmanifest_*.acf are opened;
    # Steam user data, account files, and login configuration are never read.
    if path.is_symlink():
        raise ValueError("Steam manifest is a symbolic link")
    with path.open("rb") as source:
        data = source.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("Steam manifest exceeds the supported size")
    return parse_keyvalues(data.decode("utf-8-sig"))


def registry_steam_roots() -> list[Path]:
    """Read Steam's install paths without changing any registry key."""
    try:
        import winreg
    except ImportError:
        return []
    locations = (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath", winreg.KEY_READ),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath", winreg.KEY_READ | winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath", winreg.KEY_READ),
    )
    roots = []
    for hive, key, value, access in locations:
        try:
            with winreg.OpenKey(hive, key, 0, access) as handle:
                path, _ = winreg.QueryValueEx(handle, value)
            if isinstance(path, str) and path and "\x00" not in path:
                roots.append(Path(path))
        except OSError:
            continue
    return roots


def steam_libraries(roots: list[Path] | None = None) -> list[Path]:
    if roots is None:
        roots = registry_steam_roots() + [
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Steam",
        ]
    libraries = []
    seen = set()

    def add(path: Path) -> None:
        try:
            resolved = path.resolve()
            key = str(resolved).casefold()
            if key not in seen and (resolved / "steamapps").is_dir():
                seen.add(key)
                libraries.append(resolved)
        except OSError:
            pass

    for root in roots:
        root = Path(root)
        add(root)
        try:
            entries = read_manifest(root / "steamapps" / "libraryfolders.vdf").get("libraryfolders", {})
        except (OSError, UnicodeError, ValueError):
            continue
        if not isinstance(entries, dict):
            continue
        for number, entry in entries.items():
            if not number.isdigit():
                continue
            path = entry.get("path") if isinstance(entry, dict) else entry
            if isinstance(path, str) and path:
                add(Path(path))
    return libraries


def installed_steam_games(roots: list[Path] | None = None) -> list[tuple[str, Path]]:
    games = []
    for library in steam_libraries(roots):
        apps = library / "steamapps"
        try:
            manifests = sorted(apps.glob("appmanifest_*.acf"))
        except OSError:
            continue
        if len(manifests) > 4096:
            continue
        for manifest in manifests:
            try:
                state = read_manifest(manifest).get("appstate", {})
                if not isinstance(state, dict):
                    continue
                name, directory = state.get("name"), state.get("installdir")
                if not isinstance(name, str) or not isinstance(directory, str):
                    continue
                # installdir is a single directory name, never a supplied path.
                if not directory or directory in {".", ".."} or any(char in directory for char in "/\\:"):
                    continue
                path = apps / "common" / directory
                if path.is_dir():
                    games.append((name, path))
            except (OSError, UnicodeError, ValueError):
                continue
    return games


def game_arguments(argv: list[str], roots: list[Path] | None = None) -> list[str]:
    """Supply missing installation paths; explicit Melty/player paths win."""
    supplied = lambda flag: any(arg == flag or arg.startswith(flag + "=") for arg in argv)
    need_deltarune, need_gd = not supplied("--game-dir"), not supplied("--gd-dir")
    if not (need_deltarune or need_gd) or "--help" in argv or "-h" in argv:
        return list(argv)
    games = installed_steam_games(roots)
    result = list(argv)
    missing = []
    if need_deltarune:
        from deltadash.deltarune_content import ContentError, discover_data_file

        candidates = []
        for name, directory in games:
            normalized = " ".join(name.casefold().split())
            if normalized == "deltarune":
                candidates.append((0, directory))
            elif re.fullmatch(r"deltarune(?:[ :\-]*\(?(?:chapters?\s*1\s*[&+]\s*2\s*)?demo\)?)", normalized):
                candidates.append((1, directory))
        found = None
        for _, directory in sorted(candidates, key=lambda candidate: candidate[0]):
            try:
                # Pure stdlib discovery prefers Chapter 2 and rejects ambiguous
                # multi-chapter archives. Actual content is validated by the app.
                found = discover_data_file(directory)
                break
            except (OSError, ContentError):
                continue
        if found is None:
            missing.append("DELTARUNE with a readable Chapter 2 data.win")
        else:
            result.extend(("--game-dir", str(found)))
    if need_gd:
        found = next((directory for name, directory in games if " ".join(name.casefold().split()) == "geometry dash"), None)
        if found is None:
            missing.append("Geometry Dash")
        else:
            result.extend(("--gd-dir", str(found)))
    if missing:
        raise DiscoveryError(
            "Steam discovery could not find " + " and ".join(missing) + ".\n\n"
            "Install the games in Steam, or open a terminal in the DeltaDash folder and run:\n"
            'launch.cmd --game-dir "C:\\path\\to\\DELTARUNE\\chapter2\\data.win" '
            '--gd-dir "C:\\path\\to\\Geometry Dash"\n\n'
            "For a non-Steam installation, supply these paths explicitly. Game files are never modified."
        )
    return result


def launch() -> int:
    sys.dont_write_bytecode = True
    package = Path(__file__).resolve().parent
    sys.path.insert(0, str(package / "app"))
    log = None
    try:
        # pythonw has no console: retain diagnostics outside both game installs.
        if sys.stdout is None or sys.stderr is None:
            local = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            directory = local / "DeltaDashArcade" / "logs"
            directory.mkdir(parents=True, exist_ok=True)
            log = (directory / "launcher.log").open("a", encoding="utf-8")
            if sys.stdout is None:
                sys.stdout = log
            if sys.stderr is None:
                sys.stderr = log
        from deltadash.app import main

        return main(game_arguments(sys.argv[1:])) or 0
    except Exception:
        details = traceback.format_exc()
        if sys.stderr is not None:
            print(details, file=sys.stderr, flush=True)
        if os.name == "nt":
            import ctypes

            message = "DeltaDash could not start.\n\n" + details[-1800:]
            if log is not None:
                message += "\n\nLog: " + log.name
            ctypes.windll.user32.MessageBoxW(None, message, "DeltaDash prototype", 0x10)
        return 1
    finally:
        if log is not None:
            log.flush()


if __name__ == "__main__":
    raise SystemExit(launch())
