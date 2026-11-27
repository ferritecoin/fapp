#!/usr/bin/env python3
"""21.0 (SERVICE29): the Windows service that lets a remote session reach
the lock screen, Ctrl+Alt+Del, administrators' windows and (when allowed)
permission prompts.

    python3 desktop/test_winservice.py main.py

On every system, its logic: the records between the person's Ferritext and
the helper, what the helper does with each (a malformed one dropped, a
permission prompt only looked at unless the service allows more), the
pipes' security, what the screen's state becomes, Ctrl+Alt+Del from afar,
and the app's side (DeskClient, HelperInjector) against a stand-in helper.

On GitHub's Windows runner (GITHUB_ACTIONS), the service itself, run from
this Python build: installed and started; its helper found on this
session as SYSTEM; the screen read through it and the pointer moved; a
program that does not answer in front, which Windows keeps every
program's input from, used through the helper and ended; then the lock
screen: locked, read, Ctrl+Alt+Del pressed, the password typed, unlocked;
then removed. These change Windows (a service, the account's password,
the lock) and so never run on anyone's own computer. When one fails, the
job's log gets the helper's desk.log and pictures of the screen: the
lines after "  ! picture NAME", joined and base64-decoded, are a PNG.
"""
import base64
import ctypes
import io
import importlib.util
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
# No window of its own: one it never draws would not answer, and in front
# it would be the program that does not answer (Devices run 45).
os.environ.update({"KIVY_WINDOW": "none", "KIVY_DPI": "160",
                   "KIVY_METRICS_DENSITY": "1", "KIVY_METRICS_FONTSCALE": "1"})
HOME = tempfile.mkdtemp(prefix="fext-winservice-")
os.environ["HOME"] = HOME
os.environ["USERPROFILE"] = HOME
os.environ["FEXT_LANG"] = "en"

MAIN_PY = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "main.py")
_spec = importlib.util.spec_from_file_location("fext_winservice", MAIN_PY)
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_winservice"] = M
_spec.loader.exec_module(M)

RESULTS = []
NOTE = "  ! "         # lines tools/devices/run_suites.py copies to the job log


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f" — {detail}" if detail and not cond else ""), flush=True)


def wait(cond, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.2)
    return bool(cond())


# =============================================================================
print("The records")
R = M.DESK_RECORD
check("a record is 16 bytes, the same either way",
      R.size == 16 and R.unpack(R.pack(M.DESK_KEY, 3, 0x2E, 0x53, 0, 0))
      == (M.DESK_KEY, 3, 0x2E, 0x53, 0, 0))
check("each kind has its own number",
      len({M.DESK_HELLO, M.DESK_MOVE, M.DESK_BUTTON, M.DESK_WHEEL,
           M.DESK_KEY, M.DESK_UNIT, M.DESK_GRAB, M.DESK_SAS, M.DESK_STATE,
           M.DESK_MAP, M.DESK_FRAME}) == 11)
check("the pipes: owned by SYSTEM, open to it and to the person signed in",
      M.desk_pipe_security("S-1-5-21-1-2-3-1001") ==
      "O:SYG:SYD:P(A;;GA;;;SY)(A;;GRGW;;;S-1-5-21-1-2-3-1001)" and
      M.desk_pipe_security("") == "O:SYG:SYD:P(A;;GA;;;SY)")


# =============================================================================
print("\nWhat the helper does with what it is sent")


class StandIn:
    """_DeskInput's SendInput, kept instead of done."""

    def __init__(self):
        self.done = []

    def do(self, kind, flag, a, b, c, as_keys=False):
        self.done.append((kind, flag, a, b, c, as_keys))
        return 1


def feed(records, shown, prompts=False):
    """_desk_in served `records`, the screen showing `shown`: what was put
    in, and what the service was told."""
    queue = list(records)
    put, told = StandIn(), []
    real_read, real_follow, real_input = \
        M._desk_read, M._desk_follow, M._DeskInput
    M._desk_read = lambda _pipe: queue.pop(0) if queue else None
    M._desk_follow = lambda _state, _shared: shown
    M._DeskInput = lambda: put
    M._desk_log = lambda _text: None   # (kept off: no desk.log beside this)
    shared = M._DeskShared(1)
    shared.answer_prompts = lambda: prompts
    try:
        M._desk_in(None, 0, shared, told.append)
    finally:
        M._desk_read, M._desk_follow, M._DeskInput = \
            real_read, real_follow, real_input
    return put.done, told


