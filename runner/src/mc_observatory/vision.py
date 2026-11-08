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


def hotbar_row_boxes(width, height):
    scale = max(1, min(width // 320, height // 240))
    scale += scale % 2
    # Vanilla's 176x166 inventory has hotbar (8,142); the open book shifts it 77px.
    gui_left = max(0, int(width / 2 - 84 * scale))
    gui_right = min(width, int(width / 2 + 163 * scale))
    gui_top = min(height - 1, int(height / 2 + 55))
    gui_bottom = max(gui_top + 1, min(height, int(height / 2 + 81 * scale)))
    row_width = gui_right - gui_left
    world_left = (width - row_width) // 2
    return ((world_left, max(0, height - 24 * scale), world_left + row_width, height),
            (gui_left, gui_top, gui_right, gui_bottom))


def hotbar_rows_crop(png):
    im = image(png)
    rows = [im.crop(box) for box in hotbar_row_boxes(im.width, im.height)]
    width = rows[0].width
    strip = Image.new("RGB", (width, sum(row.height for row in rows)))
    y = 0
    for row in rows:
        strip.paste(row, (0, y))
        y += row.height
    out = io.BytesIO()
    strip.resize((strip.width * 3, strip.height * 3), Image.Resampling.NEAREST).save(out, format="PNG")
    return out.getvalue()


def recipe_book_controls(png):
    im = image(png)
    pixels = im.load()
    for y in range(1, im.height // 2):
        x = 1
        while x < im.width // 2:
            border = pixels[x - 1, y]
            if pixels[x, y] != (0, 0, 0) or border not in ((160,160,160),(255,255,255)) or pixels[x,y-1] != border:
                x += 1
                continue
            right = x
            while right < im.width and pixels[right,y] == (0,0,0): right += 1
            width = right - x
            scale = max(1, round(width / 79))
            if abs(width - 79 * scale) > scale or not all(pixels[z,y-1] == border for z in range(x,right)):
                x = right
                continue
            bottom = y + 12 * scale
            if bottom >= im.height or right >= im.width or not all(pixels[z,bottom] == border for z in range(x,right)):
                x = right
                continue
            right_border_matches = sum(pixels[right,t] == border for t in range(y,bottom))
            if right_border_matches < .75 * (bottom-y) or not all(pixels[x-1,t] == border for t in range(y,bottom)):
                x = right
                continue
            red = green = 0
            for z in range(right+5*scale,min(im.width,right+18*scale)):
                for t in range(y,bottom):
                    r,g,b = pixels[z,t]
                    red += r > 1.5*g and r > 1.5*b
                    green += g > 1.2*r and g > 1.5*b
            if abs(red - green) < 4 * scale * scale:
                x = right
                continue
            white = sum(min(pixels[z,t]) >= 240 for z in range(x,right) for t in range(y,bottom))
            return {"search": (x + width//2, (y+bottom)//2), "filter": (right+10*scale, (y+bottom)//2),
                    "results": (x-15*scale,y+18*scale,x+110*scale,y+143*scale),
                    "craftable_only": green > red, "query_present": white > 16*scale*scale,
                    "scale": scale, "anchor": (x,y)}
    return None


def _slot_border(im, center, scale):
    x,y = center
    if not (9*scale <= x < im.width-9*scale and 9*scale <= y < im.height-9*scale): return False
    p = im.load()
    return all(p[x+z*scale,y-9*scale] == (55,55,55) and p[x-9*scale,y+z*scale] == (55,55,55)
               for z in (-7,-3,1,5)) and p[x+8*scale,y] == (255,255,255)


def crafting_controls(png):
    im = image(png)
    book = recipe_book_controls(png)
    candidates = [(book["scale"],book["anchor"][0]+122*book["scale"],book["anchor"][1]-14*book["scale"])] if book else [
        (s,((im.width//s-176)//2)*s,((im.height//s-166)//2)*s) for s in range(1,7)]
    for s,left,top in candidates:
        for kind,cols,start,output,toggle in (("inventory",2,(98,18),(162,36),(114,70)),("table",3,(30,17),(132,43),(15,43))):
            slots = [(left+(start[0]+8+18*c)*s,top+(start[1]+8+18*r)*s) for r in range(cols) for c in range(cols)]
            if all(_slot_border(im,point,s) for point in slots):
                return {"kind":kind,"scale":s,"inputs":slots,"output":(left+output[0]*s,top+output[1]*s),
                        "book_open":book is not None,"book_toggle":(left+toggle[0]*s,top+toggle[1]*s),
                        "hotbar":[(left+(16+18*i)*s,top+150*s) for i in range(9)]}
    return None


def container_gui_visible(png):
    im = image(png)
    for s in range(1,7):
        for shift in (0,77):
            left = ((im.width//s-176)//2+shift)*s
            for y in range(max(9*s,im.height//2-40*s),min(im.height-9*s,im.height//2+100*s),s):
                if all(_slot_border(im,(left+(16+18*i)*s,y),s) for i in range(9)):
                    return True
    return False


def crafting_slot(png, center, scale):
    im = image(png)
    x,y = center
    patch = im.crop((x-8*scale,y-8*scale,x+8*scale,y+8*scale))
    colors = list(zip(*(channel.tobytes() for channel in patch.split())))
    corners = [im.getpixel((x+dx*scale,y+dy*scale)) for dx,dy in ((-7,-7),(6,-7),(-7,6),(6,6))]
    return {"occupied":sum(max(abs(v-139) for v in color)>20 for color in colors)>4*scale*scale,
            "ghost":sum(r>g+25 and r>b+25 and abs(g-b)<8 for r,g,b in corners)>=3}


def filtered_recipe_icons(png):
    controls = recipe_book_controls(png)
    if not controls or not controls["craftable_only"] or not controls["query_present"]: return []
    im = image(png);s=controls["scale"];left,top,_,_=controls["results"]
    icons = []
    for row in range(4):
        for col in range(5):
            center = (left+(12+25*col)*s,top+(11+25*row)*s)
            corner = im.getpixel((center[0]-12*s,center[1]-12*s))
            ring = [im.getpixel((center[0]+dx*s,center[1]+dy*s)) for dx,dy in ((-9,-9),(9,-9),(-9,9),(9,9))]
            if min(corner)>80 and max(corner)-min(corner)<8 and all(min(p)>80 and max(p)-min(p)<8 for p in ring):
                icons.append({"center":center,**crafting_slot(png,center,s)})
    return icons


_STACK_DIGITS = (
    "01110/10001/10011/10101/11001/10001/01110",
    "00100/01100/00100/00100/00100/00100/11111",
    "01110/10001/00001/00110/01000/10001/11111",
    "01110/10001/00001/00110/00001/10001/01110",
    "00011/00101/01001/10001/11111/00001/00001",
    "11111/10000/11110/00001/00001/10001/01110",
    "00110/01000/10000/11110/10001/10001/01110",
    "11111/10001/00001/00010/00100/00100/00100",
    "01110/10001/10001/01110/10001/10001/01110",
    "01110/10001/10001/01111/00001/00010/01100",
)


def stack_count(png, center, scale):
    im = image(png);p=im.load();cx,cy=center
    def digit(x):
        bits = "/".join("".join("1" if min(p[x+dx*scale,cy+(1+dy)*scale])>=240 else "0" for dx in range(5)) for dy in range(7))
        return _STACK_DIGITS.index(bits) if bits in _STACK_DIGITS else None
    units = digit(cx+3*scale)
    tens = digit(cx-3*scale)
    return 1 if units is None else units+(10*tens if tens is not None else 0)


def matching_hotbar_slots(output_png, output_center, after_png, layout):
    s=layout["scale"]
    def icon(im,point):
        x,y=point
        return im.crop((x-8*s,y-8*s,x+8*s,y+s)).resize((16,9),Image.Resampling.NEAREST)
    reference=icon(image(output_png),output_center);after=image(after_png)
    return [{"slot":i+1,"count":stack_count(after_png,point,s)} for i,point in enumerate(layout["hotbar"])
            if sum(ImageStat.Stat(ImageChops.difference(reference,icon(after,point))).mean)/3<2]


def changes(before, after, target_box=None):
    a, b = image(before), image(after)
    if a.size != b.size:
        raise ValueError("Screenshot dimensions changed during action")
    full = ImageChops.difference(a.resize((128, 72)), b.resize((128, 72)))
    if target_box is None:
        patch = ImageChops.difference(center(a, 64, 64), center(b, 64, 64))
    else:
        l,t,r,bottom = target_box
        if not (0 <= l < r <= 1 and 0 <= t < bottom <= 1):
            raise ValueError("Invalid normalized target box")
        dx,dy = (r-l)*.2, (bottom-t)*.2
        bounds = (round((l+dx)*a.width),round((t+dy)*a.height),round((r-dx)*a.width),round((bottom-dy)*a.height))
        if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
            raise ValueError("Target box is smaller than a pixel")
        patch = ImageChops.difference(a.crop(bounds),b.crop(bounds))
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


CLOSE_FACE_WIDTH = 180 / 480


def close_face(box, width, height):
    return box is not None and box[0] <= .5 <= box[2] and box[1] <= .5 <= box[3] and (box[2] - box[0]) * width >= CLOSE_FACE_WIDTH * height


def large_face_box(png):
    im = image(png)
    if im.height > 480:
        im = im.resize((round(im.width * 480 / im.height),480))
    cy, cx = im.height//2, im.width//2
    half = min(80,cy)
    band = im.crop((0,cy-half,im.width,cy+half)).convert("L")
    ridge = ImageChops.subtract(band.filter(ImageFilter.MaxFilter(5)),band)
    p, light = ridge.load(),band.load()
    columns = [x for x in range(3,im.width-3) if sum(p[x,y]>18 and light[x,y]<130 for y in range(band.height)) >= band.height*.85]
    half_width = CLOSE_FACE_WIDTH * im.height / 2
    left = max((x for x in columns if x <= cx-half_width),default=None)
    right = min((x for x in columns if x >= cx+half_width),default=None)
    if left is None or right is None:
        return None
    return (left/im.width,(cy-half)/im.height,right/im.width,(cy+half)/im.height)


_LAVA_SUBTITLE = (
    "100000000000000000000000000000000000000000000000000",
    "100000000000000000000000000000000000000000000000000",
    "100000011100100010011100000010110001110010110001111",
    "100000000010100010000010000011001010001011001010000",
    "100000011110100010011110000010001010001010001001110",
    "100000100010010100100010000011110010001011110000001",
    "111110011110001000011110000010000001110010000011110",
    "000000000000000000000000000010000000000010000000000",
)


def lava_sound_nearby(png):
    im = image(png)
    pixels = im.load()
    ink = [(x,y) for y,row in enumerate(_LAVA_SUBTITLE) for x,bit in enumerate(row) if bit == "1"]
    gaps = [(x,y) for y,row in enumerate(_LAVA_SUBTITLE) for x,bit in enumerate(row) if bit == "0"]
    def matches(x,y,level):
        color = pixels[x,y]
        return max(color)-min(color) <= 4 and abs(min(color)-level) <= 8
    for scale in (2,1,3,4,6):
        for y in range(max(0,im.height-max(180,100*scale)),im.height-18*scale):
            for x in range(max(0,im.width-max(280,64*scale)),im.width-51*scale+1):
                color = pixels[x,y]
                level = min(color)
                if level < 65 or max(color)-level > 4: continue
                if not matches(x,y+6*scale,level) or not matches(x+4*scale,y+6*scale,level): continue
                if matches(x+2*scale,y,level) or matches(x+5*scale,y+6*scale,level): continue
                if all(matches(x+dx*scale,y+dy*scale,level) for dx,dy in ink) and not any(matches(x+dx*scale,y+dy*scale,level) for dx,dy in gaps):
                    return True
    return False


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
