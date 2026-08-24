"""Quick smoke test for the WebScanner server endpoints."""
import ssl
import urllib.request
import urllib.error
import json
import base64

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

OPENER = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=CTX),
    urllib.request.HTTPRedirectHandler(),
)

BASE = "https://localhost:8443"


def get(path):
    r = OPENER.open(BASE + path)
    return r.status, r.read()


def post(path, payload):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    r = OPENER.open(req)
    return r.status, r.read()


# 1) index page
status, html = get("/")
print("INDEX status:", status)
assert status == 200
assert "getUserMedia" in html.decode()
assert 'value="A4"' in html.decode()
print("  -> has camera JS + A4 selector: OK")

# 2) HTTP -> HTTPS redirect
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

no_redir = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=CTX), NoRedirect())
try:
    no_redir.open("http://localhost:8080/", timeout=5)
    print("HTTP redirect: no redirect (unexpected)")
except urllib.error.HTTPError as e:
    print("HTTP redirect status:", e.code, "->", e.headers.get("Location"))
    assert e.code == 301
    assert e.headers.get("Location", "").startswith("https://")
    print("  -> redirects to HTTPS: OK")

# 3) build a fake tilted document image and test detect + process + pdf
import cv2
import numpy as np
import io

img = np.full((900, 1200, 3), 90, dtype=np.uint8)
pts = np.array([[260, 180], [1020, 240], [960, 780], [200, 720]], dtype=np.int32)
cv2.fillConvexPoly(img, pts, (255, 255, 255))
cv2.putText(img, "WebScanner Test Document", (330, 400),
            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (20, 20, 20), 2)
ok, buf = cv2.imencode(".jpg", img)
data_url = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()

status, body = post("/api/detect", {"image": data_url})
det = json.loads(body)
print("DETECT status:", status, "| corners:", [[round(c[0], 3), round(c[1], 3)] for c in det["corners"]])
assert det["ok"] and len(det["corners"]) == 4
print("  -> edge detection: OK")

status, body = post("/api/process", {
    "image": data_url,
    "corners": det["corners"],
    "src_w": 1200, "src_h": 900,
    "rotate": 0,
    "filter": "bw",
    "brightness": 0, "contrast": 1.0,
})
proc = json.loads(body)
print("PROCESS status:", status, "| output len:", len(proc["image"]))
assert proc["ok"] and proc["image"].startswith("data:image/jpeg")
print("  -> process + bw filter: OK")

# 4) pdf export with two pages
status, body = post("/api/export/pdf", {
    "pages": [proc["image"], proc["image"]],
    "page_size": "A4",
})
print("PDF status:", status, "| bytes:", len(body))
assert body[:4] == b"%PDF"
print("  -> multi-page A4 PDF: OK")

print("ALL SERVER SMOKE TESTS PASSED")
