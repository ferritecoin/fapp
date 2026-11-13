#!/usr/bin/env python3
"""The composer, driven the way a person drives it.

    xvfb-run -a python3 desktop/test_composer.py main.py

  * a real mouse click — through Kivy's mouse provider, as on a computer —
    on the message field puts the cursor in it, anywhere on the pill (in
    9.1.0 the closed emoji panel lay over it and took every click);
  * no hidden part (emoji panel, reply preview, reaction strip, jump
    button) takes a touch meant for what lies beneath it;
  * nothing written: your initial, the camera and the microphone; writing:
    they fold away and the microphone becomes the send button;
  * a recording from a capture command becomes a WAV voice note with its
    length and waveform, sent as audio/wav; a silent microphone is said so;
  * a webcam photo arrives the right way up, and is deleted if not sent;
    with no camera, a sheet says so and offers a photo instead;
  * icons are drawn smooth, and every icon the app names exists;
  * the window carries the Ferritext icon.
"""
import importlib.util
import os
import sys
import tempfile
import threading
import time
import wave

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
if sys.platform.startswith("linux"):     # xvfb-run's X display
    os.environ.setdefault("SDL_VIDEODRIVER", "x11")
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-composer-")
os.environ["USERPROFILE"] = os.environ["HOME"]   # Path.home() on Windows
os.environ["FEXT_LANG"] = "en"          # the English the checks read
os.environ["FEXT_SERVER_HOST"] = "127.0.0.1:1"
os.environ.pop("FEXT_THEME", None)

CLIENT = sys.argv[1] if len(sys.argv) > 1 else "main.py"
_spec = importlib.util.spec_from_file_location("fext_composer", CLIENT)
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_composer"] = M
_spec.loader.exec_module(M)

from kivy.clock import Clock                                  # noqa: E402
from kivy.core.window import Window                           # noqa: E402
from kivy.graphics.texture import Texture                     # noqa: E402
from kivy.metrics import dp                                   # noqa: E402
from kivy.uix.image import Image as KivyImage                 # noqa: E402
from kivy.uix.modalview import ModalView                      # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f" — {detail}" if detail and not cond else ""))


# ---- a person with one contact and a short chat --------------------------------
keys = M.IdentityManager()
priv, _p, _a, _n = M.mine_identity()
ME = keys.add_identity(priv, "Personal")
keys.activate(ME)
keys.set_username("writer")
store = M.LocalStore(keys.store_path())
pk, _pp, _ad, _nn = M.mine_identity()
PEER = M.address_from_priv(pk)
store.upsert_contact(PEER, "reader", M.pub_to_compressed(M.priv_to_pub(pk)).hex(),
                     listed=True)
for i in range(3):
    store.add_in(PEER, f"message {i}", M.now_iso(), i + 1, f"{i:032x}",
                 unread=False)
store.close()

M.FextApp._start_worker = lambda self: setattr(self, "worker", None)
M.SplashOverlay.HOLD = 0.2
app = M.FextApp()
SENT = {}
OPENED = []
REAL_CHOOSER = M.ImageChooserModal       # click_attach stands in for it

# A capture command that "records" a 440 Hz tone in real time, and one that
# finds no microphone and exits at once.
TONE = ("import math,struct,sys,time\n"
        "out=sys.stdout.buffer\n"
        "for n in range(100):\n"
        "    out.write(b''.join(struct.pack('<h',int(9000*math.sin("
        "2*math.pi*440*(n*1600+i)/16000))) for i in range(1600)))\n"
        "    out.flush(); time.sleep(0.1)\n")
SILENT = "import sys; sys.exit(1)"


def capture_with(code):
    return lambda: [lambda feed: M._ProcessCapture(
        feed, [sys.executable, "-c", code])]


def click(x, y, then=None):
    """A left click at window position (x, y), as the mouse provider sees
    one: pressed, held for a moment, released."""
    h = Window.system_size[1]
    Window.dispatch("on_mouse_down", x, h - y, "left", [])
    Clock.schedule_once(
        lambda _dt: Window.dispatch("on_mouse_up", x, h - y, "left", []), 0.06)
    if then is not None:
        Clock.schedule_once(lambda _dt: then(), 0.35)


