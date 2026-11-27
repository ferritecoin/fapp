#!/bin/bash
# An Android emulator of one API level, booted headless and ready for adb
# (17.2, .github/workflows/devices.yml). It uses the Android SDK the GitHub
# runner already has; KVM must be enabled first (the workflow does it).
#
#   tools/devices/emulator.sh API_LEVEL
#
# The image is x86_64, Google APIs where Google publishes one for that
# level, else the plain one. The emulated cameras show a moving test scene.
set -euo pipefail
API="$1"
SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:?no Android SDK}}"
BIN="$SDK/cmdline-tools/latest/bin"

LOG="${RUNNER_TEMP:-/tmp}/emulator-$API.log"
say() { echo "$(date +%T) $*"; }      # each step timed, in the job's log
# One folder for the AVD, for both tools: avdmanager's own default moved
# (run 3: "Unknown AVD name"), the emulator's did not.
export ANDROID_AVD_HOME="$HOME/.android/avd"
mkdir -p "$ANDROID_AVD_HOME"

say "the emulator and platform tools"
yes | "$BIN/sdkmanager" --licenses > /dev/null || true
"$BIN/sdkmanager" --install emulator platform-tools > /dev/null
IMAGE=""
for target in google_apis default; do
  candidate="system-images;android-$API;$target;x86_64"
  say "trying $candidate"
  if "$BIN/sdkmanager" --install "$candidate" > /dev/null 2>&1; then
    IMAGE="$candidate"; break
  fi
done
[ -n "$IMAGE" ] || { echo "::error::no x86_64 image for API $API"; exit 1; }
say "image: $IMAGE"

echo no | "$BIN/avdmanager" create avd --force -n "api$API" -k "$IMAGE" \
  -d pixel_5 > /dev/null
# A 4 GB data partition: the self-test needs little, and the image's own
# default (7 GB for API 36) did not fit on the runner (run 44: "Not enough
# space to create userdata partition. Available: 6981.93 MB"). The emulator
# still asked for 7.4 GB in run 52, so the workflow makes room first.
"$SDK/emulator/emulator" -avd "api$API" -no-window -no-snapshot -noaudio \
  -no-boot-anim -gpu swiftshader_indirect -memory 3072 -partition-size 4096 \
  -camera-back emulated -camera-front emulated > "$LOG" 2>&1 &
EMULATOR=$!
say "booting"

# Not adb's own wait-for-device, which waits for ever: ten minutes at the
# most, and at once if the emulator stopped.
ADB="$SDK/platform-tools/adb"
booted=""
for _ in $(seq 300); do
  kill -0 "$EMULATOR" 2>/dev/null || {
    echo "::error::the emulator stopped"; tail -60 "$LOG"; exit 1; }
  booted="$("$ADB" shell getprop sys.boot_completed 2>/dev/null \
            | tr -d '\r' || true)"           # no device yet: not booted
  [ "$booted" = 1 ] && break
  sleep 2
done
[ "$booted" = 1 ] || {
  echo "::error::API $API did not boot in ten minutes"; tail -60 "$LOG"; exit 1; }
say "booted"
# Settled: the launcher in front, as a person finds a phone. Booted, the
# system still starts its own apps for a while (run 10, API 36: the app's
# window was taken away a second into its self-test).
# A system dialog in front (run 13, API 30: "Application Not Responding:
# com.android.systemui", which then stayed over the app) is closed first.
for _ in $(seq 60); do                      # two minutes at the most
  focus="$("$ADB" shell dumpsys window 2>/dev/null | grep -m1 mCurrentFocus)"
  echo "$focus" | grep -qi launcher && break
  if echo "$focus" | grep -qiE "Not Responding|isn't responding"; then
    say "closing a system dialog: $(echo "$focus" | tr -d '\r')"
    "$ADB" shell am broadcast -a android.intent.action.CLOSE_SYSTEM_DIALOGS \
      > /dev/null 2>&1 || true
    "$ADB" shell input keyevent KEYCODE_HOME || true
  fi
  sleep 2
done
say "settled: $("$ADB" shell dumpsys window 2>/dev/null \
                 | grep -m1 mCurrentFocus | tr -d '\r')"
# Run 16 (API 30, twice): the system's own UI did not answer for the two
# minutes, its dialog back each time it was closed, and the app's video
# checks then failed under it (as in run 13). The run goes on, saying so.
if "$ADB" shell dumpsys window 2>/dev/null | grep -m1 mCurrentFocus \
     | grep -qiE "Not Responding|isn't responding"; then
  echo "::warning::the emulator's own system UI is not responding (this runner is too slow for it): what fails below may be that, not the app; run the job again"
fi
# And quiet: no change to the system's configuration for 45 s. A minute or
# two after booting, API 36's emulator still applies its overlays (Config
# changes=80000000, which no app may take in place), and an app on screen
# is then recreated, which an SDL app cannot outlive (runs 10, 12, 14).
"$ADB" logcat -c 2>/dev/null || true
quiet=0
for _ in $(seq 120); do                     # four minutes at the most
  if "$ADB" logcat -d 2>/dev/null | grep -q "Config changes="; then
    "$ADB" logcat -c 2>/dev/null || true
    quiet=0
  else
    quiet=$((quiet + 2))
    [ "$quiet" -ge 45 ] && break
  fi
  sleep 2
done
say "quiet for ${quiet}s"
# Awake, unlocked, and no animations to wait for.
"$ADB" shell input keyevent 82 || true
for scale in window_animation_scale transition_animation_scale \
             animator_duration_scale; do
  "$ADB" shell settings put global "$scale" 0 || true
done
echo "Android $("$ADB" shell getprop ro.build.version.release | tr -d '\r')" \
     "(API $API) is up"
