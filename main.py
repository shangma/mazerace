import kivy
kivy.require('1.9.1')

from kivy.app import App
from kivy.uix.widget import Widget
from kivy.uix.popup import Popup
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.popup import Popup
from kivy.graphics import Color, Rectangle, Ellipse
from kivy.metrics import sp
from kivy.event import EventDispatcher
from kivy.properties import NumericProperty, StringProperty, BooleanProperty, \
    ListProperty, ReferenceListProperty, ObjectProperty 
from kivy.vector import Vector
from kivy.clock import Clock
from kivy.logger import Logger
from kivy import platform
from kivy.config import Config
from kivy.lib import osc
import plyer
import json

from service.game import Gamenet, now, seconds_since, \
    player_color, player_corner_hint, home_corner, goal_corner, \
    player_markup, BOUNCE_FACTOR, DEBUG_OSC

class Player(EventDispatcher):
    index = NumericProperty(-1)
    color = ListProperty()
    corner_hint = ListProperty()
    name = StringProperty()
    row = NumericProperty(-1)
    col = NumericProperty(-1)

    def __init__(self, index):
        self.index = index
        self.color = player_color(index)
        self.corner_hint = player_corner_hint(index)

class MazeraceCell(Widget):
    row = None
    col = None
    gui = None
    goal_of = None

    def __init__(self, row, col, gui, goal_of = None, *args, **kwargs):
        super(MazeraceCell, self).__init__(*args, **kwargs)
        self.row = row
        self.col = col
        self.gui = gui
        self.goal_of = goal_of

    def redraw(self, win_color=None):
        self.canvas.clear()
        with self.canvas:
            if win_color is not None:
                win_color=win_color[:3]+[0.3]  # Transparency for contrast's sake
                Color(*win_color)
                Rectangle(pos=self.pos, size=self.size)
                Color(1, 1, 1, 1)
            for p in self.gui.players:
                if p.index!=self.gui.my_index and p.row==self.row and p.col==self.col:
                    Color(*p.color)
                    r, c = p.corner_hint
                    Rectangle(pos=(self.x+self.width*(0.05+c*.5), self.y+self.height*(0.05+r*0.5)), size=(self.width*0.25, self.height*0.25))
                    Color(1, 1, 1, 1)
            # Redraw walls
            for c in self.children:
                self.remove_widget(c)
                self.add_widget(c)

class MazeraceWall(Widget):
    color = ListProperty()
    vertical = BooleanProperty(False)

    def __init__(self, color=None, vertical=False, *args, **kwargs):
        self.color = color or [1, 1, 1, 1]
        self.vertical = vertical
        super(MazeraceWall, self).__init__(*args, **kwargs)

    def bounce_ball(self, ball):
        if self.collide_widget(ball):
            if self.vertical:
                ball.velocity_x = -BOUNCE_FACTOR*ball.velocity_x
                # uncollide
                overlap = (ball.width+self.width)/2.0-abs(ball.center_x-self.center_x)
                if ball.center_x>self.center_x:
                    ball.x += overlap
                else:
                    ball.x -= overlap
            else:
                ball.velocity_y = -BOUNCE_FACTOR*ball.velocity_y
                # uncollide
                overlap = (ball.height+self.height)/2.0-abs(ball.center_y-self.center_y)
                if ball.center_y>self.center_y:
                    ball.y += overlap
                else:
                    ball.y -= overlap

class MazeraceBall(Widget):
    color = ListProperty()
    velocity_x = NumericProperty(0)
    velocity_y = NumericProperty(0)
    velocity = ReferenceListProperty(velocity_x, velocity_y)
    acceleration_x = NumericProperty(0)
    acceleration_y = NumericProperty(0)
    acceleration = ReferenceListProperty(acceleration_x, acceleration_y)
    max_velocity = NumericProperty()

    def __init__(self, color, *args, **kwargs):
        super(MazeraceBall, self).__init__(*args, **kwargs)
        self.color = color
        self.max_velocity = 0.49*min(self.width,self.height)

    def move(self):
        velocity = Vector(*self.acceleration) + self.velocity
        # avoid going through walls ;)
        abs_velocity = velocity.length()
        if abs_velocity>self.max_velocity:
            velocity *= self.max_velocity/abs_velocity
        self.velocity = velocity
        self.pos = velocity + self.pos

