"""Windowed front-end for shotcut -- pick a folder, get per-shot screenshots.

Bundled as a single exe by build.py, with ffmpeg/ffprobe alongside it. Detection
runs on a worker thread; the UI thread only drains a queue, so the window stays
responsive and Stop actually works.
"""
import os, sys, glob, json, queue, threading, traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


def resource_dir():
    """Where the bundled ffmpeg lives: the temp unpack dir when frozen."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


# must happen before shotlib shells out to ffmpeg by bare name
os.environ["PATH"] = (os.path.join(resource_dir(), "bin") + os.pathsep +
                      resource_dir() + os.pathsep + os.environ.get("PATH", ""))

import shotcut  # noqa: E402  (needs the PATH above)

APP = "쇼트 스크린샷 추출기"


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.worker = None
        self.stop_flag = threading.Event()
        root.title(APP)
        root.geometry("760x560")
        root.minsize(680, 480)

        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(root, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        self.src = tk.StringVar()
        self.out = tk.StringVar()
        self.prefix = tk.StringVar()
        self.pattern = tk.StringVar(value="*.mp4")
        self.quality = tk.IntVar(value=2)
        self.frames_in_name = tk.BooleanVar(value=True)
        self.out_touched = False

        r = 0
        ttk.Label(frm, text="영상 폴더").grid(row=r, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.src).grid(row=r, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="찾기…", command=self.pick_src).grid(row=r, column=2, **pad)

        r += 1
        ttk.Label(frm, text="저장 위치").grid(row=r, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.out).grid(row=r, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="찾기…", command=self.pick_out).grid(row=r, column=2, **pad)

        r += 1
        opt = ttk.Frame(frm)
        opt.grid(row=r, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(opt, text="접두어").pack(side="left")
        ttk.Entry(opt, textvariable=self.prefix, width=8).pack(side="left", padx=(4, 14))
        ttk.Label(opt, text="파일 형식").pack(side="left")
        ttk.Entry(opt, textvariable=self.pattern, width=10).pack(side="left", padx=(4, 14))
        ttk.Label(opt, text="화질(1=최고)").pack(side="left")
        ttk.Spinbox(opt, from_=1, to=15, width=4,
                    textvariable=self.quality).pack(side="left", padx=(4, 14))
        ttk.Checkbutton(opt, text="파일명에 프레임 수 병기",
                        variable=self.frames_in_name).pack(side="left")

        r += 1
        btns = ttk.Frame(frm)
        btns.grid(row=r, column=0, columnspan=3, sticky="ew", **pad)
        self.run_btn = ttk.Button(btns, text="실행", command=self.start)
        self.run_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="중지", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.open_btn = ttk.Button(btns, text="결과 폴더 열기", command=self.open_out,
                                   state="disabled")
        self.open_btn.pack(side="left")

        r += 1
        self.bar = ttk.Progressbar(frm, mode="determinate")
        self.bar.grid(row=r, column=0, columnspan=3, sticky="ew", **pad)

        r += 1
        self.status = ttk.Label(frm, text="영상 폴더를 지정하세요.", anchor="w")
        self.status.grid(row=r, column=0, columnspan=3, sticky="ew", **pad)

        r += 1
        frm.rowconfigure(r, weight=1)
        box = ttk.Frame(frm)
        box.grid(row=r, column=0, columnspan=3, sticky="nsew", **pad)
        box.rowconfigure(0, weight=1); box.columnconfigure(0, weight=1)
        self.log = tk.Text(box, height=14, wrap="none", state="disabled",
                           font=("Consolas", 9))
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(box, command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.drain)

    # --- ui helpers ------------------------------------------------------
    def pick_src(self):
        d = filedialog.askdirectory(title="영상이 들어있는 폴더")
        if not d:
            return
        d = os.path.normpath(d)
        self.src.set(d)
        if not self.out_touched:
            self.out.set(os.path.join(os.path.dirname(d) or d, "shots"))
        n = len(glob.glob(os.path.join(d, self.pattern.get())))
        self.status.config(text=f"영상 {n}개를 찾았습니다.")

    def pick_out(self):
        d = filedialog.askdirectory(title="스크린샷을 저장할 폴더")
        if d:
            self.out.set(os.path.normpath(d))
            self.out_touched = True

    def open_out(self):
        if os.path.isdir(self.out.get()):
            os.startfile(self.out.get())

    def write(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # --- run -------------------------------------------------------------
    def start(self):
        src, out = self.src.get().strip(), self.out.get().strip()
        if not os.path.isdir(src):
            messagebox.showerror(APP, "영상 폴더를 지정하세요."); return
        vids = shotcut.find_videos(src, self.pattern.get() or "*.mp4")
        if not vids:
            messagebox.showerror(APP, f"{self.pattern.get()} 에 맞는 영상이 없습니다.")
            return
        if not out:
            messagebox.showerror(APP, "저장 위치를 지정하세요."); return
        if os.path.isdir(out) and os.path.exists(os.path.join(out, shotcut.CACHE_NAME)):
            if not messagebox.askyesno(APP, "저장 위치에 이전 결과가 있습니다.\n"
                                            "같은 이름의 폴더는 덮어씁니다. 계속할까요?"):
                return

        self.stop_flag.clear()
        self.run_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.bar.config(value=0, maximum=len(vids) * 2)
        self.write(f"=== {len(vids)}개 영상 / {src}")
        args = dict(src=src, out=out, vids=vids, prefix=self.prefix.get().strip(),
                    quality=self.quality.get(),
                    frames_in_name=self.frames_in_name.get())
        self.worker = threading.Thread(target=self.job, kwargs=args, daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_flag.set()
        self.status.config(text="중지 요청됨 — 현재 영상을 마치고 멈춥니다…")

    def job(self, src, out, vids, prefix, quality, frames_in_name):
        """Worker thread: never touches widgets, only the queue."""
        total = len(vids)

        def log_detect(msg, done=None, of=None):
            self.q.put(("log", msg))
            if done is not None:
                self.q.put(("bar", done))
                self.q.put(("status", f"검출 {done}/{of}"))

        def log_extract(msg, done=None, of=None):
            self.q.put(("log", msg))
            if done is not None:
                self.q.put(("bar", total + done))
                self.q.put(("status", f"추출 {done}/{of}"))

        try:
            rep = shotcut.detect(vids, out, log=log_detect,
                                 should_stop=self.stop_flag.is_set)
            if rep and not self.stop_flag.is_set():
                shotcut.extract(rep, src, out, prefix=prefix, quality=quality,
                                frames_in_name=frames_in_name, log=log_extract,
                                should_stop=self.stop_flag.is_set)
            self.q.put(("done", "중지했습니다." if self.stop_flag.is_set() else "완료"))
        except Exception:
            self.q.put(("log", traceback.format_exc()))
            self.q.put(("done", "오류로 중단됨 — 로그를 확인하세요."))

    def drain(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.write(val)
                elif kind == "bar":
                    self.bar.config(value=val)
                elif kind == "status":
                    self.status.config(text=val)
                elif kind == "done":
                    self.status.config(text=val)
                    self.run_btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
                    self.open_btn.config(state="normal")
        except queue.Empty:
            pass
        self.root.after(100, self.drain)

    def on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP, "작업 중입니다. 정말 닫을까요?"):
                return
            self.stop_flag.set()
        self.root.destroy()


def main():
    # exe with arguments == the CLI, so the build stays scriptable. A windowed
    # build has no stdout, so send it to a log file rather than crashing print.
    if len(sys.argv) > 1:
        if sys.stdout is None or sys.stderr is None:
            log = open(os.path.join(os.environ.get("TEMP", "."), "shotcut_cli.log"),
                       "w", encoding="utf-8", buffering=1)
            sys.stdout = sys.stderr = log
        shotcut.main()
        return

    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