records = [(M.DESK_MOVE, 0, 0, 100, 200, 0),
           (M.DESK_BUTTON, 1, 1, 100, 200, 0),
           (M.DESK_KEY, 1, 0x41, 0x1E, 0, 0),
           (M.DESK_UNIT, 1, 0x4E2D, 0, 0, 0),
           (99, 0, 0, 0, 0, 0)]
done, told = feed(records, M.DESK_PERSON)
check("on the person's desktop each event is put in; an unknown kind is "
      "dropped", [d[0] for d in done] == [M.DESK_MOVE, M.DESK_BUTTON,
                                         M.DESK_KEY, M.DESK_UNIT], done)
check("…characters as themselves there (VK_PACKET)",
      not any(d[5] for d in done), done)
done, _told = feed(records, M.DESK_LOCKED)
check("on the lock screen too (its password is typed from afar), its "
      "characters as keys (run 45: its box took no VK_PACKET)",
      len(done) == 4 and all(d[5] for d in done), done)
done, _told = feed(records, M.DESK_PROMPT)
check("a permission prompt: only the pointer moves, unless the service "
      "allows more", [d[0] for d in done] == [M.DESK_MOVE], done)
done, _told = feed(records, M.DESK_PROMPT, prompts=True)
check("…and with Answer permission prompts on, it is answered",
      len(done) == 4, done)
done, told = feed([(M.DESK_SAS, 0, 0, 0, 0, 0)], M.DESK_PERSON)
check("Ctrl+Alt+Del is the service's to press (SendSAS)",
      done == [] and told == [b"SAS\n"], (done, told))


inputs = M._DeskInput()
sent = []
inputs._send = lambda *events: sent.append(events)
for record in ((M.DESK_MOVE, 0, 0, 70000, 5), (M.DESK_MOVE, 0, 0, -1, 5),
               (M.DESK_BUTTON, 1, 7, 10, 10), (M.DESK_WHEEL, 0, 0, 0, 99999),
               (M.DESK_KEY, 1, 0, 0x1E, 0), (M.DESK_KEY, 1, 0x41, 300, 0),
               (M.DESK_UNIT, 1, 0, 0, 0)):
    inputs.do(*record)
check("a record out of range is dropped (a position off the screen, a "
      "fourth button, a huge wheel, key 0, a scan code over 255)",
      sent == [], sent)
inputs.do(M.DESK_MOVE, 0, 0, 32768, 32768)
inputs.do(M.DESK_BUTTON, 0, 2, 1, 2)
inputs.do(M.DESK_KEY, 3, 0x2E, 0x53, 0)
inputs.do(M.DESK_UNIT, 1, 0x4E2D, 0, 0)
move, button, key, unit = sent
check("the pointer at the place asked, over the whole virtual desktop",
      move[0].u.mi.dx == 32768 and move[0].u.mi.dwFlags == 0xC001)
check("a button let go where it is (right: 0x0010)",
      button[1].u.mi.dwFlags == 0x0010 and button[0].u.mi.dx == 1)
check("a key with its scan code, extended where it says so, down",
      key[0].u.ki.wVk == 0x2E and key[0].u.ki.wScan == 0x53 and
      key[0].u.ki.dwFlags == 0x0001)
check("a character as Unicode (KEYEVENTF_UNICODE)",
      unit[0].u.ki.wScan == 0x4E2D and unit[0].u.ki.dwFlags == 0x0004)


class Layout:
    """user32's keyboard layout calls: 'A' is Shift and the A key (0x41,
    scan 0x1E); '@' is Ctrl+Alt and Q (as German keyboards); no key
    makes '中'."""
    keys = {"A": 0x0141, "@": 0x0651, "中": -1}

    def __getattr__(self, name):
        return {"GetForegroundWindow": lambda: 1,
                "GetWindowThreadProcessId": lambda _w, _p: 7,
                "GetKeyboardLayout": lambda _t: 0x0407,
                "VkKeyScanExW": lambda ch, _l: self.keys[ch],
                "MapVirtualKeyExW": lambda vk, _m, _l: {0x41: 0x1E,
                                                        0x51: 0x10}[vk]}[name]


