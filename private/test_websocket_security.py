"""Isolated WebSocket checks: no upstream credentials, polling or notifications.

Run: python -m unittest test_websocket_security -v
"""

import ast
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch

from flask import Flask, request
from simple_websocket import Server
from wsproto import WSConnection, ConnectionType
from wsproto.events import Request, TextMessage, CloseConnection

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'private'))
import websocket_hub as hub_module


class Transport:
    def __init__(self, ws):
        self.ws = ws

    def shutdown(self, _how):
        self.ws.connected = False
        self.ws.release.set()

    def close(self):
        self.shutdown(None)


class FakeSocket:
    def __init__(self, blocked=False, broken=False):
        self.connected = True
        self.messages = []
        self.release = threading.Event()
        self.entered = threading.Event()
        self.sock = Transport(self)
        self.blocked = blocked
        self.broken = broken

    def send(self, data):
        self.entered.set()
        if self.blocked:
            if not self.release.wait(5):
                raise TimeoutError('test sender was not released')
        if self.broken or not self.connected:
            raise OSError('disconnected')
        self.messages.append(data)


def load_functions(*names, namespace):
    """Load real route/snapshot functions without main.py's startup effects."""
    tree = ast.parse((ROOT / 'private/main.py').read_text())
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            node.decorator_list = []
            nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'main.py', 'exec'), namespace)


class HubFixture(unittest.TestCase):
    def setUp(self):
        self.hub = hub_module.WebSocketHub()
        self.addCleanup(self.hub.shutdown)

    def admitted(self, **kwargs):
        ws = FakeSocket(**kwargs)
        self.assertTrue(self.hub.admit(ws, '192.0.2.1'))
        return ws

    def wait_for_sends(self):
        self.hub._work.join()


