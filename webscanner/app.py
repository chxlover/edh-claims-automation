"""app.py

WebScanner — CamScanner-like web app for phone browsers.

Run (from this folder):
    ..\\.venv\\Scripts\\python.exe app.py

Opens an HTTPS server (self-signed cert generated on first run) plus an
HTTP server that redirects to HTTPS. Camera capture in the phone browser
requires HTTPS, hence the self-signed certificate.

Endpoints:
    GET  /                    mobile web UI
    POST /api/detect          auto document edge detection
    POST /api/process         perspective correction + filter
    POST /api/export/pdf      multi-page PDF export (A4 default)
"""

from __future__ import annotations

import argparse
import base64
import io
import ipaddress
import json
import socket
import ssl
import threading
import webbrowser
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request, send_file, render_template

import scanner_engine as engine

BASE_DIR = Path(__file__).resolve().parent
CERT_DIR = BASE_DIR / "certs"
CERT_FILE = CERT_DIR / "server.crt"
KEY_FILE = CERT_DIR / "server.key"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64 MB uploads


# ---------------------------------------------------------------- helpers


def _json_image() -> dict:
    data = request.get_json(force=True)
    if not data or not data.get("image"):
        raise ValueError("Missing 'image' field")
    return data


def _get_lan_ips() -> list[str]:
    ips: set[str] = set()
    try:
        host = socket.gethostbyname_ex(socket.gethostname())[2]
        ips.update(host)
    except OSError:
        pass
    # best-effort: derive from a UDP socket to a public address
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    out = [ip for ip in ips if ip.startswith(("192.168.", "10.", "172."))]
    return sorted(out) or sorted(ips)


# ---------------------------------------------------------------- routes


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/detect", methods=["POST"])
def api_detect():
    try:
        data = _json_image()
        img = engine.decode_image(data["image"])
        corners = engine.detect_document(img)
        h, w = img.shape[:2]
        return jsonify({"ok": True, "corners": corners, "width": w, "height": h})
    except Exception as exc:  # noqa: BLE001 - report to client
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/process", methods=["POST"])
def api_process():
    try:
        data = _json_image()
        img = engine.decode_image(data["image"])
        src_h, src_w = img.shape[:2]
        corners = data.get("corners")
        if not corners:
            corners = engine.detect_document(img)
        img = engine.rotate_image(img, int(data.get("rotate", 0)))
        corners = engine.rotate_corners(
            corners, data.get("src_w", src_w), data.get("src_h", src_h),
            int(data.get("rotate", 0)))
        img = engine.warp_perspective(img, corners)
        img = engine.apply_filter(
            img,
            name=data.get("filter", "colored"),
            brightness=float(data.get("brightness", 0)),
            contrast=float(data.get("contrast", 1)),
        )
        fmt = data.get("format", "jpeg")
        quality = int(data.get("quality", 92))
        out = engine.encode_image(img, fmt=fmt, quality=quality)
        return jsonify({"ok": True, "image": out})
    except Exception as exc:  # noqa: BLE001 - report to client
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/export/pdf", methods=["POST"])
def api_export_pdf():
    try:
        data = request.get_json(force=True)
        pages = data.get("pages") or []
        if not pages:
            return jsonify({"ok": False, "error": "No pages to export"}), 400
        images = [engine.decode_image(p) for p in pages]
        page_size = data.get("page_size", "A4")
        pdf_bytes = engine.images_to_pdf(images, page_size=page_size)
        buf = io.BytesIO(pdf_bytes)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return send_file(
            buf, mimetype="application/pdf", as_attachment=True,
            download_name=f"webscan_{stamp}.pdf")
    except Exception as exc:  # noqa: BLE001 - report to client
        return jsonify({"ok": False, "error": str(exc)}), 400


# ---------------------------------------------------------------- TLS cert


def _ensure_cert() -> None:
    """Generate a self-signed certificate valid for LAN IPs + localhost."""
    CERT_DIR.mkdir(exist_ok=True)
    if CERT_FILE.exists() and KEY_FILE.exists():
        return

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, u"webscanner.local"),
    ])
    alt_names = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    for ip in _get_lan_ips():
        try:
            alt_names.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .sign(key, hashes.SHA256())
    )

    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    KEY_FILE.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))


# ---------------------------------------------------------------- serve


def _serve_https(port: int) -> None:
    _ensure_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT_FILE, KEY_FILE)
    app.run(host="0.0.0.0", port=port, ssl_context=ctx, threaded=True,
            debug=False, use_reloader=False)


def _serve_http_redirect(port: int, https_port: int) -> None:
    """Redirect plain-HTTP visitors to the HTTPS port (camera needs TLS)."""
    from werkzeug.serving import make_server

    class _Redirect:
        def __call__(self, environ, start_response):
            host = environ.get("HTTP_HOST", f"localhost:{port}").split(":")[0]
            target = f"https://{host}:{https_port}{environ.get('PATH_INFO', '/')}"
            start_response("301 Moved Permanently",
                           [("Location", target), ("Content-Length", "0")])
            return []

    make_server("0.0.0.0", port, _Redirect(), threaded=True).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="WebScanner server")
    parser.add_argument("--port", type=int, default=8443, help="HTTPS port")
    parser.add_argument("--http-port", type=int, default=8080,
                        help="HTTP redirect port")
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not open the browser")
    args = parser.parse_args()

    _ensure_cert()
    ips = _get_lan_ips() or ["localhost"]
    print("=" * 56)
    print("  WebScanner  (CamScanner-like, runs in phone browser)")
    print("=" * 56)
    print("  Open on your phone (same Wi-Fi):")
    for ip in ips:
        print(f"    https://{ip}:{args.port}")
    print("  Or on this PC:")
    print(f"    https://localhost:{args.port}")
    print("  Camera needs HTTPS. First visit shows a cert warning:")
    print("    tap Advanced -> Proceed anyway.")
    print("  Press Ctrl+C to stop.")
    print("=" * 56)

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(
            f"https://localhost:{args.port}")).start()

    redirect = threading.Thread(
        target=_serve_http_redirect,
        args=(args.http_port, args.port),
        daemon=True,
    )
    redirect.start()
    _serve_https(args.port)


if __name__ == "__main__":
    main()
