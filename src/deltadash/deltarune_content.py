"""Read the player's own Deltarune GameMaker archive, without modifying it.

Public contracts:
* ``discover_data_file(directory) -> Path`` selects one Chapter 2 archive.
* ``load_deltarune(directory_or_archive) -> DeltaruneAssets`` imports Dark World
  Kris, plus Susie/Ralsei when their supported sprite resources are present.
* Frames contain an encoded texture-page PNG, its source crop, its target
  placement, and the sprite canvas dimensions; importing/rendering never writes
  extracted proprietary assets to disk.

The supported subset is little-endian FORM with GEN8/STRG/SPRT/TPAG/TXTR.
Normal legacy and GameMaker 2 special-version 1/2/3 bitmap sprites are supported,
with embedded PNG, GameMaker ``fioq`` QOI, and ``2zoq`` BZip2-QOI pages. External
textures, DDS, SWF/Spine/vector sprites, and undocumented header layouts fail
explicitly. This is structural identification of a local Deltarune archive,
not a license check or cryptographic proof that the archive is unmodified.

Format references (public source, no game assets):
https://github.com/UnderminersTeam/UndertaleModTool/blob/master/UndertaleModLib/UndertaleChunks.cs
https://github.com/UnderminersTeam/UndertaleModTool/blob/master/UndertaleModLib/Models/UndertaleGeneralInfo.cs
https://github.com/UnderminersTeam/UndertaleModTool/blob/master/UndertaleModLib/Models/UndertaleSprite.cs
https://github.com/UnderminersTeam/UndertaleModTool/blob/master/UndertaleModLib/Models/UndertaleTexturePageItem.cs
https://github.com/UnderminersTeam/UndertaleModTool/blob/master/UndertaleModLib/Models/UndertaleEmbeddedTexture.cs
https://github.com/UnderminersTeam/UndertaleModTool/blob/master/UndertaleModLib/Util/GMImage.cs
https://github.com/UnderminersTeam/UndertaleModTool/blob/master/UndertaleModLib/Util/QoiConverter.cs
"""

from __future__ import annotations

import bz2
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import struct
import zlib


class ContentError(ValueError):
    """Missing, malformed, ambiguous, or unsupported locally supplied content."""


@dataclass(frozen=True)
class SpriteFrame:
    png: bytes
    source: tuple[int, int, int, int]
    target: tuple[int, int, int, int]
    canvas: tuple[int, int]


@dataclass(frozen=True)
class SpriteAsset:
    name: str
    frames: tuple[SpriteFrame, ...]


@dataclass(frozen=True)
class DeltaruneAssets:
    data_file: Path
    sha256: str
    game_name: str
    sprites: dict[str, SpriteAsset]


@dataclass(frozen=True)
class _Chunk:
    start: int
    end: int


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_ITEMS = 500_000
MAX_STRING_BYTES = 1024 * 1024
MAX_TEXTURE_PIXELS = 16_777_216
MAX_TEXTURE_BYTES = 128 * 1024 * 1024
MAX_SPRITE_FRAMES = 1024
MAX_SPRITE_SIDE = 4096
_CHAPTER_2 = re.compile(r"(?:^|[^a-z0-9])(?:chapter|ch)[ _-]*2(?:[^0-9]|$)", re.I)
_ALIASES = {
    "kris": ("spr_kris_dw_d", "spr_kris_dw_d_0", "spr_kris_dw_down"),
    "susie": ("spr_susie_dw_d", "spr_susie_dw_d_0", "spr_susie_dw_down"),
    "ralsei": ("spr_ralsei_dw_d", "spr_ralsei_dw_d_0", "spr_ralsei_dw_down", "spr_ralsei_d"),
}


