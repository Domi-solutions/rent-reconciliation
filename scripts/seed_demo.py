"""Build a fresh demo database: every migration, then the Kijani Court demo data.

    ./venv/bin/python scripts/seed_demo.py /path/to/demo.db

Writes to a temporary file next to the target and swaps it in at the end, so a
running demo site is never left half-built. Refuses to touch a database that is
not a demo (it must not already contain anything but demo data).
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def build(target):
    target = os.path.abspath(target)
    tmp = target + '.building'
    for f in (tmp, tmp + '-wal', tmp + '-shm'):
        if os.path.exists(f):
            os.remove(f)
    env = dict(os.environ, DATABASE_PATH=tmp, FLASK_ENV='demo', DEMO_MODE='true', AT_SMS_ENABLED='false',
               PYTHONDONTWRITEBYTECODE='1')
    # 1. schema + every migration, exactly as the app builds it
    subprocess.run([sys.executable, '-c', 'import app'], cwd=ROOT, env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # 2. demo data, in its own process against the fresh file
    subprocess.run([sys.executable, '-c',
                    'from src.database.db import get_connection\n'
                    'from src.demo.seed import seed\n'
                    'with get_connection() as conn:\n'
                    '    print("demo property", seed(conn))'],
                   cwd=ROOT, env=env, check=True)
    os.replace(tmp, target)
    for suffix in ('-wal', '-shm'):
        if os.path.exists(target + suffix):
            os.remove(target + suffix)
    return target


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit('Usage: scripts/seed_demo.py <demo.db>')
    print('Built', build(sys.argv[1]))