def centre(w):
    return w.to_window(w.x + w.width / 2, w.y + w.height / 2)


def sheets(kind):
    return [v for v in Window.children if isinstance(v, kind)]


def walk(w):
    for c in w.children:
        yield c
        yield from walk(c)


def touch_eaters(root):
    """Disabled widgets that are not on screen (opacity 0 or a 0-high
    ancestor) and not stood aside (Concealable): Kivy lets such a widget
    swallow every touch that lands on it."""
    found = []
    for w in walk(root):
        if not w.disabled or w.width <= 1 or w.height <= 1:
            continue
        hidden, aside, a = False, False, w
        while a is not None and a is not Window:
            if getattr(a, "concealed", False):
                aside = True
            if getattr(a, "opacity", 1) == 0 or getattr(a, "height", 1) <= 0:
                hidden = True
            a = a.parent
        if hidden and not aside:
            found.append(type(w).__name__)
    return found


STEPS = []


def step(fn, delay=0.4):
    """A step, then `delay` seconds. A step that returns False is not
    ready yet: it runs again every 0.2 s, for 10 s at the most, and then
    counts as a failed check."""
    STEPS.append((fn, delay))


def run(i=0, tries=0):
    if i >= len(STEPS):
        finish()
        return
    fn, delay = STEPS[i]
    try:
        ready = fn() is not False
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check(f"step {i} ({getattr(fn, '__name__', '?')}) ran", False,
              f"{type(exc).__name__}: {exc}")
        ready = True
    if not ready and tries < 50:
        Clock.schedule_once(lambda _dt: run(i, tries + 1), 0.2)
        return
    if not ready:                   # its checks never ran: say so
        check(f"step {i} ({getattr(fn, '__name__', '?')}) was ready in "
              "10 s", False)
    Clock.schedule_once(lambda _dt: run(i + 1), delay)


def screen():
    return app.conversation_screen


# ---- the steps -----------------------------------------------------------------
def open_chat():
    print("\nClicks")
    M.ConversationScreen._can_record = True
    app.api._features = {"blobs": True}
    app.open_conversation(PEER)


step(open_chat, 1.0)


def click_text():
    s = screen()
    s.entry.input.focus = False
    x, y = centre(s.entry.input)
    click(x, y, lambda: check(
        "a click on the message field puts the cursor in it",
        s.entry.input.focus, f"at {x:.0f},{y:.0f}"))


step(click_text, 0.6)


def click_pill_edge():
    s = screen()
    s.entry.input.focus = False
    ti = s.entry.input
    x, _y = ti.to_window(ti.x + dp(30), ti.y)
    _x, top = s.entry.to_window(0, s.entry.top - dp(5))
    click(x, top, lambda: check(
        "...and so does one near the pill's edge, outside the line of text",
        ti.focus))


step(click_pill_edge, 0.6)


def centred(ti):
    return abs(ti.padding[1] - (ti.height - ti.line_height) / 2) < 0.01


def type_other_scripts():
    ti = screen().entry.input
    ti.text = "أراك غدا الساعة 10:30"
    Clock.schedule_once(lambda _dt: check(
        "a line in another script's face is centred in the field (15.1)",
        ti.font_name == "FerritextWorld" and centred(ti),
        f"{ti.font_name} {ti.padding} {ti.line_height}"), 0.3)
    Clock.schedule_once(lambda _dt: setattr(ti, "text", ""), 0.4)
    Clock.schedule_once(lambda _dt: check(
        "...and the hint, back in the theme's, when it is cleared",
        ti.font_name == "Roboto" and centred(ti),
        f"{ti.font_name} {ti.padding} {ti.line_height}"), 0.7)


step(type_other_scripts, 1.0)


def no_eaters():
    eaters = touch_eaters(screen())
    check("no hidden part of the conversation can swallow a touch",
          not eaters, str(sorted(set(eaters))))


step(no_eaters, 0.2)


