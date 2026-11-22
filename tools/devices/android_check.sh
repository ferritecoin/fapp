#!/bin/bash
# The app on one running emulator (17.2): installed with its permissions,
# started with the self-test (main.py: android_selftest, debug builds only),
# its verdicts read from logcat, a screenshot and the logs kept in OUT_DIR.
# The self-test's checks: call-classes, call-audio, codecs-encoders,
# codecs-decoders, camera, (18.0) screen, (18.1) video-message, (18.2)
# video-draw (a decoder's rows drawn by the graphics chip), and (18.1)
# back: Android's Back key, pressed here, as the app took it.
#
#   tools/devices/android_check.sh APP_DEBUG.apk OUT_DIR
#
# Fails on a FAIL verdict, no verdict within five minutes, the app gone, or
# a Java crash. A Python traceback the app caught and logged is listed
# (tracebacks.txt) and warned about, not failed on.
set -uo pipefail
APK="$1"; OUT="$2"; PKG=org.fext.fext
ADB="${ANDROID_HOME:-$ANDROID_SDK_ROOT}/platform-tools/adb"
mkdir -p "$OUT"

SDK="$("$ADB" shell getprop ro.build.version.sdk | tr -d '\r')"
# Before Android 7 (API 24) adb's shell gives no exit codes, so a failed
# install "succeeds" (run 7, API 21: "Activity class does not exist"); and
# before Android 6 (API 23) permissions come with the install, and pm
# knows no -g. So -g only where it exists, and the package looked for.
GRANT=""; [ "$SDK" -ge 23 ] && GRANT="-g"
# Each step with a time limit, so a hang says which step it was (run 8,
# API 21: the job sat until its own 45 minutes, saying nothing).
timeout 300 "$ADB" install -r $GRANT "$APK" > "$OUT/install.txt" 2>&1 || \
  echo "::warning::adb install: exit $? (124: five minutes, no answer)"
cat "$OUT/install.txt"
"$ADB" shell pm path "$PKG" | grep -q '^package:' || {
  echo "::error::the APK did not install (install.txt)"; exit 1; }
# 18.0: the self-test shares the screen, which asks the person first. With
# the PROJECT_MEDIA app-op allowed, Android's dialog says yes by itself.
# From Android 6, where calls (and so the screen check) run: Android 5's
# appops knows no such name.
if [ "$SDK" -ge 23 ]; then
  "$ADB" shell appops set "$PKG" PROJECT_MEDIA allow || \
    echo "::warning::could not allow PROJECT_MEDIA: the screen check will wait"
