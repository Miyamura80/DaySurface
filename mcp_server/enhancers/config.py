"""Config enhancer - attaches a rendered config tree image when the client can show it."""

import base64
import io

from mcp_server.enhancers import enhance
from mcp_server.enhancers.base import EnhancedTool
from models.config import ConfigShowInput, ConfigShowResult


@enhance("config_show", fallback="headless")
async def config_show_enhanced(
    tool: EnhancedTool[ConfigShowInput, ConfigShowResult],
) -> ConfigShowResult:
    result = tool.call()
    image_data = _render_config_image(result.config)
    if image_data is not None:
        tool.send_image(data=image_data, mime_type="image/png")
    return result


def _render_config_image(config: dict) -> str | None:
    """Render a simple config-tree image. Returns base64 PNG or None on failure."""
    try:
        # Deliberate deferral: Pillow is treated as optional here - the image
        # render degrades to None (text-only result) when PIL is unavailable.
        from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415
    except ImportError:
        return None

    lines = list(_format_config_lines(config))
    width = 600
    line_height = 18
    height = max(40, line_height * (len(lines) + 1) + 20)

    img = Image.new("RGB", (width, height), color=(20, 20, 28))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    for i, line in enumerate(lines):
        draw.text((10, 10 + i * line_height), line, fill=(220, 220, 240), font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _format_config_lines(obj, prefix: str = "", depth: int = 0):
    """Yield indented `key: value` lines for a config dict, depth-limited for image rendering."""
    if depth > 4:
        yield f"{prefix}..."
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict):
                yield f"{prefix}{k}:"
                yield from _format_config_lines(v, prefix + "  ", depth + 1)
            else:
                yield f"{prefix}{k}: {v!r}"
    else:
        yield f"{prefix}{obj!r}"
