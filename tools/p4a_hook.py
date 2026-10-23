"""python-for-android build hook: the manifest entries p4a cannot express.

This runs after p4a has rendered AndroidManifest.xml and before Gradle
packages it; python-for-android's manifest template (v2024.01.21) has no
option for either of these.

* The services' types. Android 14 (API 34) refuses to start a foreground
  service whose manifest entry has no android:foregroundServiceType. The
  sync service is 'dataSync' (fetch data / sync work); the call service is
  'microphone', which is what lets a call's microphone stay on while the
  app is off screen. Their permissions, FOREGROUND_SERVICE_DATA_SYNC and
  FOREGROUND_SERVICE_MICROPHONE, are requested in buildozer.spec.

* The FileProvider through which the camera app hands over a photo: it
  writes straight into the app's private cache (android_take_photo in
  main.py), so a photo taken for a chat never passes through the gallery.
  Its folder list is res/xml/fext_file_paths.xml (android.add_resources)
  and the class comes from androidx.core (android.gradle_dependencies).

* The receiver for what is done to a notification from the shade — a
  reply typed into it, Mark as read, Decline on a call — which hands each
  to the app as a small file (android/NotifyReceiver.java). Not
  exported: only the app's own notifications can reach it.
"""
import re
from pathlib import Path

SERVICE_TYPES = {"ServiceFextsync": "dataSync",
                 "ServiceFextcall": "microphone"}

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


def before_apk_assemble(toolchain):
    manifest = Path("src/main/AndroidManifest.xml")
    text = manifest.read_text(encoding="utf-8")
    new = declare_notify_receiver(
        declare_file_provider(declare_service_types(text)))
    if new != text:
        manifest.write_text(new, encoding="utf-8")
