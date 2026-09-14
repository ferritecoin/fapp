#!/usr/bin/env python3
"""Conversation-layout regression test, against REAL Kivy (no stubs).

Covers the two v8.0.2 changes to the chat screen:

  * short conversations sit on the composer instead of flying to the top of
    an empty screen, and stay there however scroll_y is nudged -- while a
    conversation that genuinely overflows scrolls exactly as it always did;
  * message text can be highlighted and copied.

Run headless:

    xvfb-run -a python3 desktop/smoke_chat.py main.py
"""
import os
import sys
import tempfile

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("SDL_VIDEODRIVER", "x11")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-chat-")

CLIENT = sys.argv[1] if len(sys.argv) > 1 else "main.py"

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f" — {detail}" if detail and not cond else ""))


import importlib.util                                          # noqa: E402
_spec = importlib.util.spec_from_file_location("fext_chat", CLIENT)
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_chat"] = M
_spec.loader.exec_module(M)

from kivy.app import App                                       # noqa: E402
from kivy.clock import Clock                                   # noqa: E402
from kivy.core.window import Window                            # noqa: E402
from kivy.metrics import dp                                    # noqa: E402
from kivy.uix.screenmanager import NoTransition, ScreenManager  # noqa: E402

PEER = "FecTK1LGMNhQ1111111111111111111111"
BODY = "The quick brown fox jumps over the lazy dog and keeps on running"


class StubApp:
    """Just enough app for ConversationScreen to wire its callbacks to."""
    current_peer = PEER

    def mark_peer_read(self, *a):
        pass

    def open_contact_settings(self, *a):
        pass

    def go_back_to_chats(self, *a):
        pass

    def load_older_messages(self, *a):
        pass

    def send_current(self, *a):
        pass


def messages(n, body=None):
    return [{"id": i + 1, "server_id": None,
             "direction": "out" if i % 2 == 0 else "in",
             "body": body or f"message {i} " + "lorem ipsum " * (i % 4),
             "status": 3, "read": 1,
             "created_at": "2026-09-14T21:33:00"} for i in range(n)]


