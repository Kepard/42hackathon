"""Screenshot-only transforms and deliberately conservative change heuristics."""
import io

from PIL import Image, ImageChops, ImageStat


def image(png):
    return Image.open(io.BytesIO(png)).convert("RGB")


def center(im, width=160, height=120):
    width, height = min(width, im.width), min(height, im.height)
    x, y = (im.width - width) // 2, (im.height - height) // 2
    return im.crop((x, y, x + width, y + height))


def center_crop(png):
    crop = center(image(png))
    out = io.BytesIO()
    crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.NEAREST).save(out, format="PNG")
    return out.getvalue()


def changes(before, after):
    a, b = image(before), image(after)
    if a.size != b.size:
        raise ValueError("Screenshot dimensions changed during action")
    full = ImageChops.difference(a.resize((128, 72)), b.resize((128, 72)))
    patch = ImageChops.difference(center(a, 64, 64), center(b, 64, 64))
    r, g, bl = patch.split()
    histogram = ImageChops.lighter(ImageChops.lighter(r, g), bl).histogram()
    return {"frame_change": sum(ImageStat.Stat(full).mean) / (3 * 255),
            "center_change": sum(ImageStat.Stat(patch).mean) / (3 * 255),
            "center_changed_fraction": sum(histogram[24:]) / (patch.width * patch.height)}
