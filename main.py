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

from kivy.animation import Animation
from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.image import Image as CoreImage
from kivy.core.window import Window
from kivy.graphics import Color, Ellipse, Line, Mesh, Rectangle, \
    RoundedRectangle, Triangle
from kivy.metrics import dp, sp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image as KivyImage
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.screenmanager import NoTransition, Screen, ScreenManager, \
    SlideTransition
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import get_color_from_hex
from kivy.utils import platform as kivy_platform

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
APP_VERSION = "9.0.0"

# The identity/QR payload keeps carrying the ORIGINAL tag. It is wire data
# that older clients already produce and consume, so renaming the app must
# not rename it — the brand changed, the protocol did not.
WIRE_APP_TAG = "FEXT"

# Bundled artwork. Absent on a stripped-down checkout, so every use is
# guarded: the loading screen simply drops the mark rather than failing.
ASSET_DIR = Path(__file__).resolve().parent / "data"
LOGO_FILE = ASSET_DIR / "ferritext_logo.png"

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

# ---- Palette -----------------------------------------------------------------
# A beige chat background, a dark brown app bar, gold bubbles for your own
# messages and cream cards everywhere else. The one non-negotiable brand
# element is the golden tick.
COL_BG           = "#F6EEDF"    # chat background
COL_BG_LIST      = "#FDF8EE"    # conversation list background (cream)
COL_CARD         = "#FFFFFF"    # cards, inputs, incoming bubbles
COL_CARD_EDGE    = "#EADDC6"    # soft warm borders
COL_APPBAR       = "#6B4632"    # dark brown
COL_APPBAR_TEXT  = "#FFF6E8"    # warm white on the app bar
COL_APPBAR_DIM   = "#D9BFA5"    # subdued text on the app bar
COL_BUBBLE_ME    = "#FFE2AC"    # gold — outgoing bubbles
COL_BUBBLE_PEER  = "#FFFFFF"    # incoming bubbles
COL_TEXT         = "#3D2E22"    # espresso
COL_TEXT_DIM     = "#9C8A76"    # latte
COL_ACCENT       = "#D98E2B"    # gold — primary actions
COL_ACCENT_DARK  = "#B9741C"    # pressed state
COL_DANGER       = "#C25B4A"    # warm brick
COL_OK           = "#69945F"    # sage
COL_TICK_GOLD    = "#C9871B"    # the golden ticks
COL_TICK_GREY    = "#B3A48F"    # 0/3 — not yet on the server
COL_PRESSED      = "#F1E5CF"    # row press feedback
COL_DIVIDER      = "#EFE4D2"    # hairlines between list rows
COL_QUOTE_ME     = "#F7CF86"    # reply-quote ground inside an outgoing bubble
COL_QUOTE_PEER   = "#F7EFE2"    # ... and inside an incoming one
COL_SELECT_RGBA  = (0.851, 0.557, 0.169, 0.18)   # gold wash on selected rows

# Avatar hues, assigned by address hash — every contact gets a stable, warm
# colour of their own.
AVATAR_PALETTE = ["#D98E2B", "#C25B4A", "#8A9A5B", "#B07B9E",
                  "#5E8C8A", "#C97F5D", "#A9853F", "#7E9C6B"]


def C(hex_color: str) -> list:
    """Hex string -> kivy rgba. UI-side only; workers keep hex strings."""
    return get_color_from_hex(hex_color)

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


