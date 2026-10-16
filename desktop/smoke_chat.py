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
GROUP = M.group_peer("ab" * 16)
BODY = "The quick brown fox jumps over the lazy dog and keeps on running"


class StubApp:
    """Just enough app for ConversationScreen to wire its callbacks to."""
    current_peer = PEER
    keys = type("Keys", (), {"address": "FecMe1111111111111111111111111111",
                             "has_identity": True, "username": "me",
                             "label": "Personal"})
    store = None

    def conversation_kind(self, peer):
        return "dm"

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
        Window.clearcolor = M.C(M.T.bg)
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
        Clock.schedule_once(self.runs, 0.1)

    # -- v12: runs of messages ------------------------------------------------
    def runs(self, _dt):
        def msg(i, direction, at, status=3):
            return {"id": 100 + i, "server_id": None, "direction": direction,
                    "body": f"run message {i}", "status": status, "read": 1,
                    "created_at": f"2026-09-14T{at}+00:00"}
        self.run_msgs = [msg(0, "in", "21:30:00"), msg(1, "in", "21:30:40"),
                         msg(2, "in", "21:31:30"), msg(3, "out", "21:32:00"),
                         msg(4, "in", "21:45:00")]
        s = self.screen
        s._rendered_peer = None
        s.render(self.run_msgs, True, lambda *a: None, peer=PEER + "x")
        Clock.schedule_once(self.check_runs, 0.8)

    def check_runs(self, _dt):
        s = self.screen
        rows = [s._rows[100 + i] for i in range(5)]
        b = [r.bubble for r in rows]
        check("a run: the same person within minutes joins up",
              b[0].joined_below and b[1].joined_above and b[1].joined_below
              and b[2].joined_above and not b[2].joined_below)
        check("...the other person, or a quarter of an hour, starts anew",
              not b[3].joined_above and not b[3].joined_below and
              not b[4].joined_above)
        check("...the gap inside a run is narrow, between runs wider",
              rows[0].padding[3] == rows[1].padding[1] == rows[0].RUN_GAP
              and rows[2].padding[3] == rows[3].padding[1] == rows[0].GAP)
        check("...only the last of a run keeps the tail",
              b[0]._radii()[3] == dp(4) and b[2]._radii()[3] == dp(3)
              and b[1]._radii() == [dp(4), dp(10), dp(10), dp(4)])
        check("...a time line the next repeats is shown once",
              b[0].meta_row.parent is None and
              b[1].meta_row.parent is not None and
              b[2].meta_row.parent is not None)
        check("with one person, no disc beside their messages, no gutter",
              all(r.face is None and not r.face_room for r in rows) and
              abs(rows[0].bubble.x - rows[0].x - dp(12)) < 1)
        self.run_msgs[0] = dict(self.run_msgs[0], read=0)
        s.render(self.run_msgs, True, lambda *a: None, peer=PEER + "x")
        Clock.schedule_once(self.check_runs_again, 0.5)

    def check_runs_again(self, _dt):
        b = self.screen._bubbles[100]
        check("...and shown again when the two no longer say the same",
              b.meta_row.parent is b and "mark read" in b.meta.text)
        Clock.schedule_once(self.big_emoji, 0.1)

    # -- v12: big emoji -----------------------------------------------------------
    def big_emoji(self, _dt):
        cases = {"\U0001F44D": 1, "\U0001F602\U0001F602\U0001F602": 3,
                 "\U0001F602" * 4: 0, "ok \U0001F44D": 0, "\u2764\ufe0f": 1,
                 "\u2764": 0, "\U0001F44D\U0001F3FD": 1, "\u2192": 0,
                 "\U0001F468\u200d\U0001F469\u200d\U0001F467": 1,
                 "\U0001F1EC\U0001F1E7": 1, "1\ufe0f\u20e3": 1, "12": 0}
        wrong = {k: M.emoji_only(k) for k, v in cases.items()
                 if M.emoji_only(k) != v}
        check("a message of only one to three emoji is told apart (flags, "
              "keycaps, skin tones and families count as one)", not wrong,
              wrong)

        def msg(i, body, ext=None):
            return {"id": 200 + i, "server_id": None, "direction": "in",
                    "body": body, "status": 3, "read": 1, "ext": ext,
                    "created_at": "2026-09-14T21:50:00+00:00"}
        import json
        self.emoji_msgs = [
            msg(0, "\U0001F44D"), msg(1, "\U0001F602\U0001F602\U0001F602"),
            msg(2, "\U0001F44D", json.dumps({"fwd": 1})), msg(3, "hi")]
        s = self.screen
        s._rendered_peer = None
        s.render(self.emoji_msgs, True, lambda *a: None, peer=PEER + "e")
        Clock.schedule_once(self.check_big_emoji, 0.6)

    def check_big_emoji(self, _dt):
        b = [self.screen._bubbles[200 + i] for i in range(4)]
        check("...it is drawn large, on the page, without a bubble",
              b[0].big == 1 and b[0].body.font_size == M.fs(44) and
              b[1].big == 3 and b[1].body.font_size == M.fs(32) and
              not any(type(i).__name__.endswith("RoundedRectangle")
                      for i in b[0].canvas.before.children))
        check("...a forwarded one, or words with it, stay in a bubble",
              b[2].big == 0 and b[3].big == 0 and
              b[2].body.font_size == M.fs(17))
        Clock.schedule_once(self.faces, 0.1)

    # -- v12: in a group, a disc beside each run of theirs -------------------
    def faces(self, _dt):
        ann, bo = "FecAnn" + "1" * 28, "FecBo" + "1" * 29

        def msg(i, direction, at, author=None):
            return {"id": 300 + i, "server_id": None, "direction": direction,
                    "body": f"group message {i}", "status": 3, "read": 1,
                    "author": author,
                    "created_at": f"2026-09-14T{at}+00:00"}
        self.ann = ann
        self.group_msgs = [msg(0, "in", "21:30:00", ann),
                           msg(1, "in", "21:30:30", ann),
                           msg(2, "in", "21:31:00", bo),
                           msg(3, "out", "21:31:30"),
                           msg(4, "in", "21:32:00", ann)]
        self.next_msg = msg(5, "in", "21:32:30", ann)
        s = self.screen
        s.set_peer(GROUP, {"username": None, "ticks_enabled": 1}, 0,
                   kind="group", group={"name": "Walkers", "members": [
                       {"a": ann, "u": "ann"}, {"a": bo, "u": "bo"}]})
        s._rendered_peer = None
        s.render(self.group_msgs, True, lambda *a: None, peer=GROUP)
        Clock.schedule_once(self.check_faces, 0.8)

    def check_faces(self, _dt):
        s = self.screen
        rows = [s._rows[300 + i] for i in range(5)]
        shown = [r.face is not None and r.face.parent is r for r in rows]
        check("in a group, the last of each run of theirs has its author's "
              "disc", shown == [False, True, True, False, True], str(shown))
        room = M.BubbleRow.FACE_LEFT + M.BubbleRow.FACE + \
            M.BubbleRow.FACE_GAP
        check("...level with the bubble's foot, in a gutter all theirs share",
              abs(rows[1].face.y - rows[1].bubble.y) < 1 and
              rows[1].face.right <= rows[1].bubble.x and
              all(abs(r.bubble.x - r.x - room) < 1
                  for r in (rows[0], rows[1], rows[2], rows[4])) and
              abs(rows[3].right - rows[3].bubble.right - dp(12)) < 1,
              f"{[r.bubble.x - r.x for r in rows]}")
        check("...showing who wrote: their initial, until they have a picture",
              rows[1].face._initial.text == "A" and
              rows[2].face._initial.text == "B")
        face = rows[1].face
        check("...and a touch on it (or just beside it) is a touch on them",
              rows[1].face_at(face.center_x, face.center_y) and
              rows[1].face_at(rows[1].x + dp(2), face.top + dp(4)) and
              not rows[1].face_at(rows[1].bubble.center_x, face.center_y)
              and not rows[0].face_at(rows[0].x + dp(20),
                                      rows[0].bubble.y + dp(10)))

        class People:
            @staticmethod
            def get_contact(address):
                return {"address": address, "username": "ann", "listed": 0}
        s.app.store, s.app.list_contact = People(), lambda a: None
        try:
            s.on_face_tap(self.ann)
        finally:
            s.app.store = None
        sheets = [w for w in Window.children
                  if isinstance(w, M.MenuSheet)]
        texts = [w.text for sheet in sheets for w in sheet.walk()
                 if isinstance(w, M.Label)]
        check("...which offers what their row on the group's page does",
              sheets and "Add ann to your contacts" in texts, str(texts))
        for sheet in sheets:
            sheet.dismiss(animation=False)
        s.render(self.group_msgs + [self.next_msg], True, lambda *a: None,
                 peer=GROUP)
        Clock.schedule_once(self.check_faces_moved, 0.5)

    def check_faces_moved(self, _dt):
        rows = [self.screen._rows[300 + i] for i in (4, 5)]
        check("...a new message of theirs, joining the run, takes it over",
              rows[0].face.parent is None and rows[1].face is not None and
              rows[1].face.parent is rows[1])
        Clock.schedule_once(lambda *_a: self.stop(), 0.1)


if __name__ == "__main__":
    Harness().run()
    passed = sum(1 for _n, c in RESULTS if c)
    print(f"\n  {passed}/{len(RESULTS)} passed")
    sys.exit(0 if passed == len(RESULTS) else 1)
