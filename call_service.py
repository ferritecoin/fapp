"""Ferritext call service — Android only.

A call's sound is recorded and played by the app itself (main.CallEngine
and src/android/java/org/fext/call/CallAudio.java). Android lets an app
keep using the microphone after it leaves the screen only while that app
runs a foreground service of the 'microphone' type, so for the length of a
call the app starts this one (main.android_call_service), declared in
buildozer.spec as

    services = …, Fextcall:call_service.py:foreground

and typed by tools/p4a_hook.py. It shows the "Call in progress"
notification and does nothing else: it waits while the flag file the app
named exists, and ends — taking the notification with it — when the app
removes the file or stops it. It never imports main.py, so it starts at
once and costs almost nothing.
"""
import os
import time

FLAG = os.environ.get("PYTHON_SERVICE_ARGUMENT", "")
LONGEST_CALL = 6 * 3600            # a forgotten flag cannot keep it forever

started = time.time()
while FLAG and os.path.exists(FLAG) and time.time() - started < LONGEST_CALL:
    time.sleep(1.0)
