"""SCAN-0: measures the scan clean-up on photos whose text is known exactly.

    python scripts/scan_bench.py [folder with digital PDFs]

Pages are drawn (or taken from PDFs that have text), "photographed" in ten different ways, cleaned
by furceli/scan.py and read. Nothing is written into the repository and no text is printed.
"""
import argparse
import collections
import os
import re
import statistics
import sys
import tempfile
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import cv2  # noqa: E402
import pymupdf  # noqa: E402

from furceli import scan  # noqa: E402

LINES = """საქართველოს კანონმდებლობის შესაბამისად მხარეები თანხმდებიან შემდეგზე
ხელშეკრულების საერთო ღირებულება შეადგენს 12 500 ლარს დღგ-ს ჩათვლით
მომსახურების მიწოდების ვადა განისაზღვრება 2026 წლის 15 დეკემბრამდე
შემსრულებელი ვალდებულია დროულად და ხარისხიანად შეასრულოს სამუშაოები
დამკვეთი უფლებამოსილია მოითხოვოს ნაკლის გამოსწორება გონივრულ ვადაში
მხარეთა შორის წარმოშობილი დავა წყდება მოლაპარაკების გზით
ფორსმაჟორული გარემოებების არსებობისას ვალდებულებები ჩერდება
ანგარიშსწორება ხდება უნაღდო ფორმით მითითებულ საბანკო ანგარიშზე
ჟურნალისტი წერილს ჭადრის ქვეშ კითხულობდა და ღიმილით ფიქრობდა
The contractor shall provide a written report within five working days
Payment terms are net 30 days from the date of the invoice
წინამდებარე ხელშეკრულება შედგენილია ორ თანაბარი ძალის მქონე ეგზემპლარად""".split("\n")
FONTS = ["sylfaen.ttf", "segoeui.ttf", "segoeuib.ttf"]


def synthetic(n):
    for k in range(n):
        d = pymupdf.open()
        p = d.new_page()
        f = FONTS[k % len(FONTS)]
        p.insert_font(fontname="f", fontfile=os.path.join(r"C:\Windows\Fonts", f))
        size, y, text = (11, 12, 10)[k % 3], 80, []
        rows = LINES[k % 5:] + LINES[:k % 5] + LINES
        for t in rows:
            if y > 760:
                break
            p.insert_text((60, y), t, fontname="f", fontsize=size)
            text.append(t)
            y += size * 2
        if k % 2:
            p.draw_circle((470, 720), 45, color=(.1, .2, .7), width=3)
        yield d[0], " ".join(text)


def from_pdfs(folder, n):
    rnd, got = np.random.default_rng(4), 0
    paths = [os.path.join(a, f) for a, _, fs in os.walk(folder) for f in fs if f.lower().endswith(".pdf")]
    rnd.shuffle(paths)
    from furceli.features import Features
    for path in paths:
        try:
            e = Features()
            info = e.open(path)
            w, h = info["sizes"][0]
            text = " ".join("".join(c[4] for c in ln) for ln in e.chars(0))
            geo = sum("\u10d0" <= c <= "\u10ff" for c in text)
            if geo < 500 or "\ufffd" in text or not 1.3 < h / w < 1.5 or len(e.doc[0].get_images()) > 2:
                continue
            yield e.doc[0], text
        except Exception:
            continue
        got += 1
        if got == n:
            return


