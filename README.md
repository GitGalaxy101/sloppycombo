# DeltaDash Arcade

A **solo native prototype** for a Chapter 2 / Cyber City arcade mashup of
Deltarune and Geometry Dash. Working title; listing text and permissions have
not yet been approved.

## What the player gets

- Walk Kris around an original Cyber City side room and use its arcade cabinet.
- Play the first supported **cube section of Stereo Madness**, importing the
  actual course geometry and music from the player's Geometry Dash installation.
  The display uses a 320x240 canvas, pixel lettering, and nearest-neighbor scaling.
- Clearing that section unlocks the **Quartermaster**, an original bonus boss
  with coin fans, receipt lanes, and glitch rings. FIGHT or ACT, then dodge;
  returning the coin three times enables SPARE.
- Fast retries, pause, fullscreen, best progress, and a persistent boss unlock.

This uses Melty's supported own-copy native rewrite route. It runs an original
side-room program using Deltarune character sprites; it does **not** insert a
cabinet into the original Chapter 2 executable or its story/save. It does not
reproduce a whole GD level or claim exact native GD physics.

## Required copies and compatibility

Both installed games are mandatory. No game assets, music, levels, executable,
or saves are included in the package. Missing/unsupported data stops gameplay;
there is no made-up replacement level or character.

Deltarune: a Chapter 2 `data.win` (Windows), `game.unx` (Linux), or `game.ios`
(macOS). The reader requires Dark World Kris, validates Chapter 2 identity,
and supports bitmap SPRT/TPAG with embedded PNG/QOI texture pages. Unsupported
archives fail explicitly. Geometry Dash: `Resources/LevelData.plist` key `1`
plus the local `StereoMadness.mp3` track. Existing Windows/Proton saved courses
are optional alternatives when their static cube mechanics and music are
supported. Native macOS AES save files are unsupported.

**No retail game version has been tested yet.** The cloud workspace cannot
access the player's PC or its games. Format fixtures and runtime tests establish
development behavior, not compatibility with a particular installed release.
Unknown triggers, modes, or changed archive formats may need another reader pass.

## Windows prototype

Download the [Windows test ZIP](https://github.com/GitGalaxy101/sloppycombo/releases/download/v0.1.0-dev/DeltaDash-v0.1.0-dev-windows.zip)
from the [prototype prerelease](https://github.com/GitGalaxy101/sloppycombo/releases/tag/v0.1.0-dev)
(Windows 10/11 x64). The generated ZIP is excluded from Git history and attached
as a release asset; the repository contains the source and build scripts.
It bundles CPython, pygame/SDL, native DLLs, their notices and corresponding
pygame source. Python or a separate runtime need not be installed by the player.
**The Windows executable has not been run here.**

Extract the ZIP and double-click `DeltaDash/launch.cmd`. The local launcher
looks for both installed Steam games. Melty instead passes both game folders
directly, without asking the player for paths. If a local Steam library cannot
be found, the explicit developer launch is:

```bat
DeltaDash\runtime\python.exe -I DeltaDash\launch.py --game-dir "C:\path\to\DELTARUNE" --gd-dir "C:\path\to\Geometry Dash"
```

Add `--check-content` to produce a parser diagnostic without opening a window.
See [the local test instructions](docs/PLAYER-TEST.md) for the real-copy checks
still needed. This is a local test build, not a submitted/public Melty release.

## Controls

| Place | Controls |
| --- | --- |
| Side room | Arrows/WASD to walk; Z/Enter to interact |
| Cabinet | Up/down to choose; Z/Enter to confirm; X to leave |
| Cube trial | Space/Z/up/click to jump; hold for repeated jumps |
| Boss menu | Left/right to choose FIGHT/ACT/SPARE; Z/Enter to confirm |
| Boss attacks | Arrows/WASD to dodge; Shift for precise movement |
| Retry | Z/Space to retry; X to return to the room |
| Anywhere | Escape pauses; Q while paused exits; F11 changes fullscreen |

The mashup writes only its own progression under
`%LOCALAPPDATA%/DeltaDashArcade` (or Linux XDG data). Neither game's folder or
save is modified. `--save-dir` selects a separate lab progression directory.

## Development

Python 3.12 and `uv`:

```sh
uv sync --frozen --extra dev
.venv/bin/python tools/preflight.py
.venv/bin/python tools/generate_design.py --check
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy .venv/bin/python -m pytest -q
```

The JSON sheets are the design source of truth. Each row generates a typed
declaration in `src/deltadash/generated_design.py`. Preflight lists every
unfilled/invalid cell and broken reference; fix these before building. Change
the sheet first, then run `tools/generate_design.py`. Its `--check` mode catches
stale generated code. Sheet verification checks design contracts; real-game
verification remains separate and is recorded in [VALIDATION.md](docs/VALIDATION.md).

With accessible copies, launch the native app with:

```sh
.venv/bin/python -m deltadash --game-dir /path/to/DELTARUNE --gd-dir /path/to/GeometryDash --save-dir /tmp/deltadash-lab
```

Build the Windows package after clean preflight and passing tests:

```sh
.venv/bin/python scripts/package_windows.py
```

The packager uses checked upstream artifacts pinned in its runtime lockfile;
it produces a deterministic ZIP and SHA-256 sidecar. Neither game is read during
packaging. All synthetic fixtures, game-derived data, and publishing credentials
are excluded. See [runtime notices](docs/RUNTIME-LICENSES.md).

## Publishing status

[docs/melty.recipe-proposal.json](docs/melty.recipe-proposal.json) records the
proposed standalone recipe: Deltarune primary, Geometry Dash required companion,
both directories passed to the bundled program. The root `melty.json` remains
local and excluded from Git until the user approves committing the recipe,
because Melty uses that filename for automatic publishing. Package
inspection and recipe checks are recorded in `artifacts/` locally. A structural
one-click result alone does not prove the installation or gameplay works.

Before uploading or publishing: run on real copies, verify the Windows launch
and Melty install, capture real gameplay, agree the title/description,
creator credit, source/content license and remix choice, then obtain explicit
publication approval. No Melty listing or release has been created or submitted.

Credits and the pending licensing choices are in [CREDITS.md](CREDITS.md).
