"""Cut every video in a folder into per-shot screenshots.

  python shotcut.py <video-dir> [-o OUT] [-p PREFIX] [-g GLOB] [--only ep001 ...]

Writes, per video, a folder of stills named <id>_cut000_<duration>s.jpg plus a
shot_list.csv and shots.json covering the whole run. shots.json is what qc.py
reads, and --extract-only reuses it so you can review before committing.

detect() / extract() are the reusable core -- gui.py drives the same two calls
with a progress callback instead of print.
"""
import os, re, sys, json, csv, time, shutil, glob, argparse, subprocess
import shotlib

CACHE_NAME = "shots.json"
CSV_NAME = "shot_list.csv"


def episode_id(path):
    """ep-number out of the filename if there is one, else the whole stem."""
    stem = os.path.splitext(os.path.basename(path))[0]
    m = re.search(r"(?i)(ep\d+[a-z0-9_]*)", stem)
    return m.group(1).lower() if m else re.sub(r"[^0-9A-Za-z가-힣]+", "_", stem).strip("_")


def find_videos(src, pattern="*.mp4"):
    return sorted(glob.glob(os.path.join(src, pattern)))


SAFE_ID = re.compile(r"^[0-9A-Za-z가-힣_\-]+$")


def _checked(info, ep, src):
    """Validate one shots.json entry. Returns (fps, picks, bounds, video name)."""
    if not SAFE_ID.match(str(ep)):
        raise ValueError("에피소드 id에 경로 문자가 있음")
    vid_name = os.path.basename(str(info.get("file", "")))
    if not vid_name or not os.path.isfile(os.path.join(src, vid_name)):
        raise ValueError("영상 파일이 원본 폴더에 없음")
    fps = float(info["fps"])
    if not fps > 0:
        raise ValueError("fps")
    picks = [int(p) for p in info["picks"]]
    bounds = [(int(s), int(e)) for s, e in info["bounds"]]
    if len(picks) != len(bounds) or any(p < 0 for p in picks) or \
            any(not 0 <= s < e for s, e in bounds):
        raise ValueError("picks/bounds")
    return fps, picks, bounds, vid_name


def still_name(tag, k, frames, fps, frames_in_name):
    dur = frames / fps
    return (f"{tag}_cut{k:03d}_{dur:.2f}s_{frames}f.jpg" if frames_in_name
            else f"{tag}_cut{k:03d}_{dur:.1f}s.jpg")


def detect(vids, out_root, only=None, log=print, should_stop=lambda: False):
    """Detect shots in every video; write shots.json. Returns the report dict."""
    os.makedirs(out_root, exist_ok=True)
    rep, t0 = {}, time.time()
    todo = [v for v in vids if not only or episode_id(v) in only]
    for i, v in enumerate(todo):
        if should_stop():
            log("[중단됨]")
            break
        ep = episode_id(v)
        fps, dur, n, bounds, m, fr = shotlib.shots(v)
        rep[ep] = {
            "file": os.path.basename(v), "fps": fps, "dur": dur, "nframes": n,
            "bounds": [[int(s), int(e)] for s, e in bounds],
            "picks": shotlib.pick_frames(fr, bounds),
            "flags": shotlib.qc_flags(fps, bounds, m, fr),
        }
        log(f"{ep}: {len(bounds):4d} shots  avg {dur/len(bounds):5.2f}s  "
            f"min {min((e-s)/fps for s, e in bounds):4.2f}s  "
            f"flags {len(rep[ep]['flags']):3d}   "
            f"[{i+1}/{len(todo)} {time.time()-t0:.0f}s]", i + 1, len(todo))
    with open(os.path.join(out_root, CACHE_NAME), "w", encoding="utf-8") as f:
        json.dump(rep, f)
    log(f"\n검출 완료: {sum(len(r['bounds']) for r in rep.values())}컷 / "
        f"{time.time()-t0:.0f}초")
    return rep


