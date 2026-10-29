import numpy as np
from PIL import Image

from instapost import qa
from instapost.brand import load_brand
from instapost.render import plate, video
from tests.test_plates import QUAD, _book_photo, _page


def _plate_motion(duration=10.0):
    brand = load_brand()
    p = plate.prepare("t", _book_photo(), QUAD, seed=1)
    comp = plate.compose_on_plate(brand, p, _page(brand), seed=1)
    return comp, video.Motion(comp.still, brand, duration, 1, comp)


def test_handheld_drift_and_focus_repeat_every_loop():
    d = 9.0
    assert np.allclose(video.sway_at(0, d), video.sway_at(d, d), atol=1e-9)
    dx = [abs(video.sway_at(t, d)[0]) for t in np.linspace(0, d, 50)]
    assert 1.0 < max(dx) <= video.SWAY_PX
    assert video.focus_at(0, d) == 1.0 and video.focus_at(d / 2, d) == 0.0
    assert video.focus_at(d - 0.01, d) > 0.9


def test_handheld_transform_keeps_the_centre_without_drift():
    img = Image.new("RGB", (video.FRAME_W, video.FRAME_H), (0, 0, 0))
    img.putpixel((video.FRAME_W // 2, video.FRAME_H // 2), (255, 255, 255))
    out = np.asarray(video._handheld(img, 1.0, 0, 0, 0.0))
    assert out[video.FRAME_H // 2, video.FRAME_W // 2].max() > 200


def test_lines_are_written_left_to_right():
    comp, m = _plate_motion()
    layer = comp.layers[0]
    n = len(comp.layers)
    # Find a moment where the first body line is about half written.
    t = next(x for x in np.linspace(0, 3, 300)
             if 0.45 < video.reveal_parts(layer.order, n, x, m.duration)[0] < 0.55)
    frame = np.asarray(m._page_at(t), np.float32)
    base = np.asarray(comp.base, np.float32)
    h, w = layer.alpha.shape[:2]
    rise = int(round(video.reveal_parts(layer.order, n, t, m.duration)[2]))
    region = (frame - base)[layer.y + rise:layer.y + rise + h, layer.x:layer.x + w]
    left = np.abs(region[:, : w // 4]).mean()
    right = np.abs(region[:, -w // 4:]).mean()
    assert left > 3 * max(right, 0.01)


def test_book_photo_reel_loops_seamlessly_and_stays_in_the_safe_area():
    comp, m = _plate_motion()
    first = m.frame_at(0).astype(np.int16)
    last = m.frame_at(m.duration - 1 / 30).astype(np.int16)
    assert np.abs(first - last).mean() < 1.5
    assert m.photo and not qa.unsafe_boxes(comp.text_boxes, photo=True)
    # The light band brightens the page in the middle of the loop, not at its ends.
    mid = np.asarray(m._page_at(m.duration * 0.5), np.float32)
    start = np.asarray(m._page_at(0.0), np.float32)
    b = comp.page_box
    page = (slice(int(b.y0), int(b.y1)), slice(int(b.x0), int(b.x1)))
    assert (mid[page] - start[page]).max() > 5
