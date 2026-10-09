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
os.environ.setdefault("SDL_VIDEODRIVER", "x11")
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-composer-")
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
    STEPS.append((fn, delay))


def run(i=0):
    if i >= len(STEPS):
        finish()
        return
    fn, delay = STEPS[i]
    try:
        fn()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check(f"step {i} ({getattr(fn, '__name__', '?')}) ran", False,
              f"{type(exc).__name__}: {exc}")
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


step(record_silent_check, 0.2)


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
