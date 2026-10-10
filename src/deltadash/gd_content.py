"""Read Geometry Dash content from a player's installation, never a bundled level.

Format references (public code/documentation, no official level dumps):
https://github.com/Folleach/GeometryDashAPI/blob/master/GeometryDashAPI/Data/GameResources.cs
https://github.com/WylieMaster/GDDocs/blob/master/docs/topics/levelstring_encoding_decoding.md
https://github.com/WylieMaster/GDDocs/blob/master/docs/topics/localfiles_encrypt_decrypt.md
https://github.com/nekitdev/gd.py/blob/main/gd/api/database/database.py
https://github.com/nekitdev/gd.py/blob/main/gd/api/levels.py
https://github.com/flowvix/gd-info-explorer/blob/main/src/lib/schema.txt
https://github.com/flowvix/gd-info-explorer/blob/main/src/lib/objects.csv

The installed Resources/LevelData.plist contains official level strings with
the first 13 base64 characters of their gzip header removed. The first normal
cube opening is an explicit segment, not completion of its full source level.
Windows/Proton saves are XOR(11), URL-safe base64, gzip. Native macOS AES saves
are intentionally unsupported by this stdlib reader; installed plist works
independently. Every file is opened read-only. No download or write occurs.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zlib

from .generated_design import GD_OBJECTS


MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_XML_BYTES = 64 * 1024 * 1024
MAX_LEVEL_BYTES = 8 * 1024 * 1024
MAX_OBJECTS = 100_000
MAX_XML_NODES = 1_000_000
MAX_XML_DEPTH = 32
MAX_COORDINATE = 10_000_000
_BASE64 = re.compile(rb"[A-Za-z0-9_+/\-]*={0,2}\Z")
_CATALOG = {spec.object_id: spec for spec in GD_OBJECTS.values()}

# File identity and track names are import metadata, not level content.
# Track zero's filename is evidenced by LevelTools::getAudioFileName output:
# https://github.com/lil2kki/Main-Levels-Config/issues/16
_OFFICIAL_SONGS = {0: "StereoMadness.mp3"}


class ContentError(ValueError):
    """The installed content is absent, malformed, or exceeds supported scope."""


@dataclass(frozen=True)
class Object:
    object_id: int
    x: float
    y: float
    kind: str
    rotation: float = 0.0
    scale: float = 1.0
    flip_x: bool = False
    flip_y: bool = False

    @property
    def width(self) -> float:
        return 30.0 * self.scale

    @property
    def height(self) -> float:
        return 30.0 * self.scale


@dataclass(frozen=True)
class Level:
    name: str
    objects: tuple[Object, ...]
    music_path: Path
    length: float
    source: Path
    sha256: str
    is_segment: bool = False
    source_description: str = "Player-owned Geometry Dash course"
    song_offset: float = 0.0


def _read_limited(path: Path, limit: int = MAX_FILE_BYTES) -> bytes:
    try:
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
    except OSError as error:
        raise ContentError(f"Cannot read {path.name}: {error.strerror or type(error).__name__}") from error
    if len(data) > limit:
        raise ContentError(f"{path.name} exceeds the {limit // 1024 // 1024} MiB import limit")
    if not data:
        raise ContentError(f"{path.name} is empty")
    return data


def _base64_decode(data: bytes) -> bytes:
    if not _BASE64.fullmatch(data) or len(data) % 4 == 1:
        raise ContentError("Invalid Geometry Dash base64 payload")
    if b"=" in data and len(data) % 4:
        raise ContentError("Invalid Geometry Dash base64 padding")
    try:
        return base64.b64decode(data + b"=" * (-len(data) % 4), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as error:
        raise ContentError("Invalid Geometry Dash base64 payload") from error


def _inflate_limited(data: bytes, limit: int) -> bytes:
    # Autodetect gzip/zlib; verify checksum and trailer. Disallow truncated,
    # concatenated, trailing-data, and decompression-bomb payloads.
    try:
        inflater = zlib.decompressobj(15 | 32)
        output = inflater.decompress(data, limit + 1)
        if len(output) > limit or inflater.unconsumed_tail:
            raise ContentError("Geometry Dash decompressed payload exceeds its limit")
        if not inflater.eof or inflater.unused_data:
            raise ContentError("Geometry Dash compressed payload is truncated or has trailing data")
        output += inflater.flush()
    except zlib.error as error:
        raise ContentError("Invalid Geometry Dash gzip/zlib payload or checksum") from error
    if len(output) > limit:
        raise ContentError("Geometry Dash decompressed payload exceeds its limit")
    return output


def decode_level_data(payload: str, *, official: bool = False) -> str:
    """Decode raw or compressed k4/official data with strict resource limits."""
    if not isinstance(payload, str):
        raise ContentError("Level data must be a string")
    try:
        encoded = payload.encode("ascii")
    except UnicodeEncodeError as error:
        raise ContentError("Geometry Dash level data is not ASCII") from error
    if len(encoded) > MAX_LEVEL_BYTES:
        raise ContentError("Geometry Dash level payload exceeds its limit")
    if ";" in payload and "," in payload:
        # Raw level strings are supported by the game and found in k4 fields.
        return payload
    if official and not encoded.startswith((b"H4sI", b"eJ")):
        encoded = b"H4sIAAAAAAAAA" + encoded
    decoded = _inflate_limited(_base64_decode(encoded), MAX_LEVEL_BYTES)
    try:
        return decoded.decode("ascii")
    except UnicodeDecodeError as error:
        raise ContentError("Decoded Geometry Dash level is not ASCII") from error


def decode_save(data: bytes) -> dict:
    """Read Windows/Proton .dat or already-plain XML. Do not modify saves."""
    if len(data) > MAX_FILE_BYTES:
        raise ContentError("Geometry Dash save exceeds its import limit")
    if data.lstrip().startswith(b"<"):
        xml = data
    else:
        payload = bytes(value ^ 11 for value in data).rstrip(b"\x00")
        try:
            xml = _inflate_limited(_base64_decode(payload), MAX_XML_BYTES)
        except ContentError as error:
            raise ContentError("Unreadable Geometry Dash save; supported formats are Windows/Proton XOR saves or plain XML. Native macOS AES saves are unsupported.") from error
    return parse_plist(xml)


def parse_plist(data: bytes) -> dict:
    """Parse both Apple and Cocos abbreviated plist tags without entities."""
    if len(data) > MAX_XML_BYTES:
        raise ContentError("Geometry Dash XML exceeds its import limit")
    # The fixed external Apple plist DOCTYPE is harmless and normal. Any
    # entity declaration/internal subset is rejected before ElementTree.
    if re.search(rb"<!ENTITY\b|<!DOCTYPE[^>]*\[", data, re.I):
        raise ContentError("Geometry Dash plist entity declarations are unsupported")
    if data.lstrip().startswith(b"bplist"):
        raise ContentError("Binary Geometry Dash plists are unsupported")
    try:
        root = ET.fromstring(data)
    except (ET.ParseError, ValueError) as error:
        raise ContentError("Invalid Geometry Dash plist XML") from error
    count = 0

    def value(node: ET.Element, depth: int):
        nonlocal count
        count += 1
        if depth > MAX_XML_DEPTH or count > MAX_XML_NODES:
            raise ContentError("Geometry Dash plist nesting or node count exceeds its limit")
        tag = node.tag
        if tag in ("dict", "d"):
            children = list(node)
            if len(children) % 2:
                raise ContentError("Geometry Dash plist dictionary has an unmatched key")
            result = {}
            for index in range(0, len(children), 2):
                key = children[index]
                if key.tag not in ("key", "k") or not key.text or list(key):
                    raise ContentError("Invalid Geometry Dash plist dictionary key")
                if key.text in result:
                    raise ContentError("Duplicate Geometry Dash plist dictionary key")
                result[key.text] = value(children[index + 1], depth + 1)
            return result
        if list(node):
            raise ContentError("Unexpected children in Geometry Dash plist value")
        if tag in ("string", "s"):
            return node.text or ""
        if tag in ("integer", "i"):
            try:
                return int(node.text or "0")
            except ValueError as error:
                raise ContentError("Invalid Geometry Dash plist integer") from error
        if tag in ("real", "r"):
            return _number(node.text or "0", "plist real")
        if tag in ("true", "t"):
            return True
        if tag in ("false", "f"):
            return False
        raise ContentError(f"Unsupported Geometry Dash plist tag {tag!r}")

    if root.tag != "plist" or len(root) != 1 or root[0].tag not in ("dict", "d"):
        raise ContentError("Geometry Dash plist must have one root dictionary")
    return value(root[0], 0)


def _number(raw: str, label: str) -> float:
    try:
        result = float(raw)
    except (ValueError, TypeError) as error:
        raise ContentError(f"Invalid {label}") from error
    if not math.isfinite(result) or abs(result) > MAX_COORDINATE:
        raise ContentError(f"Nonfinite or out-of-range {label}")
    return result


def _pairs(raw: str, label: str) -> dict[str, str]:
    parts = raw.split(",")
    if len(parts) % 2:
        raise ContentError(f"Unmatched key/value in {label}")
    result = {}
    for key, val in zip(parts[::2], parts[1::2]):
        if not key or key in result:
            raise ContentError(f"Empty or duplicate key in {label}")
        result[key] = val
    return result


def _boolean(properties: dict[str, str], key: str) -> bool:
    val = properties.get(key, "0")
    if val not in ("0", "1"):
        raise ContentError(f"Invalid Geometry Dash boolean property {key}")
    return val == "1"


def parse_level(
    raw: str,
    *,
    name: str,
    music_path: Path,
    source: Path,
    allow_cube_segment: bool = False,
    source_description: str = "Player-owned Geometry Dash course",
) -> Level:
    """Validate supported mechanics, retaining original coordinates and music."""
    try:
        raw_bytes = raw.encode("ascii")
    except (UnicodeEncodeError, AttributeError) as error:
        raise ContentError("Geometry Dash level string must be ASCII text") from error
    if len(raw_bytes) > MAX_LEVEL_BYTES:
        raise ContentError("Geometry Dash level exceeds its decoded limit")
    records = raw.strip().split(";")
    while records and records[-1] == "":
        records.pop()
    if len(records) < 2 or len(records) - 1 > MAX_OBJECTS:
        raise ContentError("Geometry Dash level is empty or has too many objects")
    header = _pairs(records[0], "level header")
    known_header = {f"kS{i}" for i in range(1, 40)} | {
        f"kA{i}" for i in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14,
                           15, 16, 17, 18, 20, 22, 25, 27, 28, 29, 31, 32,
                           33, 34, 36, 37, 38, 39, 40, 41, 42, 43, 45)
    }
    if set(header) - known_header:
        raise ContentError(f"Unsupported Geometry Dash header properties: {', '.join(sorted(set(header) - known_header))}")
    if _number(header.get("kA2", "0"), "starting mode") != 0:
        raise ContentError("Only an initial cube mode is supported")
    if _number(header.get("kA4", "0"), "starting speed") != 0:
        raise ContentError("Only an initial normal speed is supported")
    for key in ("kA3", "kA8", "kA9", "kA10", "kA11", "kA20", "kA22", "kA28", "kA29"):
        if _boolean(header, key):
            raise ContentError(f"Unsupported Geometry Dash player state {key}")
    if _number(header.get("kA36", "0"), "spawn group") != 0:
        raise ContentError("Spawn groups are unsupported")
    song_offset = _number(header.get("kA13", "0"), "song offset")
    if not 0 <= song_offset <= 3600:
        raise ContentError("Song offset is outside the supported range")

    parsed = []
    for index, record in enumerate(records[1:], 1):
        props = _pairs(record, f"object {index}")
        if not {"1", "2", "3"} <= props.keys():
            raise ContentError(f"Object {index} lacks its id or coordinates")
        try:
            object_id = int(props["1"])
        except ValueError as error:
            raise ContentError(f"Invalid object id in object {index}") from error
        if object_id <= 0:
            raise ContentError(f"Invalid object id in object {index}")
        x, y = _number(props["2"], "object x"), _number(props["3"], "object y")
        parsed.append((object_id, x, y, props))

    boundaries = [x for object_id, x, _, props in parsed
                  if object_id in _CATALOG and _CATALOG[object_id].kind == "boundary"
                  and x > 0 and not _boolean(props, "62") and not _boolean(props, "11")]
    boundary = min(boundaries) if allow_cube_segment and boundaries else None
    objects = []
    # Geometry is read strictly. These properties describe edit layers, color,
    # static groups and rendering only. Actual mechanics need explicit support.
    cosmetic = {"7", "8", "9", "13", "19", "20", "21", "22", "24", "25",
                "33", "34", "41", "42", "43", "44", "57", "61", "64",
                "67", "96", "103", "108", "115", "116", "155", "156"}
    geometry = {"1", "2", "3", "4", "5", "6", "32"}
    palette = {"10", "11", "14", "15", "16", "17", "23", "30", "35",
               "36", "45", "46", "47", "48", "49", "50", "51", "52",
               "60", "62", "86", "87", "104"}
    for object_id, x, y, props in parsed:
        if boundary is not None and x >= boundary:
            continue
        spec = _CATALOG.get(object_id)
        if spec is None or not spec.supported:
            raise ContentError(f"Unsupported Geometry Dash object {object_id} at x={x:g}; the original course cannot be played correctly")
        allowed = geometry | cosmetic
        if spec.kind == "decoration":
            allowed |= palette
        unknown = set(props) - allowed
        if unknown:
            raise ContentError(f"Unsupported properties {','.join(sorted(unknown))} on object {object_id}")
        if spec.kind != "decoration" and any(_boolean(props, key) for key in ("11", "36", "62")):
            raise ContentError(f"Triggered gameplay object {object_id} is unsupported")
        rotation = _number(props.get("6", "0"), "object rotation") % 360
        if not math.isclose(rotation % 90, 0, abs_tol=1e-8):
            raise ContentError(f"Object {object_id} has an unsupported free rotation")
        scale = _number(props.get("32", "1"), "object scale")
        if not 0.1 <= scale <= 8:
            raise ContentError("Object scaling is outside the supported positive range")
        flip_x, flip_y = _boolean(props, "4"), _boolean(props, "5")
        # Reflecting a square or vertical-axis spike changes no shape; vertical
        # reflection becomes an explicit half-turn consumed by the runtime.
        if spec.kind == "spike" and flip_y:
            rotation = (rotation + 180) % 360
        kind = "decoration" if spec.kind == "cube_portal" else spec.kind
        objects.append(Object(object_id, x, y, kind, rotation, scale, flip_x, flip_y))
    gameplay = [obj for obj in objects if obj.kind in ("block", "spike")]
    if not gameplay:
        raise ContentError("The supported cube section has no playable geometry")
    length = boundary if boundary is not None else max(obj.x + obj.width / 2 for obj in gameplay) + 180
    if length < 120:
        raise ContentError("The supported cube section is too short")
    label = source_description
    if boundary is not None:
        label += f" · first cube segment, ending before mode portal at x={boundary:g}"
    return Level(name[:128] or "Untitled local course", tuple(sorted(objects, key=lambda obj: obj.x)),
                 Path(music_path), length, Path(source), hashlib.sha256(raw_bytes).hexdigest(),
                 boundary is not None, label, song_offset)


def _resources(game_dir: Path) -> tuple[Path, ...]:
    return tuple(dict.fromkeys((game_dir / "Resources", game_dir / "Contents" / "Resources", game_dir)))


def _case_file(folder: Path, filename: str) -> Path | None:
    exact = folder / filename
    if exact.is_file():
        return exact
    try:
        return next((entry for entry in folder.iterdir() if entry.name.casefold() == filename.casefold() and entry.is_file()), None)
    except OSError:
        return None


def _song_path(resource_dirs: tuple[Path, ...], save_dirs: tuple[Path, ...], *, official_id: int = 0, custom_id: int = 0) -> Path:
    if custom_id < 0 or custom_id > 2_147_483_647:
        raise ContentError("Invalid Geometry Dash custom song id")
    if custom_id:
        filename = f"{custom_id}.mp3"
        folders = save_dirs + resource_dirs
    else:
        filename = _OFFICIAL_SONGS.get(official_id)
        if filename is None:
            raise ContentError(f"Official song {official_id} has no verified filename in this reader")
        folders = resource_dirs
    for folder in folders:
        path = _case_file(folder, filename)
        if path is not None:
            try:
                if path.stat().st_size > 0:
                    return path
            except OSError:
                continue
    raise ContentError(f"Required original Geometry Dash music {filename} was not found locally")


def find_save_dirs(game_dir: Path, save_dir: Path | None = None) -> tuple[Path, ...]:
    """Standard Windows/macOS and Steam Proton locations; no whole-disk scan."""
    candidates = []
    if save_dir is not None:
        candidates.append(Path(save_dir))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "GeometryDash")
    home = Path.home()
    candidates.extend((home / "AppData" / "Local" / "GeometryDash",
                       home / "Library" / "Application Support" / "GeometryDash"))
    steamapps = next((path for path in (game_dir, *game_dir.parents) if path.name.casefold() == "steamapps"), None)
    roots = [home / ".steam" / "steam" / "steamapps", home / ".local" / "share" / "Steam" / "steamapps"]
    if steamapps is not None:
        roots.insert(0, steamapps)
    for root in roots:
        user = root / "compatdata" / "322170" / "pfx" / "drive_c" / "users" / "steamuser"
        candidates.extend((user / "AppData" / "Local" / "GeometryDash",
                           user / "Local Settings" / "Application Data" / "GeometryDash"))
    return tuple(dict.fromkeys(path for path in candidates if path.is_dir()))


def load_geometry_dash(game_dir: Path | str, save_dir: Path | str | None = None) -> Level:
    """Automatically use installed Stereo Madness opening; saves are optional.

    Requires a readable owned LevelData.plist and StereoMadness.mp3, or an
    already-present compatible saved/created normal-cube course and its actual
    local song. The player never needs to make an exported level for this API.
    No-authentic-content is a blocking error, with no authored fallback.
    """
    game_dir = Path(game_dir)
    if not game_dir.is_dir():
        raise ContentError("Geometry Dash installation directory does not exist")
    resource_dirs = _resources(game_dir)
    save_dirs = find_save_dirs(game_dir, Path(save_dir) if save_dir is not None else None)
    errors = []
    for folder in resource_dirs:
        source = _case_file(folder, "LevelData.plist")
        if source is None:
            continue
        try:
            levels = parse_plist(_read_limited(source))
            payload = levels.get("1")
            if not isinstance(payload, str) or not payload:
                raise ContentError("LevelData.plist has no official level 1 string")
            raw = decode_level_data(payload, official=True)
            music_path = _song_path(resource_dirs, save_dirs)
            return parse_level(raw, name="Stereo Madness", music_path=music_path, source=source,
                               allow_cube_segment=True, source_description="Owned Resources/LevelData.plist, official level 1")
        except ContentError as error:
            errors.append(f"{source.name}: {error}")
    for folder in save_dirs:
        for filename, container in (("CCGameManager.dat", "GLM_03"), ("CCLocalLevels.dat", "LLM_01")):
            source = _case_file(folder, filename)
            if source is None:
                continue
            try:
                records = decode_save(_read_limited(source)).get(container, {})
                if not isinstance(records, dict):
                    raise ContentError(f"Invalid {container} level dictionary")
                for record in records.values():
                    if not isinstance(record, dict) or not isinstance(record.get("k4"), str) or not record["k4"]:
                        continue
                    try:
                        raw = decode_level_data(record["k4"])
                        custom_id = int(record.get("k45", 0))
                        official_id = int(record.get("k8", 0))
                        if int(record.get("k43", 0)):
                            raise ContentError("Two-player courses are unsupported")
                        music_path = _song_path(resource_dirs, save_dirs, official_id=official_id, custom_id=custom_id)
                        return parse_level(raw, name=str(record.get("k2", "Local course")), music_path=music_path,
                                           source=source, source_description=f"Owned {source.name}, {container} course")
                    except (ContentError, ValueError, TypeError) as error:
                        if len(errors) < 6:
                            errors.append(f"{source.name} course: {error}")
            except ContentError as error:
                if len(errors) < 6:
                    errors.append(f"{source.name}: {error}")
    reason = "; ".join(errors[:6]) if errors else "No owned Resources/LevelData.plist and supported saved course were found"
    raise ContentError(f"Cannot import a real Geometry Dash cube trial. {reason}. Supported installed route: official level 1 opening plus local StereoMadness.mp3; no synthetic fallback is supplied.")