def photograph(page, kind, rnd):
    """(picture, true corners as fractions or None, true quarter turns)"""
    dpi = {"low": 95, "screenshot": 130}.get(kind, 200)
    pix = page.get_pixmap(dpi=dpi)
    sheet = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width * 3]
    sheet = sheet.reshape(pix.height, pix.width, 3).copy()
    h, w = sheet.shape[:2]
    if kind == "screenshot":
        return sheet, None, 0
    if kind == "scan":
        m = cv2.getRotationMatrix2D((w / 2, h / 2), 2.3, 1)
        out = cv2.warpAffine(sheet, m, (w, h), borderValue=(250, 250, 250))
        out = np.clip(out * .93 + rnd.normal(0, 4, out.shape), 0, 255).astype(np.uint8)
        return out, None, 0
    W, H = int(w * 1.3), int(h * 1.22)
    tone = {"bright": (206, 200, 190), "dark": (45, 38, 34), "wood": (140, 100, 66)}.get(kind, (96, 88, 80))
    back = np.ones((H, W, 3)) * tone + rnd.normal(0, 7, (H, W, 1))
    if kind == "wood":
        back += (np.sin(np.arange(W) / 9.0 + rnd.normal(0, .3, W)) * 14)[None, :, None]
    back = cv2.GaussianBlur(np.clip(back, 0, 255).astype(np.uint8), (0, 0), 1.2)
    j = {"strong": .11, "flat": .015}.get(kind, .045)
    c = np.float32([[(W - w) / 2, (H - h) / 2], [(W + w) / 2, (H - h) / 2],
                    [(W + w) / 2, (H + h) / 2], [(W - w) / 2, (H + h) / 2]])
    c += np.float32(rnd.uniform(-j, j, (4, 2)) * [W, H])
    c = np.clip(c, 6, [W - 7, H - 7]).astype(np.float32)
    m = cv2.getPerspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]), c)
    lay = cv2.warpPerspective(sheet, m, (W, H), flags=cv2.INTER_AREA)
    mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), m, (W, H))
    mask = cv2.GaussianBlur(mask, (3, 3), 0)[..., None] / 255.0
    img = back * (1 - mask) + lay * mask * .94
    yy, xx = np.mgrid[0:H, 0:W]
    light = 1 - .28 * ((xx - W * .25) ** 2 / W ** 2 + (yy - H * .2) ** 2 / H ** 2)
    if kind == "shadow":
        edge = 1 / (1 + np.exp(-(xx * .9 + yy * .35 - W * .75) / (W * .02)))
        light = light * (1 - .45 * edge)
    if kind == "dark":
        light = light * .6
    img = img * light[..., None] * np.array([1.0, .97, .88] if kind in ("shadow", "wood") else [1, 1, 1])
    img = cv2.GaussianBlur(img, (0, 0), {"blur": 1.5}.get(kind, .6))
    img = np.clip(img + rnd.normal(0, 9 if kind == "dark" else 4, img.shape), 0, 255).astype(np.uint8)
    img = cv2.imdecode(cv2.imencode(".jpg", img, [1, 82])[1], 1)
    frac, q = c / [W - 1, H - 1], 0
    if kind == "turned":
        q = 1 + int(rnd.integers(0, 3))
        img = scan.turn(img, q)
        for _ in range(q):
            frac = np.stack([1 - frac[:, 1], frac[:, 0]], 1)
        q = (4 - q) % 4
    return img, frac, q


KINDS = ["screenshot", "scan", "flat", "usual", "strong", "shadow", "dark", "bright", "wood",
         "turned", "low", "blur"]


def norm(t):
    return " ".join(re.sub(r"[\u00ad\u2010-\u2015]", "-", t).split())


def cer(want, got):
    a, b = norm(want).replace(" ", ""), norm(got).replace(" ", "")
    prev = np.arange(len(b) + 1)
    bb = np.frombuffer(b.encode("utf-32-le"), np.uint32)
    for i, ch in enumerate(a, 1):
        cur = np.minimum(prev[1:] + 1, prev[:-1] + (bb != ord(ch)))
        row = np.empty(len(b) + 1, int)
        row[0] = i
        row[1:] = cur
        row = np.minimum.accumulate(row - np.arange(len(b) + 1)) + np.arange(len(b) + 1)
        prev = row
    return prev[-1] / max(len(a), 1)


