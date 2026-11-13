#!/usr/bin/env python3
"""Calls as sessions (16.1): the state every call screen draws, checked
without sound, pictures or a network.

    python3 desktop/test_conference.py main.py

  * microphone and camera start off for the caller and for those who join,
    and nothing is published before "Confirm & join";
  * a call moves only along its stages (initiated, lobby, joined, left,
    ended), and a wrong step is refused, not guessed;
  * each participant's state is {userId, micEnabled, cameraEnabled,
    screenSharing, handRaisedAt, role}, and what another app sends is
    checked field by field;
  * raised hands keep the order they were raised in;
  * where an incoming call shows: full screen only for a 1-to-1 call in its
    own chat; a toast elsewhere; a group's call never rings, it is a banner;
  * the focus view's stage: a pinned tile, else a shared screen, else
    whoever speaks; leaving and ending;
  * a group's call on the relay (no network here): its invite, field by
    field, its host always its sender; members' state and the in-call chat
    sealed with the call key; a state that names another is ignored; each
    in-call message kept once, and gone with its chat; the card's live
    state from the relay's word; the sound's packets sealed with each
    member's own key, a replayed or altered one refused; voices mixed.
"""
import importlib.util
import os
import sys
import tempfile
import time

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ["KIVY_NO_CONSOLELOG"] = "1"
os.environ["HOME"] = tempfile.mkdtemp(prefix="fext-conference-")
os.environ["USERPROFILE"] = os.environ["HOME"]   # Path.home() on Windows
os.environ["FEXT_LANG"] = "en"          # the English the checks read

_spec = importlib.util.spec_from_file_location(
    "fext_conference", sys.argv[1] if len(sys.argv) > 1 else "main.py")
M = importlib.util.module_from_spec(_spec)
sys.modules["fext_conference"] = M
_spec.loader.exec_module(M)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  [PASS] " if cond else "  [FAIL] ") + name
          + (f" — {detail}" if detail and not cond else ""))


def address():
    return M.mine_identity()[2]


ME, ANN, BEN, CAT = (address() for _ in range(4))
GROUP = "g" + "0" * 31


def session(group=True, host=None, video=True, chat=GROUP):
    return M.CallSession(chat, ME, group=group, video=video,
                         host=host or ME)


print("Off until turned on")
s = session()
check("a new call: microphone and camera off, nothing to send",
      not s.me.mic and not s.me.camera and not s.me.screen
      and s.publishes() == set() and s.stage == "initiated")
check("the lobby opens (preview_ready)", s.preview() and s.stage == "lobby")
check("in the lobby, the microphone changes what the preview shows "
      "and is published to no one",
      s.set_mic(True) is None and s.me.mic and s.publishes() == set())
check("...and so does the camera",
      s.set_camera(True) is None and s.me.camera and s.publishes() == set())
check("a screen is not shared from the lobby",
      s.set_screen(True) is None and not s.me.screen)
s.set_camera(False)
state = s.confirm_join()
check("Confirm & join publishes exactly the six fields",
      state is not None and sorted(state) == sorted(
          ["userId", "micEnabled", "cameraEnabled", "screenSharing",
           "handRaisedAt", "role"]), str(state))
check("...as chosen in the lobby: microphone on, camera off",
      state["micEnabled"] is True and state["cameraEnabled"] is False
      and s.publishes() == {"a"})
check("...the one who made the call is its host", state["role"] == "host")
joiner = session(host=ANN)
joiner.preview()
check("one who joins is a member, and joins with both off",
      joiner.confirm_join() == {"userId": ME, "micEnabled": False,
                                "cameraEnabled": False,
                                "screenSharing": False,
                                "handRaisedAt": None, "role": "member"})

print("\nOnly along its stages")
s2 = session()
check("no joining without the lobby first",
      s2.confirm_join() is None and s2.stage == "initiated")
s2.preview()
s2.confirm_join()
check("no joining twice", s2.confirm_join() is None and s2.joined)
check("in the call, turning the camera on publishes it",
      s2.set_camera(True)["cameraEnabled"] is True
      and s2.publishes() == {"v"})
s2.set_screen(True)
check("sharing the screen adds a tile of its own, which takes the stage",
      (ME, "s") in s2.tiles() and s2.stage_tile() == (ME, "s")
      and s2.publishes() == {"v", "s"})
check("a group member leaves (call:leave) and may come back through the "
      "lobby", s2.leave() and s2.stage == "left" and s2.preview())
