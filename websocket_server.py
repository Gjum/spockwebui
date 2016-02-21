"""
The MIT License (MIT)
Original file Copyright (c) 2013 Dave P.
Adapted for SpockBot by Gjum 2016
https://github.com/dpallot/simple-websocket-server
"""
import sys


VER = sys.version_info[0]
if VER >= 3:
    from http.server import BaseHTTPRequestHandler
    from io import StringIO, BytesIO
else:
    # noinspection PyUnresolvedReferences
    from BaseHTTPServer import BaseHTTPRequestHandler
    # noinspection PyUnresolvedReferences
    from StringIO import StringIO

import hashlib
import base64
import socket
import struct
import errno
import codecs
from collections import deque


class WebSocketError(IOError):
    pass


# noinspection PyUnresolvedReferences
def _check_unicode(val):
    if VER >= 3:
        return isinstance(val, str)
    else:
        return isinstance(val, unicode)


# noinspection PyMissingConstructor
class HTTPRequest(BaseHTTPRequestHandler):
    def __init__(self, request_text):
        if VER >= 3:
            self.rfile = BytesIO(request_text)
        else:
            self.rfile = StringIO(request_text)
        self.raw_requestline = self.rfile.readline()
        self.error_code = self.error_message = None
        self.parse_request()


_VALID_STATUS_CODES = [1000, 1001, 1002, 1003, 1007, 1008,
                       1009, 1010, 1011, 3000, 3999, 4000, 4999]

HANDSHAKE_STR = (
    "HTTP/1.1 101 Switching Protocols\r\n"
    "Upgrade: WebSocket\r\n"
    "Connection: Upgrade\r\n"
    "Sec-WebSocket-Accept: %(acceptstr)s\r\n\r\n"
)

GUID_STR = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

STREAM = 0x0
TEXT = 0x1
BINARY = 0x2
CLOSE = 0x8
PING = 0x9
PONG = 0xA

HEADERB1 = 1
HEADERB2 = 3
LENGTHSHORT = 4
LENGTHLONG = 5
MASK = 6
PAYLOAD = 7

MAXHEADER = 65536
MAXPAYLOAD = 33554432


