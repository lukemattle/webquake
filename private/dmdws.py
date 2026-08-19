import websocket
import httpx
import json
import threading
import traceback
import time
import logging
import datetime
import sys
import base64
import gzip
import hashlib
import secrets
import socket
import ssl
import webbrowser
import urllib.parse
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, NoReturn, Callable, Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# DM-DSS OAuth application credentials, read from the environment with no
# fallback. An unset variable stays empty rather than becoming a placeholder
# that DMDATA would reject with a confusing 401; main.py reports it at startup.
client_id = os.environ.get("WEBQUAKE_DMDATA_CLIENT_ID", "").strip()
client_secret = os.environ.get("WEBQUAKE_DMDATA_CLIENT_SECRET", "").strip()

logger = logging.getLogger("dmdws")
logger.setLevel(logging.DEBUG)
logger_fmt = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s (%(filename)s:%(lineno)d)')

std_handler = logging.StreamHandler()
std_handler.setLevel(logging.WARN)
std_handler.setFormatter(logger_fmt)
logger.addHandler(std_handler)

def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    logger.critical("Uncaught exception " + ' '.join(traceback.format_exception(exc_type, exc_value, exc_traceback)))

sys.excepthook = handle_exception



class Connection():
    def __init__(self, refresh_token:str, types:list[str], debug=False) -> None:
        self.refresh_token = refresh_token
        self.types = types

        self._access_token: Optional[str] = None
        self._access_token_expire: Optional[int] = None

        self._client = httpx.Client()

        self._listener: Optional[Callable[[dict], Any]] = None
        self._listener_threading: Optional[bool] = None

        self._websocket_id: Optional[int] = None

        if debug:
            log_dir = os.path.join(BASE_DIR, 'logs')
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, 'dmdws-{:%Y-%m-%d %H.%M.%S}.log'.format(datetime.datetime.now()))
            file_handler = logging.FileHandler(log_path)
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(logger_fmt)
            logger.addHandler(file_handler)

            std_handler.setLevel(logging.DEBUG)

            logger.debug("Debug mode enabled.")
    

    @property
    def access_token(self):
        logger.debug("Getting access token...")
        if not self._access_token_expire or int(time.time()) > self._access_token_expire:
            logger.debug("Access token expired or not set, refreshing...")
            
            last_error = ""
            for attempt in range(3):
                try:
                    logger.debug("Requesting new access token... (Attempt %s)", attempt + 1)
                    res = self._client.post("https://manager.dmdata.jp/account/oauth2/v1/token", data={
                        "grant_type": "refresh_token",
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "refresh_token": self.refresh_token
                    })
                    res.raise_for_status()
                
                except (httpx.HTTPError, httpx.StreamError):
                    last_error = traceback.format_exc()
                    logger.warn("Failed to get access token: %s", last_error)
                    if attempt != 2:
                        logger.debug("Retrying in 1 second...")
                        time.sleep(1)
                    
                    continue
                
                else:
                    data = res.json()
                    
                    self._access_token_expire = int(time.time()) + int(data['expires_in']) - 60 # 60 seconds buffer, just in case
                    self._access_token = data['access_token']
                    
                    logger.debug("Access token refreshed.")

                    return self._access_token

            raise Exception(f"Failed to get access token: {last_error}")
        
        return self._access_token


    def _get_websocket_auth(self) -> tuple[str, int]:
        logger.debug("Getting websocket auth...")

        last_error = ""
        for attempt in range(3):
            try:
                logger.debug("Requesting websocket auth... (Attempt %s)", attempt + 1)
                res = httpx.post("https://api.dmdata.jp/v2/socket",
                    headers={
                        "Authorization": f"Bearer {self.access_token}"
                    },
                    json={
                        "classifications": [
                            "application.jquake",
                            "telegram.earthquake",
                            "eew.forecast"
                        ],
                        "types": self.types,
                        "appName": "JQuake-1.8.5",
                        "formatMode": "json"
                    }
                )
                res.raise_for_status()
            
            except (httpx.HTTPError, httpx.StreamError):
                last_error = traceback.format_exc()
                logger.warn("Failed to get websocket auth: %s", last_error)
                if attempt != 2:
                    logger.debug("Retrying in 1 second...")
                    time.sleep(1)
                continue
            
            else:
                data = res.json()

                logger.debug("Websocket auth received.")
                
                return data['websocket']['url'], data['websocket']['id']

        raise Exception(f"Failed to get websocket auth: {last_error}")
    

    def _cleanup_closed_ws(self) -> Optional[bool]:
        if self._websocket_id:
            try:
                logger.debug("Cleaning up closed websocket...")
                res = httpx.delete(f"https://api.dmdata.jp/v2/socket/{self._websocket_id}",
                    headers={
                        "Authorization": f"Bearer {self.access_token}"
                    }
                )
                res.raise_for_status()
            
            except (httpx.HTTPError, httpx.StreamError):
                logger.warn("Failed to clean up closed websocket: %s", traceback.format_exc())
                return False
            
            self._websocket_id = None
            return True
        
        return None
    

    def _attempt_decode(self, data: dict) -> dict:
        data_copy = data.copy()
        
        try:
            if data.get('type') != 'data':
                raise Exception("Not a data message.")

            encoding_used = data.get('encoding')
            if encoding_used == 'base64':
                data['body'] = base64.b64decode(data['body'])
            else:
                raise Exception(f"Unsupported encoding: {encoding_used}")

            compression_used = data.get('compression')
            if compression_used == 'gzip':
                data['body'] = gzip.decompress(data['body'])
            else:
                raise Exception(f"Unsupported compression: {compression_used}")

            data['body'] = json.loads(data['body'].decode("utf-8"))
            return data
        
        except Exception:
            logger.warn("Error while decoding message: %s", traceback.format_exc())
            return data_copy


    def _connect(self, url: str) -> None:
        if not self._listener:
            raise Exception("No message listener set.")
        
        logger.debug("Connecting to websocket...")
        ws = websocket.create_connection(url, sslopt={"cert_reqs": ssl.CERT_REQUIRED, "ssl_context": ssl.create_default_context()})
        ping_count = 0
        last_ping_log = 0.0
        while True:
            data = ws.recv()
            if type(data) is bytes:
                data = data.decode("utf-8")

            json_data: dict = json.loads(data)

            if json_data.get("type") == "ping":
                ping_count += 1
                now = time.time()
                # Log the first few pings so startup is visible, then only once an hour to keep the console tidy.
                if ping_count <= 3 or now - last_ping_log >= 3600:
                    logger.debug("Received ping, sending pong... (pingId: %s)", json_data["pingId"])
                    last_ping_log = now
                ws.send(json.dumps({"type": "pong", "pingId": json_data["pingId"]}))
                continue

            else:
                logger.debug("Received data: %s", data)
                logger.debug("Received message, attempting to decode...")
                
                if json_data.get("type") == "data":
                    json_data = self._attempt_decode(json_data)

                logger.debug("Calling listener...")
                if self._listener_threading:
                    threading.Thread(target=self._listener, args=(json_data,)).start()
                else:
                    try:
                        self._listener(json_data)
                    except Exception:
                        logger.error("Error while calling listener: %s", traceback.format_exc())


    def set_message_listener(self, listener_func: Callable[[dict], Any], threading: bool) -> None:
        """Sets the function to be called when a message is received (excluding pings). You can only have one message listener.
        
        `listener_func` should be a function that takes a single argument, the message, given as a `dict`.

        `threading` should be a boolean. If `True` the listener will be called in a separate thread (allowing you to process multiple messages at the same time),
        if `False`, you will receieve messages one by one, and only receive the next one after the listener function has returned.

        Example:
        ```python
        def listener(msg: dict):
            print(msg)
        conn = Connection(...)
        conn.set_message_listener(listener, threading=True)
        conn.run()
        ```
        """
        if self._listener:
            raise Exception("A message listener is already set.")

        self._listener = listener_func
        self._listener_threading = threading
    

    def _manage_connections(self) -> NoReturn:
        while True:
            url, ws_id = self._get_websocket_auth()
            self._websocket_id = ws_id
            try:
                self._connect(url)
            
            except Exception:
                traceback.print_exc()
                time.sleep(3)
            
            self._cleanup_closed_ws()

    
    
    def run(self) -> NoReturn:
        """Runs the connection. The function will block indefinitely."""
        if not self._listener:
            raise Exception("No message listener set.")

        self._manage_connections()

    
    def start(self) -> None:
        """Starts the connection. The function will return immediately."""
        if not self._listener:
            raise Exception("No message listener set.")

        ws_thread = threading.Thread(target=self._manage_connections, daemon=True)
        ws_thread.start()


    @staticmethod
    def get_refresh_token() -> str:
        """Opens a browser to authenticate with DMDATA and returns a refresh token.

        Uses OAuth2 authorization code flow with PKCE. Example:
        ```python
        refresh_token = Connection.get_refresh_token()
        conn = Connection(refresh_token, types=[...])
        ```
        """
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b'=').decode()

        state = secrets.token_urlsafe(32)

        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]

        redirect_uri = f"http://127.0.0.1:{port}/Callback"

        auth_url = "https://manager.dmdata.jp/account/oauth2/v1/auth?" + urllib.parse.urlencode({
            "client_id": client_id,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "contract.list telegram.list telegram.data socket.start socket.list socket.close telegram.get.earthquake gd.earthquake eew.get.forecast eew.get.warning gd.eew",
            "state": state,
        })

        auth_code: Optional[str] = None
        received_state: Optional[str] = None

        class _CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                nonlocal auth_code, received_state
                params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                auth_code = params.get('code', [None])[0]
                received_state = params.get('state', [None])[0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Authentication successful. You can close this window.")

            def log_message(self, format, *args):  # noqa: A002
                pass

        print("Opening browser for DMDATA authentication...")
        webbrowser.open(auth_url)

        server = HTTPServer(('127.0.0.1', port), _CallbackHandler)
        server.handle_request()

        if received_state != state:
            raise Exception("State mismatch — possible CSRF.")
        if not auth_code:
            raise Exception("No authorization code received.")

        res = httpx.post("https://manager.dmdata.jp/account/oauth2/v1/token", data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": auth_code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        })
        res.raise_for_status()

        return res.json()['refresh_token']