class HubTests(HubFixture):
    def test_actual_global_and_per_ip_boundaries(self):
        for i in range(10_000):
            self.assertTrue(self.hub.admit(FakeSocket(), f'client-{i // 100}'))
        self.assertEqual(len(self.hub), 10_000)
        self.assertFalse(self.hub.admit(FakeSocket(), 'another-client'))
        ws = next(iter(self.hub._clients))
        self.hub.disconnect(ws)
        self.assertTrue(self.hub.admit(FakeSocket(), 'another-client'))
        self.assertFalse(self.hub.admit(FakeSocket(), 'client-1'))

    def test_concurrent_admission_and_idempotent_cleanup(self):
        sockets = [FakeSocket() for _ in range(150)]
        with ThreadPoolExecutor(max_workers=20) as executor:
            admitted = list(executor.map(lambda ws: self.hub.admit(ws, 'shared-ip'), sockets))
        self.assertEqual(sum(admitted), 100)
        for ws in sockets:
            self.hub.disconnect(ws)
            self.hub.disconnect(ws)
        self.assertEqual(len(self.hub), 0)
        self.assertEqual(dict(self.hub._ips), {})

    def test_initial_snapshots_precede_live_diffs_and_preserve_order(self):
        ws = self.admitted()
        self.hub.broadcast('diff-1')
        self.hub.send(ws, 'snapshot-1', initial=True)
        self.hub.broadcast('diff-2')
        self.hub.send(ws, 'snapshot-2', initial=True)
        self.assertEqual(ws.messages, [])
        self.hub.activate(ws)
        for i in range(20):
            self.hub.broadcast(f'live-{i}')
        self.wait_for_sends()
        self.assertEqual(ws.messages, ['snapshot-1', 'snapshot-2', 'diff-1', 'diff-2']
                         + [f'live-{i}' for i in range(20)])
        self.assertEqual(self.hub._pending_bytes, 0)

    def test_queue_overflow_disconnects_without_continuing_with_missing_diffs(self):
        ws = self.admitted()
        for _ in range(hub_module.MAX_PENDING_MESSAGES):
            self.hub.broadcast('diff')
        self.assertTrue(self.hub.contains(ws))
        self.hub.broadcast('overflow')
        self.assertFalse(self.hub.contains(ws))
        self.assertFalse(ws.connected)
        self.assertEqual(self.hub._pending_bytes, 0)
        replacement = self.admitted()
        self.hub.send(replacement, 'fresh-snapshot', initial=True)
        self.hub.activate(replacement)
        self.wait_for_sends()
        self.assertEqual(replacement.messages, ['fresh-snapshot'])

    def test_per_client_and_global_byte_budgets(self):
        ws = self.admitted()
        self.hub.send(ws, 'x' * hub_module.MAX_PENDING_BYTES, initial=True)
        self.hub.send(ws, 'x', initial=True)
        self.assertFalse(self.hub.contains(ws))
        first, second = self.admitted(), self.admitted()
        with patch.object(hub_module, 'MAX_TOTAL_PENDING_BYTES', 10):
            self.hub.send(first, '12345678', initial=True)
            self.hub.send(second, '123', initial=True)
        self.assertTrue(self.hub.contains(first))
        self.assertFalse(self.hub.contains(second))
        self.assertEqual(self.hub._pending_bytes, 8)

    def test_stalled_write_is_interrupted_and_does_not_block_other_clients(self):
        slow = self.admitted(blocked=True)
        fast = self.admitted()
        self.hub.activate(slow)
        self.hub.activate(fast)
        self.hub.broadcast('update')
        self.assertTrue(slow.entered.wait(1))
        real_now = time.monotonic()
        # Expire only the active stalled write; the other client may still have
        # queued work, whose age is not relevant to this focused check.
        with self.hub._lock:
            self.hub._clients[slow].sending_since = real_now - 11
        self.hub._expire_slow_clients()
        self.wait_for_sends()
        self.assertFalse(self.hub.contains(slow))
        self.assertEqual(fast.messages, ['update'])
        self.assertEqual(self.hub._pending_bytes, 0)

    def test_queued_and_initialising_clients_expire(self):
        ws = self.admitted()
        with self.hub._lock:
            self.hub._clients[ws].admitted_at -= 11
        self.hub._expire_slow_clients()
        self.assertFalse(self.hub.contains(ws))
        ws = self.admitted()
        self.hub.send(ws, 'queued')
        with self.hub._lock:
            data, size, stamp = self.hub._clients[ws].pending[0]
            self.hub._clients[ws].pending[0] = (data, size, stamp - 11)
        self.hub._expire_slow_clients()
        self.assertFalse(self.hub.contains(ws))

    def test_write_failure_cleans_up(self):
        ws = self.admitted(broken=True)
        self.hub.activate(ws)
        self.hub.broadcast('update')
        self.wait_for_sends()
        self.assertEqual(len(self.hub), 0)
        self.assertEqual(self.hub._pending_bytes, 0)

    def test_worker_backlog_is_bounded_even_during_connection_churn(self):
        blocked = [self.admitted(blocked=True) for _ in range(20)]
        for ws in blocked:
            self.hub.activate(ws)
            self.hub.send(ws, 'blocked')
        for ws in blocked:
            self.assertTrue(ws.entered.wait(1))
        original_maxsize = self.hub._work.maxsize
        self.hub._work.maxsize = 1
        try:
            waiting, rejected = self.admitted(), self.admitted()
            self.hub.activate(waiting)
            self.hub.send(waiting, 'queued')
            self.hub.activate(rejected)
            self.hub.send(rejected, 'queue-full')
            self.assertTrue(self.hub.contains(waiting))
            self.assertFalse(self.hub.contains(rejected))
        finally:
            self.hub._work.maxsize = original_maxsize
        for ws in blocked:
            self.hub.disconnect(ws)
        self.wait_for_sends()
        self.assertEqual(waiting.messages, ['queued'])

    def test_client_ip_uses_only_trusted_tunnel_header(self):
        proxies = hub_module.proxy_networks('127.0.0.1/32,::1/128')
        self.assertEqual(hub_module.client_ip('127.0.0.1', '198.51.100.1', proxies), '198.51.100.1')
        self.assertEqual(hub_module.client_ip('::1', '2001:db8::1', proxies), '2001:db8::1')
        self.assertEqual(hub_module.client_ip('192.0.2.1', '198.51.100.1', proxies), '192.0.2.1')
        self.assertEqual(hub_module.client_ip('127.0.0.1', 'bad,header', proxies), '127.0.0.1')
        self.assertEqual(hub_module.client_ip('::ffff:192.0.2.1', None, proxies), '192.0.2.1')