check("leaving turned everything off",
      not s2.me.mic and not s2.me.camera and not s2.me.screen)
one = session(group=False, chat=ANN)
one.preview()
one.confirm_join()
check("a 1-to-1 call ends when either side leaves",
      one.leave() and one.stage == "ended" and one.ended_at is not None)
check("an ended call goes nowhere", not one.preview()
      and one.confirm_join() is None and not one.move("lobby"))
d = one.duration()
check("...and its length stays what it was", one.duration(now=1e12) == d)

print("\nThe others' state, checked")
s3 = session(host=ANN)
s3.preview()
s3.confirm_join()
check("a member's state from the room is taken",
      s3.update_member({"userId": ANN, "micEnabled": True,
                        "cameraEnabled": False, "screenSharing": False,
                        "handRaisedAt": None, "role": "member"})
      and s3.members[ANN].mic)
check("...the room's maker is its host, whatever the state says",
      s3.members[ANN].role == "host")
s3.update_member({"userId": BEN, "role": "host", "micEnabled": "yes",
                  "cameraEnabled": 1, "handRaisedAt": -5})
ben = s3.members[BEN]
check("...no one else can claim to be host, and only true is true",
      ben.role == "member" and not ben.mic and not ben.camera
      and ben.hand_at is None)
check("a state naming no real address is refused",
      not s3.update_member({"userId": "Fnotanaddress", "micEnabled": True})
      and not s3.update_member("nonsense") and len(s3.members) == 2)
check("this device's own state is not taken from the room",
      not s3.update_member({"userId": ME, "micEnabled": True})
      and not s3.me.mic)

print("\nRaised hands, in order")
s3.update_member({"userId": BEN, "handRaisedAt": 2000})
s3.update_member({"userId": CAT, "handRaisedAt": 1000})
s3.raise_hand(now_ms=3000)
check("first raised, first in the queue",
      [m.user for m in s3.hands()] == [CAT, BEN, ME])
s3.raise_hand(now_ms=9000)
check("raising it again keeps its place", s3.me.hand_at == 3000)
check("lowering takes it out", s3.lower_hand() is not None
      and [m.user for m in s3.hands()] == [CAT, BEN])

print("\nWhere an incoming call shows")
call_from_ann = M.CallSession(ANN, ME, group=False, video=True, host=ANN)
check("1-to-1, in its chat: full screen",
      M.incoming_presentation(call_from_ann, ANN) == "fullscreen")
check("1-to-1, elsewhere: the card and a toast, the screen left alone",
      M.incoming_presentation(call_from_ann, BEN) == "toast"
      and M.incoming_presentation(call_from_ann, None) == "toast")
conference = M.CallSession(GROUP, ME, group=True, video=False, host=ANN)
check("a group's call never rings full screen, even in its chat",
      M.incoming_presentation(conference, GROUP) == "banner"
      and M.incoming_presentation(conference, None) == "banner")

print("\nThe stage and the end")
s4 = session()
s4.preview()
s4.confirm_join()
s4.update_member({"userId": ANN, "cameraEnabled": True})
s4.update_member({"userId": BEN, "screenSharing": True})
check("a shared screen takes the stage", s4.stage_tile() == (BEN, "s"))
check("pinning a camera puts it there, in the focus view",
      s4.pin(ANN, "v") and s4.stage_tile() == (ANN, "v")
      and s4.view == "focus")
check("a tile that is not there cannot be pinned", not s4.pin(CAT, "v"))
s4.remove_member(ANN)
check("when the pinned one leaves, the stage goes back to the screen",
      s4.pinned is None and s4.stage_tile() == (BEN, "s"))
s4.update_member({"userId": BEN, "screenSharing": False})
s4.update_member({"userId": CAT})
s4.speaking = CAT
check("with no screen, whoever speaks", s4.stage_tile() == (CAT, "v"))
print("\nThe view follows a shared screen (17.2, FOCUSAUTO)")
auto = session()
auto.preview()
auto.confirm_join()
auto.update_member({"userId": ANN, "cameraEnabled": True})
start = auto.view
auto.update_member({"userId": ANN, "screenSharing": True})
to_focus = auto.follow_screens() and auto.view == "focus"
auto.update_member({"userId": ANN, "screenSharing": False})
check("someone shares a screen: the gallery becomes the focus view; the "
      "share ends: the gallery again",
      start == "grid" and to_focus and auto.follow_screens() and
      auto.view == "grid", (start, to_focus, auto.view))
