"""Deterministic, renderer-independent arcade simulation.

Course coordinates remain Geometry Dash world coordinates (30 units per block,
y increasing upward).  The cube physics are an approximation, not a measured
reimplementation of Geometry Dash's engine. Boss coordinates use screen y-down.
All game tuning comes from the validated design sheets.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
import math
from typing import TYPE_CHECKING, Any

from .generated_design import (
    BOSS_QUARTERMASTER,
    PATTERNS,
    RUNNER_CUBE_TRIAL,
    SETTINGS_ARCADE,
    BossSpec,
    PatternSpec,
    RunnerSpec,
)

if TYPE_CHECKING:
    from .gd_content import Level

_EPSILON = 1e-9
_STEP = 1.0 / SETTINGS_ARCADE.tick_hz


@dataclass(frozen=True)
class Input:
    jump: bool = False
    dx: float = 0.0
    dy: float = 0.0
    focus: bool = False


@dataclass
class Runner:
    x: float = 0.0  # left edge
    y: float = 0.0  # bottom edge
    vy: float = 0.0
    grounded: bool = True
    status: str = "alive"


def block_bounds(obj: Any) -> tuple[float, float, float, float]:
    """Return left, bottom, right, top for an imported centered cube block."""
    half = RUNNER_CUBE_TRIAL.block_units * obj.scale / 2
    return obj.x - half, obj.y - half, obj.x + half, obj.y + half


def spike_vertices(obj: Any) -> tuple[tuple[float, float], ...]:
    """An upright triangle transformed by GD's clockwise world rotation."""
    half = RUNNER_CUBE_TRIAL.block_units * obj.scale / 2
    angle = -math.radians(obj.rotation)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    return tuple(
        (obj.x + x * cos_a - y * sin_a, obj.y + x * sin_a + y * cos_a)
        for x, y in ((-half, -half), (half, -half), (0.0, half))
    )


def _bounded_dt(dt: float) -> float:
    if not math.isfinite(dt) or dt < 0:
        raise ValueError("Frame duration must be finite and nonnegative")
    return min(dt, SETTINGS_ARCADE.max_frame_seconds)


def _strict_overlap(
    x: float, y: float, size: float, bounds: tuple[float, float, float, float]
) -> bool:
    left, bottom, right, top = bounds
    return (
        x + size > left + _EPSILON and x < right - _EPSILON
        and y + size > bottom + _EPSILON and y < top - _EPSILON
    )


def _sweep_block(
    x: float, y: float, dx: float, dy: float, size: float,
    bounds: tuple[float, float, float, float],
) -> tuple[float, str] | None:
    """Continuous AABB collision; mere sliding along a top is permitted."""
    left, bottom, right, top = bounds
    if _strict_overlap(x, y, size, bounds):
        return 0.0, "inside"
    enter, leave = [], []
    for position, velocity, low, high in (
        (x, dx, left - size, right), (y, dy, bottom - size, top)
    ):
        if abs(velocity) < _EPSILON:
            if position <= low + _EPSILON or position >= high - _EPSILON:
                return None
            enter.append(-math.inf)
            leave.append(math.inf)
        else:
            first, last = (low - position) / velocity, (high - position) / velocity
            enter.append(min(first, last))
            leave.append(max(first, last))
    time = max(enter)
    if time < -_EPSILON or time > 1 + _EPSILON or time > min(leave) + _EPSILON:
        return None
    if min(leave) <= _EPSILON and time < -_EPSILON:
        return None
    vertical = enter[1] >= enter[0] - _EPSILON
    side = "top" if vertical and dy < 0 else "underside" if vertical else "side"
    return max(0.0, min(1.0, time)), side


def _swept_triangle(
    old_x: float, old_y: float, new_x: float, new_y: float, size: float,
    triangle: tuple[tuple[float, float], ...],
) -> bool:
    """Continuous separating-axis test avoids both tunnelling and box spikes."""
    axes = [(1.0, 0.0), (0.0, 1.0)]
    for index, (x, y) in enumerate(triangle):
        next_x, next_y = triangle[(index + 1) % len(triangle)]
        axes.append((-(next_y - y), next_x - x))
    square = (
        (old_x, old_y), (old_x + size, old_y),
        (old_x + size, old_y + size), (old_x, old_y + size),
    )
    entry, exit_time = 0.0, 1.0
    for ax, ay in axes:
        cube = [x * ax + y * ay for x, y in square]
        spike = [x * ax + y * ay for x, y in triangle]
        low_c, high_c, low_s, high_s = min(cube), max(cube), min(spike), max(spike)
        speed = (new_x - old_x) * ax + (new_y - old_y) * ay
        if abs(speed) < _EPSILON:
            if high_c < low_s - _EPSILON or low_c > high_s + _EPSILON:
                return False
        else:
            first, last = (low_s - high_c) / speed, (high_s - low_c) / speed
            entry = max(entry, min(first, last))
            exit_time = min(exit_time, max(first, last))
            if entry > exit_time + _EPSILON:
                return False
    return entry <= exit_time + _EPSILON