real_dlls, M._DESK_DLLS = M._DESK_DLLS, (None, Layout(), None, None)
sent.clear()
inputs.do(M.DESK_UNIT, 1, ord("A"), 0, 0, as_keys=True)
inputs.do(M.DESK_UNIT, 0, ord("A"), 0, 0, as_keys=True)
press = [(k.u.ki.wVk, k.u.ki.dwFlags) for k in sent[0]] if sent else []
check("on the lock screen a character is its keys, Shift held: pressed "
      "and let go at its press, nothing at its letting go",
      len(sent) == 1 and press == [(0x10, 0), (0x41, 0), (0x41, 2),
                                   (0x10, 2)] and
      sent[0][1].u.ki.wScan == 0x1E, (press, len(sent)))
sent.clear()
inputs.do(M.DESK_UNIT, 1, ord("@"), 0, 0, as_keys=True)
check("…Ctrl and Alt where its layout makes it so (AltGr)",
      [k.u.ki.wVk for k in sent[0]] == [0x11, 0x12, 0x51, 0x51, 0x12, 0x11])
sent.clear()
inputs.do(M.DESK_UNIT, 1, ord("中"), 0, 0, as_keys=True)
check("…and one no key makes, as Unicode still",
      sent and sent[0][0].u.ki.dwFlags == 0x0004)
M._DESK_DLLS = real_dlls


# =============================================================================
print("\nThe screen's state, as the side controlling is told it")
W = M.desk_screen_word
check("the lock screen: locked (usable: its password typed from afar)",
      W(M.DESK_LOCKED, False, lambda: None) == "locked")
check("a permission prompt: answered only where the service allows",
      W(M.DESK_PROMPT, True, lambda: None) == "prompt" and
      W(M.DESK_PROMPT, False, lambda: None) == "prompt-view")
check("nothing readable: secure; the person's: ok, or hung",
      W(M.DESK_UNSEEN, False, lambda: None) == "secure" and
      W(M.DESK_PERSON, False, lambda: None) == "ok" and
      W(M.DESK_PERSON, False, lambda: {"w": 1}) == "hung")
words = {s: M.remote_note_words({"s": s, "hp": {"x": "notepad.exe"}},
                                "Mina") for s in M.REMOTE_SCREENS}
check("each state says what to do, by name (NEXT29: a program that does "
      "not answer, not 'run as administrator')",
      "notepad.exe does not answer" in words["hung"] and
      "locked" in words["locked"] and "answer it" in words["prompt"] and
      "does not let a session answer" in words["prompt-view"], words)
state = M.sanitize_remote_state({"s": "hung", "sv": True,
                                 "hp": {"w": 77, "p": 1234, "x": "app.exe",
                                        "t": "Untitled"}})
check("the state word's new fields, checked: the service in use, the "
      "program that does not answer",
      state["s"] == "hung" and state["sv"] is True and
      state["hp"]["w"] == 77 and state["hp"]["p"] == 1234 and
      state["hp"]["x"] == "app.exe")
check("…and from a computer that sends nonsense, nothing",
      M.sanitize_remote_state({"sv": "yes", "hp": {"w": "x"}})["sv"] is False
      and M.sanitize_remote_state({"hp": [1, 2]})["hp"] is None)


# =============================================================================
print("\nThe person's side, against a stand-in helper")


class Link:
    """A DeskLink that answers as the helper would."""

    def __init__(self, answers=None):
        self.sent, self.answers, self.closed = [], answers or {}, 0
        self.pipe = None

    def ask(self, kind, flag=0, a=0, b=0, c=0, d=0, until=0):
        self.sent.append((kind, flag, a, b, c))
        if kind in self.answers:
            return self.answers[kind]() if callable(self.answers[kind]) \
                else self.answers[kind]
        return [] if not until else None

    def close(self):
        self.closed += 1


real_windows = M.IS_WINDOWS
M.IS_WINDOWS = True
client = M.DeskClient()
client.screen = Link({M.DESK_HELLO: [(M.DESK_HELLO, 0, M.DESK_VERSION, 1, 4,
                                      0)]})