check("...nothing changed, nothing to do", not auto.follow_screens())
auto.view, auto.view_chosen = "grid", True        # as its View button does
auto.update_member({"userId": ANN, "screenSharing": True})
check("once the view is chosen in this call, a shared screen leaves it be",
      not auto.follow_screens() and auto.view == "grid")
pinned = session()
pinned.preview()
pinned.confirm_join()
pinned.update_member({"userId": ANN, "cameraEnabled": True})
pinned.update_member({"userId": BEN, "cameraEnabled": True})
pinned.pin(BEN, "v")
pinned.update_member({"userId": ANN, "screenSharing": True})
check("...as does a tile pinned to the stage",
      not pinned.follow_screens() and pinned.stage_tile() == (BEN, "v"))

member_call = session(host=ANN)
member_call.preview()
member_call.confirm_join()
member_call.update_member({"userId": ANN})
check("a member cannot end the call for others", not member_call.end()
      and member_call.joined)
check("the host can (call:end)", s4.end() and s4.stage == "ended")
check("labels say what kind of call it is",
      M.call_kind_label(conference) == "Group voice call"
      and M.call_kind_label(call_from_ann) == "Video call")

print("\nA group's call on the relay: the invite")
host_call = M.new_group_call(GROUP, ME, video=False)
host_call.room = "ab" * 8
invite = M.call_invite(host_call)
got = M.session_of_invite(invite, ME, GROUP, ANN)
check("the invite carries the room, its token and the call key, and comes "
      "apart as the same call", got is not None and got.id == host_call.id
      and (got.room, got.token, got.key) ==
      (host_call.room, host_call.token, host_call.key) and got.group
      and got.me.user == ANN and got.me.role == "member", str(invite))
check("...hosted by whoever sent it, whatever it says",
      M.session_of_invite(dict(invite, h=BEN), ME, GROUP, ANN).host == ME)
broken = [dict(invite, s="xyz"), dict(invite, r="ab" * 9),
          dict(invite, t="00"), dict(invite, key="short"),
          dict(invite, key=M.b64e(b"k" * 16).ljust(44, "=")), "not a dict"]
check("an invite that does not hold together is refused, not guessed",
      all(M.session_of_invite(b, ME, GROUP, ANN) is None for b in broken))
future = M.session_of_invite(dict(invite, at=int(M.time.time()) + 10 ** 6),
                             ME, GROUP, ANN)
check("a start time in the future is taken as now",
      future.started_at <= M.time.time())
card = M.card_of_session(host_call)
again = M.session_of_card(card, ME)
check("its card keeps the invite (this device's alone) and gives it back",
      (again.room, again.token, again.key) ==
      (host_call.room, host_call.token, host_call.key))

print("\nSealed with the call key")
keys_a, keys_b = M.RoomKeys(host_call), M.RoomKeys(got)
sealed = keys_a.seal("state", {"userId": ME, "micEnabled": True})
check("a member's state is sealed: what the relay carries reads as "
      "nothing", "micEnabled" not in sealed
      and keys_b.open("state", sealed) == {"userId": ME, "micEnabled": True})
flipped = M.b64e(bytes([M.b64d(sealed)[0] ^ 1]) + M.b64d(sealed)[1:])
other = M.new_group_call(GROUP, ME, video=False)
other.room = host_call.room
check("altered, or sealed for another call, or a chat line as a state: "
      "refused", keys_b.open("state", flipped) is None
      and M.RoomKeys(other).open("state", sealed) is None
      and keys_b.open("chat", sealed) is None
      and keys_b.open("state", "%%%") is None)


class _App:
    """What RoomCall needs of an app, with no relay."""

    def __init__(self):
        self.store = M.LocalStore(M.Path(tempfile.mkdtemp()) / "t.db")


app = _App()
room_call = M.RoomCall(app, got)
room_call.frame({"event": "state", "index": 3, "address": BEN,
                 "state": keys_a.seal("state", {"userId": CAT,
                                                "micEnabled": True})})
check("a state that names someone other than its sender is not believed",
      CAT not in got.members and BEN in got.members
      and got.members[BEN].mic is False)
mk = M.b64e(b"m" * 32)
room_call.frame({"event": "state", "index": 3, "address": BEN,
                 "state": keys_a.seal("state", {"userId": BEN, "mk": mk,
                                                "handRaisedAt": 5})})
check("one that names its sender is, with the key their sound is sealed "
      "with", got.members[BEN].hand_at == 5
      and room_call.voice_keys == {3: b"m" * 32})
