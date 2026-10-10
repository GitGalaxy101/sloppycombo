"""Synthetic fixtures only: no Geometry Dash levels, music, or resources.

Fixtures exercise documented encodings and consumer contracts. They do not
establish compatibility with an installed retail copy, absent in cloud.
"""

import base64
import gzip
import hashlib
from pathlib import Path
import plistlib
import xml.etree.ElementTree as ET
import zlib

import pytest

from deltadash import gd_content as gd


SYNTHETIC_COURSE = "kA2,0,kA4,0,kA13,0;1,1,2,150,3,15;1,8,2,270,3,15;1,1,2,360,3,45;"
SYNTHETIC_OPENING = SYNTHETIC_COURSE + "1,13,2,600,3,90;1,901,2,900,3,15,28,200;"


def synthetic_compressed(raw, *, official=False):
    # This mirrors the *documented wire format*, not any game level data.
    data = base64.urlsafe_b64encode(gzip.compress(raw.encode(), mtime=0)).decode()
    return data[13:] if official else data


def synthetic_save(records, *, created=True):
    root = ET.Element("plist", version="1.0", gjver="2.0")
    top = ET.SubElement(root, "dict")
    ET.SubElement(top, "k").text = "LLM_01" if created else "GLM_03"
    entries = ET.SubElement(top, "d")
    for index, record in enumerate(records):
        ET.SubElement(entries, "k").text = f"k_{index}"
        entry = ET.SubElement(entries, "d")
        for key, val in record.items():
            ET.SubElement(entry, "k").text = key
            ET.SubElement(entry, "s").text = str(val)
    data = base64.urlsafe_b64encode(gzip.compress(ET.tostring(root), mtime=0))
    return bytes(byte ^ 11 for byte in data)


def parse(raw=SYNTHETIC_COURSE, **kwargs):
    return gd.parse_level(raw, name="Explicitly synthetic fixture", music_path=Path("synthetic-not-shipped.mp3"),
                          source=Path("synthetic-not-shipped.plist"), **kwargs)


def fake_install(tmp_path, *, raw=SYNTHETIC_OPENING, audio=True):
    resource = tmp_path / "Geometry Dash" / "Resources"
    resource.mkdir(parents=True)
    (resource / "LevelData.plist").write_bytes(plistlib.dumps({"1": synthetic_compressed(raw, official=True)}))
    if audio:
        (resource / "StereoMadness.mp3").write_bytes(b"EXPLICITLY SYNTHETIC AUDIO PATH FIXTURE, NOT AN MP3")
    return resource.parent


def test_documented_official_gzip_prefix_and_unpadded_local_decode():
    assert gd.decode_level_data(synthetic_compressed(SYNTHETIC_COURSE, official=True), official=True) == SYNTHETIC_COURSE
    assert gd.decode_level_data(synthetic_compressed(SYNTHETIC_COURSE).rstrip("=")) == SYNTHETIC_COURSE
    assert gd.decode_level_data(SYNTHETIC_COURSE) == SYNTHETIC_COURSE
    encoded = base64.urlsafe_b64encode(zlib.compress(SYNTHETIC_COURSE.encode())).decode()
    assert gd.decode_level_data(encoded) == SYNTHETIC_COURSE


def test_real_coordinate_contract_and_identity_are_preserved():
    level = parse()
    assert [(obj.object_id, obj.x, obj.y, obj.kind) for obj in level.objects] == [
        (1, 150, 15, "block"), (8, 270, 15, "spike"), (1, 360, 45, "block")]
    assert level.objects[0].width == level.objects[0].height == 30
    assert level.sha256 == hashlib.sha256(SYNTHETIC_COURSE.encode()).hexdigest()
    assert level.length == 555
    assert not level.is_segment


def test_official_segment_stops_at_first_mode_change_and_is_explicit():
    level = parse(SYNTHETIC_OPENING, allow_cube_segment=True)
    assert level.is_segment
    assert level.length == 600
    assert "first cube segment" in level.source_description
    assert [obj.object_id for obj in level.objects] == [1, 8, 1]
    with pytest.raises(gd.ContentError, match="Unsupported Geometry Dash object 13"):
        parse(SYNTHETIC_OPENING)


def test_automatic_install_route_requires_no_export_and_changes_no_files(tmp_path):
    install = fake_install(tmp_path)
    before = {path: path.read_bytes() for path in install.rglob("*") if path.is_file()}
    level = gd.load_geometry_dash(install)
    assert level.name == "Stereo Madness"
    assert level.is_segment
    assert level.music_path == install / "Resources" / "StereoMadness.mp3"
    assert level.source == install / "Resources" / "LevelData.plist"
    assert {path: path.read_bytes() for path in install.rglob("*") if path.is_file()} == before


