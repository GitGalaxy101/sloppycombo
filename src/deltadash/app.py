"""Native pixel arcade, with mandatory content from both player-owned games."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys

os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
import pygame

from . import font
from .deltarune_content import load_deltarune, ContentError as DeltaruneError
from .gd_content import load_geometry_dash, ContentError as GeometryDashError
from .gameplay import Trial, BossTurn, block_bounds, spike_vertices
from .generated_design import SETTINGS, ROOM, RUNNER, BOSS, DIALOGUE, SCENES
from .progress import ProgressStore, default_save_directory

S = SETTINGS['arcade']
R = ROOM['cyber_city']
TR = RUNNER['cube_trial']
B = BOSS['quartermaster']
WHITE = (245, 244, 252)


@dataclass
class ContentBundle:
    deltarune: object
    level: object

    @property
    def key(self):
        return hashlib.sha256((self.deltarune.sha256 + ':' + self.level.sha256 + ':' + str(self.level.length)).encode()).hexdigest()


def load_content(game_dir, gd_dir, gd_save_dir=None):
    return ContentBundle(load_deltarune(game_dir), load_geometry_dash(gd_dir, gd_save_dir))


def decode_frames(sprite):
    pages, frames = {}, []
    for frame in sprite.frames:
        key = hashlib.sha256(frame.png).digest()
        try:
            if key not in pages:
                pages[key] = pygame.image.load(io.BytesIO(frame.png), 'page.png').convert_alpha()
            crop = pages[key].subsurface(frame.source).copy()
            canvas = pygame.Surface(frame.canvas, pygame.SRCALPHA)
            x, y, width, height = frame.target
            canvas.blit(pygame.transform.scale(crop, (width, height)), (x, y))
            frames.append(canvas)
        except (pygame.error, ValueError, MemoryError) as error:
            raise DeltaruneError('The Kris texture could not be rendered. This archive format needs compatibility testing.') from error
    if not frames:
        raise DeltaruneError('The required Dark World Kris sprite has no renderable frames.')
    return frames


class Arcade:
    def __init__(self, content: ContentBundle, store: ProgressStore, muted=False):
        self.content, self.store = content, store
        self.frames = decode_frames(content.deltarune.sprites['kris'])
        self.scene = SCENES['title'].id
        self.time = 0.0
        self.scene_time = 0.0
        self.running = True
        self.paused = False
        self.muted = muted
        self.player_x, self.player_y = float(R.start_x), float(R.start_y)
        self.trial = Trial(content.level)
        self.boss = BossTurn()
        self.menu = 0
        self.cabinet_menu = False
        self.notice = ''
        self.canvas = pygame.Surface((S.width, S.height))
        self.music_ready = False
        if not muted:
            try:
                if not pygame.mixer.get_init():
                    pygame.mixer.init()
                pygame.mixer.music.load(str(content.level.music_path))
                pygame.mixer.music.set_volume(0.65)
                self.music_ready = True
            except pygame.error as error:
                raise GeometryDashError('The real GD soundtrack could not play. Check audio output, or launch with --mute.') from error

    def change(self, scene):
        self.scene, self.scene_time = scene, 0.0
        self.menu = 0
        self.notice = ''

    def stop_music(self):
        if self.music_ready and pygame.mixer.get_init():
            pygame.mixer.music.stop()

    def begin_trial(self):
        self.trial.reset()
        self.store.begin_attempt()
        self.change('trial')
        self.cabinet_menu = False
        if self.music_ready:
            offset = float(getattr(self.content.level, 'song_offset', 0.0))
            try:
                pygame.mixer.music.play(start=max(0.0, offset))
            except pygame.error as error:
                raise GeometryDashError('The GD song could not restart at its level offset.') from error

    def begin_boss(self):
        if not self.store.progress.unlocked:
            return False
        self.boss.reset()
        self.stop_music()
        self.cabinet_menu = False
        self.change('boss')
        return True

    def key(self, key):
        confirm = key in (pygame.K_z, pygame.K_RETURN, pygame.K_SPACE)
        if key == pygame.K_ESCAPE:
            self.paused = not self.paused
            if self.music_ready and self.scene == 'trial':
                pygame.mixer.music.pause() if self.paused else pygame.mixer.music.unpause()
            return
        if self.paused:
            if key == pygame.K_q:
                self.running = False
            return
        if self.scene == 'title' and confirm:
            self.change(S.room_scene)
        elif self.scene == 'room':
            if self.cabinet_menu:
                choices = 3 if self.store.progress.unlocked else 2
                if key in (pygame.K_UP, pygame.K_w):
                    self.menu = (self.menu - 1) % choices
                elif key in (pygame.K_DOWN, pygame.K_s):
                    self.menu = (self.menu + 1) % choices
                elif key == pygame.K_x:
                    self.cabinet_menu = False
                elif confirm:
                    if self.menu == 0:
                        self.begin_trial()
                    elif self.menu == 1 and self.store.progress.unlocked:
                        self.begin_boss()
                    else:
                        self.cabinet_menu = False
            elif confirm and math.hypot(self.player_x - R.cabinet_x, self.player_y - R.cabinet_y) <= R.interact_radius:
                self.cabinet_menu, self.menu = True, 0
        elif self.scene == 'retry':
            if key == pygame.K_x:
                self.change('room')
            elif confirm:
                self.begin_trial()
        elif self.scene == 'unlock' and confirm:
            self.begin_boss()
        elif self.scene == 'boss' and self.boss.state == 'menu':
            if key in (pygame.K_LEFT, pygame.K_a):
                self.menu = (self.menu - 1) % 3
            elif key in (pygame.K_RIGHT, pygame.K_d):
                self.menu = (self.menu + 1) % 3
            elif confirm:
                choice = ('FIGHT', 'ACT', 'SPARE')[self.menu]
                if not self.boss.choose(choice):
                    self.notice = 'THE MACHINE STILL WANTS ITS COIN.'
                elif choice == 'ACT':
                    self.notice = DIALOGUE['act'].text
                else:
                    self.notice = ''
        elif self.scene == 'boss_retry':
            if key == pygame.K_x:
                self.change('room')
            elif confirm:
                self.begin_boss()
        elif self.scene == 'victory' and confirm:
            self.change('room')

    def update(self, dt, held=None):
        held = held or {}
        if self.paused:
            return
        dt = min(max(0.0, dt), S.max_frame_seconds)
        self.time += dt
        self.scene_time += dt
        pressed = lambda *keys: any(held.get(k, False) for k in keys)
        if self.scene == 'room' and not self.cabinet_menu:
            dx = int(pressed(pygame.K_RIGHT, pygame.K_d)) - int(pressed(pygame.K_LEFT, pygame.K_a))
            dy = int(pressed(pygame.K_DOWN, pygame.K_s)) - int(pressed(pygame.K_UP, pygame.K_w))
            length = max(1, math.hypot(dx, dy))
            self.player_x = min(R.right, max(R.left, self.player_x + dx / length * R.speed * dt))
            self.player_y = min(R.bottom, max(R.top, self.player_y + dy / length * R.speed * dt))
        elif self.scene == 'trial':
            self.trial.update(dt, jump=pressed(pygame.K_SPACE, pygame.K_z, pygame.K_UP, pygame.K_w, 'mouse'))
            if self.trial.status == 'dead':
                self.store.record_trial(self.trial.progress)
                self.stop_music()
                self.change(TR.failure_scene)
            elif self.trial.status == 'cleared':
                self.store.record_trial(1.0, cleared=True)
                self.stop_music()
                self.change(TR.success_scene)
        elif self.scene == 'boss':
            dx = int(pressed(pygame.K_RIGHT, pygame.K_d)) - int(pressed(pygame.K_LEFT, pygame.K_a))
            dy = int(pressed(pygame.K_DOWN, pygame.K_s)) - int(pressed(pygame.K_UP, pygame.K_w))
            self.boss.update(dt, dx=dx, dy=dy, focus=pressed(pygame.K_LSHIFT, pygame.K_RSHIFT))
            if self.boss.state == 'victory':
                self.store.record_boss()
                self.change(B.win_scene)
            elif self.boss.state == 'defeat':
                self.change('boss_retry')

    def box(self, rect, color=WHITE, fill=(8, 6, 21)):
        pygame.draw.rect(self.canvas, fill, rect)
        pygame.draw.rect(self.canvas, color, rect, 1)

    def text(self, text, x, y, color=WHITE, scale=1, center=False):
        font.draw(self.canvas, str(text), (x, y), color, scale, center)

    def panel(self, text, footer='Z / ENTER TO CONTINUE', color=WHITE):
        self.box((12, 169, 296, 61), color)
        for i, line in enumerate(font.wrap(text, 46)[:3]):
            self.text(line, 21, 181 + i * 11)
        self.text(footer, 160, 218, color, center=True)

    def city(self):
        self.canvas.fill(S.background)
        for i in range(13):
            x = i * 28 - 12
            height = 18 + (i * 19 % 54)
            pygame.draw.rect(self.canvas, (25, 17, 48), (x, 105-height, 24, height))
            for y in range(110-height, 100, 9):
                for offset in (5, 15):
                    color = (41, 55, 81) if (i+y) % 3 else (73, 38, 84)
                    pygame.draw.rect(self.canvas, color, (x+offset, y, 3, 3))
        for n in range(20):
            x = (n * 41 + 17) % 320
            y = (n * 23 + 7) % 76
            pygame.draw.rect(self.canvas, (39, 36, 68), (x, y, 1, 1))

    def cabinet(self, x, y, boss=False):
        bob = int(math.sin(self.time * 3) * 2) if boss else 0
        y += bob
        pygame.draw.rect(self.canvas, (30, 15, 52), (x-23, y-68, 46, 66))
        pygame.draw.polygon(self.canvas, (70, 25, 91), [(x-20,y-68),(x+20,y-68),(x+25,y-54),(x+20,y-4),(x-20,y-4),(x-25,y-54)])
        pygame.draw.lines(self.canvas, S.pink, False, [(x-20,y-68),(x+20,y-68),(x+25,y-54),(x+20,y-4)], 2)
        self.box((x-17, y-58, 34, 29), S.cyan, (8, 25, 32))
        self.text('GD', x, y-53, S.gold, center=True)
        for line in range(4):
            pygame.draw.line(self.canvas, (15, 47, 55), (x-14,y-38+line*2), (x+13,y-38+line*2))
        pygame.draw.rect(self.canvas, (154, 54, 123), (x-22, y-25, 44, 5))
        pygame.draw.rect(self.canvas, S.cyan, (x-11, y-26, 3, 4))
        pygame.draw.rect(self.canvas, S.gold, (x+6, y-24, 4, 2))
        pygame.draw.rect(self.canvas, (13, 7, 24), (x-6, y-15, 12, 5))
        pygame.draw.line(self.canvas, S.gold, (x-4,y-13),(x+4,y-13))
        if boss:
            pygame.draw.rect(self.canvas, S.pink, (x-13,y-44,7,3))
            pygame.draw.rect(self.canvas, S.pink, (x+6,y-44,7,3))
            pygame.draw.line(self.canvas, S.gold, (x-26,y-30),(x-38,y-19),3)
            pygame.draw.line(self.canvas, S.gold, (x+26,y-30),(x+38,y-19),3)

    def kris(self, x, y, size=None, moving=False):
        image = self.frames[int(self.time * 6) % len(self.frames) if moving else 0]
        if size:
            ratio = min(size[0]/image.get_width(), size[1]/image.get_height())
            image = pygame.transform.scale(image, (max(1,int(image.get_width()*ratio)), max(1,int(image.get_height()*ratio))))
        self.canvas.blit(image, (int(x-image.get_width()/2), int(y-image.get_height())))

    def render(self):
        self.city()
        if self.scene == 'title':
            self.text('DELTADASH', 160, 28, S.cyan, 3, True)
            self.text('CYBER CITY ARCADE', 160, 59, S.pink, 1, True)
            self.cabinet(207, 153)
            self.kris(99, 157)
            self.text('TWO GAMES. ONE LAST COIN.', 160, 164, WHITE, center=True)
            self.text('Z / ENTER TO START', 160, 190, S.gold, center=True)
            self.text('NATIVE PLAYER-COPY PROTOTYPE', 160, 218, (126,117,151), center=True)
        elif self.scene == 'room':
            pygame.draw.rect(self.canvas, (27,17,44), (17,111,286,87))
            pygame.draw.rect(self.canvas, (43,25,64), (17,111,286,6))
            for y in range(135, 195, 14):
                pygame.draw.line(self.canvas, (37,23,55), (18,y),(302,y))
                for x in range(24+(y%28),300,28):
                    pygame.draw.line(self.canvas,(37,23,55),(x,y),(x,y+14))
            self.text('CHAPTER 2 / CYBER CITY', 160, 13, S.pink, center=True)
            self.text('THE LAST ARCADE', 160, 31, S.cyan, 2, True)
            self.cabinet(R.cabinet_x, R.cabinet_y)
            self.kris(self.player_x,self.player_y,moving=self.scene_time>0)
            near = math.hypot(self.player_x-R.cabinet_x,self.player_y-R.cabinet_y)<=R.interact_radius
            self.text('BOSS CLEAR' if self.store.progress.boss_beaten else 'BOSS READY' if self.store.progress.unlocked else 'BONUS BOSS LOCKED', 23, 121, S.gold)
            if self.cabinet_menu:
                self.box((87,144,194,77),S.cyan)
                choices=['PLAY CUBE TRIAL']
                if self.store.progress.unlocked:
                    choices+=['FACE QUARTERMASTER']
                choices+=['LEAVE']
                for i, choice in enumerate(choices):
                    self.text(('> ' if i==self.menu else '  ')+choice,96,157+i*17,S.gold if i==self.menu else WHITE)
            else:
                self.box((12,203,296,27),S.pink)
                self.text('Z: PLAY THE CABINET' if near else 'ARROWS / WASD: WALK TO THE CABINET',160,213,WHITE,center=True)
        elif self.scene in ('trial','retry'):
            self.render_trial()
            if self.scene == 'retry':
                self.box((39,66,242,93),S.pink)
                self.text('ONE MORE TRY?',160,81,S.pink,2,True)
                self.text(f'BEST {self.store.progress.best*100:.0f}% / ATTEMPT {self.store.progress.attempts}',160,111,WHITE,center=True)
                self.text('Z / SPACE: RETRY   X: LEAVE',160,138,S.gold,center=True)
        elif self.scene == 'unlock':
            self.cabinet(160,151,boss=True)
            self.text('CUBE TRIAL CLEAR',160,21,S.cyan,2,True)
            self.text('BONUS BOSS UNLOCKED',160,49,S.gold,center=True)
            self.panel(DIALOGUE['unlock'].text)
        elif self.scene in ('boss','boss_retry'):
            self.render_boss()
            if self.scene == 'boss_retry':
                self.box((44,83,232,78),S.pink)
                self.text('YOUR COIN IS SAFE',160,97,S.pink,center=True)
                self.text('Z: RETRY BOSS   X: LEAVE',160,125,WHITE,center=True)
                self.text('THE CABINET STAYS UNLOCKED',160,144,S.gold,center=True)
        elif self.scene == 'victory':
            self.cabinet(160,143)
            self.kris(88,150)
            self.text('KEEP THE CHANGE',160,28,S.gold,2,True)
            self.text('BONUS BOSS CLEAR',160,54,S.cyan,center=True)
            self.panel(DIALOGUE['spare'].text,'Z: RETURN TO CYBER CITY',S.gold)
        if self.store.warning:
            self.text('SAVE UNAVAILABLE: SESSION PROGRESS ONLY',160,2,S.gold,center=True)
        if self.paused:
            shade=pygame.Surface((320,240),pygame.SRCALPHA);shade.fill((0,0,0,190));self.canvas.blit(shade,(0,0))
            self.box((44,78,232,85),S.cyan)
            self.text('PAUSED',160,93,S.cyan,2,True)
            self.text('ESC: RESUME   Q: QUIT',160,126,WHITE,center=True)
            self.text('F11: WINDOW / FULLSCREEN',160,145,(143,132,159),center=True)
        return self.canvas

    def render_trial(self):
        fraction=self.trial.progress
        self.text(self.content.level.name[:42],10,10,S.cyan)
        self.text('CUBE SECTION' if self.content.level.is_segment else 'CUBE COURSE',10,23,S.pink)
        self.text(f'{fraction*100:.0f}%',305,10,S.gold,center=True)
        pygame.draw.rect(self.canvas,(44,29,59),(10,37,300,3))
        pygame.draw.rect(self.canvas,S.cyan,(10,37,int(300*fraction),3))
        scale=TR.render_scale
        camera=max(-95,self.trial.runner.x-95/scale)
        ground=190
        for y in range(63,190,24):
            pygame.draw.line(self.canvas,(34,22,49),(0,y),(320,y))
        for x in range(int(-camera*scale)%24,320,24):
            pygame.draw.line(self.canvas,(34,22,49),(x,56),(x,190))
        pygame.draw.rect(self.canvas,(44,23,62),(0,ground,320,30))
        pygame.draw.line(self.canvas,S.cyan,(0,ground),(320,ground),2)
        point=lambda x,y:(int((x-camera)*scale),int(ground-y*scale))
        for obj in self.content.level.objects:
            if not -50 < (obj.x-camera)*scale < 370:
                continue
            if obj.kind=='block':
                left,bottom,right,top=block_bounds(obj)
                x,y=point(left,top);width=max(1,int((right-left)*scale));height=max(1,int((top-bottom)*scale))
                pygame.draw.rect(self.canvas,(36,72,85),(x,y,width,height))
                pygame.draw.rect(self.canvas,S.cyan,(x,y,width,height),1)
                pygame.draw.rect(self.canvas,(63,132,143),(x+3,y+3,max(1,width-6),max(1,height-6)),1)
            elif obj.kind=='spike':
                points=[point(x,y) for x,y in spike_vertices(obj)]
                pygame.draw.polygon(self.canvas,S.pink,points)
                pygame.draw.lines(self.canvas,WHITE,True,points,1)
        x,y=point(self.trial.runner.x+TR.size/2,self.trial.runner.y)
        self.kris(x,y,size=(int(TR.size*scale),int(TR.size*scale*1.7)),moving=True)
        end_x,_=point(self.content.level.length,0)
        if end_x<340:
            pygame.draw.line(self.canvas,S.gold,(end_x,57),(end_x,ground),2)
            self.text('CLEAR',end_x,49,S.gold,center=True)
        self.text('SPACE / Z / CLICK: JUMP',160,218,WHITE,center=True)

    def render_boss(self):
        self.canvas.fill((6,4,15))
        self.text(B.name,10,9,S.pink)
        self.text(f'HP {self.boss.hp}/{B.hp}',200,9,S.cyan)
        self.cabinet(160,93,boss=True)
        pygame.draw.rect(self.canvas,(75,35,52),(110,98,100,3))
        pygame.draw.rect(self.canvas,S.pink,(110,98,int(100*self.boss.hp/B.hp),3))
        self.box((B.box_x,B.box_y,B.box_width,B.box_height),WHITE,(4,3,12))
        if self.boss.state=='dodging':
            for bullet in self.boss.bullets:
                pygame.draw.circle(self.canvas,bullet.color,(int(bullet.x),int(bullet.y)),max(1,int(bullet.radius)))
                pygame.draw.rect(self.canvas,(255,249,209),(int(bullet.x),int(bullet.y-1),1,2))
            if self.boss.invulnerability_remaining<=0 or int(self.time*12)%2==0:
                self.heart(self.boss.soul_x,self.boss.soul_y)
            self.text('ARROWS: DODGE / SHIFT: FOCUS',160,196,S.cyan,center=True)
        else:
            text=self.notice or 'RETURN THE COIN, OR SHAKE THE MACHINE.'
            for i,line in enumerate(font.wrap(text,24)[:4]):
                self.text(line,B.box_x+10,B.box_y+12+i*13)
        self.text(f'KRIS HP {self.boss.soul_hp}/{B.soul_hp}',9,222,WHITE)
        self.text(f'MERCY {self.boss.mercy}%',226,222,S.gold)
        for i,label in enumerate(('FIGHT','ACT','SPARE')):
            color=S.gold if i==self.menu and self.boss.state=='menu' else (110,83,104)
            self.box((10+i*102,204,96,14),color)
            self.text(label,58+i*102,207,color,center=True)

    def heart(self,x,y):
        points=[(-4,-2),(-2,-4),(0,-2),(2,-4),(4,-2),(4,0),(0,4),(-4,0)]
        pygame.draw.polygon(self.canvas,(255,57,89),[(int(x+a),int(y+b)) for a,b in points])


def error_screen(message, display):
    surface=pygame.Surface((S.width,S.height))
    surface.fill(S.background)
    font.draw(surface,'CANNOT START YET',(160,25),S.pink,2,True)
    for i,line in enumerate(font.wrap(message,47)[:12]):
        font.draw(surface,line,(19,58+i*11),WHITE)
    font.draw(surface,'ESC / CLOSE TO EXIT',(160,221),S.gold,1,True)
    display.blit(pygame.transform.scale(surface,display.get_size()),(0,0));pygame.display.flip()
    clock=pygame.time.Clock()
    while True:
        for event in pygame.event.get():
            if event.type==pygame.QUIT or event.type==pygame.KEYDOWN and event.key in (pygame.K_ESCAPE,pygame.K_q):
                return
        clock.tick(30)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-dir',type=Path,required=True,help='Deltarune installation folder; supplied by Melty.')
    parser.add_argument('--gd-dir',type=Path,required=True,help='Geometry Dash installation folder; supplied by Melty.')
    parser.add_argument('--gd-save-dir',type=Path,help='Optional saved-level location; built-in course is preferred.')
    parser.add_argument('--save-dir',type=Path,default=default_save_directory())
    parser.add_argument('--check-content',action='store_true',help='Validate player content without starting a window.')
    parser.add_argument('--mute',action='store_true',help='Play without audio output.')
    args=parser.parse_args(argv)
    try:
        content=load_content(args.game_dir,args.gd_dir,args.gd_save_dir)
    except (DeltaruneError,GeometryDashError,OSError) as error:
        print(f'DeltaDash cannot start: {error}',file=sys.stderr)
        if not args.check_content:
            try:
                pygame.display.init()
                display=pygame.display.set_mode((S.width*S.scale,S.height*S.scale))
                pygame.display.set_caption(S.title)
                error_screen(str(error),display)
            except pygame.error:
                pass
            finally:
                pygame.quit()
        return 2
    if args.check_content:
        print(json.dumps({'status':'content-readable','deltarune_sha256':content.deltarune.sha256,'sprite':content.deltarune.sprites['kris'].name,'course':content.level.name,'gd_sha256':content.level.sha256,'objects':len(content.level.objects),'section':content.level.is_segment,'music_present':content.level.music_path.is_file(),'real_gameplay_tested':False},indent=2))
        return 0
    try:
        pygame.display.init()
        display=pygame.display.set_mode((S.width*S.scale,S.height*S.scale),pygame.RESIZABLE)
        pygame.display.set_caption(S.title)
        game=Arcade(content,ProgressStore(args.save_dir,content.key),args.mute)
        clock=pygame.time.Clock()
        fullscreen=False
        while game.running:
            dt=clock.tick(S.fps)/1000
            for event in pygame.event.get():
                if event.type==pygame.QUIT:
                    game.running=False
                elif event.type==pygame.KEYDOWN:
                    if event.key==pygame.K_F11:
                        fullscreen=not fullscreen
                        display=pygame.display.set_mode((0,0) if fullscreen else (S.width*S.scale,S.height*S.scale),pygame.FULLSCREEN if fullscreen else pygame.RESIZABLE)
                    else:
                        game.key(event.key)
                elif event.type==pygame.MOUSEBUTTONDOWN and event.button==1 and game.scene!='trial':
                    game.key(pygame.K_z)
            keys=pygame.key.get_pressed()
            held={key:keys[key] for key in (pygame.K_UP,pygame.K_DOWN,pygame.K_LEFT,pygame.K_RIGHT,pygame.K_w,pygame.K_a,pygame.K_s,pygame.K_d,pygame.K_z,pygame.K_SPACE,pygame.K_LSHIFT,pygame.K_RSHIFT)}
            held['mouse']=pygame.mouse.get_pressed()[0]
            game.update(dt,held)
            screen=game.render()
            width,height=display.get_size()
            factor=max(1,min(width//S.width,height//S.height))
            fitted=pygame.transform.scale(screen,(S.width*factor,S.height*factor))
            display.fill((0,0,0))
            display.blit(fitted,((width-fitted.get_width())//2,(height-fitted.get_height())//2))
            pygame.display.flip()
        game.stop_music()
        return 0
    except (DeltaruneError,GeometryDashError,pygame.error) as error:
        print(f'DeltaDash cannot play: {error}',file=sys.stderr)
        if pygame.display.get_surface():
            error_screen(str(error),pygame.display.get_surface())
        return 2
    finally:
        pygame.quit()


if __name__=='__main__':
    raise SystemExit(main())