def extract(rep, src, out_root, prefix="", quality=2, frames_in_name=False,
            only=None, log=print, should_stop=lambda: False):
    """Pull the chosen still out of every shot; write shot_list.csv."""
    tmp = os.path.join(out_root, "_tmp_frames")
    rows, t0 = [], time.time()
    todo = [(ep, r) for ep, r in sorted(rep.items()) if not only or ep in only]
    for i, (ep, info) in enumerate(todo):
        if should_stop():
            log("[중단됨]")
            break
        # shots.json may not be ours (shared folder, someone else's zip), so
        # nothing in it is allowed to choose a path: the episode id must be a
        # plain name, the video must sit inside src, and the output folder must
        # resolve to somewhere under out_root.
        try:
            fps, picks, bounds, vid_name = _checked(info, ep, src)
        except ValueError as exc:
            log(f"!! {ep!r}: shots.json 항목이 이상해서 건너뜀 ({exc})")
            continue
        tag = f"{prefix}_{ep}" if prefix else ep
        outdir = os.path.realpath(os.path.join(out_root, tag))
        if not outdir.startswith(os.path.realpath(out_root) + os.sep):
            log(f"!! {ep}: 출력 경로가 저장 위치 밖이라 건너뜀")
            continue
        os.makedirs(outdir, exist_ok=True)
        # shot counts change between runs -- but only clear stills *we* named,
        # never whatever else the user keeps in that folder
        mine = re.compile(r"^" + re.escape(tag) + r"_cut\d{3}_[\d.]+s(_\d+f)?\.jpg$")
        for old in os.listdir(outdir):
            if mine.match(old):
                os.remove(os.path.join(outdir, old))
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        names = []
        for k, (s, e) in enumerate(bounds):
            names.append(still_name(tag, k, e - s, fps, frames_in_name))
            rows.append([ep, k, round(s / fps, 3), round((e - s) / fps, 3),
                         s, e, names[-1]])

        # one decode pass grabs every needed frame, rather than seeking per shot
        expr = "+".join(f"eq(n\\,{p})" for p in picks)
        r = subprocess.run(
            [shotlib.exe("ffmpeg"), "-v", "error", "-i", os.path.join(src, vid_name),
             "-map", "0:v:0", "-vf", f"select='{expr}'", "-vsync", "0",
             "-q:v", str(quality), os.path.join(tmp, "f_%04d.jpg")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            **shotlib.NOWIN)
        got = sorted(os.listdir(tmp))
        if r.returncode != 0 or len(got) != len(picks):
            log(f"!! {ep}: 예상 {len(picks)}장 / 실제 {len(got)}장  {r.stderr[:300]}")
        for f_src, name in zip(got, names):
            shutil.move(os.path.join(tmp, f_src), os.path.join(outdir, name))
        log(f"{ep}: {len(got):3d}장 -> {tag}   [{i+1}/{len(todo)} "
            f"{time.time()-t0:.0f}s]", i + 1, len(todo))
    shutil.rmtree(tmp, ignore_errors=True)

    with open(os.path.join(out_root, CSV_NAME), "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["episode", "cut", "start_sec", "duration_sec",
                    "start_frame", "end_frame", "file"])
        w.writerows(rows)
    log(f"\n완료: {len(rows)}장 / {time.time()-t0:.0f}초 -> {out_root}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video_dir")
    ap.add_argument("-o", "--out", help="output root (default: video dir)")
    ap.add_argument("-p", "--prefix", default="", help="folder/file prefix, e.g. na")
    ap.add_argument("-g", "--glob", default="*.mp4")
    ap.add_argument("-q", "--quality", type=int, default=2, help="ffmpeg -q:v, 2=best")
    ap.add_argument("--frames-in-name", action="store_true",
                    help="name stills <id>_cut000_2.34s_56f.jpg instead of _2.3s.jpg")
    ap.add_argument("--only", nargs="*", help="restrict to these episode ids")
    ap.add_argument("--detect-only", action="store_true")
    ap.add_argument("--extract-only", action="store_true", help="reuse shots.json")
    a = ap.parse_args()

    src = os.path.abspath(a.video_dir)
    out_root = os.path.abspath(a.out) if a.out else src
    os.makedirs(out_root, exist_ok=True)
    cache = os.path.join(out_root, CACHE_NAME)

    vids = find_videos(src, a.glob)
    if not vids:
        sys.exit(f"no videos matching {a.glob} in {src}")

    # the CLI's log() ignores the progress numbers the GUI uses
    def log(msg, *_):
        print(msg, flush=True)

    if a.extract_only:
        if not os.path.exists(cache):
            sys.exit(f"{cache} not found -- run detection first")
        rep = json.load(open(cache, encoding="utf-8"))
    else:
        rep = detect(vids, out_root, only=a.only, log=log)
        if a.detect_only:
            return

    extract(rep, src, out_root, prefix=a.prefix, quality=a.quality,
            frames_in_name=a.frames_in_name, only=a.only, log=log)


if __name__ == "__main__":
    main()