def qr_texture(data: str, fg: str = COL_TEXT, bg: str = "#FFFFFF"):
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
    """

    #: Current schema generation, recorded in PRAGMA user_version.
    #:   0/1  up to v8.0.2 (receipt_sent added by inspection in v7.2)
    #:   2    v9.0.0: message kinds, extension metadata, stars, media state,
    #:        per-contact notifications and capabilities, tombstones
    SCHEMA_VERSION = 2

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
                       public_key: str) -> None:
        with self._lock:
            self._db.execute(
                """INSERT INTO contacts (address, username, public_key, added_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(address) DO UPDATE SET
                       username = COALESCE(excluded.username, contacts.username),
                       public_key = excluded.public_key""",
                (address, username, public_key, now_iso()))
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

    def contacts(self) -> list:
        """Every contact of this identity, for pickers (forward-to)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM contacts ORDER BY COALESCE(username, address)"
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
                media_state: str = "") -> int:
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

    # Outcomes of add_in(). The caller must be able to tell these apart,
    # because only two of the three make it safe to acknowledge delivery.
    STORED = "stored"          # plaintext is now durably ours
    DUPLICATE = "duplicate"    # we already hold this exact message
    FAILED = "failed"          # NOT stored — do not ack, do not lose it

    def add_in(self, peer: str, body: str, created_at: str,
               server_id: Optional[int], client_msg_id: Optional[str],
               unread: bool = True, kind: str = "text",
               ext: Optional[dict] = None, media_state: str = "") -> str:
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
                           AND peer = ? AND direction = 'in'""",
                        (client_msg_id, peer)).fetchone()
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
                                             ext, media_state)
                       VALUES (?, ?, ?, 'in', ?, 0, ?, ?, ?, ?, ?, ?)""",
                    (server_id, client_msg_id, peer, body,
                     0 if unread else 1, created_at, 0 if unread else 1,
                     kind,
                     json.dumps(ext, separators=(",", ":")) if ext else None,
                     media_state))
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

    def conversations(self) -> list:
        """Distinct peers by recent activity, with preview + unread count."""
        with self._lock:
            rows = self._db.execute(
                """SELECT m.peer, c.username, c.ticks_enabled, c.notify,
                          (SELECT body FROM messages WHERE peer = m.peer
                             ORDER BY id DESC LIMIT 1) AS preview,
                          (SELECT created_at FROM messages
                             WHERE peer = m.peer ORDER BY id DESC LIMIT 1)
                             AS last_at,
                          SUM(CASE WHEN m.direction = 'in' AND m.read = 0
                              THEN 1 ELSE 0 END) AS unread,
                          MAX(m.id) AS last_id
                   FROM messages m
                   LEFT JOIN contacts c ON c.address = m.peer
                   GROUP BY m.peer ORDER BY last_id DESC""").fetchall()
            contact_only = self._db.execute(
                """SELECT address AS peer, username, ticks_enabled, notify,
                          NULL AS preview, added_at AS last_at,
                          0 AS unread, 0 AS last_id
                   FROM contacts
                   WHERE address NOT IN (SELECT DISTINCT peer FROM messages)
                   ORDER BY added_at DESC""").fetchall()
        return [dict(r) for r in rows] + [dict(r) for r in contact_only]

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
        relay cannot resurrect it.
        """
        rows = self.messages_by_ids(local_ids)
        if not rows:
            return []
        with self._lock:
            for row in rows:
                self._tombstone(row["peer"], [row["client_msg_id"]])
            self._db.executemany("DELETE FROM messages WHERE id = ?",
                                 [(r["id"],) for r in rows])
            self._db.commit()
        return rows

    def delete_by_author(self, author: str, cids: list) -> list:
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
        with self._lock:
            rows = [dict(r) for r in self._db.execute(
                f"""SELECT * FROM messages WHERE peer = ? AND direction = 'in'
                    AND client_msg_id IN ({marks})""",
                [author] + cids).fetchall()]
            self._tombstone(author, cids)
            if rows:
                self._db.executemany("DELETE FROM messages WHERE id = ?",
                                     [(r["id"],) for r in rows])
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
        # Verified sender keys come from INSIDE the authenticated ciphertext
        # — trust them over anything the directory says.
        try:
            self.app.store.upsert_contact(sender, inner.get("username"),
                                          inner["public_key"])
        except sqlite3.Error:
            log.exception("could not update contact record for %s", sender)
            MessageFlowLog.dropped(server_id, who, "contact upsert failed")
            return None

        x = payload.get("x")
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
                return self._handle_control(server_id, sender, cid,
                                            x.get("ctl"), who)
            ext = sanitize_ext(x) or None
            media = (ext or {}).get("media")
            if x.get("k") == "media" and media is not None:
                kind, media_state = media["kind"], "pending"

        body = sanitize_text(str(payload.get("body", "")))
        outcome = self.app.store.add_in(
            peer=sender, body=body,
            created_at=payload.get("sent_at",
                                   msg.get("created_at", now_iso())),
            server_id=server_id, client_msg_id=cid, kind=kind, ext=ext,
            media_state=media_state)

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
        self.app.ui_queue.put(("message_stored", sender))
        # Separate from message_stored, which also fires for receipts and
        # deletions: THIS one means a new message someone may want to hear
        # about, and is what notifications are raised from.
        self.app.ui_queue.put(("incoming", sender, body, kind))
        if media_state == "pending":
            self.app.ui_queue.put(("media_pending", sender, cid))
        return server_id

    def _handle_control(self, server_id, sender: str, cid: Optional[str],
                        ctl, who: str) -> Optional[int]:
        """Act on a control message (v9). Nothing is stored for one — it is
        an instruction, not a message — and it is always acknowledged, since
        a malformed or unknown instruction is not going to improve by being
        re-delivered."""
        store = self.app.store
        if not isinstance(ctl, dict) or not store.consume_control(sender, cid):
            return server_id
        op = ctl.get("op")
        if op == "delete":
            cids = ctl.get("cids") if isinstance(ctl.get("cids"), list) else []
            removed = store.delete_by_author(sender, cids)
            for row in removed:
                discard_media_file(row.get("media_path"))
            flow_log.info("✗   in   ctl  from %s  deleted %d of %d message(s) "
                          "for both sides", who, len(removed), len(cids))
            self.app.ui_queue.put(("message_stored", sender))
        elif op == "resend":
            # They could not fetch a photo/voice note we sent — the relay
            # has let it expire. Our device still has the file: upload again.
            target = _str(ctl.get("cid"), 64)
            if target:
                self.app.ui_queue.put(("resend_request", sender, target))
        elif op == "remedia":
            target = _str(ctl.get("cid"), 64)
            media = sanitize_media(ctl.get("media"))
            row = (store.message_by_cid(sender, target, "in")
                   if target and media else None)
            if row is not None:
                ext = json.loads(row.get("ext") or "{}")
                ext["media"] = media
                store.set_media(row["id"], state="pending", ext=ext)
                self.app.ui_queue.put(("message_stored", sender))
                self.app.ui_queue.put(("media_pending", sender, target))
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
#  Cozy widget kit — small, warm building blocks used by every screen
# =============================================================================
def paint_round(widget, hex_color: str, radius=None, corners=None):
    """Rounded warm surface behind a widget. Returns the Color instruction so
    callers can retint it (press feedback, selection)."""
    with widget.canvas.before:
        col = Color(*C(hex_color))
        rect = RoundedRectangle(
            pos=widget.pos, size=widget.size,
            radius=corners if corners is not None else [radius or dp(12)])

    def _sync(*_a):
        rect.pos, rect.size = widget.pos, widget.size
    widget.bind(pos=_sync, size=_sync)
    return col, rect


def paint_border(widget, hex_color: str, radius=None, width=1.1):
    with widget.canvas.after:
        col = Color(*C(hex_color))
        line = Line(width=dp(width) / 2.0, rounded_rectangle=(
            widget.x, widget.y, widget.width, widget.height,
            radius or dp(12)))

    def _sync(*_a):
        line.rounded_rectangle = (widget.x, widget.y, widget.width,
                                  widget.height, radius or dp(12))
    widget.bind(pos=_sync, size=_sync)
    return col


def paint_circle(widget, hex_color: str):
    with widget.canvas.before:
        col = Color(*C(hex_color))
        circ = Ellipse(pos=widget.pos, size=widget.size)

    def _sync(*_a):
        circ.pos, circ.size = widget.pos, widget.size
    widget.bind(pos=_sync, size=_sync)
    return col


class CozyButton(ButtonBehavior, Label):
    """Rounded, flat button. `filled` gives a gold primary;
    `outline` gives a quiet bordered secondary."""

    def __init__(self, text="", bg=COL_ACCENT, fg="#FFFFFF", outline=None,
                 radius=None, font_size=None, bold=True, pressed=None, **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(44))
        super().__init__(text=text, **kw)
        self.bold = bold
        self.font_size = font_size or sp(15)
        self.color = C(fg)
        self._bg_hex, self._pressed_hex = bg, pressed or COL_ACCENT_DARK
        radius = radius if radius is not None else dp(12)
        self._bg_col, _ = paint_round(self, bg, radius=radius)
        if outline:
            paint_border(self, outline, radius=radius)
            self._pressed_hex = pressed or COL_PRESSED
        self.bind(state=self._on_state)

    def _on_state(self, _w, state):
        self._bg_col.rgba = C(self._pressed_hex if state == "down"
                              else self._bg_hex)


def draw_icon(name: str, cx: float, cy: float, size: float, rgba,
              width: Optional[float] = None) -> None:
    """Draw one vector icon into the CURRENT canvas context.

    Every icon in the app is drawn rather than typed: Kivy's bundled Roboto
    has no glyphs for symbols (stars, bells, arrows, ticks all render as
    empty boxes), and drawing also keeps them crisp at any density. Each
    icon fits a `size` square centred on (cx, cy).
    """
    h = size / 2.0
    w = width or max(dp(1.4), size * 0.08)
    Color(*rgba)
    line = dict(width=w, cap="round", joint="round")
    if name == "back":
        Line(points=[cx + h * .30, cy + h * .62, cx - h * .32, cy,
                     cx + h * .30, cy - h * .62], **line)
    elif name == "close":
        k = h * .55
        Line(points=[cx - k, cy - k, cx + k, cy + k], **line)
        Line(points=[cx - k, cy + k, cx + k, cy - k], **line)
    elif name == "more":                       # vertical ellipsis
        r = max(dp(1.7), size * .09)
        for dy in (-h * .58, 0, h * .58):
            Ellipse(pos=(cx - r, cy + dy - r), size=(2 * r, 2 * r))
    elif name == "plus":
        k = h * .62
        Line(points=[cx - k, cy, cx + k, cy], **line)
        Line(points=[cx, cy - k, cx, cy + k], **line)
    elif name == "send":
        Triangle(points=[cx - h * .78, cy + h * .72, cx - h * .78, cy - h * .72,
                         cx + h * .88, cy])
    elif name in ("reply", "forward"):
        d = -1 if name == "reply" else 1       # which way the arrow points
        tip = (cx + d * h * .72, cy + h * .22)
        Line(points=[tip[0] - d * h * .48, tip[1] + h * .46, tip[0], tip[1],
                     tip[0] - d * h * .48, tip[1] - h * .46], **line)
        Line(bezier=[tip[0], tip[1], cx - d * h * .55, cy + h * .22,
                     cx - d * h * .78, cy - h * .05,
                     cx - d * h * .72, cy - h * .68], **line)
    elif name == "copy":
        r = size * .09
        Line(rounded_rectangle=(cx - h * .72, cy - h * .78, h * 1.0, h * 1.18,
                                r), width=w)
        Line(points=[cx - h * .40, cy + h * .44, cx - h * .40, cy + h * .74,
                     cx + h * .72, cy + h * .74, cx + h * .72, cy - h * .36,
                     cx + h * .32, cy - h * .36], **line)
    elif name in ("star", "star_filled"):
        pts = []
        for i in range(10):
            ang = math.pi / 2 + i * math.pi / 5
            rad = h * (.86 if i % 2 == 0 else .38)
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
        r = size * .12
        a, b, c = ((cx + h * .52, cy + h * .58), (cx - h * .52, cy),
                   (cx + h * .52, cy - h * .58))
        Line(points=[a[0], a[1], b[0], b[1], c[0], c[1]], **line)
        for px, py in (a, b, c):
            Ellipse(pos=(px - r, py - r), size=(2 * r, 2 * r))
    elif name == "delete":
        top = cy + h * .52
        Line(points=[cx - h * .74, top, cx + h * .74, top], **line)
        Line(points=[cx - h * .26, top, cx - h * .26, top + h * .24,
                     cx + h * .26, top + h * .24, cx + h * .26, top], **line)
        Line(points=[cx - h * .56, top, cx - h * .44, cy - h * .78,
                     cx + h * .44, cy - h * .78, cx + h * .56, top], **line)
        for dx in (-.17, .17):
            Line(points=[cx + dx * h, cy + h * .28, cx + dx * h * .8,
                         cy - h * .52], width=w * .8, cap="round")
    elif name == "edit":                       # a pen, nib bottom-left
        ux, uy = .7071, .7071                  # along the pen
        vx, vy = -.7071, .7071                 # across it
        t = h * .24
        nib = (cx - h * .70, cy - h * .70)
        neck = (nib[0] + ux * h * .48, nib[1] + uy * h * .48)
        end = (cx + h * .70, cy + h * .70)
        body = [neck[0] + vx * t, neck[1] + vy * t,
                end[0] + vx * t, end[1] + vy * t,
                end[0] - vx * t, end[1] - vy * t,
                neck[0] - vx * t, neck[1] - vy * t]
        Line(points=body, close=True, **line)
        Line(points=[neck[0] + vx * t, neck[1] + vy * t, nib[0], nib[1],
                     neck[0] - vx * t, neck[1] - vy * t], **line)
    elif name == "image":
        Line(rounded_rectangle=(cx - h * .82, cy - h * .66, h * 1.64,
                                h * 1.32, size * .1), width=w)
        r = size * .09
        Ellipse(pos=(cx + h * .32 - r, cy + h * .26 - r), size=(2 * r, 2 * r))
        Line(points=[cx - h * .80, cy - h * .46, cx - h * .26, cy + h * .06,
                     cx + h * .12, cy - h * .28, cx + h * .38, cy - h * .06,
                     cx + h * .80, cy - h * .48], **line)
    elif name == "mic":
        Line(rounded_rectangle=(cx - h * .27, cy - h * .06, h * .54, h * .92,
                                h * .27), width=w)
        Line(circle=(cx, cy + h * .14, h * .52, 90, 270), width=w)
        Line(points=[cx, cy - h * .38, cx, cy - h * .74], **line)
        Line(points=[cx - h * .30, cy - h * .74, cx + h * .30, cy - h * .74],
             **line)
    elif name == "play":
        Triangle(points=[cx - h * .40, cy + h * .58, cx - h * .40, cy - h * .58,
                         cx + h * .62, cy])
    elif name == "pause":
        bw = size * .14
        for dx in (-h * .30, h * .30):
            Rectangle(pos=(cx + dx - bw / 2, cy - h * .56), size=(bw, h * 1.12))
    elif name in ("bell", "bell_off"):
        base = cy - h * .40
        Line(points=[cx - h * .72, base, cx + h * .72, base], **line)
        Line(bezier=[cx - h * .58, base, cx - h * .44, cy + h * .30,
                     cx - h * .52, cy + h * 1.02, cx, cy + h * .66], **line)
        Line(bezier=[cx + h * .58, base, cx + h * .44, cy + h * .30,
                     cx + h * .52, cy + h * 1.02, cx, cy + h * .66], **line)
        r = size * .085
        Ellipse(pos=(cx - r, base - h * .26 - r), size=(2 * r, 2 * r))
        Ellipse(pos=(cx - r * .8, cy + h * .66), size=(1.6 * r, 1.6 * r))
        if name == "bell_off":
            Line(points=[cx - h * .78, cy - h * .78, cx + h * .78,
                         cy + h * .78], **line)
    elif name == "check":
        Line(points=[cx - h * .60, cy + h * .02, cx - h * .16, cy - h * .42,
                     cx + h * .64, cy + h * .46], **line)
    elif name == "download":
        Line(points=[cx, cy + h * .66, cx, cy - h * .30], **line)
        Line(points=[cx - h * .40, cy + h * .06, cx, cy - h * .32,
                     cx + h * .40, cy + h * .06], **line)
        Line(points=[cx - h * .62, cy - h * .70, cx + h * .62, cy - h * .70],
             **line)
    elif name == "retry":
        rad = h * .58
        Line(circle=(cx, cy, rad, 60, 330), width=w)
        # Arrowhead at the 60-degree end, pointing back along the arc
        # (Kivy measures circle angles clockwise from 12 o'clock).
        ang = math.radians(60)
        ex, ey = cx + rad * math.sin(ang), cy + rad * math.cos(ang)
        tx, ty = -math.cos(ang), math.sin(ang)       # counter-clockwise
        nx, ny = math.sin(ang), math.cos(ang)        # outward normal
        k = h * .62
        Triangle(points=[ex + tx * k, ey + ty * k,
                         ex + nx * k * .62, ey + ny * k * .62,
                         ex - nx * k * .62, ey - ny * k * .62])
    elif name == "info":
        Line(circle=(cx, cy, h * .82), width=w)
        Line(points=[cx, cy - h * .42, cx, cy - h * .02], **line)
        r = size * .075
        Ellipse(pos=(cx - r, cy + h * .38 - r), size=(2 * r, 2 * r))


class IconButton(ButtonBehavior, Widget):
    """A round, tappable vector icon (see draw_icon for the set).

    `bg` gives it a filled circle (the send and new-chat buttons); without
    one it is a bare icon with a soft circular press highlight, which is how
    app-bar actions behave in every modern Android app.
    """

    def __init__(self, icon: str, tint=COL_APPBAR_TEXT, bg=None,
                 icon_size=None, press=None, **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("size", (dp(44), dp(44)))
        super().__init__(**kw)
        self._icon, self._tint, self._bg_hex = icon, tint, bg
        self._icon_size = icon_size
        self._press_rgba = (C(press) if press else
                            [1, 1, 1, .16] if bg is None else None)
        self.bind(pos=self._redraw, size=self._redraw, state=self._redraw,
                  disabled=self._redraw)
        self._redraw()

    def set_icon(self, icon: str, tint: Optional[str] = None) -> None:
        self._icon = icon
        if tint is not None:
            self._tint = tint
        self._redraw()

    def _redraw(self, *_a):
        self.canvas.clear()
        side = min(self.width, self.height)
        with self.canvas:
            if self._bg_hex is not None:
                Color(*C(COL_ACCENT_DARK if self.state == "down"
                         else self._bg_hex))
                Ellipse(pos=(self.center_x - side / 2,
                             self.center_y - side / 2), size=(side, side))
            elif self.state == "down" and self._press_rgba:
                Color(*self._press_rgba)
                Ellipse(pos=(self.center_x - side / 2,
                             self.center_y - side / 2), size=(side, side))
            rgba = C(self._tint)
            if self.disabled:
                rgba = rgba[:3] + [rgba[3] * .35]
            draw_icon(self._icon, self.center_x, self.center_y,
                      self._icon_size or side * .46, rgba)


class TickMarks(Widget):
    """Delivery ticks drawn as vector paths instead of text glyphs.

    v7 fix: these used to be U+2713 CHECK MARK printed through a Label, and
    Kivy's bundled Roboto has no glyph for that codepoint — so the ticks
    rendered as empty rectangles ("tofu") on desktop Linux and on any device
    whose font fallback chain lacks it. Drawing the strokes removes the font
    dependency completely, the same approach IconButton already uses for the
    app-bar icons, and it renders identically on every platform.

    Ticks overlap slightly so 2 and 3 read as a group rather than a row of
    separate marks, which is the visual language people already know.
    """

    def __init__(self, count: int = 0, tint: str = COL_TICK_GOLD,
                 mark: float = None, **kw):
        kw.setdefault("size_hint", (None, None))
        self._mark = mark or dp(9.0)         # height of one checkmark
        self._step = self._mark * 0.52       # horizontal overlap between marks
        self._count, self._tint = max(0, min(int(count), 3)), tint
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

    def _redraw(self, *_a):
        self.canvas.clear()
        if self._count <= 0:
            return
        h = self._mark
        with self.canvas:
            Color(*C(self._tint))
            for i in range(self._count):
                x = self.x + i * self._step
                y = self.y
                # A checkmark: short down-stroke into a long up-stroke.
                Line(points=[x, y + h * 0.45,
                             x + h * 0.34, y + h * 0.06,
                             x + h * 0.92, y + h * 0.82],
                     width=dp(1.15), cap="round", joint="round")


class Avatar(AnchorLayout):
    """Warm coloured circle with the contact's initial — the cozy face of
    every conversation."""

    def __init__(self, seed: str, name: str, diameter=dp(48), **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("size", (diameter, diameter))
        super().__init__(anchor_x="center", anchor_y="center", **kw)
        paint_circle(self, avatar_color(seed))
        self.add_widget(Label(text=initial_of(name), bold=True,
                              font_size=diameter * 0.42,
                              color=C("#FFFFFF")))


class AvatarButton(ButtonBehavior, Avatar):
    """Tappable avatar. ButtonBehavior sits first in the MRO and accepts only
    keyword arguments, so the positional args are forwarded as keywords."""

    def __init__(self, seed: str, name: str, diameter=dp(48), **kw):
        super().__init__(seed=seed, name=name, diameter=diameter, **kw)


class CozyInput(BoxLayout):
    """A rounded text input (white card, gold cursor)."""

    def __init__(self, hint="", password=False, multiline=False,
                 on_enter=None, radius=None, **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(46))
        kw.setdefault("padding", [dp(14), dp(8), dp(10), dp(8)])
        super().__init__(**kw)
        paint_round(self, COL_CARD, radius=radius or dp(14))
        paint_border(self, COL_CARD_EDGE, radius=radius or dp(14))
        self.input = TextInput(
            hint_text=hint, multiline=multiline, password=password,
            background_normal="", background_active="",
            background_color=(0, 0, 0, 0),
            foreground_color=C(COL_TEXT), hint_text_color=C(COL_TEXT_DIM),
            cursor_color=C(COL_ACCENT), font_size=sp(15),
            write_tab=False, padding=[0, dp(9), 0, 0])
        if on_enter is not None:
            self.input.bind(on_text_validate=lambda _w: on_enter())
        self.add_widget(self.input)

    @property
    def text(self) -> str:
        return self.input.text

    @text.setter
    def text(self, value: str) -> None:
        self.input.text = value


class SectionCard(BoxLayout):
    """White rounded card with a small gold section title."""

    def __init__(self, title: str, **kw):
        kw.setdefault("orientation", "vertical")
        kw.setdefault("size_hint_y", None)
        kw.setdefault("padding", [dp(14), dp(12), dp(14), dp(12)])
        kw.setdefault("spacing", dp(8))
        super().__init__(**kw)
        self.bind(minimum_height=self.setter("height"))
        paint_round(self, COL_CARD, radius=dp(16))
        paint_border(self, COL_CARD_EDGE, radius=dp(16))
        self.add_widget(InfoLabel(title, color=COL_ACCENT, bold=True,
                                  font_size=sp(12.5)))


class InfoLabel(Label):
    """Left-aligned wrapping text label that sizes to its content."""

    def __init__(self, text="", color=COL_TEXT, font_size=None, bold=False,
                 halign="left", **kw):
        kw.setdefault("size_hint_y", None)
        super().__init__(text=text, color=C(color), bold=bold,
                         font_size=font_size or sp(14), halign=halign,
                         valign="top", markup=True, **kw)
        self.bind(width=lambda *_: setattr(self, "text_size",
                                           (self.width, None)),
                  texture_size=lambda *_: setattr(self, "height",
                                                  self.texture_size[1]))


class CozyModal(ModalView):
    """Base for every dialog: dimmed backdrop, warm rounded card, title."""

    def __init__(self, title: str, dismissable: bool = True, **kw):
        kw.setdefault("size_hint", (0.92, None))
        kw.setdefault("auto_dismiss", dismissable)
        super().__init__(background_color=(0, 0, 0, 0),
                         overlay_color=(0.18, 0.12, 0.07, 0.55), **kw)
        self.card = BoxLayout(orientation="vertical",
                              padding=[dp(18), dp(16), dp(18), dp(16)],
                              spacing=dp(10))
        paint_round(self.card, COL_BG_LIST, radius=dp(20))
        self.card.bind(minimum_height=self._fit_height)
        self.add_widget(self.card)
        self.card.add_widget(InfoLabel(title, bold=True, font_size=sp(18)))

    def _fit_height(self, _w, value):
        self.height = min(value, Window.height * 0.92)

    def add(self, widget):
        self.card.add_widget(widget)
        return widget

    def status_label(self) -> InfoLabel:
        label = InfoLabel("", color=COL_TEXT_DIM, font_size=sp(13))
        self.add(label)
        return label

    def set_status(self, label: InfoLabel, text: str,
                   color: str = COL_TEXT_DIM) -> None:
        label.color = C(color)
        label.text = text


# =============================================================================
#  Dialogs
# =============================================================================
class RegisterModal(CozyModal):
    """Pick a username for the ACTIVE identity.

    Registration sends only public values (username + public key + ownership
    signature); the server recomputes the address and enforces the 'Fec'
    proof-of-work.

    v7: this dialog is also the RECOVERY path. It is reopened when the relay
    reports that it does not know this address — which happens when the
    relay's database is reset, or when the app was restored onto a device
    whose identity was never registered here. Previously that state showed
    "Realtime auth failed" in the status ribbon forever, with no way to pick
    a new name and no way out except deleting app data.

    It now handles all three server answers distinctly:
      * username_taken      -> keep the dialog open, ask for another name
      * address_registered  -> this key already has a name here; offer to
                               adopt it in one tap rather than dead-ending
      * success             -> register and start syncing
    """

    def __init__(self, app: "FextApp", reason: str = "") -> None:
        super().__init__("Pick your name", dismissable=False)
        self.app = app
        self._suggested: Optional[str] = None
        if reason:
            self.add(InfoLabel(reason, color=COL_TICK_GOLD,
                               font_size=sp(13)))
        self.add(InfoLabel(f"for identity {short_addr(app.keys.address, 16)}"
                           "\n3–32 characters: letters, digits, underscore",
                           color=COL_TEXT_DIM, font_size=sp(13)))
        self.entry = self.add(CozyInput(hint="username",
                                        on_enter=self._submit))
        self.status = self.status_label()
        self.button = self.add(CozyButton("Register", on_release=lambda
                                          _w: self._submit()))
        # Shown only when the relay tells us this key already has a name.
        self.adopt_button = CozyButton(
            "", bg=COL_BG_LIST, fg=COL_ACCENT_DARK, outline=COL_ACCENT,
            on_release=lambda _w: self._adopt())
        self.check_label = self.add(InfoLabel("", color=COL_TEXT_DIM,
                                              font_size=sp(12.5)))

    def _submit(self) -> None:
        username = self.entry.text.strip()
        if not valid_username(username):
            self.set_status(self.status, "That name doesn't fit the format "
                            "yet — 3–32 letters, digits or _", COL_DANGER)
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
                          f"'{username}' is already taken — try another name.")
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
        """This key is already registered under `existing`. Let the user take
        that name back instead of leaving them stuck."""
        self.button.disabled = False
        if not existing:
            self.set_status(self.status, "This identity is already "
                            "registered on the relay.", COL_DANGER)
            return
        self._suggested = existing
        self.set_status(self.status,
                        f"This identity is already registered as "
                        f"'{existing}'.", COL_TICK_GOLD)
        self.adopt_button.text = f"Continue as {existing}"
        if self.adopt_button.parent is None:
            self.add(self.adopt_button)

    def _adopt(self) -> None:
        if self._suggested:
            self._succeed(self._suggested)

    def _fail(self, message: str) -> None:
        self.set_status(self.status, message, COL_DANGER)
        self.button.disabled = False

    def _succeed(self, username: str) -> None:
        self.app.keys.set_username(username)
        self.dismiss()
        self.app.on_registered()


class AddContactModal(CozyModal):
    """Find a contact by username OR by their Fec… address."""

    def __init__(self, app: "FextApp") -> None:
        super().__init__("Add a contact")
        self.app = app
        self.add(InfoLabel("Enter their username or Fec… address",
                           color=COL_TEXT_DIM, font_size=sp(13)))
        self.entry = self.add(CozyInput(hint="username or address",
                                        on_enter=self._submit))
        self.status = self.status_label()
        self.button = self.add(CozyButton("Look up",
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
                run_on_ui(self._fail, "That is neither a valid username nor "
                          "a valid Fec… address.")
                return
            if user["address"] == self.app.keys.address:
                run_on_ui(self._fail, "That's you — no need to add yourself.")
                return
            self.app.store.upsert_contact(user["address"], user["username"],
                                          user["public_key"])
        except ApiClient.ApiError as exc:
            run_on_ui(self._fail,
                      "No such user." if exc.status == 404 else str(exc))
        except requests.RequestException as exc:
            run_on_ui(self._fail, describe_network_error(exc))
        else:
            run_on_ui(self._succeed, user["address"])

    def _fail(self, message: str) -> None:
        self.set_status(self.status, message, COL_DANGER)
        self.button.disabled = False

    def _succeed(self, address: str) -> None:
        self.dismiss()
        self.app.on_contact_added(address)


class NewIdentityModal(CozyModal):
    """Create a new identity, either by MINING a fresh key until its address
    starts with 'Fec' (live attempt counter) or by IMPORTING an existing
    private key (raw hex or WIF — the same formats FEXT Core uses).
    Imported keys whose address does not meet the PoW are rejected up front
    with a clear explanation, because the server would reject them anyway."""

    def __init__(self, app: "FextApp", first_run: bool = False) -> None:
        super().__init__(f"Welcome to {APP_NAME}" if first_run else
                         "Create an identity", dismissable=False)
        self.app = app
        self.first_run = first_run
        self._mining = False
        self._cancel_mining = False
        self.add(InfoLabel(
            "FEXT identities are Bitcoin-compatible secp256k1 keys. The "
            "network only accepts addresses starting with \u201cFec\u201d, "
            "so new keys are mined until one matches (a few seconds).",
            color=COL_TEXT_DIM, font_size=sp(13)))
        self.add(InfoLabel("Identity label (stays on this device)",
                           font_size=sp(13)))
        self.label_entry = self.add(CozyInput(hint="e.g. Personal"))
        self.mine_button = self.add(CozyButton(
            "Mine my new Fec identity", on_release=lambda
            _w: self._start_mining()))
        self.progress = self.add(InfoLabel("", color=COL_TICK_GOLD,
                                           font_size=sp(13),
                                           halign="center"))
        self.add(InfoLabel("— or import an existing private key —",
                           color=COL_TEXT_DIM, font_size=sp(12.5),
                           halign="center"))
        self.key_entry = self.add(CozyInput(
            hint="64-char hex  or  WIF (K… / L… / 5…)", password=True))
        self.import_button = self.add(CozyButton(
            "Import key", bg=COL_BG_LIST, fg=COL_ACCENT_DARK,
            outline=COL_ACCENT, on_release=lambda _w: self._import_key()))
        self.status = self.status_label()
        if not first_run:
            self.add(CozyButton("Cancel", bg=COL_BG_LIST, fg=COL_TEXT_DIM,
                                outline=COL_CARD_EDGE,
                                on_release=lambda _w: self._close()))

    # ---- mining --------------------------------------------------------------
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
            if now - last_ui[0] < 0.15:        # keep the UI queue calm
                return
            last_ui[0] = now
            rate = attempts / max(now - started, 0.001)
            run_on_ui(setattr, self.progress, "text",
                      f"Mining… {attempts:,} attempts ({rate:,.0f}/s)")

        result = mine_identity(should_stop=lambda: self._cancel_mining,
                               progress=progress)
        if result is None:                     # cancelled
            return
        priv, _pub, address, attempts = result
        run_on_ui(self._finish, priv, address, attempts)

    def _finish(self, priv: int, address: str, attempts: int) -> None:
        self.progress.text = f"Found {address}\nafter {attempts:,} attempts"
        self._save(priv)

    # ---- import --------------------------------------------------------------
    def _import_key(self) -> None:
        text = self.key_entry.text.strip()
        if not text:
            return
        try:
            priv = parse_private_key(text)
        except ValueError as exc:
            self.set_status(self.status, f"Cannot parse key: {exc}",
                            COL_DANGER)
            return
        self._save(priv)

    # ---- shared save path ----------------------------------------------------
    def _save(self, priv: int) -> None:
        label = self.label_entry.text.strip() or \
            f"Identity {len(self.app.keys.identities) + 1}"
        try:
            address = self.app.keys.add_identity(priv, label)
        except ValueError as exc:
            self.set_status(self.status, str(exc), COL_DANGER)
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


class ImportChooserModal(CozyModal):
    """Pick a .json chat export to import (cross-platform file picker)."""

    def __init__(self, on_pick: Callable[[str], None]) -> None:
        super().__init__("Choose a chat export (.json)")
        self._on_pick = on_pick
        start = EXPORT_DIR if EXPORT_DIR.exists() else DATA_DIR
        self.chooser = FileChooserListView(path=str(start),
                                           filters=["*.json"],
                                           size_hint_y=None,
                                           height=dp(340))
        self.add(self.chooser)
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(10))
        row.add_widget(CozyButton("Cancel", bg=COL_BG_LIST, fg=COL_TEXT_DIM,
                                  outline=COL_CARD_EDGE,
                                  on_release=lambda _w: self.dismiss()))
        row.add_widget(CozyButton("Import this file",
                                  on_release=lambda _w: self._pick()))
        self.add(row)

    def _pick(self) -> None:
        if self.chooser.selection:
            path = self.chooser.selection[0]
            self.dismiss()
            self._on_pick(path)


class DropMenu(ModalView):
    """The ⋮ overflow menu: a small card dropped from the top-right corner,
    dismissed by tapping anywhere else."""

    ITEM_H = dp(48)

    def __init__(self, items: list, **kw):
        super().__init__(background_color=(0, 0, 0, 0),
                         overlay_color=(0, 0, 0, 0.08), auto_dismiss=True,
                         size_hint=(None, None), **kw)
        self.width = dp(236)
        self.height = self.ITEM_H * len(items) + dp(12)
        card = BoxLayout(orientation="vertical", padding=[0, dp(6), 0, dp(6)])
        paint_round(card, COL_CARD, radius=dp(12))
        paint_border(card, COL_CARD_EDGE, radius=dp(12))
        for text, action in items:
            row = CozyButton(text, bg=COL_CARD, fg=COL_TEXT, bold=False,
                             radius=0, font_size=sp(15), height=self.ITEM_H,
                             pressed=COL_PRESSED, halign="left",
                             valign="middle", padding=[dp(18), 0])
            row.bind(size=lambda w, v: setattr(w, "text_size", v))
            row.bind(on_release=lambda _w, a=action: self._run(a))
            card.add_widget(row)
        self.add_widget(card)

    def _run(self, action: Callable) -> None:
        self.dismiss()
        Clock.schedule_once(lambda _dt: action(), 0.05)

    def open_below(self, anchor) -> None:
        """Anchor the card under `anchor`, kept inside the window."""
        self._anchor_xy = anchor.to_window(anchor.right, anchor.y)
        self.open()
        # open() assigns `center` BEFORE it binds _align_center, so the
        # override has to be applied once by hand; it then holds on resize.
        self._align_center()

    def _align_center(self, *_args):
        """ModalView re-centres itself on every open and window resize;
        a dropdown must stay pinned under its button instead."""
        anchor = getattr(self, "_anchor_xy", None)
        if anchor is None:
            return super()._align_center(*_args)
        ax, ay = anchor
        self.x = max(dp(8), min(ax - self.width,
                                Window.width - self.width - dp(8)))
        self.top = max(self.height + dp(8), ay - dp(2))


class ContactPickRow(ButtonBehavior, BoxLayout):
    """A contact with a tick that toggles (forward-to picker)."""

    def __init__(self, contact: dict, **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(56))
        kw.setdefault("padding", [dp(4), dp(6), dp(8), dp(6)])
        kw.setdefault("spacing", dp(12))
        super().__init__(**kw)
        self.address = contact["address"]
        self.picked = False
        name = contact.get("username") or short_addr(self.address, 14)
        self.add_widget(Avatar(self.address, name, diameter=dp(40)))
        texts = BoxLayout(orientation="vertical")
        texts.add_widget(single_line(name, sp(15), COL_TEXT, bold=True))
        texts.add_widget(single_line(short_addr(self.address, 18), sp(12),
                                     COL_TEXT_DIM))
        self.add_widget(texts)
        self.tick = Widget(size_hint_x=None, width=dp(28))
        self.tick.bind(pos=self._paint, size=self._paint)
        self.add_widget(self.tick)
        self.bind(on_release=lambda _w: self.toggle())

    def toggle(self) -> None:
        self.picked = not self.picked
        self._paint()

    def _paint(self, *_a) -> None:
        t = self.tick
        t.canvas.clear()
        with t.canvas:
            Color(*C(COL_ACCENT if self.picked else COL_CARD_EDGE))
            Ellipse(pos=(t.center_x - dp(11), t.center_y - dp(11)),
                    size=(dp(22), dp(22)))
            if self.picked:
                draw_icon("check", t.center_x, t.center_y, dp(13),
                          C("#FFFFFF"), width=dp(1.8))
            else:
                Color(*C(COL_CARD))
                Ellipse(pos=(t.center_x - dp(9.5), t.center_y - dp(9.5)),
                        size=(dp(19), dp(19)))


class ForwardModal(CozyModal):
    """Pick one or more contacts of THIS identity to forward to."""

    def __init__(self, app: "FextApp", local_ids: list,
                 on_done: Optional[Callable] = None) -> None:
        n = len(local_ids)
        super().__init__(f"Forward {n} message{'s' if n != 1 else ''} to…")
        self.app, self.local_ids, self.on_done = app, local_ids, on_done
        contacts = app.store.contacts() if app.store else []
        scroll = ScrollView(size_hint_y=None, bar_width=dp(3),
                            height=min(dp(56) * max(1, len(contacts)) + dp(4),
                                       Window.height * 0.5))
        box = BoxLayout(orientation="vertical", size_hint_y=None)
        box.bind(minimum_height=box.setter("height"))
        self.rows = [ContactPickRow(c) for c in contacts]
        for row in self.rows:
            box.add_widget(row)
        if not self.rows:
            box.add_widget(InfoLabel("No contacts yet.", color=COL_TEXT_DIM))
        scroll.add_widget(box)
        self.add(scroll)
        buttons = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(10))
        buttons.add_widget(CozyButton("Cancel", bg=COL_BG_LIST,
                                      fg=COL_TEXT_DIM, outline=COL_CARD_EDGE,
                                      on_release=lambda _w: self.dismiss()))
        buttons.add_widget(CozyButton("Forward",
                                      on_release=lambda _w: self._go()))
        self.add(buttons)

    def _go(self) -> None:
        targets = [r.address for r in self.rows if r.picked]
        if not targets:
            return
        self.dismiss()
        sent = self.app.forward_messages(self.local_ids, targets)
        if self.on_done:
            self.on_done()
        who = (len(targets) == 1 and (self.app.store.get_contact(targets[0])
               or {}).get("username")) or f"{len(targets)} chats"
        self.app._set_status(f"Forwarded {sent} message"
                             f"{'s' if sent != 1 else ''} to {who}", COL_OK)


class DeleteModal(CozyModal):
    """Delete for me, or — only when every message is yours — for both."""

    def __init__(self, app: "FextApp", msgs: list,
                 on_done: Optional[Callable] = None) -> None:
        n = len(msgs)
        super().__init__(f"Delete {n} message{'s' if n != 1 else ''}?")
        # NOT self.ids: every Kivy widget already has an `ids` property (a
        # dict of kv-language children), and assigning a list to it raises.
        self.app, self.msg_ids, self.on_done = app, [m["id"] for m in msgs], \
            on_done
        own = all(m["direction"] == "out" and m.get("client_msg_id")
                  for m in msgs)
        peer = msgs[0]["peer"] if msgs else None
        contact = (app.store.get_contact(peer) or {}) if peer else {}
        name = contact.get("username") or "them"
        if own:
            self.add(CozyButton(f"Delete for me and {name}", bg=COL_DANGER,
                                on_release=lambda _w: self._go(True)))
            if not contact.get("caps", 0) & LocalStore.CAP_V9:
                self.add(InfoLabel(
                    f"If {name} is still on an older version of the app, "
                    f"their copy stays and they see a note instead.",
                    color=COL_TEXT_DIM, font_size=sp(12.5)))
        self.add(CozyButton("Delete for me", bg=COL_BG_LIST, fg=COL_DANGER,
                            outline=COL_DANGER,
                            on_release=lambda _w: self._go(False)))
        if not own:
            self.add(InfoLabel("Only your own messages can be deleted for "
                               "both sides.", color=COL_TEXT_DIM,
                               font_size=sp(12.5)))
        self.add(CozyButton("Cancel", bg=COL_BG_LIST, fg=COL_TEXT_DIM,
                            outline=COL_CARD_EDGE,
                            on_release=lambda _w: self.dismiss()))

    def _go(self, for_everyone: bool) -> None:
        self.dismiss()
        if self.on_done:
            self.on_done()
        self.app.delete_messages(self.msg_ids, for_everyone)


class StarredModal(CozyModal):
    """Starred messages of one chat — private bookmarks, never sent."""

    def __init__(self, app: "FextApp", peer: Optional[str]) -> None:
        super().__init__("Starred messages")
        self.app = app
        rows = app.store.starred(peer) if app.store else []
        scroll = ScrollView(size_hint_y=None, bar_width=dp(3),
                            height=min(max(dp(70), dp(66) * len(rows)),
                                       Window.height * 0.6))
        box = BoxLayout(orientation="vertical", size_hint_y=None,
                        spacing=dp(6))
        box.bind(minimum_height=box.setter("height"))
        screen = app.conversation_screen
        for msg in rows:
            item = TappableBox(size_hint_y=None, height=dp(60),
                               padding=[dp(12), dp(6)],
                               on_tap=lambda m=msg: self._open(m))
            paint_round(item, COL_CARD, radius=dp(12))
            who = screen.author_name(screen._author_of(msg))
            day = pretty_day(msg["created_at"])
            clock = pretty_time(msg["created_at"])
            # pretty_day already IS the time for today's messages.
            when = clock if day == clock else f"{day}, {clock}"
            item.add_widget(single_line(f"{who}  ·  {when}", sp(12),
                                        COL_ACCENT_DARK, bold=True))
            item.add_widget(single_line(msg["body"], sp(14), COL_TEXT))
            box.add_widget(item)
        if not rows:
            box.add_widget(InfoLabel(
                "Nothing starred yet. Long-press a message and tap the star "
                "to keep it here.", color=COL_TEXT_DIM, font_size=sp(13)))
        scroll.add_widget(box)
        self.add(scroll)
        self.add(CozyButton("Done", on_release=lambda _w: self.dismiss()))

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


class ImageChooserModal(CozyModal):
    """Desktop photo picker."""

    def __init__(self, on_pick: Callable[[str], None]) -> None:
        super().__init__("Send a photo")
        self._on_pick = on_pick
        self.chooser = FileChooserListView(
            path=str(Path.home()),
            filters=["*.jpg", "*.jpeg", "*.png", "*.webp", "*.JPG",
                     "*.JPEG", "*.PNG", "*.bmp", "*.gif"],
            size_hint_y=None, height=min(dp(360), Window.height * 0.55))
        self.add(self.chooser)
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(10))
        row.add_widget(CozyButton("Cancel", bg=COL_BG_LIST, fg=COL_TEXT_DIM,
                                  outline=COL_CARD_EDGE,
                                  on_release=lambda _w: self.dismiss()))
        row.add_widget(CozyButton("Choose", on_release=lambda _w:
                                  self._pick()))
        self.add(row)

    def _pick(self) -> None:
        if self.chooser.selection:
            path = self.chooser.selection[0]
            self.dismiss()
            self._on_pick(path)


class AttachPreviewModal(CozyModal):
    """Check the photo and add a caption before it is compressed and sent."""

    def __init__(self, app: "FextApp", path: str) -> None:
        super().__init__("Send photo")
        self.app, self.path = app, path
        self.add(KivyImage(source=path, allow_stretch=True, keep_ratio=True,
                           size_hint_y=None,
                           height=min(dp(300), Window.height * 0.4)))
        self.caption = self.add(CozyInput(hint="Add a caption…"))
        self.add(InfoLabel(
            "Sent compressed, with location and camera details removed.",
            color=COL_TEXT_DIM, font_size=sp(12)))
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(10))
        row.add_widget(CozyButton("Cancel", bg=COL_BG_LIST, fg=COL_TEXT_DIM,
                                  outline=COL_CARD_EDGE,
                                  on_release=lambda _w: self.dismiss()))
        row.add_widget(CozyButton("Send", on_release=lambda _w: self._send()))
        self.add(row)

    def _send(self) -> None:
        self.dismiss()
        self.app.send_image(self.path, sanitize_text(self.caption.text))


class RenameIdentityModal(CozyModal):
    """Rename an identity's on-device label (its relay username is fixed)."""

    def __init__(self, app: "FextApp", address: str) -> None:
        super().__init__("Rename identity")
        self.app, self.address = app, address
        ident = app.keys.find(address) or {}
        self.add(InfoLabel(
            "This label is only shown on this device, to tell your "
            "identities apart. Your username on the relay stays "
            f"@{ident.get('username') or '(unregistered)'}.",
            color=COL_TEXT_DIM, font_size=sp(12.5)))
        self.entry = self.add(CozyInput(hint="Label"))
        self.entry.text = ident.get("label", "")
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(10))
        row.add_widget(CozyButton("Cancel", bg=COL_BG_LIST, fg=COL_TEXT_DIM,
                                  outline=COL_CARD_EDGE,
                                  on_release=lambda _w: self.dismiss()))
        row.add_widget(CozyButton("Save", on_release=lambda _w: self._save()))
        self.add(row)

    def _save(self) -> None:
        label = sanitize_text(self.entry.text)[:40]
        if not label:
            return
        self.app.keys.rename_identity(self.address, label)
        self.dismiss()
        self.app.chats_screen.refresh_header()
        ProfileMenuModal(self.app).open()


