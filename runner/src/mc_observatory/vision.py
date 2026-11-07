"""Screenshot-only transforms and deliberately conservative change heuristics."""
import io

from PIL import Image, ImageChops, ImageStat, ImageFilter


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


def outline(png, target_box=None):
    return outline_box(png, target_box) is not None


def outline_box(png, target_box=None):
    im = image(png)
    if im.height > 480:
        im = im.resize((round(im.width * 480 / im.height), 480))
    patch = center(im, 240, 200).convert("L")
    ridge = ImageChops.subtract(patch.filter(ImageFilter.MaxFilter(5)), patch)
    p, luminance = ridge.load(), patch.load()
    cx, cy = patch.width // 2, patch.height // 2
    bounds = None
    if target_box is not None:
        l, t, r, b = target_box
        x0, y0 = (im.width - patch.width) // 2, (im.height - patch.height) // 2
        bounds = (l * im.width - x0, t * im.height - y0, r * im.width - x0, b * im.height - y0)
    rows = []
    for y in range(3, patch.height - 3):
        start, last, hits = None, 0, 0
        for x in range(patch.width):
            dark = any(p[x, yy] > 18 and luminance[x, yy] < 130 for yy in range(y - 3, y + 4))
            if dark and start is None:
                start = x
            if dark:
                last = x
                hits += 1
            if start is not None and (x - last > 3 or x == patch.width - 1):
                if last - start >= 65 and start < cx - 15 and last > cx + 15 and hits / (last - start + 1) > .7:
                    rows.append((y, start, last))
                start, hits = None, 0
    for top, left, right in rows:
        if top >= cy - 10:
            continue
        for bottom, l2, r2 in rows:
            if bottom <= cy + 10 or bottom - top < 55 or abs(left - l2) > 8 or abs(right - r2) > 8:
                continue
            if left < 8 or right > patch.width - 8 or (right - left) / (bottom - top) > 1.65:
                continue
            if bounds is not None:
                l, t, r, b = bounds
                overlap_w = max(0, min(right, r) - max(left, l))
                overlap_h = max(0, min(bottom, b) - max(top, t))
                if overlap_w * overlap_h < .65 * (right - left) * (bottom - top):
                    continue
            hits = sum(any(p[x, y] > 14 for x in range(max(0, left - 3), min(patch.width, left + 4)))
                       for y in range(top, bottom))
            right_hits = sum(any(p[x, y] > 14 for x in range(max(0, right - 3), min(patch.width, right + 4)))
                             for y in range(top, bottom))
            required = .9 if (right - left) / (bottom - top) < .55 else .7
            strong = [sum(any(p[x, y] > 18 for x in range(max(0, edge - 3), min(patch.width, edge + 4)))
                          for y in range(top, bottom)) / (bottom - top) for edge in (left, right)]
            if min(hits, right_hits) / (bottom - top) >= required and (min(strong) >= required or max(strong) >= .95):
                x0, y0 = (im.width - patch.width) // 2, (im.height - patch.height) // 2
                return ((left + x0) / im.width, (top + y0) / im.height, (right + x0) / im.width, (bottom + y0) / im.height)
    return None


def close_face(box, width, height):
    return box is not None and box[0] <= .5 <= box[2] and box[1] <= .5 <= box[3] and (box[2] - box[0]) * width >= 180 * height / 480


def movement_danger(png):
    im = image(png)
    patch = im.crop((int(im.width * .35), int(im.height * .62), int(im.width * .65), int(im.height * .78))).resize((30, 16))
    r, g, b = patch.split()
    pixels = list(zip(r.tobytes(), g.tobytes(), b.tobytes()))
    if sum(r > 170 and 50 < g < 175 and b < 70 and r > 1.2 * g for r, g, b in pixels) / len(pixels) > .18:
        return "lava_pixel_cue"
    if sum(b > 110 and b > r * 1.15 and b > g * .9 for r, g, b in pixels) / len(pixels) > .5:
        return "drop_or_water_pixel_cue"
    return None


_LETTERS = {
    "B": ("11110", "10001", "11110", "10001", "10001", "10001", "11110"),
    "l": ("10", "10", "10", "10", "10", "10", "01"),
    "o": ("00000", "00000", "01110", "10001", "10001", "10001", "01110"),
    "c": ("00000", "00000", "01110", "10001", "10000", "10001", "01110"),
    "k": ("1000", "1000", "1001", "1010", "1100", "1010", "1001"),
    "b": ("10000", "10000", "10110", "11001", "10001", "10001", "11110"),
    "r": ("00000", "00000", "10110", "11001", "10000", "10000", "10000"),
    "e": ("00000", "00000", "01110", "10001", "11111", "10000", "01111"),
    "n": ("00000", "00000", "11110", "10001", "10001", "10001", "10001"),
}


def broken_caption(png):
    """Brightness of the vanilla English 'Block broken' glyphs; zero if absent."""
    im = image(png).convert("L")
    scale = 2
    glyphs, width = [], 0
    for ch in "Block broken":
        if ch == " ":
            width += 4
            continue
        rows = _LETTERS[ch]
        glyphs += [(width + x, y) for y, row in enumerate(rows) for x, bit in enumerate(row) if bit == "1"]
        width += len(rows[0]) + 1
    points = [(x * scale, y * scale) for x, y in glyphs]
    pixels = im.load()
    for y in range(max(0, im.height - 180), im.height - 20 - 7 * scale):
        for x in range(max(0, im.width - width * scale - 60), im.width - width * scale + 1):
            if pixels[x, y] < 140 or pixels[x, y + 6 * scale] < 140:
                continue
            values = [pixels[x + dx, y + dy] for dx, dy in points]
            if min(values) >= 140:
                # Gaps between the glyph strokes must not also be white terrain.
                gaps = [(3 * scale, 1 * scale), (7 * scale, 1 * scale), (12 * scale, 0)]
                if all(pixels[x + dx, y + dy] < 130 for dx, dy in gaps):
                    return sum(values) / len(values)
    return 0


def hotbar_change(before, after):
    a, b = image(before), image(after)
    if a.size != b.size:
        raise ValueError("Screenshot dimensions changed during action")
    for slot in range(9):
        x = a.width // 2 - 180 + slot * 40 + 8
        box = (x, a.height - 36, x + 24, a.height - 12)
        aa, bb = a.crop(box), b.crop(box)
        counts = (x + 12, a.height - 19, x + 28, a.height - 6)
        ca = a.crop(counts).convert("L").point(lambda value: 255 if value > 230 else 0)
        cb = b.crop(counts).convert("L").point(lambda value: 255 if value > 230 else 0)
        if ImageStat.Stat(ImageChops.difference(ca, cb)).sum[0] / 255 >= 8:
            return True
        diff = sum(ImageStat.Stat(ImageChops.difference(aa, bb)).mean) / (3 * 255)
        edge = lambda im: ImageStat.Stat(im.convert("L").filter(ImageFilter.FIND_EDGES).crop((2, 2, 22, 22))).mean[0]
        if diff > .08 and edge(bb) - edge(aa) > 5:
            return True
    return False