def recall(want, got):
    """Share of the words found, whatever their order."""
    def words(t):
        return collections.Counter(w for w in re.findall(r"[^\W_]+", norm(t)) if len(w) > 1)
    a, b = words(want), words(got)
    return sum((a & b).values()) / max(sum(a.values()), 1)


def read(path):
    from furceli.features import Features
    e = Features()
    e.open(path)
    return " ".join("".join(c[4] for c in ln) for ln in e.chars(0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?")
    ap.add_argument("-n", type=int, default=2)
    ap.add_argument("--kinds", default=",".join(KINDS))
    a = ap.parse_args()
    pages = list(from_pdfs(a.folder, a.n) if a.folder else synthetic(a.n))
    print(f"{len(pages)} pages x {len(a.kinds.split(','))} kinds   (recall: share of words read)")
    best = []  # what the reader manages on the undistorted page
    for page, text in pages:
        x = page.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY)
        a_ = np.frombuffer(x.samples, np.uint8).reshape(x.height, x.stride)[:, :x.width].copy()
        best.append(recall(text, " ".join(w[4] for w in scan.recognise(
            a_, page.rect.width, page.rect.height, "kat+eng"))))
    print(f"ceiling (perfect page): recall {statistics.mean(best):.1%}")
    print(f"{'kind':11}{'corner err':>11}{'turn ok':>9}{'CER raw':>9}{'CER':>7}{'recall raw':>12}"
          f"{'recall':>8}{'tilt':>7}{'kB':>6}{'sec':>6}")
    total = collections.defaultdict(list)
    with tempfile.TemporaryDirectory() as tmp:
        src, out = os.path.join(tmp, "p.png"), os.path.join(tmp, "o.pdf")
        for kind in a.kinds.split(","):
            rows = []
            for k, (page, text) in enumerate(pages):
                img, true, q = photograph(page, kind, np.random.default_rng(100 * k + KINDS.index(kind)))
                cv2.imwrite(src, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
                raw = scan.recognise(img, 595, 595 * img.shape[0] / img.shape[1], "kat+eng")
                raw = " ".join(w[4] for w in raw)
                t = time.time()
                o = scan.analyse(src)
                err = np.nan
                if true is not None:
                    got = np.asarray(o["corners"] if o["corners"] else scan.FULL)
                    err = min(np.abs(np.roll(got, s, 0) - true).mean() for s in range(4))
                o.update(mode="gray", languages="kat+eng")
                scan.build_page(src, 0, o, out)
                sec = time.time() - t
                got = read(out)
                flat = scan.straight(img, o)
                rows.append((err, o["turn"] == q, cer(text, raw), cer(text, got), recall(text, raw),
                             recall(text, got), abs(scan.skew(flat)), os.path.getsize(out) / 1024, sec))
            cols = list(zip(*rows))
            print(f"{kind:11}{np.nanmean(cols[0]) * 100 if true is not None else 0:10.2f}%"
                  f"{sum(cols[1])}/{len(rows):<4}".rjust(0) + "".join(
                      f"{statistics.mean(c):{f}}" for c, f in zip(cols[2:], (
                          "9.1%", "7.1%", "12.1%", "8.1%", "7.2f", "6.0f", "6.1f"))), flush=True)
            for i, c in enumerate(cols):
                total[i] += c
    ok = [e < .02 for e in total[0] if e == e]
    print(f"\ncorners within 2%: {sum(ok)}/{len(ok)}   turn ok: {sum(total[1])}/{len(total[1])}   "
          f"CER {statistics.mean(total[3]):.1%} (raw {statistics.mean(total[2]):.1%})   recall "
          f"{statistics.mean(total[5]):.1%} (raw {statistics.mean(total[4]):.1%})   ceiling "
          f"{statistics.mean(best):.1%}   worse than raw: "
          f"{sum(b < a - .02 for a, b in zip(total[4], total[5]))}")


if __name__ == "__main__":
    main()