check("a member's sound key is 32 bytes or nothing",
      M.media_key_of({"mk": "short"}) is None
      and M.media_key_of({"mk": M.b64e(b"x" * 31)}) is None)
line = room_call.frame({"event": "chat", "seq": 4, "at": 1000,
                        "index": 3, "address": BEN,
                        "blob": keys_a.seal("chat", {"b": "hi all"})})
check("an in-call message: opened, kept under the call, counted",
      line == {"seq": 4, "at": 1000.0, "sender": BEN, "text": "hi all"}
      and [r["body"] for r in app.store.call_chat_rows(got.id)] ==
      ["hi all"] and got.chat_count == 4, str(line))
check("...once: the same message again is no new line",
      room_call.frame({"event": "chat", "seq": 4, "at": 1000, "index": 3,
                       "address": BEN,
                       "blob": keys_a.seal("chat", {"b": "hi all"})})
      is None and len(app.store.call_chat_rows(got.id)) == 1)
check("an in-call message is cut at a character, at 2000 bytes",
      len(M.call_chat_text("é" * 1500).encode()) == 2000
      and M.call_chat_text("  \x00hi  ") == "hi")
app.store.add_call_card(GROUP, M.card_of_session(got))
check("the card is found by its call's id; newest first among the chat's",
      app.store.call_card(GROUP, got.id)["token"] == got.token
      and app.store.call_cards(GROUP)[0]["id"] == got.id)
app.store.clear_conversation(GROUP)
check("clearing the chat takes its calls' in-call chat too",
      app.store.call_chat_rows(got.id) == []
      and app.store.call_card(GROUP, got.id) is None)
room_call.frame({"event": "leave", "index": 3, "address": BEN})
check("one who leaves goes, with their sound's key",
      BEN not in got.members and 3 not in room_call.voice_keys)

print("\nThe join's answer, against what the relay says meanwhile")


class _Api:
    """The relay's answers to a join, with no relay."""

    def __init__(self, answer):
        self.answer, self.posted = answer, []

    def room_post(self, what, body):
        self.posted.append(what)
        return self.answer if what == "join" else {"ok": True}

    def room_get(self, what, params):
        return {"messages": []}


racing = M.session_of_invite(invite, ME, GROUP, CAT)
race_app = _App()
race_app.api = _Api({"index": 2, "ticket": "t", "udp": 0, "started": 1000,
                     "members": [{"index": 0, "address": ME, "state": ""},
                                 {"index": 1, "address": BEN, "state": ""}],
                     "chat": 0})
race_call = M.RoomCall(race_app, racing)
racing.preview()
racing.confirm_join()
race_call.frame({"event": "leave", "index": 1, "address": BEN})
check("someone the relay said had left is not brought back by the join's "
      "older answer", race_call.join() and BEN not in racing.members
      and ME in racing.members and race_call.index == 2,
      str(sorted(racing.members)))
kept = M.session_of_invite(invite, ME, GROUP, CAT)
kept.members[ANN] = M.CallMember(ANN)
M.apply_room_info(kept, {"ended": 2000}, CAT)
check("an answer that names no one leaves who is there as it was",
      ANN in kept.members and not kept.live)

print("\nThe host asks everyone (16.2)")
host = M.new_group_call(GROUP, ME, video=False)
host.room = "cd" * 8
host.preview()
host.confirm_join()
member = M.session_of_invite(M.call_invite(host), ME, GROUP, BEN)
member.preview()
member.set_mic(True)
member.confirm_join()
check("only the host asks: not a member, and not outside a group's call",
      member.ask_everyone("mute") is None and
      session(group=False).ask_everyone("mute") is None)
host_keys, ask_app = M.RoomKeys(host), _App()
asking = M.RoomCall(ask_app, member)


def host_says(state):
    asking.frame({"event": "state", "index": 0, "address": ME,
                  "state": host_keys.seal("state", state)})


early = host.ask_everyone("mute", now_ms=1000)
check("the host's state carries its ask; 16.1's six fields are as they "
      "were", early["asks"] == {"mute": 1000} and
      sorted(M.CallMember(ME).state()) == sorted(
          ["userId", "micEnabled", "cameraEnabled", "screenSharing",
           "handRaisedAt", "role"]))
host_says(early)
check("an ask made before this device came is not followed",
      member.follow_asks() == ((), None) and member.me.mic)
