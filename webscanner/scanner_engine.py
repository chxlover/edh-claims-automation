"""scanner_engine.py

WebScanner processing engine (independent module).

Handles: document edge detection, perspective correction, filters
(colored / enhanced / greyscale / black & white), rotation, and
multi-page PDF export on an A4 (default) page.

Standalone test:  python scanner_engine.py
"""

from __future__ import annotations

import base64
import io
import math

import cv2
import numpy as np
from PIL import Image
from reportlab.lib.pagesizes import A4, A5, LETTER, LEGAL
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdf_canvas

# ---------------------------------------------------------------- constants

MAX_DETECT_SIDE = 1600   # downscale for edge detection (speed)
MAX_OUTPUT_SIDE = 2500   # cap for warped output (quality / size balance)

PAGE_SIZES = {
    "A4": A4,
    "A5": A5,
    "Letter": LETTER,
    "Legal": LEGAL,
}

FILTERS = ("colored", "enhanced", "greyscale", "bw")

# ---------------------------------------------------------------- helpers


def decode_image(data_url: str) -> np.ndarray:
    """Decode a base64 data URL into an OpenCV BGR image."""
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    raw = base64.b64decode(data_url)
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Cannot decode image data")
    return img


def encode_image(img: np.ndarray, fmt: str = "jpeg", quality: int = 92) -> str:
    """Encode an OpenCV BGR image to a base64 data URL."""
    ext = ".jpg" if fmt == "jpeg" else ".png"
    ok, buf = cv2.imencode(ext, img, _encode_params(fmt, quality))
    if not ok:
        raise ValueError("Cannot encode image")
    mime = "image/jpeg" if fmt == "jpeg" else "image/png"
    return f"data:{mime};base64," + base64.b64encode(buf.tobytes()).decode("ascii")


def _encode_params(fmt: str, quality: int) -> list[int]:
    if fmt == "jpeg":
        return [int(cv2.IMWRITE_JPEG_QUALITY), max(0, min(100, quality))]
    return [int(cv2.IMWRITE_PNG_COMPRESSION), 6]