client.input = Link()
check("the helper answers, of this version: in use (one user)",
      client.acquire() and client.users == 1)
check("a second user does not say hello again",
      client.acquire() and client.users == 2 and
      [s[0] for s in client.screen.sent].count(M.DESK_HELLO) == 1)
client.release()
check("…the pipes stay open while one is left",
      client.screen.closed == 0)
client.release()
check("…and close with the last (the bar on the lock screen goes)",
      client.screen.closed == 1 and client.input.closed == 1)
other = M.DeskClient()
other.screen = Link({M.DESK_HELLO: [(M.DESK_HELLO, 0, M.DESK_VERSION + 1,
                                     1, 4, 0)]})
check("a helper of another version is not used, nor asked again at once",
      not other.acquire() and not other.acquire() and
      [s[0] for s in other.screen.sent].count(M.DESK_HELLO) == 1)
other.forget()
check("…until the service was just set up",
      not other.acquire() and
      [s[0] for s in other.screen.sent].count(M.DESK_HELLO) == 2)
M.IS_WINDOWS = real_windows

print("\nCtrl+Alt+Del and the keys, through the helper")
fake = M.DeskClient()
fake.screen, fake.input = Link(), Link()
fake.acquire = lambda: True
fake.release = lambda: None
real_desk = M.DESK
M.DESK = fake


class User32:
    @staticmethod
    def MapVirtualKeyW(vk, _kind):
        return {0x11: 0x1D, 0x12: 0x38, 0x2E: 0x53, 0x41: 0x1E}.get(vk, 0)

    @staticmethod
    def SendInput(n, _array, _size):
        sendinput.append(n)
        return n


sendinput = []
inj = M.HelperInjector(user32=User32())
inj.key("ctrl", True)
inj.key("alt", True)
inj.key("delete", True)
inj.key("delete", False)
inj.key("alt", False)
inj.key("ctrl", False)
kinds = [s[0] for s in fake.input.sent]
check("Ctrl+Alt+Del held from afar is Windows' own (DESK_SAS), not the "
      "Delete key", kinds == [M.DESK_KEY, M.DESK_KEY, M.DESK_SAS, M.DESK_KEY,
                              M.DESK_KEY], fake.input.sent)
fake.input.sent.clear()
inj.key("a", True)
inj.text("中é")
check("a key with its scan code; characters as their UTF-16 units, down "
      "and up", fake.input.sent[0] == (M.DESK_KEY, 1, 0x41, 0x1E, 0) and
      [s[2] for s in fake.input.sent[1:]] == [0x4E2D, 0x4E2D, 0xE9, 0xE9])
fake.input.ask = lambda *a, **k: None          # the helper went away
inj.move(1, 2)
check("an event the helper did not take is put in as before (SendInput)",
      sendinput == [1], sendinput)
inj.close()
M.DESK = real_desk


# =============================================================================
print("\nSettings")


class Reg:
    HKEY_LOCAL_MACHINE = 0

    def __init__(self, values):
        self.values = values

    def OpenKey(self, _root, path):
        if self.values is None:
            raise OSError("not there")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def QueryValueEx(self, _key, name):
        if name not in self.values:
            raise OSError(name)
        return self.values[name], 1


check("not set up: no service state",
      M.service_state(Reg(None)) is None)
check("set up: its version, and permission prompts off unless turned on",
      M.service_state(Reg({"Version": "21.0.0"})) ==
      {"version": "21.0.0", "prompts": False} and
      M.service_state(Reg({"Version": "21.0.0", "AnswerPrompts": 1}))[
          "prompts"] is True)
if sys.platform != "win32":
    check("not on Windows: Settings says so, and nothing is locked at a "
          "sign-in", M.service_why_not() and not M.lock_after_sign_in_due())
real_exe, M.built_exe = M.built_exe, lambda: None
real_windows, M.IS_WINDOWS = M.IS_WINDOWS, True
check("the Python file does not set it up: it says why (its folders can be "
      "changed by the person's own programs)",
      "EXE" in M.service_why_not())
