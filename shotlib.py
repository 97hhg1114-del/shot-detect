"""Shot boundary detection: ffmpeg raw pipe + adaptive thresholding + validation.

Pipeline
  1. decode the whole video downscaled to 48x85 rgb24
  2. per-frame-pair mean-abs-diff metric
  3. candidate cuts: metric over an absolute floor AND either very large or
     standing out against the local rolling median (catches cuts inside
     visually similar scenes that a global threshold misses)
  4. sustained-difference validation: the content really has to change and stay
     changed, which kills camera shake / fast motion false positives
  5. flash rejection: a span of nearby candidates whose surrounding content
     matches is a lighting flash inside one shot, not two cuts
  6. minimum shot length: absorb leftover sub-0.2s fragments
  7. dissolve pass: split shots containing a gradual transition that never
     spiked a single frame pair

Tuned against a 1080x1920 24fps Korean short-form drama (fast cutting, heavy
white-flash transitions, occasional 1s cross-dissolves). Other material may want
different thresholds -- the ones that matter most are HARD_DELTA (hard cuts),
DIS_MIN (dissolves) and FLASH_SIM (flash tolerance).
"""
import os, subprocess, json
import numpy as np

# a windowed build has no console, so every ffmpeg call would flash its own
# unless we suppress it. Harmless for the CLI -- output is piped either way.
NOWIN = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

W, H = 48, 85
BPF = W * H * 3

MIN_DELTA   = 3.0    # absolute floor for a candidate
ADAPT_RATIO = 2.5    # candidate must be this many x the local median
HARD_DELTA  = 22.0   # unconditional candidate
WINDOW      = 12     # frames each side for the local median
SUSTAIN_MIN = 7.0    # required pre/post median-frame difference
SUSTAIN_W   = 6      # frames each side for the sustained check
FLASH_GAP   = 15     # max frames between the two edges of a flash
FLASH_SIM   = 20.0   # below this pre/post difference the span is a flash. Real
                     # cuts separate by 40-140, so this leaves wide margin while
                     # tolerating the exposure shift a flash leaves behind
FLASH_STD   = 20.0   # flash frames are near-uniform (white/black), low spatial std
MIN_SHOT_F  = 4      # hard floor on shot length, in frames
DECAY_F     = 12     # window after an accepted cut where motion still settles
DECAY_RATIO = 0.35   # a candidate this much weaker than the cut it follows is decay

# --- dissolve pass -------------------------------------------------------
# A cross-dissolve spreads the change over ~0.5-1.5s, so no single frame pair
# ever spikes and steps 3-6 above miss it entirely. Comparing block means that
# straddle a gap makes the transition obvious.
DIS_W       = 12     # frames averaged on each side
DIS_GAP     = 14     # frames skipped either side of the split point, so the blocks
                     # land clear of the transition itself
DIS_MIN     = 45.0   # required difference between the two blocks
DIS_RATIO   = 3.0    # ...and it must dwarf the movement inside *either* block,
                     # otherwise a fast camera move reads as a transition
DIS_MARGIN  = 26     # keep splits this far from an existing boundary


def probe(vid):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate:format=duration",
         "-of", "json", vid],
        capture_output=True, text=True, encoding="utf-8", **NOWIN).stdout
    d = json.loads(out)
    num, den = d["streams"][0]["r_frame_rate"].split("/")
    return float(num) / float(den), float(d["format"]["duration"])