def discover_data_file(game_dir: str | Path) -> Path:
    """Find one local archive, preferring an explicitly named Chapter 2 folder.

    Passing the exact archive resolves multiple-install ambiguity. Searches are
    bounded, never follow directory symlinks, and accept the equivalent Linux
    ``game.unx`` and macOS ``game.ios`` archives as well as Windows ``data.win``.
    """
    root = Path(game_dir).expanduser()
    if root.is_file():
        if root.name.lower() not in {"data.win", "game.unx", "game.ios"}:
            raise ContentError("Choose Deltarune's data.win/game.unx/game.ios archive or its installation folder.")
        return root.resolve()
    if not root.is_dir():
        raise ContentError(f"Deltarune installation folder does not exist: {root}")
    root = root.resolve()
    candidates: list[Path] = []
    visited = 0
    try:
        for current, directories, files in os.walk(root, followlinks=False):
            relative = Path(current).relative_to(root)
            directories[:] = sorted(
                directory for directory in directories
                if not (Path(current) / directory).is_symlink()
                and directory not in {".git", "node_modules", ".venv"}
            )
            if len(relative.parts) >= 8:
                directories.clear()
            visited += len(directories) + len(files)
            if visited > 100_000:
                raise ContentError("Installation search is too large. Select the Chapter 2 data.win directly.")
            for filename in sorted(files):
                if filename.lower() in {"data.win", "game.unx", "game.ios"}:
                    candidate = Path(current) / filename
                    if candidate.is_symlink():
                        continue
                    candidates.append(candidate)
                    if len(candidates) > 64:
                        raise ContentError("Too many game archives. Select the Chapter 2 data.win directly.")
    except OSError as exc:
        raise ContentError(f"Cannot inspect the Deltarune installation: {exc}") from exc
    chapter2 = [p for p in candidates if any(_CHAPTER_2.search(part) for part in p.relative_to(root).parts[:-1])]
    selected = chapter2 or candidates
    if not selected:
        raise ContentError("No data.win/game.unx/game.ios found. Choose an extracted Deltarune installation containing Chapter 2.")
    if len(selected) != 1:
        names = ", ".join(str(p.relative_to(root)) for p in selected[:6])
        raise ContentError(f"Multiple Deltarune archives found ({names}). Select the Chapter 2 archive explicitly.")
    return selected[0]


