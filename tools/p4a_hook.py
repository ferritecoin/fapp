"""python-for-android build hook: declare the sync service's type.

Android 14 (API 34) refuses to start a foreground service whose manifest
entry has no android:foregroundServiceType, and python-for-android's
manifest template (v2024.01.21) has no way to set one. This runs after p4a
has rendered AndroidManifest.xml and before Gradle packages it.

'dataSync' is the type for "fetch data / sync" work; its matching
permission, FOREGROUND_SERVICE_DATA_SYNC, is requested in buildozer.spec.
"""
import re
from pathlib import Path

SERVICE = "ServiceFextsync"
TYPE = "dataSync"


def before_apk_assemble(toolchain):
    manifest = Path("src/main/AndroidManifest.xml")
    text = manifest.read_text(encoding="utf-8")
    if f'{SERVICE}" android:foregroundServiceType' in text:
        return
    text, count = re.subn(
        r'(<service\s+android:name="[^"]*' + SERVICE + r'")',
        r'\1 android:foregroundServiceType="' + TYPE + '"', text)
    if count != 1:
        raise RuntimeError(f"p4a_hook: expected one <service> for {SERVICE}, "
                           f"found {count} — the service will not start on "
                           f"Android 14")
    manifest.write_text(text, encoding="utf-8")
    print(f"p4a_hook: {SERVICE} declared foregroundServiceType={TYPE}")
