# DeltaDash Arcade development journal

## Agreed concept

Chapter 2 / Cyber City: an optional arcade cabinet presents a real Geometry Dash
course in Deltarune's pixel style. Clearing the course unlocks a separate bonus
boss. Solo first playable. User authorized implementation and GitHub source
delivery. Melty publication still requires explicit approval.

## Route and evidence

The Melty API's `game_info` supports Deltarune own-copy native rewrites in
`standalone` mode, with files under `{managed}` and `{game}` passed at launch.
`custom-geometry-dash` is now a supported required companion; pass its directory
with `{game:custom-geometry-dash}`. Neither has an automatic loader. ULTRARUNE's
working recipe provides a reference for this installation pattern; this package
remains unverified with actual game copies. This is an original Cyber City
side-room runtime, not a patch that inserts a cabinet into the original game or
edits its Chapter 2 save.

Cloned official `rehan-remade/universal-modder` into `/tmp/universal-modder`, read
`skills/mod-any-game/SKILL.md`, and ran its knowledge searches and installed-game
scan. No matching Deltarune/GD field notes; neither game is accessible in this
cloud environment. No player game folders, settings, or saves will be modified.
Runtime progression belongs only to this mashup's own data directory.

## First-slice boundary

The cabinet's CUBE TRIAL uses the first supported cube section of an installed
Geometry Dash level, stopping before an unsupported mode portal. It does not
claim to reproduce a complete GD level or its native physics exactly. Authentic
level geometry and soundtrack are mandatory; there is no fabricated fallback.
Deltarune Dark World Kris frames are read at runtime from Chapter 2 game data.

The original bonus boss is the Quartermaster, a malfunctioning arcade cabinet.
The player dodges coin/receipt patterns and chooses ACT or FIGHT between turns.
Art for the cabinet, room and boss is newly authored procedural pixel art.

## Remaining evidence

Synthetic fixtures can verify binary parsing, progression and collisions, but do
not verify compatibility with an actual game version. Need accessible owned
Chapter 2 Deltarune files and GD Resources for a real-game run, a Windows launch,
Melty installation, and gameplay media. Do not publish until those checks,
credits/license/remix choices and explicit publication approval are complete.
