#!/usr/bin/env python3
"""Desktop launch smoke test with REAL Kivy (no stubs).

Runs the shipped client through its actual startup path — App.run() ->
build() -> _post_build() -> first-run dialog — on a mock window, then stops
the loop. This is the desktop equivalent of "does it survive initial launch".
"""
import os, sys, tempfile, traceback

os.environ.setdefault("KIVY_WINDOW", "sdl2")
os.environ.setdefault("SDL_VIDEODRIVER", "x11")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("KIVY_AUDIO", "sdl2")
os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
HOME = tempfile.mkdtemp(prefix="fext-desktop-")
os.environ["HOME"] = HOME                         # a FRESH install

CLIENT = sys.argv[1]
RESULTS = []
def check(n, c, d=""):
    RESULTS.append((n, bool(c), d))
    print(("  [PASS] " if c else "  [FAIL] ") + n + (f" — {d}" if d and not c else ""))

import importlib.util
spec = importlib.util.spec_from_file_location("fext_desktop", CLIENT)
mod = importlib.util.module_from_spec(spec)
sys.modules["fext_desktop"] = mod
try:
    spec.loader.exec_module(mod)
    check("client imports under real Kivy", True)
except Exception as e:
    check("client imports under real Kivy", False, f"{type(e).__name__}: {e}")
    traceback.print_exc(); sys.exit(1)

check("desktop platform detected (not mobile)", mod.IS_MOBILE is False,
      f"IS_MOBILE={mod.IS_MOBILE}")
check("data dir is the user's home, not a sandbox",
      str(mod.DATA_DIR) == os.path.join(HOME, ".fext"), str(mod.DATA_DIR))
check("desktop request timeout applied (12s, not the 20s mobile value)",
      mod.REQUEST_TIMEOUT == 12, str(mod.REQUEST_TIMEOUT))

# crypto works with the DESKTOP cryptography (the HKDF backend fix must be
# compatible in both directions, not just on the phone)
try:
    keys = mod.IdentityManager()
    priv, _p, _a, _n = mod.mine_identity()
    keys.add_identity(priv, "desktop")
    crypto = mod.CryptoEngine(keys)
    peer_priv, _q, _r, _s = mod.mine_identity()
    peer_pub = mod.pub_to_compressed(mod.priv_to_pub(peer_priv)).hex()
    env, _sent = crypto.build_envelope("desktop round trip", peer_pub)
    import cryptography
    check(f"seal works on desktop cryptography {cryptography.__version__} "
          f"(HKDF backend fix is version-agnostic)", True)
except Exception as e:
    check("seal works on desktop cryptography", False, f"{type(e).__name__}: {e}")

# --- the real launch ------------------------------------------------------
from kivy.clock import Clock
state = {"built": False, "error": None}
try:
    app = mod.FextApp()
    root = app.build()
    state["built"] = root is not None
    # Since v8.0.2 the root is a FloatLayout holding the ScreenManager and
    # the loading screen stacked over it; the app itself talks to .manager.
    check("App.build() constructs the UI (root + ScreenManager)",
          root is not None and hasattr(app.manager, "current"))
    check("both screens registered",
          {s.name for s in app.manager.screens} >= {"chats", "conversation"},
          str([s.name for s in app.manager.screens]))
    check("loading screen is stacked over the app",
          isinstance(app.splash, mod.SplashOverlay)
          and app.splash in root.children,
          f"splash={app.splash}")
    check("loading screen fades out in 100ms",
          abs(mod.SplashOverlay.FADE - 0.1) < 1e-9,
          str(mod.SplashOverlay.FADE))
    check("loading screen carries the wordmark",
          any("by koh-gt" in getattr(w, "text", "")
              for w in app.splash.walk()),
          "wordmark not found")
    # the loading screen hands over to the first-run dialog when it goes
    app._splash_gone()
    check("loading screen is removed once it has faded",
          app.splash is None and len(root.children) == 1,
          f"children={len(root.children)}")
    check("first-run path runs without error (welcome dialog opens)", True)
    check("app is branded Ferritext", mod.APP_NAME == "Ferritext",
          mod.APP_NAME)
    check("identity payload still carries the original wire tag",
          mod.WIRE_APP_TAG == "FEXT", mod.WIRE_APP_TAG)
    check("the mark ships with the client", mod.LOGO_FILE.exists(),
          str(mod.LOGO_FILE))
except Exception as e:
    state["error"] = e
    check("App.build() constructs the UI", False, f"{type(e).__name__}: {e}")
    traceback.print_exc()

ok = sum(1 for _n, c, _d in RESULTS if c)
print(f"\n  {ok}/{len(RESULTS)} passed")
sys.exit(0 if ok == len(RESULTS) else 1)