class _Archive:
    def __init__(self, data: bytes):
        self.data = data
        self.chunks: dict[str, _Chunk] = {}
        if len(data) < 8 or data[:4] != b"FORM":
            raise ContentError("Expected an unencrypted GameMaker FORM archive; choose Deltarune's data.win.")
        if self.u32(4) != len(data) - 8:
            raise ContentError("FORM length does not match the archive; the file is truncated or unsupported.")
        position = 8
        while position < len(data):
            self.check(position, 8)
            try:
                name = data[position:position + 4].decode("ascii")
            except UnicodeDecodeError as exc:
                raise ContentError("Invalid GameMaker chunk identifier.") from exc
            if not re.fullmatch(r"[A-Z0-9]{4}", name):
                raise ContentError("Invalid GameMaker chunk identifier.")
            length = self.u32(position + 4)
            start, end = position + 8, position + 8 + length
            self.check(start, length)
            if name in self.chunks:
                raise ContentError(f"Duplicate {name} chunk; unsupported archive.")
            self.chunks[name] = _Chunk(start, end)
            position = end
        for name in ("GEN8", "STRG", "SPRT", "TPAG", "TXTR"):
            if name not in self.chunks:
                raise ContentError(f"Missing {name} chunk. This is not a supported Deltarune archive.")
        self.strings = self._strings()
        self.game_name = self._identity()
        self.sprite_pointers = self.table("SPRT")
        page_items = sorted(self.table("TPAG"))
        self.page_items = set(page_items)
        self.page_item_ends = dict(zip(page_items, (*page_items[1:], self.chunks["TPAG"].end)))
        for pointer in page_items:
            self.check(pointer, 22, _Chunk(pointer, self.page_item_ends[pointer]))
        self.textures = self._texture_headers()
        self.png_cache: dict[int, tuple[bytes, int, int]] = {}

    def check(self, offset: int, length: int, chunk: _Chunk | None = None) -> None:
        start, end = (chunk.start, chunk.end) if chunk else (0, len(self.data))
        if length < 0 or offset < start or offset > end or length > end - offset:
            raise ContentError("Archive pointer or record extends outside its declared chunk.")

    def u32(self, offset: int) -> int:
        self.check(offset, 4)
        return struct.unpack_from("<I", self.data, offset)[0]

    def i32(self, offset: int) -> int:
        self.check(offset, 4)
        return struct.unpack_from("<i", self.data, offset)[0]

    def table(self, name: str) -> tuple[int, ...]:
        chunk = self.chunks[name]
        self.check(chunk.start, 4, chunk)
        count = self.u32(chunk.start)
        if count > MAX_ITEMS:
            raise ContentError(f"{name} resource count exceeds the supported bound.")
        self.check(chunk.start + 4, count * 4, chunk)
        if not count:
            return ()
        pointers = struct.unpack_from(f"<{count}I", self.data, chunk.start + 4)
        table_end = chunk.start + 4 + count * 4
        if any(pointer < table_end or pointer >= chunk.end for pointer in pointers):
            raise ContentError(f"{name} contains a pointer outside its resource area.")
        if len(set(pointers)) != count:
            raise ContentError(f"{name} contains duplicate resource pointers.")
        return pointers

    def _strings(self) -> dict[int, str]:
        chunk = self.chunks["STRG"]
        result = {}
        pointers = sorted(self.table("STRG"))
        ends = dict(zip(pointers, (*pointers[1:], chunk.end)))
        for pointer in pointers:
            record = _Chunk(pointer, ends[pointer])
            self.check(pointer, 4, record)
            length = self.u32(pointer)
            if length > MAX_STRING_BYTES:
                raise ContentError("GameMaker string is too large.")
            self.check(pointer + 4, length + 1, record)
            if self.data[pointer + 4 + length] != 0:
                raise ContentError("GameMaker string is missing its terminator.")
            try:
                result[pointer + 4] = self.data[pointer + 4:pointer + 4 + length].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ContentError("Invalid UTF-8 in GameMaker string table.") from exc
        return result

    def string_at(self, pointer: int) -> str:
        if pointer not in self.strings:
            raise ContentError("Resource references a string outside STRG's declared entries.")
        return self.strings[pointer]

    def _identity(self) -> str:
        chunk = self.chunks["GEN8"]
        self.check(chunk.start, 104, chunk)
        bytecode = self.data[chunk.start + 1]
        if not 13 <= bytecode <= 17:
            raise ContentError(f"Unsupported GameMaker bytecode version {bytecode}; supported versions are 13–17.")
        # GEN8 uses string-content pointers (four bytes after STRG records).
        filename = self.string_at(self.u32(chunk.start + 4))
        internal = self.string_at(self.u32(chunk.start + 40))
        display = self.string_at(self.u32(chunk.start + 100))
        if not any("deltarune" in name.casefold() for name in (filename, internal, display)):
            raise ContentError("GEN8 does not identify this archive as Deltarune. Choose your Deltarune installation.")
        self.major = self.u32(chunk.start + 44)
        if self.major not in (1, 2):
            raise ContentError(f"Unsupported GameMaker major version {self.major}.")
        return display or internal or filename

    def _texture_headers(self) -> tuple[tuple[int, int], ...]:
        pointers = self.table("TXTR")
        chunk = self.chunks["TXTR"]
        if not pointers:
            raise ContentError("Deltarune archive has no embedded texture pages.")
        if tuple(sorted(pointers)) != pointers:
            raise ContentError("TXTR headers are not ordered; unsupported texture layout.")
        # Documented header lengths: GM1=8, GM2.0.6=12, 2022.3=16,
        # 2022.9=28. Validate a unique whole-table layout rather than scanning
        # arbitrary byte offsets for an apparent texture signature.
        layouts = (8,) if self.major == 1 else (8, 12, 16, 28)
        valid = []
        external = False
        unsupported = False
        for size in layouts:
            entries = []
            plausible = True
            for index, pointer in enumerate(pointers):
                if index + 1 < len(pointers) and pointers[index + 1] != pointer + size:
                    plausible = False
                    break
                try:
                    self.check(pointer, size, chunk)
                    blob = self.u32(pointer + size - 4)
                    block_size = self.u32(pointer + 8) if size >= 16 else 0
                    if blob == 0:
                        external = True
                        plausible = False
                        break
                    if blob < pointers[-1] + size or blob >= chunk.end:
                        plausible = False
                        break
                    self.check(blob, 8, chunk)
                    signature = self.data[blob:blob + 8]
                    if not (signature == PNG_SIGNATURE or signature[:4] in (b"fioq", b"2zoq")):
                        unsupported = True
                        plausible = False
                        break
                    if size == 28:
                        width, height = self.i32(pointer + 12), self.i32(pointer + 16)
                        _dimensions(width, height)
                    if block_size:
                        self.check(blob, block_size, chunk)
                    entries.append((blob, block_size))
                except ContentError:
                    plausible = False
                    break
            if plausible:
                valid.append(tuple(entries))
        if len(valid) != 1:
            if len(valid) > 1:
                raise ContentError("Ambiguous TXTR header layout; this GameMaker version is unsupported.")
            if unsupported:
                raise ContentError("Unsupported texture encoding. Embedded PNG or GameMaker QOI/BZip2-QOI pages are required.")
            if external:
                raise ContentError("This Deltarune build stores textures externally. Embedded PNG/QOI texture pages are required.")
            raise ContentError("Unsupported or malformed TXTR headers. Use an unmodified Deltarune data.win with embedded textures.")
        entries = valid[0]
        if len({blob for blob, _ in entries}) != len(entries):
            raise ContentError("Multiple texture headers reference the same blob; unsupported archive.")
        blobs = sorted(blob for blob, _ in entries)
        next_blobs = dict(zip(blobs, (*blobs[1:], chunk.end)))
        bounds = []
        for blob, recorded_size in entries:
            next_blob = next_blobs[blob]
            bound = blob + recorded_size if recorded_size else next_blob
            if bound > next_blob:
                raise ContentError("Texture blob size overlaps another texture page.")
            bounds.append((blob, bound))
        return tuple(bounds)

    def sprite_names(self) -> dict[str, int]:
        result = {}
        chunk = self.chunks["SPRT"]
        for pointer in self.sprite_pointers:
            self.check(pointer, 4, chunk)
            name = self.string_at(self.u32(pointer))
            if name in result:
                raise ContentError(f"Duplicate sprite name {name!r}; cannot select it reliably.")
            result[name] = pointer
        return result

    def sprite(self, name: str, pointer: int) -> SpriteAsset:
        chunk = self.chunks["SPRT"]
        next_record = min((value for value in self.sprite_pointers if value > pointer), default=chunk.end)
        record = _Chunk(pointer, next_record)
        self.check(pointer, 60, record)
        width, height = self.u32(pointer + 4), self.u32(pointer + 8)
        if not (0 < width <= MAX_SPRITE_SIDE and 0 < height <= MAX_SPRITE_SIDE):
            raise ContentError(f"Sprite {name} dimensions are unsupported.")
        offset = pointer + 56
        if self.i32(offset) == -1:
            self.check(offset, 12, record)
            version, kind = self.u32(offset + 4), self.u32(offset + 8)
            if kind != 0 or version not in (1, 2, 3):
                raise ContentError(f"Sprite {name} uses unsupported sprite type/version {kind}/{version}.")
            offset += 12
            if self.major >= 2:
                offset += 8  # playback speed and mode
                if version >= 2:
                    offset += 4  # sequence offset
                if version >= 3:
                    offset += 4  # nine-slice offset
        self.check(offset, 4, record)
        count = self.u32(offset)
        if not 0 < count <= MAX_SPRITE_FRAMES:
            raise ContentError(f"Sprite {name} has no supported bitmap frames.")
        self.check(offset + 4, count * 4, record)
        frames = []
        for index in range(count):
            page_pointer = self.u32(offset + 4 + index * 4)
            if page_pointer not in self.page_items:
                raise ContentError(f"Sprite {name} references a missing TPAG frame.")
            self.check(page_pointer, 22, _Chunk(page_pointer, self.page_item_ends[page_pointer]))
            sx, sy, sw, sh, tx, ty, tw, th, cw, ch, page = struct.unpack_from("<10Hh", self.data, page_pointer)
            if not 0 <= page < len(self.textures):
                raise ContentError(f"Sprite {name} references a missing texture page.")
            if min(sw, sh, tw, th, cw, ch) <= 0 or cw > MAX_SPRITE_SIDE or ch > MAX_SPRITE_SIDE:
                raise ContentError(f"Sprite {name} has invalid texture dimensions.")
            if tx + tw > cw or ty + th > ch or (cw, ch) != (width, height):
                raise ContentError(f"Sprite {name} target rectangle does not fit its declared canvas.")
            png, page_width, page_height = self.texture(page)
            if sx + sw > page_width or sy + sh > page_height:
                raise ContentError(f"Sprite {name} crop lies outside its texture page.")
            frames.append(SpriteFrame(png, (sx, sy, sw, sh), (tx, ty, tw, th), (cw, ch)))
        return SpriteAsset(name, tuple(frames))

    def texture(self, index: int) -> tuple[bytes, int, int]:
        if index not in self.png_cache:
            start, end = self.textures[index]
            if end - start > MAX_TEXTURE_BYTES:
                raise ContentError("Texture page exceeds the supported size bound.")
            self.png_cache[index] = _texture_to_png(self.data[start:end])
        return self.png_cache[index]


