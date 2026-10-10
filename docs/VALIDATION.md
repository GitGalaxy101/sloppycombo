# Validation evidence

## Confirmed in this cloud workspace

- All sheet values and cross-sheet references pass preflight; generated typed
  design declarations match the sheets.
- Deltarune parser tests use invented FORM/GEN8/STRG/SPRT/TPAG/TXTR archives.
  They test PNG/QOI decoding, cropping, chapter identity, bounds, ambiguity and
  corrupted-data rejection; they verify the source archives remain unchanged.
- GD parser tests use invented installed plists and saved-course records. They
  exercise the documented compressed header, save encoding, real coordinate
  preservation, required local music, segment boundary and unsupported mechanics.
- Pure gameplay tests exercise jumping, landing/death, continuous triangle
  collisions, fixed steps, retries, all boss patterns, invulnerability, menu
  transitions, ACT/SPARE, victory and defeat.
- The native pygame integration loads those synthetic files, decodes generated
  MP3 silence through SDL, renders the room/course/boss, clears the course and
  unlocks the fight, pauses, and saves only the mashup's own progression.

These tests use synthetic content. They do not show either retail game running,
provide publishable gameplay footage, or verify native Geometry Dash parity.

## Required and still unverified

- Deltarune Chapter 2 archive/sprite identities from an actual supported version.
- GD retail LevelData.plist and StereoMadness.mp3 import, playable opening,
  timing, collision fairness and actual audio synchronization.
- Windows portable interpreter/SDL launch and input/audio on a real Windows PC.
- This package's installation and launch through Melty with both required games.
- A genuine gameplay screenshot/clip, listing metadata/permissions, and publish
  authorization.

The package builder verifies checksums/TLS, native Windows architecture and DLL
imports, Python syntax, archive safety and ZIP CRCs. Melty's package/recipe
inspection reports live under ignored `artifacts/`; their structural result must
not be presented as the missing real gameplay and installation checks.