M.built_exe = lambda: "C:\\Users\\me\\Downloads\\Ferritext.exe"
check("the EXE does", M.service_why_not() == "")
check("the row: off, set up for another version, on",
      M.service_words(None).startswith("Off") and
      "update it" in M.service_words({"version": "20.0.0"}) and
      M.service_words({"version": M.APP_VERSION}).startswith("On"))
M.built_exe, M.IS_WINDOWS = real_exe, real_windows
if sys.platform == "win32":
    # As in the EXE: Nuitka's sys.executable is a python.exe beside the
    # program that is not there (build run 168).
    real_python, sys.executable = sys.executable, \
        str(Path(tempfile.gettempdir()) / "nowhere" / "python.exe")
    M.__dict__["__compiled__"] = True
    try:
        named = [M.program_file(), *M._service_command()]
    finally:
        sys.executable = real_python
        del M.__dict__["__compiled__"]
    check("the EXE names its own program file to copy and to start the "
          "helper with, not the python.exe Nuitka says it is",
          all(os.path.isfile(path) for path in named), named)
if sys.platform == "win32" and os.environ.get("GITHUB_ACTIONS") == "true":
    # A step of Settings', in a process of its own as Settings runs it,
    # without the runner's Direct3D setting: as on a person's computer, or
    # on one without OpenGL 2.
    outcome = Path(tempfile.mkdtemp(prefix="fext-set-")) / "result.json"
    plain = {k: v for k, v in os.environ.items() if not k.startswith("KIVY_")}
    try:
        subprocess.run([sys.executable, MAIN_PY, M.SERVICE_SET,
                        str(outcome), "0"], env=plain, timeout=120)
        ended = True
    except subprocess.TimeoutExpired:
        ended = False
    check("a step of Settings' ends by itself and says how it went, with no "
          "window (it opened one, and without OpenGL 2 waited on Kivy's "
          "error box: build run 168)", ended and outcome.exists(),
          "did not end in 2 minutes" if not ended else "no outcome")


# =============================================================================
print("\nThe service itself, on Windows")
if sys.platform != "win32":
    print("  (Windows only: the Devices workflow's Windows job runs this "
          "part)")
elif os.environ.get("GITHUB_ACTIONS") != "true":
    print("  (only on GitHub's Windows runner: it installs a service, sets "
          "the account's password and locks the screen)")
elif not ctypes.windll.shell32.IsUserAnAdmin():
    check("the runner's account is an administrator", False)
