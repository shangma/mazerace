import socket, datetime
### Player-related helpers

DEBUG_OSC = True
BOUNCE_FACTOR = 0.4

_PLAYER_CORNER_HINTS = [ [1, 0], [0, 1], [1, 1], [0, 0] ]
_PLAYER_COLORS = [ [0, 1, 0, 1], [1, 0.25, 0.25, 1], [0.5, 0.5, 1, 1], [1, 1, 0, 1] ]
_PLAYER_MARKUP_COLORS = [ "#00FF00", "#FF3F3F", "7F7FFF", "#FFFF00" ]

def player_color(index):
    return _PLAYER_COLORS[index]

def player_markup(text, index):
    return "[color={}]{}[/color]".format(_PLAYER_MARKUP_COLORS[index], text)

def player_corner_hint(index):
    return [1-x for x in _PLAYER_CORNER_HINTS[index]]

def home_corner(index, rows, cols):
    return [pair[0]*pair[1] for pair in zip(_PLAYER_CORNER_HINTS[index], [rows-1, cols-1])]

def goal_corner(index, rows, cols):
    return [(1-pair[0])*pair[1] for pair in zip(_PLAYER_CORNER_HINTS[index], [rows-1, cols-1])]

### Time helpers
def now():
    "Returns current time. Can be used as arg for seconds_since()"
    return datetime.datetime.now()

def seconds_since(t):
    "t is something now() has returned a while ago"
    return (now()-t).total_seconds()

def sec2min(sec):
    sec=int(sec)
    return "{:d}:{:02d}".format(sec//60,sec%60)

import socket
_HOTSPOT_SERVER_IP = '192.168.43.1' # android hotspot access point
_HOTSPOT_PREFIX = '192.168.43.'

class Gamenet:
    """Helper class to figure out:
       client/server ip/port
       whether this host's client should run the server"""
    is_server_host = True # client should spawn a server as well
    server_host = 'localhost'
    server_port = 6293 # "maze" on phone keypad
    client_host = 'localhost'
    client_port = 6294

    def __init__(self):
        # are we on a hotspot?
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Note: SOCK_DGRAM doesn't "connect",
            # only finds which interface would route there ;)
            s.connect_ex((_HOTSPOT_SERVER_IP,0)) # port doesn't matter
            me = s.getsockname()[0]
            assert me!='localhost', "Weirdly configured network ;)"
            if self.valid_host(me): # i.e. it's a hotspot
                self.client_host = me
                self.server_host = _HOTSPOT_SERVER_IP
        except: # probably never happens
            pass
        self.is_server_host = self.client_host==self.server_host

    def __repr__(self):
        return '<Gamenet{} client={}:{}, server={}:{}>'.format(
            self.is_server_host and ' (server host)' or '',
            self.client_host, self.client_port,
            self.server_host, self.server_port)

    def valid_host(self,a):
        # Server uses this to avoid sending to bogus host
        # (osc chokes badly on [e.g.] ipAddr="freeze_the_process")
        return a.startswith(_HOTSPOT_PREFIX) or a=='localhost'