member.raise_hand(now_ms=1500)
host_says(host.ask_everyone("mute", now_ms=2000))
asks, state = member.follow_asks()
check("a new one is: the microphone off, the state to say so",
      asks == ("mute",) and state["micEnabled"] is False
      and not member.me.mic and member.me.hand_at == 1500)
member.set_mic(True)
host_says(host.me.state())
check("...once: the host's state again changes nothing, and each can "
      "turn theirs back on", member.follow_asks() == ((), None)
      and member.me.mic)
host_says(host.ask_everyone("hands", now_ms=3000))
asks, state = member.follow_asks()
check("Lower all hands lowers this device's hand, and only that",
      asks == ("hands",) and state["handRaisedAt"] is None
      and state["micEnabled"] is True)
cat_keys_state = dict(M.CallMember(CAT).state(), asks={"mute": 9 ** 9})
asking.frame({"event": "state", "index": 4, "address": CAT,
              "state": host_keys.seal("state", cat_keys_state)})
check("a member's state that asks is not obeyed",
      member.follow_asks() == ((), None) and
      member.members[CAT].asks == {} and member.me.mic)
sealed = host_keys.seal("state", dict(host.me.state(),
                                      mk=M.b64e(b"m" * 32)))
check("the host's state, sealed with its asks, stays well under the "
      "relay's 512", len(sealed) < 400, str(len(sealed)))

print("\nThe stage, repainted rather than rebuilt (16.2)")
lively = M.CallSession(GROUP, ME, group=True, video=False, host=ME)
lively.preview()
lively.confirm_join()
for who in (ANN, BEN):
    lively.update_member({"userId": who})
stage = M.CallStageView(lively, M._no_frames, lambda user: user[:4])
built = list(stage._tiles)
lively.speaking = ANN
stage.speaker_moved(None)
repainted = stage._tiles == built
lively.update_member({"userId": CAT})
stage.rebuild()
check("a new speaker repaints the gallery's tiles; someone new builds "
      "them again", repainted and len(stage._tiles) == 4 and
      stage._tiles[0] is not built[0])


print("\nA presenter's face over their screen (17.1)")
talk = M.CallSession(GROUP, ME, group=True, video=True, host=ME)
talk.preview()
talk.confirm_join()
talk.update_member({"userId": ANN, "cameraEnabled": True,
                    "screenSharing": True})
talk.update_member({"userId": BEN})
talk.view = "focus"


def where(stage):
    """(the stage's tiles, the strip's) as (user, track)."""
    strip, top = stage.children
    return ({(t.user, t.track) for t in top.children},
            {(t.user, t.track) for t in strip.children})


stage = M.CallStageView(talk, M._no_frames, lambda user: user[:4])
top, strip = where(stage)
check("the focus view: Ann's screen on the stage, her face in its corner "
      "and not again in the strip", top == {(ANN, "s"), (ANN, "v")} and
      (ANN, "v") not in strip and (BEN, "v") in strip, (top, strip))
M.save_pref("presenter_face", False)
stage.rebuild()
top, strip = where(stage)
check("...or, as Settings › Calls may say, the screen alone",
      top == {(ANN, "s")} and (ANN, "v") in strip, (top, strip))
M.save_pref("presenter_face", True)


class Screen:
    """What a call's control bar asks of its screen."""
    chat_open = False

    def __init__(self, session):
        self.session = session

    def __getattr__(self, _name):          # its toggles, and leave
        return lambda *_a: None


bars = [M.CallControlBar(Screen(M.CallSession(GROUP, ME, group=True,
                                              video=video, host=ME)))
        for video in (False, True)]
check("a group's voice call has no camera button; its video call has one "
      "(17.1)", bars[0].camera is None and bars[1].camera is not None and
      bars[1].camera.parent is bars[1])


class HostScreen(Screen):
    def host_ends(self):
        return True


def arrows(bar):
    return [w for w in bar.children if isinstance(w, M.IconButton) and
            w.icon == "chevron_down"]


wide = M.CallControlBar(HostScreen(M.CallSession(GROUP, ME, group=True,
                                                 video=True, host=ME)))
check("a computer's controls in three groups (17.2): the devices, each "
      "with its ▾, and the screen; Raise, React, Chat, More; Leave, with "
      "the host's ▾ for End for everyone",
      wide.share is not None and wide.react is not None and
      wide.more is not None and len(arrows(wide)) == 3)
real_narrow = M.call_is_narrow
M.call_is_narrow = lambda: True
phone = M.CallControlBar(HostScreen(M.CallSession(GROUP, ME, group=True,
                                                  video=True, host=ME)))
