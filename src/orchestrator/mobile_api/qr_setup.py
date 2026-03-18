"""QR code setup endpoint for first-time mobile app pairing.

Serves an unauthenticated HTML page with a QR code containing the server
URL and API key. The Flutter app scans this QR to configure itself.

QR payload: JSON {url: str, api_key: str}

This endpoint is auth-exempt (no Bearer token required) because it IS the
mechanism for obtaining credentials on first setup.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/v1/setup/qr", response_class=HTMLResponse)
async def get_qr_setup(request: Request) -> HTMLResponse:
    """Serve QR code page for mobile app pairing.

    Generates a QR code encoding {url, api_key} as JSON.
    Prefers SVG output (no Pillow dependency); falls back to base64 PNG.
    """
    from orchestrator.mobile_api.auth import ORCHESTRATOR_API_KEY

    base_url = str(request.base_url).rstrip("/")
    payload = json.dumps({
        "url": base_url,
        "api_key": ORCHESTRATOR_API_KEY,
    })

    qr_html = _generate_qr_html(payload, base_url)
    return HTMLResponse(content=qr_html)


def _generate_qr_html(payload: str, base_url: str) -> str:
    """Generate HTML page with embedded QR code."""
    qr_image_html = _try_svg_qr(payload) or _try_png_qr(payload) or _fallback_text(payload)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Orchestrator Mobile Setup</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      max-width: 480px;
      margin: 40px auto;
      padding: 20px;
      text-align: center;
      background: #f5f5f5;
    }}
    h1 {{ color: #333; font-size: 24px; }}
    p {{ color: #666; font-size: 14px; line-height: 1.5; }}
    .qr-container {{
      background: white;
      padding: 20px;
      border-radius: 12px;
      display: inline-block;
      margin: 20px auto;
      box-shadow: 0 2px 8px rgba(0,0,0,0.12);
    }}
    .instructions {{
      background: #e8f4fd;
      border-radius: 8px;
      padding: 16px;
      margin-top: 20px;
      font-size: 13px;
      color: #2c5f8a;
    }}
  </style>
</head>
<body>
  <h1>📱 Orchestrator Mobile Setup</h1>
  <p>Scan this QR code with the Orchestrator Mobile app to connect to this server.</p>
  <div class="qr-container">
    {qr_image_html}
  </div>
  <div class="instructions">
    <strong>How to connect:</strong><br>
    1. Open the Orchestrator Mobile app<br>
    2. Go to Settings and tap "Scan QR Code"<br>
    3. Point your camera at this QR code<br>
    <br>
    Or manually enter: <code>{base_url}</code>
  </div>
</body>
</html>"""


def _try_svg_qr(payload: str) -> str | None:
    """Generate QR code as inline SVG. Returns None if unavailable."""
    try:
        import io
        import qrcode
        import qrcode.image.svg

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=6,
            border=4,
        )
        qr.add_data(payload)
        qr.make(fit=True)

        factory = qrcode.image.svg.SvgImage
        img = qr.make_image(image_factory=factory)

        buffer = io.BytesIO()
        img.save(buffer)
        svg_bytes = buffer.getvalue()

        # Remove XML declaration if present (inline SVG doesn't need it)
        svg_str = svg_bytes.decode("utf-8")
        if svg_str.startswith("<?xml"):
            svg_str = svg_str[svg_str.index("<svg"):]

        return svg_str

    except (ImportError, AttributeError, Exception) as exc:
        logger.debug("SVG QR code unavailable: %s", exc)
        return None


def _try_png_qr(payload: str) -> str | None:
    """Generate QR code as base64-encoded PNG. Returns None if unavailable."""
    try:
        import base64
        import io
        import qrcode

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=6,
            border=4,
        )
        qr.add_data(payload)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        b64 = base64.b64encode(buffer.getvalue()).decode("ascii")

        return f'<img src="data:image/png;base64,{b64}" alt="QR Code" style="max-width:300px">'

    except (ImportError, Exception) as exc:
        logger.debug("PNG QR code unavailable: %s", exc)
        return None


def _fallback_text(payload: str) -> str:
    """Fallback when qrcode library is not installed."""
    import html
    escaped = html.escape(payload)
    return (
        f'<p style="color: orange;">⚠️ QR code library not installed.</p>'
        f'<p style="font-size: 12px; word-break: break-all; '
        f'background: #f0f0f0; padding: 10px; border-radius: 4px;">'
        f'<strong>Connection payload:</strong><br><code>{escaped}</code></p>'
        f'<p style="font-size: 11px;">Install qrcode: '
        f'<code>pip install qrcode[pil]</code></p>'
    )