def click_emoji():
    x, y = centre(screen().emoji_button)
    click(x, y, lambda: check("a click on the emoji key opens the panel",
                              screen().emoji_panel.is_open))


step(click_emoji, 0.6)


def click_emoji_again():
    x, y = centre(screen().emoji_button)
    click(x, y, lambda: check("...and another closes it again",
                              not screen().emoji_panel.is_open))


step(click_emoji_again, 0.6)


def click_attach():
    class Chooser:
        def __init__(self, callback):
            OPENED.append(("chooser", callback))

        def open(self):
            pass
    M.ImageChooserModal = Chooser
    x, y = centre(screen().attach_button)
    click(x, y, lambda: check("a click on the paperclip offers a photo",
                              OPENED and OPENED[-1][0] == "chooser"))


step(click_attach, 0.6)


def click_camera_without_one():
    M.desktop_camera_available = lambda: False
    x, y = centre(screen().camera_button)
    click(x, y, lambda: check(
        "with no camera, the camera key says so and offers a photo instead",
        sheets(M.NoCameraSheet)))


step(click_camera_without_one, 0.6)


def close_sheets():
    for v in sheets(ModalView):
        v.dismiss(animation=False)


step(close_sheets, 0.4)


def states_empty():
    print("\nThe composer's two states")
    s = screen()
    s.entry.text = ""
    s._sync_composer(animate=False)
    check("nothing written: your initial, the camera and a microphone",
          s.me_holder.width == M.TOUCH and s.me_holder.opacity == 1 and
          s.camera_button.width == s.entry.KEY and
          s.action_button.icon == "mic" and
          s.action_button.label == "Record a voice message")
    me = s.me_holder.children[0] if s.me_holder.children else None
    check("...the initial is the identity you are writing as",
          me is not None and me.label == "Writing as @writer")


step(states_empty, 0.2)


def states_typing():
    s = screen()
    s.entry.input.focus = True
    s.entry.input.insert_text("hello")


step(states_typing, 0.6)


def states_typing_check():
    s = screen()
    check("writing: they fold away, and the microphone becomes send",
          s.me_holder.width == 0 and s.camera_button.width == 0 and
          s.action_button.icon == "send" and
          s.action_button.icon_scale == 1.0 and
          s.action_button.label == "Send" and
          s.attach_button.width == s.entry.KEY,
          f"me={s.me_holder.width} cam={s.camera_button.width} "
          f"icon={s.action_button.icon}")
    s.entry.text = ""


step(states_typing_check, 0.6)


def states_back():
    s = screen()
    check("cleared, the composer is as it was",
          s.me_holder.width == M.TOUCH and s.camera_button.width > 0 and
          s.action_button.icon == "mic")
    M.ConversationScreen._can_record = False
    s._sync_composer(animate=False)
    check("where nothing can record, the button stays send",
          s.action_button.icon == "send")
    M.ConversationScreen._can_record = True
    s._sync_composer(animate=False)


step(states_back, 0.3)


def record_start():
    print("\nVoice notes on a computer")
    M._capture_routes = capture_with(TONE)
    app.send_voice = lambda path, dur, wave_b64: SENT.update(
        voice=(path, dur, wave_b64))
    x, y = centre(screen().action_button)
    click(x, y)


step(record_start, 1.6)


def record_running():
    s = screen()
    check("a click on the microphone starts recording",
          s.recorder is not None and s.rec_send.parent is s.composer,
          f"recorder={s.recorder}")
    check("...showing the time and the microphone's level",
          s.rec_label.text.startswith("0:0") and
          len(s.rec_levels._levels) >= 5)
    x, y = centre(s.rec_send)
    click(x, y)


step(record_running, 0.8)


