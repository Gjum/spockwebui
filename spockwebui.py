import socket

from spockbot.plugins.core.net import Selector
from websocket_server import WebSocket


class Event:
    def __init__(self):
        self.handlers = {}

    def reg_event_handler(self, event, handler):
        self.handlers[event] = handler

    def emit(self, event, data=None):
        print('[Event]', event, data)
        if event in self.handlers:
            self.handlers[event](event, data)


class Connection(WebSocket):
    def __init__(self, sock, address, server):
        super(Connection, self).__init__(sock, address)
        self.server = server

    def handle_connected(self):
        self.server.on_connected(self)

    def handle_message(self, msg):
        self.server.on_message(self, msg)

    def handle_close(self):
        self.server.on_disconnected(self)

    def handle_error(self, error):
        self.server.on_error(self, error)


class WebSocketServerPlugin(object):
    events = {
        'select_recv': 'on_select_receive',
        'select_send': 'on_select_send',
        'select_err': 'on_select_error',
    }

    def __init__(self, event, selector):
        self.event = event
        self.selector = selector
        for event, handler in self.events.items():
            self.event.reg_event_handler(event, getattr(self, handler))

        # XXX above is done by PluginBase

        self.socks = {}

        self.listen_sock = socket.socket()
        self.listen_sock.bind(('localhost', 8000))
        # TODO multiple clients?
        # self.listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listen_sock.listen(5)
        print('listening')

        self.selector.register_socket(self.listen_sock)
        new_client_event = 'select_recv_%s' % self.listen_sock.fileno()
        self.event.reg_event_handler(new_client_event, self.on_new_client)

    def on_new_client(self, e, fileno):
        sock, addr = self.listen_sock.accept()
        print('new client from', addr)
        ws = Connection(sock, addr, self)
        self.socks[sock] = ws
        self.selector.register_socket(sock)

    def on_select_receive(self, e, sock):
        if sock in self.socks:
            ws = self.socks[sock]
            print('receiving from', nice_ws(ws))
            ws.receive_data()
            if ws.sendq:
                self.selector.schedule_sending(ws.sock)

    def on_select_send(self, e, sock):
        if sock in self.socks:
            print('sending to', nice_ws(self.socks[sock]))
            self.socks[sock].send_queued_data()

    def on_select_error(self, e, sock):
        if sock in self.socks:
            self.on_error(self.socks[sock], 'selector')

    def on_connected(self, ws):
        print('handshaked', nice_ws(ws))
        ws.send_message("Welcome to Zombo.COM! !!")
        self.selector.schedule_sending(ws.sock)

    def on_message(self, ws, msg):
        print('recvd', msg, 'from', nice_ws(ws))

        ws.send_message("Got it!")
        self.selector.schedule_sending(ws.sock)

        if 'q' in msg:
            ws.send_close()

    def on_error(self, ws, error):
        print('error in', nice_ws(ws), error)
        self.on_disconnected(ws)

    def on_disconnected(self, ws):
        print('disconnected', nice_ws(ws))
        self.selector.unregister_socket(ws.sock)
        del self.socks[ws.sock]
        ws.send_close()


def nice_ws(ws):
    return '%s %s' % (ws.sock.fileno(), ws.address)


def main():
    event = Event()
    sel = Selector(event)
    wss = WebSocketServerPlugin(event, sel)

    while 1:
        try:
            sel.poll(10)
        except:
            print('sel error!')
            print('sel socks:', sel.sockets)
            wss.listen_sock.close()
            raise


if __name__ == '__main__':
    main()
