from __future__ import annotations

from pathlib import Path
import subprocess

from PIL import Image, ImageDraw, ImageFilter


ICON_SIZES = [
    (16, "icon_16x16.png"),
    (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"),
    (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"),
    (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"),
    (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"),
    (1024, "icon_512x512@2x.png"),
]


def _blend(start: tuple[int, int, int], end: tuple[int, int, int], step: float) -> tuple[int, int, int]:
    return tuple(int(start[index] + (end[index] - start[index]) * step) for index in range(3))


def build_master_icon(size: int = 1024) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gradient = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gradient_draw = ImageDraw.Draw(gradient)

    top = (244, 166, 88)
    bottom = (209, 86, 52)
    for y in range(size):
        color = _blend(top, bottom, y / max(size - 1, 1))
        gradient_draw.line((0, y, size, y), fill=(*color, 255))

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (72, 72, size - 72, size - 72),
        radius=230,
        fill=255,
    )
    image.paste(gradient, (0, 0), mask)

    canvas = ImageDraw.Draw(image)

    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (228, 250, 796, 696),
        radius=112,
        fill=(0, 0, 0, 90),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(28))
    image.alpha_composite(shadow)

    left_card = [
        (214, 314),
        (420, 210),
        (560, 458),
        (356, 562),
    ]
    right_card = [
        (460, 220),
        (760, 298),
        (658, 684),
        (358, 608),
    ]
    front_card_bounds = (238, 284, 786, 778)

    canvas.polygon(left_card, fill=(255, 222, 178, 235))
    canvas.polygon(right_card, fill=(255, 242, 228, 238))
    canvas.rounded_rectangle(front_card_bounds, radius=96, fill=(255, 250, 242, 255))

    lens_outer = (354, 368, 670, 684)
    lens_mid = (402, 416, 622, 636)
    lens_inner = (454, 468, 570, 584)
    canvas.ellipse(lens_outer, fill=(40, 54, 64, 255))
    canvas.ellipse(lens_mid, fill=(87, 132, 153, 255))
    canvas.ellipse(lens_inner, fill=(201, 232, 236, 255))
    canvas.ellipse((478, 486, 546, 554), fill=(255, 255, 255, 180))

    canvas.rounded_rectangle((640, 342, 744, 420), radius=30, fill=(237, 112, 84, 255))
    canvas.rounded_rectangle((274, 332, 460, 392), radius=28, fill=(246, 200, 120, 255))
    canvas.rounded_rectangle((288, 618, 734, 660), radius=20, fill=(232, 154, 97, 255))

    sparkle = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sparkle_draw = ImageDraw.Draw(sparkle)
    sparkle_draw.polygon([(748, 212), (772, 262), (826, 286), (772, 310), (748, 360), (724, 310), (670, 286), (724, 262)], fill=(255, 249, 225, 220))
    sparkle_draw.polygon([(270, 188), (286, 222), (322, 238), (286, 254), (270, 288), (254, 254), (218, 238), (254, 222)], fill=(255, 245, 214, 190))
    sparkle = sparkle.filter(ImageFilter.GaussianBlur(2))
    image.alpha_composite(sparkle)

    return image


def main() -> None:
    root_dir = Path(__file__).resolve().parents[1]
    resources_dir = root_dir / "resources"
    iconset_dir = resources_dir / "PhotosMcp.iconset"
    icns_path = resources_dir / "PhotosMcp.icns"
    preview_path = resources_dir / "PhotosMcp-preview.png"

    resources_dir.mkdir(parents=True, exist_ok=True)
    if iconset_dir.exists():
        for child in iconset_dir.iterdir():
            child.unlink()
    else:
        iconset_dir.mkdir(parents=True)

    master = build_master_icon()
    master.save(preview_path)

    for size, filename in ICON_SIZES:
        resized = master.resize((size, size), Image.Resampling.LANCZOS)
        resized.save(iconset_dir / filename)

    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset_dir), "-o", str(icns_path)],
        check=True,
    )


if __name__ == "__main__":
    main()