def record_sent():
    s = screen()
    got = SENT.get("voice")
    ok = got is not None and got[0].endswith(".wav") and \
        1000 <= got[1] <= 4000 and len(M.b64d(got[2])) == 48
    check("send makes it a WAV voice note with its length and waveform",
          ok, str(got and (got[0][-12:], got[1])))
    if got:
        with wave.open(got[0], "rb") as w:
            check("...16 kHz mono 16-bit, as long as it says",
                  (w.getframerate(), w.getnchannels(), w.getsampwidth()) ==
                  (16000, 1, 2) and
                  abs(w.getnframes() / 16 - got[1]) < 30)
        captured = {}
        app._send_media = lambda *a: captured.update(args=a)
        type(app).send_voice(app, got[0], got[1], got[2])
        deadline = time.time() + 3
        while "args" not in captured and time.time() < deadline:
            time.sleep(0.05)
        args = captured.get("args") or ()
        check("...sent as audio/wav, kept as .wav",
              len(args) > 7 and args[3] == "audio/wav" and args[7] == "wav")
    check("the composer is back after sending",
          s.recorder is None and s.entry.parent is s.composer)


step(record_sent, 0.5)


def record_silent():
    M._capture_routes = capture_with(SILENT)
    app._pending_status = ("", None)
    screen().start_recording()


step(record_silent, 1.5)


def record_silent_check():
    s = screen()
    check("a microphone that gives nothing is said so, and the composer "
          "comes back", s.recorder is None and s.entry.parent is s.composer
          and "microphone" in (app._pending_status[0] or "").lower(),
          str(app._pending_status))
    check("media files: a computer's note keeps .wav, a phone's .m4a",
          M.media_suffix("voice", "audio/wav") == "wav" and
          M.media_suffix("voice", "audio/mp4") == "m4a" and
          M.media_suffix("voice", None) == "m4a" and
          M.media_suffix("image", "image/jpeg") == "jpg")


def press(w):
    """Mouse down on a widget, kept down: a hold (see release, drag)."""
    x, y = centre(w)
    h = Window.system_size[1]
    Window.dispatch("on_mouse_down", x, h - y, "left", [])
    HOLD.update(x=x, y=y)


def drag(dx, dy, steps=6):
    h = Window.system_size[1]
    for i in range(1, steps + 1):
        x, y = HOLD["x"] + dx * i / steps, HOLD["y"] + dy * i / steps
        Window.dispatch("on_mouse_move", x, h - y, [])
    HOLD.update(x=HOLD["x"] + dx, y=HOLD["y"] + dy)


def release():
    h = Window.system_size[1]
    Window.dispatch("on_mouse_up", HOLD["x"], h - HOLD["y"], "left", [])


HOLD = {}


def hold_start():
    print("\nHold to record")
    M._capture_routes = capture_with(TONE)
    SENT.clear()
    press(screen().action_button)


def hold_heard():
    """Held until there is a second of sound, not for a fixed time: the
    tone's process starts late on a slow computer (run 7, Windows)."""
    s = screen()
    return s.recorder is None or s.recorder.elapsed_ms >= 1000


def hold_release():
    s = screen()
    check("holding the microphone records, red under the finger, with "
          "the lock over it", s.recorder is not None and not s._rec_locked
          and s.action_button._holding and s.lock_pill.parent is not None)
    release()


def hold_sent():
    s = screen()
    got = SENT.get("voice")
    check("...letting go sends it", got is not None and got[1] >= 700 and
          s.recorder is None, str(got and got[1]))
    SENT.clear()
    press(s.action_button)


def hold_cancel():
    drag(-(M.ConversationScreen.CANCEL_SLIDE + dp(20)), 0)


def hold_cancelled():
    s = screen()
    check("sliding left deletes it: nothing sent, the composer back",
          s.recorder is None and not SENT.get("voice") and
          s.entry.parent is s.composer)
    press(s.action_button)


def hold_lock():
    drag(0, M.ConversationScreen.LOCK_SLIDE + dp(20))


def hold_locked():
    s = screen()
    check("sliding up locks it: hands-free, with delete, pause and send",
          s.recorder is not None and s._rec_locked and
          s.rec_cancel.parent is s.composer and
          s.rec_pause.parent is s.composer and
          s.rec_send.parent is s.composer)
    release()
    check("...and letting go then sends nothing", s.recorder is not None
          and not SENT.get("voice"))
    s.toggle_pause()
    HOLD["paused_at"] = s.recorder.elapsed_ms