def test_music_filename_lookup_is_case_insensitive_and_required(tmp_path):
    install = fake_install(tmp_path)
    audio = install / "Resources" / "StereoMadness.mp3"
    audio.rename(audio.with_name("stereomadness.MP3"))
    assert gd.load_geometry_dash(install).music_path.name == "stereomadness.MP3"
    audio.with_name("stereomadness.MP3").unlink()
    with pytest.raises(gd.ContentError, match="Required original Geometry Dash music StereoMadness.mp3"):
        gd.load_geometry_dash(install)


@pytest.mark.parametrize("created,filename", [(True, "CCLocalLevels.dat"), (False, "CCGameManager.dat")])
def test_existing_real_save_route_uses_documented_containers_and_local_custom_song(tmp_path, created, filename):
    # No export file: an already-installed save is read automatically/explicitly.
    install = tmp_path / "Geometry Dash"
    install.mkdir()
    save_dir = tmp_path / "GeometryDash"
    save_dir.mkdir()
    record = {"k2": "Synthetic local fixture", "k4": synthetic_compressed(SYNTHETIC_COURSE), "k45": "123456"}
    source = save_dir / filename
    source.write_bytes(synthetic_save([record], created=created))
    (save_dir / "123456.mp3").write_bytes(b"SYNTHETIC PATH FIXTURE ONLY")
    before = source.read_bytes()
    level = gd.load_geometry_dash(install, save_dir)
    assert level.name == "Synthetic local fixture"
    assert not level.is_segment
    assert level.music_path == save_dir / "123456.mp3"
    assert level.source == source
    assert source.read_bytes() == before


def test_one_unsupported_saved_course_does_not_hide_a_supported_one(tmp_path):
    install = tmp_path / "install"
    install.mkdir()
    save_dir = tmp_path / "save"
    save_dir.mkdir()
    records = [
        {"k2": "Unsupported synthetic fixture", "k4": SYNTHETIC_COURSE + "1,901,2,420,3,15;", "k45": 123},
        {"k2": "Supported synthetic fixture", "k4": SYNTHETIC_COURSE, "k45": 123},
    ]
    (save_dir / "CCLocalLevels.dat").write_bytes(synthetic_save(records))
    (save_dir / "123.mp3").write_bytes(b"SYNTHETIC PATH FIXTURE ONLY")
    assert gd.load_geometry_dash(install, save_dir).name == "Supported synthetic fixture"


def test_plain_and_cocos_plist_and_fixed_external_doctype_are_supported():
    data = b'<?xml version="1.0"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist><dict><k>LLM_01</k><d><k>k_0</k><d><k>k2</k><s>Synthetic</s><k>k8</k><i>0</i><k>k14</k><t/></d></d></dict></plist>'
    assert gd.decode_save(data)["LLM_01"]["k_0"] == {"k2": "Synthetic", "k8": 0, "k14": True}
    assert gd.parse_plist(plistlib.dumps({"1": "synthetic"})) == {"1": "synthetic"}


@pytest.mark.parametrize("raw", [
    '<!DOCTYPE plist [<!ENTITY x "expanded">]><plist><dict><k>x</k><s>&x;</s></dict></plist>',
    '<plist><dict><k>x</k><s>a</s><k>x</k><s>b</s></dict></plist>',
    '<plist><dict><k>x</k></dict></plist>',
    '<plist><dict><k>x</k><i>NaN</i></dict></plist>',
    '<plist><dict><k>x</k><r>NaN</r></dict></plist>',
    '<plist><dict><k>x</k><array/></dict></plist>',
])
def test_malformed_or_unsafe_xml_is_rejected(raw):
    with pytest.raises(gd.ContentError):
        gd.parse_plist(raw.encode())


def test_xml_nesting_is_bounded(monkeypatch):
    monkeypatch.setattr(gd, "MAX_XML_DEPTH", 3)
    xml = "<plist><dict>" + "<k>x</k><d>" * 4 + "</d>" * 4 + "</dict></plist>"
    with pytest.raises(gd.ContentError, match="nesting"):
        gd.parse_plist(xml.encode())


@pytest.mark.parametrize("payload", ["A", "a====", "SGV$sbG8=", "!!!!", "SGVsbG8=\n"])
def test_invalid_base64_is_rejected(payload):
    with pytest.raises(gd.ContentError):
        gd.decode_level_data(payload)


def test_bad_checksum_truncation_and_extra_compressed_members_are_rejected():
    compressed = gzip.compress(SYNTHETIC_COURSE.encode(), mtime=0)
    corrupt = bytearray(compressed)
    corrupt[-8] ^= 1
    for bad in (compressed[:-1], bytes(corrupt), compressed + b"extra", compressed + compressed):
        with pytest.raises(gd.ContentError):
            gd.decode_level_data(base64.urlsafe_b64encode(bad).decode())