class Harness(App):
    def build(self):
        Window.size = (dp(420), dp(820))
        Window.clearcolor = M.C(M.COL_BG_LIST)
        self.screen = M.ConversationScreen(StubApp())
        manager = ScreenManager(transition=NoTransition())
        manager.add_widget(self.screen)
        Clock.schedule_once(self.short_chat, 0.4)
        return manager

    # -- a chat too small to scroll -----------------------------------------
    def short_chat(self, _dt):
        s = self.screen
        s.set_peer(PEER, {"username": "kgt03", "ticks_enabled": 1,
                          "auto_read": 1}, 0)
        s.render(messages(1, "a"), True, lambda *a: None, peer=PEER)
        Clock.schedule_once(self.check_short, 1.0)

    def check_short(self, _dt):
        s = self.screen
        bottom = s.scroll.y
        check("short chat: one message does not fill the viewport",
              s.content_height() < s.scroll.height,
              f"content={s.content_height()} view={s.scroll.height}")
        check("short chat: the filler pads it out to the viewport",
              abs(s.messages.height - s.scroll.height) < 1.0,
              f"messages={s.messages.height} view={s.scroll.height}")
        check("short chat: the message rests on the composer",
              abs(s.scroll.g_translate.xy[1] - bottom) < 1.0,
              f"translate_y={s.scroll.g_translate.xy[1]} bottom={bottom}")
        # THE REGRESSION: before v8.0.2 this dropped the message at the top
        # of an otherwise empty screen and left it there.
        s.scroll.scroll_y = 1.0
        check("short chat: it STAYS there when scroll_y is nudged to the top",
              abs(s.scroll.g_translate.xy[1] - bottom) < 1.0,
              f"translate_y={s.scroll.g_translate.xy[1]} bottom={bottom}")
        check("short chat: counts as at-bottom, so no jump pill",
              s.at_bottom() is True)
        Clock.schedule_once(self.long_chat, 0.1)

    # -- a chat long enough to scroll ---------------------------------------
    def long_chat(self, _dt):
        self.screen.render(messages(40), True, lambda *a: None, peer=PEER)
        Clock.schedule_once(self.check_long, 1.4)

    def check_long(self, _dt):
        s = self.screen
        check("long chat: content overflows the viewport",
              s.content_height() > s.scroll.height,
              f"content={s.content_height()} view={s.scroll.height}")
        check("long chat: the filler gets out of the way entirely",
              s._filler.height == 0, str(s._filler.height))
        check("long chat: content height is the real height again",
              abs(s.messages.height - s.content_height()) < 0.5)
        s.scroll.scroll_y = 0.0
        at_bottom = s.scroll.g_translate.xy[1]
        check("long chat: scroll_y 0 shows the newest message",
              abs(at_bottom - s.scroll.y) < 1.0,
              f"translate_y={at_bottom}")
        check("long chat: it is at-bottom there", s.at_bottom() is True)
        self._at_bottom_y = at_bottom
        s.scroll.scroll_y = 1.0
        # ScrollView repositions its content on a Clock trigger, so the
        # translation is only correct on the NEXT frame.
        Clock.schedule_once(self.check_long_scrolled, 0.2)

    def check_long_scrolled(self, _dt):
        s = self.screen
        check("long chat: scroll_y 1 really does scroll to the oldest",
              s.scroll.g_translate.xy[1] < self._at_bottom_y - dp(100),
              f"translate_y={s.scroll.g_translate.xy[1]}")
        check("long chat: scrolled away is NOT at-bottom (jump pill shows)",
              s.at_bottom() is False)
        Clock.schedule_once(self.check_selection, 0.1)

    # -- highlight and copy --------------------------------------------------
    def check_selection(self, _dt):
        # Bubbles wrap on the frame AFTER they are added (BubbleRow drives
        # layout_for off its width), so let the render settle before
        # measuring anything.
        self.screen.render(messages(1, BODY), True, lambda *a: None,
                           peer=PEER)
        Clock.schedule_once(self.check_selection_settled, 1.0)

    def check_selection_settled(self, _dt):
        s = self.screen
        body = s._bubbles[1].body
        check("message text is selectable", isinstance(body, M.SelectableText),
              type(body).__name__)
        check("it is read-only, so the phone's bubble offers only Copy",
              body.readonly is True)
        body.select_all()
        check("select-all highlights the whole message",
              body.selection_text == BODY, repr(body.selection_text))
        M.Clipboard.copy("")
        body.copy()
        check("copy puts it on the clipboard",
              M.Clipboard.paste() == BODY, repr(M.Clipboard.paste()))
        body.cancel_selection()
        body.select_text(4, 19)
        body.copy()
        check("a partial highlight copies just that run",
              M.Clipboard.paste() == BODY[4:19], repr(M.Clipboard.paste()))
        M.Clipboard.copy("")
        body.cancel_selection()
        body._handle_shortcut(ord("a"))          # the desktop ctrl+A / ctrl+C
        body._handle_shortcut(ord("c"))
        check("ctrl+A then ctrl+C copies the message",
              M.Clipboard.paste() == BODY, repr(M.Clipboard.paste()))
        check("the text is not clipped by the bubble",
              body.height + 0.5 >= body.minimum_height,
              f"h={body.height} needs={body.minimum_height}")
        check("tapping a message can still mark it read",
              hasattr(body, "_on_tap"))
        Clock.schedule_once(lambda *_a: self.stop(), 0.1)


if __name__ == "__main__":
    Harness().run()
    passed = sum(1 for _n, c in RESULTS if c)
    print(f"\n  {passed}/{len(RESULTS)} passed")
    sys.exit(0 if passed == len(RESULTS) else 1)