def hold_paused():
    s = screen()
    check("paused, the clock holds still",
          abs(s.recorder.elapsed_ms - HOLD["paused_at"]) < 150 and
          s.rec_pause.icon == "mic", str((s.recorder.elapsed_ms,
                                          HOLD["paused_at"])))
    s.toggle_pause()


def hold_resumed():
    s = screen()
    check("...and carries on once resumed",
          s.recorder.elapsed_ms > HOLD["paused_at"] + 300)
    x, y = centre(s.rec_send)
    click(x, y)


def hold_locked_sent():
    got = SENT.get("voice")
    check("...send sends it, without the paused part",
          got is not None and got[1] < HOLD["paused_at"] + 2000)


step(record_silent_check, 0.2)
step(hold_start, 0.2)
step(hold_heard, 0.1)
step(hold_release, 0.6)
step(hold_sent, 0.9)
step(hold_cancel, 0.5)
step(hold_cancelled, 0.4)
step(hold_lock, 0.5)
step(hold_locked, 0.8)
step(hold_paused, 0.8)
step(hold_resumed, 0.6)
step(hold_locked_sent, 0.4)


def call_hold_start():
    print("\nA call while recording (18.1)")
    SENT.clear()
    press(screen().action_button)


def call_while_held():
    s = screen()
    if s.recorder is None or s.recorder.elapsed_ms < 1000:
        return False                 # a second of it (0.7 s is the least)
    HOLD["rec"] = s.recorder
    s.app._call_begins()
    check("a call that begins while a voice message is held down pauses "
          "it, hands-free, with delete and send: nothing is sent",
          s.recorder is HOLD["rec"] and s._rec_locked and
          s.recorder.paused and s.rec_send.parent is s.composer and
          s.rec_pause.icon == "mic" and not SENT.get("voice"))
    release()
    HOLD["paused_at"] = s.recorder.elapsed_ms


def call_kept():
    s = screen()
    check("...lifting the finger sends nothing, and its clock holds "
          "still: it is never sent by itself at its limit",
          s.recorder is HOLD["rec"] and not SENT.get("voice") and
          s.recorder.elapsed_ms == HOLD["paused_at"],
          str((s.recorder and s.recorder.elapsed_ms, HOLD["paused_at"])))
    x, y = centre(s.rec_send)
    click(x, y)


def call_kept_sent():
    got = SENT.get("voice")
    check("...after the call, Send sends what was recorded before it",
          got is not None and 1000 <= got[1] <= HOLD["paused_at"] + 100,
          str(got and got[1]))
    SENT.clear()
    app._pending_status = ("", None)
    screen().start_recording()               # hands-free
    screen().recorder.can_pause = False      # as Android 6's recorder


def call_no_pause():
    s = screen()
    s.app._call_begins()
    check("...where a recording cannot pause (Android 6) it is deleted, "
          "and that is said: nothing goes out by itself",
          s.recorder is None and not SENT.get("voice") and
          s.entry.parent is s.composer and
          app._pending_status[0] == "Recording deleted: a call began",
          str(app._pending_status))


step(call_hold_start, 0.2)
step(call_while_held, 0.6)
step(call_kept, 0.6)
step(call_kept_sent, 0.6)
step(call_no_pause, 0.3)