class ProtocolTests(HubFixture):
    def setUp(self):
        super().setUp()
        self.app = Flask('websocket-security-test')
        self.namespace = dict(WebsocketBase=object, request=request, time=time,
                              websocket_hub=hub_module, open_sockets=self.hub,
                              _ws_trusted_proxies=hub_module.proxy_networks('127.0.0.1/32'),
                              send_recent_data=lambda ws: self.hub.send(ws, 'snapshot', initial=True))
        load_functions('websocket_recv_open', namespace=self.namespace)

    def make_protocol_pair(self):
        server_sock, client_sock = socket.socketpair()
        self.addCleanup(client_sock.close)
        self.addCleanup(server_sock.close)
        client_sock.settimeout(2)
        client = WSConnection(ConnectionType.CLIENT)
        handshake = client.send(Request(host='localhost', target='/ws'))
        headers = {}
        for line in handshake.decode().split('\r\n')[1:]:
            if ': ' in line:
                key, value = line.split(': ', 1)
                headers['HTTP_' + key.upper().replace('-', '_')] = value
        ws = Server({'werkzeug.socket': server_sock, **headers},
                    max_message_size=hub_module.MAX_MESSAGE_SIZE)
        self.addCleanup(ws.thread.join, 2)
        client.receive_data(client_sock.recv(4096))
        list(client.events())
        return ws, client, client_sock

    def start_route(self, ws):
        errors = []
        def run():
            with self.app.test_request_context('/ws', headers={'CF-Connecting-IP': '198.51.100.1'},
                                               environ_base={'REMOTE_ADDR': '127.0.0.1'}):
                try:
                    self.namespace['websocket_recv_open'](ws)
                except Exception as exc:
                    # Flask-Sock normally handles ConnectionClosed itself.
                    from simple_websocket import ConnectionClosed
                    if not isinstance(exc, ConnectionClosed):
                        errors.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        self.addCleanup(thread.join, 2)
        return thread, errors

    def read_messages(self, client, sock, count):
        messages = []
        while len(messages) < count:
            data = sock.recv(65536)
            if not data:
                break
            client.receive_data(data)
            messages.extend(event.data for event in client.events() if isinstance(event, TextMessage))
        return messages

    def test_real_heartbeat_broadcast_and_disconnect(self):
        ws, client, sock = self.make_protocol_pair()
        thread, errors = self.start_route(ws)
        self.assertEqual(self.read_messages(client, sock, 1), ['snapshot'])
        sock.sendall(client.send(TextMessage(data='!ping')))
        self.assertEqual(self.read_messages(client, sock, 1), ['!pong'])
        self.hub.broadcast('{"type":"earthquake"}')
        self.assertEqual(self.read_messages(client, sock, 1), ['{"type":"earthquake"}'])
        sock.sendall(client.send(CloseConnection(code=1000)))
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(self.hub), 0)

    def test_real_oversize_message_is_closed_by_websocket_library(self):
        ws, client, sock = self.make_protocol_pair()
        thread, errors = self.start_route(ws)
        self.read_messages(client, sock, 1)
        sock.sendall(client.send(TextMessage(data='x' * 1025)))
        client.receive_data(sock.recv(4096))
        self.assertTrue(any(isinstance(event, CloseConnection) and event.code == 1009
                            for event in client.events()))
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(self.hub), 0)

    def test_initial_snapshot_exception_still_releases_connection(self):
        def fail(_ws):
            raise RuntimeError('snapshot failed')
        self.namespace['send_recent_data'] = fail
        ws = FakeSocket()
        with self.app.test_request_context('/ws', environ_base={'REMOTE_ADDR': '127.0.0.1'}):
            with self.assertRaises(RuntimeError):
                self.namespace['websocket_recv_open'](ws)
        self.assertEqual(len(self.hub), 0)
        self.assertFalse(ws.connected)

    def test_invalid_messages_and_ping_flood_release_connections(self):
        for messages in (['unexpected'], [b'!ping'], ['!ping'] * 11, [None]):
            with self.subTest(messages=messages):
                ws = FakeSocket()
                incoming = iter(messages)
                ws.receive = lambda timeout: next(incoming)
                with self.app.test_request_context('/ws', environ_base={'REMOTE_ADDR': '127.0.0.1'}):
                    self.namespace['websocket_recv_open'](ws)
                self.assertEqual(len(self.hub), 0)
                self.assertFalse(ws.connected)

    def test_admission_rejection_does_not_build_a_snapshot(self):
        for _ in range(100):
            self.assertTrue(self.hub.admit(FakeSocket(), '198.51.100.1'))
        ws = FakeSocket()
        def unexpected_snapshot(_ws):
            self.fail('rejected connections must not build snapshots')
        self.namespace['send_recent_data'] = unexpected_snapshot
        with self.app.test_request_context('/ws', headers={'CF-Connecting-IP': '198.51.100.1'},
                                          environ_base={'REMOTE_ADDR': '127.0.0.1'}):
            self.namespace['websocket_recv_open'](ws)
        self.assertEqual(len(self.hub), 100)
        self.assertFalse(ws.connected)

    def test_real_snapshot_contains_all_japan_taiwan_and_history_messages(self):
        ws = self.admitted()
        namespace = dict(WebsocketBase=object, json=json, open_sockets=self.hub,
                         _last_nied_full=[{'code': 'jp'}], _last_snet_full=[{'code': 'sea'}],
                         _last_exptech_full=[{'code': 'tw'}],
                         recent_earthquake_data={'jp': {'type': 'earthquake'}},
                         recent_tw_eew_data={'tw': {'type': 'tw_eew'}},
                         recent_tsunami_data={'type': 'tsunami'},
                         recent_offshore_obs_data={'type': 'tsunami_obs'},
                         recent_past_quake_data={'type': 'earthquake'},
                         recent_tw_quake_data={'type': 'tw_earthquake'},
                         recent_jma_history=[], recent_tw_history=[{'earthquake_no': 1}],
                         _build_quake_points_index=lambda: [{'ts': 1}])
        load_functions('send_data', 'send_recent_data', namespace=namespace)
        self.hub.broadcast('{"type":"nied_stations_diff","stations":[]}')
        namespace['send_recent_data'](ws)
        self.hub.activate(ws)
        self.wait_for_sends()
        self.assertEqual([json.loads(message)['type'] for message in ws.messages], [
            'nied_stations', 'snet_stations', 'exptech_stations', 'earthquake',
            'tw_eew', 'tsunami', 'tsunami_obs', 'earthquake', 'tw_earthquake',
            'jma_history', 'tw_history', 'quake_points_index', 'nied_stations_diff',
        ])
