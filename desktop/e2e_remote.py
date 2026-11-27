#!/usr/bin/env python3
"""A remote session end to end, with the real program as the computer left
behind (20.0).

    xvfb-run -a python3 desktop/e2e_remote.py            ../main.py, by Python
    python desktop/e2e_remote.py dist/Ferritext-20.0.0-windows.exe

Three processes on one screen, and a stand-in for the relay:

  the away computer   the program named (an executable, or main.py run by
                      this Python), started as a person would start it, in
                      a home of its own that lists the controller in its
                      Remote access. Nothing in it is stubbed: its screen
                      is read, its input injected (Windows' SendInput,
                      X11's XTEST), its watcher started.
  the controller      a Ferritext app inside this script, on the real API:
                      it connects, sees the screen and sends the input.
  the target          a small window (Kivy) that writes down what it is
                      given: the clicks, the wheel, the keys, its text.

What is checked: the session answered by itself and its picture seen (the
target's colour found where the target is); the pointer, each button, the
wheel, a drag; text typed as a person codes and writes (capitals, signs,
brackets, letters no keyboard here has); the keys and shortcuts of editing
and moving about; the programs listed and one closed (Windows); a restart
asked from afar and the session regained once the computer is back; the
program ended as a crash would end it, started again by its watcher, and
the session regained; and how long each took.

The restart is played: FEXT_RESTART_TEST makes the program write the
restart's notice to a file instead of restarting this machine; this script
then ends the program and its watcher, and starts it as a sign-in would.
"""
import ctypes
import ctypes.util
import http.server
import importlib.util
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
MAIN = HERE.parent / "main.py"
WINDOWS = sys.platform == "win32"
#: Where the target window is put, and its colour (found in the picture).
TARGET = {"left": 16, "top": 16, "width": 440, "height": 300}
MAGENTA = (1.0, 0.0, 1.0)


