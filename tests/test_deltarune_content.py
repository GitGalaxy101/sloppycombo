"""Entirely synthetic GameMaker fixtures: no Deltarune artwork or game files."""

from __future__ import annotations

import bz2
import hashlib
from pathlib import Path
import struct
import zlib

import pytest

from deltadash.deltarune_content import (
    ContentError,
    PNG_SIGNATURE,
    _qoi_pixels,
    discover_data_file,
    load_deltarune,
)


def synthetic_png(width=4, height=4):
    """A synthetic teal square; independent PNG fixture writer."""
    def section(tag, contents):
        return struct.pack(">I", len(contents)) + tag + contents + struct.pack(">I", zlib.crc32(tag + contents) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    pixels = (b"\x00" + b"\x12\x34\x56\xff" * width) * height
    return PNG_SIGNATURE + section(b"IHDR", header) + section(b"IDAT", zlib.compress(pixels)) + section(b"IEND", b"")


def synthetic_qoi(width=4, height=4):
    pixels = width * height
    assert 1 <= pixels <= 32
    # Full RGBA opcode then a run of the remaining pixels.
    payload = b"\xff\x12\x34\x56\xff" + (bytes((0x40 | (pixels - 2),)) if pixels > 1 else b"")
    return b"fioq" + struct.pack("<HHI", width, height, len(payload)) + payload


def synthetic_archive(*, texture=None, texture_header=12, sprite_version=None,
                      names=("spr_kris_dw_d",), game_name="Deltarune Chapter 2 SYNTHETIC",
                      city_marker=True, page_items=1):
    """Construct documented records with absolute pointers, not a real game."""
    data = bytearray(b"FORM\0\0\0\0")
    offsets = {}

    def begin(tag):
        position = len(data)
        data.extend(tag.encode("ascii") + b"\0\0\0\0")
        offsets[tag] = len(data)
        return position

    def end(position):
        struct.pack_into("<I", data, position + 4, len(data) - position - 8)

    def reserve(size):
        position = len(data)
        data.extend(b"\0" * size)
        return position

    strings = [game_name, "Synthetic config", *names]
    if city_marker:
        strings.append("room_dw_city_SYNTHETIC")
    position = begin("STRG")
    data.extend(struct.pack("<I", len(strings)))
    table = reserve(len(strings) * 4)
    string_pointers = {}
    for index, name in enumerate(strings):
        pointer = len(data)
        encoded = name.encode("utf-8")
        struct.pack_into("<I", data, table + index * 4, pointer)
        data.extend(struct.pack("<I", len(encoded)) + encoded + b"\0")
        string_pointers[name] = pointer + 4
    end(position)
    position = begin("GEN8")
    pointer = reserve(128)
    data[pointer:pointer + 4] = b"\1\x11\0\0"
    struct.pack_into("<I", data, pointer + 4, string_pointers[game_name])
    struct.pack_into("<I", data, pointer + 8, string_pointers["Synthetic config"])
    struct.pack_into("<I", data, pointer + 40, string_pointers[game_name])
    struct.pack_into("<4I", data, pointer + 44, 2, 3, 0, 0)
    struct.pack_into("<I", data, pointer + 100, string_pointers[game_name])
    end(position)
    position = begin("SPRT")
    data.extend(struct.pack("<I", len(names)))
    table = reserve(len(names) * 4)
    frame_pointers = []
    for index, name in enumerate(names):
        pointer = reserve(56)
        offsets[name] = pointer
        struct.pack_into("<I", data, table + index * 4, pointer)
        struct.pack_into("<3I", data, pointer, string_pointers[name], 3, 3)
        if sprite_version is not None:
            data.extend(struct.pack("<iII", -1, sprite_version, 0))
            data.extend(struct.pack("<fI", 10.0, 0))
            if sprite_version >= 2:
                data.extend(b"\0" * 4)
            if sprite_version >= 3:
                data.extend(b"\0" * 4)
        data.extend(struct.pack("<I", 1))
        frame_pointers.append(reserve(4))
        data.extend(b"\0" * 4)  # collision mask count: none
    end(position)
    position = begin("TPAG")
    data.extend(struct.pack("<I", page_items))
    table = reserve(page_items * 4)
    for index in range(page_items):
        pointer = len(data)
        offsets[f"frame{index}"] = pointer
        struct.pack_into("<I", data, table + index * 4, pointer)
        data.extend(struct.pack("<10Hh", 0, 0, 2, 2, 1, 1, 2, 2, 3, 3, 0))
    pointer = offsets["frame0"]
    offsets["frame"] = pointer
    for pointer_location in frame_pointers:
        struct.pack_into("<I", data, pointer_location, pointer)
    end(position)
    position = begin("TXTR")
    textures = texture if isinstance(texture, tuple) else (texture if texture is not None else synthetic_png(),)
    data.extend(struct.pack("<I", len(textures)))
    table = reserve(len(textures) * 4)
    headers = []
    for index in range(len(textures)):
        pointer = reserve(texture_header)
        headers.append(pointer)
        struct.pack_into("<I", data, table + index * 4, pointer)
        if texture_header >= 28:
            struct.pack_into("<ii", data, pointer + 12, 4, 4)
    offsets["texture_header"] = headers[0]
    for index, texture in enumerate(textures):
        while len(data) % 128:
            data.append(0)
        blob = len(data)
        if index == 0:
            offsets["texture"] = blob
        data.extend(texture)
        pointer = headers[index]
        struct.pack_into("<I", data, pointer + texture_header - 4, blob)
        if texture_header >= 16:
            struct.pack_into("<I", data, pointer + 8, len(texture))
    end(position)
    struct.pack_into("<I", data, 4, len(data) - 8)
    return bytes(data), offsets


def local_archive(tmp_path, **kwargs):
    data, offsets = synthetic_archive(**kwargs)
    path = tmp_path / "data.win"
    path.write_bytes(data)
    return path, data, offsets


@pytest.mark.parametrize("texture_header", [8, 12, 16, 28])
@pytest.mark.parametrize("sprite_version", [None, 1, 2, 3])
def test_embedded_png_import_and_placement(tmp_path, texture_header, sprite_version):
    path, data, _ = local_archive(tmp_path, texture_header=texture_header, sprite_version=sprite_version)
    assets = load_deltarune(tmp_path)
    assert assets.data_file == path
    assert assets.sha256 == hashlib.sha256(data).hexdigest()
    assert "SYNTHETIC" in assets.game_name
    assert tuple(assets.sprites) == ("kris",)
    sprite = assets.sprites["kris"]
    assert sprite.name == "spr_kris_dw_d"
    assert len(sprite.frames) == 1
    frame = sprite.frames[0]
    assert frame.png == synthetic_png()
    assert frame.source == (0, 0, 2, 2)
    assert frame.target == (1, 1, 2, 2)
    assert frame.canvas == (3, 3)
    assert path.read_bytes() == data  # importer never edits the user's archive


def test_optional_party_resources(tmp_path):
    local_archive(tmp_path, names=("spr_kris_dw_d", "spr_susie_dw_d", "spr_ralsei_dw_d"))
    assert set(load_deltarune(tmp_path).sprites) == {"kris", "susie", "ralsei"}


@pytest.mark.parametrize("texture_header", [8, 12, 16, 28])
def test_multiple_pages_have_independent_pointer_bounds(tmp_path, texture_header):
    path, original, offsets = local_archive(
        tmp_path, texture=(synthetic_png(), synthetic_qoi()), texture_header=texture_header,
    )
    data = bytearray(original)
    struct.pack_into("<h", data, offsets["frame"] + 20, 1)
    path.write_bytes(data)
    assert load_deltarune(path).sprites["kris"].frames[0].png == synthetic_png()


def test_texture_size_cannot_overlap_next_page(tmp_path):
    path, original, offsets = local_archive(
        tmp_path, texture=(synthetic_png(), synthetic_qoi()), texture_header=16,
    )
    data = bytearray(original)
    struct.pack_into("<I", data, offsets["texture_header"] + 8, len(data) - offsets["texture"])
    path.write_bytes(data)
    with pytest.raises(ContentError, match="overlaps another texture"):
        load_deltarune(path)


def test_spine_sprite_is_not_interpreted_as_bitmap(tmp_path):
    path, original, offsets = local_archive(tmp_path, sprite_version=2)
    data = bytearray(original)
    struct.pack_into("<I", data, offsets["spr_kris_dw_d"] + 64, 2)
    path.write_bytes(data)
    with pytest.raises(ContentError, match="unsupported sprite type/version"):
        load_deltarune(path)


def test_overlapping_tpag_records_rejected_even_when_unreferenced(tmp_path):
    path, original, offsets = local_archive(tmp_path, page_items=2)
    data = bytearray(original)
    struct.pack_into("<I", data, offsets["TPAG"] + 8, offsets["frame"] + 10)
    path.write_bytes(data)
    with pytest.raises(ContentError, match="outside its declared chunk"):
        load_deltarune(path)


@pytest.mark.parametrize("encoding", ["qoi", "bzip_old", "bzip_new"])
def test_modern_qoi_page_import(tmp_path, encoding):
    texture = synthetic_qoi()
    if encoding == "bzip_old":
        texture = b"2zoq" + struct.pack("<HH", 4, 4) + bz2.compress(texture)
    elif encoding == "bzip_new":
        texture = b"2zoq" + struct.pack("<HHI", 4, 4, len(texture)) + bz2.compress(texture)
    local_archive(tmp_path, texture=texture, texture_header=16)
    assert load_deltarune(tmp_path).sprites["kris"].frames[0].png == synthetic_png()


def test_qoi_signed_differences_indices_and_runs():
    # full color; +1/-1/-2 8bit diff; -16/+7/-8 16bit diff;
    # +15/-16/+1/-1 24bit diff; index back to original; repeat once.
    original = (20, 40, 60, 200)
    first = (21, 39, 58, 200)
    second = (5, 46, 50, 200)
    third = (20, 30, 51, 199)
    diff16 = 0xC000 | (16 << 8) | (7 << 4) | 8
    diff24 = 0xE00000 | (15 << 15) | (16 << 10) | (1 << 5) | 31
    payload = (b"\xff" + bytes(original) + bytes((0x80 | (1 << 4) | (3 << 2) | 2,))
               + diff16.to_bytes(2, "big") + diff24.to_bytes(3, "big")
               + bytes(((20 ^ 40 ^ 60 ^ 200) & 63, 0x40)))
    texture = b"fioq" + struct.pack("<HHI", 6, 1, len(payload)) + payload
    pixels, width, height = _qoi_pixels(texture)
    assert (width, height) == (6, 1)
    assert pixels == b"".join(bytes(pixel) for pixel in (original, first, second, third, original, original))


@pytest.mark.parametrize("game_name,city_marker,message", [
    ("An unrelated game", True, "does not identify"),
    ("Deltarune Chapter 1", False, "Chapter 2 content markers"),
])
def test_wrong_game_and_chapter_rejected(tmp_path, game_name, city_marker, message):
    local_archive(tmp_path, game_name=game_name, city_marker=city_marker)
    with pytest.raises(ContentError, match=message):
        load_deltarune(tmp_path)


def test_missing_kris_is_not_replaced_with_placeholder(tmp_path):
    local_archive(tmp_path, names=("spr_susie_dw_d",))
    with pytest.raises(ContentError, match="Dark World Kris sprite was not found"):
        load_deltarune(tmp_path)


def test_ambiguous_numbered_kris_resource_rejected(tmp_path):
    local_archive(tmp_path, names=("spr_kris_dw_d_2", "spr_kris_dw_d_3"))
    with pytest.raises(ContentError, match="Multiple supported kris sprites"):
        load_deltarune(tmp_path)


def test_chapter2_discovery_prefers_correct_directory(tmp_path):
    for directory in ("chapter1_windows", "chapter2_windows", "launcher"):
        folder = tmp_path / directory
        folder.mkdir()
        (folder / "data.win").write_bytes(b"synthetic filename fixture")
    assert discover_data_file(tmp_path) == tmp_path / "chapter2_windows" / "data.win"


def test_discovery_rejects_ambiguous_chapter2_copies(tmp_path):
    for directory in ("chapter2_windows", "backup/chapter2_windows"):
        folder = tmp_path / directory
        folder.mkdir(parents=True)
        (folder / "data.win").write_bytes(b"synthetic filename fixture")
    with pytest.raises(ContentError, match="Multiple Deltarune archives"):
        discover_data_file(tmp_path)


@pytest.mark.parametrize("archive_name", ["game.unx", "game.ios"])
def test_discovery_accepts_exact_archive_and_platform_equivalent(tmp_path, archive_name):
    folder = tmp_path / "chapter2_linux"
    folder.mkdir()
    path = folder / archive_name
    path.write_bytes(b"synthetic filename fixture")
    assert discover_data_file(tmp_path) == path
    assert discover_data_file(path) == path


def test_discovery_does_not_follow_symlinked_directories(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data.win").write_bytes(b"synthetic filename fixture")
    game = tmp_path / "game"
    game.mkdir()
    (game / "chapter2").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ContentError, match="No data.win"):
        discover_data_file(game)


@pytest.mark.parametrize("mutation,message", [
    ("form_length", "FORM length"),
    ("bad_string_pointer", "outside STRG"),
    ("huge_string_count", "count exceeds"),
    ("bad_frame_pointer", "missing TPAG"),
    ("crop_outside_page", "outside its texture"),
    ("target_outside_canvas", "does not fit"),
    ("empty_sprite", "no supported bitmap"),
    ("corrupt_png", "checksum mismatch"),
    ("external_texture", "externally"),
    ("unsupported_texture", "Unsupported texture"),
])
def test_malformed_archive_fails_closed(tmp_path, mutation, message):
    path, original, offsets = local_archive(tmp_path)
    data = bytearray(original)
    if mutation == "form_length":
        struct.pack_into("<I", data, 4, len(data))
    elif mutation == "bad_string_pointer":
        struct.pack_into("<I", data, offsets["spr_kris_dw_d"], 0)
    elif mutation == "huge_string_count":
        struct.pack_into("<I", data, offsets["STRG"], 0xFFFFFFFF)
    elif mutation == "bad_frame_pointer":
        struct.pack_into("<I", data, offsets["spr_kris_dw_d"] + 60, 1)
    elif mutation == "crop_outside_page":
        struct.pack_into("<H", data, offsets["frame"], 4)
    elif mutation == "target_outside_canvas":
        struct.pack_into("<H", data, offsets["frame"] + 8, 3)
    elif mutation == "empty_sprite":
        struct.pack_into("<I", data, offsets["spr_kris_dw_d"] + 56, 0)
    elif mutation == "corrupt_png":
        data[offsets["texture"] + 20] ^= 1
    elif mutation == "external_texture":
        struct.pack_into("<I", data, offsets["texture_header"] + 8, 0)
    elif mutation == "unsupported_texture":
        data[offsets["texture"]:offsets["texture"] + 8] = b"DDS xxxx"
    path.write_bytes(data)
    with pytest.raises(ContentError, match=message):
        load_deltarune(path)


@pytest.mark.parametrize("texture,message", [
    (b"fioq" + struct.pack("<HHI", 4, 4, 1) + b"\xff", "Truncated"),
    (b"fioq" + struct.pack("<HHI", 1, 1, 1) + b"\x41", "run extends"),
    (b"fioq" + struct.pack("<HHI", 1, 1, 2) + b"\x00\x00", "unexpected data"),
    (b"fioq" + struct.pack("<HHI", 65535, 65535, 1) + b"\x00", "dimensions exceed"),
    (b"2zoq" + struct.pack("<HHI", 4, 4, 0xFFFFFFFF) + b"BZh9", "size exceeds"),
])
def test_bounded_texture_decoding(tmp_path, texture, message):
    local_archive(tmp_path, texture=texture)
    with pytest.raises(ContentError, match=message):
        load_deltarune(tmp_path)


@pytest.mark.parametrize("kind,value,message", [
    ("depth", 0, "invalid IHDR"),
    ("color", 5, "invalid IHDR"),
    ("compression", 1, "invalid IHDR"),
    ("filter", 255, "invalid IHDR"),
    ("interlace", 2, "invalid IHDR"),
    ("deflate", b"not zlib", "invalid compression"),
    ("deflate", zlib.compress(b"\0" * 1), "does not match"),
    ("deflate", zlib.compress(b"\0" * 1_000_000), "exceeds its declared"),
])
def test_crc_correct_but_invalid_png_data_rejected(tmp_path, kind, value, message):
    fields = [4, 4, 8, 6, 0, 0, 0]
    field_index = {"depth": 2, "color": 3, "compression": 4, "filter": 5, "interlace": 6}
    if kind in field_index:
        fields[field_index[kind]] = value
    header = struct.pack(">IIBBBBB", *fields)
    compressed = value if kind == "deflate" else zlib.compress((b"\0" + b"\x12\x34\x56\xff" * 4) * 4)

    def section(tag, contents):
        return struct.pack(">I", len(contents)) + tag + contents + struct.pack(">I", zlib.crc32(tag + contents) & 0xFFFFFFFF)

    texture = PNG_SIGNATURE + section(b"IHDR", header) + section(b"IDAT", compressed) + section(b"IEND", b"")
    local_archive(tmp_path, texture=texture)
    with pytest.raises(ContentError, match=message):
        load_deltarune(tmp_path)