else:
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    mine, console = M._desk_session(), kernel32.WTSGetActiveConsoleSessionId()
    print(f"  this session {mine}, the screen's {console}", flush=True)
    log_file = Path(MAIN_PY).parent / "desk.log"

    def show_log():
        if log_file.exists():
            print(f"{NOTE}desk.log:\n" + "\n".join(
                NOTE + line for line in
                log_file.read_text("utf-8", "replace").splitlines()[-30:]),
                flush=True)

    def shot():
        """(width, height, pixels) of the screen, through the helper."""
        got = M.DESK.grab(True)
        return (got[1], got[2], ctypes.string_at(
            got[3], got[1] * got[2] * 4)) if got else None

    def blank(seen) -> bool:
        """The screen below the bar is one colour (a 16x12 grid of it)."""
        if not seen:
            return True
        width, height, pixels = seen
        return len({pixels[(y * width + x) * 4:(y * width + x) * 4 + 3]
                    for x in range(0, width, max(1, width // 16))
                    for y in range(40, height, max(1, height // 12))}) <= 2

    def picture(name, seen):
        """`seen` (shot()) as a small grey PNG in the job's log."""
        if not seen:
            print(f"{NOTE}picture {name}: none (the helper read nothing)")
            return
        from PIL import Image
        width, height, pixels = seen
        grey = Image.frombuffer("RGBA", (width, height), pixels, "raw",
                                "BGRA", 0, 1).convert("L")
        for scale in (2, 3, 4, 6):         # half size, if that is not long
            small = grey.resize((width // scale, height // scale))
            out = io.BytesIO()
            small.quantize(16).save(out, "PNG", optimize=True)
            text = base64.b64encode(out.getvalue()).decode()
            if len(text) <= 24000:
                break
        print(f"{NOTE}picture {name} ({small.width}x{small.height}):")
        for at in range(0, len(text), 280):
            print(NOTE + text[at:at + 280], flush=True)

    why = M.service_install(command=[sys.executable, MAIN_PY])
    check("installed and started (LocalSystem, starts with Windows)",
          why == "", why)
    check("its helper answers on this session, from a pipe SYSTEM owns",
          wait(M.DESK.available, 30))
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, M.SAS_POLICY) as key:
            sas = winreg.QueryValueEx(key, "SoftwareSASGeneration")[0]
    except OSError:
        sas = None
    check("…and Windows lets a service press Ctrl+Alt+Del", sas in (1, 3),
          sas)
    if not M.DESK.acquire():
        show_log()
    else:
        if (M.DESK.state() or (None,))[0] == M.DESK_LOCKED:
            print(f"{NOTE}the screen is locked as this run begins: the run "
                  "before did not unlock it", flush=True)
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        def pointer():
            at = POINT()
            user32.GetCursorPos(ctypes.byref(at))
            return at.x, at.y

        got = M.DESK.grab(True)
        width, height = user32.GetSystemMetrics(78), user32.GetSystemMetrics(
            79)
        check("the whole screen read through the helper",
              got is not None and got[1:3] == (width, height) and
              got[0] == M.DESK_PERSON, (got, width, height))
        if got:
            pixels = ctypes.string_at(got[3], width * height * 4)
            check("…a picture, not a black one",
                  any(pixels[i] for i in range(0, len(pixels), 4099)))
        check("what it shows: the person's desktop; prompts off",
              M.DESK.state() == (M.DESK_PERSON, False), M.DESK.state())
        left = user32.GetSystemMetrics(76)
        top = user32.GetSystemMetrics(77)
        M.DESK.send(M.DESK_MOVE, 0, 0, 16384, 16384)
        time.sleep(0.3)
        x, y = pointer()
        check("the pointer moved by the helper, where it was sent",
              abs(x - (left + width // 4)) <= 2 and
              abs(y - (top + height // 4)) <= 2, (x, y))

        # A program that does not answer, in front: its window made and put
        # in front (Windows lets a program that pressed Alt last), its
        # messages read for a second as any program's are (until it has
        # waited for them once it is "starting", never "not answering";
        # its keys are not handed on, or Alt would open its menu), then
        # left unread.
        hang = subprocess.Popen([sys.executable, "-c", (
            "import ctypes, time\n"
            "from ctypes import wintypes\n"
            "u = ctypes.WinDLL('user32')\n"
            "u.CreateWindowExW.restype = ctypes.c_void_p\n"
            "h = ctypes.c_void_p(u.CreateWindowExW(0, 'STATIC', "
            "'Not answering', 0x10CF0000, 200, 200, 500, 300, None, None, "
            "None, None))\n"
            "u.ShowWindow(h, 5)\n"
            "u.keybd_event(0x12, 0, 0, 0)\n"
            "u.SetForegroundWindow(h)\n"
            "u.keybd_event(0x12, 0, 2, 0)\n"
            "u.SetTimer(None, 0, 1000, None)\n"
            "m = wintypes.MSG()\n"
            "while u.GetMessageW(ctypes.byref(m), None, 0, 0) > 0 and "
            "m.message != 0x0113:\n"
            "    if not 0x0100 <= m.message <= 0x0109:\n"
            "        u.DispatchMessageW(ctypes.byref(m))\n"
            "time.sleep(120)\n")])
        seen = wait(lambda: (M.windows_hung_front() or {}).get("p") ==
                    hang.pid, 30)
        hung = M.windows_hung_front()
        check("a program that does not answer, in front, is found as such",
              seen and hung is not None and hung["p"] == hang.pid,
              (hung, hang.pid))
        if not seen:
            front = user32.GetForegroundWindow()
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(front, ctypes.byref(pid))
            print(f"{NOTE}in front: window {front}, process {pid.value} "
                  f"({M._process_name(pid.value, kernel32)}); this test is "
                  f"{os.getpid()}", flush=True)
        check("…and the state says so (20.0 said 'run as administrator')",
              M.windows_screen_state() == "hung")
        M.DESK.send(M.DESK_MOVE, 0, 0, 40000, 40000)
        time.sleep(0.3)
        x, y = pointer()
        check("the helper's pointer still moves with it in front",
              abs(x - (left + width * 40000 // 65535)) <= 3, (x, y))
        if hung and hung["w"]:
            done, why = M.windows_close_program(hung["w"], hung["p"])
            check("End it: the program is ended", done and wait(
                lambda: hang.poll() is not None, 10), why)
        hang.kill()

        # The lock screen, last: if it stays locked, nothing runs after it.
        password = "Fx" + secrets.token_hex(8) + "!9a"
        user = os.environ.get("USERNAME", "")
        set_ok = subprocess.run(["net", "user", user, password],
                                capture_output=True).returncode == 0
        check("the runner's account given a password (to unlock with)",
              set_ok)
        got = M.DESK.grab(True)
        before = ctypes.string_at(got[3], got[1] * got[2] * 4) if got \
            else b""
        user32.LockWorkStation()
        locked = wait(lambda: (M.DESK.state() or (None,))[0] ==
                      M.DESK_LOCKED, 20)
        check("locked: the helper follows Windows onto the lock screen",
              locked, M.DESK.state())
        time.sleep(8.0)                  # its picture slides in, settles
        got = M.DESK.grab(True)
        if got:
            pixels = ctypes.string_at(got[3], got[1] * got[2] * 4)
            check("…and reads it (not black, not the desktop)",
                  pixels != before and
                  any(pixels[i] for i in range(0, len(pixels), 4099)))
            row = got[1] * 4
            red = [pixels[15 * row + x * 4:15 * row + x * 4 + 3]
                   for x in range(0, got[1], max(1, got[1] // 50))]
            check("…with the red bar across its top (a session is on)",
                  sum(1 for b, g, r in red if r > 150 and g < 90 and b < 90)
                  > len(red) // 2, red[:5])
        locked_seen = shot()
        M.DESK.send(M.DESK_SAS)                    # Ctrl+Alt+Del
        # The password's box comes up: the screen changes, then settles.
        wait(lambda: shot() != locked_seen, 15)
        seen_after, since = [], time.monotonic()
        for at in (1.0, 4.0, 10.0):             # pictures, for a failure
            time.sleep(max(0.0, at - (time.monotonic() - since)))
            seen_after.append((f"{at:.0f} s after Ctrl+Alt+Del", shot()))
        M.DESK.send(M.DESK_BUTTON, 1, 1, 32768, 40000)   # the box, clicked
        M.DESK.send(M.DESK_BUTTON, 0, 1, 32768, 40000)   # (under the name)
        time.sleep(1.5)
        seen_after.append(("its password box clicked", shot()))
        check("Ctrl+Alt+Del from afar: the lock screen gives way to the "
              "sign-in screen", seen_after[0][1] != locked_seen)
        for char in password:
            M.DESK.send(M.DESK_UNIT, 1, ord(char))
            M.DESK.send(M.DESK_UNIT, 0, ord(char))
        M.DESK.send(M.DESK_KEY, 1, 0x0D, 0x1C)
        M.DESK.send(M.DESK_KEY, 0, 0x0D, 0x1C)
        unlocked = wait(lambda: (M.DESK.state() or (None,))[0] ==
                        M.DESK_PERSON, 30)
        if not unlocked and blank(seen_after[-1][1]):
            # GitHub's Windows (runs 46 to 50): the sign-in screen comes up
            # blank, its window in front drawing one colour even of itself
            # (desk.log), so it has no box to type into. Said, not counted:
            # the unlock is checked on a real PC.
            print(f"{NOTE}not checked here: the sign-in screen came up blank "
                  "on this machine (no password box to type into)",
                  flush=True)
        else:
            check("Ctrl+Alt+Del, the password typed from afar: unlocked",
                  unlocked, M.DESK.state())
        if not unlocked:
            picture("locked", locked_seen)
            for name, seen in seen_after:
                picture(name, seen)
            picture("after the password", shot())
        M.DESK.release()
    show_log()
    why = M.service_remove()
    check("removed: the service gone, nothing of it left",
          why == "" and M.service_state() is None, why)

passed = sum(1 for _n, ok in RESULTS if ok)
print(f"\n  {passed}/{len(RESULTS)} passed")
sys.exit(0 if passed == len(RESULTS) else 1)