class ContactSettingsModal(CozyModal):
    """Opened by tapping the chat title bar. Per-contact controls:
      * Ticks status  — 'Enable ticks status' / 'No ticks status'. The choice
        is synced to the server; if EITHER side chooses 'No ticks', the
        server relays nothing and both sides cap at one golden tick.
      * Auto mark all as read (default ON). When off, incoming messages stay
        unread (sender stuck at 2/3) until manually marked.
      * Plaintext chat export (.json / .txt) and import (.json)."""

    def __init__(self, app: "FextApp", peer: str) -> None:
        contact = app.store.get_contact(peer) or {}
        name = contact.get("username") or short_addr(peer)
        super().__init__(f"Chat with {name}")
        self.app = app
        self.peer = peer
        self.add(InfoLabel(peer, color=COL_TEXT_DIM, font_size=sp(11.5)))
        scroll = ScrollView(size_hint_y=None, height=min(dp(430),
                            Window.height * 0.62), bar_width=dp(3))
        body = BoxLayout(orientation="vertical", size_hint_y=None,
                         spacing=dp(10))
        body.bind(minimum_height=body.setter("height"))
        scroll.add_widget(body)
        self.add(scroll)

        # ---- ticks privacy ---------------------------------------------------
        card = SectionCard("Ticks status")
        self._ticks_enabled = bool(contact.get("ticks_enabled", 1))
        seg = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        self.seg_on = CozyButton("Ticks on", height=dp(42), font_size=sp(13.5),
                                 on_release=lambda _w:
                                 self._on_ticks_change(True))
        self.seg_off = CozyButton("No ticks", height=dp(42),
                                  font_size=sp(13.5),
                                  on_release=lambda _w:
                                  self._on_ticks_change(False))
        seg.add_widget(self.seg_on)
        seg.add_widget(self.seg_off)
        card.add_widget(seg)
        # Described in words, not glyphs: the check-mark codepoint has no
        # glyph in the bundled font and rendered as an empty box here too.
        card.add_widget(InfoLabel(
            "Ticks on: delivered and read receipts flow both ways when you "
            "both enable them, so messages can reach two and three ticks.\n"
            "No ticks: either side choosing this caps both chats at a "
            "single tick.", color=COL_TEXT_DIM, font_size=sp(12.5)))
        self.ticks_status = InfoLabel("", color=COL_TEXT_DIM,
                                      font_size=sp(12.5))
        card.add_widget(self.ticks_status)
        body.add_widget(card)

        # ---- auto-read -------------------------------------------------------
        card = SectionCard("Read behaviour")
        self._auto = bool(contact.get("auto_read", 1))
        self.auto_button = CozyButton("", height=dp(42), font_size=sp(13.5),
                                      on_release=lambda _w:
                                      self._on_auto_change())
        card.add_widget(self.auto_button)
        card.add_widget(InfoLabel(
            "When off, messages stay unread (their sender sees two ticks at "
            "most) until you press \u201cMark all as read\u201d or tap a "
            "message.", color=COL_TEXT_DIM, font_size=sp(12.5)))
        body.add_widget(card)

        # ---- import / export -------------------------------------------------
        card = SectionCard("Chat history (plaintext, stays on this device)")
        row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        row.add_widget(CozyButton("Export .json", height=dp(42),
                                  bg=COL_BG_LIST, fg=COL_ACCENT_DARK,
                                  outline=COL_ACCENT, font_size=sp(13),
                                  on_release=lambda _w: self._export("json")))
        row.add_widget(CozyButton("Export .txt", height=dp(42),
                                  bg=COL_BG_LIST, fg=COL_ACCENT_DARK,
                                  outline=COL_ACCENT, font_size=sp(13),
                                  on_release=lambda _w: self._export("txt")))
        row.add_widget(CozyButton("Import .json", height=dp(42),
                                  bg=COL_BG_LIST, fg=COL_TICK_GOLD,
                                  outline=COL_TICK_GOLD, font_size=sp(13),
                                  on_release=lambda _w: self._import()))
        card.add_widget(row)
        self.io_status = InfoLabel("", color=COL_TEXT_DIM, font_size=sp(12.5))
        card.add_widget(self.io_status)
        body.add_widget(card)

        self.add(CozyButton("Done", on_release=lambda _w: self.dismiss()))
        self._style_toggles()

    # ---- toggle styling ------------------------------------------------------
    def _style_toggles(self) -> None:
        on, off = self._ticks_enabled, not self._ticks_enabled
        self.seg_on._bg_hex = COL_ACCENT if on else COL_BG_LIST
        self.seg_on.color = C("#FFFFFF" if on else COL_TEXT_DIM)
        self.seg_off._bg_hex = COL_ACCENT if off else COL_BG_LIST
        self.seg_off.color = C("#FFFFFF" if off else COL_TEXT_DIM)
        for seg in (self.seg_on, self.seg_off):
            seg._on_state(seg, seg.state)
        self.auto_button.text = ("Auto mark all as read:  ON" if self._auto
                                 else "Auto mark all as read:  OFF")
        self.auto_button._bg_hex = COL_ACCENT if self._auto else COL_BG_LIST
        self.auto_button.color = C("#FFFFFF" if self._auto else COL_TEXT_DIM)
        self.auto_button._on_state(self.auto_button, self.auto_button.state)

    # ---- ticks ---------------------------------------------------------------
    def _on_ticks_change(self, enabled: bool) -> None:
        self._ticks_enabled = enabled
        self._style_toggles()
        self.app.store.set_contact_setting(self.peer, "ticks_enabled",
                                           int(enabled))
        self.ticks_status.text = "Syncing preference…"
        threading.Thread(target=self._ticks_worker, args=(enabled,),
                         daemon=True).start()
        self.app.refresh_chat(self.peer)

    def _ticks_worker(self, enabled: bool) -> None:
        try:
            self.app.api.set_ticks(self.peer, enabled)
            note = "Preference synced to server."
        except (requests.RequestException, ApiClient.ApiError):
            note = "Saved here; the server learns about it once you're online."
        run_on_ui(setattr, self.ticks_status, "text", note)

    # ---- auto-read -----------------------------------------------------------
    def _on_auto_change(self) -> None:
        self._auto = not self._auto
        self._style_toggles()
        self.app.store.set_contact_setting(self.peer, "auto_read",
                                           int(self._auto))
        if self._auto:
            self.app.mark_peer_read(self.peer)
        self.app.refresh_chat(self.peer)

    # ---- export / import -----------------------------------------------------
    def _export(self, fmt: str) -> None:
        contact = self.app.store.get_contact(self.peer) or {}
        name = contact.get("username") or short_addr(self.peer, 10)
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = EXPORT_DIR / f"fext_chat_{name}_{stamp}.{fmt}"
        messages = self.app.store.messages_for(self.peer)
        try:
            if fmt == "json":
                path.write_text(json.dumps({
                    "fext_chat_export": 2,
                    "identity": self.app.keys.address,
                    "peer": {"address": self.peer,
                             "username": contact.get("username")},
                    "exported_at": now_iso(),
                    "messages": [{"direction": m["direction"],
                                  "body": m["body"],
                                  "created_at": m["created_at"],
                                  "status": m["status"], "read": m["read"]}
                                 for m in messages],
                }, indent=2, ensure_ascii=False), "utf-8")
            else:
                me = self.app.keys.username or "Me"
                lines = [f"{APP_NAME} chat with {name} ({self.peer})",
                         f"Exported {now_iso()}", ""]
                for m in messages:
                    who = me if m["direction"] == "out" else name
                    lines.append(f"[{m['created_at']}] {who}: {m['body']}")
                path.write_text("\n".join(lines) + "\n", "utf-8")
            opened = open_folder_in_explorer(EXPORT_DIR)
            note = f"Exported {len(messages)} messages to\n{path}"
            if opened:
                note += "\n(opened the exports folder for you)"
            self.io_status.color = C(COL_TEXT_DIM)
            self.io_status.text = note
        except OSError as exc:
            self.io_status.color = C(COL_DANGER)
            self.io_status.text = f"Export failed: {exc}"

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
            self.io_status.color = C(COL_TEXT_DIM)
            self.io_status.text = (
                f"Imported {added} new messages "
                f"({len(data['messages']) - added} duplicates skipped)")
            self.app.refresh_chat(self.peer)
            self.app.refresh_sidebar()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.io_status.color = C(COL_DANGER)
            self.io_status.text = f"Import failed: {exc}"


