"""python-for-android build hook: the manifest entries p4a cannot express.

This runs after p4a has rendered AndroidManifest.xml and before Gradle
packages it; python-for-android's manifest template (v2024.01.21) has no
option for either of these.

* The sync service's type. Android 14 (API 34) refuses to start a
  foreground service whose manifest entry has no
  android:foregroundServiceType. 'dataSync' is the type for "fetch data /
  sync" work; its matching permission, FOREGROUND_SERVICE_DATA_SYNC, is
  requested in buildozer.spec.

* The FileProvider through which the camera app hands over a photo: it
  writes straight into the app's private cache (android_take_photo in
  main.py), so a photo taken for a chat never passes through the gallery.
  Its folder list is res/xml/fext_file_paths.xml (android.add_resources)
  and the class comes from androidx.core (android.gradle_dependencies).
"""
import re
from pathlib import Path

SERVICE = "ServiceFextsync"
TYPE = "dataSync"

PROVIDER = """
        <provider android:name="androidx.core.content.FileProvider"
                  android:authorities="${applicationId}.fileprovider"
                  android:exported="false"
                  android:grantUriPermissions="true">
            <meta-data android:name="android.support.FILE_PROVIDER_PATHS"
                       android:resource="@xml/fext_file_paths" />
        </provider>
"""


def declare_service_type(text: str) -> str:
    if f'{SERVICE}" android:foregroundServiceType' in text:
        return text
    text, count = re.subn(
        r'(<service\s+android:name="[^"]*' + SERVICE + r'")',
        r'\1 android:foregroundServiceType="' + TYPE + '"', text)
    if count != 1:
        raise RuntimeError(f"p4a_hook: expected one <service> for {SERVICE}, "
                           f"found {count} — the service will not start on "
                           f"Android 14")
    print(f"p4a_hook: {SERVICE} declared foregroundServiceType={TYPE}")
    return text


def declare_file_provider(text: str) -> str:
    if "androidx.core.content.FileProvider" in text:
        return text
    if text.count("</application>") != 1:
        raise RuntimeError("p4a_hook: expected one </application> to put the "
                           "camera's FileProvider in")
    print("p4a_hook: declared the camera's FileProvider")
    return text.replace("</application>", PROVIDER + "    </application>")


def before_apk_assemble(toolchain):
    manifest = Path("src/main/AndroidManifest.xml")
    text = manifest.read_text(encoding="utf-8")
    new = declare_file_provider(declare_service_type(text))
    if new != text:
        manifest.write_text(new, encoding="utf-8")
