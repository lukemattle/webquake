"""Public-only configuration tests; no upstream services or notifications."""

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


DEFAULTS = {
    'MAX_CONNECTIONS': 10000,
    'MAX_CONNECTIONS_PER_IP': 100,
    'MAX_MESSAGE_SIZE': 1024,
    'MAX_PENDING_MESSAGES': 64,
    'MAX_PENDING_BYTES': 1048576,
    'MAX_TOTAL_PENDING_BYTES': 134217728,
    'MAX_SEND_SECONDS': 10,
}


class ConfigurationTests(unittest.TestCase):
    def load_config(self, overrides):
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith('WEBQUAKE_WS_MAX_')}
        environment.update(overrides)
        code = ('import json, sys; sys.path.insert(0, "private"); '
                'import websocket_hub as hub; '
                f'print(json.dumps({{name: getattr(hub, name) for name in {list(DEFAULTS)!r}}}))')
        return subprocess.run([sys.executable, '-c', code], env=environment,
                              cwd=Path(__file__).resolve().parent,
                              capture_output=True, text=True, timeout=5)

    def test_defaults(self):
        result = self.load_config({})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), DEFAULTS)

    def test_each_override(self):
        overrides = {'WEBQUAKE_WS_' + key: str(value + 1) for key, value in DEFAULTS.items()}
        result = self.load_config(overrides)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {key: value + 1 for key, value in DEFAULTS.items()})

    def test_blank_uses_default(self):
        result = self.load_config({'WEBQUAKE_WS_' + key: '  ' for key in DEFAULTS})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), DEFAULTS)

    def test_invalid_values_fail_at_import(self):
        for key in DEFAULTS:
            for value in ('0', '-1', 'invalid', '1.5'):
                with self.subTest(key=key, value=value):
                    result = self.load_config({'WEBQUAKE_WS_' + key: value})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('WEBQUAKE_WS_' + key + ' must be a positive integer', result.stderr)