def load_small(vid):
    cmd = ["ffmpeg", "-v", "error", "-i", vid, "-map", "0:v:0",
           "-vf", f"scale={W}:{H}:flags=bilinear",
           "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         bufsize=BPF * 64, **NOWIN)
    chunks = []
    while True:
        buf = p.stdout.read(BPF * 64)
        if not buf:
            break
        chunks.append(buf)
    p.stdout.close(); p.wait()
    raw = b"".join(chunks)
    n = len(raw) // BPF
    return np.frombuffer(raw[:n * BPF], dtype=np.uint8).reshape(n, H, W, 3)


def metrics_from(frames):
    return np.abs(np.diff(frames.astype(np.int16), axis=0)).mean(axis=(1, 2, 3))


def rolling_median(m, w):
    n = len(m)
    out = np.empty(n)
    for i in range(n):
        lo, hi = max(0, i - w), min(n, i + w + 1)
        nb = np.concatenate([m[lo:i], m[i + 1:hi]])
        out[i] = np.median(nb) if len(nb) else 0.0
    return out


def _med_frame(fr, lo, hi):
    lo, hi = max(0, lo), min(len(fr), hi)
    return np.median(fr[lo:hi].astype(np.float32), axis=0)


def _diff(a, b):
    return float(np.abs(a - b).mean())


def detect(fr, m):
    """Return (cut indices, flash spans). Index i means a cut between i and i+1."""
    n = len(m)
    med = np.maximum(rolling_median(m, WINDOW), 0.01)
    cand = np.nonzero((m >= MIN_DELTA) &
                      ((m >= HARD_DELTA) | (m / med >= ADAPT_RATIO)))[0].tolist()
    # a "cut" in the opening frames is a fade-in, not a boundary
    cand = [c for c in cand if c + 1 >= MIN_SHOT_F]

    # 4. sustained-difference validation
    kept = []
    for c in cand:
        pre = _med_frame(fr, c - SUSTAIN_W + 1, c + 1)
        post = _med_frame(fr, c + 1, c + 1 + SUSTAIN_W)
        if _diff(pre, post) >= SUSTAIN_MIN:
            kept.append(c)

    # 5. flash rejection: a span of candidates whose outside content matches and
    #    whose interior frames are near-uniform is a lighting flash, not cuts
    out, flashes, i = [], [], 0
    while i < len(kept):
        c1 = kept[i]
        before = _med_frame(fr, c1 - SUSTAIN_W + 1, c1 + 1)
        hit = -1
        for k in range(len(kept) - 1, i, -1):        # prefer the widest span
            c2 = kept[k]
            if c2 - c1 > FLASH_GAP:
                continue
            interior = fr[c1 + 1:c2 + 1].astype(np.float32)
            if len(interior) == 0:
                continue
            # a flash blows at least one frame out to near-uniform; the ramp
            # frames around it keep detail, so test the minimum, not the median
            if float(interior.std(axis=(1, 2, 3)).min()) >= FLASH_STD:
                continue                              # interior has real detail
            after = _med_frame(fr, c2 + 1, c2 + 1 + SUSTAIN_W)
            if _diff(before, after) < FLASH_SIM:
                hit = k
                break
        if hit >= 0:
            flashes.append((c1, kept[hit]))           # drop the whole span
            i = hit + 1
            continue
        out.append(c1); i += 1

    # 6. minimum shot length + motion-decay suppression.
    #    When two cuts are closer than one shot apart the frames between them are
    #    a transition (a 2-3 frame dip to black, say), so keep the LATER edge and
    #    let the new shot start on real content rather than on the transition.
    final = []
    for c in out:
        if final and c - final[-1] < MIN_SHOT_F:
            if m[c] >= DECAY_RATIO * m[final[-1]]:
                final[-1] = c        # a real second edge: start on the new content
            # otherwise it is the previous cut still settling; leave the boundary
        elif final and c - final[-1] < DECAY_F and m[c] < DECAY_RATIO * m[final[-1]]:
            continue                                  # tail of the previous cut
        else:
            final.append(c)
    while final and (n + 1) - (final[-1] + 1) < MIN_SHOT_F:
        final.pop()
    return final, flashes


def block_diff(fr):
    """D[p] = |mean(frames p-GAP-W .. p-GAP) - mean(frames p+GAP .. p+GAP+W)|.

    Large wherever the picture is different before and after p, no matter how
    gradually it got there -- which is exactly what a cross-dissolve looks like.
    """
    n = len(fr)
    hh, ww = (H // 2) * 2, (W // 2) * 2          # 2x2 pooling needs even dims
    small = (fr[:, :hh, :ww].astype(np.float32)
             .reshape(n, hh // 2, 2, ww // 2, 2, 3).mean(axis=(2, 4)))
    cs = np.concatenate([np.zeros((1,) + small.shape[1:], np.float32),
                         np.cumsum(small, axis=0)])

    def box(a, b):                       # mean of frames [a, b)
        return (cs[b] - cs[a]) / (b - a)

    D = np.zeros(n, np.float32)
    lo = DIS_GAP + DIS_W
    for p in range(lo, n - lo):
        D[p] = np.abs(box(p - DIS_GAP - DIS_W, p - DIS_GAP) -
                      box(p + DIS_GAP, p + DIS_GAP + DIS_W)).mean()
    return D


def _steady(fr, p):
    """True when the change across p is bigger than the motion inside either block.

    Both blocks sit clear of the transition zone, so a real dissolve shows two
    settled but different pictures, while a fast camera move leaves at least one
    block churning and is rejected.
    """
    pre = fr[p - DIS_GAP - DIS_W:p - DIS_GAP].astype(np.float32)
    post = fr[p + DIS_GAP:p + DIS_GAP + DIS_W].astype(np.float32)
    if len(pre) < DIS_W or len(post) < DIS_W:
        return False
    # a near-uniform block is a flash or a dip to black, not a shot to split on
    if min(float(pre.std(axis=(1, 2, 3)).mean()),
           float(post.std(axis=(1, 2, 3)).mean())) < FLASH_STD:
        return False
    between = float(np.abs(pre.mean(0) - post.mean(0)).mean())
    within = max(float(np.abs(pre - pre.mean(0)).mean()),
                 float(np.abs(post - post.mean(0)).mean()))
    return between >= DIS_MIN and between >= DIS_RATIO * max(within, 0.1)


def add_dissolves(fr, cuts, flashes, n):
    """Split shots that contain a gradual transition the frame-diff pass missed."""
    D = block_diff(fr)
    reach = DIS_GAP + DIS_W
    starts = [0] + [c + 1 for c in cuts]
    extra = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else n
        while True:
            lo, hi = s + DIS_MARGIN, e - DIS_MARGIN
            if hi <= lo:
                break
            seg = D[lo:hi]
            order = np.argsort(seg)[::-1]
            p = -1
            for j in order:                          # strongest peak that survives
                q = int(j) + lo
                if seg[j] < DIS_MIN:
                    break
                if any(f1 - reach <= q <= f2 + reach for f1, f2 in flashes):
                    continue                         # a flash already ruled out
                if _steady(fr, q):
                    p = q
                    break
            if p < 0:
                break
            extra.append(p - 1)          # cut sits between p-1 and p
            e = p                        # keep scanning the earlier half
    return sorted(set(cuts) | set(extra))


def shots(vid):
    """-> fps, duration, nframes, [(start, end_exclusive)], metrics, small_frames"""
    fps, dur = probe(vid)
    fr = load_small(vid)
    m = metrics_from(fr)
    n = len(fr)
    hard, flashes = detect(fr, m)
    cuts = add_dissolves(fr, hard, flashes, n)
    starts = [0] + [c + 1 for c in cuts]
    bounds = [(s, starts[i + 1] if i + 1 < len(starts) else n)
              for i, s in enumerate(starts)]
    return fps, dur, n, bounds, m, fr


def pick_frames(fr, bounds):
    """Choose which frame of each shot to grab as the still.

    A few frames in, so it is real content rather than the motion-blurred or
    half-dissolved frame sitting on the boundary -- and never a frame the edit
    has blown out to white or black.
    """
    std = fr.astype(np.float32).std(axis=(1, 2, 3))
    picks = []
    for s, e in bounds:
        p = s + min(5, max(0, (e - s - 1) // 3))
        if std[p] < FLASH_STD:
            alt = [q for q in range(s, e) if std[q] >= FLASH_STD]
            if alt:
                p = min(alt, key=lambda q: abs(q - p))
        picks.append(int(p))
    return picks


def qc_flags(fps, bounds, m, fr):
    """Shots worth eyeballing: too short, or changing a lot inside themselves.

    'drift' and 'spike' are how a missed cut shows up; 'short' is how a false
    positive shows up. Most flags are just heavy motion -- they are a review
    queue, not errors.
    """
    flags = []
    for k, (s, e) in enumerate(bounds):
        seg = m[s:e - 1] if e - 1 > s else np.array([0.0])
        internal_max = float(seg.max()) if len(seg) else 0.0
        endpoint = float(np.abs(fr[s].astype(np.float32) -
                                fr[e - 1].astype(np.float32)).mean())
        dur = (e - s) / fps
        why = []
        if dur < 0.5:
            why.append("short")
        if endpoint > 28 and dur > 0.6:
            why.append(f"drift{endpoint:.0f}")
        if internal_max > 12 and dur > 0.6:
            why.append(f"spike{internal_max:.0f}")
        if why:
            flags.append({"cut": k, "t": round(s / fps, 3),
                          "dur": round(dur, 2), "why": ",".join(why)})
    return flags