M.call_is_narrow = real_narrow
shown = [w for w in phone.children if isinstance(w, M.IconButton)]
check("...a phone's in one row of six: Microphone, Camera, Raise, Chat, "
      "More, Leave; the rest in More",
      len(shown) == 6 and phone.share is None and phone.react is None and
      not arrows(phone))

print("\nReactions (17.2)")
mine = M.CallSession(GROUP, ME, group=True, video=True, host=ME)
theirs = M.CallSession(GROUP, ANN, group=True, video=True, host=ME)
state = mine.react(M.QUICK_REACTIONS[0])
theirs.update_member(state)
seen = theirs.members[ME]
first = seen.reaction_seen
check("a reaction goes out in its member's state and shows on the others' "
      "devices", state.get("re", [None])[0] == M.QUICK_REACTIONS[0] and
      M.reaction_fresh(seen))
theirs.update_member(state)
check("...the same state again does not show it anew",
      theirs.members[ME].reaction_seen == first)
theirs.members[ME].reaction_seen -= M.CALL_REACTION_SECONDS + 1
check("...it fades after a few seconds", not M.reaction_fresh(
    theirs.members[ME]))
cleared = mine.clear_reaction()
odd = dict(state, re=["not an emoji", 1])
theirs.update_member(odd)
check("...and leaves the state then; one not among the quick reactions "
      "is not shown", "re" not in cleared and
      theirs.members[ME].reaction is None)

print("\nA call's link, for its group's members (17.2, CALLLINK)")
linked = M.CallSession(M.group_peer("ab" * 16), ME, group=True, video=True,
                       host=ME)
linked.room, linked.token, linked.key = "cd" * 8, "ef" * 32, \
    M.b64e(b"k" * 32)
link = M.call_link(linked, "https://relay.example:8443")
data = M.parse_call_link(link)
back = M.session_of_invite(data, data["h"], M.group_peer(data["g"]), ANN)
check("its invite rides in the link's fragment, which never leaves the "
      "device, and comes back whole",
      link.startswith("https://relay.example:8443/call#fx1.") and
      "#" in link and back is not None and back.room == linked.room and
      back.token == linked.token and back.key == linked.key and
      back.video and data["g"] == "ab" * 16)
check("...any other link, or a damaged one, is not a call's",
      M.parse_call_link("https://relay.example/call") is None and
      M.parse_call_link("https://relay.example/call#fx1.%%%") is None and
      M.parse_call_link("https://relay.example/other#fx1.e30") is None)

print("\nThe card, from the relay's word")
seen = M.session_of_invite(invite, ME, GROUP, CAT)
M.apply_room_info(seen, {"started": 1000, "chat": 2, "members": [
    {"index": 0, "address": ME}, {"index": 1, "address": ANN},
    {"index": 2, "address": "not an address"}]}, CAT)
check("who is in it (by address), when it began, its messages",
      sorted(seen.members) == sorted([ME, ANN]) and seen.started_at == 1000
      and seen.chat_count == 2 and seen.members[ME].role == "host")
M.watch_frame(seen, {"event": "leave", "address": ANN}, CAT)
M.watch_frame(seen, {"event": "join", "address": BEN}, CAT)
check("a watched card follows who comes and goes",
      sorted(seen.members) == sorted([ME, BEN]))
M.apply_room_info(seen, {"ended": 1600, "members": []}, CAT)
check("and its end: over, its length known",
      not seen.live and seen.duration() == 600 and not seen.members)

print("\nThe sound's packets")
ka, kb = b"a" * 32, b"b" * 32
sent = []
eng_a = M.RoomEngine(host_call.room, 0, None, "", ka, lambda *_a: None,
                     lambda tag, packet: sent.append(packet) or True)
eng_b = M.RoomEngine(host_call.room, 1, None, "", kb, lambda *_a: None)
if M.opus_library() is not None:
    eng_b._set_voice(0, ka)
    eng_a._send(M.ROOM_TRACK["a"], b"opus frame")
    eng_b._packet(sent[0])
    voice = eng_b._voices[0]
    check("a member's packet is sealed with its own key and opened by the "
          "others (room, member, track, number in the clear)",
          b"opus frame" not in sent[0] and sent[0][9] == 0
          and sent[0][10] == 0x61 and eng_b.received == 1
          and voice.jitter._frames.get(1, (None,))[0] == b"opus frame")
    eng_b._packet(sent[0])
    tampered = sent[0][:-1] + bytes([sent[0][-1] ^ 1])
    eng_b._packet(tampered)
    eng_a._send(M.ROOM_TRACK["a"], b"next")
    wrong_room = sent[1][:1] + b"z" * 8 + sent[1][9:]
    eng_b._packet(wrong_room)
    check("one played back again, altered, or for another room: refused",
          eng_b.received == 1)
    eng_b._packet(sent[1])
    check("...and the next one is taken", eng_b.received == 2)