class MazeraceSetup(Widget):
    opener = ObjectProperty()
    orientation_portrait = BooleanProperty()
    gravity_slider = ObjectProperty()
    debug_button = ObjectProperty()

    def __init__(self, opener, *args, **kwargs):
        super(MazeraceSetup, self).__init__(*args, **kwargs)
        self.opener = opener

class MazeraceGui(Widget):
    app = None
    is_server_host = BooleanProperty(False)
    game_state = StringProperty('offline')
    natural_orientation_portrait = BooleanProperty(False)
    gravity = NumericProperty()
    setup_gui = ObjectProperty()
    setup_popup = ObjectProperty()
    maze_area = ObjectProperty()
    intro = ObjectProperty()
    logtext = StringProperty()
    control_box = ObjectProperty()
    manager_box = ObjectProperty()
    name_input = ObjectProperty()
    player0 = ObjectProperty(Player(0))
    player1 = ObjectProperty(Player(1))
    player2 = ObjectProperty(Player(2))
    player3 = ObjectProperty(Player(3))
    players = ReferenceListProperty(player0, player1, player2, player3)
    numplayers = NumericProperty(0)
    my_index = NumericProperty(-1)
    my_row = NumericProperty(-1)
    my_col = NumericProperty(-1)
    ball = ObjectProperty()
    rows = None
    cols = None
    col_width = None
    row_height = None
    maze_builder = None
    maze = None

    def __init__(self, app, *args, **kwargs):
        super(MazeraceGui, self).__init__(*args, **kwargs)
        self.app = app
        self.bind(game_state=self.on_game_state)
        self.is_server_host = self.app.net.is_server_host
        if not self.is_server_host:
            self.control_box.remove_widget(self.manager_box)

    def on_game_state(self, instance, value):
        if value=='offline':
            self.my_index = -1
        if value in ['open', 'full']:
            # No builder clean-up for 'on', in case there are
            # spectators with slow machines
            if self.maze_builder is not None:
                self.maze_builder = None

    def show_popup(self,title="Maze Race", text="Hi there",
           font_size=23, dismiss_callback=None):
        content = BoxLayout(orientation="vertical")
        content_cancel = Button(text='OK', size_hint=(1, None), height=40)
        content.add_widget(Label(text=text, markup=True,
            font_size=sp(font_size)))
        content.add_widget(content_cancel)
        popup = Popup(title=title,
            size_hint=(None, None),
            size=(sp(400), sp(300)),
            content=content)
        content_cancel.bind(on_release=popup.dismiss)
        if dismiss_callback is not None:
            popup.bind(on_dismiss=dismiss_callback)
        popup.open()

    def show_setup(self):
        if self.setup_gui is None:
            self.setup_gui = MazeraceSetup(self)
        self.setup_gui.orientation_portrait = self.natural_orientation_portrait
        self.setup_gui.gravity_slider.value = self.gravity
        self.setup_gui.debug_button.state = 'normal'
        try:
            if self.app.config.get('mazerace', 'debug').lower()=='yes':
                self.setup_gui.debug_button.state = 'down'
        except:
            pass
        if self.setup_popup is None:
            self.setup_popup = Popup(title="Maze Race Setup", content=self.setup_gui)
        self.setup_popup.open()

    def setup_done(self):
        Logger.info("debug button: {}".format(self.setup_gui.debug_button.state))
        self.setup_popup.dismiss()
        self.natural_orientation_portrait = self.setup_gui.orientation_portrait
        self.gravity = self.setup_gui.gravity_slider.value
        self.app.config.set('mazerace', 'device_natural_orientation',
            self.natural_orientation_portrait and 'portrait' or 'landscape')
        self.app.config.set('mazerace', 'gravity', str(self.gravity))
        self.app.config.set('mazerace', 'debug',
            self.setup_gui.debug_button.state=='down' and 'yes' or 'no')
        self.app.config.write()

    def on_start(self):
        if self.app.net.client_host=="localhost":
            self.intro.source = "single.rst"
            self.name_input.text = "Player"
        elif self.app.net.is_server_host:
            self.intro.source = "server.rst"
        else:
            self.intro.source = "client.rst"
        orientation = self.app.config.get('mazerace', 'device_natural_orientation')
        if not orientation.lower() in ['portrait','landscape']:
            self.show_popup(title="Check device orientation",
                text=u"""First time we run [i]Maze race[/i] on a device,
we need to know its natural orientation
(which direction it reports as "down").
Please hold it upright (with the [b]OK[/b]
button in the direction of the floor),
and press [b]OK[/b].""",
                font_size=14,
                dismiss_callback=self.check_natural_orientation_callback)
        else:
            self.natural_orientation_portrait = orientation.lower()=='portrait'
        try:
            self.gravity = self.app.config.getfloat('mazerace', 'gravity')
            self.app.config.write()
        except:
            self.gravity = 0.2
            self.app.config.set('mazerace', 'gravity', str(self.gravity))
            self.app.config.write()

    def check_natural_orientation_callback(self, instance):
        try:
            ax, ay ,az = plyer.accelerometer.acceleration
        except:
            ax, ay, az = 0, 1, 0
        if abs(ay) > abs(ax):
            self.app.config.set('mazerace', 'device_natural_orientation', 'landscape')
            self.natural_orientation_portrait = False
        else:
            self.app.config.set('mazerace', 'device_natural_orientation', 'portrait')
            self.natural_orientation_portrait = True
        self.app.config.write()

    def me(self):
        try:
            return self.players[self.my_index]
        except:
            return None

    def log(self, str):
        self.logtext = str+'\n'+self.logtext

    def clear_log(self):
        self.log = ''

    def set_player_names(self, names):
        for p, n in zip(self.players, names):
            p.name = n
        self.numplayers = len(filter(None, names))

    def join(self, name):
        if not name.strip():
            self.name_input.focus = True
            return
        self.app.osc_send("/join", {"name": name})

    def leave(self):
        self.app.osc_send("/leave")

    def start_game(self, size):
        self.maze = None
        self.maze_area.clear_widgets()
        self.app.osc_send("/start", {"size": int(size)})
    
    def build_maze(self, maze_strings):
        self.maze_builder = self._maze_builder(maze_strings)

    def ball_row_col(self):
        col = int((self.ball.x-self.maze_area.x)/self.col_width)
        row = int((self.ball.y-self.maze_area.y)/self.row_height)
        return (
            max(0,min(self.rows-1,row)),
            max(0,min(self.cols-1,col)))

    def update_ball_pos(self, force=False):
        row, col = self.ball_row_col()
        if force or row!=self.my_row or col!=self.my_col:
            self.my_row, self.my_col = row, col
            self.app.osc_send('/pos',{"row": row, "col": col})

    def set_player_pos(self, index, row, col):
        p = self.players[index]
        old_row, old_col = p.row, p.col
        p.row, p.col = row, col
        if self.game_state=='on' and self.maze is not None:
            if old_row>=0 and old_col>=0:
                self.maze[old_row][old_col].redraw()
            self.maze[row][col].redraw()

    def play(self):
        if self.maze is None:
            return  # spectator on slow machine
        for p in self.players:
            if p.row>=0 and p.col>=0:
                self.maze[p.row][p.col].redraw()
        self.game_state = 'on'

    def winner(self, winner_index, new_state):
        p = self.players[winner_index]
        self.maze[p.row][p.col].redraw(win_color=p.color)
        self.game_state = new_state
        if winner_index!=self.my_index:
            self.show_popup(text=
                player_markup("[b]{}[/b] wins.".format(p.name), winner_index))

    def update(self):
        # Partial maze draw (if needed)
        if self.maze_builder is not None:
            self.maze_builder.next()

        if not (self.my_index>=0 and self.game_state=='on'):
            return

        # The rest is physics ;)

        # Read accelerometer
        try:
            ax, ay ,az = plyer.accelerometer.acceleration
        except:
            ax, ay = 0, 3  # for debugging on desktop
        if self.natural_orientation_portrait:
            tmp = ax
            ax = -ay
            ay = tmp
        self.ball.acceleration = Vector(ax or 0, ay or 0)*-self.gravity
        self.ball.move()
        ball_row, ball_col = self.ball_row_col()
        for r in range(max(ball_row-1,0),min(ball_row+2,self.rows)):
            for c in range(max(ball_col-1,0),min(ball_col+2,self.cols)):
                for w in self.maze[r][c].children:
                    w.bounce_ball(self.ball)
        self.update_ball_pos()

    def _maze_builder(self, maze_strings):
        self.rows = len(maze_strings)
        self.cols = len(maze_strings[0])
        self.col_width = self.maze_area.width/(self.cols+0.1)
        self.row_height = self.maze_area.height/(self.rows+0.1)
        goal_cells = {}
        for p in self.players:
            p.row = p.col = -1
            if p.name:
                goal_cells['{},{}'.format(*goal_corner(p.index, self.rows, self.cols))] = p
        maze = []
        self.maze_area.clear_widgets()
        for r in range(self.rows):
            row = []
            for c in range(self.cols):
                cell = MazeraceCell(r, c, self,
                    goal_of=goal_cells.get('{},{}'.format(r,c)),
                    width=self.col_width,
                    height=self.row_height,
                    x=self.maze_area.x+(c+0.1)*self.col_width,
                    y=self.maze_area.y+(r+0.1)*self.row_height)
                if r==0:  # Bottom wall
                    cell.add_widget(MazeraceWall(
                        color=cell.goal_of and cell.goal_of.color or None,
                        width=0.8*cell.width,
                        height=0.1*cell.height,
                        x=cell.x+0.05*self.col_width,
                        y=cell.y-0.1*self.row_height))
                if c==0:  # Left wall
                    cell.add_widget(MazeraceWall(
                        vertical = True,
                        color=cell.goal_of and cell.goal_of.color or None,
                        width=0.1*cell.width,
                        height=0.8*cell.height,
                        x=cell.x-0.1*self.col_width,
                        y=cell.y+0.05*self.row_height))
                if maze_strings[r][c] in '|7':  # Right wall
                    cell.add_widget(MazeraceWall(
                        vertical = True,
                        color=c==self.cols-1 and cell.goal_of and \
                            cell.goal_of.color or None,
                        width=0.1*cell.width,
                        height=0.8*cell.height,
                        x=cell.x+0.9*cell.width,
                        y=cell.y+0.05*cell.height))
                if maze_strings[r][c] in '-7':  # Top wall
                    cell.add_widget(MazeraceWall(
                        color=r==self.rows-1 and cell.goal_of and \
                            cell.goal_of.color or None,
                        width=0.8*cell.width,
                        height=0.1*cell.height,
                        x=cell.x+0.05*cell.width,
                        y=cell.y+0.9*cell.height))
                row.append(cell)
                self.maze_area.add_widget(cell)
            maze.append(row)
            yield
        self.maze = maze
        self.maze_builder = None
        if self.my_index<0:  # Spectator mode
            yield
        self.my_row, self.my_col = home_corner(self.my_index, self.rows, self.cols)
        self.ball = MazeraceBall(self.me().color,
            size=(self.col_width*0.4, self.row_height*0.4),
            x=self.maze_area.x+(self.my_col+0.35)*self.col_width,
            y=self.maze_area.y+(self.my_row+0.35)*self.row_height)
        self.maze_area.add_widget(self.ball)
        self.update_ball_pos(force=True)
        yield