def load_main(home: Path, server: str):
    """../main.py as a module, in a home and on a relay of its own."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ.update(HOME=str(home), USERPROFILE=str(home),
                      XDG_CONFIG_HOME=str(home / ".config"),
                      FEXT_LANG="en", FEXT_SERVER_URL=server,
                      FEXT_CALL_LOOPBACK="1", KIVY_NO_ARGS="1",
                      KIVY_NO_CONSOLELOG="1")
    if sys.platform.startswith("linux"):
        os.environ.setdefault("SDL_VIDEODRIVER", "x11")
    spec = importlib.util.spec_from_file_location("fext_e2e", MAIN)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fext_e2e"] = module
    spec.loader.exec_module(module)
    return module


def make_app(M, label: str):
    """An app without its window's loop, as the suites make one, but on
    the real API and with its own new identity."""
    app = M.FextApp()
    app._dismiss_splash = lambda *_a: None
    app._probe_transport = lambda: None
    app.build()
    priv, _p, _a, _n = M.mine_identity()
    address = app.keys.add_identity(priv, label)
    app.keys.activate(address)
    app.keys.set_username(label)
    app.crypto = M.CryptoEngine(app.keys)
    app.api = M.ApiClient(app.keys)
    app.store = M.LocalStore(app.keys.store_path())
    app.worker = M.SyncWorker(app)
    app.foreground = True
    return app


def who(app) -> dict:
    return {"address": app.keys.address, "username": app.keys.username,
            "public_key": app.keys.public_key_hex}


# ---- the away computer's home, made ready (python e2e_remote.py --prep) ----
def prepare(spec_file: str) -> None:
    """The away computer's data folder, as its person left it: an identity,
    the controller as a verified contact listed in Remote access, and a
    message sent to them (each side learns what the other's app can do
    from what it sends)."""
    spec = json.loads(Path(spec_file).read_text("utf-8"))
    M = load_main(Path(spec["home"]), spec["server"])
    from kivy.clock import Clock
    app = make_app(M, "away")
    peer = spec["peer"]
    app.store.upsert_contact(peer["address"], peer["username"],
                             peer["public_key"])
    app.store.set_verified(peer["address"], True)
    M.remote_access_grant(app.keys.address, peer["address"], 0)
    M.save_pref("device_name", "The away computer")
    Path(spec["out"]).write_text(json.dumps(who(app)), "utf-8")
    until = time.monotonic() + 30
    while not Path(spec["go"]).exists() and time.monotonic() < until:
        time.sleep(0.05)            # the relay knows this address by now
    app.send_text(peer["address"], "hi from the away computer")
    until = time.monotonic() + 30
    while app.store.outbox() and time.monotonic() < until:
        Clock.tick()
        app._drain_ui_queue(0)
        time.sleep(0.02)
    sent = not app.store.outbox()
    app.store.close() if hasattr(app.store, "close") else None
    os._exit(0 if sent else 3)


# ---- the target window (python e2e_remote.py --target LOG) -----------------
def target(log_file: str) -> None:
    """A window that writes down what it is given, one JSON line each."""
    os.environ["KIVY_NO_ARGS"] = "1"
    os.environ["KIVY_NO_CONSOLELOG"] = "1"
    from kivy.config import Config
    Config.set("graphics", "position", "custom")
    for key, value in TARGET.items():
        Config.set("graphics", key, str(value))
    Config.set("graphics", "borderless", "1")
    Config.set("graphics", "resizable", "0")
    Config.set("input", "mouse", "mouse,disable_multitouch")
    Config.set("kivy", "exit_on_escape", "0")
    from kivy.app import App
    from kivy.core.window import Window
    from kivy.uix.floatlayout import FloatLayout
    from kivy.uix.textinput import TextInput
    log = open(log_file, "a", encoding="utf-8")

    def say(kind: str, **what) -> None:
        log.write(json.dumps(dict(what, kind=kind, at=time.time())) + "\n")
        log.flush()

    class Target(App):
        title = "Ferritext e2e target"

        def build(self):
            Window.clearcolor = MAGENTA + (1,)
            try:
                Window.always_on_top = True
            except Exception:
                pass
            root = FloatLayout()
            # The lower half takes the text; the upper half stays the
            # colour the picture is searched for.
            self.field = TextInput(size_hint=(1, .5), pos_hint={"x": 0,
                                                                "y": 0})
            root.add_widget(self.field)
            self.field.focus = True
            self.field.bind(text=lambda _w, text: say("text", text=text),
                            focus=self._focus)
            Window.bind(
                on_mouse_down=lambda _w, x, y, b, m: say(
                    "down", x=x, y=y, button=b, mods=sorted(m)),
                on_mouse_up=lambda _w, x, y, b, m: say(
                    "up", x=x, y=y, button=b, mods=sorted(m)),
                mouse_pos=lambda _w, pos: say("move", x=pos[0], y=pos[1]),
                on_key_down=lambda _w, key, _s, _c, mods: say(
                    "key", key=key, mods=sorted(mods)),
                on_request_close=self._close)
            say("up", x=0, y=0, button="none", mods=[],
                left=Window.left, top=Window.top, size=list(Window.size))
            return root

        def _focus(self, field, focused):
            if not focused:                 # a click outside it took it
                field.focus = True

        def _close(self, *_a, **_k):
            say("close")
            return False

    Target().run()


if len(sys.argv) > 2 and sys.argv[1] == "--prep":
    prepare(sys.argv[2])
if len(sys.argv) > 2 and sys.argv[1] == "--target":
    target(sys.argv[2])
    os._exit(0)                 # at once: no teardown that could fault


# ---- the relay's stand-in ---------------------------------------------------
class Relay(http.server.ThreadingHTTPServer):
    """What the app asks of a relay to send, receive and call: a directory
    (filled by this script), a mailbox each, a pass for the call port. No
    realtime socket (the app then asks every few seconds), nothing checked:
    the checking is the real relay's, and the app's own of what it is
    given."""
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), RelayHandler)
        self.users, self.mail, self.next_id = {}, [], 1
        self.seen, self.lock = {}, threading.Lock()
        self.udp_port = 0
        self.url = "http://127.0.0.1:%d" % self.server_address[1]


class RelayHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):
        pass

    def _answer(self, status: int, body) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _unknown(self) -> None:
        self._answer(404, {"error": {"code": "not_found",
                                     "message": "not here"}})

    def do_GET(self):
        relay, url = self.server, urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        me = self.headers.get("X-Client-Address", "")
        with relay.lock:
            if me:
                relay.seen[me] = time.time()
            if url.path == "/api/health":
                return self._answer(200, {
                    "ok": True, "version": "stand-in",
                    "features": ["calls"], "call_udp": relay.udp_port})
            if url.path == "/api/users/lookup":
                for user in relay.users.values():
                    if query.get("address") == user["address"] or \
                            query.get("username") == user["username"]:
                        return self._answer(200, user)
                return self._unknown()
            if url.path == "/api/username/available":
                return self._answer(200, {"available": True})
            if url.path == "/api/messages/sync":
                return self._answer(200, {"messages": [
                    m for m in relay.mail if m["recipient"] == me],
                    "has_more": False})
            if url.path == "/api/events/sync":
                return self._answer(200, {"events": []})
            if url.path == "/api/calls/ticket":
                return self._answer(200, {"ticket": "e2e", "ttl": 60,
                                          "udp": relay.udp_port})
        self._unknown()

    def do_POST(self):
        relay = self.server
        size = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(size) or b"{}")
        except ValueError:
            body = {}
        me = self.headers.get("X-Client-Address", "")
        now = datetime.now(timezone.utc).isoformat()
        with relay.lock:
            if me:
                relay.seen[me] = time.time()
            if self.path == "/api/register":
                for user in relay.users.values():
                    if user["public_key"] == body.get("public_key"):
                        return self._answer(200, user)
                return self._unknown()
            if self.path == "/api/messages/send":
                relay.next_id += 1
                relay.mail.append({
                    "id": relay.next_id, "sender": me,
                    "recipient": body.get("recipient"),
                    "envelope": body.get("envelope"),
                    "client_msg_id": body.get("client_msg_id"),
                    "created_at": now})
                return self._answer(200, {"server_id": relay.next_id,
                                          "created_at": now})
            if self.path == "/api/messages/ack":
                ids = set(body.get("ids") or [])
                relay.mail[:] = [m for m in relay.mail if not (
                    m["id"] in ids and m["recipient"] == me)]
                return self._answer(200, {"acked": len(ids)})
            if self.path == "/api/messages/read":
                return self._answer(200, {"updated": len(body.get("ids")
                                                          or [])})
            if self.path == "/api/prefs/ticks":
                return self._answer(200, {})
        self._unknown()


class CallPort(threading.Thread):
    """The relay's call port: REG answered with the address seen; packets
    passed between the two ends of a tag (as the suites' stand-in does)."""

    def __init__(self, M):
        super().__init__(daemon=True)
        self.M = M
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.ends, self.passed = {}, 0

    def run(self):
        M = self.M
        self.sock.settimeout(0.1)
        while True:
            try:
                data, src = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except ConnectionResetError:    # Windows: an ICMP refusal from
                continue                    # an end that has gone
            except OSError:
                return
            if len(data) < 10:
                continue
            tag = data[1:9]
            if data[0] == M.PKT_REG:
                self.ends.setdefault(tag, {})[data[9]] = src
                self.sock.sendto(struct.pack("!B8sBB", M.PKT_REG_OK, tag,
                                             data[9], 4) +
                                 socket.inet_aton(src[0]) +
                                 struct.pack("!HB", src[1], 1), src)
                continue
            ends = self.ends.get(tag, {})
            role = next((r for r, a in ends.items() if a == src), None)
            if role is not None and (1 - role) in ends:
                self.sock.sendto(data, ends[1 - role])
                self.passed += 1


# ---- this machine's screen and pointer, read ---------------------------------
if WINDOWS:
    _user32 = ctypes.windll.user32

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    def screen() -> tuple:
        """(left, top, width, height) of the whole desktop."""
        return tuple(_user32.GetSystemMetrics(n) for n in (76, 77, 78, 79))

    def pointer() -> tuple:
        at = _POINT()
        _user32.GetCursorPos(ctypes.byref(at))
        return at.x, at.y

    def front() -> str:
        """The window in front, for a failure's account: its title, its
        class and its program. Windows gives a program no input into a
        window of a program above it (run as administrator, a system
        dialog)."""
        from ctypes import wintypes
        _user32.GetForegroundWindow.restype = ctypes.c_void_p
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return "no window in front"
        title = ctypes.create_unicode_buffer(200)
        kind = ctypes.create_unicode_buffer(200)
        _user32.GetWindowTextW(ctypes.c_void_p(hwnd), title, 200)
        _user32.GetClassNameW(ctypes.c_void_p(hwnd), kind, 200)
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd),
                                         ctypes.byref(pid))
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        image = "(not readable: a program above this one)"
        process = kernel32.OpenProcess(0x1000, False, pid.value)
        if process:
            path = ctypes.create_unicode_buffer(520)
            size = wintypes.DWORD(520)
            if kernel32.QueryFullProcessImageNameW(
                    ctypes.c_void_p(process), 0, path, ctypes.byref(size)):
                image = path.value
            kernel32.CloseHandle(ctypes.c_void_p(process))
        return (f"in front: {title.value!r} ({kind.value}), "
                f"process {pid.value} {image}")
