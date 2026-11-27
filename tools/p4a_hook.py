"""python-for-android build hook: the manifest entries p4a cannot express.

This runs after p4a has rendered AndroidManifest.xml and before Gradle
packages it; python-for-android's manifest template (v2024.01.21) has no
option for either of these.

* The services' types. Android 14 (API 34) refuses to start a foreground
  service whose manifest entry has no android:foregroundServiceType. The
  sync service is 'dataSync' (fetch data / sync work); the call service is
  'microphone', which is what lets a call's microphone stay on while the
  app is off screen; 18.0's share service is 'mediaProjection', which
  Android wants running while the screen is shared; 20.0's camera service
  is 'camera', which lets a call's camera stay on off screen. It is a
  service of its own, not a second type of the call service: Android 14
  refuses to start a service of a type whose permission (the camera's)
  the person has not given, and a voice call needs none. Their
  permissions, FOREGROUND_SERVICE_DATA_SYNC, FOREGROUND_SERVICE_MICROPHONE,
  FOREGROUND_SERVICE_MEDIA_PROJECTION and FOREGROUND_SERVICE_CAMERA, are
  requested in buildozer.spec.

* The FileProvider through which the camera app hands over a photo: it
  writes straight into the app's private cache (android_take_photo in
  main.py), so a photo taken for a chat never passes through the gallery.
  Its folder list is res/xml/fext_file_paths.xml (android.add_resources)
  and the class comes from androidx.core (android.gradle_dependencies).

* The receiver for what is done to a notification from the shade — a
  reply typed into it, Mark as read, Decline on a call — which hands each
  to the app as a small file (android/NotifyReceiver.java). Not
  exported: only the app's own notifications can reach it.

* 17.2: the app's activity may shrink to picture in picture (a video
  call over other apps, main.py: android_pip).

* 20.0: the keyboard's backspace (KEEP_BACKSPACE). SDL's Java, which p4a
  copies into the build, takes what a keyboard deletes from the text the
  keyboard itself typed since the field took it. Text the app put in the
  field is not in it (a draft, an emoji from the app's own panel, a
  message being edited, anything typed before the keyboard was hidden
  and shown again): on Android 11 and later nothing was deleted, and
  backspace "sometimes did not work". What SDL cannot delete from its
  own text now goes to the app as backspaces.

* 21.1: a Back no view takes (BACK_TO_APP). SDL hands the app the keys
  its views take; a Back that finds no view with the focus goes on to
  Android, whose onBackPressed (SDL's, then Android's own) closed the app
  from whatever page: the owner's "Back exits all the way out". It now
  goes to the app as the Back key, and the app decides (main.py,
  _step_back: a step back; on the outermost page, home).
"""
import re
from pathlib import Path

SERVICE_TYPES = {"ServiceFextsync": "dataSync",
                 "ServiceFextcall": "microphone",
                 "ServiceFextshare": "mediaProjection",
                 "ServiceFextcam": "camera",
                 "ServiceFextloc": "location"}

PROVIDER = """
        <provider android:name="androidx.core.content.FileProvider"
                  android:authorities="${applicationId}.fileprovider"
                  android:exported="false"
                  android:grantUriPermissions="true">
            <meta-data android:name="android.support.FILE_PROVIDER_PATHS"
                       android:resource="@xml/fext_file_paths" />
        </provider>
"""


RECEIVER = """
        <receiver android:name="org.fext.notify.NotifyReceiver"
                  android:exported="false" />
"""


def declare_service_type(text: str, service: str, kind: str) -> str:
    if f'{service}" android:foregroundServiceType' in text:
        return text
    text, count = re.subn(
        r'(<service\s+android:name="[^"]*' + service + r'")',
        r'\1 android:foregroundServiceType="' + kind + '"', text)
    if count != 1:
        raise RuntimeError(f"p4a_hook: expected one <service> for {service}, "
                           f"found {count} — the service will not start on "
                           f"Android 14")
    print(f"p4a_hook: {service} declared foregroundServiceType={kind}")
    return text


def declare_service_types(text: str) -> str:
    for service, kind in SERVICE_TYPES.items():
        text = declare_service_type(text, service, kind)
    return text


def declare_file_provider(text: str) -> str:
    if "androidx.core.content.FileProvider" in text:
        return text
    if text.count("</application>") != 1:
        raise RuntimeError("p4a_hook: expected one </application> to put the "
                           "camera's FileProvider in")
    print("p4a_hook: declared the camera's FileProvider")
    return text.replace("</application>", PROVIDER + "    </application>")