class FramedBoxLayout(BoxLayout):
    "placeholder for kv file"
    pass


class MazeraceApp(App):
    gui = None
    net = None
    last_pending_ping = None
    service = None

    def build_config(self, config):
        config.setdefaults('mazerace', {
            'device_natural_orientation': 'unknown',
            'gravity': '0.2',
            'debug': 'no'})

    def build(self):
        self.net = Gamenet()
        if self.net.is_server_host:
            self.start_server()
        self.gui = MazeraceGui(self)
        self.gui.app = self
        plyer.accelerometer.enable()
        return self.gui

    def start_server(self):
        if platform == 'android':
            from android import AndroidService
            service = AndroidService('Mazerace server', 'Server is running')
            service.start('service started')
            self.service = service
        else:
            Logger.warn('Not android: Please start service manually!')

    def stop_server(self):
        if self.service:
            self.service.stop()
            self.service = None

    def osc_send(self, osc_addr, data={}, raw=False):
        if not raw:
            data['host'] = self.net.client_host
            data = json.dumps(data)
        osc.sendMsg(osc_addr, [ data ], ipAddr=self.net.server_host, port=self.net.server_port)
        if DEBUG_OSC and osc_addr!='/ping':
            Logger.info('sendind: {}'.format([osc_addr, data]))

    def parse_message(self, m):
        try:
            d = json.loads(m[2])
            d['osc_addr'] = m[0]
            return d
        except:
            return None

    def start_client(self):
        osc.init()
        oscid = osc.listen(ipAddr=self.net.client_host, port=self.net.client_port)
        osc.bind(oscid, self.osc_handle_pong, '/pong')
        osc.bind(oscid, self.osc_handle_log, '/log')
        osc.bind(oscid, self.osc_handle_players, '/players')
        osc.bind(oscid, self.osc_handle_joined, '/joined')
        osc.bind(oscid, self.osc_handle_left, '/left')
        osc.bind(oscid, self.osc_handle_draw, '/draw')
        osc.bind(oscid, self.osc_handle_pos, '/pos')
        osc.bind(oscid, self.osc_handle_go, '/go')
        osc.bind(oscid, self.osc_handle_win, '/win')
        Clock.schedule_interval(lambda dt: osc.readQueue(oscid), 0)
        Clock.schedule_interval(lambda dt: self.gui.update(), 1/60.0)
        Clock.schedule_interval(lambda dt: self.osc_send_ping(), 0.25)

    def osc_send_ping(self):
        self.osc_send('/ping', self.net.client_host, raw=True)
        if self.last_pending_ping is None:
            self.last_pending_ping = now()
        elif seconds_since(self.last_pending_ping)>2:
            self.gui.game_state = 'offline'

    def osc_handle_pong(self, message, *args):
        self.last_pending_ping = None
        self.gui.game_state = message[2]

    def osc_handle_log(self, message, *args):
        self.gui.log(self.parse_message(message)['message'])

    def osc_handle_joined(self, message, *args):
        self.gui.my_index = self.parse_message(message)['index']

    def osc_handle_left(self, message, *args):
        self.gui.my_index = -1

    def osc_handle_draw(self, message, *args):
        self.gui.build_maze(self.parse_message(message)['maze'])

    def osc_handle_players(self, message, *args):
        self.gui.set_player_names(self.parse_message(message)['names'])

    def osc_handle_pos(self, message, *args):
        message = self.parse_message(message)
        self.gui.set_player_pos(message['index'], message['row'], message['col'])

    def osc_handle_go(self, message, *args):
        self.gui.play()

    def osc_handle_win(self, message, *args):
        message = self.parse_message(message)
        self.gui.winner(message['index'], message['state'])

    def on_start(self):
        if platform=="android":
            try:
                if self.config.get('mazerace', 'debug').lower()=='yes':
                    Config.set('kivy', 'log_enable', 1)
                    Config.set('kivy', 'log_dir', '/sdcard/mazerace-logs')
                    Config.set('kivy', 'log_level', 'info')
                else:
                    Config.set('kivy', 'log_enable', 0)
            except:
                Config.set('kivy', 'log_enable', 0)
            # Keep screen on
            # source: https://gist.github.com/cpthappy/50a00dfe091b7467e19a
            from jnius import autoclass
            from android.runnable import run_on_ui_thread
            PythonActivity = autoclass('org.renpy.android.PythonActivity')
            Params = autoclass('android.view.WindowManager$LayoutParams')
            @run_on_ui_thread
            def keep_screen_on():
                PythonActivity.mActivity.getWindow().addFlags(Params.FLAG_KEEP_SCREEN_ON)
            keep_screen_on()
        self.start_client()
        self.gui.on_start()

    def on_stop(self):
        self.stop_server()

if __name__ == '__main__':
    MazeraceApp().run()
