from kivy.lib import osc
from kivy.logger import Logger
from kivy.config import Config, ConfigParser
from kivy import platform

from game import Gamenet, now, seconds_since, sec2min, \
    goal_corner, player_markup, DEBUG_OSC
from time import sleep
from random import randint
import json, threading

class Client:
    "A machine that may or may not be a player in current game"
    host = ''
    last_ping = None
    player = None

    def __init__(self, host):
        self.host = host

class Player:
    "A player in current game. There may be up to 4 players"
    host = ''
    name = ''
    index = None
    row = None
    col = None
    goal_row = None
    goal_col = None

    def __init__(self, host, name, index):
        self.host = host
        self.name = name
        self.index = index

class Server:
    net = None
    # possible states:
    # open (users can join)
    # full (there are already 4 players)
    # starting (maze is being generated)
    # drawing (player devices are drawing the maze)
    # on (we're playing)
    state = ''
    clients = {}
    players = []
    name_to_player = {}
    oscid = None
    maze = None
    maze_generator = None
    rows = None
    cols = None
    game_start_time = None

    def __init__(self):
        self.net = Gamenet()
        self.players = [None for i in range(4)]

    def osc_send(self, osc_addr, data, host=None, raw=False):
        if not raw:
            data = json.dumps(data)
        if host is not None:
            if DEBUG_OSC and not osc_addr in ['/pong', '/draw']:
                Logger.info('sendind: {}'.format([osc_addr, data, host]))
            if self.net.valid_host(host):
                osc.sendMsg(osc_addr, [ data ], ipAddr=host, port=self.net.client_port)
        else:  # Broadcast
            for host in self.clients.keys():
                self.osc_send(osc_addr, data, host, raw=True)

    def broadcast_players(self):
        self.osc_send('/players', {"names": [p and p.name or '' for p in self.players]})

    def add_player(self, host, name):
        try:
            index = self.players.index(None)
        except:
            return None
        self.clients[host].player = p = Player(host, name, index)
        self.name_to_player[name] = self.players[index] = p
        if self.net.server_host=='localhost' or not None in self.players:
            self.state = 'full'
        self.osc_send('/log',{"message": player_markup("{} joins".format(name), index)})
        self.broadcast_players()
        return index

    def drop_player(self, p):
        self.players[p.index] = None
        if self.name_to_player.has_key(p.name):
            del self.name_to_player[p.name]
        if self.clients.has_key(p.host):
            self.clients[p.host].player = None
        self.osc_send('/log',{"message": player_markup("{} leaves".format(p.name), p.index)})
        if not (len(filter(None, self.players))):  # Everyone's left :(
            self.abandon_game()
        if self.state=='full':
            self.state='open'
        self.broadcast_players()

    def drop_stale_clients(self):
        for k,c in self.clients.items():
            if seconds_since(c.last_ping)>4:
                del self.clients[k]
                if c.player is not None:
                    self.drop_player(c.player)

    def abandon_game(self):
        if not self.state in ['open', 'full']:
            self.osc_send('/log',{"message": "Game is abandoned :("})
            self.maze_generator = None
            self.maze = None
            self.state = 'open'

    def _maze_generator(self):
        def _join_maze_groups(maze,g1,g2):
            for row in maze:
                for cell in row:
                    if cell['group']==g1:
                        cell['group']=g2
        maze = [
            [{'group': self.cols*row+col, 'walls': 'tr', 'widgets': []}
                for col in range(self.cols)]
            for row in range(self.rows)]
        counter = 0
        for i in range(self.rows*self.cols-1):
            while True:
                if randint(0,1):
                    # try to remove a right wall
                    r = randint(0,self.rows-1)
                    c = randint(0,self.cols-2)
                    if maze[r][c]['group']!=maze[r][c+1]['group']:
                        maze[r][c]['walls'] = maze[r][c]['walls'].translate(None,'r')
                        _join_maze_groups(maze,maze[r][c]['group'],maze[r][c+1]['group'])
                        break
                else:
                    # try to remove a top wall
                    r = randint(0,self.rows-2)
                    c = randint(0,self.cols-1)
                    if maze[r][c]['group']!=maze[r+1][c]['group']:
                        maze[r][c]['walls'] = maze[r][c]['walls'].translate(None,'t')
                        _join_maze_groups(maze,maze[r][c]['group'],maze[r+1][c]['group'])
                        break
            counter = (counter+1)%100
            if not counter:
                yield False
        self.maze = [''.join(
                [{'': ' ', 'r': '|', 't': '-', 'tr': '7'}[c['walls']] for c in r])
            for r in maze]
        yield True

    def start_game(self, rows, cols):
        self.maze = None
        self.rows = rows
        self.cols = cols
        for p in self.players:
            if p is not None:
                p.row = p.col = None
                p.goal_row, p.goal_col = goal_corner(p.index, rows, cols)
        self.maze = None
        self.maze_generator = self._maze_generator()

    def check_for_new_maze(self):
        if self.maze_generator is not None and self.maze_generator.next():
            self.maze_generator = None
            return True
        return False

    def loop(self):
        osc.readQueue(self.oscid)
        self.drop_stale_clients()
        if self.check_for_new_maze():
            self.state = 'drawing'
            self.osc_send('/draw',{"maze": self.maze})

    def parse_message(self, m):
        try:
            d = json.loads(m[2])
            if not (self.clients.has_key(d['host'])):
                return None
            d['osc_addr'] = m[0]
            return d
        except:
            Logger.info('bad message: {}'.format(m))
            return None

    def osc_handle_ping(self, message, *args):
        try:
            client_addr = message[2]
        except:
            return
        if not self.net.valid_host(client_addr):
            return
        if not self.clients.has_key(client_addr):
            self.clients[client_addr] = Client(client_addr)
            self.broadcast_players()  # for the sake of the newcomer
        self.clients[client_addr].last_ping = now()
        self.osc_send('/pong', self.state, client_addr, raw=True)

    def osc_handle_join(self, message, *args):
        message = self.parse_message(message)
        if message is None: return
        if self.clients[message['host']].player is not None:
            self.osc_send('/log', {"message": "Can't join more than once"}, message['host'])
            return
        if not self.state=='open':
            self.osc_send('/log', {"message": "Can't join when game is {}".format(self.state)}, message['host'])
            return
        name = message.get('name')
        try:
            name = name.strip().capitalize()
        except:
            return
        if not name or len(name)>16 or self.name_to_player.has_key(name):
            self.osc_send('/log', {"message": "Name '{}' is taken or invalid".format(name)}, message['host'])
            return
        index = self.add_player(message['host'], name)
        if index is not None:
            self.osc_send('/joined', {"index": index}, message['host'])

    def osc_handle_leave(self, message, *args):
        message = self.parse_message(message)
        if message is None: return
        self.drop_player(self.clients[message['host']].player)
        self.osc_send('/left', {}, message['host'])

    def osc_handle_start(self, message, *args):
        message = self.parse_message(message)
        if message is None: return
        if message['host']!=self.net.server_host: return
        if not self.state in ['open','full']:
            self.osc_send('/log', {"message": "Can't start a game when game is {}".format(self.state)}, message['host'])
            return
        if not len(filter(None, self.players)):
            self.osc_send('/log', {"message": "Can't start a game when nobody's playing"}, message['host'])
            return
        self.state = 'starting'
        self.start_game(message['size'], message['size'])
        self.osc_send('/log',{"message": "Starting a {x}X{x} game".format(x=message['size'])})

    def osc_handle_pos(self, message, *args):
        message = self.parse_message(message)
        if message is None: return
        try:
            player = self.clients[message['host']].player
            player.row, player.col = message['row'], message['col']
        except:
            return
        self.osc_send('/pos', {"index": player.index, "row": player.row, "col": player.col})
        if self.state=='drawing':  # Check whether all players are ready
            if not filter(lambda p: p is not None and p.row is None, self.players):
                self.state = 'on'
                self.game_start_time = now()
                self.osc_send('/go', {})
                #self.osc_send('/log', {"message":"... Go!"})
        elif self.state=='on':  # Check for win
            if player.row==player.goal_row and player.col==player.goal_col:
                if self.net.server_host=='localhost' or not None in self.players:
                    self.state = 'full'
                else:
                    self.state = 'open'
                self.osc_send('/win',{"index": player.index, "state": self.state})
                self.osc_send('/log',{
                    "message": player_markup("[b]{}[/b] finishes in [b]{}[/b]".format(player.name,
                        sec2min(seconds_since(self.game_start_time))), player.index)})
        
    def serve(self):
        self.state = 'open'
        osc.init()
        self.oscid = osc.listen(ipAddr=self.net.server_host, port=self.net.server_port)
        osc.bind(self.oscid, self.osc_handle_ping, '/ping')
        osc.bind(self.oscid, self.osc_handle_join, '/join')
        osc.bind(self.oscid, self.osc_handle_leave, '/leave')
        osc.bind(self.oscid, self.osc_handle_start, '/start')
        osc.bind(self.oscid, self.osc_handle_pos, '/pos')
        while True:
            self.loop()
            sleep(.05)

if __name__=='__main__':
    if platform=='android':
        try:
            config = ConfigParser()
            config.read('/sdcard/.mazerace.ini')
            if config.get('mazerace', 'debug').lower()=='yes':
                Config.set('kivy', 'log_enable', 1)
                Config.set('kivy', 'log_dir', '/sdcard/mazerace-logs/server')
                Config.set('kivy', 'log_level', 'info')
            else:
                Config.set('kivy', 'log_enable', 0)
        except:
            Config.set('kivy', 'log_enable', 0)
    Server().serve()
