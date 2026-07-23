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
version = 3.0.0

# (list) Application requirements
# comma separated e.g. requirements = sqlite3,kivy
# NOTE: sqlite3 ships with python3-for-android automatically.
requirements = python3,kivy==2.3.0,pillow,requests,urllib3,charset-normalizer,idna,certifi,websocket-client,cryptography,qrcode,pyjnius,android

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

#
# Python for android (p4a) specific
#

# (str) The name of the bootstrap to use
p4a.bootstrap = sdl2

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