class WebSocket(object):
    def __init__(self, sock, address):
        self.sock = sock
        self.address = address

        self.handshaked = False
        self.can_queue = True
        self.recv_data = bytearray()
        self.sendq = deque()

        self.headerbuffer = bytearray()
        self.headertoread = 2048
        self.fin = 0
        self.opcode = 0
        self.hasmask = 0
        self.maskarray = None
        self.length = 0
        self.lengtharray = None
        self.index = 0
        self.request = None

        self.frag_start = False
        self.frag_type = BINARY
        self.frag_recv_buffer = None
        incremental_decoder = codecs.getincrementaldecoder('utf-8')
        self.frag_decoder = incremental_decoder(errors='strict')

        self.state = HEADERB1

        # restrict the size of header and payload for security reasons
        self.maxheader = MAXHEADER
        self.maxpayload = MAXPAYLOAD

    # to be overriden

    def handle_connected(self):
        """
            Called when a websocket client connects to the server.
        """
        pass

    def handle_message(self, data):
        """
            Called when websocket frame is received.

            If the frame is Text then self.recv_data is a unicode object.
            If the frame is Binary then self.recv_data is a bytearray object.
        """
        pass

    def handle_close(self):
        """
            Called when a Close frame is received.
        """
        pass

    def handle_error(self, error):
        """
            Called when an error occured.
        """

    # callable by user

    def receive_data(self):
        if self.sock._closed:
            raise WebSocketError('Receiving from closed socket')

        if self.handshaked is False:
            # do the HTTP header and handshake
            data = self.sock.recv(self.headertoread)
            if not data:
                self._kill(WebSocketError("remote socket closed"))

            else:
                # accumulate
                self.headerbuffer.extend(data)

                if len(self.headerbuffer) >= self.maxheader:
                    raise WebSocketError('header exceeded allowable size')

                # indicates end of HTTP header
                if b'\r\n\r\n' in self.headerbuffer:
                    self.request = HTTPRequest(self.headerbuffer)

                    # handshake rfc 6455
                    try:
                        key = self.request.headers['Sec-WebSocket-Key']
                        k = key.encode('ascii') + GUID_STR.encode('ascii')
                        k_s = base64.b64encode(
                            hashlib.sha1(k).digest()).decode('ascii')
                        hstr = HANDSHAKE_STR % {'acceptstr': k_s}
                        self.sendq.append((BINARY, hstr.encode('ascii')))

                        self.handshaked = True
                        self.handle_connected()
                    except Exception as e:
                        raise WebSocketError('handshake failed')

        else:
            # do normal data
            data = self.sock.recv(8192)
            if not data:
                self._kill(WebSocketError("remote socket closed"))

            try:
                if VER >= 3:
                    for d in data:
                        self._parse_message(d)
                else:
                    for d in data:
                        self._parse_message(ord(d))
            except WebSocketError as e:
                self._kill(e)

    def send_queued_data(self):
        if self.sock._closed:
            raise WebSocketError('Sending from closed socket')

        try:
            while self.sendq:
                opcode, payload = self.sendq.popleft()
                remaining = self._send_buffer(payload)
                if remaining is not None:
                    self.sendq.appendleft((opcode, remaining))
                    break
                elif opcode == CLOSE:
                    # when we close, this is our CLOSE request
                    # when client closes, this is our CLOSE response
                    self._kill(WebSocketError("connection closed"), True)
        except WebSocketError as e:
            self._kill(e)

    def send_message(self, data):
        """
            Send websocket data frame to the client.

            If data is a unicode object then the frame is sent as Text.
            If the data is a bytearray object then the frame is sent as Binary.
        """
        opcode = BINARY
        if _check_unicode(data):
            opcode = TEXT
        self._queue_message(False, opcode, data)

    def send_close(self, status=1000, reason=u''):
        """
           Send Close frame to the client. The underlying socket is only closed
           when the client acknowledges the Close frame.

           status is the closing identifier.
           reason is the reason for the close.
         """
        if not self.can_queue:
            return

        try:
            close_msg = bytearray()
            close_msg.extend(struct.pack("!H", status))
            if _check_unicode(reason):
                close_msg.extend(reason.encode('utf-8'))
            else:
                close_msg.extend(reason)
            self._queue_message(False, CLOSE, close_msg)
        finally:
            self.can_queue = False

    def send_fragment_start(self, data):
        """
            Send the start of a data fragment stream to a websocket client.
            Subsequent data should be sent using send_fragment().
            A fragment stream is completed when send_fragment_end() is called.

            If data is a unicode object then the frame is sent as Text.
            If the data is a bytearray object then the frame is sent as Binary.
        """
        opcode = BINARY
        if _check_unicode(data):
            opcode = TEXT
        self._queue_message(True, opcode, data)

    def send_fragment(self, data):
        """
            see send_fragment_start()

            If data is a unicode object then the frame is sent as Text.
            If the data is a bytearray object then the frame is sent as Binary.
        """
        self._queue_message(True, STREAM, data)

    def send_fragment_end(self, data):
        """
            see send_fragment_start()

            If data is a unicode object then the frame is sent as Text.
            If the data is a bytearray object then the frame is sent as Binary.
        """
        self._queue_message(False, STREAM, data)

    # internal

    def _queue_message(self, fin, opcode, data):
        b1 = 0
        b2 = 0
        if fin is False:
            b1 |= 0x80
        b1 |= opcode

        if _check_unicode(data):
            data = data.encode('utf-8')

        payload = bytearray()
        payload.append(b1)

        length = len(data)
        if length <= 125:
            b2 |= length
            payload.append(b2)

        elif 126 <= length <= 65535:
            b2 |= 126
            payload.append(b2)
            payload.extend(struct.pack("!H", length))

        else:
            b2 |= 127
            payload.append(b2)
            payload.extend(struct.pack("!Q", length))

        if length > 0:
            payload.extend(data)

        self.sendq.append((opcode, payload))

    def _send_buffer(self, buff):
        # this is already checked in send_queued_data()
        if self.sock._closed:
            raise WebSocketError('Sending from closed socket')

        size = len(buff)
        tosend = size
        already_sent = 0

        while tosend > 0:
            try:
                # i should be able to send a bytearray
                sent = self.sock.send(buff[already_sent:])
                if sent == 0:
                    raise WebSocketError("socket connection broken")

                already_sent += sent
                tosend -= sent

            except socket.error as e:
                # if we have full buffers then wait for them to drain
                # and try again
                if e.errno in [errno.EAGAIN, errno.EWOULDBLOCK]:
                    return buff[already_sent:]
                else:
                    raise

        return None

    def _parse_message(self, byte):
        # read in the header
        if self.state == HEADERB1:

            self.fin = byte & 0x80
            self.opcode = byte & 0x0F
            self.state = HEADERB2

            self.index = 0
            self.length = 0
            self.lengtharray = bytearray()
            self.recv_data = bytearray()

            rsv = byte & 0x70
            if rsv != 0:
                raise WebSocketError('RSV bit must be 0')

        elif self.state == HEADERB2:
            mask = byte & 0x80
            length = byte & 0x7F

            if self.opcode == PING and length > 125:
                raise WebSocketError('ping packet is too large')

            if mask == 128:
                self.hasmask = True
            else:
                self.hasmask = False

            if length <= 125:
                self.length = length

                # if we have a mask we must read it
                if self.hasmask is True:
                    self.maskarray = bytearray()
                    self.state = MASK
                else:
                    # if there is no mask and no payload we are done
                    if self.length <= 0:
                        try:
                            self._handle_packet()
                        finally:
                            self.state = HEADERB1
                            self.recv_data = bytearray()

                    # we have no mask and some payload
                    else:
                        # self.index = 0
                        self.recv_data = bytearray()
                        self.state = PAYLOAD

            elif length == 126:
                self.lengtharray = bytearray()
                self.state = LENGTHSHORT

            elif length == 127:
                self.lengtharray = bytearray()
                self.state = LENGTHLONG

        elif self.state == LENGTHSHORT:
            self.lengtharray.append(byte)

            if len(self.lengtharray) > 2:
                raise WebSocketError('short length exceeded allowable size')

            if len(self.lengtharray) == 2:
                self.length = struct.unpack_from('!H', self.lengtharray)[0]

                if self.hasmask is True:
                    self.maskarray = bytearray()
                    self.state = MASK
                else:
                    # if there is no mask and no payload we are done
                    if self.length <= 0:
                        try:
                            self._handle_packet()
                        finally:
                            self.state = HEADERB1
                            self.recv_data = bytearray()

                    # we have no mask and some payload
                    else:
                        # self.index = 0
                        self.recv_data = bytearray()
                        self.state = PAYLOAD

        elif self.state == LENGTHLONG:

            self.lengtharray.append(byte)

            if len(self.lengtharray) > 8:
                raise WebSocketError('long length exceeded allowable size')

            if len(self.lengtharray) == 8:
                self.length = struct.unpack_from('!Q', self.lengtharray)[0]

                if self.hasmask is True:
                    self.maskarray = bytearray()
                    self.state = MASK
                else:
                    # if there is no mask and no payload we are done
                    if self.length <= 0:
                        try:
                            self._handle_packet()
                        finally:
                            self.state = HEADERB1
                            self.recv_data = bytearray()

                    # we have no mask and some payload
                    else:
                        # self.index = 0
                        self.recv_data = bytearray()
                        self.state = PAYLOAD

        # MASK STATE
        elif self.state == MASK:
            self.maskarray.append(byte)

            if len(self.maskarray) > 4:
                raise WebSocketError('mask exceeded allowable size')

            if len(self.maskarray) == 4:
                # if there is no mask and no payload we are done
                if self.length <= 0:
                    try:
                        self._handle_packet()
                    finally:
                        self.state = HEADERB1
                        self.recv_data = bytearray()

                # we have no mask and some payload
                else:
                    # self.index = 0
                    self.recv_data = bytearray()
                    self.state = PAYLOAD

        # PAYLOAD STATE
        elif self.state == PAYLOAD:
            if self.hasmask is True:
                self.recv_data.append(byte ^ self.maskarray[self.index % 4])
            else:
                self.recv_data.append(byte)

            # if length exceeds allowable size
            # then we except and remove the connection
            if len(self.recv_data) >= self.maxpayload:
                raise WebSocketError('payload exceeded allowable size')

            # check if we have processed length bytes; if so we are done
            if (self.index + 1) == self.length:
                try:
                    self._handle_packet()
                finally:
                    # self.index = 0
                    self.state = HEADERB1
                    # TODO why is the buffer deleted?
                    # self.recv_data = bytearray()
            else:
                self.index += 1

    def _handle_packet(self):
        length = len(self.recv_data)

        if self.opcode in (CLOSE, STREAM, TEXT, BINARY):
            pass
        elif self.opcode == PONG or self.opcode == PING:
            if length > 125:
                raise WebSocketError('control frame length can not be > 125')
        else:
            # unknown or reserved opcode so just close
            raise WebSocketError('unknown opcode')

        if self.opcode == CLOSE:
            status = 1000
            reason = u''

            if length == 0:
                pass
            elif length >= 2:
                status = struct.unpack_from('!H', self.recv_data[:2])[0]
                reason = self.recv_data[2:]

                if status not in _VALID_STATUS_CODES:
                    status = 1002

                if len(reason) > 0:
                    try:
                        reason = reason.decode('utf8', errors='strict')
                    except:
                        status = 1002
            else:
                status = 1002

            self.send_close(status, reason)
            return

        elif self.fin == 0:
            if self.opcode != STREAM:
                if self.opcode == PING or self.opcode == PONG:
                    raise WebSocketError('fragmented control message')

                self.frag_type = self.opcode
                self.frag_start = True
                self.frag_decoder.reset()

                if self.frag_type == TEXT:
                    self.frag_recv_buffer = []
                    utf_str = self.frag_decoder.decode(
                        self.recv_data, final=False)
                    if utf_str:
                        self.frag_recv_buffer.append(utf_str)
                else:
                    self.frag_recv_buffer = bytearray()
                    self.frag_recv_buffer.extend(self.recv_data)

            else:
                if self.frag_start is False:
                    raise WebSocketError('fragmentation protocol error')

                if self.frag_type == TEXT:
                    utf_str = self.frag_decoder.decode(
                        self.recv_data, final=False)
                    if utf_str:
                        self.frag_recv_buffer.append(utf_str)
                else:
                    self.frag_recv_buffer.extend(self.recv_data)

        else:
            if self.opcode == STREAM:
                if self.frag_start is False:
                    raise WebSocketError('fragmentation protocol error')

                if self.frag_type == TEXT:
                    utf_str = self.frag_decoder.decode(
                        self.recv_data, final=True)
                    self.frag_recv_buffer.append(utf_str)
                    self.recv_data = u''.join(self.frag_recv_buffer)
                else:
                    self.frag_recv_buffer.extend(self.recv_data)
                    self.recv_data = self.frag_recv_buffer

                self.handle_message(self.recv_data)

                self.frag_decoder.reset()
                self.frag_type = BINARY
                self.frag_start = False
                self.frag_recv_buffer = None

            elif self.opcode == PING:
                self._queue_message(False, PONG, self.recv_data)

            elif self.opcode == PONG:
                pass

            else:
                if self.frag_start is True:
                    raise WebSocketError('fragmentation protocol error')

                if self.opcode == TEXT:
                    try:
                        self.recv_data = self.recv_data.decode(
                            'utf8', errors='strict')
                    except Exception as e:
                        raise WebSocketError('invalid utf-8 payload')

                self.handle_message(self.recv_data)

    def _kill(self, error, is_close=False):
        """Close the socket and handle the error"""
        self.sock.close()
        self.can_queue = False
        self.handshaked = False

        if is_close:
            self.handle_close()
        else:
            self.handle_error(error)
