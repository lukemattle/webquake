"""Bounded, ordered WebSocket delivery for the single-process live backend."""

from collections import Counter, deque
from dataclasses import dataclass, field
from ipaddress import ip_address, ip_network
import queue
import socket
import threading
import time


MAX_CONNECTIONS = 10_000
MAX_CONNECTIONS_PER_IP = 100
MAX_MESSAGE_SIZE = 1024
MAX_PENDING_MESSAGES = 64
MAX_PENDING_BYTES = 1024 * 1024
MAX_TOTAL_PENDING_BYTES = 128 * 1024 * 1024
MAX_SEND_SECONDS = 10


def client_ip(remote_addr, connecting_ip, trusted_proxies):
    """Only the explicitly trusted immediate proxy may supply Cloudflare's IP."""
    peer = ip_address(remote_addr)
    if peer.version == 6 and peer.ipv4_mapped:
        peer = peer.ipv4_mapped
    if any(peer in network for network in trusted_proxies) and connecting_ip:
        try:
            peer = ip_address(connecting_ip)
        except ValueError:
            pass
    if peer.version == 6 and peer.ipv4_mapped:
        peer = peer.ipv4_mapped
    return str(peer)


def proxy_networks(value):
    return tuple(ip_network(part.strip()) for part in value.split(',') if part.strip())


@dataclass
class _Client:
    ip: str
    initial: deque = field(default_factory=deque)
    pending: deque = field(default_factory=deque)
    ready: bool = False
    scheduled: bool = False
    pending_bytes: int = 0
    pending_messages: int = 0
    sending_since: float | None = None
    admitted_at: float = field(default_factory=time.monotonic)


class WebSocketHub:
    def __init__(self):
        self._lock = threading.RLock()
        self._clients = {}
        self._ips = Counter()
        self._pending_bytes = 0
        # Bound this separately: disconnected clients can leave obsolete tasks
        # behind during connection churn, even when admission itself is capped.
        self._work = queue.Queue(maxsize=MAX_CONNECTIONS)
        self._workers = [threading.Thread(target=self._worker, daemon=True,
                                         name=f'ws-send-{i}') for i in range(20)]
        for worker in self._workers:
            worker.start()
        self._stopped = threading.Event()
        self._watchdog = threading.Thread(target=self._watch, daemon=True, name='ws-watch')
        self._watchdog.start()

    def __len__(self):
        with self._lock:
            return len(self._clients)

    def contains(self, ws):
        with self._lock:
            return ws in self._clients

    def admit(self, ws, ip):
        with self._lock:
            if (len(self._clients) >= MAX_CONNECTIONS
                    or self._ips[ip] >= MAX_CONNECTIONS_PER_IP):
                return False
            self._clients[ws] = _Client(ip)
            self._ips[ip] += 1
            return True

    def activate(self, ws):
        """Send the complete initial snapshot before any queued live updates."""
        with self._lock:
            client = self._clients.get(ws)
            if client is None:
                return
            client.ready = True
            client.initial.extend(client.pending)
            client.pending = client.initial
            client.initial = deque()
            self._schedule(ws, client)

    def send(self, ws, data, initial=False):
        size = len(data.encode('utf-8'))
        self._enqueue(ws, data, size, initial)

    def _enqueue(self, ws, data, size, initial=False):
        with self._lock:
            client = self._clients.get(ws)
            if client is None:
                return
            if (client.pending_messages >= MAX_PENDING_MESSAGES
                    or client.pending_bytes + size > MAX_PENDING_BYTES
                    or self._pending_bytes + size > MAX_TOTAL_PENDING_BYTES):
                # Diffs must never be dropped while retaining the connection.
                # Reconnection gets a fresh snapshot using the existing client.
                self.disconnect(ws)
                return
            pending_queue = client.initial if initial and not client.ready else client.pending
            pending_queue.append((data, size, time.monotonic()))
            client.pending_messages += 1
            client.pending_bytes += size
            self._pending_bytes += size
            if client.ready:
                self._schedule(ws, client)

    def broadcast(self, data):
        # Serialise concurrent broadcasts so all clients observe the same order.
        size = len(data.encode('utf-8'))
        with self._lock:
            for ws in list(self._clients):
                self._enqueue(ws, data, size)

    def _schedule(self, ws, client):
        if client.scheduled or not client.pending:
            return
        client.scheduled = True
        try:
            self._work.put_nowait((ws, client))
        except queue.Full:
            self.disconnect(ws)

    def _worker(self):
        while True:
            item = self._work.get()
            try:
                if item is None:
                    return
                self._drain(*item)
            finally:
                self._work.task_done()

    def _drain(self, ws, client):
        # Yield after a small batch so busy clients cannot monopolise the pool.
        for _ in range(8):
            with self._lock:
                if self._clients.get(ws) is not client or not client.pending:
                    break
                data, size, _queued_at = client.pending.popleft()
                client.sending_since = time.monotonic()
            try:
                ws.send(data)
            except Exception:
                self.disconnect(ws)
                return
            with self._lock:
                if self._clients.get(ws) is not client:
                    return
                client.sending_since = None
                client.pending_messages -= 1
                client.pending_bytes -= size
                self._pending_bytes -= size
        with self._lock:
            if self._clients.get(ws) is client:
                client.scheduled = False
                self._schedule(ws, client)

    @staticmethod
    def close_transport(ws):
        # ws.close() writes a close frame and can itself block on a slow reader.
        # Shutting down the transport wakes both the blocked sender and receiver.
        try:
            ws.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            ws.sock.close()
        except OSError:
            pass

    def disconnect(self, ws):
        with self._lock:
            client = self._clients.pop(ws, None)
            if client is not None:
                self._ips[client.ip] -= 1
                if not self._ips[client.ip]:
                    del self._ips[client.ip]
                self._pending_bytes -= client.pending_bytes
                client.initial.clear()
                client.pending.clear()
        self.close_transport(ws)

    def _expire_slow_clients(self):
        now = time.monotonic()
        with self._lock:
            for ws, client in list(self._clients.items()):
                queues = [q[0][2] for q in (client.initial, client.pending) if q]
                oldest = min(queues) if queues else now
                if ((not client.ready and now - client.admitted_at > MAX_SEND_SECONDS)
                        or (client.sending_since is not None
                            and now - client.sending_since > MAX_SEND_SECONDS)
                        or now - oldest > MAX_SEND_SECONDS):
                    self.disconnect(ws)

    def _watch(self):
        while not self._stopped.wait(1):
            self._expire_slow_clients()

    def shutdown(self):
        self._stopped.set()
        with self._lock:
            for ws in list(self._clients):
                self.disconnect(ws)
        for _ in self._workers:
            self._work.put(None)
        for worker in self._workers:
            worker.join()
        self._watchdog.join()
