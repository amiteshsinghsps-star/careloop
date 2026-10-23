import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from careloop.config import Settings           # noqa: E402
from careloop import store                      # noqa: E402


@pytest.fixture()
def settings(tmp_path):
    """A Settings bound to a throwaway data root, with the store re-initialised."""
    s = Settings(env={
        "CARELOOP_DB_ROOT": str(tmp_path),
        "CARELOOP_AUTH": "off",
        "CARELOOP_PUBLIC_URL": "http://127.0.0.1:8080",
        "CARELOOP_ENCRYPT_FIELDS": "0",        # cryptography is optional; keep tests hermetic
        "CARELOOP_FAMILY_PIN": "481542",
    })
    store.init(s)
    yield s
    store._global = None
    store._settings = None


@pytest.fixture()
def cell(settings):
    g = store.global_db()
    hid = g.create_household("TESTHH", "481542")
    c = store.open_cell(hid, encrypt=False)
    yield c
    c.close()


def http(method, url, body=None, headers=None, timeout=10):
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def stop(proc, db_root):
    """Kill the child and remove its data root: a test run must not leave households in `data/`."""
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
    shutil.rmtree(db_root, ignore_errors=True)


@pytest.fixture(scope="session")
def live_server():
    """The real app, in a subprocess, with auth OFF (the documented demo mode)."""
    port = free_port()
    db = os.path.join("data", f"test-{port}")
    env = dict(os.environ, CARELOOP_AUTH="off", CARELOOP_DEMO="1", PORT=str(port),
               HOST="127.0.0.1", CARELOOP_DB_ROOT=db,
               CARELOOP_LOG="ERROR")
    proc = subprocess.Popen([sys.executable, os.path.join("scripts", "serve.py"),
                             "--port", str(port), "--auth", "off"],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            status, _, _ = http("GET", base + "/healthz", timeout=1)
            if status == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("server did not start: " + proc.stdout.read(4000).decode(errors="replace"))
    yield base
    stop(proc, db)


@pytest.fixture(scope="session")
def chaos_server():
    """A third instance with `db_locked,latency_800ms` injected: reads must survive, writes
    must be refused, and the p95 SLO has to fail on its own."""
    port = free_port()
    db = os.path.join("data", f"test-chaos-{port}")
    env = dict(os.environ, CARELOOP_AUTH="off", CARELOOP_DEMO="1", PORT=str(port),
               HOST="127.0.0.1", CARELOOP_CHAOS="db_locked,latency_800ms",
               CARELOOP_DB_ROOT=db,
               CARELOOP_LOG="ERROR")
    proc = subprocess.Popen([sys.executable, os.path.join("scripts", "serve.py"),
                             "--port", str(port), "--auth", "off",
                             "--chaos", "db_locked,latency_800ms"],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            status, _, _ = http("GET", base + "/healthz", timeout=1)
            if status == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("chaos server did not start: " + proc.stdout.read(4000).decode(errors="replace"))
    yield base
    stop(proc, db)


@pytest.fixture(scope="session")
def voice_server():
    """Another instance with its own data root, for the Alexa-shaped voice driver.

    The scenario driver captures visits and marks items done, so it would otherwise leave a
    household in the shared demo store and the plan tests downstream would find a transcript
    already recorded. Separate store, separate port, same server code."""
    port = free_port()
    db = os.path.join("data", f"test-voice-{port}")
    env = dict(os.environ, CARELOOP_AUTH="off", CARELOOP_DEMO="1", PORT=str(port),
               HOST="127.0.0.1", CARELOOP_DB_ROOT=db,
               CARELOOP_LOG="ERROR")
    proc = subprocess.Popen([sys.executable, os.path.join("scripts", "serve.py"),
                             "--port", str(port), "--auth", "off"],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            status, _, _ = http("GET", base + "/healthz", timeout=1)
            if status == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("voice server did not start: " + proc.stdout.read(4000).decode(errors="replace"))
    yield base
    stop(proc, db)


@pytest.fixture(scope="session")
def oauth_server():
    """A second instance with auth enforced, to prove the 401/linking behaviour."""
    port = free_port()
    db = os.path.join("data", f"test-oauth-{port}")
    env = dict(os.environ, CARELOOP_AUTH="oauth", PORT=str(port), HOST="127.0.0.1",
               CARELOOP_DB_ROOT=db,
               CARELOOP_PUBLIC_URL=f"http://127.0.0.1:{port}", CARELOOP_LOG="ERROR",
               CARELOOP_CLIENT_ID="careloop-web-client", CARELOOP_CLIENT_SECRET="s3cret-test",
               CARELOOP_FAMILY_PIN="481542")
    proc = subprocess.Popen([sys.executable, os.path.join("scripts", "serve.py"),
                             "--port", str(port), "--auth", "oauth",
                             "--public-url", f"http://127.0.0.1:{port}"],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            status, _, _ = http("GET", base + "/healthz", timeout=1)
            if status == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("oauth server did not start")
    yield base
    stop(proc, db)
