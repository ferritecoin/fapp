"""Ferritext background sync service — Android only.

Started by the app (FextApp._sync_background_service) as a p4a foreground
service in its OWN process, declared in buildozer.spec as:

    services = Fextsync:service.py:foreground:sticky

It reuses main.py's sync, crypto and storage code unchanged; see
main.run_background_service for what it does. A service process has no
Activity and therefore no window, so Kivy must be told not to create one
and given the screen metrics it would otherwise ask the window for (main.py
evaluates dp() while importing).
"""
import os

os.environ.update({
    "KIVY_WINDOW": "none",           # no window provider in a service
    "KIVY_DPI": "160",               # metrics without a window
    "KIVY_METRICS_DENSITY": "1",
    "KIVY_METRICS_FONTSCALE": "1",
    "KIVY_NO_ARGS": "1",
    "KIVY_NO_CONSOLELOG": "1",
})

import main  # noqa: E402  (environment must be set first)

main.run_background_service()
