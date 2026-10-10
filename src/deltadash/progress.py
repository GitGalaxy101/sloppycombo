"""The mashup's own progression only. Never opens a host-game save for writing."""

from dataclasses import dataclass, asdict
import json
import math
import os
from pathlib import Path
import tempfile


@dataclass
class Progress:
    best: float = 0.0
    unlocked: bool = False
    boss_beaten: bool = False
    attempts: int = 0


class ProgressStore:
    def __init__(self, directory: Path, content_key: str):
        self.directory = Path(directory)
        self.path = self.directory / 'progress.json'
        self.content_key = content_key
        self.warning = ''
        self.progress = Progress()
        self.load()

    def load(self):
        if not self.path.exists():
            return
        try:
            if self.path.stat().st_size > 64 * 1024:
                raise ValueError('Oversized save')
            value = json.loads(self.path.read_text())
            if value.get('schema') != 1 or value.get('content_key') != self.content_key:
                return
            data = value['progress']
            best = data['best']
            if type(best) not in (int, float) or not math.isfinite(best) or not 0 <= best <= 1:
                raise ValueError('Invalid progress')
            if any(type(data[k]) is not bool for k in ('unlocked', 'boss_beaten')):
                raise ValueError('Invalid flags')
            if type(data['attempts']) is not int or not 0 <= data['attempts'] <= 10**9:
                raise ValueError('Invalid attempts')
            if data['boss_beaten'] and not data['unlocked']:
                raise ValueError('Inconsistent progression')
            self.progress = Progress(**data)
        except (OSError, ValueError, KeyError, TypeError):
            self.warning = 'Your arcade save could not be read. Playing this session with a fresh record.'

    def begin_attempt(self):
        self.progress.attempts = min(10**9, self.progress.attempts + 1)
        self.save()

    def record_trial(self, fraction: float, cleared=False):
        if not math.isfinite(fraction) or not 0 <= fraction <= 1:
            raise ValueError('Trial progress is invalid')
        if cleared and fraction < 1:
            raise ValueError('A partial course cannot unlock the boss')
        self.progress.best = max(self.progress.best, fraction)
        self.progress.unlocked |= bool(cleared)
        self.save()

    def record_boss(self):
        if not self.progress.unlocked:
            raise ValueError('The boss is still locked')
        self.progress.boss_beaten = True
        self.save()

    def save(self):
        temporary = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            payload = {'schema': 1, 'content_key': self.content_key, 'progress': asdict(self.progress)}
            with tempfile.NamedTemporaryFile(mode='w', dir=self.directory, prefix='.progress-', suffix='.tmp', delete=False) as f:
                temporary = Path(f.name)
                json.dump(payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, self.path)
            self.warning = ''
        except OSError:
            self.warning = 'The cabinet cannot save here. Your progress lasts for this session.'
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def default_save_directory():
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
    else:
        base = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
    return base / 'DeltaDashArcade'
