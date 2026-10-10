# Portable runtime notices

DeltaDash's portable Windows prototype contains CPython 3.12.14 from the
Astral Python Build Standalone 20260924 release and pygame-ce 2.5.8's official
PyPI `cp312-win_amd64` wheel. Neither game's executable, data, music, graphics,
levels, or save files is redistributed. Game content is read from the player's
own installations at runtime.

The build pins HTTPS artifact URLs, byte sizes, and SHA-256 digests in
`scripts/windows-runtime.lock.json`. Digests for the interpreter are from uv
0.12.19's official download manifest; pygame digests are from PyPI metadata.
SDL notice bundles are the exact releases and SHA-1-verified downloads declared
by pygame-ce 2.5.8's own build configuration, additionally pinned here by SHA-256.
The build keeps TLS verification enabled and verifies every cached artifact.

## CPython and native dependencies

The runtime's unmodified `runtime/LICENSE.txt` retains the Python Software
Foundation license and bundled third-party notices, including the terms for
the Microsoft Visual C++ runtime DLLs. Supplementary notices in `licenses/`
are copied from the same Python Build Standalone release for bzip2, libffi,
OpenSSL 3, SQLite, liblzma, zlib, Tcl/Tk, and Tix. Tcl/Tk's distribution notices
also remain in the runtime tree. These components retain their upstream terms.

CPython source: <https://github.com/python/cpython/tree/v3.12.14>.
Interpreter build sources, patches, dependency versions, source URLs, and
checksums: <https://github.com/astral-sh/python-build-standalone/tree/20260924>.
The relevant native-library source links are in that release's
`pythonbuild/downloads.json`. No runtime binaries have been modified.
Unused headers, static import libraries, pip, and precompiled bytecode are
omitted from the portable bundle.

## pygame-ce and SDL

pygame-ce is licensed under LGPL 2.1 or later. Its full LGPL text is included
at `licenses/pygame-LGPL.txt`. The corresponding pygame-ce library sources
are included in `sources/pygame_ce-2.5.8-source-no-font.tar.gz`, including
its build configuration and source files. They are repacked from the verified
upstream source archive with the separately licensed default, example, and test
fixture fonts omitted. The library's source files remain unchanged.
The original source artifact's upstream URL and SHA-256 appear in the
accompanying runtime lockfile and build manifest.

pygame remains a separate dynamically loaded library in
`runtime/Lib/site-packages/pygame/`; its Python and native-extension files can
be inspected and replaced with a compatible build. DeltaDash's Python source
is included in `app/deltadash/`. The portable interpreter's search-path file
is included as `runtime/python312._pth`. These layouts permit users to debug
and run the application with a modified pygame library. No restriction is
placed on reverse engineering needed to debug such modifications.

The pygame wheel's DLLs are retained, including SDL2, SDL2_image, SDL2_mixer,
SDL2_ttf, FreeType, PortMidi, libjpeg, libpng, libtiff, WebP, Ogg, Opus,
Opusfile, WavPack, and libxmp. Upstream notices from pygame-ce's source
`docs/licenses/` and the pinned SDL2_mixer and SDL2_ttf distributions are
included in `licenses/`; this directory may contain notices for optional
libraries the wheel does not include. SDL's libraries use their upstream
zlib-style terms; the codec and font libraries retain their individual terms.
This software is based in part on the work of the FreeType Team.

The wheel's `pygame/freesansbold.ttf` default font and all example/test fixture
fonts are omitted from the runtime and source archive; those unused font assets
have separate license terms. DeltaDash supplies its own pixel lettering.
No font from either game is included in this package.
Unused pygame runtime documentation, examples, and tests are omitted; their
corresponding library sources and all collected license notices remain bundled.

## Verification limits

The Linux build checks each native binary's Windows x86-64 PE architecture
and imports, ensuring non-system DLL dependencies exist in the bundle. It
checks Python application syntax and the ZIP's CRCs. The build is reproducible
for the same repository files, pinned artifacts, Python, and zlib versions.
It does not execute the Windows runtime. A launch on Windows 10/11 x64 with
both games installed remains required before treating this prototype as a
Windows-tested release. Windows supplies its own system DLLs; it is not
necessary to install Python, pygame, or a separate Visual C++ runtime.