def declare_notify_receiver(text: str) -> str:
    if "org.fext.notify.NotifyReceiver" in text:
        return text
    if text.count("</application>") != 1:
        raise RuntimeError("p4a_hook: expected one </application> to put the "
                           "notification receiver in")
    print("p4a_hook: declared the notification receiver")
    return text.replace("</application>", RECEIVER + "    </application>")


def declare_picture_in_picture(text: str) -> str:
    if "android:supportsPictureInPicture" in text:
        return text
    text, count = re.subn(
        r'(<activity\s+android:name="org\.kivy\.android\.PythonActivity")',
        r'\1 android:supportsPictureInPicture="true"'
        r' android:resizeableActivity="true"', text)
    if count != 1:
        raise RuntimeError(f"p4a_hook: expected one PythonActivity, found "
                           f"{count} — a video call could not float")
    print("p4a_hook: the activity may go to picture in picture")
    return text


#: SDL 2.28's SDLInputConnection.deleteSurroundingText: the line the
#: keyboard's delete reaches on Android 11 and later, and what goes before
#: it. `fextBefore`: how much of the keyboard's own text is before the
#: cursor; a delete of more than that is the app's text.
SDL_ACTIVITY = Path("src/main/java/org/libsdl/app/SDLActivity.java")
BACKSPACE_AT = ("        if (!super.deleteSurroundingText(beforeLength, "
                "afterLength)) {\n")
KEEP_BACKSPACE = """\
        // Ferritext (tools/p4a_hook.py): what the keyboard deletes beyond
        // the text it typed itself is the app's, and goes on as backspaces.
        if (afterLength == 0) {
            Editable fextHeld = getEditable();
            int fextBefore = fextHeld == null ? 0
                    : Math.max(0, Selection.getSelectionStart(fextHeld));
            for (int fextLeft = beforeLength - fextBefore; fextLeft > 0;
                    fextLeft--) {
                nativeGenerateScancodeForUnichar('\\b');
            }
        }
"""


def keep_backspace(java: str) -> str:
    if "fextBefore" in java:
        return java
    if java.count(BACKSPACE_AT) != 1 or \
            "nativeGenerateScancodeForUnichar" not in java:
        raise RuntimeError("p4a_hook: SDL's deleteSurroundingText is not as "
                           "expected (another SDL?) — backspace would "
                           "sometimes do nothing; see KEEP_BACKSPACE")
    print("p4a_hook: the keyboard's backspace reaches the app's own text")
    return java.replace(BACKSPACE_AT, KEEP_BACKSPACE + BACKSPACE_AT)


#: 21.1: SDL's onBackPressed, which Android calls for a Back no view took:
#: it goes to the app as the Back key first (onNativeKeyDown and Up, as a
#: keyboard's Back goes); Android's own Back only while SDL's library is
#: not yet loaded.
BACK_AT = re.compile(r"public void onBackPressed\(\)\s*\{\n")
BACK_TO_APP = """\
        // Ferritext (tools/p4a_hook.py): a Back no view took goes to the
        // app as the key all the same, and the app decides; Android's own
        // closed the app from whatever page.
        try {
            onNativeKeyDown(android.view.KeyEvent.KEYCODE_BACK);
            onNativeKeyUp(android.view.KeyEvent.KEYCODE_BACK);
            return;
        } catch (Throwable fextNotYet) {
            // SDL's library not loaded yet: Android's Back, as before
        }
"""


def back_to_app(java: str) -> str:
    if "fextNotYet" in java:
        return java
    if len(BACK_AT.findall(java)) != 1 or \
            "void onNativeKeyDown(int" not in java:
        raise RuntimeError("p4a_hook: SDL's onBackPressed is not as expected "
                           "(another SDL?) — a Back no view takes would "
                           "close the app; see BACK_TO_APP")
    print("p4a_hook: a Back no view takes reaches the app")
    return BACK_AT.sub(lambda m: m.group(0) + BACK_TO_APP, java, count=1)


def before_apk_assemble(toolchain):
    manifest = Path("src/main/AndroidManifest.xml")
    text = manifest.read_text(encoding="utf-8")
    new = declare_picture_in_picture(declare_notify_receiver(
        declare_file_provider(declare_service_types(text))))
    if new != text:
        manifest.write_text(new, encoding="utf-8")
    java = SDL_ACTIVITY.read_text(encoding="utf-8")
    patched = back_to_app(keep_backspace(java))
    if patched != java:
        SDL_ACTIVITY.write_text(patched, encoding="utf-8")