else:
    def front() -> str:
        return ""

    _x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
    _x11.XOpenDisplay.restype = ctypes.c_void_p
    _x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    _x11.XDefaultRootWindow.restype = ctypes.c_ulong
    _x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    _x11.XQueryPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong] + \
        [ctypes.POINTER(ctypes.c_ulong)] * 2 + \
        [ctypes.POINTER(ctypes.c_int)] * 4 + [ctypes.POINTER(ctypes.c_uint)]
    _x11.XDisplayWidth.argtypes = _x11.XDisplayHeight.argtypes = \
        [ctypes.c_void_p, ctypes.c_int]
    _dpy = _x11.XOpenDisplay(None)
    _root = _x11.XDefaultRootWindow(_dpy)

    def screen() -> tuple:
        return 0, 0, _x11.XDisplayWidth(_dpy, 0), _x11.XDisplayHeight(_dpy, 0)

    def pointer() -> tuple:
        r, c = ctypes.c_ulong(), ctypes.c_ulong()
        at = [ctypes.c_int() for _ in range(4)]
        mask = ctypes.c_uint()
        _x11.XQueryPointer(_dpy, _root, ctypes.byref(r), ctypes.byref(c),
                           *(ctypes.byref(v) for v in at),
                           ctypes.byref(mask))
        return at[0].value, at[1].value