class FakeCameraView(KivyImage):
    """Stands in for kivy.uix.camera.Camera the way OpenCV feeds it: the
    frame's top row uploaded first, the texture flipped for display."""

    def __init__(self, play=True, resolution=(64, 32), **kw):
        kw.pop("index", None)
        super().__init__(**kw)
        w, h = 64, 32
        rows = []
        for y in range(h):              # top half red on the left, rest blue
            rows.append(b"".join((b"\xff\x00\x00" if (y < h // 2 and x < w // 2)
                                  else b"\x00\x00\xff") for x in range(w)))
        tex = Texture.create(size=(w, h), colorfmt="rgb")
        tex.flip_vertical()
        tex.blit_buffer(b"".join(rows), colorfmt="rgb", bufferfmt="ubyte")
        self.texture = tex
        self.play = play


def camera_shot():
    print("\nPhotos")
    import kivy.uix.camera
    kivy.uix.camera.Camera = FakeCameraView
    M.desktop_camera_available = lambda: True
    previews = []
    M.AttachPreviewModal = lambda app_, path, temporary=False: type(
        "P", (), {"open": lambda self: previews.append((path, temporary))})()
    screen().take_photo()
    sheet = sheets(M.CameraSheet)
    check("with a camera, the camera key opens its view", sheet)
    if sheet:
        sheet[0]._shoot()
    step_state["previews"] = previews


step_state = {}
step(camera_shot, 0.6)


def camera_check():
    previews = step_state.get("previews") or []
    check("the shutter hands over a photo to send, marked temporary",
          previews and previews[0][1] is True)
    if previews:
        from PIL import Image
        im = Image.open(previews[0][0]).convert("RGB")
        w, h = im.size
        tl, br = im.getpixel((2, 2)), im.getpixel((w - 3, h - 3))
        check("...the right way up (its top-left corner where it was)",
              tl[0] > 200 and tl[2] < 60 and br[2] > 200,
              f"top-left {tl}, bottom-right {br}")
        step_state["shot"] = previews[0][0]


step(camera_check, 0.3)


def temporary_photo():
    path = step_state.get("shot")
    if not path:
        return check("a temporary photo is deleted once sent", False)
    app._send_media = lambda *a: None
    app.current_peer = PEER
    app.send_image(path, "", temporary=True)
    deadline = time.time() + 5
    while os.path.exists(path) and time.time() < deadline:
        time.sleep(0.05)
    check("a picked or taken photo is deleted once its copy is sent",
          not os.path.exists(path))
    keep = M.MEDIA_DIR / "mine.png"
    from PIL import Image
    Image.new("RGB", (40, 30), (9, 9, 9)).save(keep)
    app.send_image(str(keep), "", temporary=False)
    time.sleep(1.0)
    check("...but a file chosen from the computer is left where it is",
          keep.exists())


step(temporary_photo, 0.3)


def icons():
    print("\nIcons")
    src = open(CLIENT, encoding="utf-8").read()
    import re
    names = set(re.findall(r'draw_icon\(\s*"([a-z_]+)"', src))
    names |= set(re.findall(r'IconButton\(\s*"([a-z_]+)"', src))
    names |= set(re.findall(r'(?:set_icon|morph|show_icon)\(\s*"([a-z_]+)"',
                            src))
    names |= set(re.findall(r'key\("([a-z_]+)"', src))
    names |= set(re.findall(r'ListRow\(\s*"([a-z_]+)"', src))   # 18.1
    names |= {"mic", "send", "smile", "keyboard"}
    # Every name must draw something: compare against an unknown one.
    blank = M.offscreen_texture(("test", "blank"), 40, 40, lambda k: None)
    empty = blank.pixels if blank else b""
    missing = []
    for name in sorted(names):
        tex = M.offscreen_texture(
            ("test", name), 40, 40,
            lambda k, n=name: M.icon_geometry(n, 20 * k, 20 * k, 30 * k,
                                              (1, 1, 1, 1), 1.0 * k, k))
        if tex is None or tex.pixels == empty:
            missing.append(name)
    check(f"each of the {len(names)} icons the app names draws something",
          not missing, str(missing))
    tex = M.offscreen_texture(
        ("test", "attach-aa"), 40, 40,
        lambda k: M.icon_geometry("attach", 20 * k, 20 * k, 22 * k,
                                  (1, 1, 1, 1), 0.7 * k, k))
    alpha = tex.pixels[3::4] if tex else b""
    partial = [a for a in alpha if 0 < a < 255]
    inked = [a for a in alpha if a > 0]
    check("icons are drawn smooth: their edges carry partial coverage",
          len(set(partial)) >= 8 and len(partial) > len(inked) * .25,
          f"{len(set(partial))} levels, {len(partial)}/{len(inked)} pixels")
    check("the window carries the Ferritext icon, not Kivy's",
          app.icon.endswith("icon_desktop.png") and os.path.isfile(app.icon))


step(icons, 0.2)


def chooser_short_window():
    print("\nA file chooser on a resized window (16.0)")
    CHOOSER["was"] = tuple(Window.size)
    # The app's own list, as Linux and a Mac show it (and Windows, where
    # its own dialog will not open).
    M.IS_WINDOWS = False
    CHOOSER["sheet"] = REAL_CHOOSER(lambda path: None)
    CHOOSER["sheet"].open(animation=False)
    Window.size = (dp(360), dp(560))


CHOOSER = {}
step(chooser_short_window, 1.0)


def chooser_fits():
    sheet = CHOOSER["sheet"]
    buttons = sheet.body.children[0]              # the last row: its buttons
    bottom = buttons.to_window(buttons.x, buttons.y)[1]
    check("on a short window its list gives way, and its buttons stay in "
          "view", sheet.scroller.height >= sheet.body.height - 1 and
          bottom >= 0 and sheet.chooser.height < dp(360),
          f"scroller {sheet.scroller.height:.0f}, body "
          f"{sheet.body.height:.0f}, list {sheet.chooser.height:.0f}")
    CHOOSER["short"] = sheet.chooser.height
    # Taller again, but not the app's own 820 dp: Windows' runner has a
    # 768-pixel screen, and a window cannot be taller (run 7).
    Window.size = (dp(420), dp(680))


step(chooser_fits, 1.0)


def chooser_drag_on():
    """A person's drag gives the window one size after another. On Windows
    the first, asked for here, reaches the sheet before the window's own
    height has changed (Kivy reads that from the drawing surface, which
    follows once the system has resized the window): the next one finds
    it changed (runs 7, 8 and 10: the list kept its short height)."""
    Window.size = (dp(421), dp(680))


step(chooser_drag_on, 0.5)


def chooser_grows_back():
    sheet = CHOOSER["sheet"]
    # Back to its 360 dp, or to all the room a sheet has (90% of the
    # window), where the window is too short for that.
    whole = abs(sheet.chooser.height - dp(360)) < 1 or \
        abs(sheet.height - Window.height * 0.9) < 1
    check("...and grows back when the window does",
          sheet.chooser.height > CHOOSER["short"] + 1 and whole,
          f"list {sheet.chooser.height:.0f} (short {CHOOSER['short']:.0f}), "
          f"sheet {sheet.height:.0f} in {tuple(Window.size)}")
    sheet.dismiss(animation=False)
    Window.size = CHOOSER["was"]


step(chooser_grows_back, 0.3)

# ---- 18.1: on Windows, Windows' own Open dialog ------------------------------
import ctypes                                                 # noqa: E402

DIALOG = {"picked": [], "title": f"Ferritext check {os.getpid()}"}


def dialog_asks():
    """What Windows is handed, read back from the structure itself."""
    seen = {}

    def windows(pointer):
        box = pointer._obj
        if "kinds" not in seen:            # the first call's, 25 long
            seen["kinds"] = ctypes.wstring_at(box.lpstrFilter, 25)
        seen["title"], seen["flags"] = box.lpstrTitle, box.Flags
        seen["room"], seen["start"] = box.nMaxFile, box.lpstrInitialDir
        path = ctypes.create_unicode_buffer("C:\\photos\\a b.png")
        ctypes.memmove(box.lpstrFile, path, ctypes.sizeof(path))
        return seen.get("answer", 1)

    got = M.windows_open_dialog("Send a photo", ["*.jpg", "*.png"],
                                "C:\\photos", ask=windows)
    check("Windows' Open dialog is asked for the kinds of file by name "
          "and pattern, under the sheet's title, in the folder given, for "
          "a file that exists; the one chosen comes back",
          got == "C:\\photos\\a b.png" and
          seen["kinds"] == "*.jpg;*.png\0*.jpg;*.png\0\0" and
          seen["title"] == "Send a photo" and seen["start"] == "C:\\photos"
          and seen["flags"] & 0x1000 and seen["flags"] & 0x80000 and
          seen["room"] == 32768, (got, seen))
    seen["answer"] = 0
    check("...closed without a choice, nothing comes back",
          M.windows_open_dialog("Send a photo", ["*.png"],
                                ask=windows) is None)


step(dialog_asks, 0.1)


def dialog_in_place():
    """As on Windows: the sheet's open() goes to Windows' dialog."""
    DIALOG["real"] = M.IS_WINDOWS, M.windows_open_dialog
    M.IS_WINDOWS = True
    M.windows_open_dialog = lambda title, kinds, start="", owner=None: \
        DIALOG.setdefault("asked", (title, kinds)) and "C:\\photos\\a.png"
    REAL_CHOOSER(DIALOG["picked"].append, title="Their code").open()


step(dialog_in_place, 0.6)


def dialog_picked():
    check("on Windows a photo is chosen in Windows' own dialog, not the "
          "app's list: no sheet opens, and the file comes back to the app "
          "on its own thread",
          DIALOG["picked"] == ["C:\\photos\\a.png"] and
          DIALOG["asked"] == ("Their code", REAL_CHOOSER.KINDS) and
          not any(isinstance(w, REAL_CHOOSER) for w in Window.children),
          (DIALOG["picked"], DIALOG.get("asked")))

    def broken(title, kinds, start="", owner=None):
        raise OSError("no dialog here")
    M.windows_open_dialog = broken
    REAL_CHOOSER(DIALOG["picked"].append).open()


step(dialog_picked, 0.6)


def dialog_falls_back():
    sheets = [w for w in Window.children if isinstance(w, REAL_CHOOSER)]
    check("...and where Windows' dialog will not open, the app's own list "
          "does", len(sheets) == 1 and len(DIALOG["picked"]) == 1)
    for sheet in sheets:
        sheet.dismiss(animation=False)
    M.IS_WINDOWS, M.windows_open_dialog = DIALOG["real"]


step(dialog_falls_back, 0.3)

if sys.platform == "win32":
    # The real one, on Windows itself: it opens under its title over the
    # app's window, and closed, gives nothing back.
    USER32 = ctypes.windll.user32
    USER32.FindWindowW.restype = ctypes.c_void_p
    USER32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    USER32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                    ctypes.c_size_t, ctypes.c_size_t]

    def real_dialog_opens():
        DIALOG["gave"] = []
        DIALOG["thread"] = threading.Thread(
            target=lambda: DIALOG["gave"].append(M.windows_open_dialog(
                DIALOG["title"], ["*.png"], tempfile.gettempdir(),
                Window.get_window_info().window)), daemon=True)
        DIALOG["thread"].start()

    def real_dialog_shown():
        found = USER32.FindWindowW("#32770", DIALOG["title"])
        if not found and DIALOG["thread"].is_alive():
            return False                       # not up yet: asked again
        check("Windows itself: its Open dialog opens under the title "
              "asked for, while the app goes on", bool(found),
              DIALOG["gave"])
        if found:
            USER32.PostMessageW(found, 0x0010, 0, 0)          # WM_CLOSE

    def real_dialog_closed():
        if DIALOG["thread"].is_alive():
            return False
        check("...and closed without a choice, it gives nothing back",
              DIALOG["gave"] == [None], DIALOG["gave"])

    step(real_dialog_opens, 1.0)
    step(real_dialog_shown, 0.5)
    step(real_dialog_closed, 0.3)


def finish():
    passed = sum(1 for _n, c in RESULTS if c)
    print(f"\n  {passed}/{len(RESULTS)} passed", flush=True)
    os._exit(0 if passed == len(RESULTS) else 1)


def start(_dt):
    if app.splash is not None or app.store is None:
        Clock.schedule_once(start, 0.2)
        return
    Clock.schedule_once(lambda _dt: run(0), 0.3)


Clock.schedule_once(start, 0.5)
threading.Timer(240, lambda: os._exit(3)).start()
app.run()
