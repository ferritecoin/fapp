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
import os
import platform
import queue
import re
import sqlite3
import stat
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
from kivy.graphics import Color, Ellipse, Line, RoundedRectangle, Triangle
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

APP_NAME = "FEXT"

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
        return {"v": 2, "app": APP_NAME, "username": ident.get("username"),
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

    # ---- outgoing ------------------------------------------------------------
    def build_envelope(self, body: str, recipient_pub_hex: str) -> tuple:
        """Returns (envelope, sent_at). `envelope` is the ONLY thing that is
        transmitted; it is opaque ciphertext to everyone but the recipient."""
        sent_at = now_iso()
        sender_address = self._keys.address

        # ---- step 1: SIGN ----------------------------------------------------
        signature = self._keys.sign(
            self._canonical(2, "text", body, sent_at, sender_address))
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
    def open_envelope(self, envelope: dict, claimed_sender: str) -> dict:
        """Decrypt + verify. Returns the trusted inner payload dict."""
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
            ok = ecdsa_verify(
                self._canonical(payload["v"], payload["type"], payload["body"],
                                payload["sent_at"], recomputed),
                b64d(payload["signature"]), sender_point)
            if not ok:
                raise self.EnvelopeError("signature verification failed")
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
    """

    #: Columns added after v7.1 shipped, applied to databases that predate
    #: them. SQLite has no "ADD COLUMN IF NOT EXISTS", so this is done by
    #: inspection rather than by catching the error — an OperationalError is
    #: too blunt an instrument to distinguish "already there" from "broken".
    _MIGRATIONS = (
        ("receipt_sent", (
            "ALTER TABLE messages "
            "ADD COLUMN receipt_sent INTEGER NOT NULL DEFAULT 0",
            # Backfill: history predates the column, so treat it as settled.
            # Without this, upgrading replays a read receipt for every
            # message ever received — and the relay's routing ledger is
            # purged after 7 days, so almost none of them can be confirmed
            # and they would be retried on every sync cycle forever.
            "UPDATE messages SET receipt_sent = 1",
        )),
    )

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
        lock held."""
        have = {r["name"] for r in
                self._db.execute("PRAGMA table_info(messages)").fetchall()}
        for column, statements in self._MIGRATIONS:
            if column in have:
                continue
            log.info("local store: adding messages.%s", column)
            for statement in statements:
                self._db.execute(statement)

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
        assert field in ("ticks_enabled", "auto_read")   # whitelisted columns
        with self._lock:
            self._db.execute(
                f"UPDATE contacts SET {field} = ? WHERE address = ?",
                (int(value), address))
            self._db.commit()

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
                client_msg_id: str) -> int:
        """Insert an outgoing message at 0/3 (grey tick). Returns local id."""
        with self._lock:
            cur = self._db.execute(
                """INSERT INTO messages (client_msg_id, peer, direction, body,
                                         status, read, created_at)
                   VALUES (?, ?, 'out', ?, 0, 1, ?)""",
                (client_msg_id, peer, body, created_at))
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
               unread: bool = True) -> str:
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
                                             created_at, receipt_sent)
                       VALUES (?, ?, ?, 'in', ?, 0, ?, ?, ?)""",
                    (server_id, client_msg_id, peer, body,
                     0 if unread else 1, created_at, 0 if unread else 1))
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
                """SELECT m.peer, c.username, c.ticks_enabled,
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
                """SELECT address AS peer, username, ticks_enabled,
                          NULL AS preview, added_at AS last_at,
                          0 AS unread, 0 AS last_id
                   FROM contacts
                   WHERE address NOT IN (SELECT DISTINCT peer FROM messages)
                   ORDER BY added_at DESC""").fetchall()
        return [dict(r) for r in rows] + [dict(r) for r in contact_only]

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
        who = MessageFlowLog.who(sender)
        fragment = MessageFlowLog.fragment(msg.get("envelope"))

        try:
            payload = self.app.crypto.open_envelope(msg.get("envelope", {}),
                                                    sender)
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

        outcome = self.app.store.add_in(
            peer=sender, body=sanitize_text(str(payload.get("body", ""))),
            created_at=payload.get("sent_at",
                                   msg.get("created_at", now_iso())),
            server_id=server_id, client_msg_id=msg.get("client_msg_id"))

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


