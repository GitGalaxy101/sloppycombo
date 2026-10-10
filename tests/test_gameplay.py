"""Synthetic geometry only: these fixtures are never available as game content."""

from dataclasses import replace
import math
from pathlib import Path

import pytest

from deltadash.gd_content import Level, Object
from deltadash.gameplay import (
    BossTurn, Bullet, Input, Trial, _swept_triangle, spike_vertices,
)
from deltadash.generated_design import (
    BOSS_QUARTERMASTER, PATTERNS, RUNNER_CUBE_TRIAL, SETTINGS_ARCADE,
)


TICK = 1 / SETTINGS_ARCADE.tick_hz


def synthetic_level(*objects, length=600):
    """The metadata labels this fixture synthetic and has no actual music."""
    return Level(
        name="SYNTHETIC COLLISION TEST", objects=tuple(objects),
        music_path=Path("SYNTHETIC-NOT-PLAYABLE.mp3"), length=length,
        source=Path("SYNTHETIC-NOT-GAME-CONTENT"), sha256="0" * 64,
    )


def advance(simulation, seconds, **controls):
    for _ in range(round(seconds * SETTINGS_ARCADE.tick_hz)):
        simulation.update(TICK, **controls)


def quiet_turn(boss, monkeypatch):
    """Isolate menu/progression rules; pattern hazards have separate tests."""
    monkeypatch.setattr(boss, "_spawn", lambda: None)
    advance(boss, boss.spec.turn_seconds)


def test_ground_course_clears_at_finish_and_freezes():
    trial = Trial(synthetic_level(length=100))
    advance(trial, 0.5)
    assert trial.status == "cleared"
    assert trial.progress == 1
    assert trial.runner.x == 100
    assert trial.runner.y == 0
    trial.update(0.25, jump=True)
    assert trial.runner.x == 100


@pytest.mark.parametrize("kind,object_id", [("block", 1), ("spike", 8)])
def test_unjumped_course_hazards_kill_before_finish(kind, object_id):
    trial = Trial(synthetic_level(Object(object_id, 90, 15, kind)))
    advance(trial, 0.5)
    assert trial.status == "dead"
    assert 0 < trial.progress < 1
    frozen = replace(trial.runner)
    trial.update(0.25, jump=True)
    assert trial.runner == frozen


def test_jump_clears_ground_spike_preserving_geometry():
    spike = Object(8, 80, 15, "spike")
    level = synthetic_level(spike, length=150)
    trial = Trial(level)
    trial.update(TICK, jump=True)
    advance(trial, 0.6)
    assert trial.status == "cleared"
    assert trial.level is level and level.objects == (spike,)


def test_platform_top_landing_is_safe_but_side_collision_is_fatal():
    block = Object(1, 125, 30, "block")
    landing = Trial(synthetic_level(block))
    landing.update(TICK, jump=True)
    advance(landing, 0.4 - TICK)
    assert landing.status == "alive"
    assert landing.runner.grounded
    assert landing.runner.y == 45
    assert landing.runner.vy == 0
    side = Trial(synthetic_level(block))
    advance(side, 0.5)
    assert side.status == "dead"


def test_block_underside_collision_is_fatal():
    trial = Trial(synthetic_level(Object(1, 45, 75, "block")))
    advance(trial, 0.2, jump=True)
    assert trial.status == "dead"
    assert trial.runner.y + trial.spec.size == pytest.approx(60)


def test_adjacent_floor_blocks_have_no_false_side_collision():
    trial = Trial(synthetic_level(*(Object(1, x, -15, "block") for x in range(15, 200, 30)), length=180))
    advance(trial, 0.7)
    assert trial.status == "cleared"


def test_spike_bounding_corner_is_safe_but_crossing_triangle_is_not():
    triangle = spike_vertices(Object(8, 0, 0, "spike"))
    assert not _swept_triangle(-15, 10, -13, 10, 2, triangle)
    assert _swept_triangle(-15, 10, 15, 10, 2, triangle)


@pytest.mark.parametrize("rotation,apex", [(0, (0, 15)), (90, (15, 0)), (180, (0, -15)), (270, (-15, 0))])
def test_spike_rotations_preserve_clockwise_world_orientation(rotation, apex):
    triangle = spike_vertices(Object(8, 0, 0, "spike", rotation=rotation))
    assert triangle[-1] == pytest.approx(apex)


