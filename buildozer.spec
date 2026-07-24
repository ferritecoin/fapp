[app]

# (str) Title of your application
title = FEXT

# (str) Package name
package.name = fext

# (str) Package domain (needed for android/ios packaging)
package.domain = org.fext

# (str) Source code where the main.py live
source.dir = .

# (list) Source files to include (let empty to include all the files)
source.include_exts = py,png,jpg,kv,atlas,ttf

# (list) List of inclusions using pattern matching
#source.include_patterns = assets/*,images/*.png

# (list) Source files to exclude (let empty to not exclude anything)
source.exclude_exts = spec

# (list) List of directory to exclude (let empty to not exclude anything)
source.exclude_dirs = tests, bin, venv, .git, .github, .buildozer, __pycache__

# (str) Application versioning (method 1)
version = 7.1.0

# (list) Application requirements
# comma separated e.g. requirements = sqlite3,kivy
# NOTE: sqlite3 ships with python3-for-android automatically.
requirements = python3,kivy,pillow,requests,urllib3,charset-normalizer,idna,certifi,websocket-client,cryptography,qrcode,pyjnius,android

# (str) Presplash of the application
#presplash.filename = %(source.dir)s/data/presplash.png

# (str) Icon of the application
#icon.filename = %(source.dir)s/data/icon.png

# (str) Supported orientation (one of landscape, sensorLandscape, portrait or all)
orientation = portrait

# (bool) Indicate if the application should be fullscreen or not
fullscreen = 0

#
# Android specific
#

# (list) Permissions
# The app is a networked E2E messenger: it needs the internet, and it checks
# connectivity state to drive its online/offline status ribbon.
android.permissions = INTERNET,ACCESS_NETWORK_STATE

# (list) Put these files or directories in the apk res directory.
# Ships res/xml/network_security_config.xml, which permits cleartext for the
# relay host ONLY. The v7.1 client probes https:// and falls back to http://
# for relays without TLS; Android (API 28+) blocks cleartext by default, so
# without this the platform refuses the connection before any Python runs and
# the client-side fallback cannot help. Scoped to the one host in preference
# to a blanket android:usesCleartextTraffic="true".
android.add_resources = src/android/res/xml/network_security_config.xml:xml/network_security_config.xml

# NOTE: the <application> attribute that points at the config above is set via
# p4a.extra_args at the bottom of this file, NOT via
# android.extra_manifest_application_arguments. Buildozer 1.5.0 escapes the
# quotes in that option's file (" -> \") and then wraps the whole value in
# another pair of quotes, and because p4a is invoked without a shell those
# characters land verbatim in AndroidManifest.xml — which then fails to parse
# ("ManifestMerger2$MergeFailureException: Error parsing AndroidManifest.xml").
# p4a.extra_args is shlex-split, so the attribute survives intact.

# (int) Target Android API, should be as high as possible.
android.api = 34

# (int) Minimum API your APK / AAB will support.
android.minapi = 21

# (str) Android NDK version to use
#android.ndk = 25b

# (int) Android NDK API to use. This is the minimum API your app will support.
android.ndk_api = 21

# (bool) Use --private data storage (True) or --dir public storage (False)
#android.private_storage = True

# (bool) If True, then automatically accept SDK license
# agreements. This is intended for automation only. If set to False,
# the default, you will be shown the license when first running
# buildozer.
android.accept_sdk_license = True

# (str) The Android arch to build for, choices: armeabi-v7a, arm64-v8a, x86, x86_64
# Two ABIs cover essentially all physical phones (arm64) plus older/emulated
# 32-bit devices (armeabi-v7a).
android.archs = arm64-v8a, armeabi-v7a

# (bool) enables Android auto backup feature (Android API >=23)
android.allow_backup = True

# (str) The format used to package the app for release mode (aab or apk or aar).
# We ship a directly-installable APK, not a Play-Store bundle.
android.release_artifact = apk

# (str) The format used to package the app for debug mode (apk).
android.debug_artifact = apk

#
# Python for android (p4a) specific
#

# (str) The name of the bootstrap to use
p4a.bootstrap = sdl2

# (str) python-for-android branch/tag to use.
# Pinned to a stable release: p4a's default (master) now targets CPython 3.14,
# whose removed private C-API (_PyUnicode_FastCopyCharacters,
# _PyInterpreterState_GetConfig) makes Kivy 2.3.x fail to compile. v2024.01.21
# targets Python 3.11.5 + Kivy 2.3.0 + OpenSSL-based cryptography 2.8 (no Rust),
# a known-good, widely-used combination.
p4a.branch = v2024.01.21

# (str) extra command line arguments to pass when invoking p4a.
# Points <application> at res/xml/network_security_config.xml (shipped via
# android.add_resources), which permits cleartext for the relay host only so
# the v7.1 https->http fallback is not blocked by the platform. This is set
# here rather than through android.extra_manifest_application_arguments
# because that option mangles the quotes (see the note above); p4a.extra_args
# is shlex-split, so the single quotes below keep the inner double quotes.
p4a.extra_args = --extra-manifest-application-arguments='android:networkSecurityConfig="@xml/network_security_config"'

#
# Buildozer
#

# (int) Log level (0 = error only, 1 = info, 2 = debug (with command output))
log_level = 2

# (int) Display warning if buildozer is run as root (0 = False, 1 = True)
warn_on_root = 1


[buildozer]

# (str) Path to build artifact storage, absolute or relative to spec file
# bin_dir = ./bin