class IconButton(ButtonBehavior, Widget):
    """Hand-drawn vector icons ('plus', 'back', 'send') — crisp on every
    platform, no reliance on emoji/glyph coverage in the bundled font."""

    def __init__(self, icon: str, tint=COL_APPBAR_TEXT, bg=None, **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("size", (dp(44), dp(44)))
        super().__init__(**kw)
        self._icon, self._tint = icon, tint
        self._bg_col = paint_circle(self, bg) if isinstance(bg, str) else None
        self._bg_hex = bg
        self.bind(pos=self._redraw, size=self._redraw, state=self._redraw)
        self._redraw()

    def _redraw(self, *_a):
        if self._bg_col is not None:
            self._bg_col.rgba = C(COL_ACCENT_DARK if self.state == "down"
                                  else self._bg_hex)
        self.canvas.after.clear()
        cx, cy = self.center_x, self.center_y
        r = min(self.width, self.height) * 0.22
        with self.canvas.after:
            Color(*C(self._tint))
            if self._icon == "plus":
                Line(points=[cx - r, cy, cx + r, cy], width=dp(1.4),
                     cap="round")
                Line(points=[cx, cy - r, cx, cy + r], width=dp(1.4),
                     cap="round")
            elif self._icon == "back":
                Line(points=[cx + r * 0.6, cy + r, cx - r * 0.6, cy,
                             cx + r * 0.6, cy - r],
                     width=dp(1.6), cap="round", joint="round")
            elif self._icon == "send":
                Triangle(points=[cx - r, cy + r, cx - r, cy - r,
                                 cx + r * 1.2, cy])
                Line(points=[cx - r, cy, cx - r * 0.1, cy], width=dp(1.2),
                     cap="round")


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
        super().__init__("Welcome to FEXT" if first_run else
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
    """Pick a FEXT .json chat export to import (cross-platform file picker)."""

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
                lines = [f"FEXT chat with {name} ({self.peer})",
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
                raise ValueError("not a FEXT chat export")
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
        self._build_identity(body)
        self._build_public(body)
        self._build_private(body)
        self.add(CozyButton("Done", on_release=lambda _w: self.dismiss()))

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
            # U+25CF/U+25CB (black/white circle) are ALSO missing from the
            # bundled font and rendered as boxes. U+2022 BULLET does render;
            # inactive rows are distinguished by colour and weight instead.
            marker = "\u2022  " if active else "     "
            line = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(6))
            row = CozyButton(
                f"{marker}{ident.get('label') or '?'}   "
                f"{short_addr(ident['address'], 10)}",
                height=dp(42), font_size=sp(13.5),
                bg=COL_BUBBLE_ME if active else COL_BG_LIST,
                fg=COL_TEXT if active else COL_TEXT_DIM,
                pressed=COL_PRESSED)
            row.bind(on_release=lambda _w, a=ident["address"]:
                     self._switch(a))
            line.add_widget(row)
            remove = CozyButton("Remove", size_hint_x=None, width=dp(78),
                                height=dp(42), font_size=sp(12),
                                bg=COL_BG_LIST, fg=COL_DANGER,
                                outline=COL_DANGER)
            remove.bind(on_release=lambda _w, a=ident["address"]:
                        self._remove(a))
            line.add_widget(remove)
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


class ConversationRow(ButtonBehavior, BoxLayout):
    """One warm row in the chat list: avatar, name, preview, time, and an
    unread pip in gold."""

    def __init__(self, conv: dict, on_open: Callable[[str], None], **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(74))
        kw.setdefault("padding", [dp(12), dp(10), dp(12), dp(10)])
        kw.setdefault("spacing", dp(12))
        super().__init__(**kw)
        peer = conv["peer"]
        name = conv.get("username") or short_addr(peer, 14)
        unread = int(conv.get("unread") or 0)
        preview = (conv.get("preview") or
                   "Say hello — this chat is end-to-end encrypted")[:70]
        self._bg_col, _ = paint_round(self, COL_CARD, radius=dp(16))
        paint_border(self, COL_CARD_EDGE, radius=dp(16))
        self.bind(state=lambda _w, s: setattr(
            self._bg_col, "rgba", C(COL_PRESSED if s == "down" else COL_CARD)))
        self.bind(on_release=lambda _w: on_open(peer))

        self.add_widget(Avatar(peer, name, diameter=dp(48)))

        middle = BoxLayout(orientation="vertical", spacing=dp(3))
        title = Label(text=name, bold=True, font_size=sp(15),
                      color=C(COL_TEXT), halign="left", valign="middle",
                      size_hint_y=None, height=dp(22), shorten=True,
                      shorten_from="right")
        title.bind(size=lambda w, v: setattr(w, "text_size", v))
        sub = Label(text=preview, font_size=sp(12.5),
                    color=C(COL_TICK_GOLD if unread else COL_TEXT_DIM),
                    halign="left", valign="middle", size_hint_y=None,
                    height=dp(20), shorten=True, shorten_from="right")
        sub.bind(size=lambda w, v: setattr(w, "text_size", v))
        middle.add_widget(title)
        middle.add_widget(sub)
        self.add_widget(middle)

        right = BoxLayout(orientation="vertical", size_hint_x=None,
                          width=dp(50), spacing=dp(4))
        right.add_widget(Label(text=pretty_day(conv.get("last_at") or ""),
                               font_size=sp(11), color=C(COL_TEXT_DIM),
                               size_hint_y=None, height=dp(20)))
        if unread:
            holder = AnchorLayout(anchor_x="right", anchor_y="top")
            pip = AnchorLayout(size_hint=(None, None), size=(dp(24), dp(24)),
                               anchor_x="center", anchor_y="center")
            paint_circle(pip, COL_ACCENT)
            pip.add_widget(Label(text=str(min(unread, 99)), bold=True,
                                 font_size=sp(11), color=C("#FFFFFF")))
            holder.add_widget(pip)
            right.add_widget(holder)
        else:
            right.add_widget(Widget())
        self.add_widget(right)


class ChatsScreen(Screen):
    """The home screen: your identity in the app bar, conversations below."""

    def __init__(self, app: "FextApp", **kw):
        super().__init__(name="chats", **kw)
        self.app = app
        root = BoxLayout(orientation="vertical")
        paint_round(root, COL_BG_LIST, radius=dp(0))
        self.add_widget(root)

        # ---- app bar ---------------------------------------------------------
        bar = BoxLayout(size_hint_y=None, height=dp(64),
                        padding=[dp(12), 0, dp(8), 0], spacing=dp(10))
        paint_round(bar, COL_APPBAR, radius=dp(0))
        self.avatar_holder = AnchorLayout(size_hint_x=None, width=dp(44))
        bar.add_widget(self.avatar_holder)

        titles = BoxLayout(orientation="vertical")
        brand = Label(text=APP_NAME, bold=True, font_size=sp(19),
                      color=C(COL_APPBAR_TEXT), halign="left",
                      valign="bottom", size_hint_y=None, height=dp(26))
        brand.bind(size=lambda w, v: setattr(w, "text_size", v))
        self.me_label = Label(text="", font_size=sp(11.5),
                              color=C(COL_APPBAR_DIM), halign="left",
                              valign="top", size_hint_y=None, height=dp(20),
                              shorten=True, shorten_from="right")
        self.me_label.bind(size=lambda w, v: setattr(w, "text_size", v))
        titles.add_widget(brand)
        titles.add_widget(self.me_label)
        bar.add_widget(titles)
        bar.add_widget(IconButton("plus", bg=COL_ACCENT, tint="#FFFFFF",
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
                              padding=[dp(10), dp(10), dp(10), dp(16)],
                              spacing=dp(8))
        self.list.bind(minimum_height=self.list.setter("height"))
        self.scroll.add_widget(self.list)
        root.add_widget(self.scroll)

    def refresh_header(self) -> None:
        keys = self.app.keys
        self.avatar_holder.clear_widgets()
        if not keys.has_identity:
            return
        avatar = AvatarButton(keys.address, keys.username or keys.label,
                              diameter=dp(42))
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
class MessageBubble(ButtonBehavior, BoxLayout):
    """One message. Outgoing = gold, right-aligned, carrying the tick tier.
    Incoming = white, left-aligned; tapping an unread one marks it read."""

    def __init__(self, msg: dict, ticks_enabled: bool,
                 on_tap: Optional[Callable] = None, **kw):
        kw.setdefault("orientation", "vertical")
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("padding", [dp(12), dp(8), dp(12), dp(6)])
        kw.setdefault("spacing", dp(3))
        super().__init__(**kw)
        self._last_width = 0.0            # remembered for in-place updates
        outgoing = msg["direction"] == "out"
        # Tail corner points at the speaker — the small cue that makes a
        # chat feel like a chat.
        corners = ([dp(16), dp(16), dp(4), dp(16)] if outgoing
                   else [dp(16), dp(16), dp(16), dp(4)])
        paint_round(self, COL_BUBBLE_ME if outgoing else COL_BUBBLE_PEER,
                    corners=corners)
        if not outgoing:
            paint_border(self, COL_CARD_EDGE, radius=dp(16))

        self.body = Label(text=msg["body"], color=C(COL_TEXT),
                          font_size=sp(14.5), halign="left", valign="top",
                          size_hint=(None, None))
        self.add_widget(self.body)

        # Meta line = timestamp + vector ticks. Kept as two widgets rather
        # than one markup Label so the ticks can be DRAWN (see TickMarks).
        self.meta = Label(text=self._stamp_text(msg), markup=True,
                          font_size=sp(10.5), color=C(COL_TEXT_DIM),
                          halign="right", valign="middle",
                          size_hint=(None, None), height=dp(14))
        count, tint = self._tick_state(msg, outgoing, ticks_enabled)
        self.ticks = TickMarks(count=count, tint=tint)
        self.meta_box = BoxLayout(orientation="horizontal", spacing=dp(4),
                                  size_hint=(None, None), height=dp(14))
        self.meta_box.add_widget(self.meta)
        self.meta_box.add_widget(self.ticks)
        meta_row = AnchorLayout(anchor_x="right", size_hint_y=None,
                                height=dp(14))
        meta_row.add_widget(self.meta_box)
        self.add_widget(meta_row)

        if on_tap is not None:
            self.bind(on_release=lambda _w: on_tap())

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
        """Repaint in place after a status/read/body change.

        A tick advancing 1/3 -> 2/3 -> 3/3 used to rebuild the entire
        conversation; it now rewrites one short label. Only when the body
        text itself changes does the bubble need re-wrapping.
        """
        outgoing = msg["direction"] == "out"
        self.meta.text = self._stamp_text(msg)
        self.ticks.set_state(*self._tick_state(msg, outgoing, ticks_enabled))
        if self.body.text != msg["body"]:
            self.body.text = msg["body"]
        if self._last_width:
            self.layout_for(self._last_width)
        else:
            self.meta.texture_update()
            self.meta.size = (self.meta.texture_size[0], dp(14))
            self.meta_box.width = (self.meta.width + self.ticks.width
                                   + (dp(4) if self.ticks.width else 0))

    def layout_for(self, available: float) -> None:
        """Wrap to at most 76% of the viewport, then size to content."""
        self._last_width = available
        max_text = max(dp(120), available * 0.76 - dp(24))
        self.body.text_size = (max_text, None)
        self.body.texture_update()
        self.body.size = self.body.texture_size
        self.meta.texture_update()
        self.meta.size = (self.meta.texture_size[0], dp(14))
        # Composite meta row: timestamp + drawn ticks + the gap between them.
        meta_w = self.meta.width + self.ticks.width
        if self.ticks.width:
            meta_w += dp(4)
        self.meta_box.width = meta_w
        content = max(self.body.width, meta_w)
        self.width = content + dp(24)
        self.height = self.body.height + dp(14) + dp(20)


class BubbleRow(AnchorLayout):
    """Aligns a bubble left (incoming) or right (outgoing) and tracks its
    wrapped height."""

    def __init__(self, bubble: MessageBubble, outgoing: bool, **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(48))
        kw.setdefault("padding", [dp(10), dp(3), dp(10), dp(3)])
        super().__init__(anchor_x="right" if outgoing else "left",
                         anchor_y="center", **kw)
        self.bubble = bubble
        self.add_widget(bubble)
        bubble.bind(height=lambda _w, v: setattr(self, "height", v + dp(6)))
        self.bind(width=lambda _w, v: bubble.layout_for(v))


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
        root = BoxLayout(orientation="vertical")
        paint_round(root, COL_BG, radius=dp(0))
        self.add_widget(root)

        # ---- app bar ---------------------------------------------------------
        self.bar = BoxLayout(size_hint_y=None, height=dp(64),
                             padding=[dp(4), 0, dp(10), 0], spacing=dp(6))
        paint_round(self.bar, COL_APPBAR, radius=dp(0))
        self.bar.add_widget(IconButton("back", on_release=lambda _w:
                                       self.app.go_back_to_chats()))
        self.peer_avatar_holder = AnchorLayout(size_hint_x=None, width=dp(42))
        self.bar.add_widget(self.peer_avatar_holder)

        titles = TappableBox(on_tap=lambda: self.app.open_contact_settings())
        self.peer_name = Label(text="", bold=True, font_size=sp(16),
                               color=C(COL_APPBAR_TEXT), halign="left",
                               valign="bottom", size_hint_y=None,
                               height=dp(24), shorten=True,
                               shorten_from="right")
        self.peer_name.bind(size=lambda w, v: setattr(w, "text_size", v))
        self.peer_sub = Label(text="", font_size=sp(11),
                              color=C(COL_APPBAR_DIM), halign="left",
                              valign="top", size_hint_y=None, height=dp(20),
                              shorten=True, shorten_from="right")
        self.peer_sub.bind(size=lambda w, v: setattr(w, "text_size", v))
        titles.add_widget(self.peer_name)
        titles.add_widget(self.peer_sub)
        self.bar.add_widget(titles)
        self.mark_read_button = CozyButton(
            "Mark read", size_hint=(None, None), width=dp(92), height=dp(36),
            font_size=sp(12), on_release=lambda _w:
            self.app.mark_peer_read(self.app.current_peer))
        root.add_widget(self.bar)

        # ---- messages --------------------------------------------------------
        stack = FloatLayout()
        self.scroll = ScrollView(bar_width=dp(3), size_hint=(1, 1),
                                 pos_hint={"x": 0, "y": 0})
        self.messages = BoxLayout(orientation="vertical", size_hint_y=None,
                                  padding=[0, dp(10), 0, dp(10)],
                                  spacing=dp(2))
        self.messages.bind(minimum_height=self.messages.setter("height"))
        self.messages.bind(height=lambda *_a: self._on_content_height())
        self.scroll.add_widget(self.messages)
        stack.add_widget(self.scroll)
        self.jump_button = JumpToBottomButton(on_tap=self.jump_to_latest,
                                              pos_hint={"right": 0.965,
                                                        "y": 0.02})
        stack.add_widget(self.jump_button)
        self.scroll.bind(scroll_y=lambda *_a: self._on_scrolled())
        root.add_widget(stack)

        # ---- composer --------------------------------------------------------
        composer = BoxLayout(size_hint_y=None, height=dp(66),
                             padding=[dp(10), dp(10), dp(10), dp(10)],
                             spacing=dp(8))
        paint_round(composer, COL_BG_LIST, radius=dp(0))
        self.entry = CozyInput(hint="Message", radius=dp(22),
                               on_enter=self.app.send_current)
        composer.add_widget(self.entry)
        composer.add_widget(IconButton("send", bg=COL_ACCENT, tint="#FFFFFF",
                                       size=(dp(46), dp(46)),
                                       on_release=lambda _w:
                                       self.app.send_current()))
        root.add_widget(composer)

    # ---- header --------------------------------------------------------------
    def set_peer(self, peer: str, contact: dict, unread_count: int) -> None:
        name = contact.get("username") or short_addr(peer, 14)
        self.peer_avatar_holder.clear_widgets()
        self.peer_avatar_holder.add_widget(Avatar(peer, name, diameter=dp(40)))
        self.peer_name.text = name
        ticks = "ticks on" if contact.get("ticks_enabled", 1) else "no ticks"
        auto = "auto-read" if contact.get("auto_read", 1) else "manual read"
        self.peer_sub.text = f"{short_addr(peer, 12)}  ·  {ticks} · {auto}"
        # "Mark read" only appears when it can actually do something.
        manual = unread_count and not contact.get("auto_read", 1)
        if manual and self.mark_read_button.parent is None:
            self.bar.add_widget(self.mark_read_button)
        elif not manual and self.mark_read_button.parent is not None:
            self.bar.remove_widget(self.mark_read_button)

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
        signatures = {m["id"]: (m["status"], m["read"], m["body"])
                      for m in messages}
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
        self._bubbles.clear()
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
        self._rendered_sigs = {m["id"]: (m["status"], m["read"], m["body"])
                               for m in messages}
        self._rendered_peer = peer
        self._rendered_ticks = ticks_enabled
        self._unseen = 0
        self.scroll_to_bottom(animate=False)

    def _append_bubble(self, msg: dict, ticks_enabled: bool,
                       on_tap_unread: Callable) -> None:
        day = str(msg.get("created_at", ""))[:10]
        if day and day != self._last_day:
            self._last_day = day
            self.messages.add_widget(DayDivider(self._day_text(day)))
        outgoing = msg["direction"] == "out"
        tap = None
        if not outgoing and not msg["read"]:
            tap = (lambda m=msg: on_tap_unread(m["id"], m.get("server_id")))
        bubble = MessageBubble(msg, ticks_enabled, on_tap=tap)
        self._bubbles[msg["id"]] = bubble
        self.messages.add_widget(BubbleRow(bubble, outgoing))

    # ---- scroll position -----------------------------------------------------
    BOTTOM_EPSILON = 0.02        # within 2% of the end counts as "at bottom"

    def at_bottom(self) -> bool:
        """True when the latest message is already on screen.

        A ScrollView whose content is shorter than the viewport reports
        scroll_y == 1, which is simultaneously the top AND the bottom; treat
        that as at-bottom or short chats would show a pointless jump pill.
        """
        if self.messages.height <= self.scroll.height:
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
            if not animate or self.messages.height <= self.scroll.height:
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
class FextApp(App):
    """Owns the identity lifecycle, the two screens and the UI-queue pump.

    Threading contract (unchanged from v2): ALL network + crypto work happens
    off the UI thread. Workers never touch widgets directly — they post to
    `ui_queue`, which is drained on the Kivy main thread by `_drain_ui_queue`
    (the Clock analogue of the old tkinter `after()` loop).
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.title = f"{APP_NAME} — end-to-end encrypted messenger"
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
        self.manager.add_widget(self.chats_screen)
        self.manager.add_widget(self.conversation_screen)
        Window.bind(on_keyboard=self._on_keyboard)     # Android back button
        Clock.schedule_interval(self._drain_ui_queue, 0.15)
        Clock.schedule_once(self._post_build, 0.05)
        return self.manager

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

    def _post_build(self, _dt) -> None:
        self._probe_transport()
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
        """Android/Esc back: leave the conversation before leaving the app."""
        if key in (27, 1001):
            if self.manager.current == "conversation":
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
        else:
            self._set_status("Pick a username to start chatting",
                             COL_TICK_GOLD)
            RegisterModal(self).open()

    def _start_worker(self) -> None:
        self.worker = SyncWorker(self)
        self.worker.start()
        self._set_status("Connecting…", COL_TEXT_DIM)

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
        if self.store is None or self.current_peer is None:
            return
        body = sanitize_text(self.conversation_screen.entry.text)
        if not body:
            return
        contact = self.store.get_contact(self.current_peer)
        if contact is None:
            self._set_status("Unknown contact", COL_DANGER)
            return
        self.conversation_screen.entry.text = ""
        client_msg_id = uuid.uuid4().hex
        # 0/3 immediately: the grey tick means "not yet on the server".
        local_id = self.store.add_out(self.current_peer, body, now_iso(),
                                      client_msg_id)
        MessageFlowLog.tick(
            0, None, MessageFlowLog.who(self.keys.address, self.keys.username),
            MessageFlowLog.who(self.current_peer, contact.get("username")),
            f"[local id {local_id}, client_msg_id {client_msg_id[:8]}…]")
        self.refresh_chat(self.current_peer)
        threading.Thread(
            target=self._send_worker,
            args=(local_id, self.current_peer, contact["public_key"], body,
                  client_msg_id),
            daemon=True).start()

    def _send_worker(self, local_id: int, peer: str, peer_pub: str,
                     body: str, client_msg_id: str) -> None:
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
            envelope, _sent_at = self.crypto.build_envelope(body, peer_pub)
        except Exception as exc:
            log.exception("could not build envelope for %s", peer)
            MessageFlowLog.failed(me, them, f"encryption failed: {exc}")
            self.ui_queue.put(("status", f"Could not encrypt: {exc}",
                               COL_DANGER))
            return

        fragment = MessageFlowLog.fragment(envelope)
        try:
            result = self.api.send_message(peer, envelope, client_msg_id)
        except (requests.RequestException, ApiClient.ApiError,
                ValueError) as exc:
            MessageFlowLog.failed(me, them, str(exc), fragment)
            self.ui_queue.put(("status", f"Send failed: {exc}", COL_DANGER))
            return
        except Exception as exc:                     # never lose the thread
            log.exception("unexpected error sending to %s", peer)
            MessageFlowLog.failed(me, them, f"unexpected: {exc}", fragment)
            self.ui_queue.put(("status", f"Send failed: {exc}", COL_DANGER))
            return

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
            return
        MessageFlowLog.tick(1, server_id, me, them, fragment)
        self.ui_queue.put(("message_stored", peer))

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
                    if peer == self.current_peer:
                        contact = self.store.get_contact(peer) or {}
                        if contact.get("auto_read", 1):
                            self.mark_peer_read(peer, refresh=False)
                        self._dirty_chat = True
                    self._dirty_list = True
        except queue.Empty:
            pass
        if self._dirty_chat:
            self._dirty_chat = False
            self.refresh_chat(self.current_peer)
        if self._dirty_list:
            self._dirty_list = False
            self.refresh_sidebar()

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


# =============================================================================
#  Entrypoint
# =============================================================================
def main() -> None:
    FextApp().run()


if __name__ == "__main__":
    main()