def _dimensions(width: int, height: int) -> None:
    if not 0 < width <= 8192 or not 0 < height <= 8192 or width * height > MAX_TEXTURE_PIXELS:
        raise ContentError("Texture dimensions exceed the supported bounds.")


def _png(data: bytes) -> tuple[bytes, int, int]:
    if not data.startswith(PNG_SIGNATURE):
        raise ContentError("Invalid embedded PNG signature.")
    position = 8
    width = height = 0
    seen_data = False
    expected_bytes = 0
    decoded_bytes = 0
    decoder = zlib.decompressobj()
    while position < len(data):
        if len(data) - position < 12:
            raise ContentError("Truncated embedded PNG chunk.")
        length = struct.unpack_from(">I", data, position)[0]
        kind = data[position + 4:position + 8]
        if length > MAX_TEXTURE_BYTES or length > len(data) - position - 12:
            raise ContentError("Embedded PNG chunk extends outside its texture page.")
        payload = data[position + 8:position + 8 + length]
        crc = struct.unpack_from(">I", data, position + 8 + length)[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != crc:
            raise ContentError("Embedded PNG checksum mismatch; the texture is damaged.")
        if position == 8:
            if kind != b"IHDR" or length != 13:
                raise ContentError("Embedded PNG must begin with a valid IHDR.")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            _dimensions(width, height)
            depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            if depth not in depths.get(color, ()) or compression != 0 or filtering != 0 or interlace not in (0, 1):
                raise ContentError("Embedded PNG has invalid IHDR fields.")
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
            if not interlace:
                expected_bytes = (((width * channels * depth + 7) // 8) + 1) * height
            else:
                # Exact filtered byte count for the seven Adam7 passes.
                for x, y, dx, dy in ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
                                     (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)):
                    columns = max(0, (width - x + dx - 1) // dx)
                    rows = max(0, (height - y + dy - 1) // dy)
                    if columns and rows:
                        expected_bytes += (((columns * channels * depth + 7) // 8) + 1) * rows
        elif kind == b"IHDR":
            raise ContentError("Embedded PNG contains duplicate IHDR.")
        if kind == b"IDAT":
            seen_data = True
            try:
                decoded_bytes += len(decoder.decompress(payload, max_length=expected_bytes - decoded_bytes + 1))
            except zlib.error as exc:
                raise ContentError("Embedded PNG image data has invalid compression/checksum.") from exc
            if decoded_bytes > expected_bytes or decoder.unconsumed_tail or decoder.unused_data:
                raise ContentError("Embedded PNG image data exceeds its declared dimensions or has trailing compressed data.")
        position += 12 + length
        if kind == b"IEND":
            if length or not seen_data:
                raise ContentError("Embedded PNG is missing image data or has an invalid IEND.")
            if decoded_bytes != expected_bytes or not decoder.eof:
                raise ContentError("Embedded PNG image data is truncated or does not match its dimensions.")
            return data[:position], width, height
    raise ContentError("Embedded PNG is missing IEND.")


def _signed(value: int, bits: int) -> int:
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def _qoi_pixels(data: bytes) -> tuple[bytes, int, int]:
    if len(data) < 12 or data[:4] != b"fioq":
        raise ContentError("Invalid GameMaker QOI header.")
    width, height, length = struct.unpack_from("<HHI", data, 4)
    _dimensions(width, height)
    if length > MAX_TEXTURE_BYTES or length > len(data) - 12:
        raise ContentError("GameMaker QOI payload length exceeds its texture page.")
    payload = memoryview(data)[12:12 + length]
    position = 0
    rgba = [0, 0, 0, 255]
    index = [(0, 0, 0, 0)] * 64
    result = bytearray(width * height * 4)
    run = 0

    def take() -> int:
        nonlocal position
        if position >= len(payload):
            raise ContentError("Truncated GameMaker QOI pixel stream.")
        value = payload[position]
        position += 1
        return value

    pixels = width * height
    for pixel in range(pixels):
        if run:
            run -= 1
        else:
            first = take()
            if first & 0xC0 == 0:
                rgba[:] = index[first & 63]
            elif first & 0xE0 == 0x40:
                run = first & 31
            elif first & 0xE0 == 0x60:
                run = (((first & 31) << 8) | take()) + 32
            elif first & 0xC0 == 0x80:
                for channel, shift in enumerate((4, 2, 0)):
                    rgba[channel] = (rgba[channel] + _signed((first >> shift) & 3, 2)) & 255
            elif first & 0xE0 == 0xC0:
                merged = first << 8 | take()
                for channel, shift, bits in ((0, 8, 5), (1, 4, 4), (2, 0, 4)):
                    rgba[channel] = (rgba[channel] + _signed((merged >> shift) & ((1 << bits) - 1), bits)) & 255
            elif first & 0xF0 == 0xE0:
                merged = first << 16 | take() << 8 | take()
                for channel, shift in enumerate((15, 10, 5, 0)):
                    rgba[channel] = (rgba[channel] + _signed((merged >> shift) & 31, 5)) & 255
            elif first & 0xF0 == 0xF0:
                for channel, mask in enumerate((8, 4, 2, 1)):
                    if first & mask:
                        rgba[channel] = take()
            else:
                raise ContentError("Unsupported GameMaker QOI opcode.")
            if run > pixels - pixel - 1:
                raise ContentError("GameMaker QOI run extends beyond the texture dimensions.")
            index[(rgba[0] ^ rgba[1] ^ rgba[2] ^ rgba[3]) & 63] = tuple(rgba)
        result[pixel * 4:pixel * 4 + 4] = bytes(rgba)
    # The declared QOI payload ends after its final pixel. Archive alignment
    # padding is outside that payload. Never silently repeat a truncated pixel.
    if position != len(payload):
        raise ContentError("GameMaker QOI stream has unexpected data after its final pixel.")
    return bytes(result), width, height


def _encode_png(rgba: bytes, width: int, height: int) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    scanlines = b"".join(b"\0" + rgba[row * width * 4:(row + 1) * width * 4] for row in range(height))
    return (PNG_SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b""))


def _texture_to_png(data: bytes) -> tuple[bytes, int, int]:
    if data.startswith(PNG_SIGNATURE):
        return _png(data)
    if data[:4] == b"2zoq":
        if len(data) < 12:
            raise ContentError("Truncated BZip2-QOI header.")
        width, height = struct.unpack_from("<HH", data, 4)
        _dimensions(width, height)
        # Pre-2022.5 has BZh immediately after width/height; later versions add
        # an explicit decompressed length. This checks the documented signature.
        legacy = data[8:11] == b"BZh"
        start = 8 if legacy else 12
        expected = None if legacy else struct.unpack_from("<I", data, 8)[0]
        if expected is not None and expected > MAX_TEXTURE_BYTES:
            raise ContentError("BZip2-QOI decompressed size exceeds the supported bound.")
        try:
            decoder = bz2.BZ2Decompressor()
            uncompressed = decoder.decompress(data[start:], max_length=MAX_TEXTURE_BYTES + 1)
        except (OSError, EOFError, ValueError) as exc:
            raise ContentError("Damaged BZip2-QOI texture stream.") from exc
        if len(uncompressed) > MAX_TEXTURE_BYTES or not decoder.eof:
            raise ContentError("BZip2-QOI texture is truncated or exceeds the supported bound.")
        if any(decoder.unused_data):
            raise ContentError("BZip2-QOI texture has unexpected bytes after its compressed stream.")
        if expected is not None and len(uncompressed) != expected:
            raise ContentError("BZip2-QOI decompressed length does not match its header.")
        rgba, inner_width, inner_height = _qoi_pixels(uncompressed)
        if (inner_width, inner_height) != (width, height):
            raise ContentError("BZip2-QOI inner dimensions do not match its outer header.")
        return _encode_png(rgba, width, height), width, height
    if data[:4] == b"fioq":
        rgba, width, height = _qoi_pixels(data)
        return _encode_png(rgba, width, height), width, height
    raise ContentError("Unsupported texture format; expected embedded PNG or GameMaker QOI.")


def _select_sprite(role: str, names: dict[str, int]) -> str | None:
    for alias in _ALIASES[role]:
        if alias in names:
            return alias
    # Numbered copies of the known down-facing Dark World family are accepted
    # only if exactly one exists. Never take an arbitrary substring match.
    family = re.compile(rf"spr_{role}_dw_(?:d|down)(?:_[0-9]+)?")
    matches = sorted(name for name in names if family.fullmatch(name))
    if len(matches) > 1:
        raise ContentError(f"Multiple supported {role} sprites found ({', '.join(matches)}); this build needs an explicit resource mapping.")
    return matches[0] if matches else None


def load_deltarune(game_dir: str | Path) -> DeltaruneAssets:
    """Import supported character frames from a local Chapter 2 installation.

    Raises ContentError with actionable diagnostics; no placeholder character
    artwork is substituted when a real resource is missing or unsupported.
    """
    path = discover_data_file(game_dir)
    try:
        size = path.stat().st_size
        if not 8 <= size <= MAX_ARCHIVE_BYTES:
            raise ContentError("Deltarune archive is empty or exceeds the supported 1 GiB size bound.")
        with path.open("rb") as source:
            data = source.read(MAX_ARCHIVE_BYTES + 1)
        if len(data) != size:
            raise ContentError("Deltarune archive changed while being read; close the game/updater and retry.")
    except OSError as exc:
        raise ContentError(f"Cannot read Deltarune's archive: {exc}") from exc
    archive = _Archive(data)
    # Chapter 2 should be established by content as well as folder selection.
    # Cyber City room / Queen resource names are retained in its STRG table.
    chapter2 = _CHAPTER_2.search(archive.game_name) or any(
        name.startswith(("room_dw_city", "spr_queen", "obj_queen"))
        for name in archive.strings.values()
    )
    if not chapter2:
        raise ContentError("This archive has no supported Chapter 2 content markers. Select Deltarune Chapter 2's data.win.")
    names = archive.sprite_names()
    sprites = {}
    for role in ("kris", "susie", "ralsei"):
        name = _select_sprite(role, names)
        if name is None:
            if role == "kris":
                raise ContentError("Dark World Kris sprite was not found. This Deltarune build needs a supported resource mapping (spr_kris_dw_d family).")
            continue
        sprites[role] = archive.sprite(name, names[name])
    return DeltaruneAssets(path, hashlib.sha256(data).hexdigest(), archive.game_name, sprites)