def share(x: float, y: float) -> tuple:
    """A point of the screen as the wire has it: a share of the whole
    picture, 0 to 65535 across and down."""
    left, top, wide, high = screen()
    return (round((x - left) * 65535 / (wide - 1)),
            round((y - top) * 65535 / (high - 1)))


# ---- processes -----------------------------------------------------------------
def end_tree(proc) -> None:
    if proc is None or proc.poll() is not None:
        return
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.wait(30)
    except subprocess.TimeoutExpired:
        pass


def end_pid(pid: int) -> None:
    """One process ended at once, as a crash ends it."""
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def end_watchers(home: Path) -> None:
    """The away computer's watcher (it is nobody's child), ended."""
    mark = str(home / ".fext")
    if WINDOWS:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine "
             "-like '*--guard*' } | ForEach-Object { Stop-Process -Id "
             "$_.ProcessId -Force }"], capture_output=True)
    else:
        subprocess.run(["pkill", "-9", "-f", "--", f"--guard {mark}"],
                       capture_output=True)


def end_all(procs: dict, home: Path) -> None:
    """Everything the run started, ended: the watcher first (it would
    start the program again as it is ended), then the programs, and the
    one the watcher started, which is nobody's child. A window left open
    would lie over the next run's."""
    end_watchers(home)
    for proc in procs.values():
        end_tree(proc)
    try:
        end_pid(json.loads((home / ".fext" / "keepup.json").read_text())
                ["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    end_watchers(home)


#: The lines of the away program's own log worth a failure's account.
TELLING = ("remote", "refused", "input", "video:", "control", "Traceback",
           "Error", "ERROR", "screen",
           # 21.0: what a regain that did not come needs said (run 44,
           # Windows): the call, the watcher, a second Ferritext.
           "call", "session", "keep", "guard", "open already", "lock")


def kept_log(home: Path, last: int = 30) -> list:
    """The end of what the away program logged itself (Kivy keeps the
    app's lines in its home; TELLING picks those that matter): one its
    watcher started says nothing anywhere else."""
    lines = []
    for path in sorted((home / ".kivy" / "logs").glob("*.txt"))[-2:]:
        try:
            said = path.read_text("utf-8", "replace").splitlines()
        except OSError:
            continue
        lines += [f"{path.name}:"] + [
            line for line in said if not line.startswith("[DEBUG") and
            any(word in line for word in TELLING)][-last:]
    return lines


# ---- the run ---------------------------------------------------------------------
RESULTS, TIMES = [], {}
#: What a session needs of the program (main.py: build_check's names).
NEEDED = ("video (PyAV)", "screen sharing", "remote control")
NOTE = "  ! "       # a failure's account (tools/devices/run_suites.py shows these)


def note(*lines) -> None:
    for line in lines:
        print(NOTE + str(line)[:280], flush=True)


def check(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(("  [PASS] " if cond else "  [FAIL] ") + name +
          (f" — {detail}" if detail and not cond else ""), flush=True)


def main() -> int:
    away_cmd = [sys.executable, str(MAIN)]
    if len(sys.argv) > 1 and not sys.argv[1].endswith(".py"):
        away_cmd = [str(Path(sys.argv[1]).resolve())]   # a built executable
    print("the away computer runs: " + " ".join(away_cmd), flush=True)
    work = Path(tempfile.mkdtemp(prefix="fext-e2e-"))
    out = Path(os.environ.get("FEXT_E2E_OUT") or work / "out")
    out.mkdir(parents=True, exist_ok=True)
    relay = Relay()
    threading.Thread(target=relay.serve_forever, daemon=True).start()
    M = load_main(work / "controller", relay.url)
    from kivy.clock import Clock
    from kivy.core.window import Window
    port = CallPort(M)
    port.start()
    relay.udp_port = port.port

    alice = make_app(M, "controller")
    A = alice.keys.address
    relay.users[A] = who(alice)

    def pump():
        Clock.tick()
        # The controller's window reads its own events, as a running
        # program's does. One that does not is "not responding" to
        # Windows within seconds, which puts a stand-in window of the
        # system's in front, and input sent from afar is then refused.
        Window.mainloop()
        alice._drain_ui_queue(0)

    def wait(cond, timeout=10.0):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            pump()
            if cond():
                return True
            time.sleep(0.01)
        pump()
        return bool(cond())

    # -- the away computer's home, and the program started in it -------------
    home = work / "away"
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
               XDG_CONFIG_HOME=str(home / ".config"),
               KIVY_HOME=str(home / ".kivy"),
               FEXT_RESTART_TEST=str(work / "restart.asked"))
    env.pop("KIVY_NO_CONSOLELOG", None)
    if WINDOWS:
        for var, sub in (("LOCALAPPDATA", "Local"), ("APPDATA", "Roaming")):
            (home / "AppData" / sub).mkdir(parents=True, exist_ok=True)
            env[var] = str(home / "AppData" / sub)
    home.mkdir(parents=True, exist_ok=True)

    # What the program says it can do here (--check). Plain Python without
    # its video package cannot be controlled, and the run says so in one
    # line; an executable that cannot is a failed build.
    report = work / "check.json"
    try:
        subprocess.run(away_cmd + ["--check", str(report)], env=env,
                       cwd=str(home), timeout=180, capture_output=True)
        can = json.loads(report.read_text("utf-8"))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        can = {"report": f"none ({type(exc).__name__}: {exc})"}
    lacks = "; ".join(f"{part}: {can.get(part, can.get('report', '?'))}"
                      for part in NEEDED if can.get(part, "?"))
    if lacks:
        built = len(away_cmd) == 1
        check("the program can be controlled on this computer" if built else
              "this Python cannot be controlled, so no session is tried "
              f"({lacks[:200]})", not built, lacks)
        print(f"\n  {sum(RESULTS)}/{len(RESULTS)} passed")
        return 1 if built else 0
    spec = work / "prep.json"
    spec.write_text(json.dumps({
        "home": str(home), "server": relay.url, "peer": who(alice),
        "out": str(work / "away.json"), "go": str(work / "go")}), "utf-8")
    prep = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                             "--prep", str(spec)], env=env)
    wait(lambda: (work / "away.json").exists() or prep.poll() is not None,
         120)
    if not (work / "away.json").exists():
        print("the away computer's home could not be made ready")
        return 2
    away = json.loads((work / "away.json").read_text("utf-8"))
    B = away["address"]
    relay.users[B] = away
    (work / "go").write_text("go")
    alice.store.upsert_contact(B, away["username"], away["public_key"])
    alice.store.set_verified(B, True)
    alice.worker.start()
    alice.send_text(B, "hi from the controller")
    wait(lambda: prep.poll() is not None, 60)

    print("\nBefore the session: messages both ways through the relay")
    check("the away computer's home was made ready, its message sent",
          prep.returncode == 0, f"exit code {prep.returncode}")
    check("the controller received it, and may call",
          wait(lambda: alice.call_blocker(B, remote=True) is None, 30),
          str(alice.call_blocker(B, remote=True)))

    procs = {"away": None, "target": None}

    def start_away(*extra):
        said = open(out / "away.out.txt", "ab")
        procs["away"] = subprocess.Popen(
            away_cmd + list(extra), env=env, cwd=str(home), stdout=said,
            stderr=subprocess.STDOUT, start_new_session=not WINDOWS)
        return time.monotonic()

    def away_up(since, timeout=180.0):
        """The program asks the relay for its mail: it runs, and is
        connected."""
        mark = time.time()
        return wait(lambda: relay.seen.get(B, 0) > mark, timeout) and \
            time.monotonic() - since

    try:
        return run(M, alice, A, B, relay, port, procs, start_away, away_up,
                   wait, pump, work, out, home, env)
    finally:
        end_all(procs, home)
        try:
            alice.end_call()
        except Exception:
            pass