def test_holding_jump_jumps_again_on_landing():
    trial = Trial(synthetic_level(length=1000))
    advance(trial, 0.5, jump=True)
    assert trial.status == "alive"
    assert trial.runner.vy > 0
    assert not trial.runner.grounded
    assert trial.runner.y > 0


def test_tap_just_before_landing_is_buffered():
    trial = Trial(synthetic_level(length=1000))
    trial.update(TICK, jump=True)
    while trial.runner.vy >= 0 or trial.runner.y >= 10:
        trial.update(TICK, jump=False)
    trial.update(TICK, jump=True)
    advance(trial, 0.06, jump=False)
    assert not trial.runner.grounded
    assert trial.runner.vy > 0


def test_long_stall_is_capped_and_cannot_skip_hazard():
    level = synthetic_level(Object(8, 60, 15, "spike"))
    stalled, ordinary = Trial(level), Trial(level)
    stalled.update(30)
    advance(ordinary, SETTINGS_ARCADE.max_frame_seconds)
    assert stalled.status == ordinary.status == "dead"
    assert stalled.runner == ordinary.runner


def test_fixed_substeps_are_independent_of_frame_partition():
    level = synthetic_level(length=1000)
    a, b = Trial(level), Trial(level)
    for _ in range(60):
        a.update(1 / 60, Input(jump=True))
    for _ in range(120):
        b.update(TICK, jump=True)
    assert a.runner == b.runner


def test_retry_restores_full_initial_runner_state():
    trial = Trial(synthetic_level(Object(8, 60, 15, "spike")))
    trial.update(0.25)
    assert trial.status == "dead"
    trial.reset()
    assert trial.status == "alive" and trial.progress == 0
    assert trial.runner.y == trial.runner.vy == 0
    assert trial.runner.grounded
    trial.update(TICK, jump=True)
    assert trial.runner.y > 0


@pytest.mark.parametrize("invalid", [-1, math.inf, math.nan])
def test_invalid_elapsed_time_rejected(invalid):
    with pytest.raises(ValueError):
        Trial(synthetic_level()).update(invalid)
    with pytest.raises(ValueError):
        BossTurn().update(invalid)


@pytest.mark.parametrize("obj", [
    Object(99, 10, 15, "unsupported"), Object(8, 10, 15, "spike", rotation=45),
    Object(1, 10, 15, "block", scale=2),
])
def test_unsupported_collision_content_is_rejected(obj):
    with pytest.raises(ValueError):
        Trial(synthetic_level(obj))


def test_boss_menu_freezes_hazards_and_time():
    boss = BossTurn()
    bullet = Bullet(boss.soul_x, boss.soul_y, 40, 0, 3, 4, (1, 2, 3))
    boss.bullets.append(bullet)
    old_position = bullet.x, bullet.y
    boss.update(30, dx=1, dy=1)
    assert boss.state == "menu" and boss.elapsed == 0
    assert boss.soul_hp == boss.spec.soul_hp
    assert (bullet.x, bullet.y) == old_position


def test_turn_completes_clears_bullets_and_cycles_patterns(monkeypatch):
    boss = BossTurn()
    for expected in ("coin_fan", "receipt_lanes", "glitch_ring", "coin_fan"):
        assert boss.pattern_id == expected
        assert boss.choose("ACT")
        quiet_turn(boss, monkeypatch)
        assert boss.state == "menu"
        assert boss.elapsed == boss.spec.turn_seconds
        assert boss.bullets == []


def test_mercy_requires_explicit_spare_confirmation(monkeypatch):
    boss = BossTurn()
    assert not boss.choose("SPARE")
    assert boss.state == "menu"
    for mercy in (34, 68, 100):
        assert boss.choose("ACT")
        assert boss.mercy == mercy
        assert boss.state == "dodging"
        assert not boss.choose("SPARE")
        quiet_turn(boss, monkeypatch)
    assert boss.state == "menu" and boss.hp == boss.spec.hp
    assert boss.choose("SPARE")
    assert boss.state == "victory" and boss.result == "spare"


def test_six_confirmed_fights_win(monkeypatch):
    boss = BossTurn()
    for index in range(6):
        assert boss.choose("FIGHT")
        assert boss.hp == max(0, 90 - (index + 1) * 15)
        if index < 5:
            quiet_turn(boss, monkeypatch)
    assert boss.state == "victory" and boss.result == "fight"