@dataclass
class Trial:
    level: Level
    spec: RunnerSpec = RUNNER_CUBE_TRIAL
    runner: Runner = field(default_factory=Runner)
    _accumulator: float = field(default=0.0, init=False, repr=False)
    _held: bool = field(default=False, init=False, repr=False)
    _jump_buffer: float = field(default=0.0, init=False, repr=False)
    _obstacles: list[Any] = field(default_factory=list, init=False, repr=False)
    _centers: list[float] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.level.length) or self.level.length <= 0:
            raise ValueError("Imported course must have a positive, finite length")
        for obj in self.level.objects:
            if obj.kind not in {"block", "spike", "decoration", "cube_portal"}:
                raise ValueError(f"Unsupported course object kind: {obj.kind}")
            if obj.kind in {"block", "spike"}:
                if obj.scale != 1 or not math.isclose(obj.rotation % 90, 0, abs_tol=_EPSILON):
                    raise ValueError("Course collision supports unit scale and quarter-turn rotations")
                if not all(math.isfinite(value) for value in (obj.x, obj.y, obj.rotation)):
                    raise ValueError("Course coordinates must be finite")
                self._obstacles.append(obj)
        self._obstacles.sort(key=lambda obj: obj.x)
        self._centers = [obj.x for obj in self._obstacles]

    @property
    def status(self) -> str:
        return self.runner.status

    @property
    def progress(self) -> float:
        return max(0.0, min(1.0, self.runner.x / self.level.length))

    def reset(self) -> None:
        self.runner = Runner()
        self._accumulator = 0.0
        self._held = False
        self._jump_buffer = 0.0

    def update(self, dt: float, jump: bool | Input = False) -> None:
        dt = _bounded_dt(dt)
        if self.status != "alive":
            return
        held = jump.jump if isinstance(jump, Input) else bool(jump)
        if held and not self._held:
            self._jump_buffer = self.spec.jump_buffer_seconds
        self._held = held
        self._accumulator += dt
        while self._accumulator + _EPSILON >= _STEP and self.status == "alive":
            self._step(_STEP)
            self._accumulator = max(0.0, self._accumulator - _STEP)

    def _nearby(self, old_x: float, new_x: float) -> list[Any]:
        half = self.spec.block_units / 2
        first = bisect_left(self._centers, min(old_x, new_x) - half)
        last = bisect_right(self._centers, max(old_x, new_x) + self.spec.size + half)
        return self._obstacles[first:last]

    def _step(self, dt: float) -> None:
        runner = self.runner
        if runner.grounded and (self._held or self._jump_buffer > 0):
            runner.vy = abs(self.spec.jump_velocity)
            runner.grounded = False
            self._jump_buffer = 0.0
        self._jump_buffer = max(0.0, self._jump_buffer - dt)
        old_x, old_y = runner.x, runner.y
        runner.vy -= self.spec.gravity * dt
        dx, dy = self.spec.speed * dt, runner.vy * dt
        next_x, next_y = old_x + dx, old_y + dy
        objects = self._nearby(old_x, next_x)
        collisions: list[tuple[float, str, float]] = []
        for obj in objects:
            if obj.kind == "block":
                bounds = block_bounds(obj)
                hit = _sweep_block(old_x, old_y, dx, dy, self.spec.size, bounds)
                if hit is not None:
                    collisions.append((hit[0], hit[1], bounds[3]))
        if dy < 0 and old_y >= 0 and next_y <= 0:
            collisions.append((old_y / -dy, "top", 0.0))
        runner.grounded = False
        segments = [(old_x, old_y, next_x, next_y)]
        if collisions:
            time, face, top = min(collisions, key=lambda hit: hit[0])
            impact_x, impact_y = old_x + dx * time, old_y + dy * time
            if face != "top":
                runner.x, runner.y = impact_x, impact_y
                runner.status = "dead"
                return
            next_y = top
            runner.vy = 0.0
            runner.grounded = True
            segments = [(old_x, old_y, impact_x, top), (impact_x, top, next_x, top)]
            # A landing does not allow sliding through the side of a taller block.
            for obj in objects:
                if obj.kind != "block":
                    continue
                bounds = block_bounds(obj)
                side = _sweep_block(impact_x, top, next_x - impact_x, 0.0, self.spec.size, bounds)
                if side is not None or _strict_overlap(next_x, top, self.spec.size, bounds):
                    runner.x, runner.y = impact_x, top
                    runner.status = "dead"
                    return
        for obj in objects:
            if obj.kind == "spike" and any(
                _swept_triangle(x, y, end_x, end_y, self.spec.size, spike_vertices(obj))
                for x, y, end_x, end_y in segments
            ):
                runner.x, runner.y = next_x, next_y
                runner.status = "dead"
                return
        runner.x, runner.y = next_x, next_y
        if next_y < self.spec.fall_limit:
            runner.status = "dead"
        elif next_x >= self.level.length:
            runner.x = self.level.length
            runner.status = "cleared"