def run(M, alice, A, B, relay, port, procs, start_away, away_up, wait, pump,
        work, out, home, env) -> int:
    log_file = work / "target.jsonl"

    def rest(seconds):
        """Time passes, the controller's window answering throughout."""
        wait(lambda: False, seconds)

    def events(kind=None, since=0):
        try:
            lines = log_file.read_text("utf-8").splitlines()
        except OSError:
            return []
        got = [json.loads(line) for line in lines[since:]]
        return [e for e in got if kind is None or e["kind"] == kind]

    def count():
        try:
            return len(log_file.read_text("utf-8").splitlines())
        except OSError:
            return 0

    def text():
        seen = events("text")
        return seen[-1]["text"] if seen else ""

    def send(kind, *fields):
        alice.send_remote_input(kind, tuple(fields))

    def key(name, down=None):
        number = M.REMOTE_KEYS.index(name)
        for state in ((1, 0) if down is None else (int(down),)):
            send(M.INPUT_KEY, number, state)

    def chord(*names):
        for name in names:
            key(name, True)
        for name in reversed(names):
            key(name, False)

    def typed(words):
        """As RemoteSurface sends a computer's typing: a few characters a
        packet."""
        for at in range(0, len(words), 8):
            send(M.INPUT_TEXT, words[at:at + 8])

    def controlled():
        call = alice.call
        return call is not None and not call.ended and \
            alice.controlling(call)

    def picture():
        """Their screen as this side drew it last, as an image; None
        before the first."""
        call = alice.call
        frames = alice.__dict__.get("_call_frames") or {}
        for (user, track), tex in list(frames.items()):
            if track == "s" and tex is not None and call is not None and \
                    user != alice.my_call_address():
                return tex
        return None

    print("\nThe program, started as a person starts it")
    began = start_away()
    TIMES["start"] = away_up(began)
    check("it runs and asks the relay for its mail"
          + (f" ({TIMES['start']:.1f} s)" if TIMES["start"] else ""),
          TIMES["start"], f"see {out / 'away.out.txt'}")
    (work / "target").mkdir(exist_ok=True)
    procs["target"] = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--target",
         str(log_file)], stdout=open(out / "target.out.txt", "ab"),
        stderr=subprocess.STDOUT,
        env=dict(os.environ, HOME=str(work / "target"),
                 USERPROFILE=str(work / "target"),
                 KIVY_HOME=str(work / "target" / ".kivy")))
    check("the target window is up",
          wait(lambda: any("size" in e for e in events("up")), 60))
    rest(1.0)

    print("\nThe session: answered by itself, its screen seen")
    alice.open_conversation(B)
    began = time.monotonic()
    placed = alice.connect_remote(B)
    check("Connect to their computer places the session", placed,
          str(alice.call_blocker(B, remote=True)))
    answered = wait(controlled, 60)
    TIMES["answer"] = time.monotonic() - began
    check(f"their computer answered by itself and let this side control it "
          f"({TIMES['answer']:.1f} s)", answered,
          f"call: {getattr(alice.call, '__dict__', None)}")
    seen = wait(lambda: picture() is not None, 45)
    TIMES["picture"] = time.monotonic() - began
    check(f"its screen arrived ({TIMES['picture']:.1f} s after Connect)",
          seen)
    left, top, wide, high = screen()
    spot = (TARGET["left"] + TARGET["width"] / 2,
            TARGET["top"] + TARGET["height"] / 4)     # the coloured half

    def colour_there():
        tex = picture()
        if tex is None:
            return None
        from PIL import Image
        tex.save(str(out / "picture.png"), flipped=False)
        image = Image.open(out / "picture.png").convert("RGB")
        x = round((spot[0] - left) / wide * image.width)
        y = round((spot[1] - top) / high * image.height)
        return image.getpixel((min(x, image.width - 1),
                               min(y, image.height - 1)))

    def magenta():
        got = colour_there()
        return got is not None and got[0] > 170 and got[2] > 170 and \
            got[1] < 100
    check("the picture is this screen: the target's colour is where the "
          "target is", wait(magenta, 20), f"{colour_there()} at {spot}")
    tex = picture()
    if tex is not None:
        print(f"    their screen is {wide}x{high}; drawn here from "
              f"{tex.width}x{tex.height}")

    print("\nThe pointer, the buttons, the wheel, a drag")
    aim = (TARGET["left"] + 60, TARGET["top"] + 40)
    send(M.INPUT_MOVE, *share(*aim))
    began = time.monotonic()
    moved = wait(lambda: all(abs(a - b) <= 2 for a, b in
                             zip(pointer(), aim)), 5)
    TIMES["move"] = time.monotonic() - began
    if not moved:
        note(f"their state: {getattr(alice.call, 'rc_state', None)}",
             front(), *kept_log(home, 12))
    check(f"the pointer goes where it is sent ({TIMES['move'] * 1000:.0f} ms)",
          moved, f"{pointer()} for {aim}")
    names = {1: "left", 2: "right", 3: "middle"}
    for number, name in names.items():
        mark = count()
        send(M.INPUT_BUTTON, number, 1, *share(*aim))
        send(M.INPUT_BUTTON, number, 0, *share(*aim))
        ok = wait(lambda: [e["button"] for e in events(None, mark)
                           if e["kind"] in ("down", "up")] == [name, name], 5)
        got = [e for e in events(None, mark) if e["kind"] in ("down", "up")]
        where = got and abs(got[0]["x"] - 60) <= 2
        check(f"the {name} button, down and up, where it was aimed",
              ok and where, str(got))
    for dy, name in ((120, "scrolldown"), (-120, "scrollup")):
        mark = count()
        send(M.INPUT_WHEEL, 0, dy)
        check(f"the wheel turned {'away' if dy > 0 else 'towards'}",
              wait(lambda: any(e["button"] == name
                               for e in events("down", mark)), 5),
              str(events("down", mark)))
    mark = count()
    send(M.INPUT_BUTTON, 1, 1, *share(*aim))
    for step in range(1, 6):
        send(M.INPUT_MOVE, *share(aim[0] + step * 20, aim[1]))
    send(M.INPUT_BUTTON, 1, 0, *share(aim[0] + 100, aim[1]))
    wait(lambda: events("up", mark), 5)
    drag = events(None, mark)
    downs = [e for e in drag if e["kind"] == "down"]
    ups = [e for e in drag if e["kind"] == "up"]
    check("a drag: held, drawn 100 points across, let go",
          downs and ups and abs(ups[-1]["x"] - downs[0]["x"] - 100) <= 3
          and any(e["kind"] == "move" for e in drag), str(drag[-4:]))

    print("\nTyping and editing, as a person writes and codes")
    field = (TARGET["left"] + 100, TARGET["top"] + TARGET["height"] - 40)
    send(M.INPUT_BUTTON, 1, 1, *share(*field))
    send(M.INPUT_BUTTON, 1, 0, *share(*field))
    rest(0.5)
    line = "Hello, Ferritext 20! def f(x): return {'a': [x**2]} #~|\\ <-> €ß你好"
    began = time.monotonic()
    typed(line)
    ok = wait(lambda: text() == line, 15)
    TIMES["typing"] = time.monotonic() - began
    check(f"a line of prose and code arrives whole: capitals, signs, "
          f"brackets, letters this keyboard lacks ({len(line)} characters "
          f"in {TIMES['typing']:.1f} s)", ok, repr(text()))
    key("enter")
    typed("second line")
    check("Enter starts a line", wait(
        lambda: text() == line + "\nsecond line", 5), repr(text()))
    for _ in range(4):
        key("backspace")
    check("Backspace, four times", wait(
        lambda: text() == line + "\nsecond ", 5), repr(text()))
    key("home")
    typed("the ")
    check("Home, then typing at the line's start", wait(
        lambda: text() == line + "\nthe second ", 5), repr(text()))
    key("end")
    typed("line.")
    key("up")
    key("home")
    typed("# ")
    check("End, Up and Home move about", wait(
        lambda: text() == "# " + line + "\nthe second line.", 5),
        repr(text()))
    key("delete")
    check("Delete takes the character ahead", wait(
        lambda: text() == "# " + line[1:] + "\nthe second line.", 5),
        repr(text()))
    key("shift", True)
    key("end")
    key("shift", False)
    typed("x")
    check("Shift+End selects to the line's end; typing replaces it", wait(
        lambda: text() == "# x\nthe second line.", 5), repr(text()))
    chord("ctrl", "a")
    typed("all gone")
    check("Ctrl+A selects everything", wait(
        lambda: text() == "all gone", 5), repr(text()))
    chord("ctrl", "z")
    check("Ctrl+Z takes the last typing back", wait(
        lambda: text() and text() != "all gone", 5), repr(text()))
    mark = count()
    chord("ctrl", "shift", "p")
    key("f5")
    key("escape")
    key("tab")
    wait(lambda: len(events("key", mark)) >= 6, 5)
    keys = [(e["key"], e["mods"]) for e in events("key", mark)]
    check("a shortcut with two modifiers (Ctrl+Shift+P), F5, Esc and Tab "
          "arrive as keys", any(k == 112 and "ctrl" in m and "shift" in m
                                for k, m in keys) and
          {286, 27, 9} <= {k for k, _m in keys}, str(keys))
    for name, code in (("slash", 47), ("minus", 45), ("equal", 61),
                       ("bracketleft", 91), ("grave", 96)):
        if name not in M.REMOTE_KEYS:
            check(f"Ctrl+{name} can be sent", False, "no such key")
            continue
        mark = count()
        chord("ctrl", name)
        check(f"Ctrl+{name} arrives as that key with Ctrl held",
              wait(lambda: any(e["key"] == code and "ctrl" in e["mods"]
                               for e in events("key", mark)), 5),
              str([(e["key"], e["mods"]) for e in events("key", mark)]))

    if WINDOWS:
        print("\nThe programs open on it: listed, and one closed")
        sheets = []
        real_show = M.RemoteProgramsSheet.show
        M.RemoteProgramsSheet.show = classmethod(
            lambda cls, app, programs: sheets.append(programs))
        alice.remote_ask({"op": "list"})
        wait(lambda: sheets, 10)
        listed = sheets[-1] if sheets else None
        ours = [p for p in listed or [] if "e2e target" in p.get("t", "")]
        check("the target window is in the list", bool(ours), str(listed))
        if ours:
            alice.remote_ask({"op": "end", "w": ours[0]["w"],
                              "p": ours[0]["p"]})
            check("closing it from afar asks the window to close",
                  wait(lambda: events("close"), 10))
        M.RemoteProgramsSheet.show = real_show

    aims = iter([(200, 60), (120, 90), (260, 40), (160, 110)])

    def regained(label, began, timeout):
        """(the session is back and moves the pointer, seconds it took).
        The pointer is sent somewhere it is not, once a second until it is
        there: a first send that does nothing is said (the session was
        up before its input was)."""
        back = wait(controlled, timeout) and wait(
            lambda: picture() is not None, 45)
        took = time.monotonic() - began
        TIMES[label] = took
        if not back:
            call = alice.call
            try:
                kept = (home / ".fext" / "keepup.json").read_text()
            except OSError as exc:
                kept = f"({exc})"
            note(f"{label}: no session ({controlled()}) or no picture "
                 f"after {took:.0f} s",
                 # 21.0: the controller's side of it, and the watcher's
                 f"the controller's call: {call and call.state}, ended "
                 f"{call and call.ended}, remote {call and call.remote}, "
                 f"placing it again: {alice.__dict__.get('_remote_retry')}",
                 f"keepup.json: {kept}",
                 *kept_log(home, 45))
            return False, took
        dx, dy = next(aims)
        aim = (TARGET["left"] + dx, TARGET["top"] + dy)
        there = lambda: all(abs(a - b) <= 2                   # noqa: E731
                            for a, b in zip(pointer(), aim))
        sends = 0
        while not there() and sends < 15:
            sends += 1
            send(M.INPUT_MOVE, *share(*aim))
            wait(there, 1.0)
        if sends != 1:
            call = alice.call
            note(f"{label}: the pointer is at {pointer()}, sent to {aim} "
                 f"{sends} times; their state: "
                 f"{getattr(call, 'rc_state', None)}", front(),
                 *([] if there() else kept_log(home, 12)))
        return there(), took

    print("\nThe program ended as a crash ends it: its watcher starts it "
          "again")
    try:
        state = json.loads((home / ".fext" / "keepup.json").read_text())
    except (OSError, ValueError):
        state = {}
    check("the watcher knows the program (keepup.json)",
          isinstance(state.get("pid"), int) and state.get("quit") is False,
          str(state))
    if isinstance(state.get("pid"), int):
        began = time.monotonic()
        end_pid(state["pid"])
        dropped = wait(lambda: not controlled(), 60)
        check("the session drops", dropped)
        back, took = regained("crash", began, 180)
        check(f"...and is regained by itself, the pointer moved again "
              f"({took:.0f} s after the crash)", back,
              f"see {out / 'away.out.txt'}")

    print("\nA restart asked from afar: the session regained once it is back")
    asked = work / "restart.asked"
    alice.remote_ask({"op": "restart"})
    check("the computer takes the restart",
          wait(asked.exists, 15) and
          wait(lambda: getattr(alice.call, "restarting", False), 10))
    began = time.monotonic()
    end_watchers(home)                      # a restart ends all of it
    end_tree(procs["away"])
    try:
        end_pid(json.loads((home / ".fext" / "keepup.json").read_text())
                ["pid"])
    except (OSError, ValueError, KeyError):
        pass
    check("the session drops as the computer goes down",
          wait(lambda: not controlled(), 60))
    rest(10)                                # the computer is down a while
    start_away("--startup")                 # ...then starts, at sign-in
    back, took = regained("restart", began, 240)
    check(f"the controller connects again by itself, the pointer moved "
          f"again ({took:.0f} s after the restart began)", back,
          f"see {out / 'away.out.txt'}")
    words = [e for e in alice.store.fetch_conversation(B)
             if "restart" in str(e.get("body", "")).lower()] \
        if hasattr(alice.store, "fetch_conversation") else []
    del words

    print("\nEnded from the controller's side, and connected again by hand")
    alice.end_call()
    wait(lambda: alice.call is None or alice.call.ended, 10)
    rest(2)
    began = time.monotonic()
    again = alice.connect_remote(B)
    back, took = regained("again", began, 90)
    check(f"a new session connects ({took:.1f} s)", again and back)
    alice.end_call()
    wait(lambda: alice.call is None or alice.call.ended, 10)

    passed = sum(RESULTS)
    print("\n  times: " + ", ".join(
        f"{name} {value:.1f} s" for name, value in TIMES.items() if value))
    print(f"\n  {passed}/{len(RESULTS)} passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    code = main()
    os._exit(code)