def test_compression_bomb_and_file_limits_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(gd, "MAX_LEVEL_BYTES", 128)
    bomb = base64.urlsafe_b64encode(gzip.compress(b"x" * 10000)).decode()
    with pytest.raises(gd.ContentError, match="exceeds"):
        gd.decode_level_data(bomb)
    path = tmp_path / "synthetic-big"
    path.write_bytes(b"x" * 129)
    with pytest.raises(gd.ContentError, match="exceeds"):
        gd._read_limited(path, 128)


@pytest.mark.parametrize("object_id", [9, 10, 11, 35, 36, 45, 99, 200, 901, 1049, 1268, 987654])
def test_unsupported_gameplay_never_silently_becomes_playable(object_id):
    raw = SYNTHETIC_COURSE + f"1,{object_id},2,400,3,15;1,13,2,600,3,90;"
    with pytest.raises(gd.ContentError, match=f"Unsupported Geometry Dash object {object_id}"):
        parse(raw, allow_cube_segment=True)


@pytest.mark.parametrize("key,val", [("121", "1"), ("134", "1"), ("128", "2"), ("129", "2"), ("9999", "0")])
def test_unimplemented_collision_or_future_properties_reject(key, val):
    with pytest.raises(gd.ContentError, match="Unsupported properties"):
        parse(SYNTHETIC_COURSE + f"1,1,2,450,3,15,{key},{val};")


@pytest.mark.parametrize("key", ["kA3", "kA8", "kA10", "kA11", "kA20", "kA22", "kA28", "kA29"])
def test_mini_dual_multiplayer_gravity_reverse_platformer_and_mirror_reject(key):
    with pytest.raises(gd.ContentError, match="player state"):
        parse(f"{key},1;" + SYNTHETIC_COURSE.split(";", 1)[1])


@pytest.mark.parametrize("raw", [
    "kA2,1;1,1,2,150,3,15;",
    "kA4,2;1,1,2,150,3,15;",
    "kA13,-1;1,1,2,150,3,15;",
    "kA2,0;1,1,2,NaN,3,15;",
    "kA2,0;1,1,2,150,3,inf;",
    "kA2,0;1,8,2,150,3,15,6,45;",
    "kA2,0;1,8,2,150,3,15,32,-1;",
    "kA2,0;1,1,2,150,3,15,32,100;",
    "kA2,0;1,1,2,150,3,15,2,200;",
    "kA2,0;1,1,2,150,3;",
    "kA2,0;1,1,2,150;",
])
def test_invalid_mode_speed_offset_geometry_and_pairs_are_rejected(raw):
    with pytest.raises(gd.ContentError):
        parse(raw)


def test_vertical_spike_reflection_is_a_runtime_quarter_turn_and_scaling_preserved():
    level = parse(SYNTHETIC_COURSE + "1,8,2,420,3,30,5,1,6,90,32,0.5;")
    spike = level.objects[-1]
    assert spike.rotation == 270
    assert spike.scale == 0.5
    assert spike.width == 15


def test_known_color_triggers_and_decorative_spikes_do_not_become_hazards():
    level = parse(SYNTHETIC_COURSE + "1,18,2,420,3,15;1,29,2,450,3,15,7,255,8,100,9,0,10,0.5;")
    assert all(obj.kind == "decoration" for obj in level.objects[-2:])


def test_default_windows_save_location_is_discovered(tmp_path, monkeypatch):
    local = tmp_path / "AppData" / "Local"
    save_dir = local / "GeometryDash"
    save_dir.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    assert save_dir in gd.find_save_dirs(tmp_path)


def test_selected_steam_library_proton_save_location_is_discovered(tmp_path):
    steamapps = tmp_path / "steamapps"
    install = steamapps / "common" / "Geometry Dash"
    install.mkdir(parents=True)
    save_dir = steamapps / "compatdata" / "322170" / "pfx" / "drive_c" / "users" / "steamuser" / "AppData" / "Local" / "GeometryDash"
    save_dir.mkdir(parents=True)
    assert save_dir in gd.find_save_dirs(install)


def test_missing_copy_has_no_fake_level_fallback(tmp_path):
    with pytest.raises(gd.ContentError, match="no synthetic fallback"):
        gd.load_geometry_dash(tmp_path)


def test_native_macos_encrypted_save_reports_stdlib_limit():
    with pytest.raises(gd.ContentError, match="Native macOS AES saves are unsupported"):
        gd.decode_save(b"not actually encrypted; explicitly synthetic bytes")


def test_raw_parser_has_a_clear_error_for_non_ascii_level_fields():
    with pytest.raises(gd.ContentError, match="ASCII"):
        parse("kS1,\u00e9;1,1,2,150,3,15;")
