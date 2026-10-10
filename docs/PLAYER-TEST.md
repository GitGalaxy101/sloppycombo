# Smallest next real-game test

The cloud machine has neither game installed and cannot reach the player's PC.
It can build the prototype but cannot establish real-copy compatibility or
capture real gameplay. Keep publishing blocked until this test succeeds.

1. On Windows 10/11 x64 with both games installed through Steam, extract the
   local `DeltaDash-v0.1.0-dev-windows.zip`. Double-click `DeltaDash/launch.cmd`.
   It reads Steam's library list/manifests only and launches the bundled runtime;
   no Python download, loader install, registry edits, or game-file edits occur.
2. If the launcher cannot locate a game, use Steam **Manage > Browse local
   files** to identify that installation, then run the explicit launch in the
   main README. Melty's recipe passes both paths itself in the eventual listing.
3. Check that Kris appears, walk to the cabinet, and confirm that the real
   Stereo Madness opening and its music load. Clear the labeled cube section,
   fight or spare the Quartermaster, restart, and check the boss stays unlocked.
4. If it stops, send the error shown by the window or the non-secret text from
   `%LOCALAPPDATA%/DeltaDashArcade/logs/launcher.log`, plus the game versions.
   Don't send login files, whole Steam userdata, passwords, or keys.

The developer parser-only command is:

```bat
DeltaDash\runtime\python.exe -I DeltaDash\launch.py --game-dir "C:\path\to\DELTARUNE" --gd-dir "C:\path\to\Geometry Dash" --check-content
```

Its success means content was readable, not that the scene or music was played.
If there are multiple archives or an unsupported format, the reader explains
what needs inspection. No failed import should be replaced by a fake course.

Once this version is running with genuine files, capture its game window showing
the cabinet, pixel GD challenge, and bonus boss. A screenshot is sufficient.
The synthetic rendering diagnostics from cloud tests cannot be listing media.

Melty end-to-end testing comes after a working real-game run and agreed listing
metadata. The recipe's structural checks cannot substitute for pressing Play in
Melty and verifying that it installs and launches this exact package.
