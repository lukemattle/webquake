"""Minimal .env loader (stdlib only). Importing this module loads the repo's .env
into os.environ without overriding variables that are already set."""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))

def load_env():
    for directory in (os.path.join(_HERE, '..'), _HERE):
        path = os.path.join(directory, '.env')
        if not os.path.isfile(path):
            continue
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, value = line.split('=', 1)
                key, value = key.strip(), value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                os.environ.setdefault(key, value)

load_env()