def _order_corners(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as TL, TR, BR, BL."""
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).reshape(-1)
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(d)]
    bl = pts[np.argmax(d)]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def _rotate_point(p: np.ndarray, center: tuple[float, float], deg: int) -> np.ndarray:
    """Rotate a point around a center by a multiple of 90 degrees (CW)."""
    deg = deg % 360
    if deg == 0:
        return p
    rad = math.radians(deg)
    x, y = float(p[0]), float(p[1])
    cx, cy = center
    x -= cx
    y -= cy
    rx = x * math.cos(rad) + y * math.sin(rad)
    ry = -x * math.sin(rad) + y * math.cos(rad)
    return np.array([rx + cx, ry + cy], dtype=np.float32)


# ---------------------------------------------------------------- detection


def detect_document(img_bgr: np.ndarray) -> list[list[float]]:
    """Detect the largest quadrilateral document.

    Returns 4 normalized corners [[x,y]x4] in [0..1] relative to the
    original image. Falls back to the full image frame when no clean
    quad is found.
    """
    h, w = img_bgr.shape[:2]
    scale = min(1.0, MAX_DETECT_SIDE / max(h, w))
    small = cv2.resize(img_bgr, (int(w * scale), int(h * scale)),
                       interpolation=cv2.INTER_AREA) if scale < 1.0 else img_bgr

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 60, 200)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    edges = cv2.dilate(edges, kernel, iterations=2)

    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    best: np.ndarray | None = None
    best_area = 0.0
    total = float(small.shape[0] * small.shape[1])

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < total * 0.10:  # ignore tiny blobs
            continue
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            if area > best_area:
                best_area = area
                best = approx.reshape(4, 2)

    if best is None:
        # No quad found -> use the whole frame
        return [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]

    best = best / np.array([small.shape[1], small.shape[0]], dtype=np.float32)
    ordered = _order_corners(best)
    return ordered.tolist()


# ---------------------------------------------------------------- transform


def warp_perspective(img_bgr: np.ndarray,
                     corners: list[list[float]]) -> np.ndarray:
    """Apply perspective correction using normalized corners (TL,TR,BR,BL)."""
    h, w = img_bgr.shape[:2]
    pts = np.array(corners, dtype=np.float32) * np.array(
        [w, h], dtype=np.float32)
    pts = _order_corners(pts)

    (tl, tr, br, bl) = pts
    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    max_w = int(max(width_a, width_b))
    max_h = int(max(height_a, height_b))

    # Keep aspect, cap output size
    cap = MAX_OUTPUT_SIDE / max(max_w, max_h)
    if cap < 1.0:
        max_w = int(max_w * cap)
        max_h = int(max_h * cap)

    dst = np.array([[0, 0], [max_w - 1, 0], [max_w - 1, max_h - 1],
                    [0, max_h - 1]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(pts, dst)
    warped = cv2.warpPerspective(img_bgr, matrix, (max_w, max_h),
                                 flags=cv2.INTER_CUBIC,
                                 borderMode=cv2.BORDER_REPLICATE)
    return warped


def rotate_image(img_bgr: np.ndarray, deg: int) -> np.ndarray:
    """Rotate by multiples of 90 degrees (CW)."""
    deg = deg % 360
    if deg == 0:
        return img_bgr
    if deg == 90:
        return cv2.rotate(img_bgr, cv2.ROTATE_90_CLOCKWISE)
    if deg == 180:
        return cv2.rotate(img_bgr, cv2.ROTATE_180)
    if deg == 270:
        return cv2.rotate(img_bgr, cv2.ROTATE_90_COUNTERCLOCKWISE)
    raise ValueError("Rotation must be a multiple of 90")


def rotate_corners(corners: list[list[float]], img_w: int, img_h: int,
                   deg: int) -> list[list[float]]:
    """Rotate normalized corners along with the image (multiples of 90)."""
    deg = deg % 360
    if deg == 0:
        return [list(c) for c in corners]
    center = (img_w / 2.0, img_h / 2.0)
    pts = [np.array([c[0] * img_w, c[1] * img_h], dtype=np.float32)
           for c in corners]
    rot = [_rotate_point(p, center, deg) for p in pts]

    def out_size(d: int) -> tuple[int, int]:
        return (img_h, img_w) if d % 180 == 90 else (img_w, img_h)

    nw, nh = out_size(deg)
    norm = [[float(p[0]) / nw, float(p[1]) / nh] for p in rot]
    return _order_corners(np.array(norm, dtype=np.float32)).tolist()


# ---------------------------------------------------------------- filters


def _adjust_brightness_contrast(img: np.ndarray, brightness: float,
                                contrast: float) -> np.ndarray:
    if brightness == 0 and contrast == 1.0:
        return img
    out = img.astype(np.float32)
    out = out * contrast + brightness
    return np.clip(out, 0, 255).astype(np.uint8)


def _magic_color(img: np.ndarray) -> np.ndarray:
    """CLAHE on lightness + mild saturation + unsharp (CamScanner-like)."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    lab = cv2.merge((l, a, b))
    out = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    s = np.clip(s.astype(np.float32) * 1.15, 0, 255).astype(np.uint8)
    out = cv2.cvtColor(cv2.merge((h, s, v)), cv2.COLOR_HSV2BGR)

    blur = cv2.GaussianBlur(out, (0, 0), 1.5)
    return cv2.addWeighted(out, 1.25, blur, -0.25, 0)


def _black_white(img: np.ndarray) -> np.ndarray:
    """Adaptive threshold binarization tuned for scanned documents."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    h, w = gray.shape
    block = int(min(h, w) / 60) | 1
    block = max(11, min(127, block))
    if block % 2 == 0:
        block += 1
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY, block, 10)
    return cv2.cvtColor(bw, cv2.COLOR_GRAY2BGR)


def apply_filter(img_bgr: np.ndarray, name: str = "colored",
                 brightness: float = 0.0, contrast: float = 1.0) -> np.ndarray:
    """Apply the named filter plus optional brightness/contrast."""
    if name == "enhanced":
        out = _magic_color(img_bgr)
    elif name == "greyscale":
        out = cv2.cvtColor(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY),
                           cv2.COLOR_GRAY2BGR)
    elif name == "bw":
        out = _black_white(img_bgr)
    else:  # colored
        out = img_bgr.copy()
    return _adjust_brightness_contrast(out, brightness, contrast)


# ---------------------------------------------------------------- pdf export


def images_to_pdf(images: list[np.ndarray], page_size: str = "A4",
                  margin_mm: float = 4.0) -> bytes:
    """Build a PDF from a list of BGR images fitted on `page_size`.

    A4 is the default page size. Each image is scaled to fit inside the
    page with the given margin, centered, aspect preserved.
    """
    if page_size not in PAGE_SIZES:
        page_size = "A4"
    w_pt, h_pt = PAGE_SIZES[page_size]
    buf = io.BytesIO()
    pdf = pdf_canvas.Canvas(buf, pagesize=(w_pt, h_pt))
    margin = margin_mm * 72.0 / 25.4

    for img in images:
        pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        iw, ih = pil.size
        avail_w = w_pt - 2 * margin
        avail_h = h_pt - 2 * margin
        scale = min(avail_w / iw, avail_h / ih, 1.0)
        dw, dh = iw * scale, ih * scale
        x = (w_pt - dw) / 2.0
        y = (h_pt - dh) / 2.0
        pdf.drawImage(ImageReader(pil), x, y, width=dw, height=dh)
        pdf.showPage()

    pdf.save()
    return buf.getvalue()


# ---------------------------------------------------------------- main (test)


def _make_test_document() -> np.ndarray:
    """Draw a fake tilted document sheet on a darker background."""
    img = np.full((900, 1200, 3), 90, dtype=np.uint8)
    pts = np.array([[260, 180], [1020, 240], [960, 780], [200, 720]],
                   dtype=np.int32)
    cv2.fillConvexPoly(img, pts, (255, 255, 255))
    cv2.putText(img, "WebScanner Test Document", (330, 400),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (20, 20, 20), 2)
    for i in range(6):
        y = 460 + i * 50
        cv2.line(img, (300, y), (940, y), (120, 120, 120), 2)
    return img


if __name__ == "__main__":
    print("== WebScanner engine self-test ==")
    doc = _make_test_document()
    print("created test document:", doc.shape)

    corners = detect_document(doc)
    print("detected corners:", [[round(c[0], 3), round(c[1], 3)] for c in corners])

    warped = warp_perspective(doc, corners)
    print("warped size:", warped.shape)

    for f in FILTERS:
        out = apply_filter(warped, f)
        print(f"filter '{f}' ok ->", out.shape, out.dtype)

    rot = rotate_image(warped, 90)
    print("rotate 90 ok ->", rot.shape)
    rc = rotate_corners(corners, doc.shape[1], doc.shape[0], 90)
    print("rotated corners ok:", len(rc))

    pdf = images_to_pdf([warped, apply_filter(warped, "bw")], "A4")
    print("pdf bytes:", len(pdf))
    assert pdf[:4] == b"%PDF", "PDF header missing"
    assert len(pdf) > 5000
    print("ALL ENGINE TESTS PASSED")
