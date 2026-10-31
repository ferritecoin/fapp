#!/usr/bin/env python3
"""A release's download page, to host anywhere but GitHub.

    python3 tools/download_page.py Ferritext-15.1.0.apk OUT_DIR \\
        --url https://example.org/ferritext/

GitHub's downloads are not dependable from mainland China, and WeChat
renames an APK sent in a chat so that it will not install. People are sent
this page's link or QR code instead, and open it in the browser. OUT_DIR
receives index.html, one file in Simplified Chinese and English with
nothing fetched from elsewhere, and a copy of the APK beside it. The page
gives the APK's size and SHA-256, a QR code of --url (the page's own
address, where OUT_DIR is served), and the steps to install the app and to
keep it running (Settings › Notifications › Keep Ferritext running).
"""
import argparse
import hashlib
import html
import re
import shutil
from pathlib import Path

import qrcode

ROOT = Path(__file__).resolve().parent.parent


def qr_svg(text: str) -> str:
    """`text` as a QR code: an SVG of dark squares on a light plate."""
    code = qrcode.QRCode(border=2,
                         error_correction=qrcode.constants.ERROR_CORRECT_M)
    code.add_data(text)
    code.make(fit=True)
    rows = code.get_matrix()
    path = "".join(f"M{x} {y}h1v1h-1z" for y, row in enumerate(rows)
                   for x, dark in enumerate(row) if dark)
    n = len(rows)
    return (f'<svg class="qr" viewBox="0 0 {n} {n}" role="img" '
            f'aria-label="QR code: {html.escape(text)}" '
            f'shape-rendering="crispEdges"><rect width="{n}" height="{n}" '
            f'fill="#FFFFF8"/><path d="{path}" fill="#1B1915"/></svg>')


def mark_svg() -> str:
    """The Ferritext mark, in the page's accent."""
    svg = (ROOT / "data" / "ferritext_logo_packet.svg").read_text("utf-8")
    svg = re.sub(r'\s(width|height)="\d+"', "", svg, count=2)
    return svg.replace("<svg ", '<svg class="mark" aria-hidden="true" ', 1)


PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ferritext {version}</title>
<style>
:root {{ --bg: #FFFFF8; --surface: #F7F3E8; --text: #1B1915;
        --text-2: #4E483F; --text-3: #6F685C; --accent: #7F5F18;
        --on-accent: #FFFFF8; --rule: #E2DBCB; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg: #000000; --surface: #0F0E0C; --text: #ECE6D9;
          --text-2: #B3AB9E; --text-3: #9A9285; --accent: #CBA761;
          --on-accent: #14120E; --rule: #33302A; }}
}}
* {{ box-sizing: border-box; }}
a {{ color: var(--accent); }}
body {{ margin: 0; background: var(--bg); color: var(--text);
       font: 17px/1.6 "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei",
             "Source Han Sans SC", system-ui, sans-serif; }}
main {{ max-width: 640px; margin: 0 auto; padding: 40px 16px 56px; }}
.mark {{ width: 64px; height: 64px; fill: var(--accent); }}
h1 {{ font-size: 32px; line-height: 1.2; margin: 8px 0 4px; }}
h2 {{ font-size: 21px; margin: 40px 0 8px; padding-top: 24px;
     border-top: 1px solid var(--rule); }}
.sub {{ color: var(--text-2); margin: 0 0 24px; }}
.get {{ display: block; background: var(--accent); color: var(--on-accent);
       text-decoration: none; text-align: center; font-weight: 600;
       padding: 16px; border-radius: 12px; font-size: 19px; }}
.facts {{ color: var(--text-3); font-size: 14px; margin: 12px 0 0;
         overflow-wrap: anywhere; }}
.facts code {{ font-size: 13px; }}
.scan {{ display: flex; gap: 20px; align-items: center; margin: 28px 0 0;
        padding: 16px; background: var(--surface); border-radius: 12px; }}
.qr {{ width: 132px; height: 132px; flex: none; border-radius: 6px; }}
.scan p {{ margin: 0; color: var(--text-2); }}
ol {{ padding-left: 1.4em; }} li {{ margin: 0 0 10px; }}
.note {{ color: var(--text-3); font-size: 15px; }}
</style>
</head>
<body>
<main>
{mark}
<h1>Ferritext</h1>
<p class="sub">端到端加密通讯 · 版本 {version}</p>
<a class="get" href="{apk}" download>下载 Android 版</a>
<p class="facts">APK · {size} · SHA-256: <code>{sha256}</code></p>
<div class="scan">{qr}
<p>用手机扫描，在浏览器中打开本页。</p></div>
<h2>安装</h2>
<ol>
<li>在浏览器中打开本页。在微信里请点右上角“···”，选择“在浏览器打开”：微信会把 APK 改名，无法安装。</li>
<li>下载后打开 APK，按提示允许浏览器“安装未知应用”。</li>
<li>小米、红米：如提示“纯净模式”，选择“仍然安装”，或在设置中暂时关闭纯净模式。华为、荣耀、OPPO、vivo 会提示风险，选择“继续安装”。</li>
<li>打开 Ferritext，进入“设置 › 通知 › 让 Ferritext 保持运行”，打开其中的设置，以免在后台错过消息。</li>
</ol>
<p class="note">下载后可核对上面的 SHA-256，确认文件完整、未被改动。iPhone 和鸿蒙 NEXT 暂不支持，网页版在计划中。</p>
<section lang="en">
<h2>Ferritext {version} in English</h2>
<p><a href="{apk}" download>Download for Android (APK, {size})</a>. Open this
page in a browser: inside WeChat, tap “···” and “Open in browser”, as WeChat
renames an APK so that it will not install. Allow the browser to install
unknown apps. On Xiaomi and Redmi phones choose “Install anyway” if pure mode
objects; Huawei, Honor, OPPO and vivo warn too: choose to continue. Then open
Settings › Notifications › Keep Ferritext running in the app, so that it is not
stopped in the background.</p>
<p class="note">Check the file against the SHA-256 above. Not yet for iPhone or
HarmonyOS NEXT; a web app is planned.</p>
</section>
</main>
</body>
</html>
"""


def build(apk: Path, out: Path, url: str, version: str = "") -> Path:
    if not version:
        found = re.search(r"\d+\.\d+\.\d+", apk.name)
        if not found:
            raise SystemExit(f"{apk.name} names no version: give --version")
        version = found.group(0)
    data = apk.read_bytes()
    out.mkdir(parents=True, exist_ok=True)
    if apk.resolve() != (out / apk.name).resolve():
        shutil.copyfile(apk, out / apk.name)
    page = PAGE.format(version=html.escape(version), mark=mark_svg(),
                       apk=html.escape(apk.name), qr=qr_svg(url),
                       # decimal, as Android shows the downloaded file
                       size=f"{len(data) / 1e6:.1f}\u00a0MB",
                       sha256=hashlib.sha256(data).hexdigest())
    (out / "index.html").write_text(page, "utf-8")
    return out / "index.html"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("apk", type=Path, help="the release's APK")
    parser.add_argument("out", type=Path, help="the folder to serve")
    parser.add_argument("--url", required=True,
                        help="where the folder will be served (the QR code)")
    parser.add_argument("--version", default="",
                        help="the version, if the APK's name lacks it")
    args = parser.parse_args()
    print("wrote", build(args.apk, args.out, args.url, args.version))


if __name__ == "__main__":
    main()