@dataclass
class Bullet:
    x: float
    y: float
    vx: float
    vy: float
    radius: float
    damage: int
    color: tuple[int, int, int]


def _segment_distance_squared(
    x: float, y: float, old_x: float, old_y: float, new_x: float, new_y: float
) -> float:
    dx, dy = new_x - old_x, new_y - old_y
    length = dx * dx + dy * dy
    fraction = max(0.0, min(1.0, ((x - old_x) * dx + (y - old_y) * dy) / length)) if length else 0.0
    return (x - old_x - dx * fraction) ** 2 + (y - old_y - dy * fraction) ** 2


@dataclass
class BossTurn:
    spec: BossSpec = BOSS_QUARTERMASTER
    state: str = field(default="menu", init=False)
    hp: int = field(init=False)
    soul_hp: int = field(init=False)
    mercy: int = field(default=0, init=False)
    soul_x: float = field(init=False)
    soul_y: float = field(init=False)
    bullets: list[Bullet] = field(default_factory=list, init=False)
    elapsed: float = field(default=0.0, init=False)
    invulnerability_remaining: float = field(default=0.0, init=False)
    pattern_id: str = field(init=False)
    result: str | None = field(default=None, init=False)
    _accumulator: float = field(default=0.0, init=False, repr=False)
    _spawn_clock: float = field(default=0.0, init=False, repr=False)
    _wave: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.reset()

    @property
    def pattern(self) -> PatternSpec:
        return PATTERNS[self.pattern_id]

    def reset(self) -> None:
        self.state = "menu"
        self.hp, self.soul_hp, self.mercy = self.spec.hp, self.spec.soul_hp, 0
        self.soul_x = self.spec.box_x + self.spec.box_width / 2
        self.soul_y = self.spec.box_y + self.spec.box_height / 2
        self.bullets.clear()
        self.elapsed = self.invulnerability_remaining = 0.0
        self.pattern_id = self.spec.pattern
        self.result = None
        self._accumulator = self._spawn_clock = 0.0
        self._wave = 0

    def choose(self, choice: str) -> bool:
        """Confirm a menu action. Reaching 100 mercy still requires SPARE."""
        if self.state != "menu":
            return False
        action = choice.lower()
        if action == "fight":
            self.hp = max(0, self.hp - self.spec.fight_damage)
            if self.hp == 0:
                self.state, self.result = "victory", "fight"
                return True
        elif action == "act":
            self.mercy = min(100, self.mercy + self.spec.act_mercy)
        elif action == "spare":
            if self.mercy < 100:
                return False
            self.state, self.result = "victory", "spare"
            self.bullets.clear()
            return True
        else:
            return False
        self.state = "dodging"
        self.elapsed = self._accumulator = self._spawn_clock = 0.0
        self._wave = 0
        self.bullets.clear()
        self.soul_x = self.spec.box_x + self.spec.box_width / 2
        self.soul_y = self.spec.box_y + self.spec.box_height / 2
        return True

    def update(self, dt: float, dx: float | Input = 0.0, dy: float = 0.0, focus: bool = False) -> None:
        dt = _bounded_dt(dt)
        if self.state != "dodging":
            return
        if isinstance(dx, Input):
            dx, dy, focus = dx.dx, dx.dy, dx.focus
        if not math.isfinite(dx) or not math.isfinite(dy):
            raise ValueError("Soul input must be finite")
        self._accumulator += dt
        while self._accumulator + _EPSILON >= _STEP and self.state == "dodging":
            self._step(_STEP, dx, dy, focus)
            self._accumulator = max(0.0, self._accumulator - _STEP)

    def _spawn(self) -> None:
        pattern = self.pattern
        left, top = self.spec.box_x, self.spec.box_y
        width, height = self.spec.box_width, self.spec.box_height
        center_x, center_y = left + width / 2, top + height / 2
        outside = pattern.radius + self.spec.soul_radius
        if pattern.kind == "fan":
            # A 120-degree downward fan starts completely above the arena.
            for index in range(pattern.count):
                angle = -math.pi / 3 + index * (2 * math.pi / 3) / (pattern.count - 1)
                self.bullets.append(Bullet(
                    center_x, top - outside,
                    math.sin(angle) * pattern.speed, math.cos(angle) * pattern.speed,
                    pattern.radius, pattern.damage, pattern.color,
                ))
        elif pattern.kind == "lanes":
            # Leave one deterministic double-width passage, advancing each wave.
            gap = self._wave % (pattern.count + 1)
            for index in range(pattern.count + 1):
                if index == gap:
                    continue
                self.bullets.append(Bullet(
                    left + (index + 1) * width / (pattern.count + 2), top - outside,
                    0.0, pattern.speed, pattern.radius, pattern.damage, pattern.color,
                ))
        elif pattern.kind == "ring":
            radius = math.hypot(width / 2, height / 2) + outside
            for index in range(pattern.count):
                angle = index * 2 * math.pi / pattern.count + self._wave * math.pi / pattern.count
                cosine, sine = math.cos(angle), math.sin(angle)
                self.bullets.append(Bullet(
                    center_x + cosine * radius, center_y + sine * radius,
                    -cosine * pattern.speed, -sine * pattern.speed,
                    pattern.radius, pattern.damage, pattern.color,
                ))
        else:
            raise ValueError(f"Unsupported bullet pattern: {pattern.kind}")
        self._wave += 1

    def _step(self, dt: float, dx: float, dy: float, focus: bool) -> None:
        self.elapsed += dt
        self.invulnerability_remaining = max(0.0, self.invulnerability_remaining - dt)
        if self.elapsed + _EPSILON >= self.spec.turn_seconds:
            self.elapsed = self.spec.turn_seconds
            self.state = "menu"
            self.bullets.clear()
            self.pattern_id = self.pattern.next_pattern
            self._accumulator = 0.0
            return
        length = math.hypot(dx, dy)
        if length > 1:
            dx, dy = dx / length, dy / length
        speed = self.spec.soul_speed * (self.spec.soul_focus_factor if focus else 1.0)
        old_soul_x, old_soul_y = self.soul_x, self.soul_y
        radius = self.spec.soul_radius
        self.soul_x = min(self.spec.box_x + self.spec.box_width - radius,
                          max(self.spec.box_x + radius, self.soul_x + dx * speed * dt))
        self.soul_y = min(self.spec.box_y + self.spec.box_height - radius,
                          max(self.spec.box_y + radius, self.soul_y + dy * speed * dt))
        self._spawn_clock += dt
        while self._spawn_clock + _EPSILON >= self.pattern.interval:
            self._spawn()
            self._spawn_clock = max(0.0, self._spawn_clock - self.pattern.interval)
        surviving = []
        # The ring begins outside the box, so culling uses its full spawn radius.
        margin = math.hypot(self.spec.box_width / 2, self.spec.box_height / 2) + self.pattern.radius + radius
        for bullet in self.bullets:
            old_x, old_y = bullet.x, bullet.y
            bullet.x += bullet.vx * dt
            bullet.y += bullet.vy * dt
            relative_x, relative_y = old_x - old_soul_x, old_y - old_soul_y
            end_x, end_y = bullet.x - self.soul_x, bullet.y - self.soul_y
            hit = _segment_distance_squared(0.0, 0.0, relative_x, relative_y, end_x, end_y)
            if self.invulnerability_remaining <= _EPSILON and hit <= (bullet.radius + radius) ** 2:
                self.soul_hp = max(0, self.soul_hp - bullet.damage)
                self.invulnerability_remaining = self.spec.invulnerability
                if self.soul_hp == 0:
                    self.state = "defeat"
                    self.bullets.clear()
                    return
                continue
            if (
                self.spec.box_x - margin <= bullet.x <= self.spec.box_x + self.spec.box_width + margin
                and self.spec.box_y - margin <= bullet.y <= self.spec.box_y + self.spec.box_height + margin
            ):
                surviving.append(bullet)
        self.bullets = surviving
