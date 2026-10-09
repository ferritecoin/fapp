#!/usr/bin/env python3
# =============================================================================
#  FEXT Client  --  client.py                                         (v3.0.0)
#  ---------------------------------------------------------------------------
#  v3.0: TOTAL UI REFACTOR — CustomTkinter -> Kivy, for Android & iOS.
#        * One codebase for desktop (Win/macOS/Linux) and mobile (buildozer /
#          kivy-ios). On mobile, HOME is the app sandbox, so ~/.fext lands in
#          app-private storage automatically.
#        * A two-screen messenger layout (chat list -> conversation) with
#          initial-letter avatars and FEXT's golden delivery ticks.
#        * ZERO changes to the security core: the crypto block, key storage,
#          E2E engine, API client, local store and sync worker are carried
#          over verbatim from v2.1 — only the UI layer was rebuilt.
#
#  ***  OPEN SOURCE SOFTWARE  —  TRANSPARENCY IS THE SECURITY MODEL  ***
#
#  This client is published in full so anyone can verify, line by line, the
#  single promise the whole system rests on:
#
#          >>>  NO PLAINTEXT EVER LEAVES THIS PROCESS.  <<<
#
#  Audit checklist for reviewers (all in this one file):
#    * IdentityManager — secp256k1 private keys (Bitcoin-compatible; the same
#                        key imports into FEXT Core to spend coins) are
#                        generated/imported locally and stored ONLY in
#                        ~/.fext/identities.json (chmod 600). Multiple
#                        identities are supported; switching never restarts
#                        the app and never transmits a private key anywhere.
#    * PoW mining      — mine_identity() grinds keys until the Base58Check
#                        address (version byte 0x24) starts with 'Fec'. This
#                        is the network's Sybil-resistance requirement; the
#                        server independently recomputes and enforces it.
#    * CryptoEngine    — the full E2E flow, documented inline:
#                          SIGN    (deterministic ECDSA, sender's static key)
#                        → ENCRYPT (ephemeral secp256k1 ECDH → HKDF-SHA256
#                                   → AES-256-GCM, to the recipient's key)
#                        → SEND    (only the opaque ciphertext envelope)
#    * ApiClient / SyncWorker — every byte that goes on the wire is built in
#                        these two classes; grep for `requests.` and
#                        `_ws.send` and confirm only ciphertext + public
#                        values are transmitted.
#    * The relay server (closed source, untrusted by design) stores only
#      undelivered ciphertext (max 3 days) and a public username→address
#      directory. Even a fully malicious server cannot read, forge, or
#      silently alter messages: forgery fails ECDSA verification, tampering
#      fails AES-GCM authentication.
#
#  Tick system (0/3 .. 3/3), rendered on every outgoing bubble:
#      0/3  ✓ grey    queued locally, not yet on the server
#      1/3  ✓ gold    ciphertext stored by the relay
#      2/3  ✓✓ gold   recipient downloaded the ciphertext
#      3/3  ✓✓✓ gold  recipient actively marked it read
#  Per-contact privacy (tap the chat title bar): if EITHER side selects
#  "No ticks status" the server relays nothing and both sides cap at 1 gold
#  tick. "Auto mark all as read" (default on) controls whether merely
#  viewing a chat emits the 3/3 receipt or whether you mark manually.
#
#  Mobile packaging:
#      Android:  buildozer android debug     (see buildozer.spec; requirements
#                include kivy, requests, websocket-client, cryptography,
#                qrcode, pillow)
#      iOS:      toolchain build kivy … via kivy-ios, then add this file as
#                main.py to the Xcode project.
#
#  License intent: free to read, run, audit, modify and redistribute.
# =============================================================================

from __future__ import annotations

import base64
import io
import json
import logging
import math
import os
import platform
import queue
import re
import sqlite3
import stat
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import qrcode
import requests
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

try:
    import websocket as _websocket_mod        # pip install websocket-client
except ImportError:                            # graceful HTTP-polling fallback
    _websocket_mod = None
    print("[fext] 'websocket-client' is not installed — realtime push is "
          "DISABLED and the client will fall back to HTTP polling.\n"
          "[fext] Install it for instant delivery:  pip install "
          "websocket-client", file=sys.stderr)

# ---- logging ----------------------------------------------------------------
# The client keeps its own message-flow log, in the SAME format the relay
# writes, so the two can be read side by side. That is the point: when a
# message goes missing, the question is always "which of the two of us thinks
# it happened?", and answering it used to mean guessing.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("fext.client")
flow_log = logging.getLogger("fext.flow")

# ---- Kivy (import AFTER env hints; window is created on import) -------------
os.environ.setdefault("KIVY_NO_ARGS", "1")     # our argv is not kivy's argv

from kivy.config import Config                 # noqa: E402
from kivy.utils import platform as kivy_platform  # noqa: E402

if kivy_platform not in ("android", "ios"):
    # A right-click must not leave Kivy's multitouch-emulation marker behind
    # (a red dot that stays on screen until clicked again); and edges are
    # smoothed by the GPU where it can (4 samples is Kivy's maximum) —
    # unless the environment sets either (KCFG_INPUT_MOUSE,
    # KCFG_GRAPHICS_MULTISAMPLES).
    if "KCFG_INPUT_MOUSE" not in os.environ:
        Config.set("input", "mouse", "mouse,disable_multitouch")
    if "KCFG_GRAPHICS_MULTISAMPLES" not in os.environ:
        Config.set("graphics", "multisamples", "4")
    if platform.system() == "Windows":
        # Its own taskbar entry, with its own icon, rather than Python's —
        # this has to happen before the first window appears.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Ferritext.Messenger")
        except Exception:
            pass

from kivy.animation import Animation
from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.image import Image as CoreImage
from kivy.core.text import Label as CoreLabel, LabelBase
from kivy.core.window import Window
from kivy.graphics import ClearBuffers, ClearColor, Color, Ellipse, Fbo, \
    Line, Mesh, PopMatrix, PushMatrix, Rectangle, RoundedRectangle, Scale, \
    SmoothEllipse, SmoothLine, SmoothRoundedRectangle, Triangle
from kivy.graphics.texture import Texture
from kivy.metrics import dp, sp
from kivy.properties import NumericProperty
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image as KivyImage
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.screenmanager import NoTransition, Screen, ScreenManager, \
    SlideTransition
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import get_color_from_hex

# =============================================================================
#  FEXT crypto core — Bitcoin-compatible secp256k1 + Base58Check (pure Python)
#  This block is embedded verbatim in BOTH client.py and server.py so that each
#  remains a single self-contained file with zero native-crypto dependencies.
# =============================================================================
import hashlib
import hmac as _hmac
import os as _os
import struct as _struct

# ---- secp256k1 domain parameters (identical to Bitcoin) ---------------------
_P  = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_N  = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
_GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8
_G  = (_GX, _GY)

FEXT_ADDR_VERSION = 0x24        # 36 decimal -> every address starts with 'F'
FEXT_ADDR_PREFIX  = "Fec"       # proof-of-work requirement (client mines this)
WIF_VERSION       = 0x80        # Bitcoin/FEXT-Core compatible WIF

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


# ---- RIPEMD-160 (hashlib when OpenSSL provides it, pure-Python fallback) ----
def _ripemd160(data: bytes) -> bytes:
    try:
        h = hashlib.new("ripemd160")
        h.update(data)
        return h.digest()
    except (ValueError, TypeError):          # OpenSSL 3 without legacy provider
        return _ripemd160_py(data)


_RMD_R1 = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
           7, 4, 13, 1, 10, 6, 15, 3, 12, 0, 9, 5, 2, 14, 11, 8,
           3, 10, 14, 4, 9, 15, 8, 1, 2, 7, 0, 6, 13, 11, 5, 12,
           1, 9, 11, 10, 0, 8, 12, 4, 13, 3, 7, 15, 14, 5, 6, 2,
           4, 0, 5, 9, 7, 12, 2, 10, 14, 1, 3, 8, 11, 6, 15, 13]
_RMD_R2 = [5, 14, 7, 0, 9, 2, 11, 4, 13, 6, 15, 8, 1, 10, 3, 12,
           6, 11, 3, 7, 0, 13, 5, 10, 14, 15, 8, 12, 4, 9, 1, 2,
           15, 5, 1, 3, 7, 14, 6, 9, 11, 8, 12, 2, 10, 0, 4, 13,
           8, 6, 4, 1, 3, 11, 15, 0, 5, 12, 2, 13, 9, 7, 10, 14,
           12, 15, 10, 4, 1, 5, 8, 7, 6, 2, 13, 14, 0, 3, 9, 11]
_RMD_S1 = [11, 14, 15, 12, 5, 8, 7, 9, 11, 13, 14, 15, 6, 7, 9, 8,
           7, 6, 8, 13, 11, 9, 7, 15, 7, 12, 15, 9, 11, 7, 13, 12,
           11, 13, 6, 7, 14, 9, 13, 15, 14, 8, 13, 6, 5, 12, 7, 5,
           11, 12, 14, 15, 14, 15, 9, 8, 9, 14, 5, 6, 8, 6, 5, 12,
           9, 15, 5, 11, 6, 8, 13, 12, 5, 12, 13, 14, 11, 8, 5, 6]
_RMD_S2 = [8, 9, 9, 11, 13, 15, 15, 5, 7, 7, 8, 11, 14, 14, 12, 6,
           9, 13, 15, 7, 12, 8, 9, 11, 7, 7, 12, 7, 6, 15, 13, 11,
           9, 7, 15, 11, 8, 6, 6, 14, 12, 13, 5, 14, 13, 13, 7, 5,
           15, 5, 8, 11, 14, 14, 6, 14, 6, 9, 12, 9, 12, 5, 15, 8,
           8, 5, 12, 9, 12, 5, 14, 6, 8, 13, 6, 5, 15, 13, 11, 11]
_RMD_K1 = [0x00000000, 0x5A827999, 0x6ED9EBA1, 0x8F1BBCDC, 0xA953FD4E]
_RMD_K2 = [0x50A28BE6, 0x5C4DD124, 0x6D703EF3, 0x7A6D76E9, 0x00000000]


def _rmd_f(j: int, x: int, y: int, z: int) -> int:
    if j < 16:
        return x ^ y ^ z
    if j < 32:
        return (x & y) | (~x & z)
    if j < 48:
        return (x | ~y) ^ z
    if j < 64:
        return (x & z) | (y & ~z)
    return x ^ (y | ~z)


def _rol(x: int, n: int) -> int:
    x &= 0xFFFFFFFF
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


def _ripemd160_py(data: bytes) -> bytes:
    h = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0]
    msg = bytearray(data)
    bitlen = len(data) * 8
    msg.append(0x80)
    while len(msg) % 64 != 56:
        msg.append(0)
    msg += _struct.pack("<Q", bitlen)
    for off in range(0, len(msg), 64):
        x = _struct.unpack("<16I", msg[off:off + 64])
        a1, b1, c1, d1, e1 = h
        a2, b2, c2, d2, e2 = h
        for j in range(80):
            rnd = j // 16
            t = (_rol((a1 + _rmd_f(j, b1, c1, d1) + x[_RMD_R1[j]]
                       + _RMD_K1[rnd]) & 0xFFFFFFFF, _RMD_S1[j]) + e1) & 0xFFFFFFFF
            a1, e1, d1, c1, b1 = e1, d1, _rol(c1, 10), b1, t
            t = (_rol((a2 + _rmd_f(79 - j, b2, c2, d2) + x[_RMD_R2[j]]
                       + _RMD_K2[rnd]) & 0xFFFFFFFF, _RMD_S2[j]) + e2) & 0xFFFFFFFF
            a2, e2, d2, c2, b2 = e2, d2, _rol(c2, 10), b2, t
        t = (h[1] + c1 + d2) & 0xFFFFFFFF
        h[1] = (h[2] + d1 + e2) & 0xFFFFFFFF
        h[2] = (h[3] + e1 + a2) & 0xFFFFFFFF
        h[3] = (h[4] + a1 + b2) & 0xFFFFFFFF
        h[4] = (h[0] + b1 + c2) & 0xFFFFFFFF
        h[0] = t
    return _struct.pack("<5I", *h)


# ---- hashes -----------------------------------------------------------------
def sha256d(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def hash160(data: bytes) -> bytes:
    return _ripemd160(hashlib.sha256(data).digest())


# ---- Base58Check ------------------------------------------------------------
def b58encode(raw: bytes) -> str:
    num = int.from_bytes(raw, "big")
    out = ""
    while num:
        num, rem = divmod(num, 58)
        out = _B58_ALPHABET[rem] + out
    pad = 0
    for byte in raw:
        if byte == 0:
            pad += 1
        else:
            break
    return "1" * pad + out


def b58decode(text: str) -> bytes:
    num = 0
    for ch in text:
        idx = _B58_ALPHABET.find(ch)
        if idx < 0:
            raise ValueError("invalid base58 character")
        num = num * 58 + idx
    raw = num.to_bytes((num.bit_length() + 7) // 8, "big")
    pad = 0
    for ch in text:
        if ch == "1":
            pad += 1
        else:
            break
    return b"\x00" * pad + raw


def b58check_encode(version: int, payload: bytes) -> str:
    body = bytes([version]) + payload
    return b58encode(body + sha256d(body)[:4])


def b58check_decode(text: str) -> tuple:
    raw = b58decode(text)
    if len(raw) < 5:
        raise ValueError("base58check payload too short")
    body, checksum = raw[:-4], raw[-4:]
    if sha256d(body)[:4] != checksum:
        raise ValueError("base58check checksum mismatch")
    return body[0], body[1:]


# ---- elliptic-curve arithmetic (affine, None = point at infinity) -----------
def _ec_add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2:
        if (y1 + y2) % _P == 0:
            return None
        lam = (3 * x1 * x1) * pow(2 * y1, -1, _P) % _P
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, _P) % _P
    x3 = (lam * lam - x1 - x2) % _P
    y3 = (lam * (x1 - x3) - y1) % _P
    return (x3, y3)


def _ec_mul(k: int, point):
    result = None
    addend = point
    while k:
        if k & 1:
            result = _ec_add(result, addend)
        addend = _ec_add(addend, addend)
        k >>= 1
    return result


# ---- key serialization ------------------------------------------------------
def pub_to_compressed(point) -> bytes:
    x, y = point
    return bytes([2 + (y & 1)]) + x.to_bytes(32, "big")


def compressed_to_pub(raw: bytes):
    """Parse + validate a 33-byte compressed secp256k1 public key.
    Raises ValueError on anything that is not a real curve point."""
    if len(raw) != 33 or raw[0] not in (2, 3):
        raise ValueError("public key must be 33 bytes, prefix 02/03")
    x = int.from_bytes(raw[1:], "big")
    if x >= _P:
        raise ValueError("public key x out of range")
    y_sq = (pow(x, 3, _P) + 7) % _P
    y = pow(y_sq, (_P + 1) // 4, _P)
    if (y * y) % _P != y_sq:
        raise ValueError("x is not on the curve")
    if (y & 1) != (raw[0] & 1):
        y = _P - y
    return (x, y)


def priv_to_pub(priv: int):
    if not 1 <= priv < _N:
        raise ValueError("private key out of range")
    return _ec_mul(priv, _G)


# ---- P2PKH-style FEXT addresses ---------------------------------------------
def address_from_pub_bytes(compressed: bytes) -> str:
    return b58check_encode(FEXT_ADDR_VERSION, hash160(compressed))


def address_from_priv(priv: int) -> str:
    return address_from_pub_bytes(pub_to_compressed(priv_to_pub(priv)))


def address_meets_pow(address: str) -> bool:
    return address.startswith(FEXT_ADDR_PREFIX)


def mine_identity(should_stop=None, progress=None):
    """Grind secp256k1 keys until the P2PKH address starts with 'Fec'.

    Vanity-mining trick: compute P = k*G once, then walk k, k+1, k+2 …
    with a single cheap point ADDITION (P += G) per attempt instead of a
    full scalar multiplication. Returns (priv_int, compressed_pub, address)
    or None when `should_stop()` fired first.
    """
    k = int.from_bytes(_os.urandom(32), "big") % (_N - 1) + 1
    point = _ec_mul(k, _G)
    attempts = 0
    while True:
        attempts += 1
        compressed = pub_to_compressed(point)
        address = address_from_pub_bytes(compressed)
        if address.startswith(FEXT_ADDR_PREFIX):
            return k, compressed, address, attempts
        if should_stop is not None and should_stop():
            return None
        if progress is not None and attempts % 250 == 0:
            progress(attempts)
        k += 1
        if k >= _N:
            k = 1
        point = _ec_add(point, _G)


# ---- WIF import / export (FEXT Core & Bitcoin Core compatible) --------------
def priv_to_wif(priv: int, compressed: bool = True) -> str:
    payload = priv.to_bytes(32, "big") + (b"\x01" if compressed else b"")
    return b58check_encode(WIF_VERSION, payload)


def wif_to_priv(wif: str) -> int:
    version, payload = b58check_decode(wif)
    if version != WIF_VERSION:
        raise ValueError("not a WIF private key (wrong version byte)")
    if len(payload) == 33 and payload[-1] == 1:
        payload = payload[:-1]
    if len(payload) != 32:
        raise ValueError("WIF payload must be 32 bytes")
    priv = int.from_bytes(payload, "big")
    if not 1 <= priv < _N:
        raise ValueError("WIF private key out of range")
    return priv


def parse_private_key(text: str) -> int:
    """Accept 64-char raw hex OR WIF (both formats FEXT Core understands)."""
    text = text.strip()
    if len(text) == 64:
        try:
            priv = int(text, 16)
        except ValueError:
            pass
        else:
            if 1 <= priv < _N:
                return priv
            raise ValueError("hex private key out of range")
    return wif_to_priv(text)


# ---- deterministic ECDSA (RFC 6979) -----------------------------------------
def _rfc6979_nonce(z: int, priv: int) -> int:
    x = priv.to_bytes(32, "big")
    m = (z % _N).to_bytes(32, "big")
    v = b"\x01" * 32
    key = b"\x00" * 32
    key = _hmac.new(key, v + b"\x00" + x + m, hashlib.sha256).digest()
    v = _hmac.new(key, v, hashlib.sha256).digest()
    key = _hmac.new(key, v + b"\x01" + x + m, hashlib.sha256).digest()
    v = _hmac.new(key, v, hashlib.sha256).digest()
    while True:
        v = _hmac.new(key, v, hashlib.sha256).digest()
        k = int.from_bytes(v, "big")
        if 1 <= k < _N:
            return k
        key = _hmac.new(key, v + b"\x00", hashlib.sha256).digest()
        v = _hmac.new(key, v, hashlib.sha256).digest()


def ecdsa_sign(message: bytes, priv: int) -> bytes:
    """Deterministic low-s ECDSA. Returns compact 64-byte r||s."""
    z = int.from_bytes(hashlib.sha256(message).digest(), "big")
    while True:
        k = _rfc6979_nonce(z, priv)
        px, _py = _ec_mul(k, _G)
        r = px % _N
        if r == 0:
            z = (z + 1) % _N            # astronomically unlikely; stay total
            continue
        s = (pow(k, -1, _N) * (z + r * priv)) % _N
        if s == 0:
            z = (z + 1) % _N
            continue
        if s > _N // 2:                 # canonical low-s form
            s = _N - s
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def ecdsa_verify(message: bytes, signature: bytes, pub_point) -> bool:
    if len(signature) != 64:
        return False
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    if not (1 <= r < _N and 1 <= s < _N):
        return False
    z = int.from_bytes(hashlib.sha256(message).digest(), "big")
    try:
        s_inv = pow(s, -1, _N)
        point = _ec_add(_ec_mul((z * s_inv) % _N, _G),
                        _ec_mul((r * s_inv) % _N, pub_point))
    except ValueError:
        return False
    if point is None:
        return False
    return point[0] % _N == r


# ---- ECDH (for the client's ECIES message envelopes) ------------------------
def ecdh_shared_secret(priv: int, peer_pub_point) -> bytes:
    point = _ec_mul(priv, peer_pub_point)
    if point is None:
        raise ValueError("ECDH produced the point at infinity")
    return hashlib.sha256(pub_to_compressed(point)).digest()





# =============================================================================
#  FEXT crypto ACCELERATION LAYER  (v4 performance work)
#  ---------------------------------------------------------------------------
#  The affine implementation above is the AUDIT REFERENCE: short, obvious, and
#  easy to check by eye. It is also slow, because every point operation does a
#  modular inversion — an ECDSA verify cost ~25 ms, which capped the relay at
#  ~40 authenticated requests/second and made each message cost ~26 ms of
#  phone CPU to seal and ~38 ms to open.
#
#  This layer keeps the reference implementation intact and adds two faster
#  paths that produce BYTE-IDENTICAL results (verified by tests/test_fastec.py
#  and the differential suite, which compare against the affine code above):
#
#    Tier 1  libsecp256k1 via `coincurve`, when installed .... ~0.04 ms/verify
#    Tier 2  pure-Python Jacobian + windowed tables (always) .. ~2.6  ms/verify
#    Tier 3  the affine reference above ....................... ~25   ms/verify
#
#  Tier 2 needs no dependencies, so the "one self-contained file" property is
#  preserved: `pip install coincurve` is an optional server-side speedup, not
#  a requirement. Tier 3 is never used at runtime but stays readable for
#  auditors, and the test-suite pins the fast paths to it.
#
#  Techniques in tier 2:
#    * Jacobian coordinates (x = X/Z², y = Y/Z³) so adds/doubles are pure
#      multiplies and only ONE inversion happens, at the end.
#    * 4-bit windowed multiplication; the generator G's table is built once
#      at import (Montgomery batch inversion) and reused forever.
#    * Shamir's trick: u1*G + u2*Q shares a single ladder of doublings.
# =============================================================================

_INF_J = (0, 0, 0)              # Jacobian point at infinity (Z == 0)
_WIN = 4                        # bits consumed per windowed step
_WIN_SIZE = 1 << _WIN


def _jac_double(p):
    """dbl-2009-l, specialised for a == 0 (secp256k1)."""
    x1, y1, z1 = p
    if z1 == 0 or y1 == 0:
        return _INF_J
    a = (y1 * y1) % _P
    b = (4 * x1 * a) % _P
    c = (8 * a * a) % _P
    d = (3 * x1 * x1) % _P
    x3 = (d * d - 2 * b) % _P
    y3 = (d * (b - x3) - c) % _P
    z3 = (2 * y1 * z1) % _P
    return (x3, y3, z3)


def _jac_add(p, q):
    """add-2007-bl, Jacobian + Jacobian."""
    x1, y1, z1 = p
    x2, y2, z2 = q
    if z1 == 0:
        return q
    if z2 == 0:
        return p
    z1z1 = (z1 * z1) % _P
    z2z2 = (z2 * z2) % _P
    u1 = (x1 * z2z2) % _P
    u2 = (x2 * z1z1) % _P
    s1 = (y1 * z2 * z2z2) % _P
    s2 = (y2 * z1 * z1z1) % _P
    if u1 == u2:
        return _INF_J if s1 != s2 else _jac_double(p)
    h = (u2 - u1) % _P
    r = (s2 - s1) % _P
    hh = (h * h) % _P
    hhh = (h * hh) % _P
    u1hh = (u1 * hh) % _P
    x3 = (r * r - hhh - 2 * u1hh) % _P
    y3 = (r * (u1hh - x3) - s1 * hhh) % _P
    z3 = (z1 * z2 * h) % _P
    return (x3, y3, z3)


def _jac_add_affine(p, q_affine):
    """madd-2007-bl: mixed add where q has Z == 1 (all table entries do)."""
    x1, y1, z1 = p
    x2, y2 = q_affine
    if z1 == 0:
        return (x2, y2, 1)
    z1z1 = (z1 * z1) % _P
    u2 = (x2 * z1z1) % _P
    s2 = (y2 * z1 * z1z1) % _P
    if x1 == u2:
        return _INF_J if y1 != s2 else _jac_double(p)
    h = (u2 - x1) % _P
    r = (s2 - y1) % _P
    hh = (h * h) % _P
    hhh = (h * hh) % _P
    x1hh = (x1 * hh) % _P
    x3 = (r * r - hhh - 2 * x1hh) % _P
    y3 = (r * (x1hh - x3) - y1 * hhh) % _P
    z3 = (z1 * h) % _P
    return (x3, y3, z3)


def _jac_to_affine(p):
    """Jacobian -> affine: the single modular inversion of the operation."""
    x, y, z = p
    if z == 0:
        return None
    z_inv = pow(z, -1, _P)
    z_inv2 = (z_inv * z_inv) % _P
    return ((x * z_inv2) % _P, (y * z_inv2 * z_inv) % _P)


def _build_window(point_affine):
    """[None, 1P, 2P, … 15P] as affine points, via one batched inversion."""
    jac = [_INF_J, (point_affine[0], point_affine[1], 1)]
    for i in range(2, _WIN_SIZE):
        jac.append(_jac_double(jac[i // 2]) if i % 2 == 0
                   else _jac_add_affine(jac[i - 1], point_affine))
    zs = [p[2] for p in jac[1:]]
    prefix = [1]
    for z in zs:
        prefix.append((prefix[-1] * z) % _P)
    inv_all = pow(prefix[-1], -1, _P)
    tail = []
    for i in range(len(zs) - 1, -1, -1):
        z_inv = (inv_all * prefix[i]) % _P
        inv_all = (inv_all * zs[i]) % _P
        x, y, _z = jac[i + 1]
        z_inv2 = (z_inv * z_inv) % _P
        tail.append(((x * z_inv2) % _P, (y * z_inv2 * z_inv) % _P))
    return [None] + tail[::-1]


_G_WINDOW = _build_window(_G)          # fixed base: built once, reused forever


def _mul_jac(k, table):
    k %= _N
    if k == 0:
        return _INF_J
    nibbles = []
    while k:
        nibbles.append(k & (_WIN_SIZE - 1))
        k >>= _WIN
    acc = _INF_J
    for idx, nib in enumerate(reversed(nibbles)):
        if idx:
            for _ in range(_WIN):
                acc = _jac_double(acc)
        if nib:
            entry = table[nib]
            acc = ((entry[0], entry[1], 1) if acc[2] == 0
                   else _jac_add_affine(acc, entry))
    return acc


def _shamir(k1, table1, k2, table2):
    """k1*P1 + k2*P2 in one ladder — halves the doubling work of a verify."""
    k1 %= _N
    k2 %= _N
    n1, n2 = [], []
    a, b = k1, k2
    while a or b:
        n1.append(a & (_WIN_SIZE - 1))
        n2.append(b & (_WIN_SIZE - 1))
        a >>= _WIN
        b >>= _WIN
    acc = _INF_J
    for idx, (d1, d2) in enumerate(zip(reversed(n1), reversed(n2))):
        if idx:
            for _ in range(_WIN):
                acc = _jac_double(acc)
        for nib, table in ((d1, table1), (d2, table2)):
            if nib:
                entry = table[nib]
                acc = ((entry[0], entry[1], 1) if acc[2] == 0
                       else _jac_add_affine(acc, entry))
    return acc


# ---- optional native tier ----------------------------------------------------
try:
    import coincurve as _coincurve
    from coincurve.ecdsa import cdata_to_der as _cc_der
    from coincurve.ecdsa import deserialize_compact as _cc_compact
    HAVE_NATIVE_SECP256K1 = True
except Exception:                       # any import/ABI problem -> pure Python
    _coincurve = None
    HAVE_NATIVE_SECP256K1 = False


def _normalize_low_s(signature: bytes) -> bytes:
    """Map (r, s) to its canonical low-S form.

    ECDSA is malleable: (r, s) and (r, N-s) are BOTH valid for the same
    message and key. Two consequences we handle here:

      * libsecp256k1 rejects high-S by policy while the affine reference
        accepts it. Normalising first makes every tier agree exactly.
      * The relay's replay cache is keyed on the signature bytes, so an
        attacker could previously flip s -> N-s and replay a captured
        request under a "new" key. Normalising before both the cache lookup
        and the verify closes that, since both forms collapse to one key.
    """
    if len(signature) != 64:
        return signature
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    if s > _N // 2:
        s = _N - s
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def _ec_mul_fast(k, point):
    """Windowed Jacobian scalar multiply (affine in, affine out)."""
    if point is None:
        return None
    k %= _N
    if k == 0:
        return None
    table = _G_WINDOW if point == _G else _build_window(point)
    return _jac_to_affine(_mul_jac(k, table))


def _ec_add_fast(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    return _jac_to_affine(_jac_add((p1[0], p1[1], 1), (p2[0], p2[1], 1)))


def _ecdsa_verify_pure(message: bytes, signature: bytes, pub_point) -> bool:
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    if not (1 <= r < _N and 1 <= s < _N):
        return False
    z = int.from_bytes(hashlib.sha256(message).digest(), "big")
    try:
        s_inv = pow(s, -1, _N)
    except ValueError:
        return False
    point = _jac_to_affine(_shamir((z * s_inv) % _N, _G_WINDOW,
                                   (r * s_inv) % _N,
                                   _build_window(pub_point)))
    return point is not None and point[0] % _N == r


def _ecdsa_verify_native(message: bytes, signature: bytes, pub_point) -> bool:
    try:
        pub = _coincurve.PublicKey(pub_to_compressed(pub_point))
        return pub.verify(_cc_der(_cc_compact(signature)), message)
    except Exception:
        return False


def ecdsa_verify_accel(message: bytes, signature: bytes, pub_point) -> bool:
    """Fast ECDSA verification. Identical accept/reject to the reference."""
    if len(signature) != 64:
        return False
    signature = _normalize_low_s(signature)
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    if not (1 <= r < _N and 1 <= s < _N):
        return False
    if HAVE_NATIVE_SECP256K1:
        return _ecdsa_verify_native(message, signature, pub_point)
    return _ecdsa_verify_pure(message, signature, pub_point)


def ecdsa_sign_accel(message: bytes, priv: int) -> bytes:
    """Deterministic RFC-6979 signing — byte-identical to the reference."""
    z = int.from_bytes(hashlib.sha256(message).digest(), "big")
    while True:
        k = _rfc6979_nonce(z, priv)
        point = _jac_to_affine(_mul_jac(k, _G_WINDOW))
        if point is None:
            continue
        r = point[0] % _N
        if r == 0:
            continue
        s = (pow(k, -1, _N) * (z + r * priv)) % _N
        if s == 0:
            continue
        if s > _N // 2:
            s = _N - s
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def ecdh_shared_secret_accel(priv: int, peer_pub_point) -> bytes:
    point = _ec_mul_fast(priv, peer_pub_point)
    if point is None:
        raise ValueError("ECDH produced the point at infinity")
    return hashlib.sha256(pub_to_compressed(point)).digest()


# ---- promote the fast paths to the names the rest of the file uses ----------
# The affine originals stay reachable as *_affine for audit and for the
# differential tests that pin these implementations to them.
_ec_mul_affine = _ec_mul
_ec_add_affine = _ec_add
ecdsa_verify_affine = ecdsa_verify
ecdsa_sign_affine = ecdsa_sign
ecdh_shared_secret_affine = ecdh_shared_secret

_ec_mul = _ec_mul_fast
_ec_add = _ec_add_fast
ecdsa_verify = ecdsa_verify_accel
ecdsa_sign = ecdsa_sign_accel
ecdh_shared_secret = ecdh_shared_secret_accel


# =============================================================================
#  Configuration
# =============================================================================
# ---- relay endpoint ---------------------------------------------------------
# The relay is pinned to one deployment on purpose: pinning removes a whole
# class of "point the client at an attacker's server" mistakes.
#
# v7.1 CHANGE OF POLICY. v7 hardcoded an https:// URL and made TLS mandatory.
# That was the right security posture and the wrong engineering call: relays
# that speak plain HTTP became UNREACHABLE, and the app failed at first-run
# registration with "Network error — is the relay reachable?". An app that
# cannot be used protects nobody.
#
# The scheme is now DISCOVERED rather than assumed. HTTPS is tried first and
# preferred; if the relay does not speak it, the client falls back to HTTP
# and says so plainly in the status ribbon. Message bodies are end-to-end
# encrypted either way — what TLS adds is protection for the metadata around
# them, so losing it is a real downgrade the user deserves to be told about,
# but not a reason to refuse to run.
#
# Set FEXT_REQUIRE_TLS=1 (or REQUIRE_TLS = True) for deployments that would
# rather fail closed than fall back.
SERVER_HOST = _os.environ.get("FEXT_SERVER_HOST", "118.189.201.104:50607")

# A full URL still overrides everything, for development and the tests. If it
# carries an explicit scheme, that scheme is TRIED FIRST but does not disable
# the fallback unless REQUIRE_TLS is set.
SERVER_URL_OVERRIDE = _os.environ.get("FEXT_SERVER_URL", "")

REQUIRE_TLS = _os.environ.get("FEXT_REQUIRE_TLS", "") not in ("", "0", "false")

APP_NAME = "Ferritext"
#: Must equal `version` in buildozer.spec — CI refuses to build if not.
APP_VERSION = "9.1.1"

# The identity/QR payload keeps carrying the ORIGINAL tag. It is wire data
# that older clients already produce and consume, so renaming the app must
# not rename it — the brand changed, the protocol did not.
WIRE_APP_TAG = "FEXT"

IS_ANDROID = kivy_platform == "android"
IS_IOS = kivy_platform == "ios"
IS_MOBILE = IS_ANDROID or IS_IOS


def _resolve_data_dir() -> Path:
    """Where keys + local chat databases live.
    Desktop: ~/.fext (unchanged from v2, so existing identities carry over).
    Android: the app-private sandbox — Path.home() is NOT reliable there,
             so ask the platform for the real app storage path.
    iOS:     the app sandbox home (Path.home() already points inside it)."""
    if IS_ANDROID:
        try:
            from android.storage import app_storage_path  # type: ignore
            return Path(app_storage_path()) / ".fext"
        except Exception:
            pass
    return Path.home() / ".fext"


DATA_DIR = _resolve_data_dir()             # keys + local DBs live ONLY here

# ---- bundled type and artwork --------------------------------------------------
# In the source tree and the APK they sit in data/ beside this file. The
# single-file desktop release carries them inside itself instead, as comment
# lines after the code (tools/bundle_desktop.py writes them): a manifest
# line, then base64 of one xz stream. They are unpacked once, into
# DATA_DIR/assets/<digest>, and every file is checked against its SHA-256
# on each start. Absent altogether (a stripped-down checkout), every use is
# guarded: Kivy's own font stands in, the loading screen drops the mark.
_BUNDLE_MARK = "# FERRITEXT-ASSETS "


def _file_matches(path: Path, size: int, sha: str) -> bool:
    try:
        return path.stat().st_size == size and \
            hashlib.sha256(path.read_bytes()).hexdigest() == sha
    except OSError:
        return False


def _unpack_bundled_assets(source: Path) -> Optional[Path]:
    import lzma
    try:
        text = source.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    at = text.rfind("\n" + _BUNDLE_MARK)
    if at < 0:
        return None
    lines = text[at + 1:].splitlines()
    try:
        manifest = json.loads(lines[0][len(_BUNDLE_MARK):])
        files = [(str(rel), int(size), str(sha))
                 for rel, size, sha in manifest["files"]]
        target = DATA_DIR / "assets" / re.sub(r"[^0-9a-f]", "",
                                              manifest["digest"])[:16]
        for rel, _size, _sha in files:
            if rel.startswith(("/", "\\")) or ".." in Path(rel).parts:
                raise ValueError(f"unsafe path {rel!r}")
        if all(_file_matches(target / rel, size, sha)
               for rel, size, sha in files):
            return target
        data = lzma.decompress(base64.b64decode("".join(
            line[2:] for line in lines[1:] if line.startswith("# "))))
        pos = 0
        for rel, size, sha in files:
            chunk, pos = data[pos:pos + size], pos + size
            if len(chunk) != size or hashlib.sha256(chunk).hexdigest() != sha:
                raise ValueError(f"{rel} does not match its checksum")
            path = target / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            part = path.with_name(path.name + ".part")
            part.write_bytes(chunk)
            os.replace(part, path)
        log.info("unpacked the bundled fonts and icons into %s", target)
        return target
    except Exception:
        log.exception("could not unpack the bundled fonts and icons")
        return None


def _resolve_asset_dir() -> Path:
    here = Path(__file__).resolve().parent / "data"
    if (here / "fonts").is_dir():
        return here
    return _unpack_bundled_assets(Path(__file__).resolve()) or here


ASSET_DIR = _resolve_asset_dir()
LOGO_FILE = ASSET_DIR / "ferritext_logo.png"
DESKTOP_ICON = ASSET_DIR / "icon_desktop.png"      # title bar, taskbar, dock
DESKTOP_ICO = ASSET_DIR / "icon_desktop.ico"       # Windows' own sizes
IDENTITIES_PATH = DATA_DIR / "identities.json"
EXPORT_DIR = DATA_DIR / "exports"          # plaintext chat exports live here

POLL_INTERVAL_SECONDS = 2.5     # HTTP fallback cadence when WS is unavailable
WS_IDLE_TIMEOUT = 30            # max seconds between outbound WS frames
WS_RECV_TICK = 3.0              # socket recv timeout; stop/keepalive cadence
WS_RECONNECT_MIN = 0.5          # delay before re-dialing a dropped socket
WS_RECONNECT_MAX = 30.0         # cap for offline exponential backoff
# Mobile radios take seconds to wake from idle, and a relay doing a
# pure-Python ECDSA verify can hold a request for tens of milliseconds more.
# 8s was tight enough that ordinary latency looked like failure on a phone.
REQUEST_TIMEOUT = 20 if IS_MOBILE else 12
FOREGROUND_RECONNECT_MAX = 6.0  # backoff cap while the app is on screen
MAX_MESSAGE_CHARS = 4000        # sanitization cap for outgoing text
CHAT_PAGE_SIZE = 60             # messages materialised per chat page
# Mirrors the relay's LEDGER_RETENTION_SECONDS. A read receipt older than
# this can never be confirmed, because the routing row it needs is gone.
RECEIPT_RETRY_WINDOW_SECONDS = 7 * 86400
HKDF_INFO = b"fext.v2.secp256k1-ecdh-hkdf-sha256.aes256gcm"

# ---- v9 media ---------------------------------------------------------------
# Photos and voice notes are sealed with a fresh per-file AES-256-GCM key,
# uploaded to the relay as an opaque blob, and referenced from a normal
# end-to-end message that carries the key. The relay keeps blobs only for a
# while; past that the recipient asks the sender to upload again, and the
# sender's device — which still holds the file — does it (see the 'resend'
# control). Long-term storage is the phones', never the relay's.
MEDIA_DIR = DATA_DIR / "media"
MAX_MEDIA_BYTES = 8 * 1024 * 1024    # hard cap for one sealed blob
IMAGE_MAX_EDGE = 1280                # photos are resized to fit this box…
IMAGE_JPEG_QUALITY = 60              # …and re-encoded: ~100-200 KB typical
THUMB_EDGE = 40                      # inline preview, travels in the message
THUMB_QUALITY = 40
VOICE_SAMPLE_RATE = 16000            # wideband speech
VOICE_BITRATE = 24000                # AAC-LC: ~3 KB per second of speech
MAX_VOICE_SECONDS = 300
MEDIA_MIME = {"image": ("image/jpeg", "image/png", "image/webp"),
              "voice": ("audio/mp4", "audio/aac", "audio/ogg",
                        "audio/3gpp", "audio/amr", "audio/wav")}

# =============================================================================
#  Design system (v9.1): two themes, type, text size, motion
# =============================================================================
# A serif with true italics for everything people write, spaced capitals for
# section heads, buttons and dates, thin rules instead of boxes, and colour
# only where it carries meaning: gold for the delivery ticks, red for what is
# unread or cannot be undone.
#
# Every text/ground pair either theme uses is at least WCAG AA (4.5:1; 3:1
# for non-text marks) — see desktop/test_design.py, which measures them.
THEMES = {
    "dark": {
        "bg": "#11100E", "surface": "#1A1916", "surface_hi": "#24221E",
        "own": "#2A2620", "peer": "#1A1916", "rule": "#2C2924",
        "rule_strong": "#3D3931",
        "text": "#ECE6D9", "text_2": "#B3AB9E", "text_3": "#9A9285",
        "accent": "#CBA761", "accent_press": "#A98A4C", "on_accent": "#14120E",
        "alert": "#D0654D", "on_alert": "#11100E", "ok": "#8FA67E",
        "tick": "#CBA761", "tick_grey": "#857D71", "press": "#23211D",
        "quote_own": "#35302A", "quote_peer": "#24221E",
        "select": (0.796, 0.655, 0.380, 0.13), "scrim": (0, 0, 0, 0.62),
        "plate": "#F4EFE4",         # the light plate a QR code sits on
    },
    "light": {
        "bg": "#FFFFF8", "surface": "#F7F3E8", "surface_hi": "#EFE9DB",
        "own": "#F1EADA", "peer": "#FFFFF8", "rule": "#E2DBCB",
        "rule_strong": "#CFC6B3",
        "text": "#1B1915", "text_2": "#4E483F", "text_3": "#6F685C",
        "accent": "#7F5F18", "accent_press": "#634A12", "on_accent": "#FFFFF8",
        "alert": "#A23A28", "on_alert": "#FFFFF8", "ok": "#4F6B3E",
        "tick": "#9A7424", "tick_grey": "#8C8578", "press": "#EFE9DB",
        "quote_own": "#E8DFCB", "quote_peer": "#EFE9DB",
        "select": (0.498, 0.373, 0.094, 0.10), "scrim": (0.07, 0.06, 0.04, .48),
        "plate": "#FFFFF8",
    },
}
#: Avatar colours, picked by address hash. Each carries a light initial at
#: >= 3:1 (initials are large text).
AVATAR_PALETTE = ["#3E5A8C", "#B4472E", "#3F7F73", "#A87A2A",
                  "#8E3B55", "#6E5238", "#4E7A45", "#6B3E6E"]


class _Tokens:
    """The live theme: `T.bg`, `T.text`, … — read when a widget is BUILT.
    Changing theme rebuilds the widget tree (FextApp.apply_appearance), so
    nothing has to repaint itself in place."""
    name = "dark"

    def load(self, name: str) -> None:
        self.name = name if name in THEMES else "dark"
        for key, value in THEMES[self.name].items():
            setattr(self, key, value)

    @property
    def dark(self) -> bool:
        return self.name == "dark"


T = _Tokens()
#: The pre-9.1 colour names, kept as live aliases of the theme's tokens:
#: worker threads still post status colours by these names.
_COL_TOKENS = {
    "COL_BG": "bg", "COL_BG_LIST": "bg", "COL_CARD": "surface",
    "COL_CARD_EDGE": "rule", "COL_APPBAR": "bg", "COL_APPBAR_TEXT": "text",
    "COL_APPBAR_DIM": "text_3", "COL_BUBBLE_ME": "own",
    "COL_BUBBLE_PEER": "peer", "COL_TEXT": "text", "COL_TEXT_DIM": "text_3",
    "COL_ACCENT": "accent", "COL_ACCENT_DARK": "accent_press",
    "COL_DANGER": "alert", "COL_OK": "ok", "COL_TICK_GOLD": "tick",
    "COL_TICK_GREY": "tick_grey", "COL_PRESSED": "press",
    "COL_DIVIDER": "rule", "COL_QUOTE_ME": "quote_own",
    "COL_QUOTE_PEER": "quote_peer", "COL_SELECT_RGBA": "select",
}


def apply_theme(name: str) -> None:
    T.load(name)
    globals().update({alias: getattr(T, token)
                      for alias, token in _COL_TOKENS.items()})


def retint(hex_color: str) -> str:
    """The current theme's version of a colour another theme produced (a
    status line posted just before the theme changed)."""
    if not isinstance(hex_color, str):
        return hex_color
    for theme in THEMES.values():
        for token, value in theme.items():
            if value == hex_color:
                return getattr(T, token)
    return hex_color


apply_theme("dark")
# Declared here for readers and linters; apply_theme() keeps them current.
COL_BG = COL_BG_LIST = COL_APPBAR = T.bg
COL_CARD = COL_BUBBLE_PEER = T.surface
COL_CARD_EDGE = COL_DIVIDER = T.rule
COL_APPBAR_TEXT, COL_TEXT = T.text, T.text
COL_APPBAR_DIM = COL_TEXT_DIM = T.text_3
COL_BUBBLE_ME, COL_ACCENT, COL_ACCENT_DARK = T.own, T.accent, T.accent_press
COL_DANGER, COL_OK, COL_TICK_GOLD, COL_TICK_GREY = (T.alert, T.ok, T.tick,
                                                    T.tick_grey)
COL_PRESSED, COL_QUOTE_ME, COL_QUOTE_PEER = T.press, T.quote_own, T.quote_peer
COL_SELECT_RGBA = T.select


def C(hex_color: str) -> list:
    """Hex string -> kivy rgba. UI-side only; workers keep hex strings."""
    return get_color_from_hex(hex_color)


def mix(a: str, b: str, t: float) -> str:
    """`a` moved a fraction `t` of the way to `b` (hex colours)."""
    a, b = a.lstrip("#"), b.lstrip("#")
    return "#" + "".join(
        f"{round(int(a[i:i + 2], 16) * (1 - t) + int(b[i:i + 2], 16) * t):02X}"
        for i in (0, 2, 4))


# ---- system appearance ------------------------------------------------------
def system_prefers_dark() -> Optional[bool]:
    """What the operating system is set to: True dark, False light, None
    when it cannot be told. FEXT_THEME=dark|light overrides (tests,
    screenshots)."""
    forced = os.environ.get("FEXT_THEME", "").lower()
    if forced in ("dark", "light"):
        return forced == "dark"
    try:
        if IS_ANDROID:
            autoclass, _ = _jni()
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            mode = activity.getResources().getConfiguration().uiMode
            return (mode & 0x30) == 0x20          # UI_MODE_NIGHT_YES
        if sys.platform == "win32":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion"
                                r"\Themes\Personalize") as key:
                return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        if sys.platform == "darwin":
            out = subprocess.run(["defaults", "read", "-g",
                                  "AppleInterfaceStyle"], capture_output=True,
                                 text=True, timeout=2)
            return "dark" in out.stdout.lower()
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface",
                              "color-scheme"], capture_output=True, text=True,
                             timeout=2)
        if out.returncode == 0 and out.stdout.strip():
            return "dark" in out.stdout
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface",
                              "gtk-theme"], capture_output=True, text=True,
                             timeout=2)
        if out.returncode == 0 and out.stdout.strip():
            return "dark" in out.stdout.lower()
    except Exception:
        pass
    return None


def system_reduces_motion() -> bool:
    """Android's 'Remove animations' sets the animator scale to 0."""
    if not IS_ANDROID:
        return False
    try:
        autoclass, _ = _jni()
        Global = autoclass("android.provider.Settings$Global")
        resolver = android_context().getContentResolver()
        return Global.getFloat(resolver, "animator_duration_scale", 1.0) == 0
    except Exception:
        return False


#: Multiplies every font size, on top of the system font scale that sp()
#: already applies.
TEXT_SCALES = (("small", "S", 0.9), ("default", "M", 1.0),
               ("large", "L", 1.15), ("largest", "XL", 1.3))
THEME_CHOICES = (("system", "System"), ("light", "Light"), ("dark", "Dark"))


class _Appearance:
    mode = "system"                 # the user's choice: system/light/dark
    scale = 1.0
    reduce_motion = False

    def resolve(self) -> str:
        """Re-read the preferences and the system; returns the theme name."""
        prefs = load_prefs()
        self.mode = prefs.get("theme", "system")
        if self.mode not in ("system", "light", "dark"):
            self.mode = "system"
        self.scale = dict((k, s) for k, _l, s in TEXT_SCALES).get(
            prefs.get("text_size", "default"), 1.0)
        motion = prefs.get("reduce_motion")
        self.reduce_motion = bool(system_reduces_motion() if motion is None
                                  else motion)
        if self.mode != "system":
            return self.mode
        dark = system_prefers_dark()
        return "light" if dark is False else "dark"


APPEARANCE = _Appearance()


def fs(size: float) -> float:
    """A font size in sp, scaled by the user's text-size choice."""
    return sp(size) * APPEARANCE.scale


def motion(duration: float) -> float:
    """An animation's duration, or 0 when the user has asked for less."""
    return 0 if APPEARANCE.reduce_motion else duration


# ---- type -------------------------------------------------------------------
# Kivy draws a label with ONE font file and has no fallback, so the bundled
# serif carries the emoji too (tools/build_fonts.py merges them). It is
# registered under Kivy's default family name, so every Label and TextInput
# in the app — and in Kivy's own widgets — is set in it.
FONT_DIR = ASSET_DIR / "fonts"
SERIF_FILES = {"fn_regular": "FerritextSerif-Regular.ttf",
               "fn_italic": "FerritextSerif-Italic.ttf",
               "fn_bold": "FerritextSerif-Bold.ttf",
               "fn_bolditalic": "FerritextSerif-BoldItalic.ttf"}
CAPS_FONT = FONT_DIR / "ui.ttf"          # Titillium Web Light, as supplied
CAPS_FONT_NAME = "Roboto"


def register_fonts() -> bool:
    global CAPS_FONT_NAME
    files = {k: FONT_DIR / v for k, v in SERIF_FILES.items()}
    ok = all(p.is_file() for p in files.values())
    if ok:
        paths = {k: str(p) for k, p in files.items()}
        LabelBase.register("Roboto", **paths)
        LabelBase.register("FerritextSerif", **paths)
    else:
        log.warning("bundled fonts missing from %s — using Kivy's", FONT_DIR)
    if CAPS_FONT.is_file():
        LabelBase.register("FerritextCaps", str(CAPS_FONT))
        CAPS_FONT_NAME = "FerritextCaps"
    return ok


register_fonts()

# =============================================================================
#  Small shared utilities (DRY helpers)
# =============================================================================
def b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def short_addr(address: str, n: int = 12) -> str:
    return f"{address[:n]}…" if len(address) > n else address


def single_line_text(text: str, limit: int) -> str:
    """`text` on one line, cut to `limit` characters with an ellipsis."""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit - 1].rstrip() + "…"


def message_text(msg: dict) -> str:
    """The words of a message, as a person would quote them: a photo's
    caption, nothing for a voice note, the body otherwise."""
    kind = msg.get("kind") or "text"
    if kind in ("image", "voice"):
        try:
            ext = json.loads(msg.get("ext") or "{}")
        except (TypeError, ValueError):
            ext = {}
        media = ext.get("media") if isinstance(ext, dict) else None
        return (media or {}).get("cap", "") if kind == "image" else ""
    return msg.get("body", "")


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(text: str) -> str:
    """Client-side input sanitization for outgoing messages: strips dangerous
    control characters (keeps \\n and \\t), trims edges, enforces a hard
    length cap. SQL injection is separately impossible because both the local
    store and the server use parameterized queries exclusively."""
    text = _CONTROL_CHARS.sub("", text)
    text = text.strip()
    return text[:MAX_MESSAGE_CHARS]


_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def _str(value, limit: int) -> Optional[str]:
    return value[:limit] if isinstance(value, str) else None


def _int(value, low: int, high: int) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if low <= value <= high else None


def sanitize_media(media) -> Optional[dict]:
    """Validate a peer-supplied media reference field by field.

    The block it came in is authenticated, but authenticated is not the same
    as well-formed: a buggy or hostile client can sign anything. Every field
    is type-checked and bounded before it reaches the database or the
    screen, and anything unrecognised is dropped rather than passed through.
    """
    if not isinstance(media, dict):
        return None
    kind = media.get("kind")
    if kind not in MEDIA_MIME:
        return None
    blob, sha = _str(media.get("blob"), 64), _str(media.get("sha"), 64)
    mime = _str(media.get("mime"), 40)
    size = _int(media.get("size"), 1, MAX_MEDIA_BYTES)
    try:
        key_ok = len(b64d(media.get("key", ""))) == 32
    except (TypeError, ValueError):
        key_ok = False
    if not (blob and _TOKEN_RE.match(blob) and sha and _HEX64_RE.match(sha)
            and mime in MEDIA_MIME[kind] and size and key_ok):
        return None
    out = {"kind": kind, "blob": blob, "sha": sha, "mime": mime,
           "size": size, "key": media["key"]}
    for field, low, high in (("w", 1, 20000), ("h", 1, 20000),
                             ("dur", 0, MAX_VOICE_SECONDS * 1000 + 999)):
        value = _int(media.get(field), low, high)
        if value is not None:
            out[field] = value
    for field, limit in (("thumb", 24000), ("wave", 400)):
        value = _str(media.get(field), limit + 1)
        if value is not None and len(value) <= limit:
            try:
                b64d(value)
                out[field] = value
            except (TypeError, ValueError):
                pass
    caption = _str(media.get("cap"), 1000)
    if caption:
        out["cap"] = sanitize_text(caption)
    return out


def sanitize_ext(x) -> dict:
    """The parts of a peer's extension block we keep: reply quote, forwarded
    mark, media reference. Routing fields (to/cid/xv/k) are consumed by the
    sync layer and never stored."""
    out = {}
    if not isinstance(x, dict):
        return out
    reply = x.get("reply")
    if isinstance(reply, dict):
        quoted = {}
        cid = _str(reply.get("cid"), 64)
        if cid and _TOKEN_RE.match(cid):
            quoted["cid"] = cid
        snip = _str(reply.get("snip"), 300)
        if snip:
            quoted["snip"] = sanitize_text(snip)[:300]
        author = _str(reply.get("from"), 64)
        if author and valid_address(author):
            quoted["from"] = author
        if reply.get("kind") in ("text", "image", "voice"):
            quoted["kind"] = reply["kind"]
        if quoted:
            out["reply"] = quoted
    if x.get("fwd") in (1, True):
        out["fwd"] = 1
    media = sanitize_media(x.get("media"))
    if media:
        out["media"] = media
    return out


# ---- v9.1 groups and reactions ----------------------------------------------
# A group is a list of members that every member keeps; the relay never knows
# one exists. A group message travels as one ordinary 1:1 envelope per
# member, each carrying {"grp": {"id", "v"}} in its signed extension block,
# so it is as end-to-end encrypted, recipient-bound and replay-proof as a
# direct message. Membership changes are signed control messages from the
# group's admin, applied only when their version is newer.
GROUP_PREFIX = "g:"            # messages.peer for a group's conversation
MAX_GROUP_MEMBERS = 32         # pairwise fan-out: one send per member
MAX_GROUP_NAME = 64
_GID_RE = re.compile(r"^[0-9a-f]{32}$")
#: Offered first in the reaction strip, in this order.
QUICK_REACTIONS = ("\U0001F44D", "\u2764\ufe0f", "\U0001F602", "\U0001F62E",
                   "\U0001F622", "\U0001F64F")


def group_peer(gid: str) -> str:
    return GROUP_PREFIX + gid


def gid_of(peer: Optional[str]) -> Optional[str]:
    """The group id behind a conversation key, or None for a direct chat."""
    if isinstance(peer, str) and peer.startswith(GROUP_PREFIX):
        gid = peer[len(GROUP_PREFIX):]
        return gid if _GID_RE.match(gid) else None
    return None


def sanitize_gid(value) -> Optional[str]:
    return value if isinstance(value, str) and _GID_RE.match(value) else None


def sanitize_emoji(value) -> Optional[str]:
    """A reaction: one emoji, which may be a sequence (skin tone, flag,
    ZWJ family) of up to 16 code points. '' means "take it back". Anything
    containing ASCII, letters, spaces or control characters is refused, so
    a reaction can never smuggle text onto someone's screen."""
    if value == "":
        return ""
    if not isinstance(value, str) or not 1 <= len(value) <= 16:
        return None
    for ch in value:
        o = ord(ch)
        if o < 0xa0 or ch.isspace() or ch.isalpha() or ch.isdigit():
            return None
    if all(ch in "\u200d\ufe0f\ufe0e" for ch in value):
        return None
    return value


def sanitize_group_state(raw) -> Optional[dict]:
    """Validate a peer-supplied group state, refusing it whole if any part
    is wrong. Every member's address must be the hash of the key given for
    it, so nobody can put a key of their choosing under someone else's
    address and read that person's share of the group's messages."""
    if not isinstance(raw, dict):
        return None
    gid = sanitize_gid(raw.get("id"))
    name = sanitize_text(_str(raw.get("name"), 400) or "")[:MAX_GROUP_NAME]
    version = _int(raw.get("v"), 1, 2 ** 31 - 1)
    admin = _str(raw.get("admin"), 64)
    members = raw.get("members")
    if not (gid and name.strip() and version and admin and
            isinstance(members, list) and
            2 <= len(members) <= MAX_GROUP_MEMBERS):
        return None
    out, seen = [], set()
    for member in members:
        if not isinstance(member, dict):
            return None
        address = _str(member.get("a"), 64)
        key = (_str(member.get("k"), 66) or "").lower()
        if not address or address in seen or not valid_address(address):
            return None
        try:
            pub = bytes.fromhex(key)
            compressed_to_pub(pub)
        except ValueError:
            return None
        if address_from_pub_bytes(pub) != address:
            return None
        username = _str(member.get("u"), 32)
        entry = {"a": address, "k": key}
        if username and valid_username(username):
            entry["u"] = username
        out.append(entry)
        seen.add(address)
    if admin not in seen:
        return None
    return {"id": gid, "name": name.strip(), "v": version, "admin": admin,
            "members": out}


MEDIA_AAD = b"fext.media.v1"


def compress_image(path: str) -> tuple:
    """Shrink a photo for sending: (jpeg_bytes, width, height, thumb_b64).

    Fits it inside IMAGE_MAX_EDGE, re-encodes it as a progressive JPEG and,
    by not passing them on, drops EXIF and every other metadata block —
    which is where phones keep the GPS position a photo was taken at. The
    camera's rotation flag is applied to the pixels first, because dropping
    it would otherwise turn portrait photos on their side.
    """
    from PIL import Image, ImageOps
    import io as _io
    with Image.open(path) as source:
        source.load()
        try:
            image = ImageOps.exif_transpose(source)
        except Exception:                        # malformed EXIF: ignore it
            image = source.copy()
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        flat = Image.new("RGB", image.size, (255, 255, 255))
        flat.paste(image, mask=image.split()[-1])
        image = flat
    else:
        image = image.convert("RGB")
    image.thumbnail((IMAGE_MAX_EDGE, IMAGE_MAX_EDGE), Image.LANCZOS)
    out = _io.BytesIO()
    image.save(out, "JPEG", quality=IMAGE_JPEG_QUALITY, optimize=True,
               progressive=True)
    thumb = image.copy()
    thumb.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.LANCZOS)
    small = _io.BytesIO()
    thumb.save(small, "JPEG", quality=THUMB_QUALITY)
    return out.getvalue(), image.width, image.height, b64e(small.getvalue())


def voice_label(duration_ms: int) -> str:
    seconds = max(0, int(round((duration_ms or 0) / 1000.0)))
    return f"Voice message ({seconds // 60}:{seconds % 60:02d})"


def seal_media(data: bytes) -> tuple:
    """Encrypt one media file for upload. Returns (blob, key_b64, sha_hex).

    A fresh 256-bit key per file, never reused, so the relay's copy is just
    noise and forwarding a photo can share its reference without sharing any
    other key. The key travels inside the end-to-end envelope; `sha` lets
    the recipient reject a corrupted or substituted download BEFORE trying
    to decrypt it.
    """
    key, nonce = os.urandom(32), os.urandom(12)
    blob = nonce + AESGCM(key).encrypt(nonce, data, MEDIA_AAD)
    return blob, b64e(key), hashlib.sha256(blob).hexdigest()


def open_media(blob: bytes, key_b64: str, sha_hex: str) -> bytes:
    """Verify and decrypt a downloaded blob; raises ValueError on mismatch."""
    if hashlib.sha256(blob).hexdigest() != sha_hex:
        raise ValueError("media failed its integrity check")
    if len(blob) < 12 + 16:
        raise ValueError("media blob is truncated")
    try:
        return AESGCM(b64d(key_b64)).decrypt(blob[:12], blob[12:], MEDIA_AAD)
    except Exception:
        raise ValueError("media could not be decrypted")


def media_suffix(kind: str, mime: Optional[str]) -> str:
    """The file extension a received photo or voice note is saved under —
    by its type, so a desktop opens it with the right program."""
    if kind == "image":
        return "jpg"
    if kind == "voice":
        return {"audio/wav": "wav", "audio/ogg": "ogg", "audio/aac": "aac",
                "audio/3gpp": "3gp", "audio/amr": "amr"}.get(mime or "",
                                                              "m4a")
    return "bin"


def discard_media_file(path: Optional[str]) -> None:
    """Delete a decrypted media file — but only one inside MEDIA_DIR, so a
    corrupted database row can never point this at anything else."""
    if not path:
        return
    try:
        target = Path(path).resolve()
        if MEDIA_DIR.resolve() in target.parents and target.is_file():
            target.unlink()
    except OSError:
        log.warning("could not remove media file %s", path)


def valid_username(name: str) -> bool:
    return bool(re.match(r"^[A-Za-z0-9_]{3,32}$", name))


def valid_address(value: str) -> bool:
    if not re.match(r"^F[1-9A-HJ-NP-Za-km-z]{20,40}$", value):
        return False
    try:
        b58check_decode(value)
        return True
    except ValueError:
        return False


# =============================================================================
#  MessageFlowLog — the client half of the golden-tick audit trail
# =============================================================================
class MessageFlowLog:
    """Mirrors the relay's flow log, line for line and format for format, so
    the two can be diffed when a message goes missing.

        ✓    1/3   the relay accepted our ciphertext (or: we accepted theirs)
        ✓✓   2/3   the recipient holds it
        ✓✓✓  3/3   the recipient read it

    Every line carries who -> whom and a fragment of the ciphertext. The
    fragment is the join key: it is the one value that exists identically on
    both sides of the wire, so `grep` on it lines a client's story up against
    the relay's without either side having to log anything sensitive.

    PRIVACY NOTE. Unlike the relay, this process legitimately holds
    plaintext — and it stays out of the log regardless. What lands here is
    what the relay could already see: addresses, sizes, timings. Message
    bodies never appear.
    """

    _TIERS = {0: "·  ", 1: "✓  ", 2: "✓✓ ", 3: "✓✓✓"}
    _LABELS = {0: "queued locally (not yet on the relay)",
               1: "accepted by relay",
               2: "delivered to recipient",
               3: "read by recipient"}

    #: How much base64 ciphertext to echo. Matches the relay's default.
    FRAGMENT_CHARS = 24

    @classmethod
    def fragment(cls, envelope: Optional[dict]) -> str:
        """The head of the base64 ciphertext plus its true byte length —
        opaque, but unique enough to identify one message anywhere."""
        if not isinstance(envelope, dict):
            return "ct=?"
        ct = envelope.get("ciphertext", "")
        if not isinstance(ct, str):
            return "ct=?"
        try:
            size = len(b64d(ct))
        except (ValueError, TypeError):
            size = 0
        head = ct[:cls.FRAGMENT_CHARS] + ("…" if len(ct) > cls.FRAGMENT_CHARS
                                          else "")
        return f"ct={head} ({size}B)"

    @staticmethod
    def who(address: str, username: Optional[str] = None) -> str:
        short = (address if len(address) <= 14
                 else f"{address[:6]}…{address[-4:]}")
        return f"{username}({short})" if username else short

    @classmethod
    def tick(cls, tier: int, server_id, sender: str, recipient: str,
             extra: str = "") -> None:
        flow_log.info("%s %d/3  #%s  %s -> %s  %s%s",
                      cls._TIERS.get(tier, "?"), tier,
                      server_id if server_id else "-", sender, recipient,
                      cls._LABELS.get(tier, ""),
                      f"  {extra}" if extra else "")

    @classmethod
    def failed(cls, sender: str, recipient: str, reason: str,
               extra: str = "") -> None:
        flow_log.warning("✗   0/3  %s -> %s  NOT SENT: %s%s",
                         sender, recipient, reason,
                         f"  {extra}" if extra else "")

    @classmethod
    def dropped(cls, server_id, sender: str, reason: str) -> None:
        """An INBOUND message we could not take custody of. Loud on purpose:
        this is the exact moment at which a message is at risk of being lost,
        and the whole 2/3-tick bug was this path failing quietly."""
        flow_log.error("✗   in   #%s  from %s  NOT STORED: %s "
                       "(withholding ack so the relay keeps its copy)",
                       server_id if server_id else "-", sender, reason)


def open_folder_in_explorer(path: Path) -> bool:
    """Reveal `path` in the OS file manager (desktop only). On mobile there
    is no file manager to open into the app sandbox; callers show the path
    as text instead. Returns True when a file manager was launched."""
    path.mkdir(parents=True, exist_ok=True)
    if IS_MOBILE:
        return False
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(str(path))                  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except OSError:
        return False


def run_on_ui(fn: Callable, *args) -> None:
    """Hop onto the Kivy main thread (the tkinter `after(0, …)` of this app).
    ALL widget mutation from worker threads goes through here."""
    Clock.schedule_once(lambda _dt: fn(*args), 0)


# =============================================================================
#  Platform glue — Android through pyjnius, with desktop fallbacks
# =============================================================================
# Everything Android-specific lives here so the rest of the file stays
# platform-neutral. Each entry point is defensive: on a desktop, or if a
# device's media stack misbehaves, the caller gets an exception or a False
# and turns it into a status line. None of it may take the app down.

REQ_PICK_IMAGE = 4711
REQ_TAKE_PHOTO = 4712
NOTIFY_CHANNEL = "messages"


def _jni():
    from jnius import autoclass, cast            # Android only
    return autoclass, cast


def android_context():
    """The Activity in the app process; the Service in the sync service."""
    autoclass, _ = _jni()
    activity = autoclass("org.kivy.android.PythonActivity").mActivity
    if activity is not None:
        return activity
    return autoclass("org.kivy.android.PythonService").mService


def android_sdk() -> int:
    autoclass, _ = _jni()
    return int(autoclass("android.os.Build$VERSION").SDK_INT)


def _charseq(text: str):
    autoclass, cast = _jni()
    return cast("java.lang.CharSequence", autoclass("java.lang.String")(text))


def android_request_permissions(permissions: list, callback=None) -> None:
    """Runtime permissions (Android 6+). Missing ones are simply asked for;
    the OS remembers a refusal, so asking again is harmless."""
    try:
        from android.permissions import request_permissions
        request_permissions(permissions, callback)
    except Exception:
        log.exception("could not request %s", permissions)


def android_has_permission(permission: str) -> bool:
    try:
        from android.permissions import check_permission
        return bool(check_permission(permission))
    except Exception:
        return False


def android_share_text(text: str) -> None:
    autoclass, cast = _jni()
    Intent = autoclass("android.content.Intent")
    intent = Intent(Intent.ACTION_SEND)
    intent.setType("text/plain")
    intent.putExtra(Intent.EXTRA_TEXT, _charseq(text))
    android_context().startActivity(
        Intent.createChooser(intent, _charseq("Share")))


def android_share_image(path: str) -> None:
    """Share a photo with another app.

    Other apps cannot read this app's private storage, and Android 7+
    forbids handing them file:// paths anyway, so the photo is published
    through MediaStore first and the resulting content:// URI is shared.
    (A side effect is that a shared photo also lands in the gallery.)
    """
    autoclass, cast = _jni()
    ctx = android_context()
    media = autoclass("android.provider.MediaStore$Images$Media")
    uri_text = media.insertImage(ctx.getContentResolver(), path,
                                 "Ferritext photo", None)
    if not uri_text:
        raise RuntimeError("the gallery refused the photo")
    Intent = autoclass("android.content.Intent")
    intent = Intent(Intent.ACTION_SEND)
    intent.setType("image/jpeg")
    intent.putExtra(Intent.EXTRA_STREAM, cast(
        "android.os.Parcelable", autoclass("android.net.Uri").parse(uri_text)))
    intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    ctx.startActivity(Intent.createChooser(intent, _charseq("Share photo")))


def _notification_id(peer: str) -> int:
    """Stable per chat, so a second message replaces the first notification
    instead of stacking. (hash() is randomised per process — useless here.)"""
    return 1000 + int(hashlib.sha256(peer.encode()).hexdigest()[:6], 16)


def android_notify(peer: str, title: str, text: str) -> None:
    """Post a 'new message' notification; tapping it opens that chat."""
    autoclass, cast = _jni()
    ctx = android_context()
    Context = autoclass("android.content.Context")
    manager = cast("android.app.NotificationManager",
                   ctx.getSystemService(Context.NOTIFICATION_SERVICE))
    Builder = autoclass("android.app.Notification$Builder")
    if android_sdk() >= 26:
        Channel = autoclass("android.app.NotificationChannel")
        importance = autoclass("android.app.NotificationManager"
                               ).IMPORTANCE_HIGH
        channel = Channel(NOTIFY_CHANNEL, _charseq("Messages"), importance)
        channel.setDescription("New messages")
        manager.createNotificationChannel(channel)
        builder = Builder(ctx, NOTIFY_CHANNEL)
    else:
        builder = Builder(ctx)
        builder.setPriority(1)                    # PRIORITY_HIGH
    res = ctx.getResources()
    icon = res.getIdentifier("ic_stat_ferritext", "drawable",
                             ctx.getPackageName())
    builder.setSmallIcon(icon or ctx.getApplicationInfo().icon)
    builder.setContentTitle(_charseq(title))
    builder.setContentText(_charseq(text))
    builder.setAutoCancel(True)
    # The launcher intent, not an explicit Activity class: it is what a tap
    # on the app icon would send, so it resumes the running instance.
    intent = ctx.getPackageManager().getLaunchIntentForPackage(
        ctx.getPackageName())
    Intent = autoclass("android.content.Intent")
    intent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP |
                    Intent.FLAG_ACTIVITY_CLEAR_TOP |
                    Intent.FLAG_ACTIVITY_NEW_TASK)
    intent.putExtra("fext_peer", peer)
    PendingIntent = autoclass("android.app.PendingIntent")
    flags = PendingIntent.FLAG_UPDATE_CURRENT
    if android_sdk() >= 23:
        flags |= PendingIntent.FLAG_IMMUTABLE    # mandatory from Android 12
    builder.setContentIntent(PendingIntent.getActivity(
        ctx, _notification_id(peer), intent, flags))
    manager.notify(_notification_id(peer), builder.build())


def android_clear_notification(peer: str) -> None:
    try:
        autoclass, cast = _jni()
        ctx = android_context()
        Context = autoclass("android.content.Context")
        cast("android.app.NotificationManager",
             ctx.getSystemService(Context.NOTIFICATION_SERVICE)
             ).cancel(_notification_id(peer))
    except Exception:
        pass


def android_pick_image(callback: Callable[[str], None]) -> None:
    """Open the system photo picker; `callback(path)` gets a private copy.

    The picker hands back a content:// URI. Reading it through a detached
    file descriptor gives Python an ordinary file object — no byte-by-byte
    marshalling across the JNI boundary.
    """
    autoclass, _ = _jni()
    from android import activity

    def on_result(request, result, data):
        if request != REQ_PICK_IMAGE:
            return
        activity.unbind(on_activity_result=on_result)
        if result != -1 or data is None:          # -1 == RESULT_OK
            return
        try:
            resolver = android_context().getContentResolver()
            fd = resolver.openFileDescriptor(data.getData(), "r").detachFd()
            MEDIA_DIR.mkdir(parents=True, exist_ok=True)
            target = MEDIA_DIR / f"pick_{uuid.uuid4().hex}"
            with os.fdopen(fd, "rb") as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
        except Exception:
            log.exception("could not read the picked image")
            return
        run_on_ui(callback, str(target))

    activity.bind(on_activity_result=on_result)
    Intent = autoclass("android.content.Intent")
    intent = Intent(Intent.ACTION_GET_CONTENT)
    intent.setType("image/*")
    intent.addCategory(Intent.CATEGORY_OPENABLE)
    android_context().startActivityForResult(
        Intent.createChooser(intent, _charseq("Choose a photo")),
        REQ_PICK_IMAGE)


def android_take_photo(callback: Callable[[str], None]) -> None:
    """Open the camera app; `callback(path)` gets the photo it took.

    The camera writes the picture through a FileProvider grant straight
    into this app's private cache — never the gallery, never storage another
    app can read — and it is moved into the media folder from there. The
    provider is declared by tools/p4a_hook.py. Asking for no CAMERA
    permission is deliberate: the camera app holds its own, and an app that
    declares it without being granted it may not use this intent at all.
    """
    autoclass, cast = _jni()
    from android import activity
    ctx = android_context()
    File = autoclass("java.io.File")
    folder = File(ctx.getCacheDir(), "camera")
    folder.mkdirs()
    shot = File(folder, f"shot_{uuid.uuid4().hex}.jpg")
    path = shot.getAbsolutePath()
    uri = autoclass("androidx.core.content.FileProvider").getUriForFile(
        ctx, ctx.getPackageName() + ".fileprovider", shot)

    def on_result(request, result, _data):
        if request != REQ_TAKE_PHOTO:
            return
        activity.unbind(on_activity_result=on_result)
        try:
            if result != -1 or not os.path.getsize(path):    # -1: RESULT_OK
                raise OSError("no photo")
            MEDIA_DIR.mkdir(parents=True, exist_ok=True)
            target = MEDIA_DIR / f"shot_{uuid.uuid4().hex}.jpg"
            shutil.move(path, target)
        except OSError:
            try:
                os.remove(path)
            except OSError:
                pass
            return
        run_on_ui(callback, str(target))

    activity.bind(on_activity_result=on_result)
    Intent = autoclass("android.content.Intent")
    MediaStore = autoclass("android.provider.MediaStore")
    intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
    intent.putExtra(MediaStore.EXTRA_OUTPUT, cast("android.os.Parcelable", uri))
    # Camera apps differ in where they look for the grant; give it both ways.
    intent.setClipData(autoclass("android.content.ClipData").newRawUri(
        _charseq("photo"), uri))
    intent.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION |
                    Intent.FLAG_GRANT_READ_URI_PERMISSION)
    try:
        ctx.startActivityForResult(intent, REQ_TAKE_PHOTO)
    except Exception:
        activity.unbind(on_activity_result=on_result)
        raise


# ---- background delivery (Android) ------------------------------------------
# Notifications only reach someone if something is listening for messages.
# While the app's process is alive, its own SyncWorker does that. Android
# kills background apps freely, though, so a small foreground service (a
# separate process, see service.py and run_background_service) takes over
# whenever the app's worker has gone quiet, and steps aside again the moment
# it is back. Each side proves it is alive by touching a heartbeat file; two
# processes never hold a connection for the same identity at once.
SERVICE_NAME = "fextsync"          # must match `services =` in buildozer.spec
HEARTBEAT_EVERY = 10.0             # seconds between beats
HEARTBEAT_STALE = 35.0             # older than this: that process is gone


def _beat_path(owner: str) -> Path:
    return DATA_DIR / f".beat_{owner}"


def write_heartbeat(owner: str) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _beat_path(owner).write_text(f"{time.time():.1f}")
    except OSError:
        pass


def heartbeat_fresh(owner: str) -> bool:
    try:
        return time.time() - float(_beat_path(owner).read_text()) \
            < HEARTBEAT_STALE
    except (OSError, ValueError):
        return False


def load_prefs() -> dict:
    """Device-wide preferences shared by the app and its service."""
    try:
        data = json.loads((DATA_DIR / "prefs.json").read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_pref(key: str, value) -> None:
    prefs = load_prefs()
    prefs[key] = value
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = DATA_DIR / "prefs.json.tmp"
        tmp.write_text(json.dumps(prefs), "utf-8")
        tmp.replace(DATA_DIR / "prefs.json")
    except OSError:
        log.exception("could not save preferences")


def background_delivery_enabled() -> bool:
    return bool(load_prefs().get("background_delivery", True))


def android_style_system_bars() -> None:
    """Paint Android's status and navigation bars in the page colour, with
    dark icons on the light theme and light icons on the dark one."""
    if not IS_ANDROID:
        return
    try:
        from android.runnable import run_on_ui_thread   # type: ignore
    except Exception:
        return
    bg, light = T.bg, not T.dark

    @run_on_ui_thread
    def apply():
        try:
            autoclass, _ = _jni()
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            Color_ = autoclass("android.graphics.Color")
            LayoutParams = autoclass("android.view.WindowManager$LayoutParams")
            window = activity.getWindow()
            window.clearFlags(LayoutParams.FLAG_TRANSLUCENT_STATUS)
            window.addFlags(LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS)
            window.setStatusBarColor(Color_.parseColor(bg))
            window.setNavigationBarColor(Color_.parseColor(bg))
            view = window.getDecorView()
            flags = view.getSystemUiVisibility()
            light_bars = 0x2000 | (0x10 if android_sdk() >= 26 else 0)
            flags = flags | light_bars if light else flags & ~light_bars
            view.setSystemUiVisibility(flags)
        except Exception:
            log.exception("could not colour the system bars")
    apply()


def windows_window_icons(ico_path: str) -> None:
    """Hand Windows the .ico's own small and large frames for the title bar,
    taskbar and Alt-Tab, at the sizes it asks for — rather than letting it
    shrink one picture to 16 pixels itself."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                      ctypes.c_uint, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_uint]
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                        ctypes.c_size_t, ctypes.c_size_t]
        hwnd = Window.get_window_info().window
        for which, w_metric, h_metric in ((0, 49, 50), (1, 11, 12)):
            # ICON_SMALL: SM_CXSMICON/SM_CYSMICON; ICON_BIG: SM_CXICON/...
            icon = user32.LoadImageW(None, ico_path, 1,             # ICON
                                     user32.GetSystemMetrics(w_metric),
                                     user32.GetSystemMetrics(h_metric),
                                     0x10)                  # LOADFROMFILE
            if icon:
                user32.SendMessageW(hwnd, 0x80, which, icon)  # WM_SETICON
    except Exception:
        log.exception("could not set the window's icons")


def android_service(start: bool) -> None:
    """Start or stop the background sync service (Android only)."""
    autoclass, _ = _jni()
    ctx = android_context()
    service = autoclass(f"{ctx.getPackageName()}.Service"
                        f"{SERVICE_NAME.capitalize()}")
    if start:
        service.start(ctx, "ic_stat_ferritext", APP_NAME,
                      "Ready for new messages", "")
    else:
        service.stop(ctx)


class VoiceRecorder:
    """Android voice-note recorder (MediaRecorder, AAC in MP4).

    AAC-LC at 16 kHz mono is the format every Android version both records
    and plays back, and the one Windows and macOS open natively, so a voice
    note works on every device it lands on. Opus would be smaller, but
    Android only gained an Opus encoder in 10 and older phones could not
    play it. The level meter is sampled while recording, which yields the
    waveform for free instead of decoding the audio afterwards.
    """

    def __init__(self) -> None:
        autoclass, _ = _jni()
        MediaRecorder = autoclass("android.media.MediaRecorder")
        AudioSource = autoclass("android.media.MediaRecorder$AudioSource")
        OutputFormat = autoclass("android.media.MediaRecorder$OutputFormat")
        AudioEncoder = autoclass("android.media.MediaRecorder$AudioEncoder")
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        self.path = str(MEDIA_DIR / f"rec_{uuid.uuid4().hex}.m4a")
        self._rec = MediaRecorder()
        self._rec.setAudioSource(AudioSource.MIC)
        self._rec.setOutputFormat(OutputFormat.MPEG_4)
        self._rec.setAudioEncoder(AudioEncoder.AAC)
        self._rec.setAudioChannels(1)
        self._rec.setAudioSamplingRate(VOICE_SAMPLE_RATE)
        self._rec.setAudioEncodingBitRate(VOICE_BITRATE)
        self._rec.setOutputFile(self.path)
        self._rec.prepare()
        self._rec.start()
        self._started = time.monotonic()
        self.levels: list = []
        self._tick = Clock.schedule_interval(self._sample, 0.1)

    def _sample(self, _dt) -> None:
        try:
            self.levels.append(int(self._rec.getMaxAmplitude()))
        except Exception:
            pass

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)

    def _halt(self) -> None:
        self._tick.cancel()
        try:
            self._rec.stop()
        finally:
            self._rec.release()

    def stop(self) -> tuple:
        """Returns (path, duration_ms, waveform_b64)."""
        duration = self.elapsed_ms
        self._halt()
        return self.path, duration, waveform_b64(self.levels)

    def cancel(self) -> None:
        try:
            self._halt()
        except Exception:
            pass
        discard_media_file(self.path)


# ---- voice notes on a computer -----------------------------------------------
# A computer records plain 16 kHz mono PCM and keeps it as WAV: no encoder is
# needed, and Android and every desktop player open it. It is ten times the
# size of a phone's AAC, so a computer's notes are capped at four minutes,
# which keeps them under the media limit.
PCM_RATE = 16000
PCM_BYTES_PER_SECOND = PCM_RATE * 2
MAX_PCM_SECONDS = min(MAX_VOICE_SECONDS, 240)
_PCM_CHUNK = PCM_BYTES_PER_SECOND // 10            # 100 ms


class _SoundDeviceCapture:
    """The `sounddevice` package (PortAudio), wherever it is installed."""

    name = "sounddevice"

    def __init__(self, feed: Callable[[bytes], None]) -> None:
        import sounddevice                                 # type: ignore
        self.failed = False

        def callback(indata, _frames, _time, _status):
            feed(bytes(indata))
        self._stream = sounddevice.RawInputStream(
            samplerate=PCM_RATE, channels=1, dtype="int16",
            blocksize=_PCM_CHUNK // 2, callback=callback)
        self._stream.start()

    def stop(self) -> None:
        try:
            self._stream.stop()
        finally:
            self._stream.close()


class _WinMMCapture:
    """Windows' own wave-in API (winmm.dll), through ctypes: present on
    every Windows machine, so recording needs nothing installed.

    Eight 100 ms buffers are queued with the driver; a thread hands each
    one over as it fills (WHDR_DONE) and queues it again.
    """

    name = "winmm"
    WHDR_DONE = 0x00000001
    WAVE_MAPPER = 0xFFFFFFFF

    def __init__(self, feed: Callable[[bytes], None]) -> None:
        import ctypes
        from ctypes import wintypes
        self._ct, self.failed = ctypes, False
        self._feed = feed

        class WAVEFORMATEX(ctypes.Structure):
            _fields_ = [("wFormatTag", wintypes.WORD),
                        ("nChannels", wintypes.WORD),
                        ("nSamplesPerSec", wintypes.DWORD),
                        ("nAvgBytesPerSec", wintypes.DWORD),
                        ("nBlockAlign", wintypes.WORD),
                        ("wBitsPerSample", wintypes.WORD),
                        ("cbSize", wintypes.WORD)]

        class WAVEHDR(ctypes.Structure):
            pass
        WAVEHDR._fields_ = [("lpData", ctypes.c_void_p),
                            ("dwBufferLength", wintypes.DWORD),
                            ("dwBytesRecorded", wintypes.DWORD),
                            ("dwUser", ctypes.c_size_t),
                            ("dwFlags", wintypes.DWORD),
                            ("dwLoops", wintypes.DWORD),
                            ("lpNext", ctypes.POINTER(WAVEHDR)),
                            ("reserved", ctypes.c_size_t)]
        self._hdr_size = ctypes.sizeof(WAVEHDR)
        mm = ctypes.WinDLL("winmm")
        handle_p = ctypes.POINTER(ctypes.c_void_p)
        hdr_p = ctypes.POINTER(WAVEHDR)
        mm.waveInOpen.argtypes = [handle_p, wintypes.UINT,
                                  ctypes.POINTER(WAVEFORMATEX),
                                  ctypes.c_size_t, ctypes.c_size_t,
                                  wintypes.DWORD]
        for fn in ("waveInPrepareHeader", "waveInAddBuffer",
                   "waveInUnprepareHeader"):
            getattr(mm, fn).argtypes = [ctypes.c_void_p, hdr_p, wintypes.UINT]
        for fn in ("waveInStart", "waveInStop", "waveInReset", "waveInClose"):
            getattr(mm, fn).argtypes = [ctypes.c_void_p]
        for fn in ("waveInOpen", "waveInPrepareHeader", "waveInAddBuffer",
                   "waveInUnprepareHeader", "waveInStart", "waveInStop",
                   "waveInReset", "waveInClose"):
            getattr(mm, fn).restype = wintypes.UINT
        self._mm = mm
        fmt = WAVEFORMATEX(1, 1, PCM_RATE, PCM_BYTES_PER_SECOND, 2, 16, 0)
        self._handle = ctypes.c_void_p()
        rc = mm.waveInOpen(ctypes.byref(self._handle), self.WAVE_MAPPER,
                           ctypes.byref(fmt), 0, 0, 0)       # CALLBACK_NULL
        if rc != 0:
            raise OSError(f"no microphone (waveInOpen returned {rc})")
        self._buffers = [ctypes.create_string_buffer(_PCM_CHUNK)
                         for _ in range(8)]
        self._headers = [WAVEHDR(ctypes.cast(b, ctypes.c_void_p), _PCM_CHUNK,
                                 0, 0, 0, 0, None, 0) for b in self._buffers]
        for hdr in self._headers:
            mm.waveInPrepareHeader(self._handle, ctypes.byref(hdr),
                                   self._hdr_size)
            mm.waveInAddBuffer(self._handle, ctypes.byref(hdr),
                               self._hdr_size)
        if mm.waveInStart(self._handle) != 0:
            mm.waveInReset(self._handle)          # hand the buffers back
            self._close()
            raise OSError("the microphone would not start")
        self._running = True
        self._next = 0
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _take(self, requeue: bool) -> bool:
        """Hand over the next buffer if the driver has filled it."""
        hdr = self._headers[self._next]
        if not hdr.dwFlags & self.WHDR_DONE:
            return False
        if hdr.dwBytesRecorded:
            self._feed(self._ct.string_at(hdr.lpData, hdr.dwBytesRecorded))
        hdr.dwFlags &= ~self.WHDR_DONE
        hdr.dwBytesRecorded = 0
        if requeue:
            self._mm.waveInAddBuffer(self._handle, self._ct.byref(hdr),
                                     self._hdr_size)
        self._next = (self._next + 1) % len(self._headers)
        return True

    def _pump(self) -> None:
        while self._running:
            if not self._take(requeue=True):
                time.sleep(0.02)

    def _close(self) -> None:
        for hdr in self._headers:
            self._mm.waveInUnprepareHeader(self._handle, self._ct.byref(hdr),
                                           self._hdr_size)
        self._mm.waveInClose(self._handle)

    def stop(self) -> None:
        self._running = False
        self._thread.join(timeout=1.0)
        # Reset returns every queued buffer, the half-filled one included.
        self._mm.waveInReset(self._handle)
        for _ in range(len(self._headers)):
            if not self._take(requeue=False):
                break
        self._close()


class _ProcessCapture:
    """A command-line recorder writing raw PCM to its standard output:
    arecord (ALSA) or parec (PulseAudio, PipeWire) on Linux, ffmpeg or
    SoX's rec wherever they are installed."""

    name = "process"

    def __init__(self, feed: Callable[[bytes], None], argv: list) -> None:
        self.name, self.failed = os.path.basename(argv[0]), False
        self._proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL)

        def pump():
            got = False
            while True:
                data = self._proc.stdout.read(_PCM_CHUNK)
                if not data:
                    break
                got = True
                feed(data)
            if not got:
                self.failed = True               # exited at once: no device
        self._thread = threading.Thread(target=pump, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._thread.join(timeout=1.0)


def _capture_commands() -> list:
    system = platform.system()
    rate = str(PCM_RATE)
    raw_out = ["-ac", "1", "-ar", rate, "-f", "s16le", "-"]
    candidates = []
    if system == "Linux":
        candidates += [
            ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", rate,
             "-c", "1"],
            ["parec", "--raw", "--format=s16le", f"--rate={rate}",
             "--channels=1"],
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "pulse",
             "-i", "default"] + raw_out]
    elif system == "Darwin":
        candidates += [["ffmpeg", "-nostdin", "-loglevel", "error", "-f",
                        "avfoundation", "-i", ":0"] + raw_out]
    if system != "Windows":
        candidates += [["rec", "-q", "-t", "raw", "-b", "16", "-e",
                        "signed-integer", "-r", rate, "-c", "1", "-"]]
    found = []
    for argv in candidates:
        exe = shutil.which(argv[0])
        if exe:
            found.append([exe] + argv[1:])
    return found


def _capture_routes() -> list:
    """Ways this computer can record, best first, as factories of feed."""
    routes = []
    try:
        import importlib.util
        if importlib.util.find_spec("sounddevice") is not None:
            routes.append(_SoundDeviceCapture)
    except (ImportError, ValueError):
        pass
    if platform.system() == "Windows":
        routes.append(_WinMMCapture)
    for argv in _capture_commands():
        routes.append(lambda feed, argv=argv: _ProcessCapture(feed, argv))
    return routes


def desktop_recording_available() -> bool:
    return not IS_MOBILE and bool(_capture_routes())


class PcmRecorder:
    """Records a voice note on a computer, through the first capture route
    that opens; same interface as VoiceRecorder.

    The level meter (and so the waveform) comes from the samples
    themselves: the loudest of every 100 ms.
    """

    max_seconds = MAX_PCM_SECONDS

    def __init__(self) -> None:
        import array
        self._array = array.array
        self._lock = threading.Lock()
        self._pcm = bytearray()
        self._pending = b""
        self.levels: list = []
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        self.path = str(MEDIA_DIR / f"rec_{uuid.uuid4().hex}.wav")
        self._source = None
        errors = []
        for route in _capture_routes():
            try:
                self._source = route(self._feed)
                break
            except Exception as exc:
                errors.append(str(exc) or type(exc).__name__)
        if self._source is None:
            raise OSError("no way to record here" + (
                f" ({'; '.join(errors)})" if errors else ""))
        log.info("recording through %s", self._source.name)

    def _feed(self, data: bytes) -> None:
        with self._lock:
            room = self.max_seconds * PCM_BYTES_PER_SECOND - len(self._pcm)
            if room <= 0:
                return
            data = data[:room]
            self._pcm.extend(data)
            buf = self._pending + data
            whole = len(buf) - len(buf) % _PCM_CHUNK
            for i in range(0, whole, _PCM_CHUNK):
                samples = self._array("h", buf[i:i + _PCM_CHUNK])
                if sys.byteorder == "big":
                    samples.byteswap()
                self.levels.append(max(max(samples), -min(samples)))
            self._pending = buf[whole:]

    @property
    def failed(self) -> bool:
        return bool(getattr(self._source, "failed", False)) and not self._pcm

    @property
    def elapsed_ms(self) -> int:
        with self._lock:
            return len(self._pcm) * 1000 // PCM_BYTES_PER_SECOND

    def _halt(self) -> None:
        source, self._source = self._source, None
        if source is not None:
            source.stop()

    def stop(self) -> tuple:
        """Returns (path, duration_ms, waveform_b64)."""
        self._halt()
        import wave
        with self._lock:
            pcm = bytes(self._pcm)
            levels = list(self.levels)
        with wave.open(self.path, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(PCM_RATE)
            out.writeframes(pcm)
        return (self.path, len(pcm) * 1000 // PCM_BYTES_PER_SECOND,
                waveform_b64(levels))

    def cancel(self) -> None:
        try:
            self._halt()
        except Exception:
            pass
        discard_media_file(self.path)


def waveform_b64(levels: list, bars: int = 48) -> str:
    """Down-sample level readings to `bars` bytes (0-255), base64."""
    if not levels:
        return ""
    peak = max(levels) or 1
    step = len(levels) / float(bars)
    out = bytearray()
    for i in range(bars):
        chunk = levels[int(i * step):max(int((i + 1) * step), int(i * step) + 1)]
        out.append(min(255, int(255 * (max(chunk) if chunk else 0) / peak)))
    return b64e(bytes(out))


class VoicePlayer:
    """Plays one voice note at a time.

    Android: MediaPlayer, polled for progress (no Java listener class needed).
    Desktop: Kivy's audio stack has no AAC decoder, so the file is handed to
    the operating system's default player instead.
    """

    def __init__(self) -> None:
        self._mp = None
        self._poll = None
        self.path: Optional[str] = None
        self.on_progress: Optional[Callable] = None   # (path, fraction)
        self.on_stop: Optional[Callable] = None       # (path)

    @property
    def playing(self) -> bool:
        return self._mp is not None

    def toggle(self, path: str) -> None:
        if self.path == path and self._mp is not None:
            self.stop()
            return
        self.stop()
        if not IS_ANDROID:
            open_with_system(path)
            return
        autoclass, _ = _jni()
        mp = autoclass("android.media.MediaPlayer")()
        mp.setDataSource(path)
        mp.prepare()
        mp.start()
        self._mp, self.path = mp, path
        self._poll = Clock.schedule_interval(self._check, 0.15)

    def _check(self, _dt) -> None:
        mp = self._mp
        if mp is None:
            return
        try:
            if not mp.isPlaying():
                self.stop()
                return
            total = mp.getDuration() or 1
            if self.on_progress:
                self.on_progress(self.path, mp.getCurrentPosition() / total)
        except Exception:
            self.stop()

    def stop(self) -> None:
        if self._poll is not None:
            self._poll.cancel()
            self._poll = None
        mp, path = self._mp, self.path
        self._mp, self.path = None, None
        if mp is not None:
            try:
                mp.stop()
                mp.release()
            except Exception:
                pass
        if path and self.on_stop:
            self.on_stop(path)


def open_with_system(path: str) -> bool:
    """Open a file in the OS's default application (desktop)."""
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(path)                     # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        log.warning("no application to open %s", path)
        return False


_AVATAR_CACHE: dict = {}


def avatar_color(seed: str) -> str:
    """Stable warm colour for an address/identity (hex string).
    Cached: this is called for every row and bubble avatar, and a SHA-256
    per repaint is pure waste when the mapping never changes."""
    cached = _AVATAR_CACHE.get(seed)
    if cached is None:
        digest = hashlib.sha256(seed.encode("utf-8")).digest()
        cached = AVATAR_PALETTE[digest[0] % len(AVATAR_PALETTE)]
        _AVATAR_CACHE[seed] = cached
    return cached


def initial_of(name: str) -> str:
    for ch in name:
        if ch.isalnum():
            return ch.upper()
    return "?"


def qr_texture(data: str, fg: str = "#1B1915", bg: str = "#FFFFF8"):
    """Render a QR code to a Kivy texture (via an in-memory PNG)."""
    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color=fg, back_color=bg).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return CoreImage(buf, ext="png").texture


def pretty_time(iso: str) -> str:
    try:
        stamp = datetime.fromisoformat(iso)
        return stamp.astimezone().strftime("%H:%M")
    except ValueError:
        return iso[11:16]


def pretty_day(iso: str) -> str:
    try:
        stamp = datetime.fromisoformat(iso).astimezone()
        today = datetime.now().astimezone().date()
        if stamp.date() == today:
            return stamp.strftime("%H:%M")
        return stamp.strftime("%d %b")
    except ValueError:
        return ""

# =============================================================================
#  IdentityManager — multi-identity secp256k1 key storage (~/.fext)
# =============================================================================
class IdentityManager:
    """Owns ~/.fext/identities.json (chmod 600): a list of secp256k1
    identities the user can switch between WITHOUT restarting the app.

    Each identity is Bitcoin-compatible by construction — the private key
    exported here (hex or WIF) imports directly into FEXT Core to spend
    coins. New identities are either MINED (grinding until the version-0x24
    Base58Check address starts with 'Fec') or IMPORTED from a raw-hex / WIF
    private key the user already controls.

    Private keys live ONLY in this file and this process's memory; they are
    never transmitted anywhere (audit: this class has no network imports).
    """

    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._data: dict = {"version": 2, "active": None, "identities": []}
        self._load()

    # ---- persistence ---------------------------------------------------------
    def _load(self) -> None:
        if IDENTITIES_PATH.exists():
            try:
                self._data = json.loads(IDENTITIES_PATH.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

    def _save(self) -> None:
        IDENTITIES_PATH.write_text(json.dumps(self._data, indent=2), "utf-8")
        try:      # best effort: private keys readable by owner only
            os.chmod(IDENTITIES_PATH, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    # ---- collection ----------------------------------------------------------
    @property
    def identities(self) -> list:
        return list(self._data.get("identities", []))

    @property
    def has_identity(self) -> bool:
        return bool(self._data.get("identities"))

    def find(self, address: str) -> Optional[dict]:
        for ident in self._data.get("identities", []):
            if ident.get("address") == address:
                return ident
        return None

    def add_identity(self, priv: int, label: str) -> str:
        """Store a new identity. The key MUST hash to a 'Fec…' address — the
        server enforces this too, so accepting anything else would only
        create an identity that can never register."""
        pub = pub_to_compressed(priv_to_pub(priv))
        address = address_from_pub_bytes(pub)
        if not address_meets_pow(address):
            raise ValueError(
                f"this key's address is {address}; the network requires the "
                f"'{FEXT_ADDR_PREFIX}' proof-of-work prefix, so the server "
                "would reject it. Mine a compliant key instead.")
        if self.find(address) is not None:
            raise ValueError("that identity already exists on this device")
        self._data.setdefault("identities", []).append({
            "label": label.strip() or short_addr(address),
            "address": address,
            "private_key_hex": "%064x" % priv,
            "public_key_hex": pub.hex(),
            "username": None,
            "created_at": now_iso(),
        })
        self._data["active"] = address
        self._save()
        return address

    def remove_identity(self, address: str) -> Optional[str]:
        """Delete an identity from this device. Returns the address that is
        active afterwards, or None when none are left.

        The local chat database is removed too — leaving it behind would
        keep plaintext history on disk for an identity the user believes
        they deleted.
        """
        idents = self._data.get("identities", [])
        remaining = [i for i in idents if i["address"] != address]
        if len(remaining) == len(idents):
            return self._data.get("active")
        self._data["identities"] = remaining
        if self._data.get("active") == address:
            self._data["active"] = (remaining[0]["address"] if remaining
                                    else None)
        self._save()
        for path in (self.store_path_for(address),
                     Path(str(self.store_path_for(address)) + "-wal"),
                     Path(str(self.store_path_for(address)) + "-shm")):
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass         # best effort: the identity is gone regardless
        return self._data.get("active")

    def rename_identity(self, address: str, label: str) -> None:
        """Change the on-device label of an identity (not its username)."""
        ident = self.find(address)
        if ident is None:
            raise KeyError(address)
        ident["label"] = label.strip() or short_addr(address)
        self._save()

    def clear_username(self) -> None:
        """Forget the registered name for the ACTIVE identity.

        Used when the relay says it does not know this address (its database
        was reset, or the identity was never registered here). The keypair is
        untouched — only the claim that it has a name on this relay is
        dropped, so the user can register again.
        """
        ident = self._require_active()
        ident.pop("username", None)
        self._save()

    def activate(self, address: str) -> None:
        if self.find(address) is None:
            raise KeyError("unknown identity")
        self._data["active"] = address
        self._save()

    # ---- active identity -----------------------------------------------------
    @property
    def active(self) -> Optional[dict]:
        addr = self._data.get("active")
        ident = self.find(addr) if addr else None
        if ident is None and self.has_identity:
            ident = self._data["identities"][0]
            self._data["active"] = ident["address"]
            self._save()
        return ident

    def _require_active(self) -> dict:
        ident = self.active
        if ident is None:
            raise RuntimeError("no active identity")
        return ident

    @property
    def address(self) -> str:
        return self._require_active()["address"]

    @property
    def username(self) -> Optional[str]:
        return self._require_active().get("username")

    @property
    def label(self) -> str:
        return self._require_active().get("label", "")

    @property
    def public_key_hex(self) -> str:
        return self._require_active()["public_key_hex"]

    @property
    def _priv(self) -> int:
        return int(self._require_active()["private_key_hex"], 16)

    def set_username(self, username: str) -> None:
        self._require_active()["username"] = username
        self._save()

    # ---- crypto operations (private key never leaves this class) -------------
    def sign(self, message: bytes) -> bytes:
        return ecdsa_sign(message, self._priv)

    def ecdh(self, peer_pub_compressed: bytes) -> bytes:
        return ecdh_shared_secret(self._priv,
                                  compressed_to_pub(peer_pub_compressed))

    # ---- exposure for the Settings UI ----------------------------------------
    def private_export(self) -> str:
        """Click-to-show private key text (hex + WIF, FEXT Core compatible).
        Handled ONLY by the Settings screen; never leaves the machine."""
        priv = self._priv
        return json.dumps({
            "private_key_hex": "%064x" % priv,
            "private_key_wif": priv_to_wif(priv, compressed=True),
            "note": "Import either format into FEXT Core to spend coins.",
        }, indent=2)

    def public_bundle(self) -> dict:
        """Everything safe to share (this is what the QR code encodes)."""
        ident = self._require_active()
        return {"v": 2, "app": WIRE_APP_TAG, "username": ident.get("username"),
                "address": ident["address"],
                "public_key": ident["public_key_hex"]}

    def store_path(self) -> Path:
        """Per-identity local database — chats never bleed across identities.
        Base58 addresses are filesystem-safe by construction."""
        return self.store_path_for(self.address)

    @staticmethod
    def store_path_for(address: str) -> Path:
        """Database path for any identity, active or not — removal needs to
        delete the file of an identity that is not the current one."""
        return DATA_DIR / f"store_{address}.db"


# =============================================================================
#  CryptoEngine — the end-to-end encryption core (secp256k1 ECIES)
# =============================================================================
class CryptoEngine:
    """Implements the documented flow:  SIGN → ENCRYPT → SEND.

    Outgoing (build_envelope):
      1. SIGN. A canonical string binding (version | type | sha256(body) |
         sent_at | sender address) is signed with the sender's static
         secp256k1 key (deterministic RFC-6979 ECDSA). The signature plus
         the sender's public bundle rides INSIDE the encrypted payload, so
         the recipient verifies authenticity WITHOUT trusting the server.
      2. ENCRYPT. A fresh ephemeral secp256k1 keypair is generated per
         message. ECDH(ephemeral_priv, recipient_pub) → HKDF-SHA256 →
         256-bit AES key. AES-256-GCM encrypts the signed payload; the AAD
         binds the ciphertext to (ephemeral_pub || recipient_pub) so an
         envelope cannot be transplanted to a different recipient.
      3. SEND. Only {ephemeral_public_key, nonce, ciphertext} leaves the
         client. Per-message forward secrecy comes from the ephemeral key,
         which is discarded immediately after use.

    Incoming (open_envelope):
      1. DECRYPT with our static private key + the ephemeral public key
         (AES-GCM authentication fails loudly on any tampering).
      2. VERIFY the embedded ECDSA signature, AND
      3. Recompute the Base58Check address from the embedded public key and
         require it to equal the sender address the server attached to the
         row — pinning the payload's claimed author to the directory
         identity (and, transitively, to its 'Fec' proof-of-work).
    """

    class EnvelopeError(ValueError):
        """Raised when an incoming envelope fails decryption/verification."""

    def __init__(self, keys: IdentityManager) -> None:
        self._keys = keys

    # ---- internal helpers ----------------------------------------------------
    @staticmethod
    def _derive_key(shared_secret: bytes) -> bytes:
        """HKDF-SHA256 over the ECDH secret.

        `backend` is passed EXPLICITLY and must stay. The Android build
        bundles cryptography 2.8, where HKDF.__init__ takes `backend` as a
        REQUIRED positional argument; it only became optional in 3.1. Omitting
        it raises TypeError inside every seal and every open — the app installs
        and registers fine and then fails on the first message, in both
        directions. Desktop testing hides this completely, because a modern
        cryptography accepts the call either way.
        """
        return HKDF(algorithm=SHA256(), length=32, salt=None,
                    info=HKDF_INFO,
                    backend=default_backend()).derive(shared_secret)

    @staticmethod
    def _canonical(version: int, msg_type: str, body: str,
                   sent_at: str, sender_address: str) -> bytes:
        # Signing a canonical string (not raw JSON) sidesteps every JSON
        # serialization-ambiguity pitfall.
        return f"{version}|{msg_type}|{sha256_hex(body.encode())}|" \
               f"{sent_at}|{sender_address}".encode("utf-8")

    # ---- v9 extension block --------------------------------------------------
    #: Version of the `x` extension block. Readers ignore keys they do not
    #: know, so this only changes for an incompatible reinterpretation.
    X_VERSION = 1

    @classmethod
    def _x_canonical(cls, x: dict, base: bytes) -> bytes:
        """What `xsig` signs: the extension, bound to the v2 canonical string.

        v9 features — reply quotes, forwarded marks, media references and
        delete-for-both — need metadata the v2 signature does not cover: it
        signs only version|type|sha256(body)|sent_at|sender. Changing that
        string would make every message from a v9 client fail verification
        on a v8 client, so instead the metadata travels in a separate `x`
        block with its OWN signature, which a v8 client simply never looks
        at. Hashing `base` in binds the block to this exact body and time,
        so it cannot be lifted onto a different message.

        `x` is serialized with sorted keys and no whitespace, and carries
        only strings, ints, lists and dicts, so both ends produce the same
        bytes from it.
        """
        digest = sha256_hex(json.dumps(
            x, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False).encode("utf-8"))
        return f"fx{cls.X_VERSION}|{digest}|{sha256_hex(base)}".encode("utf-8")

    # ---- outgoing ------------------------------------------------------------
    def build_envelope(self, body: str, recipient_pub_hex: str,
                       x: Optional[dict] = None,
                       recipient: Optional[str] = None,
                       cid: Optional[str] = None) -> tuple:
        """Returns (envelope, sent_at). `envelope` is the ONLY thing that is
        transmitted; it is opaque ciphertext to everyone but the recipient.

        `x`, when given, is the v9 extension block (see _x_canonical). It is
        stamped with the recipient's address and the message's client id
        before signing, which closes a replay the v2 format leaves open: a
        v2 signature names no recipient, so anyone who RECEIVED a signed
        message could re-encrypt it to a third party and it would verify as
        genuinely from its author. An `x` block names exactly one recipient
        and one message, and v9 clients refuse a block addressed elsewhere.
        """
        sent_at = now_iso()
        sender_address = self._keys.address

        # ---- step 1: SIGN ----------------------------------------------------
        base = self._canonical(2, "text", body, sent_at, sender_address)
        signature = self._keys.sign(base)
        payload = {
            "v": 2,
            "type": "text",
            "body": body,
            "sent_at": sent_at,
            "sender": {
                "address": sender_address,
                "username": self._keys.username,
                "public_key": self._keys.public_key_hex,
            },
            "signature": b64e(signature),
        }
        if x is not None:
            if not recipient or not cid:
                raise ValueError("an extension block needs its recipient "
                                 "and client message id")
            x = dict(x, xv=self.X_VERSION, to=recipient, cid=cid)
            payload["x"] = x
            payload["xsig"] = b64e(self._keys.sign(self._x_canonical(x, base)))
        plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")

        # ---- step 2: ENCRYPT -------------------------------------------------
        recipient_pub_raw = bytes.fromhex(recipient_pub_hex)
        recipient_point = compressed_to_pub(recipient_pub_raw)
        eph_priv = int.from_bytes(os.urandom(32), "big") % (_N - 1) + 1
        eph_pub_raw = pub_to_compressed(priv_to_pub(eph_priv))
        shared = ecdh_shared_secret(eph_priv, recipient_point)
        aes_key = self._derive_key(shared)
        nonce = os.urandom(12)
        aad = eph_pub_raw + recipient_pub_raw
        ciphertext = AESGCM(aes_key).encrypt(nonce, plaintext, aad)

        # ---- step 3: (caller) SEND -------------------------------------------
        envelope = {
            "ephemeral_public_key": eph_pub_raw.hex(),
            "nonce": b64e(nonce),
            "ciphertext": b64e(ciphertext),
        }
        return envelope, sent_at

    # ---- incoming ------------------------------------------------------------
    def open_envelope(self, envelope: dict, claimed_sender: str,
                      cid: Optional[str] = None) -> dict:
        """Decrypt + verify. Returns the trusted inner payload dict.

        A payload carrying an `x` block is accepted only if that block's own
        signature verifies, it is addressed to THIS identity, and it is bound
        to the client message id the relay delivered it under. Failing any
        of those is treated exactly like a bad signature, never as "show the
        body and drop the extras": a v9 sender always produces a valid block,
        so an invalid one means tampering or a replay, and quietly showing
        the body would hand the replay its payoff.
        """
        try:
            eph_pub_raw = bytes.fromhex(envelope["ephemeral_public_key"])
            nonce = b64d(envelope["nonce"])
            ciphertext = b64d(envelope["ciphertext"])
        except (KeyError, ValueError, TypeError):
            raise self.EnvelopeError("malformed envelope")

        my_pub_raw = bytes.fromhex(self._keys.public_key_hex)
        aad = eph_pub_raw + my_pub_raw
        try:
            shared = self._keys.ecdh(eph_pub_raw)
            aes_key = self._derive_key(shared)
            plaintext = AESGCM(aes_key).decrypt(nonce, ciphertext, aad)
            payload = json.loads(plaintext.decode("utf-8"))
        except Exception:
            raise self.EnvelopeError("decryption failed (wrong key or tampering)")

        # ---- verify authorship ----------------------------------------------
        try:
            sender = payload["sender"]
            sender_pub_raw = bytes.fromhex(sender["public_key"])
            sender_point = compressed_to_pub(sender_pub_raw)
            recomputed = address_from_pub_bytes(sender_pub_raw)
            if recomputed != claimed_sender:
                raise self.EnvelopeError("sender identity mismatch")
            if sender.get("address") != recomputed:
                raise self.EnvelopeError("payload identity mismatch")
            base = self._canonical(payload["v"], payload["type"],
                                   payload["body"], payload["sent_at"],
                                   recomputed)
            ok = ecdsa_verify(base, b64d(payload["signature"]), sender_point)
            if not ok:
                raise self.EnvelopeError("signature verification failed")
            x = payload.get("x")
            if x is not None:
                if not isinstance(x, dict):
                    raise self.EnvelopeError("malformed extension block")
                if not ecdsa_verify(self._x_canonical(x, base),
                                    b64d(payload.get("xsig", "")),
                                    sender_point):
                    raise self.EnvelopeError("extension signature failed")
                if x.get("to") != self._keys.address:
                    raise self.EnvelopeError(
                        "extension addressed to a different identity")
                if not x.get("cid") or (cid and x["cid"] != cid):
                    raise self.EnvelopeError(
                        "extension bound to a different message")
        except self.EnvelopeError:
            raise
        except (KeyError, ValueError, TypeError):
            raise self.EnvelopeError("signature verification failed")
        return payload


# =============================================================================
#  ApiClient — everything that touches the network over HTTP
# =============================================================================
class ApiClient:
    """Thin, auditable HTTP layer over the relay's JSON API.

    Authentication: privileged endpoints require proof of key ownership.
    The client signs f"{timestamp}.{nonce}.{context}" with its secp256k1
    key (RFC-6979 deterministic ECDSA), where `nonce` is 16 fresh random
    bytes per request and `context` binds the signature to the exact
    request (raw-body hash for POSTs, since_id for syncs). The server
    checks freshness, replay and signature.
    """

    class ApiError(RuntimeError):
        """`detail` carries the server's machine-readable extra fields — e.g.
        the username an address is already registered under — so recovery
        flows can branch on data instead of parsing English."""

        def __init__(self, status: int, code: str, message: str,
                     detail: Optional[dict] = None) -> None:
            super().__init__(f"[{status}/{code}] {message}")
            self.status, self.code = status, code
            self.message = message
            self.detail = detail or {}

    def __init__(self, keys: IdentityManager) -> None:
        self._keys = keys
        self._session = self._build_session()

    @staticmethod
    def _build_session() -> "requests.Session":
        """A session that survives the ordinary flakiness of mobile networks.

        v7.1: a single dropped TCP connection used to surface as a hard
        "Network error" straight to the user. Handing a walking commuter a
        modal error because one packet went missing is not a network
        problem, it is a design problem. urllib3 now retries connection-level
        failures with a short backoff before we ever report anything.

        Only CONNECTION and READ failures are retried, and reads only for
        idempotent verbs — replaying a POST that the relay already processed
        could duplicate a message.

        `total` MUST be finite. With total=None urllib3 consults only the
        per-category counters, and a category it does not decrement — a TLS
        handshake failure, for instance — never exhausts the retry, so the
        request loops forever and the app hangs instead of reporting an
        error. That is not hypothetical: it happened in testing, where a
        wrong-scheme guess ran past nine connection attempts and never
        returned.
        """
        session = requests.Session()
        try:
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
            retry = Retry(
                total=3, connect=2, read=2, status=0, redirect=2,
                backoff_factor=0.4,
                allowed_methods=frozenset(["GET", "DELETE"]),
                raise_on_status=False)
            adapter = HTTPAdapter(max_retries=retry, pool_maxsize=8)
            session.mount("http://", adapter)
            session.mount("https://", adapter)
        except Exception:
            pass          # a plain session still works, just less patiently
        return session

    # ---- plumbing (DRY) ------------------------------------------------------
    def deregister(self) -> dict:
        """Remove this identity from the relay (releases the username)."""
        return self._delete("/api/users/me", "delete.me")

    def auth_fields(self, context: str) -> dict:
        timestamp = int(time.time())
        nonce = os.urandom(16).hex()
        signature = self._keys.sign(
            f"{timestamp}.{nonce}.{context}".encode("utf-8"))
        return {"address": self._keys.address, "timestamp": timestamp,
                "nonce": nonce, "signature": b64e(signature)}

    def _auth_headers(self, context: str) -> dict:
        f = self.auth_fields(context)
        return {"X-Client-Address": f["address"],
                "X-Auth-Timestamp": str(f["timestamp"]),
                "X-Auth-Nonce": f["nonce"],
                "X-Auth-Signature": f["signature"]}

    def _handle(self, resp: requests.Response) -> dict:
        try:
            data = resp.json()
        except ValueError:
            raise self.ApiError(resp.status_code, "bad_response",
                                "server returned non-JSON response")
        if resp.status_code >= 400:
            err = data.get("error", {}) if isinstance(data, dict) else {}
            raise self.ApiError(resp.status_code, err.get("code", "unknown"),
                                err.get("message", "request failed"),
                                {k: v for k, v in err.items()
                                 if k not in ("code", "message")})
        return data

    def _attempt(self, call) -> dict:
        """Run `call(base_url)`, re-resolving the transport once on failure.

        This is what makes a scheme change survivable at runtime: if the
        relay gains TLS, or loses it, or the cached choice is simply stale,
        the first failure triggers a re-probe and the request is retried
        against whatever actually answers. Only then is an error reported.
        """
        base = ENDPOINT.base if ENDPOINT.resolved else ENDPOINT.resolve()
        try:
            return call(base or ENDPOINT.base)
        except requests.RequestException as first:
            ENDPOINT.invalidate()
            retry_base = ENDPOINT.resolve(force=True)
            if not retry_base or retry_base == base:
                raise first
            return call(retry_base)

    def _get(self, path: str, params: dict,
             headers: Optional[dict] = None) -> dict:
        return self._attempt(lambda base: self._handle(
            self._session.get(f"{base}{path}", params=params,
                              headers=headers, timeout=REQUEST_TIMEOUT)))

    def _post_signed(self, path: str, body: dict, context_prefix: str) -> dict:
        # Serialize ONCE, hash those exact bytes, sign, send those exact
        # bytes — so the server-side body hash always matches. The signature
        # is over the body only, never the URL, so re-resolving the
        # transport and retrying is safe.
        raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers = self._auth_headers(f"{context_prefix}.{sha256_hex(raw)}")
        headers["Content-Type"] = "application/json"
        return self._attempt(lambda base: self._handle(
            self._session.post(f"{base}{path}", data=raw, headers=headers,
                               timeout=REQUEST_TIMEOUT)))

    def _delete(self, path: str, context: str) -> dict:
        """Authenticated DELETE. There is no body to bind the signature to,
        so the context is a fixed verb string, matching how the server
        authenticates 'ws.connect' and 'events.sync'."""
        headers = self._auth_headers(context)
        return self._attempt(lambda base: self._handle(
            self._session.delete(f"{base}{path}", headers=headers,
                                 timeout=REQUEST_TIMEOUT)))

    # ---- public API ----------------------------------------------------------
    def health(self) -> bool:
        try:
            self._get("/api/health", {})
            return True
        except (requests.RequestException, self.ApiError):
            return False

    def username_available(self, username: str) -> bool:
        data = self._get("/api/username/available", {"username": username})
        return bool(data.get("available"))

    def register(self, username: str) -> dict:
        """Registration proves key ownership by signing 'register.<name>';
        the server then recomputes the address and enforces the 'Fec' PoW."""
        signature = self._keys.sign(f"register.{username}".encode("utf-8"))
        payload = {"username": username,
                   "public_key": self._keys.public_key_hex,
                   "signature": b64e(signature)}
        # Registration is the FIRST call a new install makes, so it is where
        # a wrong transport guess used to dead-end the whole app. Going
        # through _attempt() means a bad guess re-probes and recovers here
        # rather than showing "Network error" on the very first screen.
        return self._attempt(lambda base: self._handle(
            self._session.post(f"{base}/api/register", json=payload,
                               timeout=REQUEST_TIMEOUT)))

    def lookup(self, *, username: Optional[str] = None,
               address: Optional[str] = None) -> dict:
        params = ({"username": username} if username is not None
                  else {"address": address})
        return self._get("/api/users/lookup", params)

    def send_message(self, recipient: str, envelope: dict,
                     client_msg_id: str) -> dict:
        body = {"recipient": recipient, "envelope": envelope,
                "client_msg_id": client_msg_id}
        return self._post_signed("/api/messages/send", body, "send")

    def sync(self, since_id: int = 0) -> dict:
        headers = self._auth_headers(f"sync.{since_id}")
        return self._get("/api/messages/sync", {"since_id": since_id}, headers)

    def ack(self, ids: list) -> dict:
        return self._post_signed("/api/messages/ack", {"ids": ids}, "ack")

    def read(self, ids: list) -> dict:
        return self._post_signed("/api/messages/read", {"ids": ids}, "read")

    def set_ticks(self, peer: str, enabled: bool) -> dict:
        return self._post_signed("/api/prefs/ticks",
                                 {"peer": peer, "enabled": enabled}, "prefs")

    def events_sync(self) -> dict:
        headers = self._auth_headers("events.sync")
        return self._get("/api/events/sync", {}, headers)

    # ---- v9: capabilities and media blobs ------------------------------------
    def features(self) -> dict:
        """The relay's advertised features, from /api/health.

        A v8 relay advertises none, which is how the app knows to say "this
        relay cannot carry photos or voice notes yet" instead of failing an
        upload. Cached for the session; `refresh` is for the settings screen.
        """
        if getattr(self, "_features", None) is None:
            try:
                data = self._get("/api/health", {})
            except (requests.RequestException, self.ApiError):
                return {}                    # unknown; ask again next time
            feats = data.get("features") if isinstance(data, dict) else None
            self._features = {
                "blobs": isinstance(feats, list) and "blobs" in feats,
                "blob_max": int(data.get("blob_max") or MAX_MEDIA_BYTES)
                if isinstance(data, dict) else MAX_MEDIA_BYTES,
                "blob_ttl": int(data.get("blob_ttl") or 0)
                if isinstance(data, dict) else 0,
                "version": str(data.get("version", "")) if isinstance(
                    data, dict) else ""}
        return self._features

    def upload_blob(self, blob: bytes) -> dict:
        """Store one sealed media file on the relay; returns {'blob': id,
        'expires_at': ...}. The signature binds the exact bytes."""
        if len(blob) > MAX_MEDIA_BYTES:
            raise self.ApiError(413, "too_large", "media is too large")
        headers = self._auth_headers(f"blob.{sha256_hex(blob)}")
        headers["Content-Type"] = "application/octet-stream"
        return self._attempt(lambda base: self._handle(
            self._session.post(f"{base}/api/blobs", data=blob,
                               headers=headers,
                               timeout=max(REQUEST_TIMEOUT, 60))))

    def download_blob(self, blob_id: str) -> bytes:
        """Fetch a sealed blob. 410 means the relay let it expire — the cue
        to ask the sender for it again."""
        if not _TOKEN_RE.match(blob_id or ""):
            raise self.ApiError(400, "bad_blob", "malformed media id")
        headers = self._auth_headers(f"blob.get.{blob_id}")

        def call(base):
            resp = self._session.get(f"{base}/api/blobs/{blob_id}",
                                     headers=headers,
                                     timeout=max(REQUEST_TIMEOUT, 60))
            if resp.status_code >= 400:
                self._handle(resp)                   # raises ApiError
            if len(resp.content) > MAX_MEDIA_BYTES + 64:
                raise self.ApiError(413, "too_large", "media is too large")
            return resp.content
        return self._attempt(call)


# =============================================================================
#  LocalStore — per-identity client-side history (plaintext stays local)
# =============================================================================
class LocalStore:
    """Local SQLite store for decrypted history, contacts and per-contact
    tick settings. One database file PER IDENTITY under ~/.fext/, so chats
    never bleed between identities. The relay keeps only undelivered
    ciphertext; the *only* place plaintext exists is this database on the
    user's own machine. Parameterized queries only; thread-safe via a lock.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT
    );
    CREATE TABLE IF NOT EXISTS contacts (
        address       TEXT PRIMARY KEY,
        username      TEXT,
        public_key    TEXT NOT NULL,
        ticks_enabled INTEGER NOT NULL DEFAULT 1,  -- "Enable ticks status"
        auto_read     INTEGER NOT NULL DEFAULT 1,  -- "Auto mark all as read"
        added_at      TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS messages (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id     INTEGER UNIQUE,   -- relay row id (receipt correlation)
        client_msg_id TEXT,
        peer          TEXT NOT NULL,
        direction     TEXT NOT NULL,    -- 'in' | 'out'
        body          TEXT NOT NULL,
        status        INTEGER NOT NULL DEFAULT 0,  -- out: 0..3 tick tier
        read          INTEGER NOT NULL DEFAULT 1,  -- in: 0 until marked read
        created_at    TEXT NOT NULL,
        -- in: 0 until the relay has CONFIRMED our read receipt. A receipt
        -- written to a socket is not a receipt the relay received, so the
        -- send is retried until something acknowledges it. Without this the
        -- sender's 3rd tick simply never arrived and nobody could say why.
        receipt_sent  INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_local_msgs_peer ON messages (peer, id);
    CREATE INDEX IF NOT EXISTS idx_local_msgs_cmid ON messages (client_msg_id);
    -- v9: client ids of messages deleted on this device, so a message the
    -- relay re-pushes (an ack lost to a dropped socket) or one that arrives
    -- AFTER its author's delete-for-both cannot come back from the dead.
    CREATE TABLE IF NOT EXISTS tombstones (
        cid  TEXT NOT NULL,
        peer TEXT NOT NULL,
        at   TEXT NOT NULL,
        PRIMARY KEY (cid, peer)
    );
    -- v9.1: group chats. The relay only ever carries 1:1 envelopes; a group
    -- is a membership list every member keeps, and a group message is sent
    -- to each member separately (pairwise fan-out). Conversation key for a
    -- group's messages: messages.peer = 'g:' || gid.
    CREATE TABLE IF NOT EXISTS groups (
        gid        TEXT PRIMARY KEY,
        name       TEXT NOT NULL,
        admin      TEXT NOT NULL,
        members    TEXT NOT NULL,              -- JSON [{a, u, k}]
        version    INTEGER NOT NULL DEFAULT 1,
        state      TEXT NOT NULL DEFAULT 'active',  -- active|pending|left|removed
        notify     INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    -- One row per recipient of each outgoing group message: its relay id
    -- (for receipts) and tick tier. The message's own status is the lowest.
    CREATE TABLE IF NOT EXISTS group_sends (
        local_id  INTEGER NOT NULL,
        member    TEXT NOT NULL,
        server_id INTEGER,
        status    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (local_id, member)
    );
    CREATE INDEX IF NOT EXISTS idx_group_sends_sid ON group_sends (server_id);
    -- v9.1: reactions, one per person per message (choosing the same one
    -- again takes it back). Keyed by conversation + the message's client id.
    CREATE TABLE IF NOT EXISTS reactions (
        peer    TEXT NOT NULL,
        cid     TEXT NOT NULL,
        reactor TEXT NOT NULL,
        emoji   TEXT NOT NULL,
        at      TEXT NOT NULL,
        PRIMARY KEY (peer, cid, reactor)
    );
    """

    #: Current schema generation, recorded in PRAGMA user_version.
    #:   0/1  up to v8.0.2 (receipt_sent added by inspection in v7.2)
    #:   2    v9.0.0: message kinds, extension metadata, stars, media state,
    #:        per-contact notifications and capabilities, tombstones
    #:   3    v9.1.0: group chats (groups, group_sends, messages.author),
    #:        reactions, contacts.listed
    SCHEMA_VERSION = 3

    #: Additive migrations: (table, column, statements). Applied when the
    #: column is missing, decided by inspection rather than by catching the
    #: error — an OperationalError is too blunt an instrument to tell
    #: "already there" from "broken" — so they are idempotent and safe to run
    #: against a database that was migrated halfway.
    #:
    #: COMPATIBILITY POLICY, which every future migration must keep:
    #:   * only ever ADD tables and columns, each with a DEFAULT; never drop,
    #:     rename or retype one, and never change what an existing column
    #:     means;
    #:   * `body` always holds a human-readable rendering of the message,
    #:     whatever its kind, because older releases show nothing else.
    #: Together these let an OLDER release open a database a newer one has
    #: migrated — it ignores the columns it does not know and its INSERTs
    #: name their columns, so the new ones take their defaults — and a
    #: newer release open anything older. Both directions are exercised by
    #: desktop/test_core.py against the previous release's real code.
    _MIGRATIONS = (
        ("messages", "receipt_sent", (
            "ALTER TABLE messages "
            "ADD COLUMN receipt_sent INTEGER NOT NULL DEFAULT 0",
            # Backfill: history predates the column, so treat it as settled.
            # Without this, upgrading replays a read receipt for every
            # message ever received — and the relay's routing ledger is
            # purged after 7 days, so almost none of them can be confirmed
            # and they would be retried on every sync cycle forever.
            "UPDATE messages SET receipt_sent = 1",
        )),
        # ---- schema 2 (v9.0.0) ----------------------------------------------
        ("messages", "kind", (
            "ALTER TABLE messages ADD COLUMN kind TEXT NOT NULL "
            "DEFAULT 'text'",)),
        ("messages", "ext", (
            "ALTER TABLE messages ADD COLUMN ext TEXT",)),
        ("messages", "starred", (
            "ALTER TABLE messages ADD COLUMN starred INTEGER NOT NULL "
            "DEFAULT 0",)),
        ("messages", "media_path", (
            "ALTER TABLE messages ADD COLUMN media_path TEXT",)),
        ("messages", "media_state", (
            "ALTER TABLE messages ADD COLUMN media_state TEXT NOT NULL "
            "DEFAULT ''",)),
        ("contacts", "notify", (
            "ALTER TABLE contacts ADD COLUMN notify INTEGER NOT NULL "
            "DEFAULT 1",)),
        ("contacts", "caps", (
            "ALTER TABLE contacts ADD COLUMN caps INTEGER NOT NULL "
            "DEFAULT 0",)),
        # ---- schema 3 (v9.1.0) ----------------------------------------------
        # Who wrote an incoming GROUP message (a DM's author is its peer).
        ("messages", "author", (
            "ALTER TABLE messages ADD COLUMN author TEXT",)),
        # 0 for people met only inside a group: known (their key verifies
        # their messages) but not added, so they stay out of the chat list.
        ("contacts", "listed", (
            "ALTER TABLE contacts ADD COLUMN listed INTEGER NOT NULL "
            "DEFAULT 1",)),
    )

    #: contacts.caps bit: this peer has sent us a valid v9 extension block,
    #: so it understands delete-for-both, replies and media references.
    CAP_V9 = 1

    def __init__(self, path: Path) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(self._SCHEMA)
            self._migrate()
            self._db.commit()

    def _migrate(self) -> None:
        """Bring an older database up to the current schema. Called with the
        lock held.

        A database stamped with a NEWER generation than this release knows
        is opened anyway, never "repaired": under the additive policy above
        everything this release reads is still there, and touching it would
        risk damaging a schema this code does not understand.
        """
        found = self._db.execute("PRAGMA user_version").fetchone()[0]
        if found > self.SCHEMA_VERSION:
            log.warning("local store: database is schema %s, this release "
                        "knows %s — opening it as-is (newer releases only "
                        "ever add columns)", found, self.SCHEMA_VERSION)
            return
        columns = {}
        for table, column, statements in self._MIGRATIONS:
            have = columns.get(table)
            if have is None:
                have = columns[table] = {
                    r["name"] for r in self._db.execute(
                        f"PRAGMA table_info({table})").fetchall()}
            if column in have:
                continue
            log.info("local store: adding %s.%s", table, column)
            for statement in statements:
                self._db.execute(statement)
            have.add(column)
        if found < self.SCHEMA_VERSION:
            self._db.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- meta ----------------------------------------------------------------
    def get_meta(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value))
            self._db.commit()

    # ---- contacts ------------------------------------------------------------
    def upsert_contact(self, address: str, username: Optional[str],
                       public_key: str, listed: bool = True) -> None:
        """Add or refresh a contact. `listed=False` is for someone known only
        from a group: their key is kept (it verifies their messages, and lets
        you message them later) but they are not put in the chat list. A
        later listed upsert, or a direct message, lists them; nothing ever
        unlists a contact."""
        with self._lock:
            self._db.execute(
                """INSERT INTO contacts (address, username, public_key, added_at,
                                         listed)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(address) DO UPDATE SET
                       username = COALESCE(excluded.username, contacts.username),
                       public_key = excluded.public_key,
                       listed = MAX(contacts.listed, excluded.listed)""",
                (address, username, public_key, now_iso(), 1 if listed else 0))
            self._db.commit()

    def get_contact(self, address: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM contacts WHERE address = ?", (address,)).fetchone()
        return dict(row) if row else None

    def set_contact_setting(self, address: str, field: str, value: int) -> None:
        assert field in ("ticks_enabled", "auto_read",   # whitelisted columns
                         "notify")
        with self._lock:
            self._db.execute(
                f"UPDATE contacts SET {field} = ? WHERE address = ?",
                (int(value), address))
            self._db.commit()

    def add_caps(self, address: str, bits: int) -> None:
        """Record what a peer has PROVEN it understands (OR-ed in, never
        cleared: a peer that rolls back simply stops getting the benefit of
        features it can no longer act on, which degrades to v2 text)."""
        with self._lock:
            self._db.execute(
                "UPDATE contacts SET caps = caps | ? WHERE address = ?",
                (int(bits), address))
            self._db.commit()

    def contacts(self, listed_only: bool = True) -> list:
        """Contacts of this identity, for pickers (forward-to, new group)."""
        sql = "SELECT * FROM contacts"
        if listed_only:
            sql += " WHERE listed = 1"
        with self._lock:
            rows = self._db.execute(
                sql + " ORDER BY COALESCE(username, address) COLLATE NOCASE"
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- messages ------------------------------------------------------------
    def _claim_server_id(self, server_id: int, keep_local_id: int = -1) -> bool:
        """Make `server_id` available to one row, freeing any stale claim.

        `server_id` is UNIQUE locally, but relay ids are only unique WITHIN
        ONE RELAY GENERATION: reset the relay's database, or point the app at
        a different relay, and ids restart at 1 while local rows still hold
        1, 2, 3… Every consequence of that collision was bad. `add_in` threw
        the incoming message away, `mark_sent` raised IntegrityError inside a
        worker thread with no handler for it, and the delivery receipt for
        the NEW message landed on whichever OLD row happened to own that id —
        which is how a message that was never delivered ended up wearing two
        golden ticks.

        The resolution is unambiguous once stated: the relay has just told us
        this id now refers to a different message, so any local row still
        claiming it is stale BY DEFINITION. Clear the old claim. That row
        keeps the tick tier it had already earned — the relay has long since
        forgotten it, so there were no further receipts coming for it anyway.

        Called with the lock held. Returns True if anything was displaced.
        """
        row = self._db.execute(
            "SELECT id, direction, status FROM messages "
            "WHERE server_id = ? AND id != ?",
            (server_id, keep_local_id)).fetchone()
        if row is None:
            return False
        log.warning(
            "local store: relay id %s was still claimed by local row %s "
            "(%s, %s tick[s]) — releasing it; that row can no longer be "
            "correlated with relay receipts. This normally means the relay's "
            "database was reset or you are talking to a different relay.",
            server_id, row["id"], row["direction"], row["status"])
        self._db.execute("UPDATE messages SET server_id = NULL WHERE id = ?",
                         (row["id"],))
        return True

    def add_out(self, peer: str, body: str, created_at: str,
                client_msg_id: str, kind: str = "text",
                ext: Optional[dict] = None, media_path: Optional[str] = None,
                media_state: str = "", recipients: Optional[list] = None
                ) -> int:
        """Insert an outgoing message at 0/3 (grey tick). Returns local id.

        `body` is always the human-readable rendering ("Photo", the caption,
        "Voice message (0:07)"…); `kind`/`ext` carry what a v9 screen needs
        to draw it richly. See the compatibility policy on _MIGRATIONS.
        """
        with self._lock:
            cur = self._db.execute(
                """INSERT INTO messages (client_msg_id, peer, direction, body,
                                         status, read, created_at, kind, ext,
                                         media_path, media_state)
                   VALUES (?, ?, 'out', ?, 0, 1, ?, ?, ?, ?, ?)""",
                (client_msg_id, peer, body, created_at, kind,
                 json.dumps(ext, separators=(",", ":")) if ext else None,
                 media_path, media_state))
            if recipients:
                # A group message: one delivery row per recipient, all at 0.
                self._db.executemany(
                    "INSERT OR IGNORE INTO group_sends (local_id, member) "
                    "VALUES (?, ?)", [(cur.lastrowid, m) for m in recipients])
            self._db.commit()
            return cur.lastrowid

    def mark_sent(self, local_id: int, server_id: int) -> bool:
        """Server stored the ciphertext -> 1/3 (first golden tick).

        Returns False instead of raising: this runs on a send worker whose
        death would leave the message stuck at 0/3 with nothing on screen to
        say so.
        """
        try:
            with self._lock:
                self._claim_server_id(server_id, keep_local_id=local_id)
                self._db.execute(
                    "UPDATE messages SET server_id = ?, status = MAX(status, 1) "
                    "WHERE id = ?", (server_id, local_id))
                self._db.commit()
            return True
        except sqlite3.Error:
            log.exception("local store: could not record relay id %s for "
                          "local message %s", server_id, local_id)
            return False

    def apply_receipt(self, server_id: int, status: str) -> Optional[str]:
        """delivered -> 2/3, read -> 3/3. Status only ever ratchets upward.
        Returns the peer address when a row was updated (for UI refresh)."""
        tier = {"delivered": 2, "read": 3}.get(status)
        if tier is None:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT id, peer, status FROM messages WHERE server_id = ? "
                "AND direction = 'out'", (server_id,)).fetchone()
            if row is None:
                peer = self._group_receipt(server_id, tier)
                if peer is not None:
                    return peer
                # Not an error: receipts for a conversation this identity no
                # longer holds, or for a stale id we have already released.
                log.debug("receipt '%s' for relay id %s matched no local "
                          "outgoing message", status, server_id)
                return None
            if row["status"] >= tier:
                return None                  # already at or above this tier
            self._db.execute(
                "UPDATE messages SET status = MAX(status, ?) WHERE id = ?",
                (tier, row["id"]))
            self._db.commit()
            return row["peer"]

    def _group_receipt(self, server_id: int, tier: int) -> Optional[str]:
        """A receipt for one recipient's copy of a group message. Called
        with the lock held; returns the group's peer key if anything moved."""
        hit = self._db.execute(
            "SELECT local_id, member, status FROM group_sends "
            "WHERE server_id = ?", (server_id,)).fetchone()
        if hit is None or hit["status"] >= tier:
            return None
        self._db.execute(
            "UPDATE group_sends SET status = ? WHERE local_id = ? "
            "AND member = ?", (tier, hit["local_id"], hit["member"]))
        peer = self._settle_group_status(hit["local_id"])
        self._db.commit()
        return peer

    def _settle_group_status(self, local_id: int) -> Optional[str]:
        """A group message's tick is its slowest recipient's. Lock held."""
        low = self._db.execute(
            "SELECT MIN(status) AS s FROM group_sends WHERE local_id = ?",
            (local_id,)).fetchone()["s"]
        row = self._db.execute("SELECT peer, status FROM messages WHERE id = ?",
                               (local_id,)).fetchone()
        if row is None:
            return None
        if low is not None and low > row["status"]:
            self._db.execute("UPDATE messages SET status = ? WHERE id = ?",
                             (low, local_id))
        return row["peer"]

    def group_send_sent(self, local_id: int, member: str,
                        server_id: int) -> bool:
        """The relay stored `member`'s copy (1/3 for that recipient)."""
        try:
            with self._lock:
                self._db.execute(
                    "UPDATE group_sends SET server_id = ?, status = "
                    "MAX(status, 1) WHERE local_id = ? AND member = ?",
                    (server_id, local_id, member))
                self._settle_group_status(local_id)
                self._db.commit()
            return True
        except sqlite3.Error:
            log.exception("local store: could not record group send %s/%s",
                          local_id, member)
            return False

    def group_sends(self, local_id: int) -> list:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM group_sends WHERE local_id = ? ORDER BY member",
                (local_id,)).fetchall()
        return [dict(r) for r in rows]

    # Outcomes of add_in(). The caller must be able to tell these apart,
    # because only two of the three make it safe to acknowledge delivery.
    STORED = "stored"          # plaintext is now durably ours
    DUPLICATE = "duplicate"    # we already hold this exact message
    FAILED = "failed"          # NOT stored — do not ack, do not lose it

    def add_in(self, peer: str, body: str, created_at: str,
               server_id: Optional[int], client_msg_id: Optional[str],
               unread: bool = True, kind: str = "text",
               ext: Optional[dict] = None, media_state: str = "",
               author: Optional[str] = None) -> str:
        """Insert an incoming message (unread).

        Returns STORED, DUPLICATE or FAILED. The old boolean collapsed
        DUPLICATE and FAILED into one value, and the sync worker acked on
        both — which meant a message that could not be stored was reported to
        the sender as delivered and then deleted from the relay. Never again:
        the three cases are now distinct and only the first two are ackable.

        Duplicate detection prefers `client_msg_id`, a uuid4 minted by the
        SENDER, because it is globally unique and survives a relay database
        reset. `server_id` is only a fallback for messages sent by clients
        old enough not to set one.
        """
        try:
            with self._lock:
                if client_msg_id and self._db.execute(
                        "SELECT 1 FROM tombstones WHERE cid = ? AND peer = ?",
                        (client_msg_id, peer)).fetchone():
                    # Deleted here already — by its author, possibly before
                    # it even arrived. Report it as held so it is acked and
                    # the relay stops offering it.
                    return self.DUPLICATE
                if client_msg_id:
                    dup = self._db.execute(
                        """SELECT 1 FROM messages WHERE client_msg_id = ?
                           AND peer = ? AND direction = 'in'
                           AND COALESCE(author, '') = ?""",
                        (client_msg_id, peer, author or "")).fetchone()
                elif server_id:
                    dup = self._db.execute(
                        """SELECT 1 FROM messages WHERE server_id = ?
                           AND direction = 'in'""", (server_id,)).fetchone()
                else:
                    dup = None
                if dup is not None:
                    return self.DUPLICATE
                if server_id:
                    self._claim_server_id(server_id)
                self._db.execute(
                    """INSERT INTO messages (server_id, client_msg_id, peer,
                                             direction, body, status, read,
                                             created_at, receipt_sent, kind,
                                             ext, media_state, author)
                       VALUES (?, ?, ?, 'in', ?, 0, ?, ?, ?, ?, ?, ?, ?)""",
                    (server_id, client_msg_id, peer, body,
                     0 if unread else 1, created_at, 0 if unread else 1,
                     kind,
                     json.dumps(ext, separators=(",", ":")) if ext else None,
                     media_state, author))
                self._db.commit()
                return self.STORED
        except sqlite3.Error:
            log.exception("local store: could not store incoming message "
                          "(relay id %s) from %s", server_id, peer)
            return self.FAILED

    def unread_for(self, peer: str) -> list:
        """[(local_id, server_id), …] for unread incoming messages."""
        with self._lock:
            rows = self._db.execute(
                """SELECT id, server_id FROM messages
                   WHERE peer = ? AND direction = 'in' AND read = 0
                   ORDER BY id ASC""", (peer,)).fetchall()
        return [(r["id"], r["server_id"]) for r in rows]

    def mark_read_local(self, local_ids: list) -> None:
        if not local_ids:
            return
        with self._lock:
            self._db.executemany(
                "UPDATE messages SET read = 1 WHERE id = ?",
                [(i,) for i in local_ids])
            self._db.commit()

    def pending_read_receipts(self, limit: int = 200) -> list:
        """[(relay id, peer), …] for messages we have READ but whose receipt
        the relay has not confirmed. Retried every sync cycle until it does,
        or until the message is older than the relay's ledger retention —
        past that the relay has forgotten the routing row, no confirmation
        can ever arrive, and retrying would be a permanent slow leak.

        This exists because `ws.send()` succeeding proves only that bytes
        entered a socket buffer, which a dead peer will happily accept. The
        old code took that as delivery and returned, so a receipt sent into a
        half-open connection vanished and the sender's third tick never came.
        """
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(seconds=RECEIPT_RETRY_WINDOW_SECONDS)).isoformat()
        with self._lock:
            rows = self._db.execute(
                """SELECT server_id, peer FROM messages
                   WHERE direction = 'in' AND read = 1 AND receipt_sent = 0
                     AND server_id IS NOT NULL AND created_at >= ?
                   ORDER BY id ASC LIMIT ?""", (cutoff, limit)).fetchall()
        return [(r["server_id"], r["peer"]) for r in rows]

    def peers_for_server_ids(self, server_ids: list) -> list:
        """[(relay id, peer), …] for incoming rows, so a receipt can be
        logged with both ends named rather than as a bare number."""
        if not server_ids:
            return []
        marks = ",".join("?" * len(server_ids))
        with self._lock:
            rows = self._db.execute(
                f"""SELECT server_id, peer FROM messages
                    WHERE direction = 'in' AND server_id IN ({marks})""",
                list(server_ids)).fetchall()
        return [(r["server_id"], r["peer"]) for r in rows]

    def confirm_read_receipts(self, server_ids: list) -> None:
        """Called only once the relay has ACKNOWLEDGED the receipt."""
        if not server_ids:
            return
        with self._lock:
            self._db.executemany(
                "UPDATE messages SET receipt_sent = 1 WHERE server_id = ?",
                [(i,) for i in server_ids])
            self._db.commit()

    def messages_for(self, peer: str, limit: Optional[int] = None,
                     before_id: Optional[int] = None,
                     from_id: Optional[int] = None) -> list:
        """Messages oldest-first.

        Three modes, all used by the chat screen:
          * no arguments      -> the whole conversation (export, migration)
          * limit             -> the most recent `limit` rows (cold open)
          * from_id           -> everything from `from_id` onward, so the
                                 visible window is ANCHORED: refreshing after
                                 a new message extends the list instead of
                                 sliding it, which is what lets the renderer
                                 append rather than rebuild
          * before_id + limit -> the page immediately older ("load earlier")
        """
        with self._lock:
            if from_id is not None:
                rows = self._db.execute(
                    """SELECT * FROM messages WHERE peer = ? AND id >= ?
                       ORDER BY id ASC""", (peer, from_id)).fetchall()
                return [dict(r) for r in rows]
            if limit is None:
                rows = self._db.execute(
                    "SELECT * FROM messages WHERE peer = ? ORDER BY id ASC",
                    (peer,)).fetchall()
                return [dict(r) for r in rows]
            if before_id is None:
                rows = self._db.execute(
                    """SELECT * FROM messages WHERE peer = ?
                       ORDER BY id DESC LIMIT ?""", (peer, limit)).fetchall()
            else:
                rows = self._db.execute(
                    """SELECT * FROM messages WHERE peer = ? AND id < ?
                       ORDER BY id DESC LIMIT ?""",
                    (peer, before_id, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def has_messages_before(self, peer: str, message_id: int) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM messages WHERE peer = ? AND id < ? LIMIT 1",
                (peer, message_id)).fetchone()
        return row is not None

    def message_count(self, peer: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE peer = ?",
                (peer,)).fetchone()
        return int(row["n"] if row else 0)

    def conversations(self, me: Optional[str] = None) -> list:
        """Every conversation by recent activity, with preview + unread count.

        Each row carries `kind`: 'dm', 'group' (peer 'g:<gid>') or 'self'
        (peer == `me`, the notes-to-self chat). The newest message's
        direction, kind, status and author come along, so the list can show
        "You: …" and its tick without a query per row.
        """
        with self._lock:
            rows = self._db.execute(
                """SELECT m.peer, c.username, c.ticks_enabled, c.notify,
                          g.name AS group_name, g.notify AS group_notify,
                          g.state AS group_state,
                          l.body AS preview, l.created_at AS last_at,
                          l.direction AS last_direction, l.kind AS last_kind,
                          l.status AS last_status, l.author AS last_author,
                          SUM(CASE WHEN m.direction = 'in' AND m.read = 0
                              THEN 1 ELSE 0 END) AS unread,
                          MAX(m.id) AS last_id
                   FROM messages m
                   LEFT JOIN contacts c ON c.address = m.peer
                   LEFT JOIN groups g ON 'g:' || g.gid = m.peer
                   JOIN messages l ON l.id = (SELECT MAX(id) FROM messages
                                              WHERE peer = m.peer)
                   GROUP BY m.peer ORDER BY last_id DESC""").fetchall()
            contact_only = self._db.execute(
                """SELECT address AS peer, username, ticks_enabled, notify,
                          NULL AS preview, added_at AS last_at,
                          0 AS unread, 0 AS last_id
                   FROM contacts
                   WHERE listed = 1
                     AND address NOT IN (SELECT DISTINCT peer FROM messages)
                   ORDER BY added_at DESC""").fetchall()
            group_only = self._db.execute(
                """SELECT 'g:' || gid AS peer, name AS group_name,
                          notify AS group_notify, state AS group_state,
                          NULL AS preview, updated_at AS last_at,
                          0 AS unread, 0 AS last_id
                   FROM groups WHERE 'g:' || gid NOT IN
                       (SELECT DISTINCT peer FROM messages)
                   ORDER BY updated_at DESC""").fetchall()
        out = []
        for row in [dict(r) for r in rows] + [dict(r) for r in group_only] \
                + [dict(r) for r in contact_only]:
            peer = row["peer"]
            if peer.startswith(GROUP_PREFIX):
                row["kind"] = "group"
                row["notify"] = row.get("group_notify", 1)
            elif me is not None and peer == me:
                row["kind"] = "self"
            else:
                row["kind"] = "dm"
            out.append(row)
        return out

    # ---- v9: stars, deletion, lookups, media ---------------------------------
    def message(self, local_id: int) -> Optional[dict]:
        with self._lock:
            row = self._db.execute("SELECT * FROM messages WHERE id = ?",
                                   (local_id,)).fetchone()
        return dict(row) if row else None

    def messages_by_ids(self, local_ids: list) -> list:
        if not local_ids:
            return []
        marks = ",".join("?" * len(local_ids))
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM messages WHERE id IN ({marks}) ORDER BY id",
                [int(i) for i in local_ids]).fetchall()
        return [dict(r) for r in rows]

    def message_by_cid(self, peer: str, cid: str,
                       direction: Optional[str] = None) -> Optional[dict]:
        sql = "SELECT * FROM messages WHERE peer = ? AND client_msg_id = ?"
        args = [peer, cid]
        if direction:
            sql += " AND direction = ?"
            args.append(direction)
        with self._lock:
            row = self._db.execute(sql + " ORDER BY id DESC LIMIT 1",
                                   args).fetchone()
        return dict(row) if row else None

    def set_starred(self, local_ids: list, starred: bool) -> None:
        """Stars are private bookkeeping: they never leave this device."""
        if not local_ids:
            return
        with self._lock:
            self._db.executemany(
                "UPDATE messages SET starred = ? WHERE id = ?",
                [(1 if starred else 0, int(i)) for i in local_ids])
            self._db.commit()

    def starred(self, peer: Optional[str] = None) -> list:
        """Starred messages, newest first; one chat or all of them."""
        sql = "SELECT * FROM messages WHERE starred = 1"
        args = []
        if peer is not None:
            sql += " AND peer = ?"
            args.append(peer)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY id DESC", args).fetchall()
        return [dict(r) for r in rows]

    def _tombstone(self, peer: str, cids) -> None:
        """Called with the lock held."""
        stamp = now_iso()
        self._db.executemany(
            "INSERT OR IGNORE INTO tombstones (cid, peer, at) VALUES (?, ?, ?)",
            [(c, peer, stamp) for c in cids if c])

    def delete_messages(self, local_ids: list) -> list:
        """Delete for me. Returns the removed rows (for their media files).

        Each deleted message's client id is tombstoned so a re-push from the
        relay cannot resurrect it; its reactions and group delivery rows go
        with it.
        """
        rows = self.messages_by_ids(local_ids)
        if not rows:
            return []
        with self._lock:
            for row in rows:
                self._tombstone(row["peer"], [row["client_msg_id"]])
                if row["client_msg_id"]:
                    self._db.execute(
                        "DELETE FROM reactions WHERE peer = ? AND cid = ?",
                        (row["peer"], row["client_msg_id"]))
            self._db.executemany("DELETE FROM messages WHERE id = ?",
                                 [(r["id"],) for r in rows])
            self._db.executemany("DELETE FROM group_sends WHERE local_id = ?",
                                 [(r["id"],) for r in rows])
            self._db.commit()
        return rows

    def delete_by_author(self, author: str, cids: list,
                         peer: Optional[str] = None) -> list:
        """Apply a delete-for-both that `author` sent us.

        Only INCOMING messages from `author` are eligible. The request is
        authenticated as coming from `author` (it arrives in their signed,
        recipient-bound envelope), and restricting it to their own messages
        is what stops anyone deleting text they did not write — the same
        rule the sending screen enforces, applied again here because a
        modified client could ignore that screen. Tombstones are written
        even for ids we do not hold yet, so a message that arrives after its
        own deletion is never shown. Returns the removed rows.
        """
        cids = [c for c in cids if isinstance(c, str) and c][:500]
        if not cids:
            return []
        marks = ",".join("?" * len(cids))
        # In a group (`peer` 'g:…') the author is recorded per message; in a
        # DM the author IS the peer.
        peer = peer or author
        with self._lock:
            rows = [dict(r) for r in self._db.execute(
                f"""SELECT * FROM messages WHERE peer = ? AND direction = 'in'
                    AND COALESCE(author, peer) = ?
                    AND client_msg_id IN ({marks})""",
                [peer, author] + cids).fetchall()]
            self._tombstone(peer, cids)
            if rows:
                self._db.executemany("DELETE FROM messages WHERE id = ?",
                                     [(r["id"],) for r in rows])
                self._db.executemany(
                    "DELETE FROM reactions WHERE peer = ? AND cid = ?",
                    [(peer, r["client_msg_id"]) for r in rows])
            self._db.commit()
        return rows

    def consume_control(self, peer: str, cid: Optional[str]) -> bool:
        """True the FIRST time a control message id is seen, False after.

        Control messages are acted on, not stored, so the message table's
        duplicate check cannot catch a relay re-push of one; without this a
        re-pushed 'resend' would upload the same file again on every
        reconnect. The tombstone table already means "this id is consumed".
        """
        if not cid:
            return True
        with self._lock:
            if self._db.execute(
                    "SELECT 1 FROM tombstones WHERE cid = ? AND peer = ?",
                    (cid, peer)).fetchone():
                return False
            self._tombstone(peer, [cid])
            self._db.commit()
        return True

    def set_media(self, local_id: int, *, path=None, state=None,
                  ext: Optional[dict] = None) -> None:
        sets, args = [], []
        if path is not None:
            sets.append("media_path = ?")
            args.append(path)
        if state is not None:
            sets.append("media_state = ?")
            args.append(state)
        if ext is not None:
            sets.append("ext = ?")
            args.append(json.dumps(ext, separators=(",", ":")))
        if not sets:
            return
        with self._lock:
            self._db.execute(
                f"UPDATE messages SET {', '.join(sets)} WHERE id = ?",
                args + [int(local_id)])
            self._db.commit()

    # ---- v9.1: group chats ---------------------------------------------------
    @staticmethod
    def _group_row(row) -> Optional[dict]:
        if row is None:
            return None
        group = dict(row)
        try:
            group["members"] = json.loads(group["members"] or "[]")
        except (TypeError, ValueError):
            group["members"] = []
        return group

    def group(self, gid: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute("SELECT * FROM groups WHERE gid = ?",
                                   (gid,)).fetchone()
        return self._group_row(row)

    def groups(self) -> list:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM groups ORDER BY name COLLATE NOCASE").fetchall()
        return [self._group_row(r) for r in rows]

    def save_group(self, state: dict, status: str = "active") -> None:
        """Store a group's full state (already sanitised): insert or replace.
        `notify` is the user's own setting and survives updates."""
        stamp = now_iso()
        with self._lock:
            self._db.execute(
                """INSERT INTO groups (gid, name, admin, members, version,
                                       state, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(gid) DO UPDATE SET
                       name = excluded.name, admin = excluded.admin,
                       members = excluded.members, version = excluded.version,
                       state = excluded.state, updated_at = excluded.updated_at""",
                (state["id"], state["name"], state["admin"],
                 json.dumps(state["members"], separators=(",", ":")),
                 int(state["v"]), status, stamp, stamp))
            self._db.commit()

    def set_group_state(self, gid: str, status: str) -> None:
        assert status in ("active", "pending", "left", "removed")
        with self._lock:
            self._db.execute("UPDATE groups SET state = ?, updated_at = ? "
                             "WHERE gid = ?", (status, now_iso(), gid))
            self._db.commit()

    def set_group_notify(self, gid: str, on: bool) -> None:
        with self._lock:
            self._db.execute("UPDATE groups SET notify = ? WHERE gid = ?",
                             (1 if on else 0, gid))
            self._db.commit()

    def add_event(self, peer: str, body: str,
                  created_at: Optional[str] = None) -> int:
        """A line of conversation history that nobody sent — "Ann added
        Ben", "You left". Stored as an already-read incoming row of kind
        'event', so older releases still show its text."""
        with self._lock:
            cur = self._db.execute(
                """INSERT INTO messages (peer, direction, body, status, read,
                                         created_at, receipt_sent, kind)
                   VALUES (?, 'in', ?, 0, 1, ?, 1, 'event')""",
                (peer, body, created_at or now_iso()))
            self._db.commit()
            return cur.lastrowid

    # ---- v9.1: reactions -------------------------------------------------------
    def set_reaction(self, peer: str, cid: str, reactor: str,
                     emoji: str) -> bool:
        """Record `reactor`'s reaction to message `cid` in `peer`'s chat;
        an empty emoji takes it back. True if anything changed."""
        with self._lock:
            old = self._db.execute(
                "SELECT emoji FROM reactions WHERE peer = ? AND cid = ? "
                "AND reactor = ?", (peer, cid, reactor)).fetchone()
            if emoji:
                if old is not None and old["emoji"] == emoji:
                    return False
                self._db.execute(
                    """INSERT INTO reactions (peer, cid, reactor, emoji, at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(peer, cid, reactor) DO UPDATE SET
                           emoji = excluded.emoji, at = excluded.at""",
                    (peer, cid, reactor, emoji, now_iso()))
            else:
                if old is None:
                    return False
                self._db.execute(
                    "DELETE FROM reactions WHERE peer = ? AND cid = ? "
                    "AND reactor = ?", (peer, cid, reactor))
            self._db.commit()
        return True

    def reaction_of(self, peer: str, cid: str, reactor: str) -> str:
        with self._lock:
            row = self._db.execute(
                "SELECT emoji FROM reactions WHERE peer = ? AND cid = ? "
                "AND reactor = ?", (peer, cid, reactor)).fetchone()
        return row["emoji"] if row else ""

    def reactions_for(self, peer: str, cids: list) -> dict:
        """{cid: [(emoji, reactor, at), …]} oldest first, for `cids`."""
        cids = [c for c in cids if c]
        if not cids:
            return {}
        out: dict = {}
        with self._lock:
            for start in range(0, len(cids), 400):
                chunk = cids[start:start + 400]
                marks = ",".join("?" * len(chunk))
                for row in self._db.execute(
                        f"""SELECT cid, emoji, reactor, at FROM reactions
                            WHERE peer = ? AND cid IN ({marks})
                            ORDER BY at, reactor""", [peer] + chunk):
                    out.setdefault(row["cid"], []).append(
                        (row["emoji"], row["reactor"], row["at"]))
        return out

    # ---- plaintext chat import / export --------------------------------------
    def import_message(self, peer: str, direction: str, body: str,
                       created_at: str, status: int, read: int) -> bool:
        """Insert an imported row unless an identical one already exists."""
        if direction not in ("in", "out"):
            return False
        with self._lock:
            dup = self._db.execute(
                """SELECT 1 FROM messages WHERE peer = ? AND direction = ?
                   AND body = ? AND created_at = ?""",
                (peer, direction, body, created_at)).fetchone()
            if dup:
                return False
            self._db.execute(
                """INSERT INTO messages (peer, direction, body, status, read,
                                         created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (peer, direction, body, int(status), int(read), created_at))
            self._db.commit()
            return True


# =============================================================================
#  SyncWorker — realtime WebSocket sync with HTTP-polling fallback
# =============================================================================
def describe_network_error(exc: Exception) -> str:
    """Turn a transport exception into something a user can act on.

    v7.1: every failure used to surface as the single string "Network error
    — is the relay reachable?", which is the least useful thing we could
    have said. It cannot distinguish a relay that is switched off from a
    phone with no signal from a TLS mismatch — and on Android the real
    cause was almost always the last one, invisibly.
    """
    text = str(exc).lower()
    if isinstance(exc, requests.exceptions.SSLError):
        return ("Secure connection failed — the relay's certificate was "
                "rejected")
    if isinstance(exc, (requests.exceptions.ConnectTimeout,
                        requests.exceptions.ReadTimeout)):
        return "The relay didn't respond in time — the network may be slow"
    if isinstance(exc, requests.exceptions.ConnectionError):
        if "name or service not known" in text or "nodename nor servname" \
                in text or "getaddrinfo" in text:
            return "Couldn't look up the relay's address — check your network"
        if "connection refused" in text:
            return "The relay refused the connection — it may be offline"
        if "network is unreachable" in text:
            return "No route to the relay — are you online?"
        return "Couldn't reach the relay — check your connection"
    return f"Network problem: {type(exc).__name__}"


class Endpoint:
    """Works out which transport the relay actually speaks, and remembers.

    HTTPS is preferred but not required. The resolved choice is cached on
    disk so later launches skip probing, and it is invalidated the moment a
    request fails so a relay that gains (or loses) TLS is picked up without
    a reinstall.

    On a TLS failure the client falls back to plain HTTP rather than
    accepting an untrusted certificate. That is deliberate: falling back is
    visible and we tell the user their transport is unencrypted, whereas
    quietly trusting a bad certificate would show a padlock the user has no
    reason to believe. Never trade an honest downgrade for a dishonest
    upgrade.
    """

    PROBE_TIMEOUT = 6         # generous: mobile networks are slow to answer
    CACHE_NAME = "transport.json"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._base: Optional[str] = None
        self._secure = False
        self._cache = DATA_DIR / self.CACHE_NAME
        self._load_cache()

    # ---- candidates ---------------------------------------------------------
    def _candidates(self) -> list:
        if SERVER_URL_OVERRIDE:
            base = SERVER_URL_OVERRIDE.rstrip("/")
            if REQUIRE_TLS and base.startswith("http://"):
                return ["https://" + base[len("http://"):]]
            # An explicit scheme is tried first; the sibling scheme remains a
            # fallback so a misconfigured build still connects.
            other = ("http://" + base[len("https://"):]
                     if base.startswith("https://")
                     else "https://" + base[len("http://"):])
            return [base] if REQUIRE_TLS else [base, other]
        host = SERVER_HOST.strip().rstrip("/")
        for prefix in ("https://", "http://"):
            if host.startswith(prefix):
                host = host[len(prefix):]
        secure = f"https://{host}"
        return [secure] if REQUIRE_TLS else [secure, f"http://{host}"]

    # ---- cache --------------------------------------------------------------
    def _load_cache(self) -> None:
        try:
            data = json.loads(self._cache.read_text("utf-8"))
            base = data.get("base")
            if isinstance(base, str) and base in self._candidates():
                self._base = base
                self._secure = base.startswith("https://")
        except (OSError, ValueError):
            pass

    def _save_cache(self) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._cache.write_text(json.dumps({"base": self._base}), "utf-8")
        except OSError:
            pass

    # ---- resolution ---------------------------------------------------------
    def _probe(self, base: str) -> bool:
        try:
            resp = requests.get(f"{base}/api/health",
                                timeout=self.PROBE_TIMEOUT)
            return resp.status_code < 500
        except requests.RequestException:
            return False

    def resolve(self, force: bool = False) -> Optional[str]:
        """Return a usable base URL, probing if we do not have one."""
        with self._lock:
            if self._base is not None and not force:
                return self._base
        chosen, errors = None, []
        for candidate in self._candidates():
            if self._probe(candidate):
                chosen = candidate
                break
            errors.append(candidate)
        with self._lock:
            if chosen is not None:
                changed = chosen != self._base
                self._base = chosen
                self._secure = chosen.startswith("https://")
                if changed:
                    self._save_cache()
                    log_transport(chosen, self._secure)
            return self._base if chosen is None else chosen

    def invalidate(self) -> None:
        """Forget the current choice so the next call re-probes."""
        with self._lock:
            self._base = None

    # ---- accessors ----------------------------------------------------------
    @property
    def base(self) -> str:
        """Best known base URL. Falls back to the first candidate so callers
        always have something to try rather than crashing on None."""
        with self._lock:
            if self._base is not None:
                return self._base
        return self._candidates()[0]

    @property
    def secure(self) -> bool:
        with self._lock:
            return self._secure

    @property
    def resolved(self) -> bool:
        with self._lock:
            return self._base is not None

    def ws_url(self) -> str:
        base = self.base
        return (base.replace("https://", "wss://")
                    .replace("http://", "ws://")) + "/ws"


def log_transport(base: str, secure: bool) -> None:
    print(f"[FEXT] transport: {base} "
          f"({'TLS' if secure else 'CLEARTEXT — metadata is exposed'})",
          file=sys.stderr)


ENDPOINT = Endpoint()


def _ws_ssl_options() -> dict:
    """Trust store for the WebSocket transport.

    v7 fix for a silent, mobile-only failure. `requests` verifies against
    the certifi bundle SHIPPED INSIDE the app. `websocket-client` also
    verifies by default, but builds its context with
    ssl.create_default_context().load_default_certs() — the OPERATING
    SYSTEM trust store. In a python-for-android build the OpenSSL default
    certificate paths frequently do not resolve, so HTTPS succeeds while
    WSS fails to handshake.

    That failure is invisible: the sync worker falls back to HTTP polling
    and reports "Connected (polling)", which looks like success while
    realtime push is silently gone and messages only appear every few
    seconds. Pointing websocket-client at the same certifi bundle keeps
    both transports on one trust store.

    Verification is never disabled — an unverified TLS session would defeat
    the entire reason for moving off cleartext.
    """
    try:
        import certifi
        return {"ca_certs": certifi.where()}
    except ImportError:
        return {}          # fall back to the OS store rather than skip TLS


class SyncWorker(threading.Thread):
    """Background thread that keeps this identity synchronized.

    Preferred transport: a persistent WebSocket to ws://…/ws — the server
    pushes envelopes and tick receipts instantly, and this thread answers
    with delivery acks ('ack') the moment a message is decrypted + stored
    (that ack is what deletes the ciphertext from the server and lights the
    sender's 2nd golden tick).

    Fallback transport: if the websocket-client package is missing or the
    socket cannot be established, the worker degrades to HTTP polling of
    /api/messages/sync + /api/events/sync every POLL_INTERVAL_SECONDS.

    All UI mutation goes through `ui_queue` — never directly from here.
    """

    def __init__(self, app: "FextApp") -> None:
        super().__init__(daemon=True)
        self.app = app
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()     # "retry right now"
        self._ws = None
        self._ws_lock = threading.Lock()
        # relay id -> consecutive failed decryption attempts. In memory on
        # purpose: a restart is exactly when a key problem may have been
        # fixed, so the count should start again.
        self._decrypt_failures: dict = {}
        # Which heartbeat this worker keeps alive ('app' or 'service'); see
        # write_heartbeat. None disables beating (tests).
        self.beat_owner: Optional[str] = "app"
        self._last_beat = 0.0

    def _beat(self) -> None:
        if self.beat_owner and time.monotonic() - self._last_beat \
                >= HEARTBEAT_EVERY:
            self._last_beat = time.monotonic()
            write_heartbeat(self.beat_owner)

    def nudge(self) -> None:
        """Cut any pending backoff short and re-dial immediately."""
        self.wake_event.set()

    def _sleep(self, seconds: float) -> None:
        """Interruptible wait: returns early on stop OR on a manual retry."""
        self.wake_event.clear()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.stop_event.is_set() or self.wake_event.is_set():
                return
            self._beat()
            time.sleep(min(0.1, end - time.monotonic()))

    # ---- outbound frames (called from UI thread too) -------------------------
    def send_ws(self, payload: dict) -> bool:
        """Best-effort frame over the live socket. False when offline.

        CAUTION at every call site: True here means "the bytes entered a
        socket buffer", NOT "the relay received them". A half-open TCP
        connection accepts writes for a long time before anything notices.
        Anything that must not be lost needs a transport that answers.
        """
        with self._ws_lock:
            ws = self._ws
            if ws is None:
                return False
            try:
                ws.send(json.dumps(payload, separators=(",", ":")))
                return True
            except Exception:
                return False

    def send_read_receipts(self, server_ids: list) -> None:
        """3/3 tick.

        The socket is tried first because it makes the sender's third tick
        appear instantly. It is NOT trusted, because it cannot answer: the
        HTTP endpoint returns a count, so that is what actually clears the
        receipt from the retry queue. Sending both is harmless — the relay's
        read confirmation is idempotent and emits at most one receipt per
        message — and it is the difference between a third tick that usually
        arrives and one that always does.
        """
        ids = [i for i in server_ids if i]
        if not ids:
            return
        self.send_ws({"type": "read", "ids": ids})      # fast path
        self._flush_read_receipts(ids)                  # confirming path

    def _flush_read_receipts(self, ids: Optional[list] = None) -> None:
        """Confirm outstanding read receipts over HTTP, retrying forever
        until the relay acknowledges. Called with explicit ids on a fresh
        read, and with none from the sync loop to drain the backlog."""
        if ids is None:
            pairs = self.app.store.pending_read_receipts()
        else:
            pairs = self.app.store.peers_for_server_ids(
                [i for i in ids if i])
        if not pairs:
            return
        server_ids = [sid for sid, _ in pairs]
        try:
            self.app.api.read(server_ids)
        except (requests.RequestException, ApiClient.ApiError) as exc:
            log.debug("read receipts for %s not confirmed yet (%s) — "
                      "will retry", server_ids, exc)
            return
        self.app.store.confirm_read_receipts(server_ids)
        me = MessageFlowLog.who(self.app.keys.address, self.app.keys.username)
        for server_id, peer in pairs:
            contact = self.app.store.get_contact(peer) or {}
            MessageFlowLog.tick(
                3, server_id, MessageFlowLog.who(peer, contact.get("username")),
                me, "[our read receipt confirmed by the relay]")

    # ---- inbound handling ----------------------------------------------------
    #: How many times an undecryptable envelope is refused before it is
    #: acked away. Bounded so one poison row cannot wedge the mailbox
    #: forever, but not 1, because the usual cause — a contact key that is
    #: momentarily stale — fixes itself on the next directory refresh.
    MAX_DECRYPT_ATTEMPTS = 3

    def _ingest_message(self, msg: dict) -> Optional[int]:
        """Decrypt + verify + store one envelope.

        Returns the relay id to ACKNOWLEDGE, or None to leave the message on
        the relay for another attempt.

        This function is where the two-golden-ticks bug lived. It used to
        `return msg.get("id")` on EVERY path, including the two where it had
        stored nothing: an envelope that would not decrypt, and an insert
        that came back False. Acknowledging is what makes the relay DELETE
        its only copy and tell the sender "delivered", so those two paths
        destroyed the message and lit a second golden tick on a screen far
        away. An acknowledgement now costs custody: it is returned only once
        the plaintext is durably in this device's database.
        """
        server_id = msg.get("id")
        sender = msg.get("sender", "")
        cid = msg.get("client_msg_id")
        who = MessageFlowLog.who(sender)
        fragment = MessageFlowLog.fragment(msg.get("envelope"))

        try:
            payload = self.app.crypto.open_envelope(msg.get("envelope", {}),
                                                    sender, cid=cid)
        except CryptoEngine.EnvelopeError as exc:
            return self._handle_undecryptable(server_id, sender, who,
                                              fragment, exc)

        inner = payload["sender"]
        x = payload.get("x")
        ctl = x.get("ctl") if isinstance(x, dict) and x.get("k") == "ctl" \
            else None
        group_traffic = isinstance(x, dict) and (
            isinstance(x.get("grp"), dict) or (isinstance(ctl, dict) and (
                ctl.get("grp") or ctl.get("op") in ("group", "leave"))))
        # Verified sender keys come from INSIDE the authenticated ciphertext
        # — trust them over anything the directory says. Someone met only
        # in a group is remembered but not added to the chat list.
        try:
            self.app.store.upsert_contact(sender, inner.get("username"),
                                          inner["public_key"],
                                          listed=not group_traffic)
        except sqlite3.Error:
            log.exception("could not update contact record for %s", sender)
            MessageFlowLog.dropped(server_id, who, "contact upsert failed")
            return None

        conversation, author = sender, None
        kind, ext, media_state = "text", None, ""
        if x is not None:
            # A verified block proves this peer runs v9: remember it, so the
            # screen can offer what only v9 peers understand.
            cid = cid or x.get("cid")
            try:
                self.app.store.add_caps(sender, LocalStore.CAP_V9)
            except sqlite3.Error:
                log.exception("could not record capabilities of %s", sender)
            if x.get("k") == "ctl":
                return self._handle_control(server_id, sender, cid, ctl, who,
                                            inner)
            if isinstance(x.get("grp"), dict):
                gid = self._group_route(sanitize_gid(x["grp"].get("id")),
                                        sender, inner)
                if gid is None:
                    # From someone who is not (or no longer) a member of a
                    # group we are in. Not ours to show: acknowledge it so
                    # the relay forgets it, and store nothing.
                    MessageFlowLog.dropped(server_id, who,
                                           "group message from a non-member")
                    return server_id
                conversation, author = group_peer(gid), sender
            ext = sanitize_ext(x) or None
            media = (ext or {}).get("media")
            if x.get("k") == "media" and media is not None:
                kind, media_state = media["kind"], "pending"

        body = sanitize_text(str(payload.get("body", "")))
        outcome = self.app.store.add_in(
            peer=conversation, body=body,
            created_at=payload.get("sent_at",
                                   msg.get("created_at", now_iso())),
            server_id=server_id, client_msg_id=cid, kind=kind, ext=ext,
            media_state=media_state, author=author)

        if outcome == LocalStore.FAILED:
            # Not ours yet. Withhold the ack; the relay keeps its copy and
            # re-pushes on the next connect.
            MessageFlowLog.dropped(server_id, who, "local database write failed")
            return None

        if outcome == LocalStore.DUPLICATE:
            # We genuinely already hold this one — a re-push after a
            # reconnect. Acking is correct and stops it coming back.
            flow_log.debug("·   in   #%s  from %s  already held  %s",
                           server_id, who, fragment)
            self._decrypt_failures.pop(server_id, None)
            return server_id

        self._decrypt_failures.pop(server_id, None)
        MessageFlowLog.tick(2, server_id,
                            MessageFlowLog.who(sender, inner.get("username")),
                            MessageFlowLog.who(self.app.keys.address,
                                               self.app.keys.username),
                            f"{fragment}  [stored locally — acking]")
        self.app.ui_queue.put(("message_stored", conversation))
        # Separate from message_stored, which also fires for receipts and
        # deletions: THIS one means a new message someone may want to hear
        # about, and is what notifications are raised from.
        self.app.ui_queue.put(("incoming", conversation, body, kind, sender))
        if media_state == "pending":
            self.app.ui_queue.put(("media_pending", conversation, cid))
        return server_id

    # ---- v9.1: groups ----------------------------------------------------------
    def _member_entry(self, address: str, inner: Optional[dict] = None) -> dict:
        if address == self.app.keys.address:
            entry = {"a": address, "k": self.app.keys.public_key_hex}
            if self.app.keys.username:
                entry["u"] = self.app.keys.username
            return entry
        entry = {"a": address, "k": (inner or {}).get("public_key", "")}
        if (inner or {}).get("username"):
            entry["u"] = inner["username"]
        return entry

    def _group_route(self, gid: Optional[str], sender: str,
                     inner: dict) -> Optional[str]:
        """Which group conversation a group message belongs in, or None to
        drop it. A message for a group we have never heard of — its
        invitation is still on the way — is kept in a placeholder that the
        group's real state replaces when it arrives."""
        if gid is None:
            return None
        store = self.app.store
        group = store.group(gid)
        if group is None:
            store.save_group({"id": gid, "name": "New group", "v": 0,
                              "admin": sender,
                              "members": [self._member_entry(sender, inner),
                                          self._member_entry(
                                              self.app.keys.address)]},
                             status="pending")
            return gid
        if group["state"] in ("left", "removed"):
            return None
        if sender not in {m["a"] for m in group["members"]}:
            return None
        return gid

    def _who(self, address: str, start: bool = True) -> str:
        if address == self.app.keys.address:
            return "You" if start else "you"
        contact = self.app.store.get_contact(address) or {}
        return contact.get("username") or short_addr(address, 10)

    def _apply_group_state(self, sender: str, state: Optional[dict]) -> None:
        """Adopt a group's new state — only from its admin, only if newer.

        An invitation (a state we did not have) is accepted only from the
        admin it names and only if it lists us; after that only the admin we
        already know may change the group, and only with a higher version,
        so neither a member nor a replay can rewrite it.
        """
        if state is None:
            return
        store, me = self.app.store, self.app.keys.address
        gid = state["id"]
        peer = group_peer(gid)
        local = store.group(gid)
        members = {m["a"] for m in state["members"]}
        if local is None or local["state"] == "pending":
            if sender != state["admin"] or me not in members:
                return
            store.save_group(state, "active")
            for member in state["members"]:
                if member["a"] != me:
                    store.upsert_contact(member["a"], member.get("u"),
                                         member["k"], listed=False)
            store.add_event(peer, f"{self._who(sender)} added you to "
                                  f"\u201c{state['name']}\u201d")
            self.app.ui_queue.put(("message_stored", peer))
            return
        if sender != local["admin"] or state["v"] <= local["version"]:
            return
        before = {m["a"] for m in local["members"]}
        status = "active" if me in members else (
            "left" if local["state"] == "left" else "removed")
        store.save_group(state, status)
        for member in state["members"]:
            if member["a"] != me:
                store.upsert_contact(member["a"], member.get("u"),
                                     member["k"], listed=False)
        who = self._who(sender)
        if state["name"] != local["name"]:
            store.add_event(peer, f"{who} renamed the group "
                                  f"\u201c{state['name']}\u201d")
        added = [m for m in state["members"] if m["a"] not in before]
        removed = [a for a in before if a not in members]
        if added:
            store.add_event(peer, f"{who} added " + ", ".join(
                self._who(m["a"], start=False) if m["a"] == me
                else m.get("u") or self._who(m["a"]) for m in added))
        for address in removed:
            if address == me:
                store.add_event(peer, f"{who} removed you")
            elif address != sender:
                store.add_event(peer, f"{who} removed {self._who(address)}")
        if state["admin"] != local["admin"]:
            store.add_event(peer, f"{self._who(state['admin'])} now "
                                  f"administers the group")
        self.app.ui_queue.put(("message_stored", peer))

    def _apply_leave(self, sender: str, gid: Optional[str]) -> None:
        """A member left. Everyone drops them at once; if it was the admin,
        the first remaining member takes over — the same choice on every
        phone — and the admin (old or new) publishes the result."""
        store, me = self.app.store, self.app.keys.address
        local = store.group(gid) if gid else None
        if local is None or sender not in {m["a"] for m in local["members"]}:
            return
        members = [m for m in local["members"] if m["a"] != sender]
        admin = local["admin"]
        if admin == sender:
            admin = members[0]["a"] if members else me
        store.save_group({"id": gid, "name": local["name"],
                          "v": local["version"], "admin": admin,
                          "members": members}, local["state"])
        peer = group_peer(gid)
        store.add_event(peer, f"{self._who(sender)} left")
        self.app.ui_queue.put(("message_stored", peer))
        if admin == me and local["state"] == "active" and len(members) > 1:
            self.app.ui_queue.put(("group_publish", gid))

    def _handle_control(self, server_id, sender: str, cid: Optional[str],
                        ctl, who: str,
                        inner: Optional[dict] = None) -> Optional[int]:
        """Act on a control message (v9). Nothing is stored for one — it is
        an instruction, not a message — and it is always acknowledged, since
        a malformed or unknown instruction is not going to improve by being
        re-delivered.

        A control naming a group ("grp") acts on that group's conversation,
        and only if the sender is one of its members.
        """
        store = self.app.store
        if not isinstance(ctl, dict) or not store.consume_control(sender, cid):
            return server_id
        op = ctl.get("op")
        if op == "group":
            self._apply_group_state(sender, sanitize_group_state(ctl.get("g")))
            return server_id
        if op == "leave":
            self._apply_leave(sender, sanitize_gid(ctl.get("grp")))
            return server_id
        conversation = sender
        if ctl.get("grp") is not None:
            gid = sanitize_gid(ctl.get("grp"))
            group = store.group(gid) if gid else None
            if group is None or group["state"] != "active" or sender not in {
                    m["a"] for m in group["members"]}:
                log.info("ignoring %r from %s for a group it is not in", op,
                         sender)
                return server_id
            conversation = group_peer(gid)
        if op == "react":
            target = _str(ctl.get("cid"), 64)
            emoji = sanitize_emoji(ctl.get("e"))
            if target and _TOKEN_RE.match(target) and emoji is not None:
                if store.set_reaction(conversation, target, sender, emoji):
                    self.app.ui_queue.put(("reaction", conversation, target,
                                           sender, emoji))
                    self.app.ui_queue.put(("message_stored", conversation))
            return server_id
        if op == "delete":
            cids = ctl.get("cids") if isinstance(ctl.get("cids"), list) else []
            removed = store.delete_by_author(sender, cids, conversation)
            for row in removed:
                discard_media_file(row.get("media_path"))
            flow_log.info("✗   in   ctl  from %s  deleted %d of %d message(s) "
                          "for both sides", who, len(removed), len(cids))
            self.app.ui_queue.put(("message_stored", conversation))
        elif op == "resend":
            # They could not fetch a photo/voice note we sent — the relay
            # has let it expire. Our device still has the file: upload again.
            target = _str(ctl.get("cid"), 64)
            if target:
                self.app.ui_queue.put(("resend_request", sender, target,
                                       gid_of(conversation)))
        elif op == "remedia":
            target = _str(ctl.get("cid"), 64)
            media = sanitize_media(ctl.get("media"))
            row = (store.message_by_cid(conversation, target, "in")
                   if target and media else None)
            if row is not None and (row.get("author") or row["peer"]) == sender:
                ext = json.loads(row.get("ext") or "{}")
                ext["media"] = media
                store.set_media(row["id"], state="pending", ext=ext)
                self.app.ui_queue.put(("message_stored", conversation))
                self.app.ui_queue.put(("media_pending", conversation, target))
        else:
            log.info("ignoring unknown control op %r from %s", op, sender)
        return server_id

    def _handle_undecryptable(self, server_id, sender: str, who: str,
                              fragment: str, exc: Exception) -> Optional[int]:
        """An envelope that will not open.

        Usually a stale contact key — the sender encrypted to an identity
        this device has since replaced. Refusing to ack keeps the relay's
        copy alive so a later attempt can succeed, but refusing FOREVER
        would wedge the mailbox behind one bad row, so after a few attempts
        the message is recorded locally as unreadable and acked away. Either
        way the user is told; the one outcome that is not acceptable is the
        old one, where it disappeared and the sender was told 'delivered'.
        """
        attempts = self._decrypt_failures.get(server_id, 0) + 1
        self._decrypt_failures[server_id] = attempts
        if attempts < self.MAX_DECRYPT_ATTEMPTS:
            MessageFlowLog.dropped(
                server_id, who,
                f"{exc} (attempt {attempts}/{self.MAX_DECRYPT_ATTEMPTS})")
            return None

        log.error("giving up on relay message %s from %s after %d attempts: "
                  "%s — recording it as unreadable", server_id, sender,
                  attempts, exc)
        placeholder = ("⚠ A message arrived that this device could not "
                       "decrypt. It was most likely encrypted to an older "
                       "identity key. Ask the sender to try again.")
        # Stored as ALREADY READ so no read receipt is ever emitted for it:
        # the sender is entitled to know it arrived (2/3), and entitled NOT
        # to be told it was read, because it never was.
        self.app.store.add_in(
            peer=sender, body=placeholder, created_at=now_iso(),
            server_id=server_id, client_msg_id=None, unread=False)
        flow_log.warning("✗   in   #%s  from %s  UNREADABLE after %d "
                         "attempts  %s  [placeholder stored — acking]",
                         server_id, who, attempts, fragment)
        self._decrypt_failures.pop(server_id, None)
        self.app.ui_queue.put(("message_stored", sender))
        return server_id

    def _handle_frame(self, frame: dict) -> Optional[dict]:
        """Process one pushed frame; returns an ack frame to send, if any."""
        kind = frame.get("type")
        if kind == "message":
            server_id = self._ingest_message(frame)
            if server_id:
                return {"type": "ack", "ids": [server_id]}
        elif kind == "receipt":
            server_id = frame.get("server_id", 0)
            status = frame.get("status", "")
            peer = self.app.store.apply_receipt(server_id, status)
            if peer:
                contact = self.app.store.get_contact(peer) or {}
                MessageFlowLog.tick(
                    {"delivered": 2, "read": 3}.get(status, 0), server_id,
                    MessageFlowLog.who(self.app.keys.address,
                                       self.app.keys.username),
                    MessageFlowLog.who(peer, contact.get("username")),
                    "[receipt from relay]")
                self.app.ui_queue.put(("message_stored", peer))
        return None

    # ---- transports ----------------------------------------------------------
    def _ws_session(self) -> bool:
        """One WebSocket session. Returns True if it ever authenticated
        (used to decide whether to bother with the HTTP fallback)."""
        if _websocket_mod is None:
            return False
        url = ENDPOINT.ws_url()
        try:
            ws = _websocket_mod.create_connection(
                url, timeout=10, sslopt=_ws_ssl_options())
        except Exception as exc:
            # A wss:// failure against a relay that only speaks ws:// is not
            # an outage, it is a wrong guess. Drop the cached choice so the
            # next cycle re-probes and settles on whatever the relay really
            # offers, instead of retrying the same broken scheme forever.
            if url.startswith("wss://"):
                text = str(exc).lower()
                if ("certificate" in text or "wrong_version_number" in text
                        or "sslv3" in text or "ssl" in text):
                    ENDPOINT.invalidate()
                    ENDPOINT.resolve(force=True)
            return False
        try:
            fields = self.app.api.auth_fields("ws.connect")
            ws.send(json.dumps({"type": "auth", **fields}))
            hello = json.loads(ws.recv())
            if hello.get("type") != "auth_ok":
                if hello.get("type") == "auth_error":
                    code = hello.get("code", "")
                    if code == "unknown_address":
                        # The relay has no record of this identity — its
                        # database was reset, or this key was never
                        # registered here. Recoverable: ask for a name.
                        # Stop the worker so it does not spin re-dialing
                        # against an identity the relay will keep rejecting.
                        self.stop_event.set()
                        self.app.ui_queue.put(("needs_registration",
                                               hello.get("message", "")))
                    else:
                        self.app.ui_queue.put(
                            ("status", f"Realtime auth failed: "
                             f"{hello.get('message', 'unknown error')}",
                             COL_DANGER))
                return False
            with self._ws_lock:
                self._ws = ws
            # A reconnect is the first honest chance to discover that
            # receipts written to the PREVIOUS socket never landed.
            threading.Thread(target=self._flush_read_receipts,
                             daemon=True).start()
            # Say what is actually true. Message bodies are end-to-end
            # encrypted on every transport, but on cleartext the metadata
            # around them is not — and the user is entitled to know which
            # they have rather than seeing one reassuring string regardless.
            if ENDPOINT.secure:
                self.app.ui_queue.put(
                    ("status", "Connected · encrypted end-to-end and in "
                     "transit", COL_OK))
            else:
                self.app.ui_queue.put(
                    ("status", "Connected · end-to-end encrypted "
                     "(transport not secured)", COL_TICK_GOLD))
            # Short recv tick -> the loop wakes every few seconds to honour
            # stop_event and the keepalive schedule even while idle. The
            # keepalive is wall-clock based (not recv-timeout based) so a
            # socket busy RECEIVING pushes still emits an outbound frame at
            # least every WS_IDLE_TIMEOUT seconds — the server reaps
            # connections that stay silent longer than its read timeout.
            ws.settimeout(WS_RECV_TICK)
            last_outbound = time.monotonic()
            while not self.stop_event.is_set():
                self._beat()
                if time.monotonic() - last_outbound >= WS_IDLE_TIMEOUT:
                    if not self.send_ws({"type": "ping"}):
                        break
                    last_outbound = time.monotonic()
                try:
                    raw = ws.recv()
                except _websocket_mod.WebSocketTimeoutException:
                    continue
                if raw is None or raw == "":
                    break
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(frame, dict):
                    reply = self._handle_frame(frame)
                    if reply is not None:
                        # Failing to ack means the server would keep the
                        # ciphertext forever; drop the session and let the
                        # reconnect redeliver (dedup by server_id is safe).
                        if not self.send_ws(reply):
                            break
                        last_outbound = time.monotonic()
            return True
        except Exception:
            return True
        finally:
            with self._ws_lock:
                self._ws = None
            try:
                ws.close()
            except Exception:
                pass

    def _http_poll_once(self) -> None:
        """HTTP fallback: drain queued receipts + undelivered envelopes."""
        for event in self.app.api.events_sync().get("events", []):
            if isinstance(event, dict):
                self._handle_frame(event)
        self._flush_read_receipts()          # retry anything unconfirmed
        while True:
            data = self.app.api.sync(0)
            ack_ids = []
            skipped = 0
            for msg in data.get("messages", []):
                server_id = self._ingest_message(msg)
                if server_id:
                    ack_ids.append(server_id)
                else:
                    skipped += 1        # left on the relay deliberately
            if ack_ids:
                self.app.api.ack(ack_ids)
            if skipped:
                # Do not spin: the rows we refused are still pending, so
                # has_more/refetch would loop straight back onto them.
                log.info("%d message(s) left on the relay for another "
                         "attempt", skipped)
                break
            if not data.get("has_more"):
                break

    # ---- main loop -----------------------------------------------------------
    def run(self) -> None:
        """Transport priority per cycle:
             1. WebSocket session (blocks for its whole lifetime; the server
                re-pushes the full undelivered backlog on every connect, so
                nothing is missed across reconnects).
             2. One HTTP sweep when the socket is unavailable.
           Wait strategy between cycles:
             * live socket dropped        -> re-dial almost immediately
             * no socket, HTTP reachable  -> steady POLL_INTERVAL cadence
             * fully offline              -> exponential backoff (capped)
        """
        online: Optional[bool] = None
        backoff = WS_RECONNECT_MIN
        while not self.stop_event.is_set():
            self._beat()
            had_ws = self._ws_session()
            if self.stop_event.is_set():
                break
            if had_ws:
                # A live session just ended (server restart, network blip):
                # skip the HTTP sweep and re-dial fast.
                online = None
                backoff = WS_RECONNECT_MIN
                self.app.ui_queue.put(("status", "Reconnecting…",
                                       COL_TEXT_DIM))
                self._sleep(WS_RECONNECT_MIN)
                continue
            reachable = False
            try:
                self._http_poll_once()
                reachable = True
                if online is not True:
                    online = True
                    if _websocket_mod is None:
                        self.app.ui_queue.put(
                            ("status", "Connected (polling — install "
                             "'websocket-client' for instant push)",
                             COL_TICK_GOLD))
                    else:
                        self.app.ui_queue.put(
                            ("status", "Connected (polling) · end-to-end "
                             "encrypted" + ("" if ENDPOINT.secure
                                            else " (transport not secured)"),
                             COL_OK if ENDPOINT.secure else COL_TICK_GOLD))
            except ApiClient.ApiError as exc:
                if exc.code == "unknown_address":
                    self.stop_event.set()
                    self.app.ui_queue.put(("needs_registration", exc.message))
                    break
                if online is not False:
                    online = False
                    self.app.ui_queue.put(("status", f"Sync error: "
                                           f"{exc.message}", COL_DANGER))
            except requests.RequestException as exc:
                if online is not False:
                    online = False
                    self.app.ui_queue.put(
                        ("status", f"{describe_network_error(exc)} — "
                         f"retrying…", COL_DANGER))
            if reachable:
                backoff = WS_RECONNECT_MIN
                self._sleep(POLL_INTERVAL_SECONDS)
            else:
                self._sleep(backoff)
                # While the app is on screen the user is WATCHING for their
                # messages, so a 30s blind window reads as "the app is
                # broken". QA saw exactly that after a relay restart or a
                # network transition: sync recovered, but only after a long
                # unexplained gap with no way to hurry it along. Cap the
                # backoff much lower in the foreground and let the user
                # force an immediate retry (see FextApp.retry_now).
                cap = (FOREGROUND_RECONNECT_MAX if self.app.foreground
                       else WS_RECONNECT_MAX)
                backoff = min(backoff * 2, cap)




# =============================================================================
#  Widget kit (v9.1) — the parts every screen is built from
# =============================================================================
TOUCH = dp(48)                     # the smallest tap target anywhere


def paint_round(widget, hex_color: str, radius=None, corners=None):
    """A rounded ground behind a widget. Returns (Color, shape) so callers
    can retint it (press feedback, selection).

    Curves are drawn anti-aliased. Kivy's smooth shapes need an opaque
    colour (a translucent one shows a darker rim), which every ground is.
    """
    radii = list(corners) if corners is not None else [
        radius if radius is not None else dp(6)]
    curved = any(r > 0 for r in radii)
    with widget.canvas.before:
        col = Color(*C(hex_color))
        rect = (SmoothRoundedRectangle if curved else RoundedRectangle)(
            pos=widget.pos, size=widget.size, radius=radii)

    def _sync(*_a):
        rect.pos, rect.size = widget.pos, widget.size
        if curved:
            # The smooth shape does not clamp a corner larger than half
            # the side the way the plain one does.
            cap = max(0.0, min(widget.width, widget.height) / 2.0)
            rect.radius = [min(r, cap) for r in radii]
    widget.bind(pos=_sync, size=_sync)
    return col, rect


def paint_border(widget, hex_color: str, radius=None, width=1.0):
    radius = dp(6) if radius is None else radius
    with widget.canvas.after:
        col = Color(*C(hex_color))
        line = SmoothLine(width=max(1.0, dp(width) / 2.0), rounded_rectangle=(
            widget.x, widget.y, widget.width, widget.height, radius))

    def _sync(*_a):
        line.rounded_rectangle = (widget.x + .5, widget.y + .5,
                                  widget.width - 1, widget.height - 1, radius)
    widget.bind(pos=_sync, size=_sync)
    return col


def paint_circle(widget, hex_color: str):
    with widget.canvas.before:
        col = Color(*C(hex_color))
        circ = SmoothEllipse(pos=widget.pos, size=widget.size)

    def _sync(*_a):
        circ.pos, circ.size = widget.pos, widget.size
    widget.bind(pos=_sync, size=_sync)
    return col


def hairline(widget, edge: str = "bottom", color: Optional[str] = None,
             inset: float = 0.0, inset_right: float = 0.0):
    """A one-pixel rule along one edge — the app's only kind of border."""
    with widget.canvas.after:
        col = Color(*C(color or T.rule))
        rule = Rectangle()

    def _sync(*_a):
        y = widget.y if edge == "bottom" else widget.top - dp(1)
        rule.pos = (widget.x + inset, y)
        rule.size = (max(0, widget.width - inset - inset_right), dp(1))
    widget.bind(pos=_sync, size=_sync)
    _sync()
    return col


def ink_color(seed: str) -> str:
    """A person's avatar colour, made readable as TEXT on this theme's
    surfaces (author names in a group)."""
    base = avatar_color(seed)
    return mix(base, "#FFFFFF", .45) if T.dark else mix(base, "#000000", .18)


class Rule(Widget):
    """A free-standing hairline (between rows, under a heading)."""

    def __init__(self, color: Optional[str] = None, inset: float = 0, **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(1))
        super().__init__(**kw)
        with self.canvas:
            Color(*C(color or T.rule))
            self._r = Rectangle()
        self._inset = inset
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_a):
        self._r.pos = (self.x + self._inset, self.y)
        self._r.size = (max(0, self.width - self._inset), dp(1))


class CapsLabel(Widget):
    """Tracked capitals in the caps face (data/fonts/ui.ttf).

    Capitals want letter-spacing, and Kivy's text renderer has none, so
    each glyph is rendered once (white, cached per size) and laid out here
    with the tracking added; the colour is a tint over the cached glyphs.
    Too long for its width, it ends in an ellipsis.
    """

    _glyphs: dict = {}

    def __init__(self, text: str = "", size: float = 11.5,
                 color: Optional[str] = None, tracking: float = 0.12,
                 halign: str = "left", **kw):
        self._px = fs(size)
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", round(self._px * 1.6))
        super().__init__(**kw)
        self._track = self._px * tracking
        self._color = color or T.text_3
        self.halign = halign
        self._text = ""
        self.natural_width = 0.0
        self.bind(pos=self._redraw, size=self._redraw)
        self.text = text

    @classmethod
    def glyph(cls, ch: str, px: float):
        key = (ch, round(px, 1))
        tex = cls._glyphs.get(key)
        if tex is None:
            core = CoreLabel(text=ch, font_size=px, font_name=CAPS_FONT_NAME,
                             color=(1, 1, 1, 1))
            core.refresh()
            tex = cls._glyphs[key] = core.texture
        return tex

    @property
    def text(self) -> str:
        return self._text

    @text.setter
    def text(self, value: str) -> None:
        self._text = str(value or "").upper()
        widths = [self.glyph(c, self._px).width for c in self._text]
        self.natural_width = (sum(widths) + self._track *
                              max(0, len(widths) - 1))
        self._redraw()

    def set_color(self, hex_color: str) -> None:
        self._color = hex_color
        self._redraw()

    def _fit(self) -> str:
        text = self._text
        if self.width <= 0 or self.natural_width <= self.width + 0.5:
            return text
        ell = self.glyph("…", self._px).width
        room, out = self.width - ell, ""
        for ch in text:
            w = self.glyph(ch, self._px).width + self._track
            if room - w < 0:
                break
            room -= w
            out += ch
        return out.rstrip() + "…"

    def _redraw(self, *_a) -> None:
        self.canvas.clear()
        text = self._fit()
        if not text:
            return
        texes = [self.glyph(c, self._px) for c in text]
        total = sum(t.width for t in texes) + self._track * (len(texes) - 1)
        if self.halign == "center":
            x = (self.x + self.width / 2) - total / 2
        elif self.halign == "right":
            x = self.right - total
        else:
            x = self.x
        with self.canvas:
            Color(*C(self._color))
            for tex in texes:
                y = round((self.y + self.height / 2) - tex.height / 2)
                Rectangle(texture=tex, pos=(round(x), y), size=tex.size)
                x += tex.width + self._track


class InfoLabel(Label):
    """Wrapping serif text that sizes itself to its content."""

    def __init__(self, text: str = "", color: Optional[str] = None,
                 font_size=None, bold: bool = False, halign: str = "left",
                 italic: bool = False, **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("markup", True)
        super().__init__(text=text, color=C(color or T.text), bold=bold,
                         italic=italic, font_size=font_size or fs(16),
                         halign=halign, valign="top", **kw)
        self.bind(width=lambda *_: setattr(self, "text_size",
                                           (self.width, None)),
                  texture_size=lambda *_: setattr(self, "height",
                                                  self.texture_size[1]))


def single_line(text: str, font_size, color, bold=False, **kw) -> Label:
    """A one-line label that ends in an ellipsis rather than overflowing —
    every name and preview in the app."""
    label = Label(text=text, font_size=font_size, color=C(color), bold=bold,
                  halign=kw.pop("halign", "left"), valign="middle",
                  shorten=True, shorten_from="right", max_lines=1, **kw)
    label.bind(size=lambda w, v: setattr(w, "text_size", v))
    return label


def line_height(size: float, factor: float = 1.32) -> float:
    """The height a line of serif text at `size` (sp) needs."""
    return round(fs(size) * factor)


class Btn(ButtonBehavior, Widget):
    """A button with a tracked-capitals label.

    primary  — gold ground, the one thing the screen is for
    quiet    — hairline outline, everything else
    danger   — red outline and text, for what cannot be undone
    solid_danger — red ground (the final 'Remove')
    text     — no ground at all, in the accent
    """

    def __init__(self, text: str = "", kind: str = "primary", **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", TOUCH)
        on_release = kw.pop("on_release", None)
        super().__init__(**kw)
        self.kind = kind
        fill = {"primary": T.accent, "solid_danger": T.alert}.get(kind)
        ink = {"primary": T.on_accent, "solid_danger": T.on_alert,
               "danger": T.alert, "text": T.accent}.get(kind, T.text)
        edge = {"quiet": T.rule_strong, "danger": T.alert}.get(kind)
        self._fill_hex = fill
        with self.canvas.before:
            self._bg = Color(*(C(fill) if fill else (0, 0, 0, 0)))
            self._shape = RoundedRectangle(radius=[dp(4)])
            self._edge = Color(*(C(edge) if edge else (0, 0, 0, 0)))
            self._line = Line(width=dp(1) / 2.0)
        self.label = CapsLabel(text, size=12.5, color=ink, tracking=0.14,
                               halign="center", size_hint=(None, None))
        self.add_widget(self.label)
        self.bind(pos=self._sync, size=self._sync, state=self._on_state,
                  disabled=lambda _w, d: setattr(self, "opacity",
                                                 .4 if d else 1))
        if on_release is not None:
            self.bind(on_release=on_release)

    @property
    def text(self) -> str:
        return self.label.text

    @text.setter
    def text(self, value: str) -> None:
        self.label.text = value

    def natural_width(self) -> float:
        return self.label.natural_width + dp(36)

    def _sync(self, *_a):
        self._shape.pos, self._shape.size = self.pos, self.size
        self._line.rounded_rectangle = (self.x + .5, self.y + .5,
                                        self.width - 1, self.height - 1,
                                        dp(4))
        self.label.size = (max(0, self.width - dp(20)), self.label.height)
        self.label.center = (self.x + self.width / 2, self.y + self.height / 2)

    def _on_state(self, _w, state):
        if self._fill_hex:
            self._bg.rgba = C(T.accent_press if self.kind == "primary"
                              and state == "down" else self._fill_hex)
            if self.kind == "solid_danger":
                self._bg.a = .85 if state == "down" else 1
        else:
            self._bg.rgba = C(T.press) if state == "down" else (0, 0, 0, 0)


class ButtonRow(BoxLayout):
    """Buttons side by side — or stacked, when their labels would not fit
    side by side (large text, a narrow phone)."""

    def __init__(self, buttons, spacing=None, **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", TOUCH)
        super().__init__(spacing=spacing if spacing is not None else dp(10),
                         **kw)
        self.buttons = list(buttons)
        for b in self.buttons:
            self.add_widget(b)
        self.bind(width=self._reflow)

    def _reflow(self, *_a) -> None:
        share = (self.width - self.spacing * (len(self.buttons) - 1)) / \
            max(1, len(self.buttons))
        stack = any(b.natural_width() > share for b in self.buttons)
        self.orientation = "vertical" if stack else "horizontal"
        n = len(self.buttons) if stack else 1
        self.height = TOUCH * n + self.spacing * (n - 1)


def button_row(*buttons, spacing=None) -> BoxLayout:
    return ButtonRow(buttons, spacing=spacing)


#: Icons are geometry, rendered once offscreen at ICON_SUPERSAMPLE times
#: their size and filtered down, so their edges are smooth whatever the GPU
#: offers — many desktops run without multisampling, and lines drawn
#: straight onto the window came out jagged there. Cached per icon and size,
#: drawn white and tinted when used.
ICON_SUPERSAMPLE = 4
_OFFSCREEN: dict = {}
_offscreen_ok = True
_DOWNSAMPLE_FS = """
$HEADER$
uniform vec2 texel;
void main(void) {
    /* four bilinear taps, each the mean of a 2x2 block: a 4x4 box filter */
    gl_FragColor = 0.25 * (
        texture2D(texture0, tex_coord0 + vec2(-texel.x, -texel.y)) +
        texture2D(texture0, tex_coord0 + vec2( texel.x, -texel.y)) +
        texture2D(texture0, tex_coord0 + vec2(-texel.x,  texel.y)) +
        texture2D(texture0, tex_coord0 + vec2( texel.x,  texel.y)));
}
"""


def offscreen_texture(key, width_px: int, height_px: int,
                      paint: Callable[[int], None]):
    """A white-on-clear texture of `paint(k)`'s drawing, made at k times the
    size and box-filtered down; None where offscreen drawing fails (the
    caller then draws directly). `key` caches it."""
    global _offscreen_ok
    tex = _OFFSCREEN.get(key)
    if tex is not None or not _offscreen_ok:
        return tex
    k = ICON_SUPERSAMPLE
    try:
        big = Fbo(size=(width_px * k, height_px * k), noadd=True)
        with big:
            ClearColor(1, 1, 1, 0)
            ClearBuffers()
            Color(1, 1, 1, 1)
            paint(k)
        big.draw()
        small = Fbo(size=(width_px, height_px), fs=_DOWNSAMPLE_FS,
                    noadd=True)
        if not small.shader.success:
            raise RuntimeError("the downsampling shader did not compile")
        small["texel"] = (1.0 / (width_px * k), 1.0 / (height_px * k))
        with small:
            ClearColor(1, 1, 1, 0)
            ClearBuffers()
            Color(1, 1, 1, 1)
            Rectangle(texture=big.texture, size=(width_px, height_px))
        small.draw()
        pixels = small.pixels
        tex = Texture.create(size=(width_px, height_px), colorfmt="rgba")
        tex.blit_buffer(pixels, colorfmt="rgba", bufferfmt="ubyte")
        # Kept so the texture can be refilled if the GL context is lost.
        tex.add_reload_observer(lambda t, px=pixels: t.blit_buffer(
            px, colorfmt="rgba", bufferfmt="ubyte"))
    except Exception:
        log.exception("offscreen drawing unavailable — drawing icons "
                      "directly")
        _offscreen_ok = False
        return None
    _OFFSCREEN[key] = tex
    return tex


def draw_icon(name: str, cx: float, cy: float, size: float, rgba,
              width: Optional[float] = None, scale: float = 1.0) -> None:
    """Draw one icon into the CURRENT canvas context: it fits a `size`
    square centred on (cx, cy), tinted `rgba`. `scale` shrinks the drawing
    (an animation) without rendering the icon again at every step.

    Icons are drawn, not typed: no font has the whole set, and drawing keeps
    them crisp at any density.
    """
    # Kivy's Line width is HALF the stroke: this draws one 1/16 of the icon
    # thick (1.5 units of the 24-unit grid), never thinner than 1.2dp.
    w = width or max(dp(0.6), size * 0.031)
    side = int(math.ceil(size * 1.25)) + 4
    tex = offscreen_texture(
        ("icon", name, round(size, 2), round(w, 3)), side, side,
        lambda k: icon_geometry(name, side * k / 2.0, side * k / 2.0,
                                size * k, (1, 1, 1, 1), w * k, dp(1) * k))
    Color(*rgba)
    if tex is None:
        icon_geometry(name, cx, cy, size * scale, rgba, w * scale, dp(1))
        return
    if scale == 1.0:
        Rectangle(texture=tex, size=(side, side),
                  pos=(round(cx - side / 2.0), round(cy - side / 2.0)))
    else:
        d = side * scale
        Rectangle(texture=tex, size=(d, d), pos=(cx - d / 2.0, cy - d / 2.0))


def icon_geometry(name: str, cx: float, cy: float, size: float, rgba,
                  w: float, u: float) -> None:
    """The geometry of one icon, in the current colour and canvas: it fits a
    `size` square centred on (cx, cy), stroked `w` wide; `u` is one dp.

    The newer icons are laid out on a 24-unit grid with y running down, as
    icon artwork usually is; `G` maps grid points onto the canvas.
    """
    h = size / 2.0
    s = size / 24.0
    line = dict(width=w, cap="round", joint="round")

    def G(*xy) -> list:
        out = []
        for i in range(0, len(xy), 2):
            out += [cx + (xy[i] - 12) * s, cy + (12 - xy[i + 1]) * s]
        return out

    def arc(x0, y0, r, a0, a1) -> list:
        """Grid-space arc; angles in degrees, clockwise on screen."""
        n = max(8, int(abs(a1 - a0) / 5))
        pts = []
        for i in range(n + 1):
            a = math.radians(a0 + (a1 - a0) * i / n)
            pts += G(x0 + r * math.cos(a), y0 + r * math.sin(a))
        return pts

    def fill(pts) -> None:
        """A convex polygon (canvas points), as a triangle fan."""
        verts = []
        for i in range(0, len(pts), 2):
            verts += [pts[i], pts[i + 1], 0, 0]
        Mesh(vertices=verts, indices=list(range(len(verts) // 4)),
             mode="triangle_fan")

    def dot(x, y, r) -> None:
        px, py = G(x, y)
        Ellipse(pos=(px - r * s, py - r * s), size=(2 * r * s, 2 * r * s))
    if name == "back":
        Line(points=[cx + h * .28, cy + h * .60, cx - h * .32, cy,
                     cx + h * .28, cy - h * .60], **line)
    elif name == "chevron":
        Line(points=[cx - h * .18, cy + h * .44, cx + h * .22, cy,
                     cx - h * .18, cy - h * .44], **line)
    elif name == "close":
        k = h * .52
        Line(points=[cx - k, cy - k, cx + k, cy + k], **line)
        Line(points=[cx - k, cy + k, cx + k, cy - k], **line)
    elif name == "more":                       # vertical ellipsis
        r = max(1.6 * u, size * .085)
        for dy in (-h * .56, 0, h * .56):
            Ellipse(pos=(cx - r, cy + dy - r), size=(2 * r, 2 * r))
    elif name == "plus":
        k = h * .58
        Line(points=[cx - k, cy, cx + k, cy], **line)
        Line(points=[cx, cy - k, cx, cy + k], **line)
    elif name == "send":
        # A folded paper plane: the near wing solid, the far one a tone
        # lighter, so the fold reads even at the size of a thumb.
        nose, top, notch, low = (21.0, 11.1), (2.9, 4.4), (8.6, 12.6), \
            (6.2, 19.9)
        fill(G(*notch, *nose, *top))
        Color(rgba[0], rgba[1], rgba[2], rgba[3] * .58)
        fill(G(*notch, *low, *nose))
        Color(*rgba)
    elif name in ("reply", "forward"):
        d = -1 if name == "reply" else 1
        tip = (cx + d * h * .70, cy + h * .22)
        Line(points=[tip[0] - d * h * .46, tip[1] + h * .44, tip[0], tip[1],
                     tip[0] - d * h * .46, tip[1] - h * .44], **line)
        Line(bezier=[tip[0], tip[1], cx - d * h * .55, cy + h * .22,
                     cx - d * h * .78, cy - h * .05,
                     cx - d * h * .70, cy - h * .66], **line)
    elif name == "copy":
        r = size * .08
        Line(rounded_rectangle=(cx - h * .70, cy - h * .76, h * 1.0, h * 1.16,
                                r), width=w)
        Line(points=[cx - h * .38, cy + h * .44, cx - h * .38, cy + h * .72,
                     cx + h * .70, cy + h * .72, cx + h * .70, cy - h * .36,
                     cx + h * .32, cy - h * .36], **line)
    elif name in ("star", "star_filled"):
        pts = []
        for i in range(10):
            ang = math.pi / 2 + i * math.pi / 5
            rad = h * (.84 if i % 2 == 0 else .37)
            pts += [cx + rad * math.cos(ang), cy + rad * math.sin(ang)]
        if name == "star_filled":
            verts = [cx, cy, 0, 0]
            for i in range(0, len(pts), 2):
                verts += [pts[i], pts[i + 1], 0, 0]
            verts += [pts[0], pts[1], 0, 0]
            Mesh(vertices=verts, indices=list(range(len(verts) // 4)),
                 mode="triangle_fan")
        else:
            Line(points=pts, close=True, width=w, joint="round")
    elif name == "share":
        r = size * .11
        a, b, c = ((cx + h * .50, cy + h * .56), (cx - h * .50, cy),
                   (cx + h * .50, cy - h * .56))
        Line(points=[a[0], a[1], b[0], b[1], c[0], c[1]], **line)
        for px, py in (a, b, c):
            Ellipse(pos=(px - r, py - r), size=(2 * r, 2 * r))
    elif name == "delete":
        top = cy + h * .50
        Line(points=[cx - h * .72, top, cx + h * .72, top], **line)
        Line(points=[cx - h * .25, top, cx - h * .25, top + h * .22,
                     cx + h * .25, top + h * .22, cx + h * .25, top], **line)
        Line(points=[cx - h * .54, top, cx - h * .42, cy - h * .76,
                     cx + h * .42, cy - h * .76, cx + h * .54, top], **line)
        for dx in (-.16, .16):
            Line(points=[cx + dx * h, cy + h * .26, cx + dx * h * .8,
                         cy - h * .50], width=w * .85, cap="round")
    elif name == "edit":                       # a pen, nib down
        ux, uy = .7071, .7071
        vx, vy = -.7071, .7071
        t = h * .22
        nib = (cx - h * .70, cy - h * .70)
        neck = (nib[0] + ux * h * .46, nib[1] + uy * h * .46)
        end = (cx + h * .68, cy + h * .68)
        Line(points=[neck[0] + vx * t, neck[1] + vy * t,
                     end[0] + vx * t, end[1] + vy * t,
                     end[0] - vx * t, end[1] - vy * t,
                     neck[0] - vx * t, neck[1] - vy * t], close=True, **line)
        Line(points=[neck[0] + vx * t, neck[1] + vy * t, nib[0], nib[1],
                     neck[0] - vx * t, neck[1] - vy * t], **line)
    elif name == "image":
        Line(rounded_rectangle=(cx - h * .80, cy - h * .64, h * 1.60,
                                h * 1.28, size * .08), width=w)
        r = size * .08
        Ellipse(pos=(cx + h * .30 - r, cy + h * .24 - r), size=(2 * r, 2 * r))
        Line(points=[cx - h * .78, cy - h * .44, cx - h * .26, cy + h * .06,
                     cx + h * .12, cy - h * .26, cx + h * .38, cy - h * .04,
                     cx + h * .78, cy - h * .46], **line)
    elif name == "mic":
        # Capsule with a grille, its cradle, stand and foot.
        Line(rounded_rectangle=(*G(8.9, 14.8), 6.2 * s, 12.4 * s, 3.1 * s),
             width=w)
        for gy in (6.5, 9.3):
            Line(points=G(12.4, gy, 15.1, gy), width=w * .8, cap="round")
        Line(points=arc(12, 10.4, 6.9, 0, 180), **line)
        Line(points=G(12, 17.3, 12, 20.8), **line)
        Line(points=G(8.4, 20.8, 15.6, 20.8), **line)
    elif name == "camera":
        # Body, the viewfinder's hump, the lens, the lamp.
        Line(rounded_rectangle=(*G(2.6, 19.4), 18.8 * s, 12.2 * s, 2.6 * s),
             width=w)
        Line(points=G(7.8, 7.2, 9.4, 4.6, 14.6, 4.6, 16.2, 7.2), **line)
        Line(circle=(*G(12, 13.3), 3.7 * s), width=w)
        dot(18.0, 10.2, .85)
    elif name == "play":
        Triangle(points=[cx - h * .36, cy + h * .56, cx - h * .36, cy - h * .56,
                         cx + h * .60, cy])
    elif name == "pause":
        bw = size * .13
        for dx in (-h * .28, h * .28):
            Rectangle(pos=(cx + dx - bw / 2, cy - h * .54), size=(bw, h * 1.08))
    elif name in ("bell", "bell_off"):
        base = cy - h * .40
        Line(points=[cx - h * .70, base, cx + h * .70, base], **line)
        Line(bezier=[cx - h * .56, base, cx - h * .42, cy + h * .30,
                     cx - h * .50, cy + h * .98, cx, cy + h * .64], **line)
        Line(bezier=[cx + h * .56, base, cx + h * .42, cy + h * .30,
                     cx + h * .50, cy + h * .98, cx, cy + h * .64], **line)
        r = size * .08
        Ellipse(pos=(cx - r, base - h * .26 - r), size=(2 * r, 2 * r))
        if name == "bell_off":
            Line(points=[cx - h * .76, cy - h * .76, cx + h * .76,
                         cy + h * .76], **line)
    elif name == "check":
        Line(points=[cx - h * .58, cy + h * .02, cx - h * .16, cy - h * .40,
                     cx + h * .62, cy + h * .44], **line)
    elif name == "download":
        Line(points=[cx, cy + h * .64, cx, cy - h * .28], **line)
        Line(points=[cx - h * .38, cy + h * .06, cx, cy - h * .30,
                     cx + h * .38, cy + h * .06], **line)
        Line(points=[cx - h * .60, cy - h * .68, cx + h * .60, cy - h * .68],
             **line)
    elif name == "retry":
        rad = h * .56
        Line(circle=(cx, cy, rad, 60, 330), width=w)
        ang = math.radians(60)
        ex, ey = cx + rad * math.sin(ang), cy + rad * math.cos(ang)
        tx, ty = -math.cos(ang), math.sin(ang)
        nx, ny = math.sin(ang), math.cos(ang)
        k = h * .58
        Triangle(points=[ex + tx * k, ey + ty * k,
                         ex + nx * k * .6, ey + ny * k * .6,
                         ex - nx * k * .6, ey - ny * k * .6])
    elif name == "info":
        Line(circle=(cx, cy, h * .80), width=w)
        Line(points=[cx, cy - h * .40, cx, cy - h * .02], **line)
        r = size * .07
        Ellipse(pos=(cx - r, cy + h * .36 - r), size=(2 * r, 2 * r))
    elif name == "smile":
        Line(circle=(*G(12, 12), 9.5 * s), width=w)
        for ex in (8.8, 15.2):
            px, py = G(ex, 9.5)
            Ellipse(pos=(px - 1.15 * s, py - 1.45 * s),
                    size=(2.3 * s, 2.9 * s))
        Line(points=arc(12, 12.2, 5.2, 26, 154), **line)
    elif name == "keyboard":
        Line(rounded_rectangle=(*G(2.4, 18.4), 19.2 * s, 12.8 * s, 2.4 * s),
             width=w)
        for y, xs in ((9.4, (6.2, 9.4, 12.6, 15.8)),
                      (12.4, (7.8, 11.0, 14.2, 17.4))):
            for x in xs:
                dot(x, y, 1.0)
        dot(17.8, 9.4, 1.0)
        Line(points=G(8.6, 15.5, 15.4, 15.5), **line)
    elif name == "down":
        Line(points=G(6.8, 9.4, 12, 14.6, 17.2, 9.4), **line)
    elif name in ("person", "person_add"):
        hx = cx - (h * .18 if name == "person_add" else 0)
        Line(circle=(hx, cy + h * .30, h * .30), width=w)
        Line(bezier=[hx - h * .62, cy - h * .72, hx - h * .56, cy - h * .12,
                     hx + h * .56, cy - h * .12, hx + h * .62, cy - h * .72],
             **line)
        if name == "person_add":
            Line(points=[cx + h * .54, cy + h * .30, cx + h * .54,
                         cy - h * .10], **line)
            Line(points=[cx + h * .34, cy + h * .10, cx + h * .74,
                         cy + h * .10], **line)
    elif name == "group":
        for dx, s in ((-h * .30, .9), (h * .34, 1.0)):
            px = cx + dx
            Line(circle=(px, cy + h * .30 * s, h * .25 * s), width=w)
            Line(bezier=[px - h * .50 * s, cy - h * .70,
                         px - h * .46 * s, cy - h * .16,
                         px + h * .46 * s, cy - h * .16,
                         px + h * .50 * s, cy - h * .70], **line)
    elif name == "bookmark":
        Line(points=[cx - h * .46, cy + h * .76, cx - h * .46, cy - h * .74,
                     cx, cy - h * .36, cx + h * .46, cy - h * .74,
                     cx + h * .46, cy + h * .76], close=True, **line)
    elif name == "sliders":                    # settings
        for i, (y, k) in enumerate(((cy + h * .52, -.30), (cy, .34),
                                    (cy - h * .52, -.06))):
            Line(points=[cx - h * .78, y, cx + h * .78, y], **line)
            r = size * .12
            Ellipse(pos=(cx + k * h * 1.4 - r, y - r), size=(2 * r, 2 * r))
    elif name == "key":
        Line(circle=(cx - h * .38, cy + h * .02, h * .30), width=w)
        Line(points=[cx - h * .08, cy + h * .02, cx + h * .76, cy + h * .02],
             **line)
        Line(points=[cx + h * .50, cy + h * .02, cx + h * .50, cy - h * .28],
             **line)
        Line(points=[cx + h * .72, cy + h * .02, cx + h * .72, cy - h * .22],
             **line)
    elif name == "leave":
        Line(points=[cx + h * .10, cy + h * .76, cx - h * .70, cy + h * .76,
                     cx - h * .70, cy - h * .76, cx + h * .10, cy - h * .76],
             **line)
        Line(points=[cx - h * .18, cy, cx + h * .78, cy], **line)
        Line(points=[cx + h * .46, cy + h * .32, cx + h * .78, cy,
                     cx + h * .46, cy - h * .32], **line)
    elif name == "attach":
        # A paperclip: one wire turned back on itself three times, laid
        # along a diagonal. Built in the clip's own frame (u along it, v
        # across: legs at v = +-1.2 and +-3.6) and turned onto the grid.
        k = math.sqrt(.5)

        def uv(u, v):
            return (12.0 + (u + v) * k, 12.4 + (v - u) * k)

        def turn(u0, v0, r, a0, a1):
            pts = []
            for i in range(21):
                a = math.radians(a0 + (a1 - a0) * i / 20)
                pts += uv(u0 + r * math.cos(a), v0 + r * math.sin(a))
            return pts

        wire = list(uv(2.4, -1.2)) + list(uv(-3.4, -1.2))
        wire += turn(-3.4, 0.0, 1.2, 270, 90)
        wire += list(uv(5.6, 1.2))
        wire += turn(5.6, -1.2, 2.4, 90, -90)
        wire += list(uv(-5.6, -3.6))
        wire += turn(-5.6, 0.0, 3.6, 270, 90)
        wire += list(uv(3.6, 3.6))
        Line(points=G(*wire), **line)



class IconButton(ButtonBehavior, Widget):
    """A tappable line icon, at least 48dp to touch.

    `bg` gives it a filled disc (send, the compose button); without one it
    is a bare icon with a soft circular highlight while pressed. `label` is
    what a screen reader would say — Kivy cannot speak it yet, but it keeps
    every control named for when it can, and the tests use it.
    """

    #: Scales the icon alone (not the disc): `morph` swaps one icon for
    #: another by shrinking the old away and springing the new one in.
    icon_scale = NumericProperty(1.0)

    def __init__(self, icon: str, tint: Optional[str] = None,
                 bg: Optional[str] = None, icon_size=None, press=None,
                 label: str = "", **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("size", (TOUCH, TOUCH))
        super().__init__(**kw)
        self._icon, self._tint, self._bg_hex = icon, tint or T.text_2, bg
        self._icon_size = icon_size
        self._morph_to: Optional[str] = None
        self.label = label or icon
        self._press_hex = press
        self.bind(pos=self._redraw, size=self._redraw, state=self._redraw,
                  disabled=self._redraw, icon_scale=self._redraw)
        self._redraw()

    @property
    def icon(self) -> str:
        return self._icon

    def set_icon(self, icon: str, tint: Optional[str] = None) -> None:
        self._icon = icon
        if tint is not None:
            self._tint = tint
        self._redraw()

    def show_icon(self, icon: str) -> None:
        """Change the icon at once, ending any morph under way."""
        Animation.cancel_all(self, "icon_scale")
        self._morph_to = None
        self.icon_scale = 1.0
        self.set_icon(icon)

    def morph(self, icon: str) -> None:
        """Change the icon with a short shrink-and-spring. Asked again for
        the same icon while one is under way (a keystroke each), it lets
        that one finish rather than starting over."""
        if icon == (self._morph_to or self._icon):
            return
        if not motion(1):
            self.show_icon(icon)
            return
        Animation.cancel_all(self, "icon_scale")
        self._morph_to = icon
        out = Animation(icon_scale=0.3, d=0.07, t="in_quad")
        out.bind(on_complete=lambda *_a: self.set_icon(icon))
        back = Animation(icon_scale=1.0, d=0.16, t="out_back")
        back.bind(on_complete=lambda *_a: setattr(self, "_morph_to", None))
        (out + back).start(self)

    def _redraw(self, *_a):
        self.canvas.clear()
        side = min(self.width, self.height)
        disc = min(side, dp(44))
        with self.canvas:
            if self._bg_hex is not None:
                Color(*C(T.accent_press if self.state == "down" and
                         self._bg_hex == T.accent else self._bg_hex))
                SmoothEllipse(pos=((self.x + self.width / 2) - disc / 2,
                                   (self.y + self.height / 2) - disc / 2),
                              size=(disc, disc))
            elif self.state == "down":
                Color(*C(self._press_hex or T.press))
                SmoothEllipse(pos=((self.x + self.width / 2) - disc / 2,
                                   (self.y + self.height / 2) - disc / 2),
                              size=(disc, disc))
            rgba = C(self._tint)
            if self.disabled:
                rgba = rgba[:3] + [rgba[3] * .35]
            if self.icon_scale > 0.05:
                draw_icon(self._icon, (self.x + self.width / 2),
                          (self.y + self.height / 2),
                          self._icon_size or dp(22), rgba,
                          scale=self.icon_scale)


class TickMarks(Widget):
    """Delivery ticks, drawn (no font has a dependable check mark).

    0 grey = still on this phone; 1 = on the relay; 2 = on their phone;
    3 = read. In a group, the count is the slowest member's. Ticks overlap
    so two and three read as one mark.
    """

    def __init__(self, count: int = 0, tint: Optional[str] = None,
                 mark: float = None, **kw):
        kw.setdefault("size_hint", (None, None))
        self._mark = mark or dp(9.5)
        self._step = self._mark * 0.52
        self._count, self._tint = max(0, min(int(count), 3)), tint or T.tick
        kw.setdefault("size", (self._width_for(self._count), self._mark))
        super().__init__(**kw)
        self.bind(pos=self._redraw, size=self._redraw)
        self._redraw()

    def _width_for(self, count: int) -> float:
        if count <= 0:
            return 0.0
        return self._mark * 0.92 + self._step * (count - 1)

    def set_state(self, count: int, tint: str) -> None:
        count = max(0, min(int(count), 3))
        if count == self._count and tint == self._tint:
            return
        self._count, self._tint = count, tint
        self.width = self._width_for(count)
        self._redraw()

    def _paint_marks(self, x0: float, y0: float, k: float) -> None:
        h, w = self._mark * k, max(dp(.7), self._mark * .08) * k
        for i in range(self._count):
            x = x0 + i * self._step * k
            Line(points=[x, y0 + h * 0.45, x + h * 0.34, y0 + h * 0.06,
                         x + h * 0.92, y0 + h * 0.82],
                 width=w, cap="round", joint="round")

    def _redraw(self, *_a):
        self.canvas.clear()
        if self._count <= 0:
            return
        pad = int(math.ceil(dp(2)))
        tw = int(math.ceil(self._width_for(self._count))) + 2 * pad
        th = int(math.ceil(self._mark)) + 2 * pad
        tex = offscreen_texture(
            ("ticks", self._count, round(self._mark, 2)), tw, th,
            lambda k: self._paint_marks(pad * k, pad * k, k))
        with self.canvas:
            Color(*C(self._tint))
            if tex is None:
                self._paint_marks(self.x, self.y, 1)
            else:
                Rectangle(texture=tex, size=(tw, th),
                          pos=(round(self.x - pad), round(self.y - pad)))


class Avatar(Widget):
    """A coloured disc with an initial.

    A group's disc carries an inner ring; notes to self are a gold disc with
    a bookmark.
    """

    def __init__(self, seed: str, name: str, diameter=dp(48),
                 kind: str = "dm", **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("size", (diameter, diameter))
        super().__init__(**kw)
        self.kind, self._d = kind, diameter
        self._fill = T.accent if kind == "self" else avatar_color(seed)
        self._initial = None
        if kind != "self":
            self._initial = Label(text=initial_of(name), bold=False,
                                  font_size=diameter * 0.48,
                                  color=C("#FFFFF8"), size_hint=(None, None))
            self.add_widget(self._initial)
        self.bind(pos=self._redraw, size=self._redraw)
        self._redraw()

    def _redraw(self, *_a):
        self.canvas.before.clear()
        d = min(self.width, self.height, self._d)
        x, y = (self.x + self.width / 2) - d / 2, (self.y + self.height / 2) - d / 2
        with self.canvas.before:
            Color(*C(self._fill))
            SmoothEllipse(pos=(x, y), size=(d, d))
            if self.kind == "group":
                Color(1, 1, 1, .55)
                inset = max(dp(2.5), d * .07)
                SmoothLine(circle=((self.x + self.width / 2),
                                   (self.y + self.height / 2), d / 2 - inset),
                           width=max(1.0, dp(.9)))
            if self.kind == "self":
                draw_icon("bookmark", (self.x + self.width / 2), (self.y + self.height / 2), d * .46,
                          C(T.on_accent), width=max(dp(.8), d * .022))
        if self._initial is not None:
            self._initial.size = (d, d)
            # Garamond's capitals sit high in their line box; nudge them to
            # the optical centre of the disc.
            self._initial.center = ((self.x + self.width / 2), (self.y + self.height / 2) - d * .03)


class AvatarButton(ButtonBehavior, Avatar):
    """A tappable avatar: the disc as drawn, inside a full 48dp target
    (ButtonBehavior takes keywords only)."""

    def __init__(self, seed: str, name: str, diameter=dp(48),
                 kind: str = "dm", **kw):
        kw.setdefault("size", (max(TOUCH, diameter), max(TOUCH, diameter)))
        super().__init__(seed=seed, name=name, diameter=diameter, kind=kind,
                         **kw)


class Field(BoxLayout):
    """A text field: an optional capitals label, the text, and a hairline
    that turns gold while you type."""

    def __init__(self, hint: str = "", label: str = "", password=False,
                 multiline=False, on_enter=None, **kw):
        kw.setdefault("orientation", "vertical")
        kw.setdefault("size_hint_y", None)
        super().__init__(**kw)
        self.caption = None
        if label:
            self.caption = CapsLabel(label, size=11, color=T.text_3)
            self.add_widget(self.caption)
        self.input = TextInput(
            hint_text=hint, multiline=multiline, password=password,
            background_normal="", background_active="",
            background_color=(0, 0, 0, 0), foreground_color=C(T.text),
            hint_text_color=C(T.text_3), cursor_color=C(T.accent),
            selection_color=C(T.accent)[:3] + [.3], font_size=fs(17),
            write_tab=False, padding=[0, dp(10), 0, dp(8)],
            size_hint_y=None, height=round(fs(17) * 1.35 + dp(18)))
        if on_enter is not None:
            self.input.bind(on_text_validate=lambda _w: on_enter())
        self.add_widget(self.input)
        self.height = self.input.height + (self.caption.height
                                           if self.caption else 0)
        with self.canvas.after:
            self._rule_col = Color(*C(T.rule_strong))
            self._rule = Rectangle()
        self.bind(pos=self._sync, size=self._sync)
        self.input.bind(focus=self._on_focus)

    def _on_focus(self, _w, focused):
        self._rule_col.rgba = C(T.accent if focused else T.rule_strong)
        self._sync()

    def _sync(self, *_a):
        thick = dp(2) if self.input.focus else dp(1)
        self._rule.pos = (self.x, self.y)
        self._rule.size = (self.width, thick)

    @property
    def text(self) -> str:
        return self.input.text

    @text.setter
    def text(self, value: str) -> None:
        self.input.text = value


CozyInput = Field                       # pre-9.1 name


class ToggleSwitch(ButtonBehavior, Widget):
    """An on/off switch: a gold track when on, a hairline one when off."""

    def __init__(self, active: bool = False,
                 on_change: Optional[Callable[[bool], None]] = None, **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("size", (dp(50), dp(30)))
        super().__init__(**kw)
        self.active, self._on_change = bool(active), on_change
        self.bind(pos=self._paint, size=self._paint,
                  on_release=lambda _w: self.flip())
        self._paint()

    def flip(self) -> None:
        self.set_active(not self.active)
        if self._on_change:
            self._on_change(self.active)

    def set_active(self, active: bool) -> None:
        self.active = bool(active)
        self._paint()

    def _paint(self, *_a) -> None:
        self.canvas.clear()
        h = self.height
        with self.canvas:
            if self.active:
                Color(*C(T.accent))
                SmoothRoundedRectangle(pos=self.pos, size=self.size,
                                       radius=[h / 2])
            else:
                Color(*C(T.rule_strong))
                SmoothLine(rounded_rectangle=(self.x + .5, self.y + .5,
                                              self.width - 1, h - 1,
                                              h / 2 - .5),
                           width=max(1.0, dp(1.1)))
            knob = h - dp(8)
            x = self.right - knob - dp(4) if self.active else self.x + dp(4)
            Color(*C(T.on_accent if self.active else T.text_3))
            SmoothEllipse(pos=(x, self.y + dp(4)), size=(knob, knob))


Toggle = ToggleSwitch


class Segmented(BoxLayout):
    """A choice of a few, in capitals: the chosen one is underlined in
    gold, the others recede. Every segment is a full 48dp target."""

    def __init__(self, options: list, value: str,
                 on_change: Optional[Callable[[str], None]] = None, **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", TOUCH)
        super().__init__(**kw)
        self.value, self._on_change = value, on_change
        self.segments = {}
        for key, text in options:
            seg = _Segment(key, text, self)
            self.segments[key] = seg
            self.add_widget(seg)
        hairline(self, "bottom")
        self._paint()

    def choose(self, key: str) -> None:
        if key == self.value:
            return
        self.value = key
        self._paint()
        if self._on_change:
            self._on_change(key)

    def _paint(self) -> None:
        for key, seg in self.segments.items():
            seg.set_selected(key == self.value)


class _Segment(ButtonBehavior, Widget):
    def __init__(self, key: str, text: str, owner: "Segmented", **kw):
        super().__init__(**kw)
        self.key = key
        self.caps = CapsLabel(text, size=12, tracking=0.14, halign="center",
                              size_hint=(None, None))
        self.add_widget(self.caps)
        with self.canvas.before:
            self._press = Color(0, 0, 0, 0)
            self._ground = Rectangle()
            self._mark = Color(*C(T.accent))
            self._bar = Rectangle()
        self.bind(pos=self._sync, size=self._sync,
                  state=lambda _w, s: setattr(
                      self._press, "rgba", C(T.press) if s == "down"
                      else (0, 0, 0, 0)),
                  on_release=lambda _w: owner.choose(key))

    def set_selected(self, on: bool) -> None:
        self.selected = on
        self.caps.set_color(T.text if on else T.text_3)
        self._mark.a = 1 if on else 0

    def _sync(self, *_a):
        self._ground.pos, self._ground.size = self.pos, self.size
        self._bar.pos = (self.x + dp(8), self.y)
        self._bar.size = (max(0, self.width - dp(16)), dp(2))
        self.caps.size = (self.width - dp(8), self.caps.height)
        self.caps.center = (self.x + self.width / 2, self.y + self.height / 2)


class SectionHead(BoxLayout):
    """A capitals heading over a group of rows."""

    def __init__(self, text: str, **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(44))
        kw.setdefault("padding", [dp(20), dp(18), dp(20), dp(6)])
        super().__init__(**kw)
        self.caps = CapsLabel(text, size=11, color=T.accent, tracking=0.16)
        self.add_widget(self.caps)


class ListRow(ButtonBehavior, BoxLayout):
    """Icon · title over an optional hint · a control or a chevron.

    The whole row is the tap target; hints wrap rather than being cut off.
    """

    def __init__(self, icon: Optional[str], title: str, hint: str = "",
                 control: Optional[Widget] = None,
                 on_tap: Optional[Callable] = None, danger: bool = False,
                 chevron: bool = False, title_color: Optional[str] = None,
                 **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("padding", [dp(20), dp(10), dp(16), dp(10)])
        kw.setdefault("spacing", dp(16))
        super().__init__(**kw)
        tint = T.alert if danger else T.text_2
        if icon:
            glyph = Widget(size_hint_x=None, width=dp(24))
            glyph.bind(pos=lambda w, *_a: self._glyph(w, icon, tint),
                       size=lambda w, *_a: self._glyph(w, icon, tint))
            self.add_widget(glyph)
        texts = BoxLayout(orientation="vertical", size_hint_y=None,
                          spacing=dp(1))
        texts.bind(minimum_height=texts.setter("height"))
        self.title = InfoLabel(title, color=title_color or
                               (T.alert if danger else T.text),
                               font_size=fs(17))
        texts.add_widget(self.title)
        self.hint = None
        if hint:
            self.hint = InfoLabel(hint, color=T.text_3, font_size=fs(14.5))
            texts.add_widget(self.hint)
        holder = AnchorLayout(anchor_y="center")
        holder.add_widget(texts)
        self.add_widget(holder)
        self._texts = texts
        if control is None and chevron:
            control = Widget(size_hint=(None, None), size=(dp(16), dp(24)))
            control.bind(pos=lambda w, *_a: self._glyph(w, "chevron",
                                                        T.text_3, dp(16)),
                         size=lambda w, *_a: self._glyph(w, "chevron",
                                                         T.text_3, dp(16)))
        if control is not None:
            box = AnchorLayout(size_hint_x=None, width=control.width,
                               anchor_x="right")
            box.add_widget(control)
            self.add_widget(box)
        texts.bind(height=self._fit)
        self._fit()
        with self.canvas.before:
            self._bg = Color(0, 0, 0, 0)
            self._rect = Rectangle()
        self.bind(pos=self._sync, size=self._sync,
                  state=lambda _w, st: setattr(
                      self._bg, "rgba", C(T.press) if st == "down"
                      else (0, 0, 0, 0)))
        if on_tap is None and isinstance(control, ToggleSwitch):
            on_tap = control.flip          # the whole row is the switch
        if on_tap is not None:
            self.bind(on_release=lambda _w: on_tap())

    def _fit(self, *_a):
        self.height = max(dp(56), self._texts.height + dp(22))

    def _sync(self, *_a):
        self._rect.pos, self._rect.size = self.pos, self.size

    @staticmethod
    def _glyph(widget, icon: str, tint: str, size=None) -> None:
        widget.canvas.clear()
        with widget.canvas:
            draw_icon(icon, (widget.x + widget.width / 2), (widget.y + widget.height / 2), size or dp(22),
                      C(tint))


SettingRow = ListRow                    # pre-9.1 name


def rows_card(rows: list, inset: float = dp(60)) -> BoxLayout:
    """Rows stacked with hairlines between them (none above or below)."""
    card = BoxLayout(orientation="vertical", size_hint_y=None)
    card.bind(minimum_height=card.setter("height"))
    for i, row in enumerate(rows):
        if i:
            card.add_widget(Rule(inset=inset))
        card.add_widget(row)
    return card


class TappableBox(ButtonBehavior, BoxLayout):
    """A tappable stack of widgets (a chat's title -> its info)."""

    def __init__(self, on_tap=None, **kw):
        kw.setdefault("orientation", "vertical")
        super().__init__(**kw)
        if on_tap is not None:
            self.bind(on_release=lambda _w: on_tap())


class UnreadBadge(Label):
    """The unread count, in red. Muted chats show it in grey."""

    def __init__(self, count: int, muted: bool = False, **kw):
        text = str(count) if count < 1000 else "999+"
        super().__init__(text=text, font_size=fs(13), bold=True,
                         color=C(T.bg if muted else T.on_alert),
                         size_hint=(None, None),
                         height=max(dp(22), round(fs(13) * 1.6)), **kw)
        with self.canvas.before:
            Color(*C(T.text_3 if muted else T.alert))
            self._pill = SmoothRoundedRectangle(radius=[self.height / 2])
        self.bind(pos=self._sync, size=self._sync,
                  texture_size=lambda w, v: setattr(
                      w, "width", max(self.height, v[0] + dp(12))))
        self.texture_update()

    def _sync(self, *_a):
        self._pill.pos, self._pill.size = self.pos, self.size
        self._pill.radius = [self.height / 2]


class Concealable:
    """A part that comes and goes: the emoji panel, the reply preview, the
    reaction strip, the jump-to-latest button.

    Kivy lets a DISABLED widget swallow every touch that lands on it — at
    opacity 0, and even when it overhangs a parent that is 0 high — so a
    hidden part has to step aside explicitly. Without this the closed emoji
    panel sat over the composer and ate every click on the message field.
    """

    concealed = False

    def conceal(self, hidden: bool) -> None:
        self.concealed = bool(hidden)
        self.disabled = bool(hidden)

    def on_touch_down(self, touch):
        if self.concealed:
            return False
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if self.concealed and touch.grab_current is not self:
            return False
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if self.concealed and touch.grab_current is not self:
            return False
        return super().on_touch_up(touch)


class ConcealableBox(Concealable, BoxLayout):
    pass


class Sheet(ModalView):
    """Every dialog is a sheet that rises from the bottom edge, where a
    thumb can reach it: a grab handle, a serif title, its contents (they
    scroll when tall), and a dimmed page behind. Tap outside, or press
    back, to close it (unless it must be answered)."""

    rise = NumericProperty(0.0)
    MAX_WIDTH = dp(600)

    def __init__(self, title: str = "", dismissable: bool = True,
                 subtitle: str = "", **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("auto_dismiss", dismissable)
        super().__init__(background_color=(0, 0, 0, 0),
                         overlay_color=T.scrim, **kw)
        self.dismissable = dismissable
        self.card = BoxLayout(orientation="vertical",
                              padding=[dp(22), dp(8), dp(22), dp(22)],
                              spacing=dp(12))
        paint_round(self.card, T.surface if T.dark else T.bg,
                    corners=[dp(16), dp(16), 0, 0])
        if not T.dark:
            with self.card.canvas.after:
                Color(*C(T.rule))
                self._edge = SmoothLine(width=1.0)
            self.card.bind(pos=self._sync_edge, size=self._sync_edge)
        handle = AnchorLayout(size_hint_y=None, height=dp(14))
        grip = Widget(size_hint=(None, None), size=(dp(36), dp(4)))
        paint_round(grip, T.rule_strong, radius=dp(2))
        handle.add_widget(grip)
        self.card.add_widget(handle)
        if title:
            self.title_label = InfoLabel(title, font_size=fs(22))
            self.card.add_widget(self.title_label)
        if subtitle:
            self.card.add_widget(InfoLabel(subtitle, color=T.text_3,
                                           italic=True, font_size=fs(15)))
        self.body = BoxLayout(orientation="vertical", size_hint_y=None,
                              spacing=dp(12))
        self.body.bind(minimum_height=self.body.setter("height"))
        self.scroller = ScrollView(size_hint_y=None, bar_width=dp(3),
                                   do_scroll_x=False)
        self.scroller.add_widget(self.body)
        self.card.add_widget(self.scroller)
        self.add_widget(self.card)
        self.body.bind(height=self._fit)
        self.card.bind(minimum_height=self._fit)
        Window.bind(size=self._fit)
        self.bind(on_dismiss=lambda *_a: Window.unbind(size=self._fit))
        self._fit()

    def _sync_edge(self, *_a):
        # The outline runs on below the bottom edge, so only its top and
        # sides show: a hairline lip on the light theme's white page.
        c = self.card
        self._edge.rounded_rectangle = (c.x + .5, c.y - dp(20), c.width - 1,
                                        c.height + dp(20) - .5, dp(16))

    def _fit(self, *_a):
        self.width = min(Window.width, self.MAX_WIDTH)
        chrome = sum(w.height for w in self.card.children
                     if w is not self.scroller)
        chrome += self.card.padding[1] + self.card.padding[3]
        chrome += self.card.spacing * max(0, len(self.card.children) - 1)
        room = Window.height * 0.9 - chrome
        self.scroller.height = max(dp(40), min(self.body.height, room))
        self.height = chrome + self.scroller.height
        self._align_center()

    def _align_center(self, *_a):
        self.x = (Window.width - self.width) / 2
        self.y = -self.height * (1 - self.rise)

    def on_rise(self, *_a):
        self._align_center()

    def open(self, *a, **kw):
        self.rise = 0.0 if motion(1) else 1.0
        super().open(*a, **kw)
        self._align_center()
        if motion(1):
            Animation(rise=1.0, d=0.22, t="out_cubic").start(self)

    def add(self, widget):
        self.body.add_widget(widget)
        return widget

    def status_label(self) -> InfoLabel:
        label = InfoLabel("", color=T.text_3, font_size=fs(15),
                          italic=True)
        self.add(label)
        return label

    def set_status(self, label: InfoLabel, text: str,
                   color: Optional[str] = None) -> None:
        label.color = C(retint(color) if color else T.text_3)
        label.text = text


CozyModal = Sheet                       # pre-9.1 name


class MenuSheet(Sheet):
    """A short list of actions, one per full-width row (the ⋮ menu)."""

    def __init__(self, items: list, title: str = "", **kw):
        super().__init__(title, **kw)
        self.card.padding = [0, dp(8), 0, dp(14)]
        if title:
            self.title_label.padding = [dp(22), 0]
        rows = []
        for item in items:
            icon, text, action = item[:3]
            danger = len(item) > 3 and item[3]
            rows.append(ListRow(icon, text, danger=danger,
                                on_tap=lambda a=action: self._run(a)))
        self.add(rows_card(rows))

    def _run(self, action: Callable) -> None:
        self.dismiss()
        Clock.schedule_once(lambda _dt: action(), 0.05)


# =============================================================================
#  Sheets (dialogs)
# =============================================================================
def hint(text: str) -> InfoLabel:
    """A quiet explanatory paragraph."""
    return InfoLabel(text, color=T.text_3, font_size=fs(15))


class RegisterModal(Sheet):
    """Pick a username for the ACTIVE identity.

    Registration sends only public values (username + public key + ownership
    signature); the relay recomputes the address and enforces the 'Fec'
    proof-of-work. It is also the recovery path when the relay no longer
    knows this address (its database was reset), and it handles the three
    answers distinctly: the name is taken (ask again), this key already has
    a name (offer to adopt it), or success.
    """

    def __init__(self, app: "FextApp", reason: str = "") -> None:
        super().__init__("Choose your name", dismissable=False,
                         subtitle=f"for the identity "
                                  f"{short_addr(app.keys.address, 16)}")
        self.app = app
        self._suggested: Optional[str] = None
        if reason:
            self.add(InfoLabel(reason, color=T.accent, font_size=fs(15)))
        self.entry = self.add(Field(hint="username", label="Username",
                                    on_enter=self._submit))
        self.add(hint("3–32 letters, digits or underscores. Others find "
                      "you by it."))
        self.status = self.status_label()
        self.button = self.add(Btn("Register", on_release=lambda
                                   _w: self._submit()))
        # Shown only when the relay says this key already has a name.
        self.adopt_button = Btn("", kind="quiet",
                                on_release=lambda _w: self._adopt())

    def _submit(self) -> None:
        username = self.entry.text.strip()
        if not valid_username(username):
            self.set_status(self.status, "That name doesn't fit yet — 3–32 "
                            "letters, digits or _", T.alert)
            return
        self.button.disabled = True
        self.set_status(self.status, "Registering…")
        threading.Thread(target=self._worker, args=(username,),
                         daemon=True).start()

    def _worker(self, username: str) -> None:
        try:
            self.app.api.register(username)
        except ApiClient.ApiError as exc:
            if exc.code == "username_taken":
                run_on_ui(self._fail,
                          f"‘{username}’ is already taken — try another.")
            elif exc.code == "address_registered":
                run_on_ui(self._offer_adopt,
                          exc.detail.get("username") or "")
            else:
                run_on_ui(self._fail, exc.message)
        except requests.RequestException as exc:
            run_on_ui(self._fail, describe_network_error(exc))
        else:
            run_on_ui(self._succeed, username)

    def _offer_adopt(self, existing: str) -> None:
        self.button.disabled = False
        if not existing:
            self.set_status(self.status, "This identity is already "
                            "registered on the relay.", T.alert)
            return
        self._suggested = existing
        self.set_status(self.status, f"This identity is already registered "
                        f"as ‘{existing}’.", T.accent)
        self.adopt_button.text = f"Continue as {existing}"
        if self.adopt_button.parent is None:
            self.add(self.adopt_button)

    def _adopt(self) -> None:
        if self._suggested:
            self._succeed(self._suggested)

    def _fail(self, message: str) -> None:
        self.set_status(self.status, message, T.alert)
        self.button.disabled = False

    def _succeed(self, username: str) -> None:
        self.app.keys.set_username(username)
        self.dismiss()
        self.app.on_registered()


class AddContactModal(Sheet):
    """Find someone by username OR by their Fec… address."""

    def __init__(self, app: "FextApp") -> None:
        super().__init__("Add a contact")
        self.app = app
        self.entry = self.add(Field(hint="username or Fec… address",
                                    label="Who", on_enter=self._submit))
        self.status = self.status_label()
        self.button = self.add(Btn("Look up",
                                   on_release=lambda _w: self._submit()))

    def _submit(self) -> None:
        query = self.entry.text.strip()
        if not query:
            return
        self.button.disabled = True
        self.set_status(self.status, "Searching…")
        threading.Thread(target=self._worker, args=(query,),
                         daemon=True).start()

    def _worker(self, query: str) -> None:
        try:
            if valid_address(query):
                user = self.app.api.lookup(address=query)
            elif valid_username(query):
                user = self.app.api.lookup(username=query)
            else:
                run_on_ui(self._fail, "That is neither a username nor a "
                          "Fec… address.")
                return
            if user["address"] == self.app.keys.address:
                run_on_ui(self._fail, "That's you — your notes to self are "
                          "at the top of the list.")
                return
            self.app.store.upsert_contact(user["address"], user["username"],
                                          user["public_key"])
        except ApiClient.ApiError as exc:
            run_on_ui(self._fail,
                      "No one by that name." if exc.status == 404
                      else str(exc))
        except requests.RequestException as exc:
            run_on_ui(self._fail, describe_network_error(exc))
        else:
            run_on_ui(self._succeed, user["address"])

    def _fail(self, message: str) -> None:
        self.set_status(self.status, message, T.alert)
        self.button.disabled = False

    def _succeed(self, address: str) -> None:
        self.dismiss()
        self.app.on_contact_added(address)


class NewIdentityModal(Sheet):
    """Create an identity: MINE a fresh key until its address starts with
    'Fec' (a live attempt counter), or IMPORT one (hex or WIF). Imported
    keys whose address misses the proof-of-work are refused here, since the
    relay would refuse them anyway."""

    def __init__(self, app: "FextApp", first_run: bool = False) -> None:
        super().__init__(f"Welcome to {APP_NAME}" if first_run else
                         "A new identity", dismissable=not first_run)
        self.app = app
        self.first_run = first_run
        self._mining = False
        self._cancel_mining = False
        self.add(hint(
            "An identity is a secp256k1 key, the kind Bitcoin uses. The "
            "network only admits addresses that begin “Fec”, so a new key "
            "is mined until one does — a few seconds."))
        self.label_entry = self.add(Field(hint="e.g. Personal",
                                          label="Label, on this device only"))
        self.mine_button = self.add(Btn(
            "Mine a new identity", on_release=lambda _w: self._start_mining()))
        self.progress = self.add(InfoLabel("", color=T.accent,
                                           font_size=fs(15), halign="center"))
        self.add(CapsLabel("or import a key", size=11, halign="center"))
        self.key_entry = self.add(Field(
            hint="64 hex characters, or WIF (K… L… 5…)", password=True,
            label="Private key"))
        self.import_button = self.add(Btn(
            "Import key", kind="quiet",
            on_release=lambda _w: self._import_key()))
        self.status = self.status_label()
        if not first_run:
            self.add(Btn("Cancel", kind="text",
                         on_release=lambda _w: self._close()))
        self.bind(on_dismiss=lambda *_a: setattr(self, "_cancel_mining",
                                                 True))

    def _start_mining(self) -> None:
        if self._mining:
            return
        self._mining = True
        self._cancel_mining = False
        self.mine_button.disabled = True
        self.import_button.disabled = True
        self.set_status(self.status, "")
        self.progress.text = "Mining… 0 attempts"
        threading.Thread(target=self._mine_worker, daemon=True).start()

    def _mine_worker(self) -> None:
        started = time.time()
        last_ui = [0.0]

        def progress(attempts: int) -> None:
            now = time.time()
            if now - last_ui[0] < 0.15:
                return
            last_ui[0] = now
            rate = attempts / max(now - started, 0.001)
            run_on_ui(setattr, self.progress, "text",
                      f"Mining… {attempts:,} attempts ({rate:,.0f} a second)")

        result = mine_identity(should_stop=lambda: self._cancel_mining,
                               progress=progress)
        if result is None:
            return
        priv, _pub, address, attempts = result
        run_on_ui(self._finish, priv, address, attempts)

    def _finish(self, priv: int, address: str, attempts: int) -> None:
        self.progress.text = f"Found {address}\nafter {attempts:,} attempts"
        self._save(priv)

    def _import_key(self) -> None:
        text = self.key_entry.text.strip()
        if not text:
            return
        try:
            priv = parse_private_key(text)
        except ValueError as exc:
            self.set_status(self.status, f"Cannot read that key: {exc}",
                            T.alert)
            return
        self._save(priv)

    def _save(self, priv: int) -> None:
        label = self.label_entry.text.strip() or \
            f"Identity {len(self.app.keys.identities) + 1}"
        try:
            address = self.app.keys.add_identity(priv, label)
        except ValueError as exc:
            self.set_status(self.status, str(exc), T.alert)
            self._mining = False
            self.mine_button.disabled = False
            self.import_button.disabled = False
            return
        self.dismiss()
        self.app.on_identity_created(address)

    def _close(self) -> None:
        self._cancel_mining = True
        self.dismiss()
        self.app.on_identity_dialog_closed()


def _file_chooser(path: str, filters: list, height: float):
    chooser = FileChooserListView(path=path, filters=filters,
                                  size_hint_y=None, height=height)
    return chooser


class ImportChooserModal(Sheet):
    """Pick a .json chat export to import."""

    def __init__(self, on_pick: Callable[[str], None]) -> None:
        super().__init__("Import a chat", subtitle="A .json export")
        self._on_pick = on_pick
        start = EXPORT_DIR if EXPORT_DIR.exists() else DATA_DIR
        self.chooser = self.add(_file_chooser(
            str(start), ["*.json"], min(dp(340), Window.height * .5)))
        self.add(button_row(Btn("Cancel", kind="quiet",
                                on_release=lambda _w: self.dismiss()),
                            Btn("Import", on_release=lambda _w: self._pick())))

    def _pick(self) -> None:
        if self.chooser.selection:
            path = self.chooser.selection[0]
            self.dismiss()
            self._on_pick(path)


class ContactPickRow(ButtonBehavior, BoxLayout):
    """Someone you can choose (forward to, a group's members): their disc,
    their name, and a ring that fills with a tick."""

    def __init__(self, contact: dict, picked: bool = False,
                 on_change: Optional[Callable] = None, kind: str = "dm",
                 name: Optional[str] = None, sub: Optional[str] = None, **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", max(dp(60), line_height(17) +
                                    line_height(14) + dp(16)))
        kw.setdefault("padding", [dp(20), dp(6), dp(20), dp(6)])
        kw.setdefault("spacing", dp(14))
        super().__init__(**kw)
        self.address = contact["address"]
        self.picked = picked
        self._on_change = on_change
        name = name or contact.get("username") or short_addr(self.address, 14)
        self.add_widget(Avatar(self.address, name, diameter=dp(40),
                               kind=kind, pos_hint={"center_y": .5}))
        texts = BoxLayout(orientation="vertical")
        texts.add_widget(single_line(name, fs(17), T.text))
        texts.add_widget(single_line(sub if sub is not None else
                                     short_addr(self.address, 18), fs(14),
                                     T.text_3))
        self.add_widget(texts)
        self.tick = Widget(size_hint_x=None, width=dp(28))
        self.tick.bind(pos=self._paint, size=self._paint)
        self.add_widget(self.tick)
        with self.canvas.before:
            self._bg = Color(0, 0, 0, 0)
            self._rect = Rectangle()
        self.bind(pos=self._sync, size=self._sync,
                  state=lambda _w, st: setattr(
                      self._bg, "rgba", C(T.press) if st == "down"
                      else (0, 0, 0, 0)),
                  on_release=lambda _w: self.toggle())

    def _sync(self, *_a):
        self._rect.pos, self._rect.size = self.pos, self.size

    def toggle(self) -> None:
        self.picked = not self.picked
        self._paint()
        if self._on_change:
            self._on_change(self)

    def _paint(self, *_a) -> None:
        t = self.tick
        t.canvas.clear()
        r = dp(11)
        with t.canvas:
            if self.picked:
                Color(*C(T.accent))
                SmoothEllipse(pos=((t.x + t.width / 2) - r,
                                   (t.y + t.height / 2) - r),
                              size=(2 * r, 2 * r))
                draw_icon("check", (t.x + t.width / 2), (t.y + t.height / 2), dp(13),
                          C(T.on_accent), width=dp(1.0))
            else:
                Color(*C(T.rule_strong))
                SmoothLine(circle=((t.x + t.width / 2), (t.y + t.height / 2),
                                   r - dp(.5)), width=max(1.0, dp(1.1)))


def conversation_title(app, peer: str) -> tuple:
    """(name, kind) for any conversation key — a contact, a group, or
    notes to self."""
    kind = app.conversation_kind(peer)
    if kind == "self":
        return "Notes to self", kind
    if kind == "group":
        group = app.store.group(gid_of(peer)) if app.store else None
        return (group or {}).get("name") or "Group", kind
    contact = app.store.get_contact(peer) if app.store else None
    return (contact or {}).get("username") or short_addr(peer, 14), kind


class ForwardModal(Sheet):
    """Forward to any chat of this identity: notes to self, a group, a
    contact — several at once."""

    def __init__(self, app: "FextApp", local_ids: list,
                 on_done: Optional[Callable] = None) -> None:
        n = len(local_ids)
        super().__init__(f"Forward {n} message{'s' if n != 1 else ''}")
        self.app, self.local_ids, self.on_done = app, local_ids, on_done
        self.card.padding = [0, dp(8), 0, dp(18)]
        self.title_label.padding = [dp(22), 0]
        targets = [{"address": app.keys.address}]
        for group in app.store.groups() if app.store else []:
            if group["state"] == "active":
                targets.append({"address": group_peer(group["gid"])})
        targets += app.store.contacts() if app.store else []
        self.rows = []
        for target in targets:
            peer = target["address"]
            name, kind = conversation_title(app, peer)
            sub = {"self": "Kept on this device",
                   "group": f"{len((app.store.group(gid_of(peer)) or {}).get('members', []))} members"
                   }.get(kind)
            self.rows.append(ContactPickRow(
                {"address": peer}, kind=kind, name=name, sub=sub,
                on_change=lambda _r: self._sync_button()))
        self.add(rows_card(self.rows, inset=dp(74)))
        self.button = Btn("Forward", on_release=lambda _w: self._go(),
                          size_hint_x=None)
        holder = BoxLayout(size_hint_y=None, height=TOUCH,
                           padding=[dp(22), 0])
        self.button.size_hint_x = 1
        holder.add_widget(self.button)
        self.add(holder)
        self._sync_button()

    def _sync_button(self) -> None:
        n = sum(1 for r in self.rows if r.picked)
        self.button.disabled = n == 0
        self.button.text = "Forward" if n <= 1 else f"Forward to {n} chats"

    def _go(self) -> None:
        targets = [r.address for r in self.rows if r.picked]
        if not targets:
            return
        self.dismiss()
        sent = self.app.forward_messages(self.local_ids, targets)
        if self.on_done:
            self.on_done()
        who = (len(targets) == 1 and conversation_title(
            self.app, targets[0])[0]) or f"{len(targets)} chats"
        self.app._set_status(f"Forwarded {sent} message"
                             f"{'s' if sent != 1 else ''} to {who}", T.ok)


class DeleteModal(Sheet):
    """Delete for me, or — only when every message is yours — for
    everyone in the chat."""

    def __init__(self, app: "FextApp", msgs: list,
                 on_done: Optional[Callable] = None) -> None:
        n = len(msgs)
        super().__init__(f"Delete {n} message{'s' if n != 1 else ''}?")
        # NOT self.ids: every Kivy widget already has an `ids` property.
        self.app, self.msg_ids, self.on_done = app, [m["id"] for m in msgs], \
            on_done
        own = all(m["direction"] == "out" and m.get("client_msg_id")
                  for m in msgs)
        peer = msgs[0]["peer"] if msgs else None
        kind = app.conversation_kind(peer) if peer else "dm"
        if own and kind != "self":
            if kind == "group":
                label, note = "Delete for everyone", ""
            else:
                contact = (app.store.get_contact(peer) or {}) if peer else {}
                name = contact.get("username") or "them"
                label = f"Delete for me and {name}"
                note = ("" if contact.get("caps", 0) & LocalStore.CAP_V9 else
                        f"If {name} is still on an older version, their "
                        f"copy stays and they see a note instead.")
            self.add(Btn(label, kind="solid_danger",
                         on_release=lambda _w: self._go(True)))
            if note:
                self.add(hint(note))
        self.add(Btn("Delete for me", kind="danger",
                     on_release=lambda _w: self._go(False)))
        if not own and kind != "self":
            self.add(hint("Only your own messages can be deleted for "
                          "everyone."))
        self.add(Btn("Cancel", kind="text",
                     on_release=lambda _w: self.dismiss()))

    def _go(self, for_everyone: bool) -> None:
        self.dismiss()
        if self.on_done:
            self.on_done()
        self.app.delete_messages(self.msg_ids, for_everyone)


class StarredModal(Sheet):
    """Starred messages of one chat — private bookmarks, never sent."""

    def __init__(self, app: "FextApp", peer: Optional[str]) -> None:
        super().__init__("Starred")
        self.app = app
        rows = app.store.starred(peer) if app.store else []
        screen = app.conversation_screen
        for msg in rows:
            item = TappableBox(size_hint_y=None, padding=[0, dp(6)],
                               spacing=dp(2),
                               on_tap=lambda m=msg: self._open(m))
            item.bind(minimum_height=item.setter("height"))
            who = screen.author_name(screen._author_of(msg))
            day = pretty_day(msg["created_at"])
            clock = pretty_time(msg["created_at"])
            when = clock if day == clock else f"{day}, {clock}"
            item.add_widget(CapsLabel(f"{who} · {when}", size=10.5,
                                      color=T.accent))
            item.add_widget(InfoLabel(single_line_text(msg["body"], 240),
                                      font_size=fs(16)))
            self.add(item)
            self.add(Rule())
        if not rows:
            self.add(hint("Nothing starred yet. Hold a message and choose "
                          "the star to keep it here."))
        self.add(Btn("Done", kind="quiet",
                     on_release=lambda _w: self.dismiss()))

    def _open(self, msg: dict) -> None:
        self.dismiss()
        if msg["peer"] != self.app.current_peer:
            self.app.open_conversation(msg["peer"])
        Clock.schedule_once(lambda _dt: self.app.conversation_screen
                            .jump_to_cid(msg.get("client_msg_id")), 0.3)


class ImageViewer(ModalView):
    """A photo, full screen on black. Tap anywhere to close."""

    def __init__(self, path: str, **kw):
        super().__init__(background_color=(0, 0, 0, 0),
                         overlay_color=(0, 0, 0, 0.94), auto_dismiss=True,
                         size_hint=(1, 1), **kw)
        box = TappableBox(on_tap=self.dismiss)
        box.add_widget(KivyImage(source=path, allow_stretch=True,
                                 keep_ratio=True))
        self.add_widget(box)


class ImageChooserModal(Sheet):
    """Desktop photo picker."""

    def __init__(self, on_pick: Callable[[str], None]) -> None:
        super().__init__("Send a photo")
        self._on_pick = on_pick
        self.chooser = self.add(_file_chooser(
            str(Path.home()), ["*.jpg", "*.jpeg", "*.png", "*.webp", "*.JPG",
                               "*.JPEG", "*.PNG", "*.bmp", "*.gif"],
            min(dp(360), Window.height * 0.5)))
        self.add(button_row(Btn("Cancel", kind="quiet",
                                on_release=lambda _w: self.dismiss()),
                            Btn("Choose", on_release=lambda _w: self._pick())))

    def _pick(self) -> None:
        if self.chooser.selection:
            path = self.chooser.selection[0]
            self.dismiss()
            self._on_pick(path)


class AttachPreviewModal(Sheet):
    """See the photo and add a caption before it is compressed and sent.
    A `temporary` photo (a copy this app made) is deleted once it has been
    sent, or set aside."""

    def __init__(self, app: "FextApp", path: str,
                 temporary: bool = False) -> None:
        super().__init__("Send photo")
        self.app, self.path, self.temporary = app, path, temporary
        self._sent = False
        if temporary:
            self.bind(on_dismiss=lambda *_a: None if self._sent
                      else discard_media_file(self.path))
        self.add(KivyImage(source=path, allow_stretch=True, keep_ratio=True,
                           size_hint_y=None,
                           height=min(dp(300), Window.height * 0.4)))
        self.caption = self.add(Field(hint="Add a caption…",
                                      on_enter=self._send))
        self.add(hint("Sent compressed, with its location and camera "
                      "details removed."))
        self.add(button_row(Btn("Cancel", kind="quiet",
                                on_release=lambda _w: self.dismiss()),
                            Btn("Send", on_release=lambda _w: self._send())))

    def _send(self) -> None:
        self._sent = True
        self.dismiss()
        self.app.send_image(self.path, sanitize_text(self.caption.text),
                            temporary=self.temporary)


class CameraSheet(Sheet):
    """A photo from the computer's camera: what it sees, and a shutter."""

    def __init__(self, on_photo: Callable[[str], None]) -> None:
        super().__init__("Take a photo")
        from kivy.uix.camera import Camera as CameraView
        self.on_photo = on_photo
        self.view = CameraView(play=True, resolution=(1280, 720),
                               allow_stretch=True, keep_ratio=True,
                               size_hint_y=None,
                               height=min(dp(320), Window.height * 0.45))
        self.add(self.view)
        self.add(button_row(Btn("Cancel", kind="quiet",
                                on_release=lambda _w: self.dismiss()),
                            Btn("Take photo",
                                on_release=lambda _w: self._shoot())))
        self.bind(on_dismiss=lambda *_a: setattr(self.view, "play", False))

    def _shoot(self) -> None:
        texture = self.view.texture
        if texture is None:
            return
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        path = str(MEDIA_DIR / f"shot_{uuid.uuid4().hex}.png")
        try:
            # Texture.save writes the texture's rows as they are stored.
            # A camera stores the picture's top row first and flips the
            # texture only for display (a negative height in its uvs); a
            # texture drawn by GL holds its bottom row first. Either way the
            # file must come out the way the picture is seen.
            texture.save(path, flipped=texture.uvsize[1] >= 0)
        except Exception:
            log.exception("could not save the camera's picture")
            return
        self.dismiss()
        self.on_photo(path)


class NoCameraSheet(Sheet):
    """Said when the computer has no camera this app can open."""

    def __init__(self, on_choose: Optional[Callable[[], None]]) -> None:
        super().__init__("No camera found")
        how = ("Ferritext opens a computer's camera through OpenCV. To use "
               "one, install it and restart:  pip install opencv-python")
        self.add(InfoLabel(how, color=T.text_2, font_size=fs(16)))

        def choose(_w):
            self.dismiss()
            if on_choose is not None:
                on_choose()
        self.add(button_row(Btn("Close", kind="quiet",
                                on_release=lambda _w: self.dismiss()),
                            Btn("Choose a photo", on_release=choose)))


_desktop_camera: Optional[bool] = None


def desktop_camera_available() -> bool:
    """Whether Kivy found a camera provider (OpenCV, GStreamer, the Mac's
    own). Asked once, on first use: looking costs a second or so."""
    global _desktop_camera
    if _desktop_camera is None:
        try:
            from kivy.core.camera import Camera as CoreCamera
            _desktop_camera = CoreCamera is not None
        except Exception:
            _desktop_camera = False
    return _desktop_camera


def open_desktop_camera(on_photo: Callable[[str], None],
                        on_choose: Optional[Callable[[], None]] = None) -> None:
    if desktop_camera_available():
        try:
            CameraSheet(on_photo).open()
            return
        except Exception:
            log.exception("the camera would not open")
    NoCameraSheet(on_choose).open()


class TextPromptSheet(Sheet):
    """One line of text, confirmed (rename an identity, rename a group)."""

    def __init__(self, title: str, label: str, value: str, note: str,
                 on_save: Callable[[str], bool], limit: int = 40) -> None:
        super().__init__(title)
        self._on_save, self._limit = on_save, limit
        self.entry = self.add(Field(label=label, on_enter=self._save))
        self.entry.text = value
        if note:
            self.add(hint(note))
        self.add(button_row(Btn("Cancel", kind="quiet",
                                on_release=lambda _w: self.dismiss()),
                            Btn("Save", on_release=lambda _w: self._save())))

    def _save(self) -> None:
        value = sanitize_text(self.entry.text)[:self._limit].strip()
        if value and self._on_save(value) is not False:
            self.dismiss()


class RenameIdentityModal(TextPromptSheet):
    """Rename an identity's on-device label (its relay username is fixed)."""

    def __init__(self, app: "FextApp", address: str) -> None:
        ident = app.keys.find(address) or {}
        self.app, self.address = app, address
        super().__init__(
            "Rename identity", "Label", ident.get("label", ""),
            "Only this device shows the label, to tell your identities "
            "apart. Your name on the relay stays "
            f"@{ident.get('username') or '(unregistered)'}.", self._apply)

    def _apply(self, label: str) -> bool:
        self.app.keys.rename_identity(self.address, label)
        self.app.chats_screen.refresh_header()
        Clock.schedule_once(lambda _dt: IdentitySheet(self.app).open(), 0.1)
        return True


class ChatHistorySheet(Sheet):
    """Plaintext export (.json / .txt) and import (.json) of one chat."""

    def __init__(self, app: "FextApp", peer: str) -> None:
        name, _kind = conversation_title(app, peer)
        super().__init__("Chat history", subtitle=f"{name} — plaintext copies "
                                                  f"that stay on this device")
        self.app, self.peer, self.name = app, peer, name
        self.add(button_row(Btn("Export .json", kind="quiet",
                                on_release=lambda _w: self._export("json")),
                            Btn("Export .txt", kind="quiet",
                                on_release=lambda _w: self._export("txt"))))
        self.add(Btn("Import .json", kind="quiet",
                     on_release=lambda _w: self._import()))
        self.io_status = self.status_label()
        self.add(Btn("Done", kind="text",
                     on_release=lambda _w: self.dismiss()))

    def _author(self, m: dict) -> str:
        if m["direction"] == "out":
            return self.app.keys.username or "Me"
        if m.get("kind") == "event":
            return "·"
        return self.app.conversation_screen.author_name(
            m.get("author") or m["peer"])

    def _export(self, fmt: str) -> None:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", self.name)[:40] or "chat"
        path = EXPORT_DIR / f"fext_chat_{safe}_{stamp}.{fmt}"
        messages = self.app.store.messages_for(self.peer)
        contact = self.app.store.get_contact(self.peer) or {}
        try:
            if fmt == "json":
                path.write_text(json.dumps({
                    "fext_chat_export": 2,
                    "identity": self.app.keys.address,
                    "peer": {"address": self.peer,
                             "username": contact.get("username") or
                             self.name},
                    "exported_at": now_iso(),
                    "messages": [dict({"direction": m["direction"],
                                       "body": m["body"],
                                       "created_at": m["created_at"],
                                       "status": m["status"],
                                       "read": m["read"]},
                                      **({"author": m["author"]}
                                         if m.get("author") else {}))
                                 for m in messages
                                 if m.get("kind") != "event"],
                }, indent=2, ensure_ascii=False), "utf-8")
            else:
                lines = [f"{APP_NAME} — {self.name} ({self.peer})",
                         f"Exported {now_iso()}", ""]
                for m in messages:
                    lines.append(f"[{m['created_at']}] {self._author(m)}: "
                                 f"{m['body']}")
                path.write_text("\n".join(lines) + "\n", "utf-8")
            opened = open_folder_in_explorer(EXPORT_DIR)
            note = f"Exported {len(messages)} messages to {path}"
            if opened:
                note += " (the folder is open)"
            self.set_status(self.io_status, note)
        except OSError as exc:
            self.set_status(self.io_status, f"Export failed: {exc}", T.alert)

    def _import(self) -> None:
        ImportChooserModal(self._import_file).open()

    def _import_file(self, path: str) -> None:
        try:
            data = json.loads(Path(path).read_text("utf-8"))
            if data.get("fext_chat_export") != 2 or \
                    not isinstance(data.get("messages"), list):
                raise ValueError(f"not a {APP_NAME} chat export")
            added = 0
            for m in data["messages"]:
                if not isinstance(m, dict):
                    continue
                ok = self.app.store.import_message(
                    peer=self.peer,
                    direction=str(m.get("direction", "")),
                    body=sanitize_text(str(m.get("body", ""))),
                    created_at=str(m.get("created_at", now_iso()))[:64],
                    status=int(m.get("status", 1) or 0) if
                    str(m.get("status", 1)).lstrip("-").isdigit() else 1,
                    read=1)
                added += 1 if ok else 0
            self.set_status(self.io_status, (
                f"Imported {added} new messages "
                f"({len(data['messages']) - added} already here)"))
            self.app.conversation_screen._rendered_peer = None
            self.app.refresh_chat(self.peer)
            self.app.refresh_sidebar()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.set_status(self.io_status, f"Import failed: {exc}", T.alert)


ContactSettingsModal = ChatHistorySheet     # pre-9.1 name


class RemoveIdentityModal(Sheet):
    """Confirm removing an identity from this device.

    The private key IS the identity: once it is gone, the address, its
    history and any coins on it cannot be recovered. So the key export is
    offered BEFORE the destructive button. Releasing the name on the relay
    is a separate choice — another device may still use the same key.
    """

    def __init__(self, app: "FextApp", address: str) -> None:
        ident = app.keys.find(address) or {}
        label = ident.get("label") or short_addr(address, 12)
        super().__init__(f"Remove “{label}”?")
        self.app = app
        self.address = address
        self.username = ident.get("username")
        self._release = bool(self.username)
        self.add(hint(
            "This deletes its private key and its chats from this device. "
            "The key cannot be recovered afterwards — if you might want "
            "this identity back, copy the key first."))
        self.add(InfoLabel(address, color=T.accent, font_size=fs(14)))
        self.add(Btn("Copy the private key first", kind="danger",
                     on_release=lambda _w: self._copy_key()))
        if self.username:
            self.release_switch = ToggleSwitch(
                True, lambda on: setattr(self, "_release", on))
            self.add(ListRow(None, f"Release “{self.username}” on the relay",
                             "Frees the name and removes you from the "
                             "directory. Leave it if another device still "
                             "uses this key.", control=self.release_switch,
                             on_tap=self.release_switch.flip,
                             padding=[0, dp(8), 0, dp(8)]))
        self.status = self.status_label()
        self.confirm = Btn("Remove identity", kind="solid_danger",
                           on_release=lambda _w: self._confirm())
        self.add(button_row(Btn("Cancel", kind="quiet",
                                on_release=lambda _w: self.dismiss()),
                            self.confirm))

    def _copy_key(self) -> None:
        """private_export() reads the ACTIVE identity, so switch, read and
        switch back — in a finally."""
        active = self.app.keys.address
        try:
            self.app.keys.activate(self.address)
            data = json.loads(self.app.keys.private_export())
            Clipboard.copy(data["private_key_wif"])
            self.set_status(self.status, "The private key is on the "
                            "clipboard — paste it somewhere safe now.",
                            T.accent)
        except Exception as exc:
            self.set_status(self.status, f"Could not export the key: {exc}",
                            T.alert)
        finally:
            try:
                self.app.keys.activate(active)
            except Exception:
                pass

    def _confirm(self) -> None:
        self.confirm.disabled = True
        self.set_status(self.status, "Removing…")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        note = ""
        if self._release and self.username:
            previous = self.app.keys.address
            try:
                self.app.keys.activate(self.address)
                ApiClient(self.app.keys).deregister()
            except (requests.RequestException, ApiClient.ApiError):
                note = (" (the relay could not be reached, so the name is "
                        "still registered)")
            finally:
                try:
                    self.app.keys.activate(previous)
                except Exception:
                    pass
        run_on_ui(self._finish, note)

    def _finish(self, note: str) -> None:
        self.dismiss()
        self.app.on_identity_removed(self.address, note)


class IdentitySheet(Sheet):
    """Tap your disc on the chat list: who you are now, every identity on
    this device (switch, rename, remove), a new one, and settings."""

    def __init__(self, app: "FextApp") -> None:
        super().__init__("Identities")
        self.app = app
        keys = app.keys
        self.card.padding = [0, dp(8), 0, dp(16)]
        self.title_label.padding = [dp(22), 0]
        rows = []
        for ident in keys.identities:
            rows.append(self._row(ident, ident["address"] == keys.address))
        self.add(rows_card(rows, inset=dp(76)))
        self.add(Rule())
        self.add(rows_card([
            ListRow("plus", "New identity", "Mine a fresh key or import one",
                    on_tap=self._new),
            ListRow("sliders", "Settings", "Appearance, notifications, keys",
                    on_tap=self._settings)]))

    def _row(self, ident: dict, active: bool) -> BoxLayout:
        address = ident["address"]
        line = BoxLayout(size_hint_y=None, height=max(dp(68), line_height(17)
                                                      + line_height(14) +
                                                      dp(22)),
                         padding=[dp(20), 0, dp(8), 0], spacing=dp(4))
        row = TappableBox(orientation="horizontal", spacing=dp(14),
                          on_tap=lambda a=address: self._switch(a))
        row.add_widget(Avatar(address, ident.get("username") or
                              ident.get("label") or "?", diameter=dp(42),
                              pos_hint={"center_y": .5}))
        texts = BoxLayout(orientation="vertical", padding=[0, dp(12)])
        name = single_line(ident.get("label") or "?", fs(17), T.text,
                           bold=active)
        texts.add_widget(name)
        texts.add_widget(single_line(
            f"@{ident.get('username') or 'unregistered'} · "
            f"{short_addr(address, 10)}", fs(14), T.text_3))
        row.add_widget(texts)
        if active:
            mark = CapsLabel("Active", size=10.5, color=T.accent,
                             size_hint=(None, None), halign="right",
                             pos_hint={"center_y": .5})
            mark.width = mark.natural_width
            row.add_widget(mark)
        line.add_widget(row)
        line.add_widget(IconButton("edit", label="Rename identity",
                                   pos_hint={"center_y": .5},
                                   on_release=lambda _w: self._rename(
                                       address)))
        line.add_widget(IconButton("delete", tint=T.alert,
                                   label="Remove identity",
                                   pos_hint={"center_y": .5},
                                   on_release=lambda _w: self._remove(
                                       address)))
        return line

    def _switch(self, address: str) -> None:
        self.dismiss()
        if address != self.app.keys.address:
            self.app._activate_identity(address)

    def _remove(self, address: str) -> None:
        self.dismiss()
        RemoveIdentityModal(self.app, address).open()

    def _rename(self, address: str) -> None:
        self.dismiss()
        RenameIdentityModal(self.app, address).open()

    def _new(self) -> None:
        self.dismiss()
        NewIdentityModal(self.app).open()

    def _settings(self) -> None:
        self.dismiss()
        self.app.open_settings()


ProfileMenuModal = IdentitySheet            # pre-9.1 name


# =============================================================================
#  The chat list — the "home" screen
# =============================================================================
BULLET = "•"
FLEURON = "❦"                 # ❦ — the empty page's ornament


class AppBar(BoxLayout):
    """A screen's head: no filled bar, just the page, a title in the serif,
    an optional italic subtitle, and a hairline beneath."""

    def __init__(self, title: str = "", on_back: Optional[Callable] = None,
                 **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", max(dp(60), line_height(20) + dp(20)))
        kw.setdefault("padding", [dp(4), 0, dp(8), 0])
        kw.setdefault("spacing", dp(4))
        super().__init__(**kw)
        if on_back is not None:
            self.add_widget(IconButton("back", label="Back",
                                       pos_hint={"center_y": .5},
                                       on_release=lambda _w: on_back()))
        else:
            self.add_widget(Widget(size_hint_x=None, width=dp(16)))
        self.title = single_line(title, fs(20), T.text)
        self.add_widget(self.title)
        hairline(self, "bottom")


class PersonRow(ButtonBehavior, BoxLayout):
    """Someone (or a group) to tap: their disc, a name, a line beneath."""

    def __init__(self, seed: str, name: str, sub: str = "",
                 on_tap: Optional[Callable] = None, kind: str = "dm",
                 trailing: Optional[Widget] = None,
                 initial_from: Optional[str] = None, **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", max(dp(64), line_height(17) +
                                    line_height(14) + dp(20)))
        kw.setdefault("padding", [dp(20), dp(6), dp(16), dp(6)])
        kw.setdefault("spacing", dp(14))
        super().__init__(**kw)
        self.add_widget(Avatar(seed, initial_from or name, diameter=dp(42),
                               kind=kind, pos_hint={"center_y": .5}))
        texts = BoxLayout(orientation="vertical", padding=[0, dp(4)])
        texts.add_widget(single_line(name, fs(17), T.text))
        if sub:
            texts.add_widget(single_line(sub, fs(14), T.text_3))
        self.add_widget(texts)
        if trailing is not None:
            trailing.pos_hint = {"center_y": .5}
            self.add_widget(trailing)
        with self.canvas.before:
            self._bg = Color(0, 0, 0, 0)
            self._rect = Rectangle()
        self.bind(pos=self._sync, size=self._sync,
                  state=lambda _w, st: setattr(
                      self._bg, "rgba", C(T.press) if st == "down"
                      else (0, 0, 0, 0)))
        if on_tap is not None:
            self.bind(on_release=lambda _w: on_tap())

    def _sync(self, *_a):
        self._rect.pos, self._rect.size = self.pos, self.size


def caps_tag(text: str, color: Optional[str] = None) -> CapsLabel:
    """A small capitals tag sized to its text (ADMIN, YOU, ACTIVE)."""
    tag = CapsLabel(text, size=10.5, color=color or T.accent,
                    size_hint=(None, None), halign="right")
    tag.width = tag.natural_width + dp(2)
    return tag


class ConversationRow(ButtonBehavior, BoxLayout):
    """One chat in the list: disc, name over a one-line preview, the time
    and the unread count on the right, a hairline that starts where the
    text does. Groups show who spoke last; your own last message shows its
    ticks."""

    def __init__(self, conv: dict, on_open: Callable[[str], None],
                 author_name: Optional[Callable] = None, **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", max(dp(76), line_height(18) +
                                    line_height(15.5) + dp(26)))
        kw.setdefault("padding", [dp(18), dp(10), dp(18), dp(10)])
        kw.setdefault("spacing", dp(14))
        super().__init__(**kw)
        peer, kind = conv["peer"], conv.get("kind", "dm")
        self.peer = peer
        if kind == "self":
            name = "Notes to self"
        elif kind == "group":
            name = conv.get("group_name") or "Group"
        else:
            name = conv.get("username") or short_addr(peer, 14)
        unread = int(conv.get("unread") or 0)
        notify = conv.get("notify")
        muted = kind != "self" and notify is not None and not notify
        with self.canvas.before:
            self._bg = Color(0, 0, 0, 0)
            self._rect = Rectangle()
        hairline(self, "bottom", inset=dp(18) + dp(52) + dp(14))
        self.bind(pos=self._sync, size=self._sync,
                  state=lambda _w, st: setattr(
                      self._bg, "rgba", C(T.press) if st == "down"
                      else (0, 0, 0, 0)))
        self.bind(on_release=lambda _w: on_open(peer))

        self.add_widget(Avatar(peer, name, diameter=dp(52), kind=kind,
                               pos_hint={"center_y": .5}))
        middle = BoxLayout(orientation="vertical", padding=[0, dp(2)])
        top = BoxLayout(spacing=dp(6))
        top.add_widget(single_line(name, fs(18), T.text, bold=True))
        if muted:
            bell = Widget(size_hint_x=None, width=dp(18))
            bell.bind(pos=lambda w, *_a: self._bell(w),
                      size=lambda w, *_a: self._bell(w))
            top.add_widget(bell)
        when = Label(text=pretty_day(conv.get("last_at") or "")
                     if conv.get("preview") is not None else "",
                     font_size=fs(13.5),
                     color=C(T.accent if unread and not muted else T.text_3),
                     size_hint_x=None, halign="right", valign="middle")
        when.bind(texture_size=lambda w, v: setattr(w, "width", v[0]),
                  size=lambda w, v: setattr(w, "text_size", (None, v[1])))
        when.texture_update()
        top.add_widget(when)
        middle.add_widget(top)

        bottom = BoxLayout(spacing=dp(6))
        preview, italic = self._preview(conv, kind, author_name)
        if conv.get("last_direction") == "out" and \
                conv.get("last_kind") != "event" and kind != "self":
            status = int(conv.get("last_status") or 0)
            if not conv.get("ticks_enabled", 1) and kind == "dm":
                status = min(status, 1)
            ticks = TickMarks(count=max(1, min(status, 3)),
                              tint=T.tick if status > 0 else T.tick_grey,
                              pos_hint={"center_y": .5})
            bottom.add_widget(ticks)
        text = single_line(preview, fs(15.5), T.text_2 if unread else T.text_3,
                           italic=italic)
        bottom.add_widget(text)
        if unread:
            holder = AnchorLayout(size_hint_x=None, width=dp(44),
                                  anchor_x="right")
            holder.add_widget(UnreadBadge(min(unread, 9999), muted=muted))
            bottom.add_widget(holder)
        middle.add_widget(bottom)
        self.add_widget(middle)

    @staticmethod
    def _preview(conv: dict, kind: str, author_name) -> tuple:
        body = conv.get("preview")
        if body is None:
            if kind == "self":
                return ("Jot things down — they stay on this device", True)
            if kind == "group":
                return ("A new group. Say something to everyone", True)
            return ("Say hello — sealed end to end", True)
        body = " ".join(str(body).split())
        if conv.get("last_kind") == "event":
            return body, True
        if kind == "group":
            if conv.get("last_direction") == "out":
                return f"You: {body}", False
            who = author_name(conv.get("last_author")) if author_name and \
                conv.get("last_author") else ""
            return (f"{who}: {body}" if who else body), False
        return body, False

    def _sync(self, *_a):
        self._rect.pos, self._rect.size = self.pos, self.size

    @staticmethod
    def _bell(widget):
        widget.canvas.clear()
        with widget.canvas:
            draw_icon("bell_off", (widget.x + widget.width / 2), (widget.y + widget.height / 2), dp(14),
                      C(T.text_3))


class FabButton(IconButton):
    """The compose button: a larger gold disc, lifted off the page."""

    def _redraw(self, *_a):
        self.canvas.clear()
        d = min(self.width, self.height)
        with self.canvas:
            Color(0, 0, 0, .28 if T.dark else .12)
            Ellipse(pos=((self.x + self.width / 2) - d / 2, (self.y + self.height / 2) - d / 2 -
                         dp(2)), size=(d, d))
            Color(*C(T.accent_press if self.state == "down" else T.accent))
            SmoothEllipse(pos=((self.x + self.width / 2) - d / 2,
                               (self.y + self.height / 2) - d / 2),
                          size=(d, d))
            draw_icon(self._icon, (self.x + self.width / 2), (self.y + self.height / 2), dp(24),
                      C(T.on_accent), width=dp(1.0))


class ChatsScreen(Screen):
    """Home: the wordmark, who you are, your conversations, and the button
    that starts a new one."""

    def __init__(self, app: "FextApp", **kw):
        super().__init__(name="chats", **kw)
        self.app = app
        root = FloatLayout()
        paint_round(root, T.bg, radius=0)
        self.add_widget(root)
        column = BoxLayout(orientation="vertical", size_hint=(1, 1),
                           pos_hint={"x": 0, "y": 0})
        root.add_widget(column)

        # ---- the head: wordmark, identity, settings --------------------------
        head = BoxLayout(size_hint_y=None, height=max(dp(84), line_height(30)
                                                      + dp(30)),
                         padding=[dp(20), dp(6), dp(6), dp(4)], spacing=dp(2))
        titles = BoxLayout(orientation="vertical", padding=[0, dp(8)])
        self.brand = single_line(APP_NAME, fs(30), T.text)
        titles.add_widget(self.brand)
        # Not in capitals: an address is case-sensitive Base58.
        self.me_label = single_line("", fs(14), T.text_3, italic=True)
        titles.add_widget(self.me_label)
        head.add_widget(titles)
        self.avatar_holder = AnchorLayout(size_hint_x=None, width=TOUCH)
        head.add_widget(self.avatar_holder)
        head.add_widget(IconButton("sliders", label="Settings",
                                   pos_hint={"center_y": .5},
                                   on_release=lambda _w:
                                   self.app.open_settings()))
        hairline(head, "bottom", inset=dp(20), inset_right=dp(20))
        column.add_widget(head)

        # ---- the status line: tap it to reconnect at once --------------------
        ribbon = TappableBox(on_tap=lambda: self.app.retry_now(),
                             orientation="horizontal", size_hint_y=None,
                             height=max(dp(40), line_height(14) + dp(12)),
                             padding=[dp(20), 0, dp(20), 0])
        self.status_label = Label(text="Starting…", font_size=fs(14),
                                  italic=True, color=C(T.text_3),
                                  halign="left", valign="middle",
                                  shorten=True, shorten_from="right")
        self.status_label.bind(size=lambda w, v: setattr(w, "text_size", v))
        ribbon.add_widget(self.status_label)
        self.retry_hint = CapsLabel("Tap to retry", size=10, color=T.accent,
                                    size_hint=(None, 1), halign="right")
        self.retry_hint.width = self.retry_hint.natural_width + dp(4)
        self.retry_hint.opacity = 0
        ribbon.add_widget(self.retry_hint)
        column.add_widget(ribbon)

        # ---- the list --------------------------------------------------------
        self.scroll = ScrollView(bar_width=dp(3), do_scroll_x=False)
        self.list = BoxLayout(orientation="vertical", size_hint_y=None,
                              padding=[0, 0, 0, dp(96)])
        self.list.bind(minimum_height=self.list.setter("height"))
        self.scroll.add_widget(self.list)
        column.add_widget(self.scroll)

        # ---- the compose button ----------------------------------------------
        self.fab = FabButton("edit", label="New conversation",
                             size=(dp(60), dp(60)),
                             on_release=lambda _w: self.app.open_compose())
        holder = AnchorLayout(anchor_x="right", anchor_y="bottom",
                              padding=[0, 0, dp(20), dp(24)],
                              size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        holder.add_widget(self.fab)
        root.add_widget(holder)

    def refresh_header(self) -> None:
        keys = self.app.keys
        self.avatar_holder.clear_widgets()
        if not keys.has_identity:
            self.me_label.text = "end-to-end encrypted correspondence"
            return
        avatar = AvatarButton(keys.address, keys.username or keys.label,
                              diameter=dp(38))
        avatar.bind(on_release=lambda _w: IdentitySheet(self.app).open())
        self.avatar_holder.add_widget(avatar)
        self.me_label.text = (f"@{keys.username}" if keys.username
                              else "unregistered") + \
            f"  ·  {short_addr(keys.address, 10)}"

    def set_status(self, text: str, color: str) -> None:
        self.status_label.text = text
        color = retint(color)
        calm = color in (T.ok, T.text_3)
        self.status_label.color = C(T.text_3 if calm else color)
        # Offer the retry only when it would do something, and only once.
        self.retry_hint.opacity = 0 if calm or "retry" in text.lower() else 1

    def refresh_list(self, conversations: list) -> None:
        self.list.clear_widgets()
        keys = self.app.keys
        if not keys.has_identity:
            self.list.add_widget(self._empty_state())
            return
        notes = next((c for c in conversations if c.get("kind") == "self"),
                     None) or {"peer": keys.address, "kind": "self",
                               "preview": None, "unread": 0}
        rest = [c for c in conversations if c.get("kind") != "self"]
        author = self.app.conversation_screen.author_name
        self.list.add_widget(ConversationRow(notes, self.app.open_conversation,
                                             author))
        for conv in rest:
            self.list.add_widget(ConversationRow(
                conv, self.app.open_conversation, author))
        if not rest:
            self.list.add_widget(self._empty_state())

    def _empty_state(self) -> BoxLayout:
        box = BoxLayout(orientation="vertical", size_hint_y=None,
                        padding=[dp(32), dp(40), dp(32), 0], spacing=dp(14))
        box.bind(minimum_height=box.setter("height"))
        box.add_widget(InfoLabel(FLEURON, color=T.accent, font_size=fs(34),
                                 halign="center"))
        box.add_widget(InfoLabel("No correspondence yet", font_size=fs(22),
                                 halign="center"))
        box.add_widget(InfoLabel(
            "Add someone by their username or Fec… address, or gather "
            "several into a group. Every word is sealed on this device "
            "before it leaves, and only they can open it.",
            color=T.text_3, font_size=fs(15.5), halign="center"))
        holder = AnchorLayout(size_hint_y=None, height=TOUCH + dp(12))
        button = Btn("Add a contact", size_hint_x=None,
                     on_release=lambda _w: self.app.open_add_contact())
        button.width = max(dp(200), button.natural_width())
        holder.add_widget(button)
        box.add_widget(holder)
        return box


class ComposeSheet(Sheet):
    """The compose button's sheet: a new contact, a new group, notes to self,
    or one of the people you already know."""

    def __init__(self, app: "FextApp") -> None:
        super().__init__("New conversation")
        self.app = app
        self.card.padding = [0, dp(8), 0, dp(16)]
        self.title_label.padding = [dp(22), 0]
        self.add(rows_card([
            ListRow("person_add", "Add a contact",
                    "By username or Fec… address",
                    on_tap=lambda: self._then(app.open_add_contact)),
            ListRow("group", "New group",
                    f"You and up to {MAX_GROUP_MEMBERS - 1} others",
                    on_tap=lambda: self._then(app.open_new_group)),
            ListRow("bookmark", "Notes to self", "Kept on this device only",
                    on_tap=lambda: self._then(
                        lambda: app.open_conversation(app.keys.address))),
        ]))
        contacts = app.store.contacts() if app.store else []
        if contacts:
            self.add(SectionHead("Contacts"))
            self.add(rows_card([
                PersonRow(c["address"], c.get("username") or
                          short_addr(c["address"], 14),
                          short_addr(c["address"], 18),
                          on_tap=lambda a=c["address"]: self._then(
                              lambda: app.open_conversation(a)))
                for c in contacts], inset=dp(76)))

    def _then(self, action: Callable) -> None:
        self.dismiss()
        Clock.schedule_once(lambda _dt: action(), 0.05)


# =============================================================================
#  The conversation
# =============================================================================
_METRIC_LABEL = None


def measure_text(text: str, font_size: float, wrap_width: float,
                 italic: bool = False) -> tuple:
    """Pixel (width, height) of `text` wrapped at `wrap_width`.

    One shared, never-displayed Label measures every bubble: the widget
    that DISPLAYS message text on a desktop is a TextInput (so it can be
    selected), and a TextInput cannot report its size until the frame after
    it is given one. Text that fits keeps its natural width; only text that
    needs wrapping is measured at the wrap width.
    """
    global _METRIC_LABEL
    if _METRIC_LABEL is None:
        _METRIC_LABEL = Label(halign="left", valign="top",
                              size_hint=(None, None))
    metric = _METRIC_LABEL
    metric.font_size = font_size
    metric.italic = italic
    metric.text = text
    if len(text) * font_size * 0.42 < wrap_width * 3:
        metric.text_size = (None, None)
        metric.texture_update()
        width, height = metric.texture_size
        if width <= wrap_width:
            return width, height
    metric.text_size = (wrap_width, None)
    metric.texture_update()
    return metric.texture_size


class SelectableText(TextInput):
    """Message text you can highlight and copy (desktop).

    A READ-ONLY TextInput: drag to select, Ctrl+A / Ctrl+C. A ScrollView
    holds each touch for scroll_timeout and only hands it on if the finger
    has not travelled, so a flick still scrolls. Painted to look exactly
    like a label: no ground, no border, no cursor.
    """

    def __init__(self, on_tap: Optional[Callable] = None, **kw):
        kw.setdefault("readonly", True)
        kw.setdefault("multiline", True)
        kw.setdefault("background_color", (0, 0, 0, 0))
        kw.setdefault("background_normal", "")
        kw.setdefault("background_active", "")
        kw.setdefault("background_disabled_normal", "")
        kw.setdefault("foreground_color", C(T.text))
        kw.setdefault("selection_color", C(T.accent)[:3] + [0.35])
        kw.setdefault("cursor_color", (0, 0, 0, 0))
        kw.setdefault("cursor_width", 0)
        kw.setdefault("padding", [0, 0, 0, 0])
        kw.setdefault("size_hint", (None, None))
        super().__init__(**kw)
        self._on_tap = on_tap
        # A TextInput scrolls to keep its cursor in view. Given the width
        # it starts with (100px) every message wraps tall, and with the
        # cursor at the end it scrolled to the LAST line — which then stayed
        # scrolled once the bubble gave it its real size, hiding the first
        # lines. The cursor lives at the start, and the view never scrolls.
        self.cursor = (0, 0)
        self.bind(size=self._unscroll, text=self._unscroll)

    def _unscroll(self, *_a):
        self.scroll_x = self.scroll_y = 0

    # Tapping a message marks it read; the TextInput swallows the touch, so
    # re-dispatch it — only when the press AND release were on this text.
    def on_touch_down(self, touch):
        handled = super().on_touch_down(touch)
        if handled:
            touch.ud["fext_tap_origin"] = self
        return handled

    def on_touch_up(self, touch):
        handled = super().on_touch_up(touch)
        if (self._on_tap is not None
                and touch.ud.get("fext_tap_origin") is self
                and self.collide_point(*touch.pos)):
            self._on_tap()
        return handled


def message_ext(msg: dict) -> dict:
    """The parsed `ext` column of a message row (reply, fwd, media)."""
    try:
        ext = json.loads(msg.get("ext") or "{}")
        return ext if isinstance(ext, dict) else {}
    except (TypeError, ValueError):
        return {}


def texture_from_b64(data: str):
    """Kivy texture for a small inline JPEG (the photo thumbnail)."""
    import io as _io
    try:
        return CoreImage(_io.BytesIO(b64d(data)), ext="jpg").texture
    except Exception:
        return None


class QuoteBlock(ButtonBehavior, BoxLayout):
    """The message a reply answers: a gold rule, who said it (capitals),
    and a line of what they said (italic). Tap it to go there."""

    def __init__(self, author: str, snippet: str, outgoing: bool,
                 on_tap: Optional[Callable] = None, **kw):
        kw.setdefault("orientation", "vertical")
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("padding", [dp(12), dp(5), dp(8), dp(6)])
        super().__init__(**kw)
        with self.canvas.before:
            Color(*C(T.quote_own if outgoing else T.quote_peer))
            self._bg = SmoothRoundedRectangle(radius=[dp(4)])
            Color(*C(T.accent))
            self._bar = Rectangle()
        self.bind(pos=self._sync, size=self._sync)
        self.author = CapsLabel(author, size=10.5, color=T.accent)
        self.snippet = single_line(snippet, fs(15), T.text_2, italic=True,
                                   size_hint_y=None, height=line_height(15))
        self.add_widget(self.author)
        self.add_widget(self.snippet)
        self.height = self.author.height + self.snippet.height + dp(11)
        if on_tap is not None:
            self.bind(on_release=lambda _w: on_tap())

    def _sync(self, *_a):
        self._bg.pos, self._bg.size = self.pos, self.size
        self._bar.pos, self._bar.size = self.pos, (dp(2), self.height)

    def natural_width(self) -> float:
        return max(self.author.natural_width,
                   measure_text(self.snippet.text, fs(15), dp(600),
                                italic=True)[0]) + dp(22)


class MediaStatus(Label):
    """A small caption chip over a photo ('Downloading…', etc.)."""

    def __init__(self, **kw):
        super().__init__(font_size=fs(13.5), italic=True,
                         color=C("#FFFFF8"), size_hint=(None, None), **kw)
        with self.canvas.before:
            self._col = Color(0, 0, 0, .5)
            self._bg = RoundedRectangle(radius=[dp(11)])
        self.bind(pos=self._sync, size=self._sync,
                  texture_size=lambda w, v: setattr(
                      w, "size", (v[0] + dp(18), max(dp(24), v[1] + dp(6)))))

    def _sync(self, *_a):
        self._bg.pos, self._bg.size = self.pos, self.size
        self._col.a = .5 if self.text else 0


class ImageContent(ButtonBehavior, FloatLayout):
    """A photo in a bubble: the full image once it is here, the blurry
    inline thumbnail (plus a status chip) until then."""

    def __init__(self, msg: dict, media: dict, screen, **kw):
        kw.setdefault("size_hint", (None, None))
        super().__init__(**kw)
        self.msg, self.media, self.screen = msg, media, screen
        with self.canvas.before:
            Color(*C(T.surface_hi))
            self._ground = SmoothRoundedRectangle(radius=[dp(6)])
        self.bind(pos=self._sync, size=self._sync)
        self.picture = KivyImage(allow_stretch=True, keep_ratio=True,
                                 size_hint=(1, 1),
                                 pos_hint={"x": 0, "y": 0})
        self.add_widget(self.picture)
        self.chip = MediaStatus(pos_hint={"center_x": .5, "center_y": .5})
        self.add_widget(self.chip)
        self.bind(on_release=lambda _w: screen.on_media_tap(self.msg))
        self.refresh(msg)

    def _sync(self, *_a):
        self._ground.pos, self._ground.size = self.pos, self.size

    def natural_size(self, max_w: float) -> tuple:
        w0, h0 = self.media.get("w") or 4, self.media.get("h") or 3
        width = min(max_w, dp(260))
        height = max(dp(90), min(dp(340), width * h0 / float(w0)))
        return width, height

    def refresh(self, msg: dict) -> None:
        self.msg = msg
        state = msg.get("media_state") or ""
        path = msg.get("media_path")
        if path and Path(path).is_file() and state in ("ready", ""):
            self.picture.texture = None
            self.picture.source = path
        elif self.media.get("thumb"):
            self.picture.source = ""
            self.picture.texture = texture_from_b64(self.media["thumb"])
        outgoing = msg["direction"] == "out"
        self.chip.text = {
            "uploading": "Sending…",
            "pending": "Downloading…",
            "downloading": "Downloading…",
            "expired": "Expired · tap to ask again",
            "requested": "Asked for it again",
            "failed": ("Not sent · tap to retry" if outgoing
                       else "Not downloaded · tap to retry"),
        }.get(state, "")


class Waveform(Widget):
    """Voice-note level bars; the played part is drawn in gold."""

    def __init__(self, wave: str = "", **kw):
        super().__init__(**kw)
        try:
            self._levels = list(b64d(wave)) if wave else []
        except (TypeError, ValueError):
            self._levels = []
        self.progress = 0.0
        self.bind(pos=self._redraw, size=self._redraw)

    def set_progress(self, fraction: float) -> None:
        self.progress = max(0.0, min(1.0, fraction))
        self._redraw()

    def _redraw(self, *_a):
        self.canvas.clear()
        bars = max(8, int(self.width / dp(4)))
        levels = self._levels or [60] * bars
        with self.canvas:
            for i in range(bars):
                lvl = levels[int(i * len(levels) / bars)] / 255.0
                height = max(dp(3), self.height * (0.18 + 0.82 * lvl))
                played = (i + 0.5) / bars <= self.progress
                Color(*C(T.accent if played else T.tick_grey))
                Rectangle(pos=(self.x + i * dp(4),
                               (self.y + self.height / 2) - height / 2),
                          size=(dp(2), height))


class VoiceContent(BoxLayout):
    """Play · waveform · length. Plays from the local copy once here."""

    def __init__(self, msg: dict, media: dict, screen, **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("height", TOUCH)
        kw.setdefault("spacing", dp(8))
        super().__init__(**kw)
        self.msg, self.media, self.screen = msg, media, screen
        self.button = IconButton("play", tint=T.on_accent, bg=T.accent,
                                 icon_size=dp(18), label="Play voice message",
                                 pos_hint={"center_y": .5},
                                 on_release=lambda _w:
                                 screen.on_media_tap(self.msg))
        self.add_widget(self.button)
        self.wave = Waveform(media.get("wave", ""), size_hint_y=None,
                             height=dp(26), pos_hint={"center_y": .5})
        self.add_widget(self.wave)
        dur = int(media.get("dur") or 0) // 1000
        self.length = Label(text=f"{dur // 60}:{dur % 60:02d}",
                            font_size=fs(13.5), color=C(T.text_3),
                            size_hint=(None, 1), width=dp(38))
        self.add_widget(self.length)
        self.refresh(msg)

    def natural_size(self, max_w: float) -> tuple:
        return min(max_w, dp(240)), TOUCH

    def refresh(self, msg: dict) -> None:
        self.msg = msg
        state = msg.get("media_state") or ""
        if state in ("expired", "failed"):
            self.button.set_icon("retry")
        elif state in ("pending", "downloading", "uploading", "requested"):
            self.button.set_icon("download")
        else:
            playing = self.screen.app.voice.path == msg.get("media_path") \
                and self.screen.app.voice.playing
            self.button.set_icon("pause" if playing else "play")


def make_body(text: str, font_size) -> Widget:
    """The message text: selectable on a desktop; on a phone a plain label
    (a long press there selects the whole message instead)."""
    if IS_MOBILE:
        return Label(text=text, color=C(T.text), font_size=font_size,
                     halign="left", valign="top", size_hint=(None, None))
    return SelectableText(text=text, font_size=font_size)


class ReactionChip(ButtonBehavior, Widget):
    """One reaction under a message — the emoji and how many gave it. Your
    own is ringed in gold; tap it to take it back (or to add yours)."""

    pop = NumericProperty(1.0)

    def __init__(self, emoji: str, count: int, mine: bool, on_tap, **kw):
        kw.setdefault("size_hint", (None, None))
        super().__init__(**kw)
        self.emoji, self.mine = emoji, mine
        self.label = Label(text=emoji if count < 2 else f"{emoji} {count}",
                           font_size=fs(15), color=C(T.text),
                           size_hint=(None, None))
        self.label.texture_update()
        self.size = (max(dp(40), self.label.texture_size[0] + dp(18)),
                     max(dp(30), round(fs(15) * 1.7)))
        self.label.size = self.label.texture_size
        self.add_widget(self.label)
        with self.canvas.before:
            PushMatrix()
            self._scale = Scale(1, 1, 1)
            ground = T.bg if T.dark else T.surface_hi
            self._ground_col = Color(*C(mix(T.accent, ground, .80) if mine
                                        else ground))
            self._ground = SmoothRoundedRectangle(radius=[dp(15)])
            Color(*C(T.accent if mine else T.rule_strong))
            self._edge = SmoothLine(width=1.0)
        with self.canvas.after:
            PopMatrix()
        self.bind(pos=self._sync, size=self._sync, pop=self._sync,
                  state=lambda _w, s: setattr(self, "opacity",
                                              .7 if s == "down" else 1))
        self.bind(on_release=lambda _w: on_tap(emoji))

    def _sync(self, *_a):
        self._scale.origin = (self.x + self.width / 2, self.y + self.height / 2)
        self._scale.x = self._scale.y = self.pop
        self._ground.pos, self._ground.size = self.pos, self.size
        self._ground.radius = [self.height / 2]
        self._edge.rounded_rectangle = (self.x + .5, self.y + .5,
                                        self.width - 1, self.height - 1,
                                        self.height / 2 - .5)
        self.label.center = ((self.x + self.width / 2), (self.y + self.height / 2) + dp(.5))

    def animate_in(self) -> None:
        if not motion(1):
            return
        Animation.cancel_all(self, "pop")
        self.pop = 0.45
        Animation(pop=1.0, d=0.34, t="out_back").start(self)


class ReactionBar(Widget):
    """The reactions under one message, wrapping onto a second line when
    they must."""

    GAP = dp(6)

    def __init__(self, **kw):
        kw.setdefault("size_hint", (None, None))
        super().__init__(**kw)
        self.chips: list = []
        self.bind(pos=self._place)

    def set(self, reactions: list, me: str, on_tap) -> None:
        for chip in self.chips:
            self.remove_widget(chip)
        groups: dict = {}
        for emoji, reactor, _at in reactions:
            groups.setdefault(emoji, []).append(reactor)
        self.chips = [ReactionChip(e, len(who), me in who, on_tap)
                      for e, who in groups.items()]
        for chip in self.chips:
            self.add_widget(chip)

    def chip_for(self, emoji: str) -> Optional[ReactionChip]:
        return next((c for c in self.chips if c.emoji == emoji), None)

    def layout_for(self, max_w: float) -> tuple:
        """Size for `max_w`: (width, height); 0 high when there are none."""
        if not self.chips:
            self.size = (0, 0)
            return 0, 0
        x = widest = 0.0
        rows = 1
        for chip in self.chips:
            if x and x + chip.width > max_w:
                rows += 1
                x = 0
            x += chip.width + self.GAP
            widest = max(widest, x - self.GAP)
        h = self.chips[0].height
        self.size = (widest, rows * h + (rows - 1) * self.GAP)
        self._max_w = max_w
        self._place()
        return self.size

    def _place(self, *_a):
        if not self.chips:
            return
        max_w = getattr(self, "_max_w", self.width)
        h = self.chips[0].height
        x, y = self.x, self.top - h
        for chip in self.chips:
            if x > self.x and x - self.x + chip.width > max_w:
                x, y = self.x, y - h - self.GAP
            chip.pos = (x, y)
            x += chip.width + self.GAP


class MessageBubble(BoxLayout):
    """One message: who wrote it (in a group), a 'Forwarded' note, the
    quoted message it answers, a photo or voice note, the text, its
    reactions, and the time with the ticks.

    Yours sit right on a warmer ground; theirs left on the page's surface.
    Touches belong to the enclosing BubbleRow (tap, long-press, selection);
    this class only lays out.
    """

    def __init__(self, msg: dict, ticks_enabled: bool, screen, **kw):
        kw.setdefault("orientation", "vertical")
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("padding", [dp(12), dp(8), dp(12), dp(7)])
        kw.setdefault("spacing", dp(5))
        super().__init__(**kw)
        self.msg, self.screen = msg, screen
        self._last_width = 0.0
        self._text_w = self._text_h = 0.0
        self._body_floor = 0.0
        outgoing = msg["direction"] == "out"
        ext = message_ext(msg)
        corners = ([dp(10), dp(10), dp(3), dp(10)] if outgoing
                   else [dp(10), dp(10), dp(10), dp(3)])
        paint_round(self, T.own if outgoing else T.peer, corners=corners)
        if not outgoing and not T.dark:
            # On the light page theirs are white cards with a hairline;
            # yours keep the warm ground.
            paint_border(self, T.rule_strong, radius=dp(10))

        self.author = None
        author = msg.get("author")
        if not outgoing and screen.kind == "group" and author:
            self.author = CapsLabel(screen.author_name(author), size=10.5,
                                    color=ink_color(author), tracking=0.14,
                                    size_hint=(None, None))
            self.add_widget(self.author)

        self.fwd = None
        if ext.get("fwd"):
            self.fwd = Label(text="Forwarded", italic=True, font_size=fs(14),
                             color=C(T.text_3), halign="left",
                             size_hint=(None, None), height=line_height(14))
            self.fwd.bind(size=lambda w, v: setattr(w, "text_size", v))
            self.add_widget(self.fwd)

        self.quote = None
        reply = ext.get("reply")
        if isinstance(reply, dict):
            who = screen.author_name(reply.get("from"), outgoing)
            snippet = reply.get("snip") or {"image": "Photo",
                                            "voice": "Voice message"}.get(
                reply.get("kind"), "Message")
            self.quote = QuoteBlock(who, snippet, outgoing,
                                    on_tap=lambda c=reply.get("cid"):
                                    screen.jump_to_cid(c))
            self.add_widget(self.quote)

        self.media = None
        media = ext.get("media")
        if msg.get("kind") == "image" and isinstance(media, dict):
            self.media = ImageContent(msg, media, screen)
        elif msg.get("kind") == "voice" and isinstance(media, dict):
            self.media = VoiceContent(msg, media, screen)
        if self.media is not None:
            self.add_widget(self.media)

        text = self._display_text(msg, media)
        self.body = make_body(text, fs(17)) if text else None
        if self.body is not None:
            if isinstance(self.body, SelectableText):
                self.body.bind(minimum_height=self._body_min_height)
            self.add_widget(self.body)

        self.reactions = ReactionBar()
        self._react_row = AnchorLayout(anchor_x="left", anchor_y="top",
                                       size_hint_y=None, height=0)
        self._react_row.add_widget(self.reactions)
        self._set_reactions(msg)

        self.meta = Label(text=self._stamp_text(msg), markup=True,
                          font_size=fs(12.5), color=C(T.text_3),
                          halign="right", valign="middle",
                          size_hint=(None, None), height=line_height(12.5))
        count, tint = self._tick_state(msg, outgoing, ticks_enabled)
        if screen.kind == "self":
            count = 0                    # nothing is sent, so nothing to tick
        self.ticks = TickMarks(count=count, tint=tint,
                               pos_hint={"center_y": .5})
        self.star = Widget(size_hint=(None, None),
                           size=(dp(12), line_height(12.5)))
        self.star.bind(pos=self._draw_star, size=self._draw_star)
        self.meta_box = BoxLayout(orientation="horizontal", spacing=dp(5),
                                  size_hint=(None, None),
                                  height=line_height(12.5))
        self.meta_box.add_widget(self.star)
        self.meta_box.add_widget(self.meta)
        self.meta_box.add_widget(self.ticks)
        meta_row = AnchorLayout(anchor_x="right", size_hint_y=None,
                                height=line_height(12.5))
        meta_row.add_widget(self.meta_box)
        self.add_widget(meta_row)
        self._draw_star()

    # ---- content -------------------------------------------------------------
    @staticmethod
    def _display_text(msg: dict, media) -> str:
        """A photo shows its caption, a voice note nothing — their readable
        `body` is for older clients, previews and exports."""
        if msg.get("kind") == "image" and isinstance(media, dict):
            return media.get("cap", "")
        if msg.get("kind") == "voice" and isinstance(media, dict):
            return ""
        return msg["body"]

    def _set_reactions(self, msg: dict) -> None:
        reactions = msg.get("reactions") or []
        self.reactions.set(reactions, self.screen.app.keys.address,
                           lambda e, m=msg: self.screen.on_reaction_tap(m, e))
        row = self._react_row
        if reactions and row.parent is None:
            # Just above the time: the meta row, when present, is last.
            self.add_widget(row, index=1 if hasattr(self, "meta_box") else 0)
        elif not reactions and row.parent is not None:
            self.remove_widget(row)

    def _draw_star(self, *_a):
        self.star.canvas.clear()
        if not self.msg.get("starred"):
            self.star.width = 0
            return
        self.star.width = dp(12)
        with self.star.canvas:
            draw_icon("star_filled", (self.star.x + self.star.width / 2), (self.star.y + self.star.height / 2),
                      dp(11), C(T.accent))

    @staticmethod
    def _stamp_text(msg: dict) -> str:
        """The time, plus a nudge on incoming messages still unread (only
        ever left so when auto-read is off)."""
        stamp = pretty_time(msg["created_at"])
        if msg["direction"] == "in" and not msg["read"]:
            return "[i][color=%s]tap to mark read[/color][/i]   %s" % (
                T.accent.lstrip("#"), stamp)
        return stamp

    @staticmethod
    def _tick_state(msg: dict, outgoing: bool, ticks_enabled: bool) -> tuple:
        """(tick count, colour). Outgoing ticks come from the relay's
        receipts (in a group: the slowest member's); incoming ones from
        what this phone knows — it holds the message (2), and whether you
        read it (3). Either side turning ticks off caps both at one."""
        if outgoing:
            status = msg["status"]
        else:
            status = 3 if msg["read"] else 2
        if not ticks_enabled:
            status = min(status, 1)
        if status <= 0:
            return 1, T.tick_grey
        return min(status, 3), T.tick

    def update(self, msg: dict, ticks_enabled: bool) -> None:
        """Repaint in place after a status/read/star/media/reaction change."""
        outgoing = msg["direction"] == "out"
        self.msg = msg
        self.meta.text = self._stamp_text(msg)
        if self.screen.kind != "self":
            self.ticks.set_state(*self._tick_state(msg, outgoing,
                                                   ticks_enabled))
        if self.media is not None:
            media = message_ext(msg).get("media")
            if isinstance(media, dict):
                self.media.media = media
            self.media.refresh(msg)
        before = {c.emoji: c.label.text for c in self.reactions.chips}
        self._set_reactions(msg)
        self._draw_star()
        if self._last_width:
            self.layout_for(self._last_width)
        pending = self.screen.take_pending_pop(msg.get("client_msg_id"))
        for chip in self.reactions.chips:
            if chip.emoji == pending or (pending is None and
                                         before.get(chip.emoji) !=
                                         chip.label.text and
                                         chip.emoji not in before):
                chip.animate_in()

    def _body_min_height(self, _w, value: float) -> None:
        """Safety net for desktop's selectable text: if, AT ITS FINAL WIDTH,
        it wants more room than measured, grow rather than clip. Readings
        from before then (a TextInput starts 100px wide) are ignored."""
        if self.body is None or not self._last_width or \
                self.body.width < self._text_w:
            return
        floor = value if value > self._text_h + 0.5 else 0.0
        if abs(floor - self._body_floor) > 0.5:
            self._body_floor = floor
            self.layout_for(self._last_width)

    def layout_for(self, available: float) -> None:
        """Size every part, then the bubble to fit them: the widest part
        decides, capped at 80% of the row."""
        self._last_width = available
        max_w = max(dp(150), available * 0.80 - dp(24))
        parts = []
        widest = dp(56)
        if self.author is not None:
            self.author.size = (min(max_w, self.author.natural_width),
                                self.author.height)
            widest = max(widest, self.author.width)
            parts.append(self.author.height)
        if self.media is not None:
            mw, mh = self.media.natural_size(max_w)
            self.media.size = (mw, mh)
            widest = max(widest, mw)
            parts.append(mh)
        if self.body is not None:
            wrap = self.media.width if isinstance(self.media,
                                                  ImageContent) else max_w
            text_w, text_h = measure_text(self.body.text, self.body.font_size,
                                          wrap)
            self._text_w, self._text_h = text_w, text_h
            if isinstance(self.body, SelectableText):
                self.body.width = text_w + dp(2)
                self.body.height = max(text_h, self._body_floor)
                self.body._unscroll()
            else:
                self.body.width = text_w
                self.body.text_size = (text_w + 0.5, None)
                self.body.height = text_h
            widest = max(widest, self.body.width)
            parts.append(self.body.height)
        if self.quote is not None:
            widest = max(widest, min(max_w, self.quote.natural_width()))
        if self.fwd is not None:
            widest = max(widest, measure_text("Forwarded", fs(14), dp(300),
                                              italic=True)[0])
        rw, rh = self.reactions.layout_for(max_w)
        self._react_row.height = rh
        widest = max(widest, rw)
        self.meta.texture_update()
        self.meta.size = (self.meta.texture_size[0], line_height(12.5))
        meta_w = self.meta.width + self.ticks.width + self.star.width
        meta_w += dp(5) * (1 + (1 if self.ticks.width else 0))
        self.meta_box.width = meta_w
        widest = max(widest, meta_w)
        if self.quote is not None:
            self.quote.width = widest
            parts.append(self.quote.height)
        if self.fwd is not None:
            self.fwd.width = widest
            parts.append(self.fwd.height)
        if self._react_row.parent is not None:
            parts.append(rh)
        parts.append(line_height(12.5))             # the meta row
        pad_l, pad_t, pad_r, pad_b = self.padding
        self.width = widest + pad_l + pad_r
        self.height = (sum(parts) + self.spacing * (len(self.children) - 1)
                       + pad_t + pad_b)


class BubbleRow(AnchorLayout):
    """A full-width row holding one bubble, and the owner of its touches.

    Long-press (or right-click) selects the message — the app bar switches
    to its actions and the reaction strip rises over it; while anything is
    selected a tap toggles a message in or out instead.
    """

    LONG_PRESS = 0.42                     # seconds

    def __init__(self, bubble: MessageBubble, outgoing: bool, screen, **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(48))
        kw.setdefault("padding", [dp(12), dp(3), dp(12), dp(3)])
        super().__init__(anchor_x="right" if outgoing else "left",
                         anchor_y="center", **kw)
        self.bubble, self.screen = bubble, screen
        self.msg_id = bubble.msg["id"]
        with self.canvas.before:
            self._tint = Color(*T.select)
            self._wash = Rectangle()
        self._tint.a = 0
        self.bind(pos=self._sync, size=self._sync)
        self.add_widget(bubble)
        bubble.bind(height=lambda _w, v: setattr(self, "height", v + dp(6)))
        self.bind(width=lambda _w, v: bubble.layout_for(v))
        self._press = None

    def _sync(self, *_a):
        self._wash.pos, self._wash.size = self.pos, self.size

    def set_selected(self, selected: bool) -> None:
        self._tint.a = T.select[3] if selected else 0

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos) or touch.is_mouse_scrolling:
            return super().on_touch_down(touch)
        if self.screen.selecting:
            self.screen.toggle_selection(self.msg_id)
            return True
        if getattr(touch, "button", "left") == "right":
            self.screen.start_selection(self.msg_id)
            return True
        touch.grab(self)
        self._press = (touch.uid, touch.pos, Clock.schedule_once(
            lambda _dt: self._long_press(touch), self.LONG_PRESS))
        super().on_touch_down(touch)       # children: quote, play, chips
        return True

    def _long_press(self, touch):
        if self._press and self._press[0] == touch.uid:
            self._press = None
            touch.ud["fext_long_press"] = True
            self.screen.start_selection(self.msg_id)

    def on_touch_move(self, touch):
        if self._press and self._press[0] == touch.uid:
            ox, oy = self._press[1]
            if abs(touch.x - ox) > dp(10) or abs(touch.y - oy) > dp(10):
                self._press[2].cancel()
                self._press = None
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            pending, self._press = self._press, None
            if pending:
                pending[2].cancel()
                if not touch.ud.get("fext_long_press"):
                    self.screen.on_bubble_tap(self.msg_id)
            return True
        return super().on_touch_up(touch)


class EventRow(BoxLayout):
    """A line in the margin of a group's history: who joined, who left,
    what it is now called. Italic, centred, not a message."""

    def __init__(self, msg: dict, **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("padding", [dp(40), dp(6), dp(40), dp(6)])
        super().__init__(**kw)
        self.msg = msg
        self.label = InfoLabel(msg["body"], color=T.text_3, italic=True,
                               font_size=fs(15), halign="center")
        self.add_widget(self.label)
        self.label.bind(height=lambda _w, v: setattr(self, "height",
                                                     v + dp(12)))


class DayDivider(BoxLayout):
    """A date set in capitals between hairlines — the rhythm of days."""

    def __init__(self, text: str, **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(46))
        kw.setdefault("padding", [dp(28), 0])
        kw.setdefault("spacing", dp(12))
        super().__init__(**kw)
        self.add_widget(Rule(pos_hint={"center_y": .5}))
        caps = CapsLabel(text, size=10.5, color=T.text_3, tracking=0.18,
                         halign="center", size_hint=(None, None),
                         pos_hint={"center_y": .5})
        caps.width = caps.natural_width + dp(2)
        self.add_widget(caps)
        self.add_widget(Rule(pos_hint={"center_y": .5}))


class JumpToBottomButton(Concealable, ButtonBehavior, Widget):
    """'Back to the latest' — appears when scrolled away from the bottom,
    with a count of what arrived meanwhile (the view is never yanked)."""

    def __init__(self, on_tap=None, **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("size", (TOUCH, TOUCH))
        super().__init__(**kw)
        self._count = 0
        self.opacity = 0
        self.conceal(True)
        self.bind(pos=self._sync, size=self._sync, state=self._sync)
        if on_tap is not None:
            self.bind(on_release=lambda _w: on_tap())
        self._badge = Label(text="", bold=True, font_size=fs(12),
                            color=C(T.on_alert), size_hint=(None, None),
                            size=(dp(20), dp(20)), opacity=0)
        self.add_widget(self._badge)
        self._sync()

    def _sync(self, *_a):
        self.canvas.before.clear()
        d = dp(42)
        cx, cy = self.x + self.width / 2, self.y + self.height / 2
        with self.canvas.before:
            Color(*C(T.press if self.state == "down" else T.surface_hi))
            SmoothEllipse(pos=(cx - d / 2, cy - d / 2), size=(d, d))
            Color(*C(T.rule_strong))
            SmoothLine(circle=(cx, cy, d / 2 - .5), width=1.0)
            draw_icon("down", cx, cy, d * .5, C(T.text_2), width=dp(.9))
            if self._count > 0:
                Color(*C(T.alert))
                SmoothEllipse(pos=(cx + dp(6), cy + dp(6)),
                              size=(dp(20), dp(20)))
        self._badge.center = (cx + dp(16), cy + dp(16))

    def set_visible(self, visible: bool) -> None:
        target = 1.0 if visible else 0.0
        self.conceal(not visible)
        if abs(self.opacity - target) < 0.01:
            return
        Animation.cancel_all(self, "opacity")
        if motion(1):
            Animation(opacity=target, d=0.15).start(self)
        else:
            self.opacity = target

    def set_count(self, count: int) -> None:
        count = max(0, int(count))
        if count == self._count:
            return
        self._count = count
        self._badge.text = str(min(count, 99)) if count else ""
        self._badge.opacity = 1 if count else 0
        self._sync()


EMOJI_SETS = (
    ("Faces",
     "😀😃😄😁😆😅🤣😂🙂🙃😉😊😇🥰😍🤩😘😗😚😙😋😛😜🤪"
     "😝🤑🤗🤭🤫🤔🤐🤨😐😑😶😏😒🙄😬😌😔😪🤤😴😷🤒🤕🤢"
     "🤮🥵🥶🥴😵🤯🤠🥳😎🤓🧐😕😟🙁😮😯😲😳🥺😦😧😨😰😥"
     "😢😭😱😖😣😞😓😩😫🥱😤😡😠🤬😈👿💀💩🤡👻👽🤖"),
    ("People",
     "👋🤚🖐✋🖖👌🤌🤏✌🤞🤟🤘🤙👈👉👆🖕👇☝👍👎✊👊🤛"
     "🤜👏🙌👐🤲🤝🙏✍💅🤳💪🦾🦵🦶👂🦻👃🧠🫀🫁🦷🦴👀👁"
     "👅👄💋🩸👶🧒👦👧🧑👱👨🧔👩🧓👴👵🙍🙎🙅🙆💁🙋🧏🙇"
     "🤦🤷👮🕵💂🥷👷🤴👸👳👲🧕🤵👰🤰🤱👼🎅🤶🦸🦹🧙🧚🧛"
     "🧜🧝🧞🧟💆💇🚶🧍🧎🏃💃🕺👯🧖🧗🤺🏇⛷🏂🏌🏄🚣🏊⛹"
     "🏋🚴🚵🤸🤼🤽🤾🤹🧘"),
    ("Nature",
     "🐶🐱🐭🐹🐰🦊🐻🐼🐨🐯🦁🐮🐷🐸🐵🙈🙉🙊🐒🐔🐧🐦🐤🦆"
     "🦅🦉🦇🐺🐗🐴🦄🐝🐛🦋🐌🐞🐜🕷🦂🐢🐍🦎🐙🦑🦀🐡🐠🐟"
     "🐬🐳🐋🦈🐊🐅🐆🦓🦍🐘🦏🐪🐫🦒🐃🐂🐄🐎🐖🐏🐑🦙🐐🦌"
     "🐕🐩🐈🐓🦃🦚🦜🦢🕊🐇🦝🦨🦡🦦🦥🐁🐀🐿🦔🐾🐉🐲🌵🎄"
     "🌲🌳🌴🌱🌿☘🍀🎍🎋🍃🍂🍁🍄🐚🌾💐🌷🌹🥀🌺🌸🌼🌻🌞"
     "🌝🌛🌜🌚🌕🌖🌗🌘🌑🌒🌓🌔🌙🌎🌍🌏🪐💫⭐🌟✨⚡☄💥"
     "🔥🌪🌈☀🌤⛅🌥☁🌦🌧⛈🌩🌨❄☃⛄🌬💨💧💦☔☂🌊🌫"),
    ("Food",
     "🍏🍎🍐🍊🍋🍌🍉🍇🍓🫐🍈🍒🍑🥭🍍🥥🥝🍅🍆🥑🥦🥬🥒🌶"
     "🫑🌽🥕🧄🧅🥔🍠🥐🥯🍞🥖🥨🧀🥚🍳🧈🥞🧇🥓🥩🍗🍖🌭🍔"
     "🍟🍕🥪🥙🧆🌮🌯🥗🥘🥫🍝🍜🍲🍛🍣🍱🥟🍤🍙🍚🍘🍥🥠🥮"
     "🍢🍡🍧🍨🍦🥧🧁🍰🎂🍮🍭🍬🍫🍿🍩🍪🌰🥜🍯🥛🍼☕🍵🧃"
     "🥤🍶🍺🍻🥂🍷🥃🍸🍹🧉🍾🧊🥄🍴🍽🥣🥡🥢🧂"),
    ("Things",
     "⌚📱💻⌨🖥🖨🖱💽💾💿📀📷📸📹🎥📞☎📺📻🎙⏱⏰⌛⏳"
     "📡🔋🔌💡🔦🕯💸💵💴💶💷💰💳💎⚖🧰🔧🔨⚒🛠⛏🔩⚙🧱"
     "⛓🧲🔫💣🧨🔪🗡⚔🛡🔮📿🧿💈⚗🔭🔬🩺💊💉🧬🦠🧪🌡🧹"
     "🧺🧻🚽🛁🔑🗝🚪🛋🛏🧸🖼🛍🛒🎁🎈🎏🎀🎊🎉🎎🏮🎐🧧✉"
     "📩📨📧💌📥📤📦🏷📪📫📬📭📮📯📜📃📄📑🧾📊📈📉🗒🗓"
     "📆📅📇🗃🗳🗄📋📁📂🗂🗞📰📓📔📒📕📗📘📙📚📖🔖🧷🔗"
     "📎🖇📐📏🧮📌"),
    ("Signs",
     "❤🧡💛💚💙💜🖤🤍🤎💔❣💕💞💓💗💖💘💝💟☮✝☪🕉☸"
     "✡🔯🕎☯☦🛐⛎♈♉♊♋♌♍♎♏♐♑♒♓🆔⚛☢☣📴"
     "📳🈶🈚🈸🈺🈷✴🆚💮🉐㊙㊗🈴🈵🈹🈲🅰🅱🆎🆑🅾🆘❌⭕"
     "🛑⛔📛🚫💯💢♨🚷🚯🚳🚱🔞📵🚭❗❕❓❔‼⁉🔅🔆〽⚠"
     "🚸🔱⚜🔰♻✅🈯💹❇✳❎🌐💠Ⓜ🌀💤🏧🚾♿🅿🈳🈂🛂🛃"),
)
RECENT_EMOJI_MAX = 24


class EmojiKey(ButtonBehavior, Label):
    def __init__(self, emoji: str, on_pick, **kw):
        if "size" in kw:
            kw.setdefault("size_hint", (None, None))
        super().__init__(text=emoji, font_size=sp(25), color=C(T.text),
                         **kw)
        with self.canvas.before:
            self._press = Color(0, 0, 0, 0)
            self._disc = SmoothEllipse()
        self.bind(pos=self._sync, size=self._sync,
                  state=lambda _w, s: setattr(
                      self._press, "rgba", C(T.press) if s == "down"
                      else (0, 0, 0, 0)),
                  on_release=lambda _w: on_pick(emoji))

    def _sync(self, *_a):
        d = dp(42)
        self._disc.pos = ((self.x + self.width / 2) - d / 2, (self.y + self.height / 2) - d / 2)
        self._disc.size = (d, d)


class EmojiPanel(Concealable, BoxLayout):
    """Emoji, by kind, where the keyboard was: capitals tabs over a grid.
    Picks go into the message being written, or — opened from the
    reaction strip — become a reaction."""

    HEIGHT = dp(292)

    def __init__(self, on_pick: Callable[[str], None], **kw):
        kw.setdefault("orientation", "vertical")
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", 0)
        super().__init__(**kw)
        self.on_pick = on_pick
        self.opacity = 0
        self.conceal(True)
        paint_round(self, T.surface, radius=0)
        hairline(self, "top")
        recent = [e for e in load_prefs().get("recent_emoji", [])
                  if isinstance(e, str) and sanitize_emoji(e)]
        self._recent = recent[:RECENT_EMOJI_MAX]
        options = ([("Recent", "Recent")] if self._recent else []) + \
            [(name, name) for name, _set in EMOJI_SETS]
        self._sets = dict(EMOJI_SETS)
        tab_scroll = ScrollView(size_hint_y=None, height=TOUCH,
                                do_scroll_y=False, bar_width=0)
        self.tabs = Segmented(options, options[0][0], self._show,
                              size_hint_x=None)
        self.tabs.width = max(dp(84) * len(options), Window.width)
        tab_scroll.add_widget(self.tabs)
        self.add_widget(tab_scroll)
        self.grid_scroll = ScrollView(do_scroll_x=False, bar_width=dp(3))
        self.add_widget(self.grid_scroll)
        self._grids: dict = {}
        self._show(options[0][0])

    def _show(self, name: str) -> None:
        grid = self._grids.get(name)
        if grid is None or name == "Recent":
            items = self._recent if name == "Recent" else self._sets[name]
            grid = GridLayout(cols=7, size_hint=(1, None),
                              padding=[dp(4), dp(4)],
                              row_default_height=TOUCH,
                              row_force_default=True)
            grid.bind(minimum_height=grid.setter("height"),
                      width=self._reflow)
            for emoji in items:
                grid.add_widget(EmojiKey(emoji, self._pick))
            self._grids[name] = grid
        self.grid_scroll.clear_widgets()
        if grid.parent is not None:
            grid.parent.remove_widget(grid)
        self.grid_scroll.add_widget(grid)
        self._reflow(grid, grid.width)
        self.grid_scroll.scroll_y = 1

    @staticmethod
    def _reflow(grid, width) -> None:
        """As many 48dp keys a row as fit, the slack spread between them."""
        cols = max(5, int((width - dp(8)) / TOUCH))
        if grid.cols != cols:
            grid.cols = cols

    def _pick(self, emoji: str) -> None:
        recent = [emoji] + [e for e in self._recent if e != emoji]
        self._recent = recent[:RECENT_EMOJI_MAX]
        save_pref("recent_emoji", self._recent)
        self.on_pick(emoji)

    def set_open(self, on: bool) -> None:
        self.height = self.HEIGHT if on else 0
        self.opacity = 1 if on else 0
        self.conceal(not on)

    @property
    def is_open(self) -> bool:
        return self.height > 0


class ReactionStrip(Concealable, BoxLayout):
    """The quick reactions, floating over the message you held: tap one to
    react (tap yours again to take it back); + opens every emoji."""

    def __init__(self, on_pick: Callable[[str], None],
                 on_more: Callable[[], None], **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("height", dp(54))
        kw.setdefault("padding", [dp(6), dp(3)])
        super().__init__(**kw)
        self.keys = {}
        for emoji in QUICK_REACTIONS:
            key = EmojiKey(emoji, on_pick, size=(dp(46), dp(48)))
            key.font_size = sp(24)
            self.keys[emoji] = key
            self.add_widget(key)
        self.add_widget(IconButton("plus", label="More reactions",
                                   size=(dp(46), dp(48)),
                                   on_release=lambda _w: on_more()))
        self.width = dp(46) * (len(QUICK_REACTIONS) + 1) + dp(12)
        with self.canvas.before:
            Color(0, 0, 0, .30 if T.dark else .10)
            self._shadow = RoundedRectangle(radius=[dp(27)])
            Color(*C(T.surface_hi))
            self._bg = SmoothRoundedRectangle(radius=[dp(27)])
            self._mine_col = Color(0, 0, 0, 0)
            self._mine = SmoothEllipse()
        with self.canvas.after:
            Color(*C(T.rule_strong))
            self._edge = SmoothLine(width=1.0)
        self.bind(pos=self._sync, size=self._sync)
        self.opacity = 0
        self.conceal(True)
        self._current = ""

    def _sync(self, *_a):
        self._shadow.pos = (self.x, self.y - dp(2))
        self._shadow.size = self.size
        self._bg.pos, self._bg.size = self.pos, self.size
        self._edge.rounded_rectangle = (self.x + .5, self.y + .5,
                                        self.width - 1, self.height - 1,
                                        dp(27))
        key = self.keys.get(self._current)
        if key is not None:
            d = dp(40)
            self._mine_col.rgba = C(mix(T.accent, T.surface_hi, .70))
            self._mine.pos = ((key.x + key.width / 2) - d / 2, (key.y + key.height / 2) - d / 2)
            self._mine.size = (d, d)
        else:
            self._mine_col.rgba = (0, 0, 0, 0)

    def show(self, current: str) -> None:
        self._current = current or ""
        self.conceal(False)
        self._sync()
        Animation.cancel_all(self, "opacity")
        if motion(1):
            self.opacity = 0
            Animation(opacity=1, d=0.14).start(self)
        else:
            self.opacity = 1

    def hide(self) -> None:
        self.conceal(True)
        Animation.cancel_all(self, "opacity")
        self.opacity = 0


def fold(widget, shown: bool, width: float, animate: bool = True) -> None:
    """Show a part of a row, or fold it away: its width runs to nothing
    (its neighbours slide over) as it fades."""
    Animation.cancel_all(widget, "width", "opacity")
    w, o = (width, 1.0) if shown else (0.0, 0.0)
    d = motion(0.18) if animate else 0
    if not d or (abs(widget.width - w) < .5 and abs(widget.opacity - o) < .01):
        widget.width, widget.opacity = w, o
        return
    Animation(width=w, opacity=o, d=d, t="out_cubic").start(widget)


class ComposerInput(BoxLayout):
    """The message field: a pill holding the emoji key, the text and the
    paperclip — and the camera, while nothing is written.

    The text fills the pill's whole height, so a click anywhere on it that
    is not one of its keys puts the cursor in the text.
    """

    KEY = dp(44)

    def __init__(self, hint="Message", on_enter=None, on_emoji=None,
                 on_attach=None, on_camera=None, **kw):
        kw.setdefault("padding", [dp(2), 0, dp(2), 0])
        super().__init__(**kw)
        paint_round(self, T.surface_hi, radius=dp(24))

        def key(icon, label, action):
            return IconButton(icon, label=label, size=(self.KEY, TOUCH),
                              pos_hint={"center_y": .5},
                              on_release=lambda _w: action and action())
        self.emoji_button = key("smile", "Emoji", on_emoji)
        self.input = TextInput(
            hint_text=hint, multiline=False, background_normal="",
            background_active="", background_color=(0, 0, 0, 0),
            foreground_color=C(T.text), hint_text_color=C(T.text_3),
            cursor_color=C(T.accent), selection_color=C(T.accent)[:3] + [.3],
            font_size=fs(17), write_tab=False, text_validate_unfocus=False,
            padding=[dp(4), 0, dp(4), 0])
        self.input.bind(size=self._centre_text)
        if on_enter is not None:
            self.input.bind(on_text_validate=lambda _w: on_enter())
        self.attach_button = key("attach", "Attach a photo", on_attach)
        self.camera_button = key("camera", "Take a photo", on_camera)
        for part in (self.emoji_button, self.input, self.attach_button,
                     self.camera_button):
            self.add_widget(part)

    def _centre_text(self, *_a) -> None:
        pad = max(0.0, (self.input.height - self.input.line_height) / 2.0)
        self.input.padding = [dp(4), pad, dp(4), pad]

    def show_camera(self, shown: bool, animate: bool = True) -> None:
        fold(self.camera_button, shown, self.KEY, animate)

    @property
    def text(self) -> str:
        return self.input.text

    @text.setter
    def text(self, value: str) -> None:
        # A single-line TextInput keeps its horizontal scroll across text
        # changes: switching from a chat with a long draft to an empty one
        # left the hint scrolled half out of view.
        self.input.text = value
        self.input.scroll_x = 0
        self.input.cursor = self.input.get_cursor_from_index(len(value))


class LiveLevels(Widget):
    """The microphone's level while recording: the latest readings as bars
    running in from the right."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._levels: list = []
        self.bind(pos=self._redraw, size=self._redraw)

    def update(self, levels: list) -> None:
        self._levels = levels
        self._redraw()

    def _redraw(self, *_a) -> None:
        self.canvas.clear()
        step, bar = dp(3.5), dp(1.5)
        bars = max(1, int(self.width / step))
        recent = self._levels[-bars:]
        peak = max([1500] + recent)
        with self.canvas:
            Color(*C(T.accent))
            x = self.right - len(recent) * step
            for level in recent:
                h = max(dp(2), self.height * .7 * min(1.0, .08 + level / peak))
                Rectangle(pos=(x, self.y + (self.height - h) / 2),
                          size=(bar, h))
                x += step


class ConversationScreen(Screen):
    """A conversation: its head (or, while messages are held, their
    actions), the messages bottom-anchored over the page, the reaction strip
    over a held message, and the composer — with the emoji panel beneath
    it. Direct chats, groups and notes to self all live here."""

    def __init__(self, app: "FextApp", **kw):
        super().__init__(name="conversation", **kw)
        self.app = app
        # ---- incremental-render bookkeeping ---------------------------------
        self._bubbles: dict = {}          # message id -> MessageBubble
        self._rendered_ids: list = []
        self._rendered_sigs: dict = {}
        self._rendered_peer: Optional[str] = None
        self._rendered_ticks: Optional[bool] = None
        self._last_day: Optional[str] = None
        self._unseen = 0
        self._scroll_trigger = None
        self._stick_to_bottom = True
        self._last_content_h = 0.0
        self.has_older = False
        self._more_button = Btn("Earlier messages", kind="text",
                                size_hint_y=None,
                                on_release=lambda _w:
                                self.app.load_older_messages())
        self.selected: dict = {}          # local id -> message, highlighted
        self._rows: dict = {}             # local id -> BubbleRow
        self._pending_pop: dict = {}      # cid -> emoji to animate in
        self.reply_to: Optional[dict] = None
        self.recorder = None
        self._rec_tick = None
        self.peer: Optional[str] = None
        self.kind = "dm"
        self.group: Optional[dict] = None
        self.peer_name_text = ""
        self._emoji_target: Optional[int] = None   # react via the panel
        self._drafts: dict = {}                    # peer -> unsent text
        root = BoxLayout(orientation="vertical")
        paint_round(root, T.bg, radius=0)
        self.add_widget(root)

        # ---- app bar: normal mode and highlight (selection) mode -------------
        self.bar = FloatLayout(size_hint_y=None,
                               height=max(dp(62), line_height(19) +
                                          line_height(14) + dp(14)))
        hairline(self.bar, "bottom")
        root.add_widget(self.bar)
        self.normal_bar = BoxLayout(padding=[dp(2), 0, dp(2), 0],
                                    spacing=dp(4), size_hint=(1, 1),
                                    pos_hint={"x": 0, "y": 0})
        self.normal_bar.add_widget(IconButton(
            "back", label="Back", pos_hint={"center_y": .5},
            on_release=lambda _w: self.app.go_back_to_chats()))
        self.peer_avatar_holder = AnchorLayout(size_hint_x=None, width=dp(42))
        self.normal_bar.add_widget(self.peer_avatar_holder)
        titles = TappableBox(on_tap=lambda: self.app.open_chat_info(),
                             padding=[dp(10), dp(8), 0, dp(8)])
        self.peer_name = single_line("", fs(19), T.text, bold=True,
                                     size_hint_y=None, height=line_height(19))
        self.peer_sub = single_line("", fs(14), T.text_3, italic=True,
                                    size_hint_y=None, height=line_height(14))
        titles.add_widget(self.peer_name)
        titles.add_widget(self.peer_sub)
        self.normal_bar.add_widget(titles)
        self.more_button = IconButton(
            "more", label="More", pos_hint={"center_y": .5},
            on_release=lambda w: self.open_menu(w))
        self.normal_bar.add_widget(self.more_button)

        self.select_bar = BoxLayout(padding=[dp(2), 0, dp(2), 0],
                                    size_hint=(1, 1),
                                    pos_hint={"x": 0, "y": 0})
        self.select_bar.add_widget(IconButton(
            "close", label="Cancel selection", pos_hint={"center_y": .5},
            on_release=lambda _w: self.clear_selection()))
        self.select_count = Label(text="1", font_size=fs(20),
                                  color=C(T.text), halign="left",
                                  valign="middle", size_hint_x=None,
                                  width=dp(36))
        self.select_count.bind(size=lambda w, v: setattr(w, "text_size", v))
        self.select_bar.add_widget(self.select_count)
        self.select_bar.add_widget(Widget())
        self.actions: dict = {}
        for name, handler, label in (
                ("reply", self._act_reply, "Reply"),
                ("copy", self._act_copy, "Copy"),
                ("star", self._act_star, "Star"),
                ("forward", self._act_forward, "Forward"),
                ("share", self._act_share, "Share"),
                ("delete", self._act_delete, "Delete")):
            button = IconButton(name, label=label, size=(dp(42), TOUCH),
                                pos_hint={"center_y": .5},
                                on_release=lambda _w, h=handler: h())
            self.actions[name] = button
            self.select_bar.add_widget(button)
        self.bar.add_widget(self.normal_bar)

        # ---- messages --------------------------------------------------------
        self.stack = FloatLayout()
        self.scroll = ScrollView(bar_width=dp(3), size_hint=(1, 1),
                                 pos_hint={"x": 0, "y": 0}, do_scroll_x=False)
        self.messages = BoxLayout(orientation="vertical", size_hint_y=None,
                                  padding=[0, dp(10), 0, dp(12)],
                                  spacing=dp(2))
        # Keeps a short conversation resting on the composer instead of up
        # under the app bar — see _sync_filler. Always the FIRST child.
        self._filler = Widget(size_hint_y=None, height=0)
        self._syncing_filler = False
        self.messages.add_widget(self._filler)
        self.messages.bind(minimum_height=self.messages.setter("height"))
        self.messages.bind(height=lambda *_a: self._on_content_height())
        self.messages.bind(minimum_height=lambda *_a: self._sync_filler())
        self.scroll.bind(height=lambda *_a: self._sync_filler())
        self.scroll.add_widget(self.messages)
        self.stack.add_widget(self.scroll)
        self.jump_button = JumpToBottomButton(on_tap=self.jump_to_latest,
                                              pos_hint={"right": 0.97,
                                                        "y": 0.02})
        self.stack.add_widget(self.jump_button)
        self.strip = ReactionStrip(self._strip_pick, self._strip_more)
        self.stack.add_widget(self.strip)
        self.scroll.bind(scroll_y=lambda *_a: self._on_scrolled())
        self.scroll.bind(scroll_y=lambda *_a: self._place_strip(),
                         size=lambda *_a: self._place_strip())
        root.add_widget(self.stack)

        # ---- reply preview (while composing a reply) -------------------------
        self.reply_bar = ConcealableBox(size_hint_y=None, height=0,
                                        opacity=0,
                                        padding=[dp(16), dp(6), dp(4), dp(6)],
                                        spacing=dp(12))
        self.reply_bar.conceal(True)
        hairline(self.reply_bar, "top")
        reply_icon = Widget(size_hint_x=None, width=dp(22))
        reply_icon.bind(pos=lambda w, *_a: self._paint_reply_icon(w),
                        size=lambda w, *_a: self._paint_reply_icon(w))
        self.reply_bar.add_widget(reply_icon)
        texts = BoxLayout(orientation="vertical", padding=[dp(10), dp(2)])
        with texts.canvas.before:
            Color(*C(T.accent))
            self._reply_accent = Rectangle()
        texts.bind(pos=lambda w, *_a: self._sync_reply_accent(w),
                   size=lambda w, *_a: self._sync_reply_accent(w))
        self.reply_author = CapsLabel("", size=10.5, color=T.accent)
        self.reply_snip = single_line("", fs(15), T.text_2, italic=True)
        texts.add_widget(self.reply_author)
        texts.add_widget(self.reply_snip)
        self.reply_bar.add_widget(texts)
        self.reply_bar.add_widget(IconButton(
            "close", label="Cancel reply", pos_hint={"center_y": .5},
            on_release=lambda _w: self.cancel_reply()))
        root.add_widget(self.reply_bar)

        # ---- composer --------------------------------------------------------
        # Nothing written: [you] ( ☺  Message   📎 📷 ) [mic]
        # Writing:              ( ☺  the text…      📎 ) [send]
        # The avatar and the camera fold away, and the microphone turns
        # into the send button.
        self.composer = BoxLayout(size_hint_y=None,
                                  height=max(dp(64), line_height(17) +
                                             dp(30)),
                                  padding=[dp(4), dp(8), dp(4), dp(8)])
        hairline(self.composer, "top")
        self.me_holder = AnchorLayout(size_hint_x=None, width=TOUCH)
        self.entry = ComposerInput(on_enter=self.app.send_current,
                                   on_emoji=self.toggle_emoji,
                                   on_attach=self.pick_image,
                                   on_camera=self.take_photo)
        self.emoji_button = self.entry.emoji_button
        self.attach_button = self.entry.attach_button
        self.camera_button = self.entry.camera_button
        self.action_holder = AnchorLayout(size_hint_x=None, width=dp(56))
        self.action_button = IconButton(
            "send", bg=T.accent, tint=T.on_accent, label="Send",
            icon_size=dp(23), on_release=lambda _w: self._on_action_button())
        self.action_holder.add_widget(self.action_button)
        self.entry.input.bind(text=lambda *_a: self._sync_composer(),
                              focus=self._on_entry_focus)
        self.composer_parts = (self.me_holder, self.entry, self.action_holder)
        for part in self.composer_parts:
            self.composer.add_widget(part)
        # Shown instead of the composer when you can no longer write here.
        self.closed_note = InfoLabel("", color=T.text_3, italic=True,
                                     font_size=fs(15), halign="center",
                                     pos_hint={"center_y": .5})
        # What the composer becomes while recording: discard, the time and
        # the microphone's level, send.
        self.rec_cancel = IconButton(
            "delete", tint=T.alert, label="Discard recording",
            pos_hint={"center_y": .5},
            on_release=lambda _w: self.cancel_recording())
        self.rec_dot = Widget(size_hint_x=None, width=dp(18))
        self.rec_dot.bind(pos=self._paint_rec_dot, size=self._paint_rec_dot)
        self.rec_label = Label(text="", font_size=fs(16), color=C(T.text),
                               halign="left", valign="middle",
                               size_hint_x=None, width=dp(58))
        self.rec_label.bind(size=lambda w, v: setattr(w, "text_size", v))
        self.rec_levels = LiveLevels()
        self.rec_send = IconButton(
            "send", bg=T.accent, tint=T.on_accent, label="Send recording",
            icon_size=dp(23), pos_hint={"center_y": .5},
            on_release=lambda _w: self.finish_recording())
        root.add_widget(self.composer)
        self.emoji_panel = EmojiPanel(self._emoji_picked)
        root.add_widget(self.emoji_panel)
        self._sync_composer(animate=False)

    # ---- header --------------------------------------------------------------
    def set_peer(self, peer: str, contact: dict, unread_count: int,
                 kind: str = "dm", group: Optional[dict] = None) -> None:
        if peer != self.peer:
            self.clear_selection()
            self.cancel_reply()
            self.cancel_recording()
            self.close_emoji()
            if self.peer is not None:
                self._drafts[self.peer] = self.entry.text
            self.entry.text = self._drafts.pop(peer, "")
            self._sync_composer(animate=False)
        self._refresh_me()
        self.peer, self.kind, self.group = peer, kind, group
        if kind == "self":
            name = "Notes to self"
        elif kind == "group":
            name = (group or {}).get("name") or "Group"
        else:
            name = contact.get("username") or short_addr(peer, 14)
        self.peer_name_text = name
        self.peer_avatar_holder.clear_widgets()
        self.peer_avatar_holder.add_widget(Avatar(
            peer, name, diameter=dp(38), kind=kind,
            pos_hint={"center_y": .5}))
        self.peer_name.text = name
        bits = []
        if kind == "self":
            bits.append("kept on this device")
        elif kind == "group":
            members = (group or {}).get("members") or []
            me = self.app.keys.address
            names = ["you"] + [self.author_name(m["a"]) for m in members
                               if m["a"] != me]
            bits.append(", ".join(names) if len(names) <= 4
                        else f"{len(members)} members")
            muted = not (group or {}).get("notify", 1)
            if muted:
                bits.append("muted")
        else:
            bits.append(short_addr(peer, 12))
            if not contact.get("notify", 1):
                bits.append("muted")
        if unread_count and kind != "group" and not contact.get("auto_read",
                                                                1):
            bits.append(f"{unread_count} unread")
        self.peer_sub.text = " · ".join(bits)
        self._sync_closed()

    def _sync_closed(self) -> None:
        """A group you left (or were removed from, or not yet joined) is
        read-only: the composer gives way to a note saying why."""
        note = ""
        if self.kind == "group":
            state = (self.group or {}).get("state")
            note = {"left": "You left this group.",
                    "removed": "You are no longer a member of this group.",
                    "pending": "This group's invitation has not arrived "
                               "yet."}.get(state, "")
        closed = bool(note)
        showing = self.closed_note.parent is not None
        if closed and not showing:
            self.close_emoji()
            self.composer.clear_widgets()
            self.closed_note.text = note
            self.composer.add_widget(self.closed_note)
        elif closed:
            self.closed_note.text = note
        elif showing:
            self.composer.clear_widgets()
            for part in self.composer_parts:
                self.composer.add_widget(part)
            self._sync_composer(animate=False)
        self.more_button.disabled = False

    # ---- bubbles -------------------------------------------------------------
    def render(self, messages: list, ticks_enabled: bool,
               on_tap_unread: Callable, peer: Optional[str] = None) -> None:
        """Incremental render: new messages append, changed ones repaint in
        place, nothing changed costs nothing. A full rebuild only when the
        list's shape changes in a way appending cannot express (another
        chat, history paged in, rows deleted or imported)."""
        signatures = {m["id"]: self._signature(m) for m in messages}
        ids = [m["id"] for m in messages]
        rebuild = (peer != self._rendered_peer
                   or ticks_enabled != self._rendered_ticks
                   or not self._rendered_ids
                   or len(ids) < len(self._rendered_ids)
                   or ids[:len(self._rendered_ids)] != self._rendered_ids)
        if rebuild:
            self._full_render(messages, ticks_enabled, on_tap_unread, peer)
            return
        for msg in messages:
            mid = msg["id"]
            previous = self._rendered_sigs.get(mid)
            if previous is None or previous == signatures[mid]:
                continue
            bubble = self._bubbles.get(mid)
            if bubble is not None:
                bubble.update(msg, ticks_enabled)
                if mid in self.selected:
                    self.selected[mid] = msg
        was_at_bottom = self.at_bottom()
        appended = 0
        sent_by_me = False
        for msg in messages[len(self._rendered_ids):]:
            self._append_bubble(msg, ticks_enabled, on_tap_unread)
            appended += 1
            if msg["direction"] == "out":
                sent_by_me = True
        self._rendered_ids = ids
        self._rendered_sigs = signatures
        if appended:
            if sent_by_me or was_at_bottom:
                self.scroll_to_bottom()
            else:
                self.note_new_messages(appended)
        self._place_strip()

    def _full_render(self, messages: list, ticks_enabled: bool,
                     on_tap_unread: Callable, peer: Optional[str]) -> None:
        self.messages.clear_widgets()
        self.messages.add_widget(self._filler)
        self._bubbles.clear()
        self._rows.clear()
        self._last_day = None
        if not messages:
            self.messages.add_widget(self._empty_state())
        if self._more_button.parent is not None:
            self._more_button.parent.remove_widget(self._more_button)
        if self.has_older:
            self.messages.add_widget(self._more_button)
        for msg in messages:
            self._append_bubble(msg, ticks_enabled, on_tap_unread)
        self._rendered_ids = [m["id"] for m in messages]
        self._rendered_sigs = {m["id"]: self._signature(m)
                               for m in messages}
        self._rendered_peer = peer
        self._rendered_ticks = ticks_enabled
        self._unseen = 0
        self.scroll_to_bottom(animate=False)

    @staticmethod
    def _signature(msg: dict) -> tuple:
        """Everything that, if it changes, means a bubble must repaint."""
        return (msg["status"], msg["read"], msg["body"], msg.get("starred"),
                msg.get("media_state"), msg.get("media_path"), msg.get("ext"),
                tuple(msg.get("reactions") or ()))

    def _append_bubble(self, msg: dict, ticks_enabled: bool,
                       on_tap_unread: Callable) -> None:
        day = str(msg.get("created_at", ""))[:10]
        if day and day != self._last_day:
            self._last_day = day
            self.messages.add_widget(DayDivider(self._day_text(day)))
        if msg.get("kind") == "event":
            self.messages.add_widget(EventRow(msg))
            return
        outgoing = msg["direction"] == "out"
        bubble = MessageBubble(msg, ticks_enabled, self)
        self._bubbles[msg["id"]] = bubble
        row = BubbleRow(bubble, outgoing, self)
        row.set_selected(msg["id"] in self.selected)
        self._rows[msg["id"]] = row
        self.messages.add_widget(row)
        pending = self.take_pending_pop(msg.get("client_msg_id"))
        if pending:
            chip = bubble.reactions.chip_for(pending)
            if chip is not None:
                chip.animate_in()

    # =========================================================================
    #  Reactions
    # =========================================================================
    def take_pending_pop(self, cid: Optional[str]) -> Optional[str]:
        return self._pending_pop.pop(cid, None) if cid else None

    def note_reaction(self, cid: str, reactor: str, emoji: str) -> None:
        """A reaction changed: make it pop, now if its chip is on screen,
        or as soon as it is drawn."""
        if not emoji:
            return
        for bubble in self._bubbles.values():
            if bubble.msg.get("client_msg_id") == cid:
                chip = bubble.reactions.chip_for(emoji)
                if chip is not None:
                    chip.animate_in()
                    return
        self._pending_pop[cid] = emoji

    def on_reaction_tap(self, msg: dict, emoji: str) -> None:
        if self.selecting:
            return
        self.app.react(msg["id"], emoji)

    def _reactable(self, msg: dict) -> bool:
        return bool(msg.get("client_msg_id")) and msg.get("kind") != "event" \
            and self.closed_note.parent is None

    def _strip_pick(self, emoji: str) -> None:
        msgs = list(self.selected.values())
        self.clear_selection()
        if len(msgs) == 1:
            self.app.react(msgs[0]["id"], emoji)

    def _strip_more(self) -> None:
        msgs = list(self.selected.values())
        if len(msgs) != 1:
            return
        self._emoji_target = msgs[0]["id"]
        self.clear_selection()
        self.open_emoji()

    def _place_strip(self, *_a) -> None:
        """Float the strip just above the held message, inside the view."""
        if self.strip.concealed or len(self.selected) != 1:
            return
        row = self._rows.get(next(iter(self.selected)))
        if row is None:
            return
        bubble = row.bubble
        bx, by = bubble.to_window(bubble.x, bubble.top)
        x, y = self.stack.to_widget(bx, by)
        sx = min(max(dp(8), x + bubble.width / 2 - self.strip.width / 2),
                 self.stack.width - self.strip.width - dp(8))
        sy = y + dp(6)
        top = self.stack.top - self.strip.height - dp(6)
        if sy > top:                          # no room above: sit below it
            bx, by = bubble.to_window(bubble.x, bubble.y)
            sy = self.stack.to_widget(bx, by)[1] - self.strip.height - dp(6)
        self.strip.pos = (self.stack.x + sx,
                          max(self.stack.y + dp(6), min(sy, top)))

    # =========================================================================
    #  Highlight (selection) mode
    # =========================================================================
    @property
    def selecting(self) -> bool:
        return bool(self.selected)

    def start_selection(self, msg_id: int) -> None:
        if msg_id not in self.selected:
            self.toggle_selection(msg_id)

    def toggle_selection(self, msg_id: int) -> None:
        bubble = self._bubbles.get(msg_id)
        if bubble is None:
            return
        if msg_id in self.selected:
            del self.selected[msg_id]
            if isinstance(bubble.body, SelectableText):
                bubble.body.cancel_selection()
        else:
            self.selected[msg_id] = bubble.msg
        row = self._rows.get(msg_id)
        if row is not None:
            row.set_selected(msg_id in self.selected)
        self._sync_selection()

    def clear_selection(self) -> None:
        for msg_id in list(self.selected):
            row = self._rows.get(msg_id)
            if row is not None:
                row.set_selected(False)
            bubble = self._bubbles.get(msg_id)
            if bubble is not None and isinstance(bubble.body, SelectableText):
                bubble.body.cancel_selection()
        self.selected.clear()
        self._sync_selection()

    def _sync_selection(self) -> None:
        """Swap the app bar, enable only the actions that apply, and raise
        the reaction strip over a single held message."""
        on = bool(self.selected)
        if on and self.select_bar.parent is None:
            self.bar.remove_widget(self.normal_bar)
            self.bar.add_widget(self.select_bar)
        elif not on and self.normal_bar.parent is None:
            self.bar.remove_widget(self.select_bar)
            self.bar.add_widget(self.normal_bar)
        msgs = list(self.selected.values())
        if len(msgs) == 1 and self._reactable(msgs[0]):
            self.strip.show(self.app.store.reaction_of(
                msgs[0]["peer"], msgs[0]["client_msg_id"],
                self.app.keys.address) if self.app.store else "")
            self._place_strip()
        else:
            self.strip.hide()
        if not on:
            return
        self.select_count.text = str(len(msgs))
        has_text = any(self._text_of(m) for m in msgs)
        self.actions["reply"].disabled = len(msgs) != 1 or \
            self.closed_note.parent is not None
        self.actions["copy"].disabled = not has_text
        self.actions["star"].set_icon(
            "star_filled" if all(m.get("starred") for m in msgs) else "star")
        self.actions["share"].disabled = not (has_text or any(
            m.get("kind") == "image" and m.get("media_path") for m in msgs))
        self.actions["forward"].disabled = any(
            m.get("kind") in ("image", "voice") and not
            ((message_ext(m).get("media") or {}).get("blob") or
             m.get("media_path")) for m in msgs)

    @staticmethod
    def _text_of(msg: dict) -> str:
        kind = msg.get("kind") or "text"
        if kind == "image":
            return (message_ext(msg).get("media") or {}).get("cap", "")
        if kind == "voice":
            return ""
        return msg.get("body", "")

    def _ordered(self) -> list:
        return [self.selected[i] for i in sorted(self.selected)]

    # ---- the six actions -------------------------------------------------------
    def _act_reply(self) -> None:
        if len(self.selected) == 1:
            msg = next(iter(self.selected.values()))
            self.clear_selection()
            self.set_reply(msg)

    def _act_copy(self) -> None:
        msgs = self._ordered()
        if len(msgs) == 1:
            bubble = self._bubbles.get(msgs[0]["id"])
            part = (bubble.body.selection_text if bubble is not None and
                    isinstance(bubble.body, SelectableText) else "")
            text = part or self._text_of(msgs[0])
        else:
            text = "\n".join(
                f"[{pretty_time(m['created_at'])}] "
                f"{self.author_name(self._author_of(m))}: {self._text_of(m)}"
                for m in msgs if self._text_of(m))
        self.clear_selection()
        if text:
            self.app.copy_text(text)

    def _act_star(self) -> None:
        msgs = self._ordered()
        star = not all(m.get("starred") for m in msgs)
        ids = [m["id"] for m in msgs]
        self.clear_selection()
        self.app.star_messages(ids, star)
        self.app._set_status("Starred" if star else "Removed from starred",
                             T.ok)

    def _act_forward(self) -> None:
        ForwardModal(self.app, [m["id"] for m in self._ordered()],
                     on_done=self.clear_selection).open()

    def _act_share(self) -> None:
        ids = [m["id"] for m in self._ordered()]
        self.clear_selection()
        self.app.share_messages(ids)

    def _act_delete(self) -> None:
        DeleteModal(self.app, self._ordered(),
                    on_done=self.clear_selection).open()

    # ---- names and replies ---------------------------------------------------
    def _author_of(self, msg: dict) -> str:
        if msg["direction"] == "out":
            return self.app.keys.address
        return msg.get("author") or msg["peer"]

    def author_name(self, address: Optional[str],
                    outgoing_bubble: bool = False) -> str:
        """Who someone is, as this phone knows them: You, the contact's
        name, their name in a group, or their address."""
        if not address:
            return "Message"
        if address == self.app.keys.address:
            return "You"
        if address == self.peer and self.kind == "dm":
            return self.peer_name_text or short_addr(address, 12)
        store = self.app.store
        contact = store.get_contact(address) if store else None
        if contact and contact.get("username"):
            return contact["username"]
        for member in (self.group or {}).get("members") or []:
            if member.get("a") == address and member.get("u"):
                return member["u"]
        return short_addr(address, 10)

    def set_reply(self, msg: dict) -> None:
        kind = msg.get("kind") or "text"
        snip = self._text_of(msg) or {"image": "Photo",
                                      "voice": "Voice message"}.get(kind, "")
        reply = {"snip": snip[:120], "from": self._author_of(msg),
                 "kind": kind}
        if msg.get("client_msg_id"):
            reply["cid"] = msg["client_msg_id"]
        self.reply_to = reply
        self.reply_author.text = self.author_name(reply["from"])
        self.reply_snip.text = snip
        self.reply_bar.height, self.reply_bar.opacity = max(
            dp(56), self.reply_author.height + line_height(15) + dp(14)), 1
        self.reply_bar.conceal(False)
        self.entry.input.focus = True

    def take_reply(self) -> Optional[dict]:
        reply = self.reply_to
        self.cancel_reply()
        return reply

    def cancel_reply(self) -> None:
        self.reply_to = None
        self.reply_bar.height, self.reply_bar.opacity = 0, 0
        self.reply_bar.conceal(True)

    def jump_to_cid(self, cid: Optional[str]) -> None:
        """Tap on a quote: scroll to the quoted message and flash it."""
        if self.selecting or not cid:
            return
        for mid, bubble in self._bubbles.items():
            if bubble.msg.get("client_msg_id") == cid:
                row = self._rows.get(mid)
                if row is None:
                    return
                self._stick_to_bottom = False
                self.scroll.scroll_to(row, padding=dp(60),
                                      animate=bool(motion(1)))
                row.set_selected(True)
                Clock.schedule_once(
                    lambda _dt: row.set_selected(mid in self.selected), 0.9)
                return
        self.app._set_status("That message is further back — open "
                             "‘Earlier messages’ to find it", T.text_3)

    # ---- taps ------------------------------------------------------------------
    def on_bubble_tap(self, msg_id: int) -> None:
        bubble = self._bubbles.get(msg_id)
        if bubble is None:
            return
        msg = bubble.msg
        if msg["direction"] == "in" and not msg["read"]:
            self.app._mark_single_read(msg["id"], msg.get("server_id"))

    def on_media_tap(self, msg: dict) -> None:
        if self.selecting:
            return
        msg = self.app.store.message(msg["id"]) or msg
        state = msg.get("media_state") or ""
        path = msg.get("media_path")
        outgoing = msg["direction"] == "out"
        if state == "expired":
            self.app.request_resend(msg["id"])
        elif state == "failed":
            (self.app.retry_media if outgoing else self.app.fetch_media)(
                msg["id"])
        elif state == "pending" and not outgoing:
            self.app.fetch_media(msg["id"])
        elif path and Path(path).is_file():
            if msg.get("kind") == "image":
                ImageViewer(path).open()
            elif msg.get("kind") == "voice":
                try:
                    self.app.voice.toggle(path)
                except Exception:
                    log.exception("could not play voice note")
                    self.app._set_status("Couldn't play that voice message",
                                         T.alert)
                self.refresh_voice_buttons()

    def refresh_voice_buttons(self) -> None:
        for bubble in self._bubbles.values():
            if isinstance(bubble.media, VoiceContent):
                bubble.media.refresh(bubble.msg)

    def on_voice_progress(self, path: str, fraction: float) -> None:
        for bubble in self._bubbles.values():
            if isinstance(bubble.media, VoiceContent) and \
                    bubble.msg.get("media_path") == path:
                bubble.media.wave.set_progress(fraction)

    def on_voice_stop(self, path: str) -> None:
        self.on_voice_progress(path, 0.0)
        self.refresh_voice_buttons()

    # ---- the ⋮ menu ------------------------------------------------------------
    def open_menu(self, _anchor=None) -> None:
        if self.peer is None or self.app.store is None:
            return
        peer, app = self.peer, self.app
        items = []
        if self.app.store.unread_for(peer):
            items.append(("check", "Mark all as read",
                          lambda: app.mark_peer_read(peer)))
        if self.kind == "group":
            gid = gid_of(peer)
            items.append(("group", "Group info", app.open_chat_info))
            items.append(("star", "Starred messages",
                          lambda: StarredModal(app, peer).open()))
            on = (self.group or {}).get("notify", 1)
            items.append(("bell_off" if on else "bell",
                          "Mute notifications" if on else
                          "Unmute notifications",
                          lambda: app.set_group_notify(gid, not on)))
            items.append(("share", "Export or import",
                          lambda: ChatHistorySheet(app, peer).open()))
            if (self.group or {}).get("state") == "active":
                items.append(("leave", "Leave group",
                              lambda: app.confirm_leave_group(gid), True))
        elif self.kind == "self":
            items.append(("star", "Starred notes",
                          lambda: StarredModal(app, peer).open()))
            items.append(("share", "Export or import",
                          lambda: ChatHistorySheet(app, peer).open()))
        else:
            contact = self.app.store.get_contact(peer) or {}
            items.append(("info", "Chat info", app.open_chat_info))
            items.append(("star", "Starred messages",
                          lambda: StarredModal(app, peer).open()))
            on = contact.get("notify", 1)
            items.append(("bell_off" if on else "bell",
                          "Mute notifications" if on else
                          "Unmute notifications",
                          lambda: app.toggle_notify(peer)))
            items.append(("share", "Export or import",
                          lambda: ChatHistorySheet(app, peer).open()))
        MenuSheet(items).open()

    # ---- emoji -----------------------------------------------------------------
    def toggle_emoji(self) -> None:
        if self.emoji_panel.is_open:
            self.close_emoji()
            self.entry.input.focus = True
        else:
            self._emoji_target = None
            self.open_emoji()

    def open_emoji(self) -> None:
        self.entry.input.focus = False
        self.emoji_panel.set_open(True)
        self.emoji_button.set_icon("keyboard")
        self.emoji_button.label = "Keyboard"

    def close_emoji(self) -> None:
        if self.emoji_panel.is_open:
            self.emoji_panel.set_open(False)
        self._emoji_target = None
        self.emoji_button.set_icon("smile")
        self.emoji_button.label = "Emoji"

    def _emoji_picked(self, emoji: str) -> None:
        if self._emoji_target is not None:
            target, self._emoji_target = self._emoji_target, None
            self.close_emoji()
            self.app.react(target, emoji)
            return
        self.entry.input.insert_text(emoji)

    def _on_entry_focus(self, _w, focused: bool) -> None:
        if focused and self.emoji_panel.is_open and IS_MOBILE:
            self.close_emoji()

    # ---- composer: photo, voice, send ------------------------------------------
    def _paint_reply_icon(self, widget) -> None:
        widget.canvas.clear()
        with widget.canvas:
            draw_icon("reply", (widget.x + widget.width / 2), (self.reply_bar.y + self.reply_bar.height / 2),
                      dp(20), C(T.accent))

    def _sync_reply_accent(self, widget) -> None:
        self._reply_accent.pos = (widget.x, widget.y + dp(3))
        self._reply_accent.size = (dp(2), max(0, widget.height - dp(6)))

    _can_record: Optional[bool] = None

    def _voice_possible(self) -> bool:
        if IS_ANDROID:
            return True
        if ConversationScreen._can_record is None:
            ConversationScreen._can_record = desktop_recording_available()
        return ConversationScreen._can_record

    def _refresh_me(self) -> None:
        """The disc of the identity you are writing as, left of the field."""
        keys = self.app.keys
        mine = (keys.address, keys.username, keys.label) \
            if keys.has_identity else None
        if mine == getattr(self, "_me_shown", False):
            return
        self._me_shown = mine
        self.me_holder.clear_widgets()
        if mine is None:
            return
        name = keys.username or keys.label
        me = AvatarButton(keys.address, name, diameter=dp(34),
                          pos_hint={"center_x": .5, "center_y": .5})
        me.label = f"Writing as {'@' + keys.username if keys.username else name}"
        me.bind(on_release=lambda _w: IdentitySheet(self.app).open())
        self.me_holder.add_widget(me)

    def _sync_composer(self, animate: bool = True) -> None:
        """Fit the composer to what is in it (see its layout above)."""
        quiet = not self.entry.input.text.strip()
        fold(self.me_holder, quiet, TOUCH, animate)
        self.entry.show_camera(quiet, animate)
        voice = quiet and self._voice_possible()
        icon = "mic" if voice else "send"
        if animate:
            self.action_button.morph(icon)
        else:
            self.action_button.show_icon(icon)
        self.action_button.label = "Record a voice message" if voice \
            else "Send"

    def _on_action_button(self) -> None:
        if self.entry.input.text.strip() or not self._voice_possible():
            self.app.send_current()
        else:
            self.start_recording()

    def _media_refused(self, what: str) -> bool:
        if self.kind != "self" and not self.app.media_available():
            self.app._set_status(f"{what} need the v9 relay — this one can't "
                                 "carry them yet", T.accent)
            return True
        return False

    def pick_image(self) -> None:
        if self._media_refused("Photos and voice messages"):
            return
        if IS_ANDROID:
            try:
                android_pick_image(lambda path: self._picked(path, True))
            except Exception:
                log.exception("photo picker failed")
                self.app._set_status("Couldn't open the photo picker",
                                     T.alert)
        else:
            ImageChooserModal(lambda path: self._picked(path, False)).open()

    def take_photo(self) -> None:
        if self._media_refused("Photos"):
            return
        if IS_ANDROID:
            try:
                android_take_photo(lambda path: self._picked(path, True))
            except Exception:
                log.exception("camera failed")
                self.app._set_status("Couldn't open the camera", T.alert)
        else:
            open_desktop_camera(lambda path: self._picked(path, True),
                                on_choose=self.pick_image)

    def _picked(self, path: str, temporary: bool = False) -> None:
        """A photo to send. `temporary`: a copy this app made (a picked or
        camera photo), deleted once it is sent or set aside."""
        AttachPreviewModal(self.app, path, temporary=temporary).open()

    def start_recording(self) -> None:
        if self._media_refused("Voice messages"):
            return
        if IS_ANDROID and not android_has_permission(
                "android.permission.RECORD_AUDIO"):
            android_request_permissions(
                ["android.permission.RECORD_AUDIO"],
                lambda _p, grants: run_on_ui(
                    self._after_mic_permission, bool(grants) and all(grants)))
            return
        try:
            self.recorder = VoiceRecorder() if IS_ANDROID else PcmRecorder()
        except Exception as exc:
            log.warning("could not start recording: %s", exc)
            self.recorder = None
            self.app._set_status("Couldn't start recording — is a "
                                 "microphone connected?", T.alert)
            return
        self.close_emoji()
        self.composer.clear_widgets()
        for part in (self.rec_cancel, self.rec_dot, self.rec_label,
                     self.rec_levels, self.rec_send):
            self.composer.add_widget(part)
        Animation.cancel_all(self.rec_dot, "opacity")
        self.rec_dot.opacity = 1
        if motion(1):
            pulse = Animation(opacity=.25, d=.6, t="in_out_sine") + \
                Animation(opacity=1, d=.6, t="in_out_sine")
            pulse.repeat = True
            pulse.start(self.rec_dot)
        self._rec_tick = Clock.schedule_interval(self._update_rec_label, 0.1)
        self._update_rec_label(0)

    def _after_mic_permission(self, granted: bool) -> None:
        if granted:
            self.start_recording()
        else:
            self.app._set_status("Voice messages need the microphone "
                                 "permission", T.accent)

    def _paint_rec_dot(self, dot, *_a) -> None:
        dot.canvas.clear()
        r = dp(5)
        with dot.canvas:
            Color(*C(T.alert))
            SmoothEllipse(pos=(dot.x + dot.width / 2 - r,
                               dot.y + dot.height / 2 - r),
                          size=(2 * r, 2 * r))

    def _update_rec_label(self, _dt) -> None:
        recorder = self.recorder
        if recorder is None:
            return
        if getattr(recorder, "failed", False) and recorder.elapsed_ms == 0:
            self.cancel_recording()
            self.app._set_status("No sound came from the microphone",
                                 T.alert)
            return
        sec = recorder.elapsed_ms // 1000
        self.rec_label.text = f"{sec // 60}:{sec % 60:02d}"
        self.rec_levels.update(list(recorder.levels))
        if sec >= getattr(recorder, "max_seconds", MAX_VOICE_SECONDS):
            self.finish_recording()

    def _end_recording_ui(self) -> None:
        if self._rec_tick is not None:
            self._rec_tick.cancel()
            self._rec_tick = None
        Animation.cancel_all(self.rec_dot, "opacity")
        self.composer.clear_widgets()
        for part in self.composer_parts:
            self.composer.add_widget(part)
        self._sync_composer(animate=False)

    def finish_recording(self) -> None:
        recorder, self.recorder = self.recorder, None
        if recorder is None:
            return
        self._end_recording_ui()
        try:
            path, duration, wave = recorder.stop()
        except Exception:
            log.exception("recording failed")
            self.app._set_status("Recording failed", T.alert)
            return
        if duration < 700:
            discard_media_file(path)
            self.app._set_status("That was too short to send", T.text_3)
            return
        self.app.send_voice(path, duration, wave)

    def cancel_recording(self) -> None:
        recorder, self.recorder = self.recorder, None
        if recorder is None:
            return
        self._end_recording_ui()
        recorder.cancel()

    # ---- bottom alignment ----------------------------------------------------
    def content_height(self) -> float:
        """Height of the real messages, without the bottom-align filler."""
        return self.messages.minimum_height - self._filler.height

    def _sync_filler(self) -> None:
        """Pin a short conversation to the BOTTOM of the viewport.

        Kivy lays a ScrollView's content out from the TOP when it is shorter
        than the viewport, and with always_overscroll the same branch runs
        for ANY scroll_y above zero — so one stray touch lifted a two-line
        chat up under the app bar. Padding the top until the content is at
        least viewport-tall makes that case impossible; a chat that really
        overflows is untouched (the filler collapses to zero).
        """
        if self._syncing_filler:
            return
        self._syncing_filler = True
        try:
            want = max(0.0, self.scroll.height - self.content_height())
            if abs(self._filler.height - want) > 0.5:
                self._filler.height = want
        finally:
            self._syncing_filler = False

    # ---- scroll position -----------------------------------------------------
    BOTTOM_EPSILON = 0.02

    def at_bottom(self) -> bool:
        """True when the newest message is on screen. A conversation that
        does not fill the viewport always is."""
        if self.content_height() <= self.scroll.height:
            return True
        return self.scroll.scroll_y <= self.BOTTOM_EPSILON

    def scroll_to_bottom(self, animate: bool = True) -> None:
        """Bring the newest message into view, once, on the frame after
        layout — one pending settle at a time."""
        self._stick_to_bottom = True

        def _apply(_dt):
            self._scroll_trigger = None
            Animation.cancel_all(self.scroll, "scroll_y")
            if not animate or not motion(1) or \
                    self.content_height() <= self.scroll.height:
                self.scroll.scroll_y = 0
            else:
                Animation(scroll_y=0, d=0.22, t="out_quad").start(self.scroll)
            self._unseen = 0
            self._sync_jump_button()

        if self._scroll_trigger is not None:
            self._scroll_trigger.cancel()
        self._scroll_trigger = Clock.schedule_once(_apply, 0.02)

    def jump_to_latest(self) -> None:
        self._unseen = 0
        self.scroll_to_bottom(animate=True)

    def _on_content_height(self) -> None:
        """Following the conversation: stay pinned to the newest message.
        Reading history: hold the reading position as the content grows."""
        old_h, new_h = self._last_content_h, self.messages.height
        self._last_content_h = new_h
        view = self.scroll.height
        if self._stick_to_bottom:
            if self.scroll.scroll_y != 0:
                self.scroll.scroll_y = 0
            return
        old_span, new_span = old_h - view, new_h - view
        if old_span > 0 and new_span > 0 and abs(new_h - old_h) > 0.5:
            offset_px = self.scroll.scroll_y * old_span
            self.scroll.scroll_y = max(0.0, min(1.0, offset_px / new_span))

    def _on_scrolled(self) -> None:
        if self.at_bottom():
            self._unseen = 0
            self._stick_to_bottom = True
        else:
            self._stick_to_bottom = False
        self._sync_jump_button()

    def cancel_pending_scroll(self) -> None:
        """Hand scroll control back to the user: the queued settle, any
        easing, and the damped overscroll's residual momentum."""
        if self._scroll_trigger is not None:
            self._scroll_trigger.cancel()
            self._scroll_trigger = None
        Animation.cancel_all(self.scroll, "scroll_y")
        self._stick_to_bottom = False
        effect = getattr(self.scroll, "effect_y", None)
        if effect is not None:
            try:
                effect.velocity = 0
                effect.stop(effect.value)
            except Exception:
                pass

    def _sync_jump_button(self) -> None:
        self.jump_button.set_count(self._unseen)
        self.jump_button.set_visible(not self.at_bottom() or
                                     self._unseen > 0)

    def note_new_messages(self, count: int) -> None:
        """Announce arrivals while reading history — without moving."""
        if count > 0:
            self._unseen += count
        self._stick_to_bottom = False
        self._sync_jump_button()

    @staticmethod
    def _day_text(day: str) -> str:
        try:
            date = datetime.fromisoformat(day).date()
        except ValueError:
            return day
        delta = (datetime.now().date() - date).days
        if delta == 0:
            return "Today"
        if delta == 1:
            return "Yesterday"
        if 1 < delta < 7:
            return date.strftime("%A")
        if date.year == datetime.now().year:
            return f"{date.day} {date.strftime('%B')}"
        return f"{date.day} {date.strftime('%B %Y')}"

    def _empty_state(self) -> BoxLayout:
        if self.kind == "self":
            text = ("Notes to self. Whatever you write, forward or photograph "
                    "here stays on this device and goes nowhere.")
        elif self.kind == "group":
            text = ("The beginning of the group. Each message is sealed "
                    "separately for every member before it leaves.")
        else:
            text = ("The beginning of your correspondence. Messages are "
                    "sealed on this device before they leave it.")
        box = BoxLayout(orientation="vertical", size_hint_y=None,
                        padding=[dp(36), dp(28), dp(36), dp(8)],
                        spacing=dp(10))
        box.bind(minimum_height=box.setter("height"))
        box.add_widget(InfoLabel(FLEURON, color=T.accent, font_size=fs(26),
                                 halign="center"))
        box.add_widget(InfoLabel(text, color=T.text_3, italic=True,
                                 font_size=fs(15.5), halign="center"))
        return box


# =============================================================================
#  Info, groups, settings
# =============================================================================
class PageScreen(Screen):
    """A titled page: app bar with back, and a scrolling column."""

    def __init__(self, app: "FextApp", name: str, title: str,
                 on_back: Callable, **kw):
        super().__init__(name=name, **kw)
        self.app = app
        root = BoxLayout(orientation="vertical")
        paint_round(root, T.bg, radius=0)
        self.add_widget(root)
        self.appbar = AppBar(title, on_back=on_back)
        root.add_widget(self.appbar)
        self.scroll = ScrollView(bar_width=dp(3), do_scroll_x=False)
        self.body = BoxLayout(orientation="vertical", size_hint_y=None,
                              padding=[0, 0, 0, dp(28)])
        self.body.bind(minimum_height=self.body.setter("height"))
        self.scroll.add_widget(self.body)
        root.add_widget(self.scroll)
        self.root_box = root

    def paragraph(self, text: str, italic: bool = False) -> InfoLabel:
        label = InfoLabel(text, color=T.text_3, font_size=fs(15),
                          italic=italic, padding=[dp(20), dp(8)])
        self.body.add_widget(label)
        return label

    def hero(self, seed: str, name: str, kind: str, sub: str = "",
             on_sub: Optional[Callable] = None, on_name=None) -> None:
        """The big disc, the name, and a line beneath (tap to copy)."""
        head = BoxLayout(orientation="vertical", size_hint_y=None,
                         padding=[dp(20), dp(26), dp(20), dp(16)],
                         spacing=dp(8))
        head.bind(minimum_height=head.setter("height"))
        holder = AnchorLayout(size_hint_y=None, height=dp(96))
        holder.add_widget(Avatar(seed, name, diameter=dp(92), kind=kind))
        head.add_widget(holder)
        title = InfoLabel(name, font_size=fs(25), halign="center")
        if on_name is not None:
            box = TappableBox(on_tap=on_name, size_hint_y=None,
                              padding=[0, dp(6)])
            box.bind(minimum_height=lambda w, v: setattr(
                w, "height", max(TOUCH, v)))
            box.add_widget(title)
            head.add_widget(box)
        else:
            head.add_widget(title)
        if sub:
            line = TappableBox(on_tap=on_sub, size_hint_y=None, height=TOUCH)
            label = single_line(sub, fs(15), T.text_3, italic=True,
                                halign="center")
            line.add_widget(label)
            if on_sub is not None:
                line.bind(state=lambda _w, s: setattr(
                    label, "color", C(T.accent if s == "down" else T.text_3)))
            head.add_widget(line)
        self.body.add_widget(head)
        self.body.add_widget(Rule())


class ChatInfoScreen(PageScreen):
    """A direct chat's (or notes to self's) page: who this is, and the
    switches for it — notifications, ticks, auto-read — starred messages
    and export. A group's page is GroupInfoScreen."""

    def __init__(self, app: "FextApp", **kw):
        super().__init__(app, "info", "About this chat",
                         lambda: app.close_chat_info(), **kw)
        self.peer: Optional[str] = None

    def show(self, peer: str) -> None:
        self.peer = peer
        store = self.app.store
        self.body.clear_widgets()
        kind = self.app.conversation_kind(peer)
        if kind == "self":
            self.appbar.title.text = "Notes to self"
            self.hero(peer, "Notes to self", "self",
                      "Kept on this device only")
            starred = len(store.starred(peer))
            self.body.add_widget(rows_card([
                ListRow("star", "Starred notes",
                        f"{starred} note{'s' if starred != 1 else ''}",
                        chevron=True,
                        on_tap=lambda: StarredModal(self.app, peer).open()),
                ListRow("share", "Export or import",
                        "Plaintext copies, kept on this device",
                        chevron=True,
                        on_tap=lambda: ChatHistorySheet(self.app,
                                                        peer).open())]))
            self.paragraph("Nothing written here is sent anywhere. Photos "
                           "and voice messages stay on this phone too; "
                           "forwarding one to a chat sends it then.",
                           italic=True)
            return
        self.appbar.title.text = "About this chat"
        contact = store.get_contact(peer) or {}
        name = contact.get("username") or short_addr(peer, 14)
        self.hero(peer, name, "dm", short_addr(peer, 22),
                  on_sub=lambda: self.app.copy_text(peer))
        self.body.add_widget(SectionHead("This chat"))
        self.body.add_widget(rows_card([
            ListRow("bell", "Notifications",
                    "Alerts for new messages in this chat",
                    control=ToggleSwitch(contact.get("notify", 1),
                                         lambda on: self.app.set_notify(peer,
                                                                        on))),
            ListRow("check", "Delivery and read ticks",
                    "Off on either side caps both at one tick",
                    control=ToggleSwitch(contact.get("ticks_enabled", 1),
                                         lambda on: self.app.set_ticks(peer,
                                                                       on))),
            ListRow("info", "Mark as read automatically",
                    "When off, tap a message to mark it read",
                    control=ToggleSwitch(contact.get("auto_read", 1),
                                         lambda on: self.app.set_auto_read(
                                             peer, on))),
        ]))
        starred = len(store.starred(peer))
        self.body.add_widget(SectionHead("Kept here"))
        self.body.add_widget(rows_card([
            ListRow("star", "Starred messages",
                    f"{starred} message{'s' if starred != 1 else ''}",
                    chevron=True,
                    on_tap=lambda: StarredModal(self.app, peer).open()),
            ListRow("share", "Export or import",
                    "Plaintext copies, kept on this device", chevron=True,
                    on_tap=lambda: ChatHistorySheet(self.app, peer).open()),
        ]))
        caps = contact.get("caps", 0) & LocalStore.CAP_V9
        self.paragraph(
            "Sealed end to end. " +
            ("They run Ferritext 9 or later: replies, photos, voice "
             "messages, reactions and delete-for-both all reach them."
             if caps else
             "Replies, photos, voice messages, reactions and "
             "delete-for-both reach them fully once they run Ferritext 9."),
            italic=True)


class NewGroupScreen(PageScreen):
    """Name a group and choose who is in it."""

    def __init__(self, app: "FextApp", **kw):
        super().__init__(app, "new_group", "New group",
                         lambda: app.go_back_to_chats(), **kw)
        self.rows: list = []

    def show(self) -> None:
        self.body.clear_widgets()
        box = BoxLayout(orientation="vertical", size_hint_y=None,
                        padding=[dp(20), dp(18), dp(20), dp(6)])
        box.bind(minimum_height=box.setter("height"))
        self.name_field = Field(hint="What is it called?", label="Group name",
                                on_enter=lambda: None)
        self.name_field.input.bind(text=lambda *_a: self._sync())
        box.add_widget(self.name_field)
        self.body.add_widget(box)
        self.count = SectionHead("Members")
        self.body.add_widget(self.count)
        contacts = self.app.store.contacts() if self.app.store else []
        self.rows = [ContactPickRow(c, on_change=lambda _r: self._sync())
                     for c in contacts]
        if self.rows:
            self.body.add_widget(rows_card(self.rows, inset=dp(74)))
        else:
            self.paragraph("Add contacts first — a group is made of people "
                           "you already know.", italic=True)
        holder = BoxLayout(size_hint_y=None, height=TOUCH + dp(36),
                           padding=[dp(20), dp(18), dp(20), dp(0)])
        self.create = Btn("Create group", on_release=lambda _w: self._go())
        holder.add_widget(self.create)
        self.body.add_widget(holder)
        self.status = self.paragraph("", italic=True)
        self._sync()

    def _sync(self) -> None:
        n = sum(1 for r in self.rows if r.picked)
        room = MAX_GROUP_MEMBERS - 1
        self.count.caps.text = f"Members · {n} of up to {room}"
        self.create.disabled = not (self.name_field.text.strip() and
                                    1 <= n <= room)

    def _go(self) -> None:
        members = [r.address for r in self.rows if r.picked]
        peer = self.app.create_group(self.name_field.text, members)
        if peer is None:
            self.status.text = "Choose a name and at least one member."
            return
        self.app.open_conversation(peer)


class GroupInfoScreen(PageScreen):
    """A group's page: its name (the admin may change it), its members
    (the admin may add and remove them), notifications, starred messages,
    and leaving."""

    def __init__(self, app: "FextApp", **kw):
        super().__init__(app, "group_info", "Group",
                         lambda: app.close_chat_info(), **kw)
        self.gid: Optional[str] = None

    def show(self, gid: str) -> None:
        self.gid = gid
        app, store = self.app, self.app.store
        group = store.group(gid) or {"name": "Group", "members": [],
                                     "admin": "", "state": "pending"}
        me = app.keys.address
        admin = group.get("admin") == me and group.get("state") == "active"
        peer = group_peer(gid)
        self.body.clear_widgets()
        members = group.get("members") or []
        self.hero(peer, group.get("name") or "Group", "group",
                  f"{len(members)} member{'s' if len(members) != 1 else ''}",
                  on_name=(lambda: self._rename(group)) if admin else None)
        state = group.get("state")
        if state != "active":
            self.paragraph({"left": "You left this group. Its history stays "
                                    "here, read-only.",
                            "removed": "You were removed from this group. "
                                       "Its history stays here, read-only.",
                            "pending": "Messages arrived for this group "
                                       "before its invitation; they will "
                                       "join it when that arrives."
                            }.get(state, ""), italic=True)
        self.body.add_widget(SectionHead("This group"))
        rows = [ListRow("bell", "Notifications",
                        "Alerts for new messages here",
                        control=ToggleSwitch(group.get("notify", 1),
                                             lambda on: app.set_group_notify(
                                                 gid, on)))]
        if admin:
            rows.append(ListRow("edit", "Rename the group",
                                group.get("name") or "", chevron=True,
                                on_tap=lambda: self._rename(group)))
        starred = len(store.starred(peer))
        rows.append(ListRow("star", "Starred messages",
                            f"{starred} message{'s' if starred != 1 else ''}",
                            chevron=True,
                            on_tap=lambda: StarredModal(app, peer).open()))
        self.body.add_widget(rows_card(rows))
        head = SectionHead(f"Members · {len(members)}")
        self.body.add_widget(head)
        member_rows = []
        if admin and len(members) < MAX_GROUP_MEMBERS:
            member_rows.append(ListRow("person_add", "Add members",
                                       "From your contacts",
                                       on_tap=lambda: AddMembersSheet(
                                           app, gid).open()))
        for m in sorted(members, key=lambda m: (m["a"] != me,
                                                m["a"] != group.get("admin"),
                                                (m.get("u") or "").lower())):
            address = m["a"]
            name = "You" if address == me else \
                app.conversation_screen.author_name(address) \
                if app.conversation_screen.peer == peer else \
                (store.get_contact(address) or {}).get("username") or \
                m.get("u") or short_addr(address, 12)
            tags = BoxLayout(size_hint=(None, None), height=dp(20),
                             spacing=dp(8))
            width = 0
            if address == group.get("admin"):
                tag = caps_tag("Admin")
                tags.add_widget(tag)
                width += tag.width
            tags.width = width
            tap = None
            if admin and address != me:
                tap = (lambda a=address, n=name: self._member_menu(a, n))
            elif address != me:
                tap = (lambda a=address, n=name: self._member_menu(a, n,
                                                                   False))
            member_rows.append(PersonRow(address, name,
                                         short_addr(address, 18), on_tap=tap,
                                         trailing=tags if width else None,
                                         initial_from=m.get("u") or name))
        self.body.add_widget(rows_card(member_rows, inset=dp(76)))
        if state == "active":
            self.body.add_widget(Rule())
            self.body.add_widget(rows_card([ListRow(
                "leave", "Leave group", "Your copy of its history stays",
                danger=True,
                on_tap=lambda: app.confirm_leave_group(gid))]))
        self.paragraph("Each message is sealed separately for every member "
                       "and signed by its author, so no one — the relay "
                       "included — can forge or read it. Groups hold up to "
                       f"{MAX_GROUP_MEMBERS} people.", italic=True)

    def _rename(self, group: dict) -> None:
        gid = group["gid"]
        TextPromptSheet("Rename group", "Group name", group.get("name", ""),
                        "Every member sees the new name.",
                        lambda name: self._renamed(gid, name),
                        limit=MAX_GROUP_NAME).open()

    def _renamed(self, gid: str, name: str) -> bool:
        self.app.rename_group(gid, name)
        self.show(gid)
        return True

    def _member_menu(self, address: str, name: str, admin: bool = True):
        items = []
        if self.app.store.get_contact(address) and \
                (self.app.store.get_contact(address) or {}).get("listed", 1):
            items.append(("send", f"Message {name}",
                          lambda: self.app.open_conversation(address)))
        else:
            items.append(("person_add", f"Add {name} to your contacts",
                          lambda: self.app.list_contact(address)))
        if admin:
            items.append(("delete", f"Remove {name} from the group",
                          lambda: self._remove(address), True))
        MenuSheet(items, title=name).open()

    def _remove(self, address: str) -> None:
        if self.app.remove_group_member(self.gid, address):
            self.show(self.gid)


class AddMembersSheet(Sheet):
    """The admin adds contacts to a group."""

    def __init__(self, app: "FextApp", gid: str) -> None:
        super().__init__("Add members")
        self.app, self.gid = app, gid
        self.card.padding = [0, dp(8), 0, dp(18)]
        self.title_label.padding = [dp(22), 0]
        group = app.store.group(gid) or {"members": []}
        have = {m["a"] for m in group["members"]}
        self.room = MAX_GROUP_MEMBERS - len(have)
        self.rows = [ContactPickRow(c, on_change=lambda _r: self._sync())
                     for c in app.store.contacts() if c["address"] not in have]
        if self.rows:
            self.add(rows_card(self.rows, inset=dp(74)))
        else:
            self.add(InfoLabel("Everyone in your contacts is already here.",
                               color=T.text_3, italic=True, font_size=fs(15),
                               padding=[dp(22), 0]))
        holder = BoxLayout(size_hint_y=None, height=TOUCH,
                           padding=[dp(22), 0])
        self.button = Btn("Add", on_release=lambda _w: self._go())
        holder.add_widget(self.button)
        self.add(holder)
        self._sync()

    def _sync(self) -> None:
        n = sum(1 for r in self.rows if r.picked)
        self.button.disabled = not 1 <= n <= self.room
        self.button.text = "Add" if n <= 1 else f"Add {n} people"

    def _go(self) -> None:
        picked = [r.address for r in self.rows if r.picked]
        self.dismiss()
        if self.app.add_group_members(self.gid, picked):
            self.app.group_info_screen.show(self.gid)


class SettingsScreen(PageScreen):
    """Settings: how the app looks and reads, notifications, and this
    identity's keys."""

    def __init__(self, app: "FextApp", **kw):
        super().__init__(app, "settings", "Settings",
                         lambda: app.close_settings(), **kw)
        self._revealed = False

    def show(self) -> None:
        self.body.clear_widgets()
        app = self.app
        self.body.add_widget(SectionHead("Appearance"))
        pad = BoxLayout(orientation="vertical", size_hint_y=None,
                        padding=[dp(20), dp(4), dp(20), dp(8)],
                        spacing=dp(4))
        pad.bind(minimum_height=pad.setter("height"))
        pad.add_widget(CapsLabel("Theme", size=10.5, color=T.text_3))
        pad.add_widget(Segmented(THEME_CHOICES, APPEARANCE.mode,
                                 lambda v: app.set_appearance(theme=v)))
        sys_dark = system_prefers_dark()
        pad.add_widget(InfoLabel(
            "System follows your phone's setting" +
            ("" if sys_dark is None else
             f" (now {'dark' if sys_dark else 'light'})") + ".",
            color=T.text_3, italic=True, font_size=fs(14.5)))
        pad.add_widget(Widget(size_hint_y=None, height=dp(10)))
        pad.add_widget(CapsLabel("Text size", size=10.5, color=T.text_3))
        current = next((k for k, _l, s in TEXT_SCALES
                        if abs(s - APPEARANCE.scale) < .01), "default")
        pad.add_widget(Segmented([(k, label) for k, label, _s in TEXT_SCALES],
                                 current,
                                 lambda v: app.set_appearance(text_size=v)))
        pad.add_widget(InfoLabel(
            "“See you at nine?” — a sample at this size, 14:05 👍",
            font_size=fs(17), padding=[0, dp(6)]))
        self.body.add_widget(pad)
        self.body.add_widget(rows_card([ListRow(
            None, "Reduce motion",
            "Sheets, reactions and scrolling move without animation",
            control=ToggleSwitch(APPEARANCE.reduce_motion,
                                 lambda on: app.set_appearance(
                                     reduce_motion=on)))]))
        if IS_ANDROID or os.environ.get("FEXT_SHOW_ANDROID_SETTINGS"):
            self.body.add_widget(SectionHead("Notifications"))
            self.body.add_widget(rows_card([ListRow(
                "bell", "Receive messages while closed",
                "A small service keeps listening after Android closes the "
                "app; Android shows it as running. Off saves battery — you "
                "are then only notified while Ferritext is open.",
                control=ToggleSwitch(background_delivery_enabled(),
                                     self._on_delivery))]))
        self._identity()
        self.body.add_widget(SectionHead("About"))
        self.paragraph(
            f"{APP_NAME} {APP_VERSION}. Set in EB Garamond, with Noto Emoji "
            "and Titillium Web, under the SIL Open Font License.",
            italic=True)

    def _on_delivery(self, on: bool) -> None:
        save_pref("background_delivery", bool(on))
        self.app._sync_background_service()

    def _qr(self, data: str, side=dp(184)) -> AnchorLayout:
        holder = AnchorLayout(size_hint_y=None, height=side + dp(20))
        plate = AnchorLayout(size_hint=(None, None),
                             size=(side + dp(16), side + dp(16)))
        paint_round(plate, T.plate, radius=dp(6))
        img = KivyImage(size_hint=(None, None), size=(side, side),
                        allow_stretch=True)
        img.texture = qr_texture(data)
        plate.add_widget(img)
        holder.add_widget(plate)
        return holder

    def _identity(self) -> None:
        keys, app = self.app.keys, self.app
        if not keys.has_identity:
            return
        self.body.add_widget(SectionHead("This identity"))
        self.body.add_widget(rows_card([
            ListRow("person", keys.label,
                    f"@{keys.username or 'not registered'}", chevron=True,
                    on_tap=lambda: RenameIdentityModal(app,
                                                       keys.address).open()),
            ListRow("copy", "Copy address", keys.address,
                    on_tap=lambda: app.copy_text(keys.address)),
        ] + ([ListRow("share", "Open the key folder", str(DATA_DIR),
                      on_tap=lambda: open_folder_in_explorer(DATA_DIR))]
             if not IS_MOBILE else [])))
        self.body.add_widget(self._qr(keys.address))
        self.body.add_widget(InfoLabel(
            "Scan for the address alone", color=T.text_3, italic=True,
            font_size=fs(14), halign="center"))
        self.paragraph(
            "The address is the HASH160 of your public key in Base58Check "
            "(version 0x24), as Bitcoin builds one. Its “Fec” prefix is the "
            "network's proof of work.", italic=True)
        bundle = json.dumps(keys.public_bundle(), indent=2)
        self.body.add_widget(SectionHead("Public bundle · safe to share"))
        self.body.add_widget(rows_card([ListRow(
            "copy", "Copy the public bundle", "Username, address and key",
            on_tap=lambda: app.copy_text(bundle))]))
        self.body.add_widget(self._qr(json.dumps(keys.public_bundle(),
                                                 separators=(",", ":")),
                                      side=dp(200)))
        self.body.add_widget(SectionHead("Private key · never share it"))
        self.private_label = InfoLabel(
            "Hidden", color=T.alert, font_size=fs(14), italic=True,
            padding=[dp(20), dp(4)])
        self.reveal_row = ListRow(
            "key", "Reveal the private key", "As raw hex and as WIF",
            on_tap=self._toggle_private)
        self.body.add_widget(rows_card([
            self.reveal_row,
            ListRow("copy", "Copy WIF", "Imports into FEXT Core",
                    on_tap=self._copy_wif)]))
        self.body.add_widget(self.private_label)

    def _toggle_private(self) -> None:
        self._revealed = not self._revealed
        self.private_label.text = (self.app.keys.private_export()
                                   if self._revealed else "Hidden")
        self.private_label.italic = not self._revealed
        self.reveal_row.title.text = ("Hide the private key" if
                                      self._revealed else
                                      "Reveal the private key")

    def _copy_wif(self) -> None:
        data = json.loads(self.app.keys.private_export())
        Clipboard.copy(data["private_key_wif"])
        self.app._set_status("Private key copied — keep it safe", T.accent)


class SettingsModal:
    """Pre-9.1 entry point: Settings is a page now."""

    def __init__(self, app: "FextApp") -> None:
        self.app = app

    def open(self) -> None:
        self.app.open_settings()


class SplashOverlay(FloatLayout):
    """The loading screen: the mark above the wordmark, on white.

    It covers the first paint (the screens being built, the identity read
    off disk, the transport probe going out) and then fades. It is a widget
    in the tree, not a ModalView, so the first-run sheet cannot show
    through it; _post_build waits for the fade.
    """

    FADE = 0.1          # seconds of fade-out
    HOLD = 0.9          # minimum time on screen, so it never merely blinks

    def __init__(self, **kw):
        super().__init__(**kw)
        # White, as specified — the ground of the launcher icon and the
        # platform presplash, so the three hand over seamlessly.
        paint_round(self, "#FFFFFF", radius=0)
        column = BoxLayout(orientation="vertical", spacing=dp(18),
                           size_hint=(None, None), width=dp(280),
                           height=dp(320),
                           pos_hint={"center_x": 0.5, "center_y": 0.5})
        if LOGO_FILE.exists():
            column.add_widget(KivyImage(source=str(LOGO_FILE),
                                        allow_stretch=True, keep_ratio=True,
                                        size_hint_y=None, height=dp(180)))
        wordmark = Label(
            text="Ferrite Text\nMessenger", halign="center", valign="top",
            font_size=sp(26), color=C("#1B1915"), line_height=1.1,
            size_hint_y=None, height=dp(74))
        wordmark.bind(size=lambda w, v: setattr(w, "text_size", v))
        column.add_widget(wordmark)
        byline = Label(text="by koh-gt", italic=True, font_size=sp(15),
                       color=C("#6F685C"), size_hint_y=None, height=dp(24))
        column.add_widget(byline)
        self.add_widget(column)


class MessagingMixin:
    """Everything that turns a message into envelopes on the relay.

    Shared, unchanged, by the app and the background service (HeadlessApp):
    a re-upload asked for while the app is closed, or the group state an
    admin must publish when someone leaves, goes out through the very same
    code. Needs `keys`, `crypto`, `api`, `store` and `ui_queue`.

    A conversation key (`peer`) is one of:
      * an address               a direct chat
      * GROUP_PREFIX + gid       a group: one envelope per other member
      * our own address          notes to self: stored here, sent nowhere
    """

    def conversation_kind(self, peer: Optional[str]) -> str:
        if not peer:
            return "none"
        if peer.startswith(GROUP_PREFIX):
            return "group"
        if peer == self.keys.address:
            return "self"
        return "dm"

    def group_recipients(self, gid: str) -> list:
        """[(address, public key), …] of the OTHER members, if we are in."""
        group = self.store.group(gid) if self.store else None
        if group is None or group["state"] != "active":
            return []
        return [(m["a"], m["k"]) for m in group["members"]
                if m["a"] != self.keys.address]

    def _send_worker(self, local_id: Optional[int], peer: str, peer_pub: str,
                     body: str, client_msg_id: str, x: Optional[dict] = None,
                     group: Optional[str] = None) -> bool:
        """SIGN → ENCRYPT → SEND one envelope to one address (`peer`), then
        record the relay's receipt (1/3).

        `group` set: this is one member's copy of group message `local_id`,
        recorded per member. `local_id` None: a control, nothing recorded.

        Every failure here has to end at the UI: a message must never sit at
        0/3 with no reason given anywhere.
        """
        me = MessageFlowLog.who(self.keys.address, self.keys.username)
        contact = self.store.get_contact(peer) or {}
        them = MessageFlowLog.who(peer, contact.get("username"))
        try:
            envelope, _sent_at = self.crypto.build_envelope(
                body, peer_pub, x=x, recipient=peer, cid=client_msg_id)
        except Exception as exc:
            log.exception("could not build envelope for %s", peer)
            MessageFlowLog.failed(me, them, f"encryption failed: {exc}")
            self.ui_queue.put(("status", f"Could not encrypt: {exc}",
                               COL_DANGER))
            return False

        fragment = MessageFlowLog.fragment(envelope)
        try:
            result = self.api.send_message(peer, envelope, client_msg_id)
        except (requests.RequestException, ApiClient.ApiError,
                ValueError) as exc:
            MessageFlowLog.failed(me, them, str(exc), fragment)
            self.ui_queue.put(("status", f"Send failed: {exc}", COL_DANGER))
            return False
        except Exception as exc:                     # never lose the thread
            log.exception("unexpected error sending to %s", peer)
            MessageFlowLog.failed(me, them, f"unexpected: {exc}", fragment)
            self.ui_queue.put(("status", f"Send failed: {exc}", COL_DANGER))
            return False

        if local_id is None:                         # a control: no row
            return True
        server_id = result.get("server_id")
        recorded = (self.store.group_send_sent(local_id, peer, server_id)
                    if group else self.store.mark_sent(local_id, server_id))
        if not recorded:
            # The relay HAS the message; we merely failed to write that
            # down. Say so rather than leaving a grey tick and no reason.
            MessageFlowLog.failed(
                me, them, f"relay accepted it as #{server_id} but the local "
                          f"store could not record that", fragment)
            self.ui_queue.put(
                ("status", "Sent, but this device could not record it — "
                 "the tick may be wrong", COL_DANGER))
            return False
        MessageFlowLog.tick(1, server_id, me, them, fragment)
        self.ui_queue.put(("message_stored",
                           group_peer(group) if group else peer))
        return True

    def _send_copies(self, local_id: Optional[int], peer: str, body: str,
                     cid: str, x: dict) -> bool:
        """Deliver one message (or control) to every recipient of a
        conversation. True only if every copy reached the relay."""
        kind = self.conversation_kind(peer)
        if kind == "self":
            return True
        if kind == "dm":
            contact = self.store.get_contact(peer)
            if contact is None:
                return False
            return self._send_worker(local_id, peer, contact["public_key"],
                                     body, cid, x)
        gid = gid_of(peer)
        group = self.store.group(gid) if gid else None
        if group is None:
            return False
        if x.get("k") == "ctl":
            x = dict(x, ctl=dict(x["ctl"], grp=gid))
        else:
            x = dict(x, grp={"id": gid, "v": group["version"]})
        ok = True
        for address, pub in self.group_recipients(gid):
            ok = self._send_worker(local_id, address, pub, body, cid, x,
                                   group=gid if local_id else None) and ok
        return ok

    def _send_control(self, peer: str, op: dict, fallback: str) -> bool:
        """Send a control to a whole conversation, synchronously (call from
        a worker thread). `fallback` is the body: what a pre-v9 client
        shows, since it cannot act on the instruction."""
        return self._send_copies(None, peer, fallback, uuid.uuid4().hex,
                                 {"k": "ctl", "ctl": op})

    def _send_control_to(self, address: str, op: dict, fallback: str,
                         pub: Optional[str] = None) -> bool:
        """A control for ONE person, whatever conversation it concerns (in a
        group: asking the author of a photo to send it again)."""
        if pub is None:
            contact = self.store.get_contact(address)
            if contact is None:
                return False
            pub = contact["public_key"]
        return self._send_worker(None, address, pub, fallback,
                                 uuid.uuid4().hex, {"k": "ctl", "ctl": op})

    def _group_state(self, gid: str) -> Optional[dict]:
        group = self.store.group(gid)
        if group is None:
            return None
        return {"id": gid, "name": group["name"], "v": group["version"],
                "admin": group["admin"], "members": group["members"]}

    def _publish_group_state(self, gid: str, also=(), fallback: str = "",
                             bump: bool = True) -> bool:
        """As the admin: bump the version and send the group's state to every
        member (and to `also`: people just removed, so they learn it)."""
        group = self.store.group(gid)
        if group is None or group["admin"] != self.keys.address:
            return False
        state = self._group_state(gid)
        if bump:
            state["v"] += 1
            self.store.save_group(state, group["state"])
        body = fallback or f"Group \u201c{state['name']}\u201d updated"
        ok = True
        targets = [(m["a"], m["k"]) for m in state["members"]
                   if m["a"] != self.keys.address] + list(also)
        for address, pub in targets:
            ok = self._send_control_to(address, {"op": "group", "g": state},
                                       body, pub=pub) and ok
        return ok

    def _on_resend_request(self, peer: str, cid: str,
                           gid: Optional[str] = None) -> None:
        """They asked for a file the relay no longer has: upload our copy
        again and point THEIR existing message at it (no duplicate). In a
        group only the person who asked is sent the new reference."""
        conversation = group_peer(gid) if gid else peer
        row = self.store.message_by_cid(conversation, cid, "out")
        if not row or not row.get("media_path") or \
                not Path(row["media_path"]).is_file():
            log.info("resend requested for %s but no local copy remains", cid)
            return
        ext = json.loads(row.get("ext") or "{}")

        def work():
            try:
                data = Path(row["media_path"]).read_bytes()
                blob, key, sha = seal_media(data)
                blob_id = self.api.upload_blob(blob).get("blob", "")
                if not _TOKEN_RE.match(blob_id or ""):
                    raise ValueError("relay returned no media id")
            except (OSError, ValueError, requests.RequestException,
                    ApiClient.ApiError) as exc:
                log.warning("re-upload for %s failed: %s", cid, exc)
                return
            media = dict(ext.get("media") or {}, blob=blob_id, key=key,
                         sha=sha)
            ext["media"] = media
            self.store.set_media(row["id"], ext=ext)
            op = {"op": "remedia", "cid": cid, "media": media}
            if gid:
                op["grp"] = gid
            self._send_control_to(peer, op, row["body"])
        threading.Thread(target=work, daemon=True).start()


class FextApp(MessagingMixin, App):
    """Owns the identity lifecycle, the two screens and the UI-queue pump.

    Threading contract (unchanged from v2): ALL network + crypto work happens
    off the UI thread. Workers never touch widgets directly — they post to
    `ui_queue`, which is drained on the Kivy main thread by `_drain_ui_queue`
    (the Clock analogue of the old tkinter `after()` loop).
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.title = f"{APP_NAME} {APP_VERSION} — end-to-end encrypted " \
                     f"messenger"
        self.keys = IdentityManager()
        self.crypto: Optional[CryptoEngine] = None
        self.api: Optional[ApiClient] = None
        self.store: Optional[LocalStore] = None
        self.worker: Optional[SyncWorker] = None
        self.ui_queue: "queue.Queue" = queue.Queue()
        self.current_peer: Optional[str] = None
        self._pending_status = ("Starting…", T.text_3)
        self._chat_anchor: Optional[int] = None  # oldest id shown in the chat
        self._dirty_chat = False               # coalesced refresh flags
        self._dirty_list = False
        self.foreground = True                 # drives reconnect aggressiveness
        self._register_modal: Optional[RegisterModal] = None
        self.splash: Optional["SplashOverlay"] = None

    # ---- lifecycle -----------------------------------------------------------
    def build(self):
        apply_theme(APPEARANCE.resolve())
        self._theme_name = T.name
        Window.clearcolor = C(T.bg)
        if IS_MOBILE:
            # Without this the soft keyboard covers the composer you are
            # typing into; 'below_target' pans just enough to keep it seen.
            Window.softinput_mode = "below_target"
        else:
            # Phone-shaped by default on desktop so the layout is exercised
            # the way it will be on a handset; still freely resizable.
            Window.size = (dp(420), dp(820))
            # The mark, not Kivy's, on the title bar, taskbar and dock.
            if DESKTOP_ICON.is_file():
                self.icon = str(DESKTOP_ICON)
        self.manager = ScreenManager(transition=NoTransition())
        self.voice = VoicePlayer()
        self._build_screens()
        Window.bind(on_keyboard=self._on_keyboard)     # Android back button
        Clock.schedule_interval(self._drain_ui_queue, 0.15)
        Clock.schedule_interval(self._watch_system_theme, 4.0)
        self._bind_notification_taps()
        android_style_system_bars()
        # The loading screen sits over the whole app until it fades, so the
        # root is something that can stack widgets; the rest of the app
        # talks to self.manager.
        root = FloatLayout()
        root.add_widget(self.manager)
        self.splash = SplashOverlay()
        root.add_widget(self.splash)
        Clock.schedule_once(lambda _dt: self._probe_transport(), 0.05)
        Clock.schedule_once(self._dismiss_splash, SplashOverlay.HOLD)
        return root

    def _build_screens(self) -> None:
        """Every screen, built in the current theme and text size."""
        self.chats_screen = ChatsScreen(self)
        self.conversation_screen = ConversationScreen(self)
        self.chat_info_screen = ChatInfoScreen(self)
        self.group_info_screen = GroupInfoScreen(self)
        self.new_group_screen = NewGroupScreen(self)
        self.settings_screen = SettingsScreen(self)
        for screen in (self.chats_screen, self.conversation_screen,
                       self.chat_info_screen, self.group_info_screen,
                       self.new_group_screen, self.settings_screen):
            self.manager.add_widget(screen)
        self.voice.on_progress = self.conversation_screen.on_voice_progress
        self.voice.on_stop = self.conversation_screen.on_voice_stop

    def _navigate(self, name: str, direction: Optional[str] = "left") -> None:
        """Show screen `name`, sliding (unless motion is reduced)."""
        if direction is None or not motion(1) or \
                self.manager.current == name:
            self.manager.transition = NoTransition()
        else:
            self.manager.transition = SlideTransition(duration=0.18,
                                                      direction=direction)
        self.manager.current = name

    # =========================================================================
    #  Appearance: theme, text size, motion
    # =========================================================================
    def set_appearance(self, theme: Optional[str] = None,
                       text_size: Optional[str] = None,
                       reduce_motion: Optional[bool] = None) -> None:
        if theme is not None:
            save_pref("theme", theme)
        if text_size is not None:
            save_pref("text_size", text_size)
        if reduce_motion is not None:
            save_pref("reduce_motion", bool(reduce_motion))
        self.apply_appearance(force=text_size is not None)

    def apply_appearance(self, force: bool = False) -> None:
        """Re-read the preferences (and the system's theme); rebuild the
        interface if anything visible changed."""
        old = (T.name, APPEARANCE.scale)
        name = APPEARANCE.resolve()
        if force or (name, APPEARANCE.scale) != old:
            self._rebuild_ui(name)

    def _watch_system_theme(self, _dt) -> None:
        """Follow the phone's dark/light switch while the app is open. The
        check can take a subprocess on a desktop, so it runs off the UI
        thread and only hops back when the answer differs."""
        if APPEARANCE.mode != "system" or getattr(self, "_theme_probe", 0):
            return
        if IS_ANDROID:
            # A cheap JNI read, and pyjnius must not be used from a thread
            # that is never detached from the JVM — so on the UI thread.
            dark = system_prefers_dark()
            if ("light" if dark is False else "dark") != T.name:
                self.apply_appearance()
            return
        self._theme_probe = 1

        def probe():
            try:
                dark = system_prefers_dark()
                want = "light" if dark is False else "dark"
                if want != T.name:
                    run_on_ui(self.apply_appearance)
            finally:
                self._theme_probe = 0
        threading.Thread(target=probe, daemon=True).start()

    def _rebuild_ui(self, name: str) -> None:
        """Build every screen again in the new theme, and put the user back
        exactly where they were: same screen, same chat, same draft."""
        current = self.manager.current
        peer = self.current_peer
        screen = self.conversation_screen
        draft = screen.entry.text
        reply = screen.reply_to
        screen.cancel_recording()
        self.voice.stop()
        for view in list(Window.children):
            if isinstance(view, ModalView):
                view.dismiss(animation=False)
        apply_theme(name)
        self._theme_name = name
        Window.clearcolor = C(T.bg)
        for old in list(self.manager.screens):
            self.manager.remove_widget(old)
        self._build_screens()
        android_style_system_bars()
        self.chats_screen.refresh_header()
        self.refresh_sidebar()
        self.chats_screen.set_status(*self._pending_status)
        if peer is not None and self.store is not None:
            self._chat_anchor = None
            self.refresh_chat(peer)
            self.conversation_screen.entry.text = draft
            if reply:
                self.conversation_screen.reply_to = reply
        if current == "info" and peer:
            self.chat_info_screen.show(peer)
        elif current == "group_info" and peer and gid_of(peer):
            self.group_info_screen.show(gid_of(peer))
        elif current == "new_group":
            self.new_group_screen.show()
        elif current == "settings":
            self.settings_screen.show()
        if current in self.manager.screen_names:
            self._navigate(current, None)

    def _probe_transport(self) -> None:
        """Work out the relay's transport in the background at startup.

        Doing it here rather than lazily on the first API call means the
        probe overlaps with identity mining and the first-run dialog, so by
        the time anyone presses Register the scheme is already known and
        that call goes straight through.
        """
        def worker():
            base = ENDPOINT.resolve(force=not ENDPOINT.resolved)
            if base is None:
                self.ui_queue.put(
                    ("status", "Can't reach the relay — tap to retry",
                     COL_DANGER))
            elif not ENDPOINT.secure:
                self.ui_queue.put(
                    ("status", "Relay reachable (transport not secured)",
                     COL_TICK_GOLD))
        threading.Thread(target=worker, daemon=True).start()

    def _dismiss_splash(self, _dt) -> None:
        """Fade the loading screen out, then start the app proper."""
        fade = Animation(opacity=0, d=SplashOverlay.FADE)
        fade.bind(on_complete=lambda *_a: self._splash_gone())
        fade.start(self.splash)

    def _splash_gone(self) -> None:
        if self.splash is not None and self.splash.parent is not None:
            self.splash.parent.remove_widget(self.splash)
        self.splash = None
        self._post_build(0)

    def _post_build(self, _dt) -> None:
        """Deferred until the loading screen has gone — see SplashOverlay."""
        if self.keys.has_identity:
            self._activate_identity(self.keys.active["address"])
        else:
            NewIdentityModal(self, first_run=True).open()

    def on_stop(self) -> None:
        if self.worker is not None:
            self.worker.stop_event.set()
        if self.store is not None:
            self.store.close()

    def _on_keyboard(self, _window, key, *_args) -> bool:
        """Android/Esc back, innermost first: an open sheet, highlight mode,
        the emoji panel, a recording, a pending reply, a page — and only
        then the app."""
        if key not in (27, 1001):
            return False
        sheets = [v for v in Window.children if isinstance(v, ModalView)]
        if sheets:
            top = sheets[0]
            if getattr(top, "dismissable", True) and top.auto_dismiss:
                top.dismiss()
            return True
        current = self.manager.current
        if current in ("info", "group_info"):
            self.close_chat_info()
            return True
        if current == "settings":
            self.close_settings()
            return True
        if current == "new_group":
            self.go_back_to_chats()
            return True
        if current == "conversation":
            screen = self.conversation_screen
            if screen.selecting:
                screen.clear_selection()
            elif screen.emoji_panel.is_open:
                screen.close_emoji()
            elif screen.recorder is not None:
                screen.cancel_recording()
            elif screen.reply_to is not None:
                screen.cancel_reply()
            else:
                self.go_back_to_chats()
            return True
        return False

    # =========================================================================
    #  Identity lifecycle
    # =========================================================================
    def _activate_identity(self, address: str) -> None:
        """Switch the live identity WITHOUT restarting: stop the old sync
        worker, swap crypto/API/store, rebuild the UI, start a new worker."""
        if self.worker is not None:
            self.worker.stop_event.set()
            self.worker = None
        if self.store is not None:
            self.store.close()
            self.store = None
        self.keys.activate(address)
        self.crypto = CryptoEngine(self.keys)
        self.api = ApiClient(self.keys)
        self.store = LocalStore(self.keys.store_path())
        self.current_peer = None
        self._navigate("chats", None)
        self.chats_screen.refresh_header()
        self.refresh_sidebar()
        if self.keys.username:
            self._start_worker()
            self._ask_notification_permission()
            self._sync_background_service()
            peer = self._launch_peer()
            if peer and self._known_conversation(peer):
                self.open_conversation(peer)
        else:
            self._set_status("Pick a username to start chatting",
                             COL_TICK_GOLD)
            RegisterModal(self).open()

    def _start_worker(self) -> None:
        self.worker = SyncWorker(self)
        self.worker.start()
        self._set_status("Connecting…", COL_TEXT_DIM)
        # Learn what the relay can carry (media needs v9) off the UI thread,
        # so the composer knows before anyone reaches for the camera.
        api = self.api
        threading.Thread(target=lambda: api.features(), daemon=True).start()

    def media_available(self) -> bool:
        """Non-blocking: answers from the cached /api/health probe."""
        features = getattr(self.api, "_features", None) if self.api else None
        if features is None:
            if self.api is not None:
                api = self.api
                threading.Thread(target=lambda: api.features(),
                                 daemon=True).start()
            return False
        return bool(features.get("blobs"))

    def on_registered(self) -> None:
        self.chats_screen.refresh_header()
        self._start_worker()

    def on_identity_created(self, address: str) -> None:
        self._activate_identity(address)

    def on_identity_removed(self, address: str, note: str = "") -> None:
        """Finish removing an identity and land somewhere sensible.

        If the identity being removed is the ACTIVE one its worker and store
        must be torn down before the files are deleted, or the sync thread
        keeps writing to a database that is being unlinked.
        """
        if address == self.keys.address:
            if self.worker is not None:
                self.worker.stop_event.set()
                self.worker = None
            if self.store is not None:
                self.store.close()
                self.store = None
        remaining = self.keys.remove_identity(address)
        if remaining:
            self._activate_identity(remaining)
            self._set_status(f"Identity removed{note}", COL_TEXT_DIM)
        else:
            # Nothing left: return to the first-run experience rather than
            # leaving a half-live UI pointing at an identity that is gone.
            self.current_peer = None
            self.chats_screen.refresh_header()
            self.chats_screen.refresh_list([])
            self._navigate("chats", None)
            self._set_status(f"No identities on this device{note}",
                             COL_TICK_GOLD)
            NewIdentityModal(self, first_run=True).open()

    def on_identity_dialog_closed(self) -> None:
        self.chats_screen.refresh_header()

    def on_contact_added(self, address: str) -> None:
        self.refresh_sidebar()
        self.open_conversation(address)

    # =========================================================================
    #  Navigation
    # =========================================================================
    def _known_conversation(self, peer: str) -> bool:
        if self.store is None:
            return False
        kind = self.conversation_kind(peer)
        if kind == "self":
            return True
        if kind == "group":
            return self.store.group(gid_of(peer)) is not None
        return self.store.get_contact(peer) is not None

    def _auto_read(self, peer: str) -> bool:
        if self.conversation_kind(peer) != "dm":
            return True
        return bool((self.store.get_contact(peer) or {}).get("auto_read", 1))

    def open_conversation(self, peer: str) -> None:
        if self.store is None:
            return
        if IS_ANDROID:
            android_clear_notification(peer)
        self.current_peer = peer
        self._chat_anchor = None                # fresh window per chat
        if self._auto_read(peer):
            self.mark_peer_read(peer, refresh=False)
        self._navigate("conversation", "left")
        self.refresh_chat(peer)
        self.refresh_sidebar()

    def go_back_to_chats(self) -> None:
        screen = self.conversation_screen
        screen.clear_selection()
        screen.cancel_recording()
        screen.close_emoji()
        self.voice.stop()
        self.current_peer = None
        self._navigate("chats", "right")
        self.refresh_sidebar()

    # =========================================================================
    #  Rendering
    # =========================================================================
    def refresh_sidebar(self) -> None:
        """Repaint the conversation list (name kept from v2 for continuity)."""
        if self.store is None:
            return
        self.chats_screen.refresh_list(
            self.store.conversations(me=self.keys.address))

    def refresh_chat(self, peer: Optional[str]) -> None:
        """Repaint the open conversation from the local store.

        Only the most recent CHAT_PAGE_SIZE messages are materialised; older
        ones are paged in on demand. With the incremental renderer the cost
        of an arriving message is independent of the conversation's length.
        """
        if peer is None or peer != self.current_peer or self.store is None:
            return
        kind = self.conversation_kind(peer)
        contact = (self.store.get_contact(peer) or {}) if kind == "dm" else {}
        group = self.store.group(gid_of(peer)) if kind == "group" else None
        unread = self.store.unread_for(peer)
        self.conversation_screen.set_peer(peer, contact, len(unread), kind,
                                          group)
        if self._chat_anchor is None:            # cold open: newest page
            messages = self.store.messages_for(peer, limit=CHAT_PAGE_SIZE)
            self._chat_anchor = messages[0]["id"] if messages else None
        else:                                    # anchored: list only grows
            messages = self.store.messages_for(peer,
                                               from_id=self._chat_anchor)
        reactions = self.store.reactions_for(
            peer, [m.get("client_msg_id") for m in messages])
        for msg in messages:
            msg["reactions"] = reactions.get(msg.get("client_msg_id"), [])
        self.conversation_screen.has_older = bool(
            self._chat_anchor is not None
            and self.store.has_messages_before(peer, self._chat_anchor))
        ticks = bool(contact.get("ticks_enabled", 1)) if kind == "dm" \
            else True
        self.conversation_screen.render(messages, ticks,
                                        self._mark_single_read, peer=peer)
        # Photos/voice notes that arrived while the background service was
        # syncing were never downloaded; fetch them now someone is looking.
        for msg in messages:
            if msg["direction"] == "in" and msg.get("media_state") == "pending":
                self.fetch_media(msg["id"])

    def load_older_messages(self) -> None:
        """Page one more CHAT_PAGE_SIZE of history in above the anchor."""
        if self.current_peer is None or self._chat_anchor is None:
            return
        older = self.store.messages_for(self.current_peer,
                                        limit=CHAT_PAGE_SIZE,
                                        before_id=self._chat_anchor)
        if not older:
            self.conversation_screen.has_older = False
            return
        self._chat_anchor = older[0]["id"]
        self.conversation_screen._rendered_peer = None    # force a full pass
        self.refresh_chat(self.current_peer)

    # =========================================================================
    #  Read marking (the 3/3 tick, from the recipient's side)
    # =========================================================================
    def mark_peer_read(self, peer: Optional[str], refresh: bool = True) -> None:
        if peer is None or self.store is None:
            return
        unread = self.store.unread_for(peer)
        if not unread:
            return
        self.store.mark_read_local([local_id for local_id, _ in unread])
        server_ids = [sid for _, sid in unread if sid]
        if server_ids and self.worker is not None:
            threading.Thread(target=self.worker.send_read_receipts,
                             args=(server_ids,), daemon=True).start()
        if refresh:
            self.refresh_chat(peer)
            self.refresh_sidebar()

    def _mark_single_read(self, local_id: int,
                          server_id: Optional[int]) -> None:
        self.store.mark_read_local([local_id])
        if server_id and self.worker is not None:
            threading.Thread(target=self.worker.send_read_receipts,
                             args=([server_id],), daemon=True).start()
        self.refresh_chat(self.current_peer)
        self.refresh_sidebar()

    # =========================================================================
    #  Sending
    # =========================================================================
    def send_current(self) -> None:
        """Send what is in the composer — as a reply, if one is pending."""
        if self.store is None or self.current_peer is None:
            return
        screen = self.conversation_screen
        if screen.closed_note.parent is not None:
            return
        body = sanitize_text(screen.entry.text)
        if not body:
            return
        screen.entry.text = ""
        reply = screen.take_reply()
        self.send_text(self.current_peer, body, reply=reply)

    def send_text(self, peer: str, body: str, reply: Optional[dict] = None,
                  forwarded: bool = False) -> Optional[int]:
        """Queue a text message. Returns its local id (None if impossible).

        Every v9 message carries a signed extension block, even a plain one:
        it is what binds the message to its recipient (see build_envelope)
        and how the other side learns this device understands replies,
        media and delete-for-both.
        """
        ext = {}
        if reply:
            ext["reply"] = reply
        if forwarded:
            ext["fwd"] = 1
        return self._queue_message(peer, body, dict(ext, k="text"), "text",
                                   ext)

    def _queue_message(self, peer: str, body: str, x: dict, kind: str,
                       ext: Optional[dict], media_path: Optional[str] = None,
                       media_state: str = "",
                       on_local_row: Optional[Callable] = None
                       ) -> Optional[int]:
        """Record an outgoing message at 0/3 and send it in the background.

        Notes to self are recorded and go nowhere. A group message records
        one delivery row per member, then goes out to each of them.
        """
        if self.store is None:
            return None
        conv = self.conversation_kind(peer)
        recipients = None
        if conv == "dm":
            contact = self.store.get_contact(peer)
            if contact is None:
                self._set_status("Unknown contact", COL_DANGER)
                return None
            label = contact.get("username")
        elif conv == "group":
            recipients = [a for a, _k in self.group_recipients(gid_of(peer))]
            if not recipients:
                self._set_status("You are no longer a member of this group",
                                 COL_TICK_GOLD)
                return None
            label = (self.store.group(gid_of(peer)) or {}).get("name")
        else:
            label = "notes to self"
        client_msg_id = uuid.uuid4().hex
        # 0/3 immediately: the grey tick means "not yet on the server".
        local_id = self.store.add_out(peer, body, now_iso(), client_msg_id,
                                      kind=kind, ext=ext or None,
                                      media_path=media_path,
                                      media_state=media_state,
                                      recipients=recipients)
        MessageFlowLog.tick(
            0, None, MessageFlowLog.who(self.keys.address, self.keys.username),
            label or MessageFlowLog.who(peer),
            f"[local id {local_id}, client_msg_id {client_msg_id[:8]}…]")
        if on_local_row is not None:
            on_local_row(local_id, client_msg_id)
        if threading.current_thread() is threading.main_thread():
            if peer == self.current_peer:
                self.refresh_chat(peer)
            self._dirty_list = True
        else:                         # media pipeline: repaint via the queue
            self.ui_queue.put(("message_stored", peer))
        if media_state != "uploading" and conv != "self":
            threading.Thread(
                target=self._send_copies,
                args=(local_id, peer, body, client_msg_id, x),
                daemon=True).start()
        return local_id

    # =========================================================================
    #  Message actions (v9): star, delete, forward, share, copy
    # =========================================================================
    def star_messages(self, local_ids: list, starred: bool) -> None:
        self.store.set_starred(local_ids, starred)
        self.refresh_chat(self.current_peer)

    def delete_messages(self, local_ids: list, for_everyone: bool) -> None:
        """Delete for me, or — for the user's OWN messages only — for both.

        For both: the instruction is sent first, and nothing is removed here
        until the relay has accepted it, so a failure leaves the messages
        visible and the user able to try again, rather than gone from this
        screen while still sitting on the other phone.
        """
        rows = self.store.messages_by_ids(local_ids)
        if not rows:
            return
        peer = rows[0]["peer"]
        if not for_everyone:
            for row in self.store.delete_messages([r["id"] for r in rows]):
                discard_media_file(row.get("media_path"))
            self.refresh_chat(self.current_peer)
            self._dirty_list = True
            return
        own = [r for r in rows if r["direction"] == "out"
               and r.get("client_msg_id")]
        if len(own) != len(rows):
            self._set_status("You can only delete your own messages for "
                             "everyone", COL_TICK_GOLD)
            return

        if self.conversation_kind(peer) == "self":
            self._finish_delete_for_everyone(own, True)
            return

        def work():
            ok = self._send_control(
                peer, {"op": "delete",
                       "cids": [r["client_msg_id"] for r in own]},
                "Deleted a message" if len(own) == 1
                else f"Deleted {len(own)} messages")
            run_on_ui(self._finish_delete_for_everyone, own, ok)
        threading.Thread(target=work, daemon=True).start()

    def _finish_delete_for_everyone(self, rows: list, ok: bool) -> None:
        if not ok:
            self._set_status("Couldn't delete for everyone — check your "
                             "connection and try again", COL_DANGER)
            return
        for row in self.store.delete_messages([r["id"] for r in rows]):
            discard_media_file(row.get("media_path"))
        self.refresh_chat(self.current_peer)
        self.refresh_sidebar()

    def forward_messages(self, local_ids: list, targets: list) -> int:
        """Send copies to other contacts of this identity. Photos and voice
        notes are forwarded by REFERENCE — the sealed blob already on the
        relay, with its key — so nothing is uploaded again."""
        rows = [r for r in self.store.messages_by_ids(local_ids)
                if r.get("kind") != "event"]
        sent = 0
        for target in targets:
            to_self = self.conversation_kind(target) == "self"
            for row in rows:
                ext = json.loads(row.get("ext") or "{}")
                media = ext.get("media")
                local = row.get("media_path") and Path(
                    row["media_path"]).is_file()
                if row["kind"] in ("image", "voice") and media and (
                        media.get("blob") or to_self):
                    # By REFERENCE: the sealed blob already on the relay.
                    self._queue_message(
                        target, row["body"],
                        {"k": "media", "media": media, "fwd": 1}, row["kind"],
                        {"media": media, "fwd": 1},
                        media_path=row.get("media_path"),
                        media_state="ready" if local or to_self else "")
                elif row["kind"] in ("image", "voice") and media and local:
                    # Kept only here (a note to self): upload it now.
                    extra = {k: v for k, v in media.items()
                             if k in ("w", "h", "thumb", "dur", "wave", "cap")}
                    data = Path(row["media_path"]).read_bytes()
                    threading.Thread(target=self._send_media, args=(
                        target, row["kind"], data,
                        media.get("mime", "application/octet-stream"), extra,
                        row["body"], None,
                        Path(row["media_path"]).suffix.lstrip(".") or "bin"),
                        daemon=True).start()
                else:
                    self.send_text(target, row["body"], forwarded=True)
                sent += 1
        self.refresh_sidebar()
        return sent

    # =========================================================================
    #  Reactions (v9.1)
    # =========================================================================
    def react(self, local_id: int, emoji: str) -> None:
        """React to a message, or take the reaction back: choosing the
        reaction you already gave removes it. One per person per message.
        Recorded here at once; the others hear about it in the background."""
        row = self.store.message(local_id) if self.store else None
        if not row or not row.get("client_msg_id") or \
                row.get("kind") == "event":
            return
        peer, cid, me = row["peer"], row["client_msg_id"], self.keys.address
        current = self.store.reaction_of(peer, cid, me)
        emoji = "" if emoji == current else (sanitize_emoji(emoji) or "")
        if not self.store.set_reaction(peer, cid, me, emoji):
            return
        self.ui_queue.put(("reaction", peer, cid, me, emoji))
        self.refresh_chat(self.current_peer)
        if self.conversation_kind(peer) == "self":
            return
        snip = single_line_text(message_text(row) or {
            "image": "a photo", "voice": "a voice message"}.get(
                row.get("kind"), "a message"), 40)
        fallback = (f"Reacted {emoji} to \u201c{snip}\u201d" if emoji
                    else "Removed a reaction")

        def work():
            if not self._send_control(peer, {"op": "react", "cid": cid,
                                             "e": emoji}, fallback):
                self.ui_queue.put(("status", "Couldn't send that reaction — "
                                   "it shows here only", COL_TICK_GOLD))
        threading.Thread(target=work, daemon=True).start()

    # =========================================================================
    #  Groups (v9.1)
    # =========================================================================
    def create_group(self, name: str, addresses: list) -> Optional[str]:
        """Create a group of us + `addresses` (contacts), with us as admin,
        and send each member the invitation. Returns the conversation key."""
        name = sanitize_text(name)[:MAX_GROUP_NAME].strip()
        members = [{"a": self.keys.address, "k": self.keys.public_key_hex,
                    **({"u": self.keys.username} if self.keys.username
                       else {})}]
        for address in dict.fromkeys(addresses):
            contact = self.store.get_contact(address)
            if contact is None or address == self.keys.address:
                continue
            entry = {"a": address, "k": contact["public_key"]}
            if contact.get("username"):
                entry["u"] = contact["username"]
            members.append(entry)
        if not name or len(members) < 2 or len(members) > MAX_GROUP_MEMBERS:
            return None
        gid = os.urandom(16).hex()
        state = {"id": gid, "name": name, "v": 1,
                 "admin": self.keys.address, "members": members}
        self.store.save_group(state, "active")
        peer = group_peer(gid)
        self.store.add_event(peer, f"You created \u201c{name}\u201d")
        threading.Thread(target=self._announce_group, args=(
            gid, (), f"{self.keys.username or 'Someone'} added you to the "
                     f"group \u201c{name}\u201d", False), daemon=True).start()
        self.refresh_sidebar()
        return peer

    def _announce_group(self, gid: str, also=(), fallback: str = "",
                        bump: bool = True) -> None:
        if not self._publish_group_state(gid, also, fallback, bump):
            self.ui_queue.put(("status", "Not every member could be reached "
                               "yet — they'll get the group when they "
                               "next receive a message", COL_TICK_GOLD))
        self.ui_queue.put(("message_stored", group_peer(gid)))

    def rename_group(self, gid: str, name: str) -> bool:
        group = self.store.group(gid)
        name = sanitize_text(name)[:MAX_GROUP_NAME].strip()
        if not group or group["admin"] != self.keys.address or not name \
                or name == group["name"]:
            return False
        state = self._group_state(gid)
        state["name"] = name
        self.store.save_group(state, group["state"])
        self.store.add_event(group_peer(gid), f"You renamed the group "
                                              f"\u201c{name}\u201d")
        threading.Thread(target=self._announce_group, args=(gid,),
                         daemon=True).start()
        self.refresh_chat(self.current_peer)
        self.refresh_sidebar()
        return True

    def add_group_members(self, gid: str, addresses: list) -> int:
        group = self.store.group(gid)
        if not group or group["admin"] != self.keys.address:
            return 0
        state = self._group_state(gid)
        have = {m["a"] for m in state["members"]}
        added = []
        for address in addresses:
            contact = self.store.get_contact(address)
            if contact is None or address in have or \
                    len(state["members"]) >= MAX_GROUP_MEMBERS:
                continue
            entry = {"a": address, "k": contact["public_key"]}
            if contact.get("username"):
                entry["u"] = contact["username"]
            state["members"].append(entry)
            added.append(contact.get("username") or short_addr(address, 10))
        if not added:
            return 0
        self.store.save_group(state, group["state"])
        self.store.add_event(group_peer(gid), "You added " + ", ".join(added))
        threading.Thread(target=self._announce_group, args=(gid,),
                         daemon=True).start()
        self.refresh_chat(self.current_peer)
        return len(added)

    def remove_group_member(self, gid: str, address: str) -> bool:
        group = self.store.group(gid)
        if not group or group["admin"] != self.keys.address or \
                address == self.keys.address:
            return False
        gone = [m for m in group["members"] if m["a"] == address]
        if not gone:
            return False
        state = self._group_state(gid)
        state["members"] = [m for m in state["members"] if m["a"] != address]
        if len(state["members"]) < 2:
            return False
        self.store.save_group(state, group["state"])
        self.store.add_event(group_peer(gid), "You removed " + (
            gone[0].get("u") or short_addr(address, 10)))
        threading.Thread(target=self._announce_group, args=(
            gid, [(address, gone[0]["k"])]), daemon=True).start()
        self.refresh_chat(self.current_peer)
        return True

    def leave_group(self, gid: str) -> None:
        """Tell every member, then keep the history read-only here."""
        group = self.store.group(gid)
        if not group or group["state"] != "active":
            return
        recipients = self.group_recipients(gid)
        self.store.set_group_state(gid, "left")
        self.store.add_event(group_peer(gid), "You left the group")

        def work():
            for address, pub in recipients:
                self._send_control_to(address, {"op": "leave", "grp": gid},
                                      f"{self.keys.username or 'A member'} "
                                      f"left \u201c{group['name']}\u201d",
                                      pub=pub)
            self.ui_queue.put(("message_stored", group_peer(gid)))
        threading.Thread(target=work, daemon=True).start()
        self.refresh_chat(self.current_peer)
        self.refresh_sidebar()

    def set_group_notify(self, gid: str, on: bool) -> None:
        self.store.set_group_notify(gid, on)
        if not on and IS_ANDROID:
            android_clear_notification(group_peer(gid))
        self.refresh_chat(self.current_peer)

    def copy_text(self, text: str) -> None:
        Clipboard.copy(text)
        self._set_status("Copied", COL_OK)

    def share_messages(self, local_ids: list) -> None:
        """Hand messages to another app: Android's share sheet, or — on a
        desktop, which has no share sheet — the clipboard."""
        rows = self.store.messages_by_ids(local_ids)
        if not rows:
            return
        image = next((r for r in rows if r["kind"] == "image"
                      and r.get("media_path")), None)
        text = "\n\n".join(r["body"] for r in rows if r["kind"] == "text")
        if IS_ANDROID:
            try:
                if image is not None and len(rows) == 1:
                    android_share_image(image["media_path"])
                else:
                    android_share_text(text or rows[0]["body"])
                return
            except Exception:
                log.exception("share sheet failed; copying instead")
        self.copy_text(text or rows[0]["body"])
        self._set_status("Copied — paste it into any app", COL_OK)

    # =========================================================================
    #  Media (v9): photos and voice notes
    # =========================================================================
    def send_image(self, path: str, caption: str = "",
                   temporary: bool = False) -> None:
        peer = self.current_peer
        if peer is None:
            return
        reply = self.conversation_screen.take_reply()
        threading.Thread(target=self._prepare_image,
                         args=(peer, path, caption, reply, temporary),
                         daemon=True).start()

    def _prepare_image(self, peer, path, caption, reply,
                       temporary: bool = False) -> None:
        try:
            data, width, height, thumb = compress_image(path)
        except Exception as exc:
            log.warning("could not read image %s: %s", path, exc)
            self.ui_queue.put(("status", "That file isn't an image this "
                               "app can read", COL_DANGER))
            return
        finally:
            # The full-size original (with its camera details) is not kept:
            # what is sent, and stays, is the compressed copy.
            if temporary:
                discard_media_file(path)
        extra = {"w": width, "h": height, "thumb": thumb}
        if caption:
            extra["cap"] = caption
        self._send_media(peer, "image", data, "image/jpeg", extra,
                         caption or "Photo", reply, "jpg")

    def send_voice(self, path: str, duration_ms: int, wave: str = "") -> None:
        peer = self.current_peer
        if peer is None:
            return
        reply = self.conversation_screen.take_reply()

        def work():
            try:
                data = Path(path).read_bytes()
            except OSError as exc:
                self.ui_queue.put(("status", f"Recording unavailable: {exc}",
                                   COL_DANGER))
                return
            finally:
                discard_media_file(path)
            extra = {"dur": int(duration_ms)}
            if wave:
                extra["wave"] = wave
            # A phone records AAC (.m4a); a computer, WAV.
            wav = path.lower().endswith(".wav")
            self._send_media(peer, "voice", data,
                             "audio/wav" if wav else "audio/mp4", extra,
                             voice_label(duration_ms), reply,
                             "wav" if wav else "m4a")
        threading.Thread(target=work, daemon=True).start()

    def _send_media(self, peer, kind, data, mime, extra, body, reply,
                    suffix) -> None:
        """Worker thread: keep a local copy, seal, upload, then send the
        reference. The bubble appears at once, marked 'uploading'. A note to
        self keeps the file here and uploads nothing; a group's members all
        share one upload."""
        to_self = self.conversation_kind(peer) == "self"
        if not to_self and not (self.api is not None and
                                self.api.features().get("blobs")):
            self.ui_queue.put((
                "status", "This relay can't carry photos or voice notes "
                "yet — it needs the v9 relay", COL_TICK_GOLD))
            return
        if len(data) > MAX_MEDIA_BYTES - 64:
            self.ui_queue.put(("status", "That's too large to send",
                               COL_DANGER))
            return
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        local_path = MEDIA_DIR / f"{uuid.uuid4().hex}.{suffix}"
        try:
            local_path.write_bytes(data)
        except OSError as exc:
            self.ui_queue.put(("status", f"Could not save media: {exc}",
                               COL_DANGER))
            return
        media = dict(extra, kind=kind, mime=mime, size=len(data))
        ext = {"media": media}
        if reply:
            ext["reply"] = reply
        rows = {}
        self._queue_message(
            peer, body, {}, kind, ext, media_path=str(local_path),
            media_state="ready" if to_self else "uploading",
            on_local_row=lambda lid, cid: rows.update(id=lid, cid=cid))
        local_id, cid = rows.get("id"), rows.get("cid")
        if local_id is None or to_self:
            return
        try:
            blob, key, sha = seal_media(data)
            blob_id = self.api.upload_blob(blob).get("blob", "")
            if not _TOKEN_RE.match(blob_id or ""):
                raise ValueError("relay returned no media id")
        except (requests.RequestException, ApiClient.ApiError,
                ValueError) as exc:
            log.warning("media upload failed: %s", exc)
            self.store.set_media(local_id, state="failed")
            self.ui_queue.put(("message_stored", peer))
            self.ui_queue.put(("status", f"Upload failed: {exc}",
                               COL_DANGER))
            return
        media = dict(media, blob=blob_id, key=key, sha=sha)
        ext["media"] = media
        self.store.set_media(local_id, state="ready", ext=ext)
        self._send_copies(local_id, peer, body, cid, dict(ext, k="media"))
        self.ui_queue.put(("message_stored", peer))

    def retry_media(self, local_id: int) -> None:
        """Re-run a failed upload from the copy kept on this device."""
        row = self.store.message(local_id)
        if not row or not row.get("media_path"):
            return
        ext = json.loads(row.get("ext") or "{}")
        media = ext.get("media") or {}
        peer = row["peer"]
        self.store.delete_messages([local_id])       # replaced, not duplicated

        def work():
            try:
                data = Path(row["media_path"]).read_bytes()
            except OSError:
                self.ui_queue.put(("status", "The original file is gone",
                                   COL_DANGER))
                return
            extra = {k: v for k, v in media.items()
                     if k in ("w", "h", "thumb", "dur", "wave", "cap")}
            self._send_media(peer, row["kind"], data,
                             media.get("mime", "application/octet-stream"),
                             extra, row["body"], ext.get("reply"),
                             Path(row["media_path"]).suffix.lstrip(".")
                             or "bin")
        threading.Thread(target=work, daemon=True).start()

    def fetch_media(self, local_id: int) -> None:
        """Download + verify + decrypt one media message, in the background.

        A 404/410 from the relay means it let the blob expire: the message
        is marked 'expired' and the bubble offers to ask the sender for it
        again, which their device does automatically if it still has it.
        """
        row = self.store.message(local_id) if self.store else None
        if not row or row["direction"] != "in":
            return
        media = json.loads(row.get("ext") or "{}").get("media") or {}
        if not media.get("blob") or row.get("media_state") in (
                "downloading", "ready"):
            return
        self.store.set_media(local_id, state="downloading")
        peer = row["peer"]

        def work():
            try:
                blob = self.api.download_blob(media["blob"])
                data = open_media(blob, media["key"], media["sha"])
                MEDIA_DIR.mkdir(parents=True, exist_ok=True)
                suffix = media_suffix(row["kind"], media.get("mime"))
                target = MEDIA_DIR / f"{row.get('client_msg_id') or uuid.uuid4().hex}.{suffix}"
                target.write_bytes(data)
                self.store.set_media(local_id, path=str(target),
                                     state="ready")
            except ApiClient.ApiError as exc:
                self.store.set_media(
                    local_id, state="expired" if exc.status in (404, 410)
                    else "failed")
            except (requests.RequestException, ValueError, OSError) as exc:
                log.warning("media download failed: %s", exc)
                self.store.set_media(local_id, state="failed")
            self.ui_queue.put(("message_stored", peer))
        threading.Thread(target=work, daemon=True).start()

    def request_resend(self, local_id: int) -> None:
        """Ask the sender to upload an expired photo/voice note again."""
        row = self.store.message(local_id)
        if not row or not row.get("client_msg_id"):
            return
        peer = row["peer"]
        what = "voice message" if row["kind"] == "voice" else "photo"
        self.store.set_media(local_id, state="requested")
        self.refresh_chat(self.current_peer)
        gid = gid_of(peer)

        def work():
            op = {"op": "resend", "cid": row["client_msg_id"]}
            if gid:
                op["grp"] = gid
                ok = self._send_control_to(row.get("author") or "", op,
                                           f"Please send that {what} again")
            else:
                ok = self._send_control(peer, op,
                                        f"Please send that {what} again")
            if not ok:
                self.store.set_media(local_id, state="expired")
                self.ui_queue.put(("message_stored", peer))
        threading.Thread(target=work, daemon=True).start()

    # =========================================================================
    #  UI-queue pump (the only place workers reach the UI)
    # =========================================================================
    def _drain_ui_queue(self, _dt) -> None:
        """Drain worker events and repaint AT MOST ONCE per tick.

        Previously each event triggered its own full chat + list repaint, so
        a burst of N messages (a reconnect flushing a backlog, say) did N
        repaints. Now the whole burst is collapsed into a single pass.
        """
        try:
            while True:
                event = self.ui_queue.get_nowait()
                kind = event[0]
                if kind == "status":
                    self._set_status(event[1], event[2])
                elif kind == "needs_registration":
                    self._handle_needs_registration(event[1])
                elif kind == "message_stored":
                    peer = event[1]
                    if peer == self.current_peer and self.store is not None:
                        if self._auto_read(peer) and self.foreground and \
                                self.manager.current == "conversation":
                            self.mark_peer_read(peer, refresh=False)
                        self._dirty_chat = True
                    self._dirty_list = True
                elif kind == "incoming":
                    self._notify_incoming(*event[1:5])
                elif kind == "media_pending" and self.store is not None:
                    row = self.store.message_by_cid(event[1], event[2], "in")
                    if row is not None:
                        self.fetch_media(row["id"])
                elif kind == "resend_request" and self.store is not None:
                    self._on_resend_request(*event[1:4])
                elif kind == "reaction":
                    if event[1] == self.current_peer:
                        self.conversation_screen.note_reaction(*event[2:5])
                elif kind == "group_publish" and self.store is not None:
                    threading.Thread(target=self._announce_group,
                                     args=(event[1],), daemon=True).start()
        except queue.Empty:
            pass
        if self._dirty_chat:
            self._dirty_chat = False
            self.refresh_chat(self.current_peer)
        if self._dirty_list:
            self._dirty_list = False
            self.refresh_sidebar()

    # =========================================================================
    #  Notifications
    # =========================================================================
    def _notify_incoming(self, peer: str, body: str, kind: str,
                         author: Optional[str] = None) -> None:
        """Raise a notification for a new message — unless the user is
        looking at that very chat, or has muted it."""
        if self.store is None:
            return
        if (self.foreground and peer == self.current_peer
                and self.manager.current == "conversation"):
            return
        name, preview, muted = notification_text(self.store, peer, body,
                                                 kind, author)
        if muted:
            return
        if IS_ANDROID:
            try:
                android_notify(peer, name, preview[:160])
            except Exception:
                log.exception("could not post a notification")
        elif not self.foreground or peer != self.current_peer:
            self._set_status(f"New message from {name}", COL_ACCENT)

    def _sync_background_service(self) -> None:
        """Run the background service if the user wants delivery while the
        app is closed (default), stop it if not. Started from here — the
        foreground — because Android 12+ refuses to start one otherwise."""
        if not IS_ANDROID:
            return
        try:
            android_service(background_delivery_enabled())
        except Exception:
            log.exception("could not start/stop the background service")

    def _ask_notification_permission(self) -> None:
        """Android 13+ needs explicit permission before any notification
        shows; earlier versions grant it at install."""
        if IS_ANDROID:
            try:
                if android_sdk() >= 33 and not android_has_permission(
                        "android.permission.POST_NOTIFICATIONS"):
                    android_request_permissions(
                        ["android.permission.POST_NOTIFICATIONS"])
            except Exception:
                log.exception("notification permission request failed")

    def _bind_notification_taps(self) -> None:
        if not IS_ANDROID:
            return
        try:
            from android import activity
            activity.bind(on_new_intent=self._on_new_intent)
        except Exception:
            log.exception("could not listen for notification taps")

    def _on_new_intent(self, intent) -> None:
        try:
            peer = intent.getStringExtra("fext_peer")
        except Exception:
            peer = None
        if peer:
            run_on_ui(self._open_from_notification, str(peer))

    def _open_from_notification(self, peer: str) -> None:
        if self._known_conversation(peer):
            self.open_conversation(peer)

    def _launch_peer(self) -> Optional[str]:
        """The chat a notification tap asked for when it COLD-started us."""
        if not IS_ANDROID:
            return None
        try:
            autoclass, _ = _jni()
            intent = autoclass("org.kivy.android.PythonActivity"
                               ).mActivity.getIntent()
            peer = intent.getStringExtra("fext_peer") if intent else None
            return str(peer) if peer else None
        except Exception:
            return None

    def _handle_needs_registration(self, message: str) -> None:
        """The relay does not recognise this identity — offer to register.

        Reached when the relay's database has been reset, or when this key
        was never registered here. The keypair is intact, so recovery is
        just picking a name again; only the local claim of having a name is
        dropped.
        """
        if self._register_modal is not None:
            return                       # already asking
        if self.worker is not None:
            self.worker.stop_event.set()
            self.worker = None
        self.keys.clear_username()
        self.chats_screen.refresh_header()
        self._set_status("This relay doesn't recognise your identity — "
                         "pick a username to reconnect", COL_TICK_GOLD)
        reason = ("The relay no longer has a record of this identity "
                  "(its database may have been reset). Your keys are "
                  "safe — choose a name to register again.")
        self._register_modal = RegisterModal(self, reason=reason)
        self._register_modal.bind(
            on_dismiss=lambda *_a: setattr(self, "_register_modal", None))
        self._register_modal.open()

    def retry_now(self) -> None:
        """User-initiated reconnect: re-probe the transport AND skip whatever
        backoff is pending. Re-probing matters because the most common cause
        of a stuck connection is a stale transport choice — the relay gained
        or lost TLS since the choice was cached."""
        ENDPOINT.invalidate()
        self._probe_transport()
        if self.worker is not None and self.worker.is_alive():
            self.worker.nudge()
            self._set_status("Reconnecting…", COL_TEXT_DIM)
        elif self.keys.has_identity and self.keys.username:
            self._start_worker()

    def on_start(self) -> None:
        if platform.system() == "Windows" and DESKTOP_ICO.is_file():
            Clock.schedule_once(
                lambda _dt: windows_window_icons(str(DESKTOP_ICO)), 0)

    def on_pause(self) -> bool:
        self.foreground = False          # background: back off politely
        return True

    def on_resume(self) -> None:
        """Returning to the app must feel instant, not 'wait out the timer'.

        Android suspends sockets in the background, so the worker is very
        often sitting in a long backoff at the moment the user looks at the
        screen again. Nudging here is what turns a 30-second blank stare
        into an immediate reconnect.
        """
        self.foreground = True
        if self.worker is not None and self.worker.is_alive():
            self.worker.nudge()
        elif self.keys.has_identity and self.keys.username:
            self._start_worker()
        # Messages that arrived while we were in the background were left
        # unread on purpose (nobody was looking); now somebody is.
        peer = self.current_peer
        if peer and self.store is not None and \
                self.manager.current == "conversation":
            if IS_ANDROID:
                android_clear_notification(peer)
            if self._auto_read(peer):
                self.mark_peer_read(peer)
        android_style_system_bars()
        self._watch_system_theme(0)

    def _set_status(self, text: str, color: Optional[str] = None) -> None:
        color = retint(color) if color else T.text_3
        self._pending_status = (text, color)
        self.chats_screen.set_status(text, color)

    # =========================================================================
    #  Dialog launchers
    # =========================================================================
    def open_add_contact(self) -> None:
        if self.store is not None and self.keys.username:
            AddContactModal(self).open()
        else:
            self._set_status("Register this identity first", COL_TICK_GOLD)

    def open_contact_settings(self) -> None:
        if self.current_peer is not None:
            ChatHistorySheet(self, self.current_peer).open()

    def open_chat_info(self) -> None:
        peer = self.current_peer
        if peer is None or self.store is None:
            return
        if self.conversation_kind(peer) == "group":
            self.group_info_screen.show(gid_of(peer))
            self._navigate("group_info", "left")
        else:
            self.chat_info_screen.show(peer)
            self._navigate("info", "left")

    def close_chat_info(self) -> None:
        self._navigate("conversation", "right")
        self.refresh_chat(self.current_peer)

    def open_settings(self) -> None:
        self._settings_from = self.manager.current
        self.settings_screen.show()
        self._navigate("settings", "left")

    def close_settings(self) -> None:
        back = getattr(self, "_settings_from", "chats")
        if back not in self.manager.screen_names or back == "settings":
            back = "chats"
        self._navigate(back, "right")
        if back == "chats":
            self.refresh_sidebar()
            self.chats_screen.refresh_header()

    def open_compose(self) -> None:
        if self.store is not None and self.keys.username:
            ComposeSheet(self).open()
        else:
            self._set_status("Register this identity first", T.accent)

    def open_new_group(self) -> None:
        if self.store is None or not self.keys.username:
            self._set_status("Register this identity first", T.accent)
            return
        self.new_group_screen.show()
        self._navigate("new_group", "left")

    def confirm_leave_group(self, gid: str) -> None:
        group = self.store.group(gid) if self.store else None
        if not group:
            return
        sheet = Sheet(f"Leave “{group['name']}”?")
        sheet.add(hint("The others are told you left. Your copy of the "
                       "history stays on this device, read-only; only the "
                       "admin can add you back."))

        def leave(_w):
            sheet.dismiss()
            self.leave_group(gid)
            if self.manager.current == "group_info":
                self.group_info_screen.show(gid)
        sheet.add(button_row(Btn("Stay", kind="quiet",
                                 on_release=lambda _w: sheet.dismiss()),
                             Btn("Leave", kind="solid_danger",
                                 on_release=leave)))
        sheet.open()

    def list_contact(self, address: str) -> None:
        """Put someone known only from a group into the chat list."""
        if self.store is None:
            return
        contact = self.store.get_contact(address)
        if contact is None:
            peer = self.current_peer or ""
            group = self.store.group(gid_of(peer)) if gid_of(peer) else None
            member = next((m for m in (group or {}).get("members", [])
                           if m["a"] == address), None)
            if member is None:
                return
            self.store.upsert_contact(address, member.get("u"), member["k"])
        else:
            self.store.upsert_contact(address, contact.get("username"),
                                      contact["public_key"], listed=True)
        self.refresh_sidebar()
        self.open_conversation(address)

    # ---- per-chat switches (chat info and the ⋮ menu) ------------------------
    def set_notify(self, peer: str, on: bool) -> None:
        self.store.set_contact_setting(peer, "notify", int(on))
        if not on and IS_ANDROID:
            android_clear_notification(peer)
        self.refresh_chat(peer)

    def toggle_notify(self, peer: str) -> None:
        on = not (self.store.get_contact(peer) or {}).get("notify", 1)
        self.set_notify(peer, on)
        self._set_status("Notifications on" if on else "Chat muted", COL_OK)

    def set_ticks(self, peer: str, enabled: bool) -> None:
        """Both ends must agree for 2nd/3rd ticks; the relay enforces it, so
        the preference is synced there (and retried by being re-set)."""
        self.store.set_contact_setting(peer, "ticks_enabled", int(enabled))
        self.refresh_chat(peer)

        def work():
            try:
                self.api.set_ticks(peer, enabled)
            except (requests.RequestException, ApiClient.ApiError):
                self.ui_queue.put(("status", "Ticks preference saved here; "
                                   "the relay hears about it once you're "
                                   "online", COL_TICK_GOLD))
        threading.Thread(target=work, daemon=True).start()

    def set_auto_read(self, peer: str, auto: bool) -> None:
        self.store.set_contact_setting(peer, "auto_read", int(auto))
        if auto:
            self.mark_peer_read(peer)
        self.refresh_chat(peer)


# =============================================================================
#  Background sync service (Android) — runs in its own process
# =============================================================================
class HeadlessApp(MessagingMixin):
    """Everything SyncWorker and the media/send helpers need from an app,
    with no UI: the background service's stand-in for FextApp. Its send
    paths are MessagingMixin's — the very same code the app runs."""

    foreground = False
    current_peer = None

    def __init__(self, keys: "IdentityManager") -> None:
        self.keys = keys
        self.crypto = CryptoEngine(keys)
        self.api = ApiClient(keys)
        self.store = LocalStore(keys.store_path())
        self.ui_queue: "queue.Queue" = queue.Queue()

    def close(self) -> None:
        self.store.close()


def notification_text(store: "LocalStore", peer: str, body: str, kind: str,
                      author: Optional[str] = None) -> tuple:
    """(title, text, muted) for a new message. A group's notification is
    titled with the group and names who wrote."""
    preview = "Photo" if kind == "image" else body
    gid = gid_of(peer)
    if gid:
        group = store.group(gid) or {}
        who = (store.get_contact(author or "") or {}).get("username") or \
            short_addr(author or "", 10)
        return (group.get("name") or "Group", f"{who}: {preview}",
                not group.get("notify", 1))
    contact = store.get_contact(peer) or {}
    return (contact.get("username") or short_addr(peer, 12), preview,
            not contact.get("notify", 1))


def _service_handle(app: HeadlessApp, event: tuple) -> None:
    kind = event[0]
    if kind == "incoming":
        peer, body, msg_kind = event[1], event[2], event[3]
        name, text, muted = notification_text(app.store, peer, body, msg_kind,
                                              event[4] if len(event) > 4
                                              else None)
        if not muted:
            try:
                android_notify(peer, name, text[:160])
            except Exception:
                log.exception("service: notification failed")
    elif kind == "resend_request":
        app._on_resend_request(*event[1:4])
    elif kind == "group_publish":
        threading.Thread(target=app._publish_group_state, args=(event[1],),
                         daemon=True).start()
    # Everything else (status lines, repaint hints, media downloads) is for
    # a screen; the app catches up from the database when it next opens.


def run_background_service(max_cycles: Optional[int] = None,
                           tick: float = 2.0) -> None:
    """The service's main loop.

    Idles while the app's own worker is alive (its heartbeat is fresh), and
    syncs + notifies when it is not. Re-reads the identity file every cycle,
    so switching identity in the app is picked up, and steps aside within a
    few seconds of the app reappearing.
    """
    log.info("background service: started")
    app = worker = None
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        cycles += 1
        keys = IdentityManager()
        active = keys.active if keys.has_identity else None
        wanted = (active is not None and active.get("username")
                  and background_delivery_enabled()
                  and not heartbeat_fresh("app"))
        if worker is not None and (not wanted or
                                   app.keys.address != active["address"]):
            worker.stop_event.set()
            worker.join(timeout=10)
            app.close()
            app = worker = None
            log.info("background service: standing down")
        if wanted and worker is None:
            try:
                app = HeadlessApp(keys)
                worker = SyncWorker(app)
                worker.beat_owner = "service"
                worker.start()
                log.info("background service: syncing %s", keys.address)
            except Exception:
                log.exception("background service: could not start a worker")
                app = worker = None
        if app is not None:
            try:
                while True:
                    _service_handle(app, app.ui_queue.get_nowait())
            except queue.Empty:
                pass
        time.sleep(tick)
    if worker is not None:
        worker.stop_event.set()
        app.close()


# =============================================================================
#  Entrypoint
# =============================================================================
def main() -> None:
    FextApp().run()


if __name__ == "__main__":
    main()