else:
    print("  (no libopus here: the packet checks are skipped)")
for engine in (eng_a, eng_b):
    engine._close()

print("\nThe way, judged in a group call (17.2, F17)")
ways = []
judge = M.RoomEngine(host_call.room, 0, None, "", ka,
                     lambda kind, value: ways.append(value)
                     if kind == "way" else None)
heard = M._RoomVoice(kb, M.ROOM_PCM)
judge._voices[1] = heard
judge.packet_ms, judge.relay_ok, judge.rtt_ms = 10, True, 40.0


def window(packets, lost, jitter_ms):
    """Four seconds of a member's packets, as heard here: the way, and the
    length this device then sends."""
    heard.top += packets + lost
    heard.count += packets
    heard.jitter_ms = jitter_ms
    judge._judged_at = -judge.JUDGE_EVERY
    judge._judge(0.0)
    return judge.way, judge._send_ms()


check("whole, even, and the relay 40 ms away: fine, Prefer latency's 10 ms "
      "packets become 5", window(400, 0, 1.0) == ("fine", 5))
check("5% lost: poor, 20 ms packets", window(380, 20, 1.0) == ("poor", 20))
check("jitter over 10 ms: poor", window(400, 0, 14.0)[0] == "poor")
check("too little heard to tell: ok, as Prefer has it",
      window(40, 0, 1.0) == ("ok", 10))
judge.rtt_ms = None
check("...and fine needs the relay's answer timed", window(400, 0, 1.0)[0]
      == "ok")
judge.rtt_ms, judge.packet_ms = 40.0, 20
check("Prefer quality keeps its 20 ms when fine", window(400, 0, 1.0) ==
      ("fine", 20))
check("...each judgement said", ways[-1] == "fine" and len(ways) == 6, ways)
judge._close()
top = M.VIDEO_LADDER.index(M.video_ceiling())
steps, step = [], 0
for way in ("poor", "poor", "poor", "poor", "fine", "ok", "fine", "fine"):
    step = M.room_video_step(step, way)
    steps.append(step)
check("the camera a rung lower each time the way is poor, down to the "
      "ladder's foot; back up a rung each time it is fine, never above "
      "Settings' video quality",
      steps == [min(n, top) for n in (1, 2, 3, 4)] +
      [min(4, top) - 1, min(4, top) - 1, min(4, top) - 2,
       max(0, min(4, top) - 3)] and
      M.room_camera_top(0) is M.video_ceiling() and
      M.room_camera_top(top) is M.VIDEO_LADDER[0], (top, steps))
import array
loud = array.array("h", [30000, -30000, 100]).tobytes()
mixed = array.array("h", M.pcm_mix([loud, loud, loud]))
check("voices are summed, and clipped rather than wrapped",
      list(mixed) == [32767, -32768, 300]
      and M.pcm_mix([loud]) == loud)
w = M.ReplayWindow()
w.mark(5)
w.mark(3)
check("the replay window: seen numbers refused, new ones taken, ones far "
      "behind refused", not w.fresh(5) and not w.fresh(3) and w.fresh(4)
      and w.fresh(6) and (w.mark(1000) or not w.fresh(400)))

print("\nThe sound's packets: 10 ms, or 20 (17.0)")
sess = M.new_group_call(GROUP, ME, video=False)
sess.members[ANN] = M.CallMember(ANN)
sess.members[ANN].packet_ms = 10
check("Prefer latency: 10 ms packets", M.room_packet_ms(sess) == 10)
M.save_pref("call_latency", "quality")
check("Prefer quality: 20", M.room_packet_ms(sess) == 20)
M.save_pref("call_latency", "latency")
sess.members[BEN] = M.CallMember(BEN)          # an app before 17.0
check("anyone on an app before 17.0 (it mixes 20 ms at a time): 20",
      M.room_packet_ms(sess) == 20)
st = M.CallMember.from_state(dict(M.CallMember(ANN).state(), ms=10))
old = M.CallMember.from_state(M.CallMember(BEN).state())
check("an app says its packets in its state; one before 17.0 says none",
      st.packet_ms == 10 and old.packet_ms is None)