class SettingsModal(CozyModal):
    """Identity settings: address/username info, key-folder access, a
    shareable public bundle (with QR), and tap-to-reveal private key export
    (hex + WIF — both import into FEXT Core)."""

    def __init__(self, app: "FextApp") -> None:
        super().__init__("Settings")
        self.app = app
        self._revealed = False
        scroll = ScrollView(size_hint_y=None,
                            height=min(dp(520), Window.height * 0.72),
                            bar_width=dp(3))
        body = BoxLayout(orientation="vertical", size_hint_y=None,
                         spacing=dp(10))
        body.bind(minimum_height=body.setter("height"))
        scroll.add_widget(body)
        self.add(scroll)
        if IS_ANDROID or os.environ.get("FEXT_SHOW_ANDROID_SETTINGS"):
            self._build_delivery(body)
        self._build_identity(body)
        self._build_public(body)
        self._build_private(body)
        body.add_widget(InfoLabel(f"{APP_NAME} {APP_VERSION}",
                                  color=COL_TEXT_DIM, font_size=sp(11.5),
                                  halign="center"))
        self.add(CozyButton("Done", on_release=lambda _w: self.dismiss()))

    # ---- notifications (Android) ---------------------------------------------
    def _build_delivery(self, body) -> None:
        card = SectionCard("Notifications")
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(10))
        row.add_widget(InfoLabel("Receive messages when the app is closed",
                                 font_size=sp(14)))
        row.add_widget(ToggleSwitch(background_delivery_enabled(),
                                    self._on_delivery,
                                    pos_hint={"center_y": .5}))
        card.add_widget(row)
        card.add_widget(InfoLabel(
            "Android closes apps in the background. With this on, a small "
            "service keeps listening so notifications still arrive; Android "
            "shows it as a running app. Turn it off to save battery — you "
            "will then only be notified while Ferritext is open.",
            color=COL_TEXT_DIM, font_size=sp(12.5)))
        body.add_widget(card)

    def _on_delivery(self, on: bool) -> None:
        save_pref("background_delivery", bool(on))
        self.app._sync_background_service()

    def _qr_image(self, data: str, side=dp(190)) -> KivyImage:
        img = KivyImage(size_hint=(None, None), size=(side, side),
                        allow_stretch=True)
        img.texture = qr_texture(data)
        return img

    # ---- identity ------------------------------------------------------------
    def _build_identity(self, body) -> None:
        keys = self.app.keys
        card = SectionCard("Identity")
        card.add_widget(InfoLabel(f"Label:  {keys.label}"))
        card.add_widget(InfoLabel(
            f"Username:  {keys.username or '(not registered)'}"))
        card.add_widget(InfoLabel(f"Address:  {keys.address}",
                                  color=COL_TICK_GOLD, font_size=sp(12.5)))
        card.add_widget(InfoLabel(
            "The address is the HASH160 of your public key in Base58Check "
            "(version 0x24) — the same P2PKH construction Bitcoin uses. Its "
            "\u201cFec\u201d prefix is this network's anti-spam "
            "proof-of-work.", color=COL_TEXT_DIM, font_size=sp(12.5)))
        # A QR of the raw address on its own (distinct from the full public
        # bundle QR further down): scan it to grab just the address — e.g. to
        # add this identity as a contact or receive coins — without pulling
        # in the username/public-key payload.
        holder = AnchorLayout(size_hint_y=None, height=dp(200))
        holder.add_widget(self._qr_image(keys.address, side=dp(170)))
        card.add_widget(holder)
        card.add_widget(InfoLabel("Scan for address only", halign="center",
                                  color=COL_TEXT_DIM, font_size=sp(11.5)))
        row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        row.add_widget(CozyButton("Copy address", height=dp(42),
                                  bg=COL_BG_LIST, fg=COL_ACCENT_DARK,
                                  outline=COL_ACCENT, font_size=sp(13),
                                  on_release=lambda _w:
                                  Clipboard.copy(keys.address)))
        if not IS_MOBILE:
            row.add_widget(CozyButton("Open key folder", height=dp(42),
                                      bg=COL_BG_LIST, fg=COL_TICK_GOLD,
                                      outline=COL_TICK_GOLD,
                                      font_size=sp(13),
                                      on_release=lambda _w:
                                      open_folder_in_explorer(DATA_DIR)))
        card.add_widget(row)
        card.add_widget(InfoLabel(
            f"Keys and chat databases live in {DATA_DIR}",
            color=COL_TEXT_DIM, font_size=sp(11.5)))
        body.add_widget(card)

    # ---- public bundle -------------------------------------------------------
    def _build_public(self, body) -> None:
        card = SectionCard("Public bundle (safe to share)")
        bundle_pretty = json.dumps(self.app.keys.public_bundle(), indent=2)
        card.add_widget(InfoLabel(bundle_pretty, font_size=sp(11.5),
                                  color=COL_TEXT))
        card.add_widget(CozyButton("Copy bundle", height=dp(42),
                                   bg=COL_BG_LIST, fg=COL_ACCENT_DARK,
                                   outline=COL_ACCENT, font_size=sp(13),
                                   on_release=lambda _w:
                                   Clipboard.copy(bundle_pretty)))
        holder = AnchorLayout(size_hint_y=None, height=dp(210))
        holder.add_widget(self._qr_image(json.dumps(
            self.app.keys.public_bundle(), separators=(",", ":"))))
        card.add_widget(holder)
        body.add_widget(card)

    # ---- private key ---------------------------------------------------------
    def _build_private(self, body) -> None:
        card = SectionCard("Private key (NEVER share this)")
        card.add_widget(InfoLabel(
            "Your secp256k1 private key, shown as raw hex and as WIF. "
            "Import either format into FEXT Core to spend the coins "
            "attached to this address.", color=COL_TEXT_DIM,
            font_size=sp(12.5)))
        self.private_label = InfoLabel("\u2022\u2022\u2022\u2022\u2022\u2022"
                                       "  hidden  \u2022\u2022\u2022\u2022"
                                       "\u2022\u2022", color=COL_DANGER,
                                       font_size=sp(12))
        card.add_widget(self.private_label)
        row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        self.reveal_button = CozyButton("Reveal", height=dp(42),
                                        bg=COL_BG_LIST, fg=COL_DANGER,
                                        outline=COL_DANGER, font_size=sp(13),
                                        on_release=lambda _w:
                                        self._toggle_private())
        row.add_widget(self.reveal_button)
        row.add_widget(CozyButton("Copy WIF", height=dp(42), bg=COL_BG_LIST,
                                  fg=COL_DANGER, outline=COL_DANGER,
                                  font_size=sp(13),
                                  on_release=lambda _w: self._copy_wif()))
        card.add_widget(row)
        body.add_widget(card)

    def _toggle_private(self) -> None:
        self._revealed = not self._revealed
        self.private_label.text = (
            self.app.keys.private_export() if self._revealed
            else "\u2022\u2022\u2022\u2022\u2022\u2022  hidden  "
                 "\u2022\u2022\u2022\u2022\u2022\u2022")
        self.reveal_button.text = "Hide" if self._revealed else "Reveal"

    def _copy_wif(self) -> None:
        data = json.loads(self.app.keys.private_export())
        Clipboard.copy(data["private_key_wif"])


class RemoveIdentityModal(CozyModal):
    """Confirm removal of an identity from this device.

    This is irreversible in the way that matters: the private key IS the
    identity, so once it is gone the address, its message history and any
    coins attached to it are unrecoverable. The dialog therefore puts the
    key export in front of the user BEFORE the destructive button, rather
    than warning about it afterwards.

    Releasing the name on the relay is offered separately, because the two
    are genuinely different decisions: you may want the identity off a
    shared device while keeping the username reserved for another device
    that holds the same key.
    """

    def __init__(self, app: "FextApp", address: str) -> None:
        ident = app.keys.find(address) or {}
        label = ident.get("label") or short_addr(address, 12)
        super().__init__(f"Remove “{label}”?")
        self.app = app
        self.address = address
        self.username = ident.get("username")
        self._release = bool(self.username)

        self.add(InfoLabel(
            "Removing this identity deletes its private key and its chat "
            "history from this device. The key cannot be recovered "
            "afterwards — if you might want this identity back, copy the "
            "key first.", color=COL_TEXT_DIM, font_size=sp(13)))
        self.add(InfoLabel(address, color=COL_TICK_GOLD, font_size=sp(11.5)))

        self.add(CozyButton("Copy private key (WIF) first", height=dp(42),
                            bg=COL_BG_LIST, fg=COL_DANGER,
                            outline=COL_DANGER, font_size=sp(13),
                            on_release=lambda _w: self._copy_key()))

        if self.username:
            self.release_button = self.add(CozyButton(
                "", height=dp(42), font_size=sp(13),
                on_release=lambda _w: self._toggle_release()))
            self.add(InfoLabel(
                f"Releasing “{self.username}” removes it from the relay's "
                f"directory so nobody can look you up, and frees the name "
                f"for reuse. Leave it on if another device still uses this "
                f"same key.", color=COL_TEXT_DIM, font_size=sp(12)))
            self._style_release()

        self.status = self.status_label()
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(10))
        row.add_widget(CozyButton("Cancel", bg=COL_BG_LIST, fg=COL_TEXT_DIM,
                                  outline=COL_CARD_EDGE,
                                  on_release=lambda _w: self.dismiss()))
        self.confirm = CozyButton("Remove identity", bg=COL_DANGER,
                                  pressed=COL_DANGER,
                                  on_release=lambda _w: self._confirm())
        row.add_widget(self.confirm)
        self.add(row)

    def _copy_key(self) -> None:
        """Export the key of the identity being removed.

        private_export() reads the ACTIVE identity, so this switches, reads,
        and switches back — in a finally, because leaving the app pointed at
        a different identity after an error would be far worse than the
        failed copy itself.
        """
        active = self.app.keys.address
        try:
            self.app.keys.activate(self.address)
            data = json.loads(self.app.keys.private_export())
            Clipboard.copy(data["private_key_wif"])
            self.set_status(self.status, "Private key copied to the "
                            "clipboard — paste it somewhere safe now.",
                            COL_TICK_GOLD)
        except Exception as exc:
            self.set_status(self.status, f"Could not export the key: {exc}",
                            COL_DANGER)
        finally:
            try:
                self.app.keys.activate(active)
            except Exception:
                pass

    def _toggle_release(self) -> None:
        self._release = not self._release
        self._style_release()

    def _style_release(self) -> None:
        self.release_button.text = (
            f"Release “{self.username}” on the relay:  "
            f"{'YES' if self._release else 'NO'}")
        self.release_button._bg_hex = (COL_ACCENT if self._release
                                       else COL_BG_LIST)
        self.release_button.color = C("#FFFFFF" if self._release
                                      else COL_TEXT_DIM)
        self.release_button._on_state(self.release_button,
                                      self.release_button.state)

    def _confirm(self) -> None:
        self.confirm.disabled = True
        self.set_status(self.status, "Removing…")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        note = ""
        if self._release and self.username:
            # Deregistration must be signed by the identity being removed,
            # so switch to it for the call and restore in a finally.
            previous = self.app.keys.address
            try:
                self.app.keys.activate(self.address)
                ApiClient(self.app.keys).deregister()
            except (requests.RequestException, ApiClient.ApiError):
                # Removal is still correct locally; the name simply stays
                # claimed on the relay until the identity is deleted there.
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


class ProfileMenuModal(CozyModal):
    """Tap your avatar in the chat list: identity switcher + settings —
    the whole multi-identity story, one warm sheet."""

    def __init__(self, app: "FextApp") -> None:
        super().__init__("You")
        self.app = app
        keys = app.keys
        head = BoxLayout(size_hint_y=None, height=dp(58), spacing=dp(12))
        head.add_widget(Avatar(keys.address, keys.username or keys.label,
                               diameter=dp(50)))
        info = BoxLayout(orientation="vertical")
        info.add_widget(InfoLabel(f"@{keys.username or '(unregistered)'}",
                                  bold=True, font_size=sp(15)))
        info.add_widget(InfoLabel(short_addr(keys.address, 24),
                                  color=COL_TEXT_DIM, font_size=sp(11.5)))
        head.add_widget(info)
        self.add(head)

        card = SectionCard("Identities on this device")
        for ident in keys.identities:
            active = ident["address"] == keys.address
            line = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(4))
            row = TappableBox(orientation="horizontal", spacing=dp(10),
                              padding=[dp(8), dp(6), dp(4), dp(6)],
                              on_tap=lambda a=ident["address"]:
                              self._switch(a))
            col, _r = paint_round(row, COL_BUBBLE_ME if active else COL_CARD,
                                  radius=dp(12))
            row.add_widget(Avatar(ident["address"],
                                  ident.get("username") or
                                  ident.get("label") or "?",
                                  diameter=dp(34)))
            texts = BoxLayout(orientation="vertical")
            texts.add_widget(single_line(ident.get("label") or "?", sp(14.5),
                                         COL_TEXT, bold=active))
            texts.add_widget(single_line(
                f"@{ident.get('username') or 'unregistered'}  ·  "
                f"{short_addr(ident['address'], 10)}", sp(11.5),
                COL_TEXT_DIM))
            row.add_widget(texts)
            line.add_widget(row)
            # Rename (pen) and delete (bin): icons, as in every contacts app,
            # instead of a word button that ate a third of the row.
            line.add_widget(IconButton(
                "edit", tint=COL_TEXT_DIM, press=COL_PRESSED,
                size=(dp(40), dp(40)), pos_hint={"center_y": .5},
                on_release=lambda _w, a=ident["address"]: self._rename(a)))
            line.add_widget(IconButton(
                "delete", tint=COL_DANGER, press=COL_PRESSED,
                size=(dp(40), dp(40)), pos_hint={"center_y": .5},
                on_release=lambda _w, a=ident["address"]: self._remove(a)))
            card.add_widget(line)
        card.add_widget(CozyButton("New identity…", height=dp(42),
                                   bg=COL_BG_LIST, fg=COL_ACCENT_DARK,
                                   outline=COL_ACCENT, font_size=sp(13.5),
                                   on_release=lambda _w: self._new()))
        self.add(card)
        self.add(CozyButton("Settings",
                            on_release=lambda _w: self._settings()))

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
        SettingsModal(self.app).open()



# =============================================================================
#  Chat list — the "home" screen
# =============================================================================
# Ticks are DRAWN (see TickMarks), so no check-mark codepoint is needed.
# U+2022 BULLET is used rather than U+25CF BLACK CIRCLE because only the
# former has a glyph in Kivy's bundled Roboto — the latter renders as an
# empty box, which is what the tick marks used to do.
BULLET = "\u2022"


class TappableBox(ButtonBehavior, BoxLayout):
    """A tappable stack of widgets (chat title -> contact settings, etc.)."""

    def __init__(self, on_tap=None, **kw):
        kw.setdefault("orientation", "vertical")
        super().__init__(**kw)
        if on_tap is not None:
            self.bind(on_release=lambda _w: on_tap())


class UnreadBadge(Label):
    """Gold pill with the unread count; widens for two digits."""

    def __init__(self, count: int, muted: bool = False, **kw):
        text = str(count) if count < 1000 else "999+"
        super().__init__(text=text, bold=True, font_size=sp(11.5),
                         color=C("#FFFFFF"), size_hint=(None, None),
                         height=dp(22), **kw)
        with self.canvas.before:
            Color(*C(COL_TICK_GREY if muted else COL_ACCENT))
            self._pill = RoundedRectangle(radius=[dp(11)])
        self.bind(pos=self._sync, size=self._sync,
                  texture_size=lambda w, v: setattr(
                      w, "width", max(dp(22), v[0] + dp(12))))
        self.texture_update()

    def _sync(self, *_a):
        self._pill.pos, self._pill.size = self.pos, self.size


class ConversationRow(ButtonBehavior, BoxLayout):
    """One chat in the list: avatar, name over a one-line
    preview, time and unread badge on the right, a hairline divider that
    starts where the text does."""

    def __init__(self, conv: dict, on_open: Callable[[str], None], **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(72))
        kw.setdefault("padding", [dp(12), dp(8), dp(14), dp(8)])
        kw.setdefault("spacing", dp(12))
        super().__init__(**kw)
        peer = conv["peer"]
        name = conv.get("username") or short_addr(peer, 14)
        unread = int(conv.get("unread") or 0)
        muted = not conv.get("notify", 1)
        preview = conv.get("preview") or "Say hello — this chat is end-to-end " \
            "encrypted"
        with self.canvas.before:
            self._bg = Color(*C(COL_BG_LIST))
            self._rect = Rectangle()
            Color(*C(COL_DIVIDER))
            self._rule = Rectangle()
        self.bind(pos=self._sync, size=self._sync,
                  state=lambda _w, st: setattr(
                      self._bg, "rgba", C(COL_PRESSED if st == "down"
                                          else COL_BG_LIST)))
        self.bind(on_release=lambda _w: on_open(peer))

        self.add_widget(Avatar(peer, name, diameter=dp(52)))
        middle = BoxLayout(orientation="vertical", padding=[0, dp(4)])
        top = BoxLayout(spacing=dp(6))
        top.add_widget(single_line(name, sp(16), COL_TEXT, bold=True))
        if muted:
            bell = Widget(size_hint_x=None, width=dp(16))
            bell.bind(pos=lambda w, *_a: self._bell(w),
                      size=lambda w, *_a: self._bell(w))
            top.add_widget(bell)
        when = Label(text=pretty_day(conv.get("last_at") or ""),
                     font_size=sp(12),
                     color=C(COL_ACCENT if unread else COL_TEXT_DIM),
                     size_hint_x=None, halign="right", valign="middle")
        when.bind(texture_size=lambda w, v: setattr(w, "width", v[0]),
                  size=lambda w, v: setattr(w, "text_size", (None, v[1])))
        when.texture_update()
        top.add_widget(when)
        middle.add_widget(top)
        bottom = BoxLayout(spacing=dp(6))
        bottom.add_widget(single_line(preview, sp(14), COL_TEXT_DIM))
        if unread:
            holder = AnchorLayout(size_hint_x=None, width=dp(36),
                                  anchor_x="right")
            holder.add_widget(UnreadBadge(min(unread, 9999), muted=muted))
            bottom.add_widget(holder)
        middle.add_widget(bottom)
        self.add_widget(middle)

    def _sync(self, *_a):
        self._rect.pos, self._rect.size = self.pos, self.size
        inset = self.padding[0] + dp(52) + self.spacing
        self._rule.pos = (self.x + inset, self.y)
        self._rule.size = (self.width - inset, dp(1))

    @staticmethod
    def _bell(widget):
        widget.canvas.clear()
        with widget.canvas:
            draw_icon("bell_off", widget.center_x, widget.center_y, dp(13),
                      C(COL_TICK_GREY))


