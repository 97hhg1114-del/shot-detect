"""Visual QC for shot detection -- look at the contact sheets before trusting a run.

  python qc.py sheet  VIDEO [--step 8] [--page 0]     every Nth frame, cuts boxed green
  python qc.py zoom   VIDEO T0 T1 [--step 2]          frame-level look at one stretch
  python qc.py flags  VIDEO [--kind drift] [--page 0] one filmstrip per flagged shot

'sheet' answers "are the marked boundaries right"; 'flags' answers "did anything
get missed" -- a filmstrip row showing two different scenes is a missed cut.
Sheets are written to the current directory (or -o DIR) as qc_*.png.
"""
import os, argparse
import numpy as np
from PIL import Image, ImageDraw
import shotlib


def load(vid):
    fps, dur, n, bounds, m, fr = shotlib.shots(vid)
    print(f"{os.path.basename(vid)}: {n} frames, {len(bounds)} shots, "
          f"avg {dur/len(bounds):.2f}s")
    return fps, n, bounds, m, fr


def grid(cells, cols, tw, th, label_h=12, pad=3, lbl_w=0):
    return Image.new("RGB", (lbl_w + cols * (tw + pad) + pad,
                             ((len(cells) + cols - 1) // cols) * (th + pad + label_h) + pad),
                     (25, 25, 30))


def cmd_sheet(a):
    fps, n, bounds, m, fr = load(a.video)
    starts = {b[0] for b in bounds}
    TW, TH, COLS, PAD, PER = 78, 138, 16, 3, 16 * 16
    idxs = list(range(0, n, a.step))
    pages = (len(idxs) + PER - 1) // PER
    idxs = idxs[a.page * PER:(a.page + 1) * PER]
    print(f"page {a.page + 1}/{pages}, {len(idxs)} thumbs")
    sheet = grid(idxs, COLS, TW, TH)
    d = ImageDraw.Draw(sheet)
    for k, i in enumerate(idxs):
        r, c = divmod(k, COLS)
        x, y = PAD + c * (TW + PAD), PAD + r * (TH + PAD + 12)
        sheet.paste(Image.fromarray(fr[i]).resize((TW, TH), Image.BILINEAR), (x, y))
        if any(i - a.step < s <= i for s in starts):
            d.rectangle([x - 2, y - 2, x + TW + 1, y + TH + 1], outline=(0, 255, 60), width=2)
        d.text((x, y + TH + 1), f"{i/fps:.1f}", fill=(160, 160, 170))
    return sheet, f"qc_sheet_p{a.page}"


def cmd_zoom(a):
    fps, n, bounds, m, fr = load(a.video)
    starts = {b[0] for b in bounds}
    TW, TH, COLS, PAD = 96, 170, 14, 3
    idxs = list(range(int(a.t0 * fps), min(n, int(a.t1 * fps)), a.step))
    sheet = grid(idxs, COLS, TW, TH, label_h=13)
    d = ImageDraw.Draw(sheet)
    for k, i in enumerate(idxs):
        r, c = divmod(k, COLS)
        x, y = PAD + c * (TW + PAD), PAD + r * (TH + PAD + 13)
        sheet.paste(Image.fromarray(fr[i]).resize((TW, TH), Image.BILINEAR), (x, y))
        if any(i - a.step < s <= i for s in starts):
            d.rectangle([x - 2, y - 2, x + TW + 1, y + TH + 1], outline=(0, 255, 60), width=3)
        d.text((x, y + TH + 1), f"{i/fps:.2f}|{m[i-1] if i else 0:.0f}", fill=(170, 170, 180))
    return sheet, f"qc_zoom_{a.t0:.0f}-{a.t1:.0f}"


def cmd_flags(a):
    fps, n, bounds, m, fr = load(a.video)
    flags = shotlib.qc_flags(fps, bounds, m, fr)
    if a.kind != "all":
        flags = [f for f in flags if a.kind in f["why"]]
    N, TW, TH, PAD, LBL, ROWS = 13, 88, 156, 3, 42, 12
    total = len(flags)
    flags = flags[a.page * ROWS:(a.page + 1) * ROWS]
    print(f"{a.kind}: {len(flags)} rows of {total} flags")
    sheet = Image.new("RGB", (LBL + N * (TW + PAD) + PAD,
                              max(1, len(flags)) * (TH + PAD + 12) + PAD), (25, 25, 30))
    d = ImageDraw.Draw(sheet)
    for r, f in enumerate(flags):
        s, e = bounds[f["cut"]]
        y = PAD + r * (TH + PAD + 12)
        d.text((2, y + TH // 2 - 12), f"c{f['cut']:03d}\n{f['dur']}s\n{f['why'][:9]}",
               fill=(255, 210, 90))
        for c, i in enumerate(np.linspace(s, e - 1, N).round().astype(int)):
            x = LBL + PAD + c * (TW + PAD)
            sheet.paste(Image.fromarray(fr[i]).resize((TW, TH), Image.BILINEAR), (x, y))
            d.text((x, y + TH + 1), f"{i/fps:.1f}", fill=(150, 150, 160))
    return sheet, f"qc_flags_{a.kind}_{a.page}"


ap = argparse.ArgumentParser()
ap.add_argument("-o", "--out", default=".", help="where to write the sheet")
sub = ap.add_subparsers(dest="cmd", required=True)
s = sub.add_parser("sheet"); s.add_argument("video"); s.add_argument("--step", type=int, default=8)
s.add_argument("--page", type=int, default=0); s.set_defaults(fn=cmd_sheet)
z = sub.add_parser("zoom"); z.add_argument("video"); z.add_argument("t0", type=float)
z.add_argument("t1", type=float); z.add_argument("--step", type=int, default=2)
z.set_defaults(fn=cmd_zoom)
g = sub.add_parser("flags"); g.add_argument("video")
g.add_argument("--kind", default="all", choices=["all", "drift", "spike", "short"])
g.add_argument("--page", type=int, default=0); g.set_defaults(fn=cmd_flags)

a = ap.parse_args()
img, name = a.fn(a)
stem = os.path.splitext(os.path.basename(a.video))[0]
os.makedirs(a.out, exist_ok=True)
out = os.path.join(os.path.abspath(a.out), f"{name}_{stem}.png")
img.save(out)
print(out, img.size)
