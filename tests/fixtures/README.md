# Synthetic audio fixture

`synthetic-silence.mp3` is four seconds of newly generated silence. It is not a
track or an asset from either game. Tests place it under a documented filename
in a temporary synthetic installation to exercise SDL's actual MP3 decoding,
play, pause, and restart paths. This file is excluded from the runtime package.

Generated with the cloud machine's FFmpeg:

```
ffmpeg -f lavfi -i anullsrc=r=22050:cl=mono -t 4 -codec:a libmp3lame -q:a 9 synthetic-silence.mp3
```