class ChatsScreen(Screen):
    """The home screen: your identity in the app bar, conversations below."""

    def __init__(self, app: "FextApp", **kw):
        super().__init__(name="chats", **kw)
        self.app = app
        root = BoxLayout(orientation="vertical")
        paint_round(root, COL_BG_LIST, radius=dp(0))
        self.add_widget(root)

        # ---- app bar ---------------------------------------------------------
        bar = BoxLayout(size_hint_y=None, height=dp(56),
                        padding=[dp(10), 0, dp(6), 0], spacing=dp(10))
        paint_round(bar, COL_APPBAR, radius=dp(0))
        self.avatar_holder = AnchorLayout(size_hint_x=None, width=dp(40))
        bar.add_widget(self.avatar_holder)

        titles = BoxLayout(orientation="vertical")
        brand = single_line(APP_NAME, sp(19), COL_APPBAR_TEXT, bold=True,
                            size_hint_y=None, height=dp(25))
        brand.valign = "bottom"
        self.me_label = single_line("", sp(11.5), COL_APPBAR_DIM,
                                    size_hint_y=None, height=dp(18))
        self.me_label.valign = "top"
        titles.add_widget(brand)
        titles.add_widget(self.me_label)
        bar.add_widget(titles)
        bar.add_widget(IconButton("plus", bg=COL_ACCENT, tint="#FFFFFF",
                                  size=(dp(40), dp(40)),
                                  pos_hint={"center_y": .5},
                                  on_release=lambda _w:
                                  self.app.open_add_contact()))
        root.add_widget(bar)

        # ---- status ribbon ---------------------------------------------------
        # Tapping the ribbon forces an immediate reconnect. Without this the
        # only cure for a stalled sync was to wait out the backoff or
        # restart the app — which is what "sometimes messages don't arrive"
        # felt like from the outside.
        ribbon = TappableBox(on_tap=lambda: self.app.retry_now(),
                             orientation="horizontal", size_hint_y=None,
                             height=dp(28), padding=[dp(14), 0, dp(14), 0])
        paint_round(ribbon, COL_BG, radius=dp(0))
        self.status_label = Label(text="Starting…", font_size=sp(11.5),
                                  color=C(COL_TEXT_DIM), halign="left",
                                  valign="middle")
        self.status_label.bind(size=lambda w, v: setattr(w, "text_size", v))
        ribbon.add_widget(self.status_label)
        self.retry_hint = Label(text="tap to retry", font_size=sp(10.5),
                                color=C(COL_TEXT_DIM), halign="right",
                                valign="middle", size_hint_x=None,
                                width=dp(72), opacity=0)
        self.retry_hint.bind(size=lambda w, v: setattr(w, "text_size", v))
        ribbon.add_widget(self.retry_hint)
        root.add_widget(ribbon)

        # ---- conversation list ----------------------------------------------
        self.scroll = ScrollView(bar_width=dp(3))
        self.list = BoxLayout(orientation="vertical", size_hint_y=None,
                              padding=[0, 0, 0, dp(16)], spacing=0)
        self.list.bind(minimum_height=self.list.setter("height"))
        self.scroll.add_widget(self.list)
        root.add_widget(self.scroll)

    def refresh_header(self) -> None:
        keys = self.app.keys
        self.avatar_holder.clear_widgets()
        if not keys.has_identity:
            return
        avatar = AvatarButton(keys.address, keys.username or keys.label,
                              diameter=dp(38))
        avatar.bind(on_release=lambda _w: ProfileMenuModal(self.app).open())
        self.avatar_holder.add_widget(avatar)
        self.me_label.text = (f"@{keys.username or 'unregistered'}  ·  "
                              f"{short_addr(keys.address, 16)}")

    def set_status(self, text: str, color: str) -> None:
        self.status_label.text = text
        self.status_label.color = C(color)
        # Offer the retry affordance only when it would actually do
        # something — a permanent "tap to retry" next to "Connected" is
        # noise that trains people to ignore the ribbon.
        self.retry_hint.opacity = 0 if color == COL_OK else 1

    def refresh_list(self, conversations: list) -> None:
        self.list.clear_widgets()
        if not conversations:
            self.list.add_widget(self._empty_state())
            return
        for conv in conversations:
            self.list.add_widget(ConversationRow(conv,
                                                 self.app.open_conversation))

    def _empty_state(self) -> BoxLayout:
        box = BoxLayout(orientation="vertical", size_hint_y=None,
                        height=dp(230), padding=[dp(24), dp(44), dp(24), 0],
                        spacing=dp(12))
        box.add_widget(InfoLabel("No conversations yet", bold=True,
                                 font_size=sp(17), halign="center"))
        box.add_widget(InfoLabel(
            "Tap + to add someone by username or Fec… address. Every message "
            "you send is encrypted on this device and can only be read by "
            "the person you send it to.",
            color=COL_TEXT_DIM, font_size=sp(13), halign="center"))
        box.add_widget(CozyButton("Add your first contact",
                                  size_hint_x=None, width=dp(230),
                                  pos_hint={"center_x": 0.5},
                                  on_release=lambda _w:
                                  self.app.open_add_contact()))
        return box


# =============================================================================
#  Conversation screen
# =============================================================================
_METRIC_LABEL = None


def measure_text(text: str, font_size: float, wrap_width: float) -> tuple:
    """Pixel (width, height) of `text` wrapped at `wrap_width`.

    One shared, never-displayed Label measures for every bubble. The widget
    that now DISPLAYS message text is a TextInput (so it can be selected and
    copied), and a TextInput cannot report the size it wants until the frame
    after it is given one — useless for laying a bubble out. Measuring with
    the same Label the bubbles used in v8.0.1 keeps the geometry pixel-for-
    pixel identical to the previous release, so making text selectable
    changes nothing about how a conversation looks.
    """
    global _METRIC_LABEL
    if _METRIC_LABEL is None:
        _METRIC_LABEL = Label(halign="left", valign="top",
                              size_hint=(None, None))
    metric = _METRIC_LABEL
    metric.font_size = font_size
    metric.text = text
    # First the NATURAL size, unwrapped: a Label given a wrap width renders
    # its texture at that full width, so measuring wrapped made a two-letter
    # "ok" as wide as a paragraph — every bubble used to span 78% of the
    # screen. Text that fits on its lines keeps its natural width; only text
    # that genuinely needs wrapping is measured at the wrap width. (The
    # length check skips the unwrapped pass for text that cannot possibly
    # fit, which would otherwise build a texture wider than the GPU allows.)
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
    """Message text you can highlight and copy — on a phone and on a PC.

    A Kivy Label cannot be selected at all, so until now the only way to get
    a message back out of the app was to retype it. A READ-ONLY TextInput
    gives the behaviour people already expect from every other app, for
    free: drag to select plus Ctrl+A/Ctrl+C on desktop, and on Android a
    long press raises the selection handles and a context bubble which —
    because the field is readonly — offers exactly one action, Copy. Kivy
    enables the handles and the bubble by itself off `_is_desktop`, so
    neither needs configuring here.

    Scrolling still works over it: a ScrollView holds each touch for
    scroll_timeout (55ms) and only hands it to the child if the finger has
    not travelled scroll_distance, so a flick scrolls and a press selects.

    It is painted to look precisely like the Label it replaces — no
    background, no border, no cursor, the same espresso text at the same
    size.
    """

    def __init__(self, on_tap: Optional[Callable] = None, **kw):
        kw.setdefault("readonly", True)
        kw.setdefault("multiline", True)
        kw.setdefault("background_color", (0, 0, 0, 0))
        kw.setdefault("background_normal", "")
        kw.setdefault("background_active", "")
        kw.setdefault("background_disabled_normal", "")
        kw.setdefault("foreground_color", C(COL_TEXT))
        kw.setdefault("selection_color", C(COL_ACCENT)[:3] + [0.35])
        kw.setdefault("cursor_color", (0, 0, 0, 0))
        kw.setdefault("cursor_width", 0)
        kw.setdefault("padding", [0, 0, 0, 0])
        kw.setdefault("size_hint", (None, None))
        super().__init__(**kw)
        self._on_tap = on_tap

    # ---- tap-to-mark-read ----------------------------------------------------
    # The bubble is a ButtonBehavior, but Kivy offers every touch to the
    # children first, so the TextInput now swallows it and the bubble's
    # on_release never fires. Tapping a message is exactly the gesture that
    # marks it read, so re-dispatch it from here — but only when the press
    # AND the release happened on this bubble, or merely scrolling past an
    # unread message would mark it read.
    def on_touch_down(self, touch):
        handled = super().on_touch_down(touch)
        if handled:
            touch.ud["fext_tap_origin"] = self
        return handled

    def on_touch_up(self, touch):
        handled = super().on_touch_up(touch)
        # .get, not .pop: every bubble under the touch sees this event, and
        # popping it in one would rob the bubble that actually started it.
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


def single_line(text: str, font_size, color, bold=False, **kw) -> Label:
    """A one-line label that truncates with an ellipsis instead of
    overflowing — the default for every name and preview in the app."""
    label = Label(text=text, font_size=font_size, color=C(color), bold=bold,
                  halign="left", valign="middle", shorten=True,
                  shorten_from="right", max_lines=1, **kw)
    label.bind(size=lambda w, v: setattr(w, "text_size", v))
    return label


class QuoteBlock(ButtonBehavior, BoxLayout):
    """The quoted message inside a reply: an accent bar, who said it, and
    one line of what they said. Tapping it scrolls to the original."""

    HEIGHT = dp(42)

    def __init__(self, author: str, snippet: str, outgoing: bool,
                 on_tap: Optional[Callable] = None, **kw):
        kw.setdefault("orientation", "vertical")
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("height", self.HEIGHT)
        kw.setdefault("padding", [dp(10), dp(4), dp(8), dp(4)])
        super().__init__(**kw)
        with self.canvas.before:
            Color(*C(COL_QUOTE_ME if outgoing else COL_QUOTE_PEER))
            self._bg = RoundedRectangle(radius=[dp(6)])
            Color(*C(COL_ACCENT))
            self._bar = RoundedRectangle(radius=[dp(2)])
        self.bind(pos=self._sync, size=self._sync)
        self.author = single_line(author, sp(12.5), COL_ACCENT_DARK, bold=True,
                                  size_hint_y=None, height=dp(17))
        self.snippet = single_line(snippet, sp(12.5), COL_TEXT_DIM,
                                   size_hint_y=None, height=dp(17))
        self.add_widget(self.author)
        self.add_widget(self.snippet)
        if on_tap is not None:
            self.bind(on_release=lambda _w: on_tap())

    def _sync(self, *_a):
        self._bg.pos, self._bg.size = self.pos, self.size
        self._bar.pos, self._bar.size = self.pos, (dp(3), self.height)

    def natural_width(self) -> float:
        """Width the quote would like (capped by the bubble later)."""
        self.author.texture_update()
        self.snippet.texture_update()
        return max(measure_text(self.author.text, sp(12.5), dp(600))[0],
                   measure_text(self.snippet.text, sp(12.5), dp(600))[0]
                   ) + dp(22)


class MediaStatus(Label):
    """A small caption chip drawn over a photo ('Downloading…', etc.)."""

    def __init__(self, **kw):
        super().__init__(font_size=sp(11.5), color=C("#FFFFFF"),
                         size_hint=(None, None), **kw)
        with self.canvas.before:
            self._col = Color(0, 0, 0, .45)
            self._bg = RoundedRectangle(radius=[dp(10)])
        self.bind(pos=self._sync, size=self._sync,
                  texture_size=lambda w, v: setattr(
                      w, "size", (v[0] + dp(16), dp(22))))

    def _sync(self, *_a):
        self._bg.pos, self._bg.size = self.pos, self.size
        self._col.a = .45 if self.text else 0


class ImageContent(ButtonBehavior, FloatLayout):
    """A photo in a bubble: the full image once it is here, the blurry
    inline thumbnail (plus a status chip) until then."""

    def __init__(self, msg: dict, media: dict, screen, **kw):
        kw.setdefault("size_hint", (None, None))
        super().__init__(**kw)
        self.msg, self.media, self.screen = msg, media, screen
        with self.canvas.before:
            Color(*C("#E9DFCF"))
            self._ground = RoundedRectangle(radius=[dp(10)])
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
        width = min(max_w, dp(250))
        height = max(dp(90), min(dp(330), width * h0 / float(w0)))
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
            "requested": "Asked them to send it again",
            "failed": ("Upload failed · tap to retry" if outgoing
                       else "Download failed · tap to retry"),
        }.get(state, "")


class Waveform(Widget):
    """Voice-note level bars; the played part is drawn in the accent."""

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
                Color(*C(COL_ACCENT if played else COL_TICK_GREY))
                Rectangle(pos=(self.x + i * dp(4),
                               self.center_y - height / 2),
                          size=(dp(2.2), height))