fi
# Awake and unlocked, as a phone is when someone opens the app: since the
# boot the screen may have timed out, and an activity behind a dark screen
# gets no surface (run 5, API 36: "Could not fetch native window").
awake() {
  "$ADB" shell svc power stayon true || true
  "$ADB" shell input keyevent KEYCODE_WAKEUP || true
  if [ "$SDK" -ge 26 ]; then                 # Android 8 and later
    "$ADB" shell wm dismiss-keyguard || true
  else                                       # the menu key opens a lock
    "$ADB" shell input keyevent 82 || true   # screen that has no PIN
  fi
}
awake
# What Android has in front, for the job's log (run 23, API 36: Back did
# not reach the app, and the kept files cannot always be read).
front() {
  timeout 30 "$ADB" shell dumpsys window 2>/dev/null \
    | grep -E 'mCurrentFocus|mFocusedApp' | head -2 | sed 's/^ */  /'
  # A keyboard on the screen takes the first Back for itself.
  timeout 30 "$ADB" shell dumpsys input_method 2>/dev/null \
    | grep -o 'mInputShown=[a-z]*' | head -1 | sed 's/^/  /'
}
# The system's own "isn't responding" dialog (a runner too slow for the
# emulator's system UI; runs 23 and 24) takes every key meant for the
# app. Its Wait is pressed, found by uiautomator (Android 7 on).
NOT_RESPONDING="Not Responding|isn't responding"
press_wait() {
  timeout 30 "$ADB" shell uiautomator dump /sdcard/fext_ui.xml \
    > /dev/null 2>&1 || return 0
  set -- $(timeout 30 "$ADB" shell cat /sdcard/fext_ui.xml 2>/dev/null \
           | grep -o 'android:id/aerr_wait"[^>]*bounds="[^"]*"' \
           | grep -o '[0-9]*,[0-9]*\]\[[0-9]*,[0-9]*' | tr '][,' '   ')
  [ $# -eq 4 ] || return 0
  "$ADB" shell input tap $(( ($1 + $3) / 2 )) $(( ($2 + $4) / 2 )) || true
  sleep 1
}
# The runner's free disk, in MB. Run 8, API 21: it filled a minute after
# the app started, and every step after that hung.
free_mb() { df -m --output=avail / | tail -1 | tr -d ' '; }

# The app started with its self-test, and its verdict waited for (five
# minutes at the most, or until the app is gone): sets $verdict.
selftest() {
  "$ADB" logcat -c
  # -W: the launch's result, or why there was none (run 5, API 21: silence).
  timeout 120 "$ADB" shell am start -W -n "$PKG/org.kivy.android.PythonActivity" \
    --es fext_selftest 1 > "$OUT/start.txt" 2>&1 || \
    echo "::warning::am start: exit $? (124: two minutes, no answer)"
  cat "$OUT/start.txt"
  echo "free disk: $(free_mb) MB"
  verdict=""
  for n in $(seq 150); do
    verdict="$(timeout 30 "$ADB" logcat -d -s python:I 2>/dev/null \
               | grep -o 'FEXT-SELFTEST done .*' | tail -1)"
    [ -n "$verdict" ] && break
    if [ "$(free_mb)" -lt 2000 ]; then
      echo "::error::the runner's disk is filling: what grew"
      du -xsh "$HOME/.android" "${RUNNER_TEMP:-/tmp}"/* /tmp 2>/dev/null \
        | sort -h | tail -12
      du -xh --max-depth=3 "$HOME/.android" 2>/dev/null | sort -h | tail -8
      break
    fi
    # Gone, after its first half-minute: no verdict is coming.
    if [ "$n" -gt 15 ] && ! "$ADB" shell ps 2>/dev/null | grep -q "$PKG"; then
      break
    fi
    sleep 2
  done
  echo "free disk: $(free_mb) MB"
}

selftest
# Runs 10, 12 and 14 (API 36): after booting, the emulator still changed
# its overlays (Config changes=80000000, the assets' paths, which no app
# may take in place), so Android recreated the app's activity; an SDL app
# cannot be recreated: it left before its verdict, or (starting) SDL's
# Java crashed. Run 16 (API 36, a slow runner: the install took 65 s): the
# emulator's whole system died and started again seconds into the app's
# start, taking every app with it (DeadSystemException). Either way the
# self-test starts once more on the settled system; a second departure
# fails as any would.
if [ -z "$verdict" ] && ! "$ADB" shell ps 2>/dev/null | grep -q "$PKG" && \
   timeout 60 "$ADB" logcat -d 2>/dev/null \
     | grep -qE "$PKG/org\.kivy\.android\.PythonActivity[^}]*\} is relaunching|Relaunching: .*$PKG/|Config changes=80000000|DeadSystemException"; then
  timeout 60 "$ADB" logcat -d > "$OUT/logcat_first.txt" 2>&1
  echo "::warning::Android recreated the app's activity, or its own system restarted, as the emulator settled, and the app left; its self-test starts once more (logcat_first.txt)"
  for _ in $(seq 60); do               # the system back: two minutes at most
    "$ADB" shell pm path "$PKG" 2>/dev/null | grep -q '^package:' && break
    sleep 2
  done
  sleep 30
  awake                                # a restarted system locks again
  selftest
fi
sleep 3                              # the screen as the check left it
timeout 30 "$ADB" exec-out screencap -p > "$OUT/screen.png" || true
# 18.1: Back, as a thumb presses it. The self-test left a sheet open (its
# "done" is said once the sheet is) and says what the key did once it
# arrives ("back PASS|FAIL"); a key that never arrives is Android's Back
# not reaching the app at all.
before_back="$(front)"
if echo "$before_back" | grep -qiE "$NOT_RESPONDING"; then
  press_wait
  before_back="$(front)"
fi
echo "in front, the self-test over:"; echo "$before_back"
if [ -n "$verdict" ]; then
  "$ADB" shell input keyevent KEYCODE_BACK || true
  for _ in $(seq 15); do
    timeout 30 "$ADB" logcat -d -s python:I 2>/dev/null \
      | grep -q 'FEXT-SELFTEST back ' && break
    sleep 1
  done
fi
timeout 60 "$ADB" logcat -d > "$OUT/logcat.txt" 2>&1
grep -o 'FEXT-SELFTEST .*' "$OUT/logcat.txt" | tr -d '\r' > "$OUT/selftest.txt"
cat "$OUT/selftest.txt"

failed=0
[ -n "$verdict" ] || { echo "::error::no verdict in five minutes"; failed=1; }
grep -q ' FAIL' "$OUT/selftest.txt" && { echo "::error::a check failed"; failed=1; }
if [ -n "$verdict" ] && ! grep -q 'FEXT-SELFTEST back ' "$OUT/selftest.txt"; then
  if echo "$before_back" | grep -qiE "$NOT_RESPONDING"; then
    echo "::warning::Back could not be tried: the emulator's own \"not responding\" dialog stayed in front of the app (this runner is too slow for its system UI); run the job again"
  else
    echo "::error::Android's Back key did not reach the app"; failed=1
  fi
  echo "in front now:"; front
  grep -E "python +:|SDL +: (on|native)" "$OUT/logcat.txt" | tail -25
fi
"$ADB" shell ps 2>/dev/null | grep -q "$PKG" || {
  echo "::error::the app is no longer running"; failed=1; }
# Silent, or gone without a Java crash (run 4, API 21): the lines that
# say how an app dies natively, or why its libraries did not load.
if [ -z "$verdict" ]; then
  grep -E "Fatal signal|dlopen|linker|CANNOT LINK|AndroidRuntime|DEBUG +:|\
python +:|Process $PKG|has died|ActivityTaskManager|Config changes|\
relaunch|Leaving application|SDL +: (on|native)" "$OUT/logcat.txt" \
    | grep -v nativeloader \
    | tail -60 > "$OUT/why.txt"
  [ -s "$OUT/why.txt" ] || tail -40 "$OUT/logcat.txt" > "$OUT/why.txt"
  cat "$OUT/why.txt"                 # none of those: the log's end
fi
# The first of each in this log too: the files are kept only when the
# account's artifact storage has room.
if grep -A 20 "FATAL EXCEPTION" "$OUT/logcat.txt" > "$OUT/crashes.txt"; then
  echo "::error::a Java crash in the log (crashes.txt)"; failed=1
  head -25 "$OUT/crashes.txt"
fi
if grep -B 2 -A 12 "Traceback (most recent call last)" "$OUT/logcat.txt" \
     > "$OUT/tracebacks.txt"; then
  echo "::warning::$(grep -c 'Traceback' "$OUT/tracebacks.txt") traceback(s) the app logged (tracebacks.txt)"
  # (Kivy's own, at each first start, lists every icon it could not
  # copy on one line: the lines are cut for the log, whole in the file.)
  head -25 "$OUT/tracebacks.txt" | cut -c1-300
fi
# The verdicts once more, last: the end of a job's log is the part read
# first (and the part its reader can ask for by itself).
echo "The self-test said:"; cat "$OUT/selftest.txt"
exit $failed
