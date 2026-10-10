"""Native-rendering integration with deliberately synthetic game-data fixtures.

These fixtures contain invented tiny images/course/audio, never retail assets.
Their screenshots are diagnostic test artifacts, not publishable gameplay media.
"""

from pathlib import Path
import shutil

import pytest

from test_deltarune_content import synthetic_archive
from test_gd_content import fake_install


@pytest.fixture
def arcade(tmp_path, monkeypatch):
    monkeypatch.setenv('SDL_VIDEODRIVER','dummy')
    monkeypatch.setenv('SDL_AUDIODRIVER','dummy')
    import pygame
    from deltadash.app import Arcade, load_content
    from deltadash.progress import ProgressStore
    pygame.display.init()
    pygame.display.set_mode((320,240))
    dr=tmp_path/'Deltarune'/'chapter2';dr.mkdir(parents=True)
    archive,_=synthetic_archive()
    (dr/'data.win').write_bytes(archive)
    # No hazards before the explicit mode boundary in this end-to-end fixture;
    # actual hazard safety/landing/death are verified separately by gameplay tests.
    gd=fake_install(tmp_path,raw='kA2,0,kA4,0,kA13,0;1,1,2,150,3,-15;1,13,2,600,3,90;')
    shutil.copyfile(Path(__file__).parent/'fixtures/synthetic-silence.mp3',gd/'Resources'/'StereoMadness.mp3')
    content=load_content(dr.parent,gd)
    game=Arcade(content,ProgressStore(tmp_path/'own-save',content.key),muted=False)
    yield game,dr,gd
    game.stop_music()
    pygame.quit()


def test_native_room_course_unlock_and_boss_entry(arcade):
    import pygame
    from deltadash.generated_design import ROOM
    game,dr,gd=arcade
    before=(dr/'data.win').read_bytes(),(gd/'Resources'/'LevelData.plist').read_bytes()
    assert not game.begin_boss()
    game.key(pygame.K_z)
    assert game.scene=='room'
    game.player_x=ROOM['cyber_city'].cabinet_x
    game.player_y=ROOM['cyber_city'].cabinet_y
    game.key(pygame.K_z)
    assert game.cabinet_menu
    game.key(pygame.K_z)
    assert game.scene=='trial' and game.music_ready
    for _ in range(160):
        game.update(1/60)
        image=game.render()
        assert image.get_size()==(320,240)
        if game.scene=='unlock':
            break
    assert game.scene=='unlock' and game.store.progress.unlocked
    assert game.store.progress.attempts==1
    game.key(pygame.K_z)
    assert game.scene=='boss'
    game.key(pygame.K_RIGHT) # ACT
    game.key(pygame.K_z)
    assert game.boss.state=='dodging' and game.boss.mercy==34
    game.update(1/60,{pygame.K_LEFT:True})
    game.render()
    assert before==((dr/'data.win').read_bytes(),(gd/'Resources'/'LevelData.plist').read_bytes())


def test_pause_freezes_simulation_and_trial_audio(arcade):
    import pygame
    game,_,_=arcade
    game.begin_trial();game.update(1/60)
    position=game.trial.runner.x
    game.key(pygame.K_ESCAPE)
    game.update(.2,{pygame.K_SPACE:True})
    assert game.trial.runner.x==position
    assert game.paused
    game.key(pygame.K_ESCAPE)
    game.update(1/60)
    assert game.trial.runner.x>position


def test_production_window_loop_loads_both_fixtures_and_exits(arcade,tmp_path):
    import pygame
    from deltadash.app import main
    game,dr,gd=arcade
    game.stop_music()
    pygame.event.post(pygame.event.Event(pygame.QUIT))
    assert main(['--game-dir',str(dr.parent),'--gd-dir',str(gd),'--save-dir',str(tmp_path/'main-save')])==0


def test_real_content_missing_is_error_and_never_fabricated(tmp_path,capsys):
    from deltadash.app import main
    assert main(['--game-dir',str(tmp_path/'missing-dr'),'--gd-dir',str(tmp_path/'missing-gd'),'--check-content'])==2
    assert 'installation folder does not exist' in capsys.readouterr().err


def test_all_scenes_render_using_native_surface(arcade,tmp_path):
    game,_,_=arcade
    for scene in ('title','room','trial','retry','unlock','boss','boss_retry','victory'):
        game.change(scene)
        image=game.render()
        assert image.get_bounding_rect().width==320
    # Owned renderer output only. Deliberately not saved as listing media.
    assert not list(tmp_path.rglob('*.png'))


def test_original_pixel_lettering_is_complete():
    from deltadash.font import GLYPHS,wrap
    assert all(len(g)==7 and all(len(row)==5 for row in g) for g in GLYPHS.values())
    assert wrap('THIS IS A SYNTHETIC TEST',9)==['THIS IS A','SYNTHETIC','TEST']
