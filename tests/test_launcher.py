"""Steam discovery uses local manifests, never Steam account configuration."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "deltadash_windows_launcher", Path(__file__).resolve().parents[1] / "scripts" / "launch_windows.py"
)
launcher = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(launcher)


def quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def library(path: Path) -> Path:
    (path / "steamapps" / "common").mkdir(parents=True)
    return path


def install(root: Path, appid: str, name: str, directory: str, *, archive: str | None = None) -> Path:
    game = root / "steamapps" / "common" / directory
    game.mkdir(parents=True)
    fields = {"appid": appid, "name": name, "installdir": directory}
    body = "\n".join(quoted(key) + " " + quoted(value) for key, value in fields.items())
    (root / "steamapps" / f"appmanifest_{appid}.acf").write_text(
        '"AppState"\n{\n' + body + "\n}\n", encoding="utf-8"
    )
    if archive is not None:
        data = game / archive
        data.parent.mkdir(parents=True, exist_ok=True)
        data.write_bytes(b"synthetic discovery marker; never a game archive")
    return game


def test_double_click_finds_separate_library_and_prefers_full_chapter_two(tmp_path):
    primary = library(tmp_path / "Steam")
    secondary = library(tmp_path / "Steam Library Ω")
    full = install(secondary, "100", "DELTARUNE", "DELTARUNE", archive="chapter2/data.win")
    (full / "chapter1").mkdir()
    (full / "chapter1" / "data.win").write_bytes(b"chapter-one discovery marker")
    install(primary, "101", "DELTARUNE Demo", "DELTARUNE Demo", archive="data.win")
    gd = install(primary, "322170", "Geometry Dash", "Geometry Dash")
    (primary / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders" { "0" { "path" ' + quoted(str(primary))
        + ' } "1" { "path" ' + quoted(str(secondary)) + ' "apps" { "100" "1" } } }',
        encoding="utf-8",
    )
    # A login file is neither necessary nor a discovery input.
    (primary / "config").mkdir()
    (primary / "config" / "loginusers.vdf").write_text("not valid KeyValues")
    result = launcher.game_arguments([], [primary])
    assert result == ["--game-dir", str((full / "chapter2/data.win").resolve()), "--gd-dir", str(gd)]
    assert (full / "chapter2/data.win").read_bytes() == b"synthetic discovery marker; never a game archive"


def test_legacy_libraryfolders_and_bom_are_supported(tmp_path):
    primary = library(tmp_path / "Steam")
    other = library(tmp_path / "legacy library")
    (primary / "steamapps" / "libraryfolders.vdf").write_text(
        '\ufeff"LibraryFolders" { "TimeNextStatsReport" "1234" "1" ' + quoted(str(other)) + " }",
        encoding="utf-8",
    )
    assert launcher.steam_libraries([primary]) == [primary.resolve(), other.resolve()]


def test_windows_escapes_comments_and_unicode_survive_keyvalues():
    parsed = launcher.parse_keyvalues(
        '// installed path\n"libraryfolders" { "1" { "path" "D:\\\\Steam Ω" '
        '"note" "A \\"quoted\\" name" } }'
    )
    assert parsed["libraryfolders"]["1"]["path"] == "D:\\Steam Ω"
    assert parsed["libraryfolders"]["1"]["note"] == 'A "quoted" name'


@pytest.mark.parametrize("name", ["DELTARUNE Demo", "DELTARUNE (Chapter 1 & 2 DEMO)"])
def test_demo_is_used_when_full_copy_has_no_readable_archive(tmp_path, name):
    root = library(tmp_path / "Steam")
    install(root, "1", "DELTARUNE", "broken full")
    demo = install(root, "2", name, "demo", archive="data.win")
    gd = install(root, "3", "Geometry Dash", "gd")
    assert launcher.game_arguments([], [root]) == ["--game-dir", str((demo / "data.win").resolve()), "--gd-dir", str(gd)]


def test_supplied_melty_paths_are_preserved_without_discovery(monkeypatch):
    def unexpected(*args):
        raise AssertionError("Explicit launch paths must not trigger discovery")
    monkeypatch.setattr(launcher, "installed_steam_games", unexpected)
    args = ["--game-dir", "D:\\Real Game", "--gd-dir=D:\\Geometry Dash", "--mute"]
    assert launcher.game_arguments(args) == args
    assert launcher.game_arguments(["--help"]) == ["--help"]


def test_only_missing_companion_path_is_added(tmp_path):
    root = library(tmp_path / "Steam")
    gd = install(root, "322170", "Geometry Dash", "gd")
    args = ["--game-dir", "C:\\non-Steam\\data.win", "--check-content"]
    assert launcher.game_arguments(args, [root]) == args + ["--gd-dir", str(gd)]


@pytest.mark.parametrize("directory", ["../outside", "..", "/outside", "C:\\outside", "a/b"])
def test_manifest_cannot_redirect_installation_outside_common(tmp_path, directory):
    root = library(tmp_path / "Steam")
    body = '"AppState" { "name" "DELTARUNE" "installdir" ' + quoted(directory) + " }"
    (root / "steamapps" / "appmanifest_100.acf").write_text(body)
    assert launcher.installed_steam_games([root]) == []


def test_missing_games_report_exact_manual_launch_command(tmp_path):
    root = library(tmp_path / "Steam")
    install(root, "1", "DELTARUNE Fan Game", "fan", archive="data.win")
    install(root, "2", "Geometry Dash SubZero", "subzero")
    with pytest.raises(launcher.DiscoveryError) as caught:
        launcher.game_arguments([], [root])
    assert "DELTARUNE" in str(caught.value)
    assert "Geometry Dash" in str(caught.value)
    assert "launch.cmd --game-dir" in str(caught.value)
    assert "--gd-dir" in str(caught.value)


def test_malformed_library_file_does_not_hide_primary_library(tmp_path):
    root = library(tmp_path / "Steam")
    (root / "steamapps" / "libraryfolders.vdf").write_text('"libraryfolders" {')
    assert launcher.steam_libraries([root]) == [root.resolve()]


@pytest.mark.parametrize("text", ['"a"', '"a" {', '"a" "1" "A" "2"', '"unclosed', '"a" "\x00"'])
def test_invalid_manifest_is_rejected(text):
    with pytest.raises(ValueError):
        launcher.parse_keyvalues(text)


def test_registry_queries_are_read_only_and_support_32_bit_view(tmp_path, monkeypatch):
    calls = []
    fake = types.SimpleNamespace(HKEY_CURRENT_USER=1, HKEY_LOCAL_MACHINE=2, KEY_READ=0x20019, KEY_WOW64_32KEY=0x200)

    class Handle:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def open_key(hive, key, reserved, access):
        calls.append((hive, key, reserved, access))
        return Handle()

    fake.OpenKey = open_key
    fake.QueryValueEx = lambda handle, value: (str(tmp_path / "Steam"), 1)
    monkeypatch.setitem(sys.modules, "winreg", fake)
    roots = launcher.registry_steam_roots()
    assert roots == [tmp_path / "Steam"] * 3
    assert all(access & fake.KEY_READ == fake.KEY_READ for _, _, _, access in calls)
    assert any(access & fake.KEY_WOW64_32KEY for _, _, _, access in calls)
    assert all("Valve\\Steam" in key for _, key, _, _ in calls)


def test_oversized_manifest_is_rejected(tmp_path):
    manifest = tmp_path / "appmanifest_1.acf"
    manifest.write_bytes(b" " * (2 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="size"):
        launcher.read_manifest(manifest)