def test_damage_grants_one_second_invulnerability(monkeypatch):
    boss = BossTurn()
    boss.choose("ACT")
    monkeypatch.setattr(boss, "_spawn", lambda: None)
    for _ in range(2):
        boss.bullets.append(Bullet(boss.soul_x, boss.soul_y, 0, 0, 3, 4, (255, 0, 0)))
    boss.update(TICK)
    assert boss.soul_hp == 16
    assert boss.invulnerability_remaining == pytest.approx(1)
    advance(boss, 0.75)
    assert boss.soul_hp == 16
    advance(boss, 0.25)
    assert boss.soul_hp == 12


def test_bullet_sweeps_cannot_tunnel_through_soul(monkeypatch):
    boss = BossTurn()
    boss.choose("ACT")
    monkeypatch.setattr(boss, "_spawn", lambda: None)
    boss.bullets.append(Bullet(boss.soul_x - 20, boss.soul_y, 6000, 0, 3, 4, (255, 0, 0)))
    boss.update(TICK)
    assert boss.soul_hp == 16


def test_defeat_and_retry_restore_entire_fight():
    boss = BossTurn()
    boss.choose("ACT")
    boss.soul_hp = 4
    boss.bullets.append(Bullet(boss.soul_x, boss.soul_y, 0, 0, 3, 4, (255, 0, 0)))
    boss.update(TICK)
    assert boss.state == "defeat" and boss.soul_hp == 0 and boss.bullets == []
    boss.reset()
    assert boss.state == "menu"
    assert boss.hp == 90 and boss.soul_hp == 20 and boss.mercy == 0
    assert boss.elapsed == boss.invulnerability_remaining == 0
    assert boss.pattern_id == BOSS_QUARTERMASTER.pattern


def test_soul_diagonal_normalization_focus_and_arena_bounds(monkeypatch):
    ordinary, focused = BossTurn(), BossTurn()
    for boss in (ordinary, focused):
        boss.choose("ACT")
        monkeypatch.setattr(boss, "_spawn", lambda: None)
    origin = ordinary.soul_x, ordinary.soul_y
    advance(ordinary, 0.1, dx=1, dy=1)
    advance(focused, 0.1, dx=1, dy=1, focus=True)
    normal_distance = math.hypot(ordinary.soul_x - origin[0], ordinary.soul_y - origin[1])
    focus_distance = math.hypot(focused.soul_x - origin[0], focused.soul_y - origin[1])
    assert normal_distance == pytest.approx(ordinary.spec.soul_speed * 0.1)
    assert focus_distance == pytest.approx(normal_distance * ordinary.spec.soul_focus_factor)
    advance(ordinary, 2, dx=1, dy=1)
    assert ordinary.soul_x <= ordinary.spec.box_x + ordinary.spec.box_width - ordinary.spec.soul_radius
    assert ordinary.soul_y <= ordinary.spec.box_y + ordinary.spec.box_height - ordinary.spec.soul_radius


@pytest.mark.parametrize("pattern_id", tuple(PATTERNS))
def test_patterns_are_deterministic_and_start_outside_arena(pattern_id):
    a, b = BossTurn(), BossTurn()
    for boss in (a, b):
        boss.pattern_id = pattern_id
        boss.choose("ACT")
        advance(boss, boss.pattern.interval)
    assert a.bullets == b.bullets
    assert len(a.bullets) == a.pattern.count
    spec = a.spec
    for bullet in a.bullets:
        assert math.hypot(bullet.vx, bullet.vy) == pytest.approx(a.pattern.speed)
        assert bullet.damage == a.pattern.damage
        assert (
            bullet.x + bullet.radius < spec.box_x
            or bullet.x - bullet.radius > spec.box_x + spec.box_width
            or bullet.y + bullet.radius < spec.box_y
            or bullet.y - bullet.radius > spec.box_y + spec.box_height
        )
        assert math.hypot(bullet.x - a.soul_x, bullet.y - a.soul_y) > bullet.radius + spec.soul_radius


def test_boss_fixed_steps_are_independent_of_frame_partition():
    a, b = BossTurn(), BossTurn()
    a.choose("ACT")
    b.choose("ACT")
    for _ in range(60):
        a.update(1 / 60, Input(dx=-1, dy=1, focus=True))
    for _ in range(120):
        b.update(TICK, dx=-1, dy=1, focus=True)
    assert a.soul_x == b.soul_x and a.soul_y == b.soul_y
    assert a.bullets == b.bullets
    assert a.elapsed == b.elapsed