if M.opus_library() is not None:
    def starts_after(ms):
        """How many packets of `ms` a voice holds before it plays."""
        jitter = M._RoomVoice(os.urandom(32), M.ROOM_PCM).jitter
        for n in range(1, 10):
            jitter.put(n, b"x", ms)
            if jitter.get() is not None:
                return n
    check("a voice plays once 30 ms of it are in hand: three packets of 10 "
          "ms, two of 20 (16.x waited for 60 ms)",
          (starts_after(10), starts_after(20)) == (3, 2),
          (starts_after(10), starts_after(20)))

    def starts_after_heard(ms):
        """...as a room hears them (17.2): its buffer follows their length."""
        voice = M._RoomVoice(os.urandom(32), M.ROOM_PCM)
        voice.follow_packets(ms)
        for n in range(1, 10):
            voice.jitter.put(n, b"x", ms)
            if voice.jitter.get() is not None:
                return n
    heard_ms = [starts_after_heard(ms) for ms in (5, 10, 20)]
    check("17.2 (F17): it starts at three of their packets, 15 to 30 ms: 5 "
          "ms packets (a fine way) play from 15 ms in hand, 10 and 20 ms as "
          "before", heard_ms == [3, 3, 2], heard_ms)


class Tone(M.CallAudio):
    """A microphone that hums, in real time; a speaker that keeps what it
    is given."""

    def __init__(self, level):
        self.level, self.played, self._sent, self._t0 = level, [], 0, None

    def start(self, fmt=M.CALL_PCM, capture=True):
        self.fmt, self._t0 = fmt, time.monotonic()

    def read(self):
        due = int((time.monotonic() - self._t0) * 1000 / self.fmt.frame_ms)
        n = self.fmt.frame_bytes // 2
        out = []
        while self._sent < due:
            out.append(array.array("h", [self.level if i % 48 < 24 else
                                         -self.level for i in range(n)])
                       .tobytes())
            self._sent += 1
        return out[-20:]

    def write(self, pcm):
        self.played.append(pcm)

    def queued(self):
        return 0


if M.opus_library() is not None:
    room = "0123456789abcdef"
    keys = {i: os.urandom(32) for i in range(3)}
    tones = {0: Tone(9000), 1: Tone(7000), 2: Tone(0)}
    engines = {2: M.RoomEngine(room, 2, None, "", keys[2], lambda *_a: None,
                               audio_factory=lambda: tones[2])}
    hub = engines[2]
    for i in (0, 1):            # each hands its packets to member 2
        engines[i] = M.RoomEngine(
            room, i, None, "", keys[i], lambda *_a: None,
            send_ws=lambda _tag, packet: hub.ws_packet(packet) or True,
            audio_factory=lambda i=i: tones[i])
        engines[i].relay_ok = True       # as if by UDP: no 20 ms for it
        hub.set_voice(i, keys[i])
    engines[0].set_packet_ms(10)
    engines[1].set_packet_ms(20)
    sizes = {0: [], 1: []}
    for i in (0, 1):
        real = engines[i]._send

        def spy(track, payload, i=i, real=real):
            sizes[i].append(M.opus_packet_ms(payload))
            real(track, payload)
        engines[i]._send = spy
        engines[i].set_mic(True)
    for e in engines.values():
        e.start()
    time.sleep(1.5)
    for e in engines.values():
        e.stop()
    for e in engines.values():
        e.join(timeout=2)
    played = b"".join(tones[2].played)
    check("one member's packets are 10 ms long, another's 20",
          set(sizes[0][5:]) == {10} and set(sizes[1][5:]) == {20},
          (set(sizes[0]), set(sizes[1])))
    check("...and a third hears both, mixed 5 ms at a time, without gaps",
          len(played) >= M.ROOM_PCM.bytes_for(900) and
          max(array.array("h", played[-9600:])) > 9000 and
          all(any(chunk) for chunk in (played[i:i + 480] for i in range(
              len(played) - 48000, len(played) - 480, 480))),
          (len(played), max(array.array("h", played[-9600:]) or [0]),
           [i for i in range(len(played) - 48000, len(played) - 480, 480)
            if not any(played[i:i + 480])][:8]))

passed = sum(1 for _n, ok in RESULTS if ok)
print(f"\n  {passed}/{len(RESULTS)} passed")
sys.exit(0 if passed == len(RESULTS) else 1)