class VoiceContent(BoxLayout):
    """▶ + waveform + duration. Plays from the local copy once it is here."""

    def __init__(self, msg: dict, media: dict, screen, **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("height", dp(46))
        kw.setdefault("spacing", dp(8))
        super().__init__(**kw)
        self.msg, self.media, self.screen = msg, media, screen
        self.button = IconButton("play", tint="#FFFFFF", bg=COL_ACCENT,
                                 size=(dp(40), dp(40)),
                                 pos_hint={"center_y": .5},
                                 on_release=lambda _w:
                                 screen.on_media_tap(self.msg))
        self.add_widget(self.button)
        self.wave = Waveform(media.get("wave", ""), size_hint_y=None,
                             height=dp(26), pos_hint={"center_y": .5})
        self.add_widget(self.wave)
        dur = int(media.get("dur") or 0) // 1000
        self.length = Label(text=f"{dur // 60}:{dur % 60:02d}",
                            font_size=sp(11.5), color=C(COL_TEXT_DIM),
                            size_hint=(None, 1), width=dp(34))
        self.add_widget(self.length)
        self.refresh(msg)

    def natural_size(self, max_w: float) -> tuple:
        return min(max_w, dp(230)), dp(46)

    def refresh(self, msg: dict) -> None:
        self.msg = msg
        state = msg.get("media_state") or ""
        if state in ("expired",):
            self.button.set_icon("retry")
        elif state in ("pending", "downloading", "uploading", "requested"):
            self.button.set_icon("download")
        elif state == "failed":
            self.button.set_icon("retry")
        else:
            playing = self.screen.app.voice.path == msg.get("media_path") \
                and self.screen.app.voice.playing
            self.button.set_icon("pause" if playing else "play")


def make_body(text: str, font_size) -> Widget:
    """The message text widget. On a desktop it is selectable (drag to
    highlight, Ctrl+C); on a phone a plain label, because long-press there
    selects the whole message and a focusable field would only fight it."""
    if IS_MOBILE:
        return Label(text=text, color=C(COL_TEXT), font_size=font_size,
                     halign="left", valign="top", size_hint=(None, None))
    return SelectableText(text=text, font_size=font_size)


class MessageBubble(BoxLayout):
    """One message: an optional 'Forwarded' mark, reply quote, photo or
    voice note, the text, and a meta row (star, time, ticks).

    Outgoing = gold and right-aligned; incoming = white and left-aligned.
    Touches belong to the enclosing BubbleRow, which owns tap, long-press
    and selection, so this class only lays things out.
    """

    def __init__(self, msg: dict, ticks_enabled: bool, screen, **kw):
        kw.setdefault("orientation", "vertical")
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("padding", [dp(10), dp(7), dp(10), dp(6)])
        kw.setdefault("spacing", dp(4))
        super().__init__(**kw)
        self.msg, self.screen = msg, screen
        self._last_width = 0.0
        self._text_w = self._text_h = 0.0
        self._body_floor = 0.0
        outgoing = msg["direction"] == "out"
        ext = message_ext(msg)
        corners = ([dp(16), dp(16), dp(4), dp(16)] if outgoing
                   else [dp(16), dp(16), dp(16), dp(4)])
        paint_round(self, COL_BUBBLE_ME if outgoing else COL_BUBBLE_PEER,
                    corners=corners)
        if not outgoing:
            paint_border(self, COL_CARD_EDGE, radius=dp(16))

        self.fwd = None
        if ext.get("fwd"):
            self.fwd = Label(text="Forwarded", italic=True, font_size=sp(11.5),
                             color=C(COL_ACCENT_DARK), halign="left",
                             size_hint=(None, None), height=dp(15))
            self.fwd.bind(size=lambda w, v: setattr(w, "text_size", v))
            self.add_widget(self.fwd)

        self.quote = None
        reply = ext.get("reply")
        if isinstance(reply, dict):
            author = screen.author_name(reply.get("from"), outgoing)
            snippet = reply.get("snip") or {"image": "Photo",
                                            "voice": "Voice message"}.get(
                reply.get("kind"), "Message")
            self.quote = QuoteBlock(author, snippet, outgoing,
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
        self.body = make_body(text, sp(14.5)) if text else None
        if self.body is not None:
            self.body.bind(minimum_height=self._body_min_height) \
                if isinstance(self.body, SelectableText) else None
            self.add_widget(self.body)

        self.meta = Label(text=self._stamp_text(msg), markup=True,
                          font_size=sp(10.5), color=C(COL_TEXT_DIM),
                          halign="right", valign="middle",
                          size_hint=(None, None), height=dp(14))
        count, tint = self._tick_state(msg, outgoing, ticks_enabled)
        self.ticks = TickMarks(count=count, tint=tint)
        self.star = Widget(size_hint=(None, None), size=(dp(12), dp(14)))
        self.star.bind(pos=self._draw_star, size=self._draw_star)
        self.meta_box = BoxLayout(orientation="horizontal", spacing=dp(4),
                                  size_hint=(None, None), height=dp(14))
        self.meta_box.add_widget(self.star)
        self.meta_box.add_widget(self.meta)
        self.meta_box.add_widget(self.ticks)
        meta_row = AnchorLayout(anchor_x="right", size_hint_y=None,
                                height=dp(14))
        meta_row.add_widget(self.meta_box)
        self.add_widget(meta_row)
        self._draw_star()

    # ---- content -------------------------------------------------------------
    @staticmethod
    def _display_text(msg: dict, media) -> str:
        """What the text part shows. A photo shows its caption (if any), a
        voice note shows nothing — their readable `body` exists for older
        clients, previews and exports, not to be printed twice."""
        if msg.get("kind") == "image" and isinstance(media, dict):
            return media.get("cap", "")
        if msg.get("kind") == "voice" and isinstance(media, dict):
            return ""
        return msg["body"]

    def _draw_star(self, *_a):
        self.star.canvas.clear()
        if not self.msg.get("starred"):
            self.star.width = 0
            return
        self.star.width = dp(12)
        with self.star.canvas:
            draw_icon("star_filled", self.star.center_x, self.star.center_y,
                      dp(11), C(COL_TICK_GOLD))

    @staticmethod
    def _stamp_text(msg: dict) -> str:
        """Timestamp, plus an unread nudge on incoming messages that still
        need a tap (only shown when auto-read is off, otherwise nothing is
        ever left unread and the hint would be noise)."""
        stamp = pretty_time(msg["created_at"])
        if msg["direction"] == "in" and not msg["read"]:
            return "%s   [color=%s]tap to mark read[/color]" % (
                stamp, COL_ACCENT.lstrip("#"))
        return stamp

    @staticmethod
    def _tick_state(msg: dict, outgoing: bool, ticks_enabled: bool) -> tuple:
        """(tick count, colour) for this message.

        v7: BOTH SIDES now show ticks. Previously only the sender saw them,
        so the same message displayed three ticks on one device and a bare
        timestamp on the other, and the recipient had no way to tell whether
        a read receipt had actually gone back.

        Outgoing ticks come from the relay's receipts:
            0 = queued on this device (grey)   1 = accepted by the relay
            2 = delivered to their device      3 = read by them
        Incoming ticks are derived LOCALLY from facts this device already
        knows — it holds the message (2), and it knows whether the user has
        read it (3) — so the same message reads the same on both screens
        without the relay having to send anything extra.

        Either side disabling ticks caps the display at one, matching the
        privacy rule the relay already enforces for receipts.
        """
        if outgoing:
            status = msg["status"]
        else:
            status = 3 if msg["read"] else 2
        if not ticks_enabled:
            status = min(status, 1)
        if status <= 0:
            return 1, COL_TICK_GREY          # queued: one grey tick
        return min(status, 3), COL_TICK_GOLD

    def update(self, msg: dict, ticks_enabled: bool) -> None:
        """Repaint in place after a status/read/star/media change.

        A tick advancing 1/3 -> 2/3 -> 3/3 used to rebuild the entire
        conversation; it now rewrites one short label.
        """
        outgoing = msg["direction"] == "out"
        self.msg = msg
        self.meta.text = self._stamp_text(msg)
        self.ticks.set_state(*self._tick_state(msg, outgoing, ticks_enabled))
        if self.media is not None:
            media = message_ext(msg).get("media")
            if isinstance(media, dict):
                self.media.media = media
            self.media.refresh(msg)
        self._draw_star()
        if self._last_width:
            self.layout_for(self._last_width)

    def _body_min_height(self, _w, value: float) -> None:
        """Safety net for the measured height (desktop's selectable text).

        The TextInput re-wraps a frame after it is given a width. If, AT ITS
        FINAL WIDTH, it wants more room than measure_text predicted — a font
        metric rounding apart — grow the bubble rather than clip the text.

        Values reported before then are ignored: a TextInput starts life
        100px wide, and its first wrap at that width asks for several times
        the real height. Trusting that reading is what made every bubble
        balloon in an early v9 build.
        """
        if self.body is None or not self._last_width or \
                self.body.width < self._text_w:
            return
        floor = value if value > self._text_h + 0.5 else 0.0
        if abs(floor - self._body_floor) > 0.5:
            self._body_floor = floor
            self.layout_for(self._last_width)

    def layout_for(self, available: float) -> None:
        """Size every part, then the bubble to fit them.

        The widest part decides the content width, capped at 78% of the
        row; the quote and the meta row stretch to it, the text wraps
        inside it, and a photo keeps its own aspect-correct size.
        """
        self._last_width = available
        max_w = max(dp(150), available * 0.78 - dp(20))
        parts = []                                  # (widget, w, h)
        widest = dp(60)
        if self.media is not None:
            mw, mh = self.media.natural_size(max_w)
            self.media.size = (mw, mh)
            widest = max(widest, mw)
            parts.append(mh)
        text_w = text_h = 0
        if self.body is not None:
            wrap = self.media.width if isinstance(self.media,
                                                  ImageContent) else max_w
            text_w, text_h = measure_text(self.body.text, self.body.font_size,
                                          wrap)
            self._text_w, self._text_h = text_w, text_h
            if isinstance(self.body, SelectableText):
                # +2px of slack: handing a TextInput the exact width of its
                # widest measured line can tip that line over by a rounding
                # error and add a wrap. Invisible, and it prevents that.
                self.body.width = text_w + dp(2)
                self.body.height = max(text_h, self._body_floor)
            else:
                self.body.width = text_w
                self.body.text_size = (text_w + 0.5, None)
                self.body.height = text_h
            widest = max(widest, self.body.width)
            parts.append(self.body.height)
        if self.quote is not None:
            widest = max(widest, min(max_w, self.quote.natural_width()))
        if self.fwd is not None:
            widest = max(widest, measure_text("Forwarded", sp(11.5),
                                              dp(300))[0])
        self.meta.texture_update()
        self.meta.size = (self.meta.texture_size[0], dp(14))
        meta_w = self.meta.width + self.ticks.width + self.star.width
        meta_w += dp(4) * (1 + (1 if self.ticks.width else 0))
        self.meta_box.width = meta_w
        widest = max(widest, meta_w)
        if self.quote is not None:
            self.quote.width = widest
            parts.append(self.quote.height)
        if self.fwd is not None:
            self.fwd.width = widest
            parts.append(self.fwd.height)
        parts.append(dp(14))                        # meta row
        pad_l, pad_t, pad_r, pad_b = self.padding
        self.width = widest + pad_l + pad_r
        self.height = (sum(parts) + self.spacing * (len(parts) - 1)
                       + pad_t + pad_b)


class BubbleRow(AnchorLayout):
    """A full-width row holding one bubble, and the owner of its touches.

    Long-press (or right-click) selects the message — the "highlight mode"
    the app bar switches into — and while anything is selected a tap
    toggles a message in or out instead of doing its normal thing. A
    selected row is washed in gold across the whole width, which also makes
    multi-select obvious.
    """

    LONG_PRESS = 0.42                     # seconds

    def __init__(self, bubble: MessageBubble, outgoing: bool, screen, **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(48))
        kw.setdefault("padding", [dp(10), dp(3), dp(10), dp(3)])
        super().__init__(anchor_x="right" if outgoing else "left",
                         anchor_y="center", **kw)
        self.bubble, self.screen = bubble, screen
        self.msg_id = bubble.msg["id"]
        with self.canvas.before:
            self._tint = Color(*COL_SELECT_RGBA)
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
        self._tint.a = COL_SELECT_RGBA[3] if selected else 0

    # ---- touches -------------------------------------------------------------
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
        super().on_touch_down(touch)       # children: links, play, text drag
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


class DayDivider(AnchorLayout):
    """Soft date pill between days — the familiar messenger rhythm."""

    def __init__(self, text: str, **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(40))
        super().__init__(**kw)
        pill = AnchorLayout(size_hint=(None, None), size=(dp(90), dp(24)))
        paint_round(pill, COL_CARD, radius=dp(12))
        label = Label(text=text, font_size=sp(11), color=C(COL_TEXT_DIM),
                      size_hint=(None, None), height=dp(24))
        label.bind(texture_size=lambda w, v: (
            setattr(w, "width", v[0]),
            setattr(pill, "width", v[0] + dp(26))))
        label.texture_update()
        pill.add_widget(label)
        self.add_widget(pill)


class JumpToBottomButton(ButtonBehavior, Widget):
    """Floating "back to the latest message" pill.

    Appears whenever the view is scrolled away from the bottom. When
    messages arrived while the user was reading history it carries a count,
    so a new message is announced WITHOUT yanking the scroll position — the
    old behaviour snapped every reader to the bottom mid-sentence.
    """

    def __init__(self, on_tap=None, **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("size", (dp(40), dp(40)))
        super().__init__(**kw)
        self._count = 0
        self.opacity = 0
        self.disabled = True
        with self.canvas.before:
            self._shadow_col = Color(*C(COL_CARD_EDGE))
            self._shadow = Ellipse()
            self._bg_col = Color(*C(COL_CARD))
            self._bg = Ellipse()
        self.bind(pos=self._sync, size=self._sync, state=self._on_state)
        if on_tap is not None:
            self.bind(on_release=lambda _w: on_tap())
        self._badge = Label(text="", bold=True, font_size=sp(10),
                            color=C("#FFFFFF"), size_hint=(None, None),
                            size=(dp(18), dp(18)), opacity=0)
        self.add_widget(self._badge)
        self._sync()

    def _on_state(self, _w, state):
        self._bg_col.rgba = C(COL_PRESSED if state == "down" else COL_CARD)

    def _sync(self, *_a):
        self._shadow.pos = (self.x - dp(1), self.y - dp(1.5))
        self._shadow.size = (self.width + dp(2), self.height + dp(2))
        self._bg.pos, self._bg.size = self.pos, self.size
        self._badge.center = (self.center_x + dp(13), self.center_y + dp(13))
        self.canvas.after.clear()
        cx, cy = self.center_x, self.center_y
        r = self.width * 0.19
        with self.canvas.after:
            Color(*C(COL_ACCENT_DARK))
            # downward chevron
            Line(points=[cx - r, cy + r * 0.55, cx, cy - r * 0.5,
                         cx + r, cy + r * 0.55],
                 width=dp(1.6), cap="round", joint="round")
            if self._count > 0:
                Color(*C(COL_ACCENT))
                Ellipse(pos=(self.center_x + dp(4), self.center_y + dp(4)),
                        size=(dp(18), dp(18)))

    def set_visible(self, visible: bool) -> None:
        target = 1.0 if visible else 0.0
        self.disabled = not visible
        if abs(self.opacity - target) < 0.01:
            return
        Animation.cancel_all(self, "opacity")
        Animation(opacity=target, d=0.15).start(self)

    def set_count(self, count: int) -> None:
        count = max(0, int(count))
        if count == self._count:
            return
        self._count = count
        self._badge.text = str(min(count, 99)) if count else ""
        self._badge.opacity = 1 if count else 0
        self._sync()


class ConversationScreen(Screen):
    """The chat itself: the app bar with the peer, the chat background,
    bubbles, and a rounded composer pinned to the bottom."""

    def __init__(self, app: "FextApp", **kw):
        super().__init__(name="conversation", **kw)
        self.app = app
        # ---- incremental-render bookkeeping ---------------------------------
        self._bubbles: dict = {}          # message id -> MessageBubble
        self._rendered_ids: list = []     # ids currently on screen, in order
        self._rendered_sigs: dict = {}    # id -> (status, read, body)
        self._rendered_peer: Optional[str] = None
        self._rendered_ticks: Optional[bool] = None
        self._last_day: Optional[str] = None
        self._unseen = 0                  # arrived while reading history
        self._scroll_trigger = None       # pending deferred scroll, if any
        self._stick_to_bottom = True      # follow new content?
        self._last_content_h = 0.0        # for preserving the read position
        self.has_older = False            # more history available to page in
        self._more_button = CozyButton(
            "Load earlier messages", bg=COL_CARD, fg=COL_ACCENT_DARK,
            outline=COL_CARD_EDGE, size_hint_y=None, height=dp(38),
            font_size=sp(12.5), pressed=COL_PRESSED,
            on_release=lambda _w: self.app.load_older_messages())
        self.selected: dict = {}          # local id -> message, highlighted
        self._rows: dict = {}             # local id -> BubbleRow
        self.reply_to: Optional[dict] = None
        self.recorder = None              # VoiceRecorder while recording
        self._rec_tick = None
        self.peer: Optional[str] = None
        self.peer_name_text = ""
        root = BoxLayout(orientation="vertical")
        paint_round(root, COL_BG, radius=dp(0))
        self.add_widget(root)

        # ---- app bar: normal mode and highlight (selection) mode -------------
        # Two complete bars swapped in one slot, rather than one bar whose
        # buttons come and go: each mode lays out exactly once, so nothing
        # shifts sideways when the mode changes.
        self.bar = FloatLayout(size_hint_y=None, height=dp(56))
        paint_round(self.bar, COL_APPBAR, radius=dp(0))
        root.add_widget(self.bar)

        self.normal_bar = BoxLayout(padding=[dp(2), 0, dp(4), 0],
                                    spacing=dp(2), size_hint=(1, 1),
                                    pos_hint={"x": 0, "y": 0})
        self.normal_bar.add_widget(IconButton(
            "back", size=(dp(44), dp(56)),
            on_release=lambda _w: self.app.go_back_to_chats()))
        self.peer_avatar_holder = AnchorLayout(size_hint_x=None, width=dp(40))
        self.normal_bar.add_widget(self.peer_avatar_holder)
        titles = TappableBox(on_tap=lambda: self.app.open_chat_info(),
                             padding=[dp(8), dp(8), 0, dp(8)])
        self.peer_name = single_line("", sp(16.5), COL_APPBAR_TEXT, bold=True,
                                     size_hint_y=None, height=dp(22))
        self.peer_sub = single_line("", sp(12), COL_APPBAR_DIM,
                                    size_hint_y=None, height=dp(18))
        titles.add_widget(self.peer_name)
        titles.add_widget(self.peer_sub)
        self.normal_bar.add_widget(titles)
        self.more_button = IconButton(
            "more", size=(dp(44), dp(56)),
            on_release=lambda w: self.open_menu(w))
        self.normal_bar.add_widget(self.more_button)

        self.select_bar = BoxLayout(padding=[dp(2), 0, dp(2), 0],
                                    size_hint=(1, 1),
                                    pos_hint={"x": 0, "y": 0})
        self.select_bar.add_widget(IconButton(
            "close", size=(dp(44), dp(56)),
            on_release=lambda _w: self.clear_selection()))
        self.select_count = Label(text="1", bold=True, font_size=sp(18),
                                  color=C(COL_APPBAR_TEXT), halign="left",
                                  valign="middle", size_hint_x=None,
                                  width=dp(34))
        self.select_count.bind(size=lambda w, v: setattr(w, "text_size", v))
        self.select_bar.add_widget(self.select_count)
        self.select_bar.add_widget(Widget())
        self.actions: dict = {}
        for name, handler in (("reply", self._act_reply),
                              ("copy", self._act_copy),
                              ("star", self._act_star),
                              ("forward", self._act_forward),
                              ("share", self._act_share),
                              ("delete", self._act_delete)):
            button = IconButton(name, size=(dp(42), dp(56)),
                                on_release=lambda _w, h=handler: h())
            self.actions[name] = button
            self.select_bar.add_widget(button)
        self.bar.add_widget(self.normal_bar)

        # ---- messages --------------------------------------------------------
        stack = FloatLayout()
        self.scroll = ScrollView(bar_width=dp(3), size_hint=(1, 1),
                                 pos_hint={"x": 0, "y": 0})
        self.messages = BoxLayout(orientation="vertical", size_hint_y=None,
                                  padding=[0, dp(10), 0, dp(10)],
                                  spacing=dp(2))
        # Keeps short conversations sitting on the composer instead of flying
        # up under the app bar — see _sync_filler. Always the FIRST child, so
        # it pads above every bubble.
        self._filler = Widget(size_hint_y=None, height=0)
        self._syncing_filler = False
        self.messages.add_widget(self._filler)
        self.messages.bind(minimum_height=self.messages.setter("height"))
        self.messages.bind(height=lambda *_a: self._on_content_height())
        self.messages.bind(minimum_height=lambda *_a: self._sync_filler())
        self.scroll.bind(height=lambda *_a: self._sync_filler())
        self.scroll.add_widget(self.messages)
        stack.add_widget(self.scroll)
        self.jump_button = JumpToBottomButton(on_tap=self.jump_to_latest,
                                              pos_hint={"right": 0.965,
                                                        "y": 0.02})
        stack.add_widget(self.jump_button)
        self.scroll.bind(scroll_y=lambda *_a: self._on_scrolled())
        root.add_widget(stack)

        # ---- reply preview (shown while composing a reply) -------------------
        self.reply_bar = BoxLayout(size_hint_y=None, height=0, opacity=0,
                                   disabled=True,
                                   padding=[dp(14), dp(6), dp(4), dp(6)],
                                   spacing=dp(10))
        paint_round(self.reply_bar, COL_BG_LIST, radius=dp(0))
        reply_icon = Widget(size_hint_x=None, width=dp(22))
        reply_icon.bind(pos=lambda w, *_a: self._paint_reply_icon(w),
                        size=lambda w, *_a: self._paint_reply_icon(w))
        self.reply_bar.add_widget(reply_icon)
        self.reply_bar.bind(pos=lambda *_a: self._paint_reply_icon(reply_icon),
                            size=lambda *_a: self._paint_reply_icon(
                                reply_icon))
        texts = BoxLayout(orientation="vertical")
        with texts.canvas.before:
            Color(*C(COL_ACCENT))
            self._reply_accent = Rectangle()
        texts.bind(pos=lambda w, *_a: self._sync_reply_accent(w),
                   size=lambda w, *_a: self._sync_reply_accent(w))
        self.reply_author = single_line("", sp(13), COL_ACCENT_DARK, bold=True,
                                        padding=[dp(10), 0])
        self.reply_snip = single_line("", sp(13), COL_TEXT_DIM,
                                      padding=[dp(10), 0])
        texts.add_widget(self.reply_author)
        texts.add_widget(self.reply_snip)
        self.reply_bar.add_widget(texts)
        self.reply_bar.add_widget(IconButton(
            "close", tint=COL_TEXT_DIM, press="#E9DFCF",
            size=(dp(40), dp(40)), pos_hint={"center_y": .5},
            on_release=lambda _w: self.cancel_reply()))
        root.add_widget(self.reply_bar)

        # ---- composer ----------------------------------------------------------
        self.composer = BoxLayout(size_hint_y=None, height=dp(60),
                                  padding=[dp(4), dp(8), dp(8), dp(8)],
                                  spacing=dp(4))
        paint_round(self.composer, COL_BG_LIST, radius=dp(0))
        with self.composer.canvas.before:
            Color(*C(COL_DIVIDER))
            self._composer_rule = Rectangle()
        self.composer.bind(pos=self._sync_rule, size=self._sync_rule)
        self.attach_button = IconButton(
            "image", tint=COL_TEXT_DIM, press="#E9DFCF",
            size=(dp(44), dp(44)), pos_hint={"center_y": .5},
            on_release=lambda _w: self.pick_image())
        self.entry = CozyInput(hint="Message", radius=dp(22), height=dp(44),
                               on_enter=self.app.send_current)
        self.action_button = IconButton(
            "send", bg=COL_ACCENT, tint="#FFFFFF", size=(dp(44), dp(44)),
            pos_hint={"center_y": .5},
            on_release=lambda _w: self._on_action_button())
        self.entry.input.bind(text=lambda *_a: self._sync_action_button())
        self.composer_parts = (self.attach_button, self.entry,
                               self.action_button)
        for part in self.composer_parts:
            self.composer.add_widget(part)
        # The recording strip that replaces the composer while recording.
        self.rec_cancel = IconButton(
            "delete", tint=COL_DANGER, press="#E9DFCF",
            size=(dp(44), dp(44)), pos_hint={"center_y": .5},
            on_release=lambda _w: self.cancel_recording())
        self.rec_label = Label(text="", font_size=sp(14), color=C(COL_TEXT),
                               halign="left", valign="middle", markup=True)
        self.rec_label.bind(size=lambda w, v: setattr(w, "text_size", v))
        self.rec_send = IconButton(
            "send", bg=COL_ACCENT, tint="#FFFFFF", size=(dp(44), dp(44)),
            pos_hint={"center_y": .5},
            on_release=lambda _w: self.finish_recording())
        root.add_widget(self.composer)
        self._sync_action_button()

    # ---- header --------------------------------------------------------------
    def set_peer(self, peer: str, contact: dict, unread_count: int) -> None:
        if peer != self.peer:
            self.clear_selection()
            self.cancel_reply()
            self.cancel_recording()
        self.peer = peer
        name = contact.get("username") or short_addr(peer, 14)
        self.peer_name_text = name
        self.peer_avatar_holder.clear_widgets()
        self.peer_avatar_holder.add_widget(Avatar(peer, name, diameter=dp(38)))
        self.peer_name.text = name
        bits = [short_addr(peer, 12)]
        if not contact.get("notify", 1):
            bits.append("muted")
        if unread_count and not contact.get("auto_read", 1):
            bits.append(f"{unread_count} unread")
        self.peer_sub.text = "  ·  ".join(bits)

    # ---- bubbles -------------------------------------------------------------
    def render(self, messages: list, ticks_enabled: bool,
               on_tap_unread: Callable, peer: Optional[str] = None) -> None:
        """Incremental render.

        The naive version cleared the whole box and rebuilt every widget on
        every refresh — and a refresh fires on each inbound message, each
        tick receipt and each send. That is O(history) work per event: at
        1000 messages a single arriving message cost seconds of widget
        construction, most of it re-creating bubbles that had not changed.

        Now the common cases are O(changed):
          * new messages appended      -> build only the new bubbles
          * tick/read status changed   -> repaint that bubble's meta line
          * nothing changed            -> no work at all
        A full rebuild still happens when the shape of the list changes in a
        way appending cannot express (peer switch, history paged in, an
        import inserting older rows, messages deleted).
        """
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

        # 1) in-place updates for rows whose status/read/body moved
        for msg in messages:
            mid = msg["id"]
            previous = self._rendered_sigs.get(mid)
            if previous is None or previous == signatures[mid]:
                continue
            bubble = self._bubbles.get(mid)
            if bubble is not None:
                bubble.update(msg, ticks_enabled)

        # 2) append whatever is genuinely new.
        #    Capture the scroll position BEFORE adding widgets: appending
        #    changes the content height, which changes what "at the bottom"
        #    means.
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
                # Following your own message down is expected; so is staying
                # pinned to the bottom if that is where you already were.
                self.scroll_to_bottom()
            else:
                # You were reading history — say so, do NOT drag the view.
                self.note_new_messages(appended)

    def _full_render(self, messages: list, ticks_enabled: bool,
                     on_tap_unread: Callable, peer: Optional[str]) -> None:
        self.messages.clear_widgets()
        self.messages.add_widget(self._filler)     # stays the topmost child
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
                msg.get("media_state"), msg.get("media_path"), msg.get("ext"))

    def _append_bubble(self, msg: dict, ticks_enabled: bool,
                       on_tap_unread: Callable) -> None:
        day = str(msg.get("created_at", ""))[:10]
        if day and day != self._last_day:
            self._last_day = day
            self.messages.add_widget(DayDivider(self._day_text(day)))
        outgoing = msg["direction"] == "out"
        bubble = MessageBubble(msg, ticks_enabled, self)
        self._bubbles[msg["id"]] = bubble
        row = BubbleRow(bubble, outgoing, self)
        row.set_selected(msg["id"] in self.selected)
        self._rows[msg["id"]] = row
        self.messages.add_widget(row)

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
        """Swap the app bar and enable only the actions that apply."""
        on = bool(self.selected)
        if on and self.select_bar.parent is None:
            self.bar.remove_widget(self.normal_bar)
            self.bar.add_widget(self.select_bar)
        elif not on and self.normal_bar.parent is None:
            self.bar.remove_widget(self.select_bar)
            self.bar.add_widget(self.normal_bar)
        if not on:
            return
        msgs = list(self.selected.values())
        self.select_count.text = str(len(msgs))
        has_text = any(self._text_of(m) for m in msgs)
        self.actions["reply"].disabled = len(msgs) != 1
        self.actions["copy"].disabled = not has_text
        self.actions["star"].set_icon(
            "star_filled" if all(m.get("starred") for m in msgs) else "star")
        self.actions["share"].disabled = not (has_text or any(
            m.get("kind") == "image" and m.get("media_path") for m in msgs))
        self.actions["forward"].disabled = any(
            m.get("kind") in ("image", "voice") and not
            (message_ext(m).get("media") or {}).get("blob") for m in msgs)

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
                             COL_OK)

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

    # ---- replies ---------------------------------------------------------------
    def _author_of(self, msg: dict) -> str:
        return self.app.keys.address if msg["direction"] == "out" \
            else msg["peer"]

    def author_name(self, address: Optional[str],
                    outgoing_bubble: bool = False) -> str:
        if not address:
            return "Message"
        if address == self.app.keys.address:
            return "You"
        if address == self.peer:
            return self.peer_name_text or short_addr(address, 12)
        contact = self.app.store.get_contact(address) if self.app.store \
            else None
        return (contact or {}).get("username") or short_addr(address, 12)

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
        self.reply_bar.height, self.reply_bar.opacity = dp(52), 1
        self.reply_bar.disabled = False
        self.entry.input.focus = True

    def take_reply(self) -> Optional[dict]:
        reply = self.reply_to
        self.cancel_reply()
        return reply

    def cancel_reply(self) -> None:
        self.reply_to = None
        self.reply_bar.height, self.reply_bar.opacity = 0, 0
        self.reply_bar.disabled = True

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
                self.scroll.scroll_to(row, padding=dp(60), animate=True)
                row.set_selected(True)
                Clock.schedule_once(
                    lambda _dt: row.set_selected(mid in self.selected), 0.9)
                return
        self.app._set_status("That message is further back — tap 'Load "
                             "earlier messages' to find it", COL_TEXT_DIM)

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
            return                       # a long-press just selected it
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
                    self.app._set_status("Couldn't play that voice note",
                                         COL_DANGER)
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

    # ---- the ⋮ menu (what used to sit behind the title tap) --------------------
    def open_menu(self, anchor) -> None:
        if self.peer is None or self.app.store is None:
            return
        peer = self.peer
        contact = self.app.store.get_contact(peer) or {}
        items = []
        if self.app.store.unread_for(peer):
            items.append(("Mark all as read",
                          lambda: self.app.mark_peer_read(peer)))
        items.append(("Chat info", self.app.open_chat_info))
        items.append(("Starred messages",
                      lambda: StarredModal(self.app, peer).open()))
        items.append(("Mute notifications" if contact.get("notify", 1)
                      else "Unmute notifications",
                      lambda: self.app.toggle_notify(peer)))
        items.append(("Ticks, read & export", self.app.open_contact_settings))
        DropMenu(items).open_below(anchor)

    # ---- composer: photo, voice, send ------------------------------------------
    def _paint_reply_icon(self, widget) -> None:
        widget.canvas.clear()
        with widget.canvas:
            draw_icon("reply", widget.center_x, self.reply_bar.center_y,
                      dp(20), C(COL_ACCENT))

    def _sync_reply_accent(self, widget) -> None:
        self._reply_accent.pos = (widget.x, widget.y + dp(3))
        self._reply_accent.size = (dp(2.5), max(0, widget.height - dp(6)))

    def _sync_rule(self, *_a) -> None:
        self._composer_rule.pos = (self.composer.x, self.composer.top - dp(1))
        self._composer_rule.size = (self.composer.width, dp(1))

    def _voice_possible(self) -> bool:
        return IS_ANDROID

    def _sync_action_button(self) -> None:
        typing = bool(self.entry.input.text.strip())
        self.action_button.set_icon(
            "send" if typing or not self._voice_possible() else "mic")

    def _on_action_button(self) -> None:
        if self.entry.input.text.strip() or not self._voice_possible():
            self.app.send_current()
        else:
            self.start_recording()

    def pick_image(self) -> None:
        if not self.app.media_available():
            self.app._set_status("Photos and voice notes need the v9 relay — "
                                 "this one can't carry them yet",
                                 COL_TICK_GOLD)
            return
        if IS_ANDROID:
            try:
                android_pick_image(self._picked)
            except Exception:
                log.exception("photo picker failed")
                self.app._set_status("Couldn't open the photo picker",
                                     COL_DANGER)
        else:
            ImageChooserModal(self._picked).open()

    def _picked(self, path: str) -> None:
        AttachPreviewModal(self.app, path).open()

    def start_recording(self) -> None:
        if not self.app.media_available():
            self.app._set_status("Voice notes need the v9 relay — this one "
                                 "can't carry them yet", COL_TICK_GOLD)
            return
        if not android_has_permission("android.permission.RECORD_AUDIO"):
            android_request_permissions(
                ["android.permission.RECORD_AUDIO"],
                lambda _p, grants: run_on_ui(
                    self._after_mic_permission, bool(grants) and all(grants)))
            return
        try:
            self.recorder = VoiceRecorder()
        except Exception:
            log.exception("could not start recording")
            self.recorder = None
            self.app._set_status("Couldn't start recording", COL_DANGER)
            return
        self.composer.clear_widgets()
        for part in (self.rec_cancel, self._rec_dot_widget(), self.rec_label,
                     self.rec_send):
            self.composer.add_widget(part)
        self._rec_tick = Clock.schedule_interval(self._update_rec_label, 0.2)
        self._update_rec_label(0)

    def _after_mic_permission(self, granted: bool) -> None:
        if granted:
            self.start_recording()
        else:
            self.app._set_status("Microphone permission is needed for voice "
                                 "notes", COL_TICK_GOLD)

    def _rec_dot_widget(self) -> Widget:
        dot = Widget(size_hint_x=None, width=dp(14))

        def paint(*_a):
            dot.canvas.clear()
            with dot.canvas:
                Color(*C(COL_DANGER))
                Ellipse(pos=(dot.center_x - dp(5), dot.center_y - dp(5)),
                        size=(dp(10), dp(10)))
        dot.bind(pos=paint, size=paint)
        return dot

    def _update_rec_label(self, _dt) -> None:
        if self.recorder is None:
            return
        sec = self.recorder.elapsed_ms // 1000
        self.rec_label.text = f"{sec // 60}:{sec % 60:02d}    Recording…"
        if sec >= MAX_VOICE_SECONDS:
            self.finish_recording()

    def _end_recording_ui(self) -> None:
        if self._rec_tick is not None:
            self._rec_tick.cancel()
            self._rec_tick = None
        self.composer.clear_widgets()
        for part in self.composer_parts:
            self.composer.add_widget(part)
        self._sync_action_button()

    def finish_recording(self) -> None:
        recorder, self.recorder = self.recorder, None
        if recorder is None:
            return
        self._end_recording_ui()
        try:
            path, duration, wave = recorder.stop()
        except Exception:
            log.exception("recording failed")
            self.app._set_status("Recording failed", COL_DANGER)
            return
        if duration < 700:
            discard_media_file(path)
            self.app._set_status("That was too short to send", COL_TEXT_DIM)
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
        """Height of the real messages, with the bottom-align filler removed."""
        return self.messages.minimum_height - self._filler.height

    def _sync_filler(self) -> None:
        """Pin a short conversation to the BOTTOM of the viewport.

        Kivy lays a ScrollView's content out from the TOP whenever it is
        shorter than the viewport (scrollview.py: `y = self.top - vp.height`),
        and because always_overscroll defaults to True the very same branch
        runs for ANY scroll_y above zero. So one stray touch was enough to
        lift a two-message chat up under the app bar and leave a screenful of
        background beneath it — the reported "messages scroll all the way to the
        top". It was never a scroll: it was the layout Kivy falls back to,
        which is also why nothing pulled the view back down. at_bottom() read
        True the entire time, quite correctly, since the newest message WAS
        on screen.

        Instead of fighting the ScrollView we make the short case impossible:
        pad the TOP until the content is at least viewport-tall. Every render
        then takes the ordinary scrolling branch, scroll_y == 0 means "newest
        message resting on the composer" in both states, and a conversation
        that genuinely overflows is untouched — the filler collapses to zero
        and this method stops mattering.
        """
        if self._syncing_filler:
            return          # setting the height below re-enters us via bind
        self._syncing_filler = True
        try:
            want = max(0.0, self.scroll.height - self.content_height())
            if abs(self._filler.height - want) > 0.5:
                self._filler.height = want
        finally:
            self._syncing_filler = False

    # ---- scroll position -----------------------------------------------------
    BOTTOM_EPSILON = 0.02        # within 2% of the end counts as "at bottom"

    def at_bottom(self) -> bool:
        """True when the latest message is already on screen.

        A conversation that does not fill the viewport is always showing its
        newest message, whatever scroll_y happens to say; treating that as
        at-bottom keeps the jump pill from appearing over a two-line chat.
        The comparison uses content_height(), NOT messages.height, because
        the filler pads the latter up to the viewport by design.
        """
        if self.content_height() <= self.scroll.height:
            return True
        return self.scroll.scroll_y <= self.BOTTOM_EPSILON

    def scroll_to_bottom(self, animate: bool = True) -> None:
        """Bring the newest message into view.

        v7: this used to fire two hard `scroll_y = 0` assignments 30ms and
        120ms apart. Because bubbles wrap (and therefore resize) after the
        first assignment, the list visibly jumped twice — the "everything
        shoots to the top" effect. Now the position settles once, on the
        frame after layout, and eases instead of snapping.
        """
        self._stick_to_bottom = True

        def _apply(_dt):
            self._scroll_trigger = None
            Animation.cancel_all(self.scroll, "scroll_y")
            if not animate or self.content_height() <= self.scroll.height:
                self.scroll.scroll_y = 0
            else:
                Animation(scroll_y=0, d=0.22, t="out_quad").start(self.scroll)
            self._unseen = 0
            self._sync_jump_button()

        # One pending settle at a time. A single user action can trigger
        # several refreshes (mark-read, chat repaint, list repaint), and
        # each used to queue its own deferred scroll — so the view could be
        # dragged back to the bottom a beat AFTER the user had scrolled away.
        if self._scroll_trigger is not None:
            self._scroll_trigger.cancel()
        self._scroll_trigger = Clock.schedule_once(_apply, 0.02)

    def jump_to_latest(self) -> None:
        self._unseen = 0
        self.scroll_to_bottom(animate=True)

    def _on_content_height(self) -> None:
        """Keep the view stable while the content height changes.

        Bubbles wrap AFTER they are added, so the list grows over the frames
        following a render — the original code papered over this by slamming
        scroll_y to 0 twice, 30ms and 120ms apart, which is what made the
        whole conversation visibly jump. Reacting to the height itself is
        both exact and cheap:

          * following the conversation -> stay pinned to the newest message
          * reading history            -> hold the CURRENT reading position
            by converting the old pixel offset into the new proportion, so
            appending a message below does not shove the text you are
            reading up the screen
        """
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
        """Reaching the bottom by hand clears the unseen counter, and moving
        away from it means the user is reading — stop following."""
        if self.at_bottom():
            self._unseen = 0
            self._stick_to_bottom = True
        else:
            self._stick_to_bottom = False
        self._sync_jump_button()

    def cancel_pending_scroll(self) -> None:
        """Hand scroll control back to the user.

        Cancels the queued settle, any in-flight easing, AND the residual
        momentum of Kivy's damped overscroll effect — without that last part
        a fling can keep dragging the view for several frames after the user
        has grabbed it, which reads as the app fighting them.
        """
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
                pass          # effect internals vary; momentum is cosmetic

    def _sync_jump_button(self) -> None:
        self.jump_button.set_count(self._unseen)
        self.jump_button.set_visible(not self.at_bottom() or
                                     self._unseen > 0)

    def note_new_messages(self, count: int) -> None:
        """Announce arrivals that landed while the user was reading history.
        Deliberately does NOT move the view — that is the whole point."""
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
        return date.strftime("%d %B %Y")

    def _empty_state(self) -> BoxLayout:
        box = BoxLayout(orientation="vertical", size_hint_y=None,
                        height=dp(120), padding=[dp(30), dp(30), dp(30), 0])
        box.add_widget(InfoLabel(
            "This is the beginning of your encrypted chat.\nMessages are "
            "sealed on this device before they leave it.",
            color=COL_TEXT_DIM, font_size=sp(13), halign="center"))
        return box



# =============================================================================
#  FextApp — the Kivy application controller
# =============================================================================
class SplashOverlay(FloatLayout):
    """The loading screen: the mark above the wordmark, on the app's cream.

    It covers the first paint — the ScreenManager building its two screens,
    the identity being read off disk, the transport probe going out — and
    then fades so the chat list does not simply snap into existence.

    It is a widget in the tree rather than a ModalView on purpose. The
    first-run dialog IS a ModalView, and a ModalView attaches to the Window
    above everything, so raising it while this was still up would show it
    through the loading screen. _post_build therefore waits for the fade.
    """

    FADE = 0.1          # seconds of fade-out
    HOLD = 0.9          # minimum time on screen, so it never merely blinks

    def __init__(self, **kw):
        super().__init__(**kw)
        # White, as specified — the same ground as the launcher icon and
        # the platform presplash, so the three hand over seamlessly.
        paint_round(self, "#FFFFFF", radius=dp(0))
        column = BoxLayout(orientation="vertical", spacing=dp(22),
                           size_hint=(None, None), width=dp(280),
                           height=dp(300),
                           pos_hint={"center_x": 0.5, "center_y": 0.5})
        if LOGO_FILE.exists():
            column.add_widget(KivyImage(source=str(LOGO_FILE),
                                        allow_stretch=True, keep_ratio=True,
                                        size_hint_y=None, height=dp(190)))
        wordmark = Label(
            text=("[b]Ferrite Text[/b]\n[b]Messenger[/b]\n"
                  "[size=%d][color=%s]by koh-gt[/color][/size]"
                  % (int(sp(12.5)), COL_TEXT_DIM.lstrip("#"))),
            markup=True, halign="center", valign="top", font_size=sp(21),
            color=C(COL_TEXT), line_height=1.25,
            size_hint_y=None, height=dp(100))
        wordmark.bind(size=lambda w, v: setattr(w, "text_size", v))
        column.add_widget(wordmark)
        self.add_widget(column)


class ToggleSwitch(ButtonBehavior, Widget):
    """A pill switch, drawn (no glyphs), gold when on."""

    def __init__(self, active: bool = False,
                 on_change: Optional[Callable[[bool], None]] = None, **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("size", (dp(44), dp(26)))
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
            Color(*C(COL_ACCENT if self.active else "#D8CBB8"))
            RoundedRectangle(pos=self.pos, size=self.size, radius=[h / 2])
            Color(*C("#FFFFFF"))
            knob = h - dp(6)
            x = self.right - knob - dp(3) if self.active else self.x + dp(3)
            Ellipse(pos=(x, self.y + dp(3)), size=(knob, knob))


class SettingRow(ButtonBehavior, BoxLayout):
    """Icon · title (+ hint) · control. The whole row is the tap target."""

    def __init__(self, icon: str, title: str, hint: str = "",
                 control: Optional[Widget] = None,
                 on_tap: Optional[Callable] = None, **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(64) if hint else dp(52))
        kw.setdefault("padding", [dp(16), dp(6), dp(16), dp(6)])
        kw.setdefault("spacing", dp(16))
        super().__init__(**kw)
        glyph = Widget(size_hint_x=None, width=dp(24))
        glyph.bind(pos=lambda w, *_a: self._glyph(w, icon),
                   size=lambda w, *_a: self._glyph(w, icon))
        self.add_widget(glyph)
        texts = BoxLayout(orientation="vertical")
        texts.add_widget(single_line(title, sp(15.5), COL_TEXT))
        if hint:
            self.hint = single_line(hint, sp(12.5), COL_TEXT_DIM)
            texts.add_widget(self.hint)
        self.add_widget(texts)
        if control is not None:
            holder = AnchorLayout(size_hint_x=None, width=control.width,
                                  anchor_x="right")
            holder.add_widget(control)
            self.add_widget(holder)
        self._bg_col, _ = paint_round(self, COL_CARD, radius=dp(0))
        self.bind(state=lambda _w, st: setattr(
            self._bg_col, "rgba", C(COL_PRESSED if st == "down"
                                    else COL_CARD)))
        if on_tap is not None:
            self.bind(on_release=lambda _w: on_tap())

    @staticmethod
    def _glyph(widget, icon: str) -> None:
        widget.canvas.clear()
        with widget.canvas:
            draw_icon(icon, widget.center_x, widget.center_y, dp(22),
                      C(COL_TEXT_DIM))


class ChatInfoScreen(Screen):
    """Tap the name in a chat: who this is, and the switches for this chat
    (notifications, ticks, auto-read), starred messages and export."""

    def __init__(self, app: "FextApp", **kw):
        super().__init__(name="info", **kw)
        self.app = app
        self.peer: Optional[str] = None
        root = BoxLayout(orientation="vertical")
        paint_round(root, COL_BG_LIST, radius=dp(0))
        self.add_widget(root)
        bar = BoxLayout(size_hint_y=None, height=dp(56),
                        padding=[dp(2), 0, dp(12), 0], spacing=dp(6))
        paint_round(bar, COL_APPBAR, radius=dp(0))
        bar.add_widget(IconButton("back", size=(dp(44), dp(56)),
                                  on_release=lambda _w:
                                  self.app.close_chat_info()))
        bar.add_widget(single_line("Chat info", sp(18), COL_APPBAR_TEXT,
                                   bold=True))
        root.add_widget(bar)
        scroll = ScrollView(bar_width=dp(3))
        self.body = BoxLayout(orientation="vertical", size_hint_y=None,
                              spacing=dp(10), padding=[0, 0, 0, dp(16)])
        self.body.bind(minimum_height=self.body.setter("height"))
        scroll.add_widget(self.body)
        root.add_widget(scroll)

    def show(self, peer: str) -> None:
        self.peer = peer
        store = self.app.store
        contact = store.get_contact(peer) or {}
        name = contact.get("username") or short_addr(peer, 14)
        self.body.clear_widgets()

        head = BoxLayout(orientation="vertical", size_hint_y=None,
                         height=dp(196), padding=[dp(16), dp(20), dp(16),
                                                  dp(14)], spacing=dp(6))
        paint_round(head, COL_CARD, radius=dp(0))
        holder = AnchorLayout(size_hint_y=None, height=dp(92))
        holder.add_widget(Avatar(peer, name, diameter=dp(88)))
        head.add_widget(holder)
        title = single_line(name, sp(21), COL_TEXT, bold=True,
                            size_hint_y=None, height=dp(30))
        title.halign = "center"
        head.add_widget(title)
        address = CozyButton(peer, bg=COL_CARD, fg=COL_TEXT_DIM, bold=False,
                             font_size=sp(11.5), height=dp(26),
                             pressed=COL_PRESSED, shorten=True,
                             shorten_from="center", halign="center",
                             valign="middle")
        address.bind(size=lambda w, v: setattr(w, "text_size", v))
        address.bind(on_release=lambda _w: self.app.copy_text(peer))
        head.add_widget(address)
        self.body.add_widget(head)

        caps = contact.get("caps", 0) & LocalStore.CAP_V9
        rows = [
            SettingRow("bell", "Notifications",
                       "Alerts for new messages in this chat",
                       ToggleSwitch(contact.get("notify", 1),
                                    lambda on: self.app.set_notify(peer, on))),
            SettingRow("check", "Delivery & read ticks",
                       "Off for either of you caps both at one tick",
                       ToggleSwitch(contact.get("ticks_enabled", 1),
                                    lambda on: self.app.set_ticks(peer, on))),
            SettingRow("info", "Mark as read automatically",
                       "When off, tap messages to mark them read",
                       ToggleSwitch(contact.get("auto_read", 1),
                                    lambda on: self.app.set_auto_read(peer,
                                                                      on))),
        ]
        self.body.add_widget(self._card(rows))
        starred = len(store.starred(peer))
        self.body.add_widget(self._card([
            SettingRow("star", "Starred messages",
                       f"{starred} message{'s' if starred != 1 else ''}",
                       on_tap=lambda: StarredModal(self.app, peer).open()),
            SettingRow("share", "Export or import chat",
                       "Plaintext copies, kept on this device",
                       on_tap=lambda: ContactSettingsModal(self.app,
                                                           peer).open()),
        ]))
        self.body.add_widget(InfoLabel(
            "End-to-end encrypted. " +
            ("This contact runs Ferritext 9: replies, photos, voice notes "
             "and delete-for-both all reach them." if caps else
             "Replies, photos, voice notes and delete-for-both reach this "
             "contact fully once they are on Ferritext 9."),
            color=COL_TEXT_DIM, font_size=sp(12.5), padding=[dp(18), 0]))

    @staticmethod
    def _card(rows: list) -> BoxLayout:
        card = BoxLayout(orientation="vertical", size_hint_y=None)
        card.bind(minimum_height=card.setter("height"))
        for i, row in enumerate(rows):
            if i:
                rule = Widget(size_hint_y=None, height=dp(1))
                paint_round(rule, COL_DIVIDER, radius=dp(0))
                card.add_widget(rule)
            card.add_widget(row)
        return card


class FextApp(App):
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
        self._pending_status = ("Starting…", COL_TEXT_DIM)
        self._chat_anchor: Optional[int] = None  # oldest id shown in the chat
        self._dirty_chat = False               # coalesced refresh flags
        self._dirty_list = False
        self.foreground = True                 # drives reconnect aggressiveness
        self._register_modal: Optional[RegisterModal] = None
        self.splash: Optional["SplashOverlay"] = None

    # ---- lifecycle -----------------------------------------------------------
    def build(self):
        Window.clearcolor = C(COL_BG_LIST)
        if IS_MOBILE:
            # Without this the Android/iOS soft keyboard covers the composer
            # you are typing into; 'below_target' pans the view just enough
            # to keep the focused input visible.
            Window.softinput_mode = "below_target"
        else:
            # Phone-shaped by default on desktop so the layout is exercised
            # the same way it will be on a handset; still freely resizable.
            Window.size = (dp(420), dp(820))
        self.manager = ScreenManager(transition=SlideTransition(
            duration=0.18, direction="left"))
        self.chats_screen = ChatsScreen(self)
        self.conversation_screen = ConversationScreen(self)
        self.chat_info_screen = ChatInfoScreen(self)
        self.manager.add_widget(self.chats_screen)
        self.manager.add_widget(self.conversation_screen)
        self.manager.add_widget(self.chat_info_screen)
        self.voice = VoicePlayer()
        self.voice.on_progress = self.conversation_screen.on_voice_progress
        self.voice.on_stop = self.conversation_screen.on_voice_stop
        Window.bind(on_keyboard=self._on_keyboard)     # Android back button
        Clock.schedule_interval(self._drain_ui_queue, 0.15)
        self._bind_notification_taps()
        # The loading screen sits over the whole app until it fades, so the
        # root has to be something that can stack widgets. Nothing else
        # reaches for App.root; the rest of the app talks to self.manager.
        root = FloatLayout()
        root.add_widget(self.manager)
        self.splash = SplashOverlay()
        root.add_widget(self.splash)
        # The probe still goes out immediately — it is a background thread,
        # and starting it behind the loading screen is exactly the overlap
        # _probe_transport was written for.
        Clock.schedule_once(lambda _dt: self._probe_transport(), 0.05)
        Clock.schedule_once(self._dismiss_splash, SplashOverlay.HOLD)
        return root

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
        """Android/Esc back, innermost first: highlight mode, a recording, a
        pending reply, chat info, the chat — and only then the app."""
        if key in (27, 1001):
            current = self.manager.current
            if current == "info":
                self.close_chat_info()
                return True
            if current == "conversation":
                screen = self.conversation_screen
                if screen.selecting:
                    screen.clear_selection()
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
        self.manager.transition = NoTransition()
        self.manager.current = "chats"
        self.manager.transition = SlideTransition(duration=0.18)
        self.chats_screen.refresh_header()
        self.refresh_sidebar()
        if self.keys.username:
            self._start_worker()
            self._ask_notification_permission()
            self._sync_background_service()
            peer = self._launch_peer()
            if peer and self.store.get_contact(peer):
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
            self.manager.transition = NoTransition()
            self.manager.current = "chats"
            self.manager.transition = SlideTransition(duration=0.18)
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
    def open_conversation(self, peer: str) -> None:
        if IS_ANDROID:
            android_clear_notification(peer)
        self.current_peer = peer
        self._chat_anchor = None                # fresh window per chat
        contact = self.store.get_contact(peer) or {}
        if contact.get("auto_read", 1):
            self.mark_peer_read(peer, refresh=False)
        self.manager.transition.direction = "left"
        self.manager.current = "conversation"
        self.refresh_chat(peer)
        self.refresh_sidebar()

    def go_back_to_chats(self) -> None:
        screen = self.conversation_screen
        screen.clear_selection()
        screen.cancel_recording()
        self.voice.stop()
        self.current_peer = None
        self.manager.transition.direction = "right"
        self.manager.current = "chats"
        self.refresh_sidebar()

    # =========================================================================
    #  Rendering
    # =========================================================================
    def refresh_sidebar(self) -> None:
        """Repaint the conversation list (name kept from v2 for continuity)."""
        if self.store is None:
            return
        self.chats_screen.refresh_list(self.store.conversations())

    def refresh_chat(self, peer: Optional[str]) -> None:
        """Repaint the open conversation from the local store.

        Only the most recent CHAT_PAGE_SIZE messages are materialised; older
        ones are paged in on demand. Combined with the incremental renderer
        this makes the cost of an arriving message independent of how long
        the conversation is.
        """
        if peer is None or peer != self.current_peer or self.store is None:
            return
        contact = self.store.get_contact(peer) or {}
        unread = self.store.unread_for(peer)
        self.conversation_screen.set_peer(peer, contact, len(unread))

        if self._chat_anchor is None:            # cold open: newest page
            messages = self.store.messages_for(peer, limit=CHAT_PAGE_SIZE)
            self._chat_anchor = messages[0]["id"] if messages else None
        else:                                    # anchored: list only grows
            messages = self.store.messages_for(peer,
                                               from_id=self._chat_anchor)
        self.conversation_screen.has_older = bool(
            self._chat_anchor is not None
            and self.store.has_messages_before(peer, self._chat_anchor))
        self.conversation_screen.render(
            messages, bool(contact.get("ticks_enabled", 1)),
            self._mark_single_read, peer=peer)
        # Photos/voice notes that arrived while the background service was
        # syncing were never downloaded (it has no screen to show them on);
        # fetch them now that someone is looking.
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
        """Record an outgoing message at 0/3 and send it in the background."""
        if self.store is None:
            return None
        contact = self.store.get_contact(peer)
        if contact is None:
            self._set_status("Unknown contact", COL_DANGER)
            return None
        client_msg_id = uuid.uuid4().hex
        # 0/3 immediately: the grey tick means "not yet on the server".
        local_id = self.store.add_out(peer, body, now_iso(), client_msg_id,
                                      kind=kind, ext=ext or None,
                                      media_path=media_path,
                                      media_state=media_state)
        MessageFlowLog.tick(
            0, None, MessageFlowLog.who(self.keys.address, self.keys.username),
            MessageFlowLog.who(peer, contact.get("username")),
            f"[local id {local_id}, client_msg_id {client_msg_id[:8]}…]")
        if on_local_row is not None:
            on_local_row(local_id, client_msg_id)
        if threading.current_thread() is threading.main_thread():
            if peer == self.current_peer:
                self.refresh_chat(peer)
            self._dirty_list = True
        else:                         # media pipeline: repaint via the queue
            self.ui_queue.put(("message_stored", peer))
        if media_state != "uploading":
            threading.Thread(
                target=self._send_worker,
                args=(local_id, peer, contact["public_key"], body,
                      client_msg_id, x),
                daemon=True).start()
        return local_id

    def _send_worker(self, local_id: int, peer: str, peer_pub: str,
                     body: str, client_msg_id: str,
                     x: Optional[dict] = None) -> bool:
        """SIGN → ENCRYPT → SEND, then record the server receipt (1/3).

        Every failure here has to end at the UI. The previous version caught
        three exception types and let everything else kill the thread — and
        one of the things it let through, sqlite3.IntegrityError from
        mark_sent, was reachable in ordinary use. When it fired the message
        sat at 0/3 forever with no error anywhere, which is precisely the
        class of silent failure this release exists to remove.
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
        if not self.store.mark_sent(local_id, server_id):
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
        self.ui_queue.put(("message_stored", peer))
        return True

    def _send_control(self, peer: str, op: dict, fallback: str) -> bool:
        """Send a control message synchronously (call from a worker thread).

        `fallback` is the body: what a pre-v9 client will display, since it
        cannot act on the instruction. Returns True once the relay has it.
        """
        contact = self.store.get_contact(peer) if self.store else None
        if contact is None:
            return False
        return self._send_worker(None, peer, contact["public_key"], fallback,
                                 uuid.uuid4().hex, {"k": "ctl", "ctl": op})

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
        rows = self.store.messages_by_ids(local_ids)
        sent = 0
        for target in targets:
            for row in rows:
                ext = json.loads(row.get("ext") or "{}")
                media = ext.get("media")
                if row["kind"] in ("image", "voice") and media and \
                        media.get("blob"):
                    self._queue_message(
                        target, row["body"],
                        {"k": "media", "media": media, "fwd": 1}, row["kind"],
                        {"media": media, "fwd": 1},
                        media_path=row.get("media_path"),
                        media_state="ready" if row.get("media_path") else "")
                else:
                    self.send_text(target, row["body"], forwarded=True)
                sent += 1
        self.refresh_sidebar()
        return sent

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
    def send_image(self, path: str, caption: str = "") -> None:
        peer = self.current_peer
        if peer is None:
            return
        reply = self.conversation_screen.take_reply()
        threading.Thread(target=self._prepare_image,
                         args=(peer, path, caption, reply),
                         daemon=True).start()

    def _prepare_image(self, peer, path, caption, reply) -> None:
        try:
            data, width, height, thumb = compress_image(path)
        except Exception as exc:
            log.warning("could not read image %s: %s", path, exc)
            self.ui_queue.put(("status", "That file isn't an image this "
                               "app can read", COL_DANGER))
            return
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
            self._send_media(peer, "voice", data, "audio/mp4", extra,
                             voice_label(duration_ms), reply, "m4a")
        threading.Thread(target=work, daemon=True).start()

    def _send_media(self, peer, kind, data, mime, extra, body, reply,
                    suffix) -> None:
        """Worker thread: keep a local copy, seal, upload, then send the
        reference. The bubble appears at once, marked 'uploading'."""
        if not (self.api is not None and self.api.features().get("blobs")):
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
            media_state="uploading",
            on_local_row=lambda lid, cid: rows.update(id=lid, cid=cid))
        local_id, cid = rows.get("id"), rows.get("cid")
        if local_id is None:
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
        contact = self.store.get_contact(peer) or {}
        x = dict(ext, k="media")
        self._send_worker(local_id, peer, contact.get("public_key", ""),
                          body, cid, x)
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
                suffix = {"image": "jpg", "voice": "m4a"}.get(row["kind"],
                                                             "bin")
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

        def work():
            ok = self._send_control(
                peer, {"op": "resend", "cid": row["client_msg_id"]},
                f"Please send that {what} again")
            if not ok:
                self.store.set_media(local_id, state="expired")
                self.ui_queue.put(("message_stored", peer))
        threading.Thread(target=work, daemon=True).start()

    def _on_resend_request(self, peer: str, cid: str) -> None:
        """They asked for a file the relay no longer has: upload our copy
        again and point their existing message at it (no duplicate)."""
        row = self.store.message_by_cid(peer, cid, "out")
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
            except (OSError, requests.RequestException,
                    ApiClient.ApiError) as exc:
                log.warning("re-upload for %s failed: %s", cid, exc)
                return
            media = dict(ext.get("media") or {}, blob=blob_id, key=key,
                         sha=sha)
            ext["media"] = media
            self.store.set_media(row["id"], ext=ext)
            self._send_control(peer, {"op": "remedia", "cid": cid,
                                      "media": media}, row["body"])
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
                        contact = self.store.get_contact(peer) or {}
                        if contact.get("auto_read", 1) and self.foreground:
                            self.mark_peer_read(peer, refresh=False)
                        self._dirty_chat = True
                    self._dirty_list = True
                elif kind == "incoming":
                    self._notify_incoming(event[1], event[2], event[3])
                elif kind == "media_pending" and self.store is not None:
                    row = self.store.message_by_cid(event[1], event[2], "in")
                    if row is not None:
                        self.fetch_media(row["id"])
                elif kind == "resend_request" and self.store is not None:
                    self._on_resend_request(event[1], event[2])
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
    def _notify_incoming(self, peer: str, body: str, kind: str) -> None:
        """Raise a notification for a new message — unless the user is
        looking at that very chat, or has muted it."""
        if self.store is None:
            return
        if (self.foreground and peer == self.current_peer
                and self.manager.current == "conversation"):
            return
        contact = self.store.get_contact(peer) or {}
        if not contact.get("notify", 1):
            return
        name = contact.get("username") or short_addr(peer, 12)
        preview = body if kind == "text" else (
            "Photo" if kind == "image" else body)
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
        if self.store is not None and self.store.get_contact(peer):
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
            if (self.store.get_contact(peer) or {}).get("auto_read", 1):
                self.mark_peer_read(peer)

    def _set_status(self, text: str, color: str = COL_TEXT_DIM) -> None:
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
            ContactSettingsModal(self, self.current_peer).open()

    def open_chat_info(self) -> None:
        if self.current_peer is None or self.store is None:
            return
        self.chat_info_screen.show(self.current_peer)
        self.manager.transition.direction = "left"
        self.manager.current = "info"

    def close_chat_info(self) -> None:
        self.manager.transition.direction = "right"
        self.manager.current = "conversation"
        self.refresh_chat(self.current_peer)

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
class HeadlessApp:
    """Everything SyncWorker and the media/send helpers need from an app,
    with no UI: the background service's stand-in for FextApp."""

    foreground = False
    current_peer = None

    # The very same code paths the app uses, not copies of them: sending a
    # control, and answering a 'resend' request by uploading the file again.
    _send_worker = FextApp._send_worker
    _send_control = FextApp._send_control
    _on_resend_request = FextApp._on_resend_request

    def __init__(self, keys: "IdentityManager") -> None:
        self.keys = keys
        self.crypto = CryptoEngine(keys)
        self.api = ApiClient(keys)
        self.store = LocalStore(keys.store_path())
        self.ui_queue: "queue.Queue" = queue.Queue()

    def close(self) -> None:
        self.store.close()


def _service_handle(app: HeadlessApp, event: tuple) -> None:
    kind = event[0]
    if kind == "incoming":
        peer, body, msg_kind = event[1], event[2], event[3]
        contact = app.store.get_contact(peer) or {}
        if contact.get("notify", 1):
            name = contact.get("username") or short_addr(peer, 12)
            text = "Photo" if msg_kind == "image" else body
            try:
                android_notify(peer, name, text[:160])
            except Exception:
                log.exception("service: notification failed")
    elif kind == "resend_request":
        app._on_resend_request(event[1], event[2])
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
