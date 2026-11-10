"""
catchclock - interval alarms that follow the in-game clock (Dota 2 or any game).

How it works
  1. Click "CatchClock!" and drag a box around the in-game clock (like Snipping Tool).
  2. The app keeps reading that box with OCR and locks its own clock to the game clock.
  3. Alarms fire on the GAME clock, so pauses, rewinds and late starts stay in sync.
  4. If the box turns EMPTY (no text at all) the app keeps counting on its own estimate, and
     snaps back to the exact game clock as soon as it can read it again.
  5. If the box holds something that is not a clock, it says "This is not a clock, pausing" and
     alarms pause until a real clock is read again.
  6. Pre-game countdown (clock running toward 0:00): alarms are OFF. They start the moment the
     clock runs forward, and the app snaps to the exact game time right away.

The "-" button minimizes it to the taskbar; alarms keep running while minimized.
Game must run in Windowed or Borderless Windowed mode.
OCR is Windows' own (needs an OCR language such as English: Settings > Time & language).
Sounds: mp3 / wav / wma / m4a, played by Windows itself (no extra packages).

Install:  pip install mss pillow winocr
Run:      pythonw catchclok.pyw      (or double-click it)
"""

import ctypes
import json
import math
import os
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog

# ----------------------------------------------------------------------------
# Windows helpers
# ----------------------------------------------------------------------------
IS_WIN = sys.platform.startswith("win")


def enable_dpi_awareness():
    """Make Tk coordinates equal real screen pixels (needed for correct capture)."""
    if not IS_WIN:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def exclude_from_capture(tk_window):
    """Hide our own window from screen capture, so it never reads itself."""
    if not IS_WIN:
        return
    try:
        tk_window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(tk_window.winfo_id()) or tk_window.winfo_id()
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11)  # WDA_EXCLUDEFROMCAPTURE
    except Exception:
        pass


def virtual_screen():
    """x, y, w, h of the whole desktop (all monitors)."""
    if IS_WIN:
        u = ctypes.windll.user32
        return u.GetSystemMetrics(76), u.GetSystemMetrics(77), u.GetSystemMetrics(78), u.GetSystemMetrics(79)
    return 0, 0, 1920, 1080


# ----------------------------------------------------------------------------
# Settings (stored in the Windows registry - no extra files next to the app)
# ----------------------------------------------------------------------------
REG_PATH = r"Software\catchclock"

DEFAULTS = {
    "region": None,            # [left, top, width, height] in screen pixels
    "pos": [60, 60],           # overlay position
    "volume": 0.8,
    "idle_alpha": 0.2,
    "fade": False,             # see-through when the mouse is away (off = always sharp)
    "scan_ms": 400,
    "hold_min": 15,            # keep counting this long while the clock is not readable
    "timers": [
        {"label": "2 min", "interval": 120, "start": 120, "lead": 0, "sound": "", "on": True},
        {"label": "3 min", "interval": 180, "start": 180, "lead": 0, "sound": "", "on": True},
    ],
}


def load_settings():
    data = json.loads(json.dumps(DEFAULTS))
    if not IS_WIN:
        return data
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH) as k:
            raw, _ = winreg.QueryValueEx(k, "settings")
        saved = json.loads(raw)
        for key in DEFAULTS:
            if key in saved:
                data[key] = saved[key]
    except Exception:
        pass
    data["fade"] = False          # no options besides volume: these are fixed
    data["hold_min"] = 15
    for t in data["timers"]:
        t["on"] = True
    return data


def save_settings(data):
    if not IS_WIN:
        return
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH) as k:
            winreg.SetValueEx(k, "settings", 0, winreg.REG_SZ, json.dumps(data))
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Clock parsing
# ----------------------------------------------------------------------------
_CLOCK_RE = re.compile(r"(-)?\s*(\d{1,2})\s*[:;.,]\s*(\d{2})(?:\s*[:;.,]\s*(\d{2}))?")


def parse_clock(text):
    """'12:34' -> 754, '-0:45' -> -45, '1:08:20' -> 4100. None if no clock found."""
    if not text:
        return None
    text = text.replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1")
    m = _CLOCK_RE.search(text)
    if m:
        rest = re.sub(r"\s", "", text[:m.start()] + text[m.end():])
        if len(rest) > 3:            # a sentence that happens to contain "10:30" is not a clock
            return None
        neg, a, b, c = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
        if c is not None:
            hh, mm, ss = a, b, int(c)
            if mm > 59 or ss > 59:
                return None
            total = hh * 3600 + mm * 60 + ss
        else:
            mm, ss = a, b
            if ss > 59:
                return None
            total = mm * 60 + ss
        return -total if neg else total
    # fallback: colon was dropped ("1234" -> 12:34, "045" -> 0:45); text must be only digits
    if not re.fullmatch(r"[\s\-\d:;.,]*", text):
        return None
    digits = re.sub(r"\D", "", text)
    if 3 <= len(digits) <= 4:
        mm, ss = int(digits[:-2]), int(digits[-2:])
        if ss <= 59:
            neg = text.strip().startswith("-")
            total = mm * 60 + ss
            return -total if neg else total
    return None


def fmt_clock(sec):
    if sec is None:
        return "--:--"
    neg = sec < 0
    sec = int(abs(math.floor(sec + 1e-9))) if not neg else int(math.ceil(abs(sec) - 1e-9))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    body = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    return ("-" + body) if neg else body


# ----------------------------------------------------------------------------
# Clock sync: follows OCR readings, ignores misreads, notices pauses
# ----------------------------------------------------------------------------
class ClockSync:
    TOL = 2.0           # readings within this many seconds of the prediction are accepted
    LOCK_AGE = 3.0      # a reading younger than this = LOCKED
    PAUSE_AFTER = 2.5   # same value for this long -> game is paused
    MAX_PREGAME = 300   # a clock running backwards from at most this = pre-game countdown
    NC_AFTER = 2        # this many non-clock readings in a row -> pause

    def __init__(self):
        self.hold = 900.0   # seconds to keep estimating while the clock is unreadable
        self.reset()

    def reset(self):
        self.mode = "idle"          # idle | run | countdown
        self.anchor_game = None
        self.anchor_wall = None
        self.last_ok_wall = None
        self.paused = False
        self.last_val = None
        self.last_change_wall = None
        self.pending = None         # (value, wall, count) for a disputed reading
        self.hist = []              # recent readings [(value, wall)]
        self.cd_value = None        # clock shown during the countdown (negative = before 0:00)
        self.not_clock = False      # region shows something that is not a clock: paused
        self.nc_count = 0

    def _expected(self, now):
        if self.anchor_game is None:
            return None
        if self.paused:
            return self.anchor_game
        return self.anchor_game + (now - self.anchor_wall)

    def _limit(self):
        return max(self.hold, self.LOCK_AGE)

    def _changes(self):
        """Steps between consecutive readings that differ: (direction, plausible, from, to, index).
        'plausible' = the size of the step matches the real time that passed (a running clock)."""
        out = []
        h = self.hist
        for i in range(1, len(h)):
            (pv, pw), (cv, cw) = h[i - 1], h[i]
            if cv == pv:
                continue
            ok = abs(abs(cv - pv) - (cw - pw)) <= 1.5
            out.append(("up" if cv > pv else "down", ok, pv, cv, i))
        return out

    @staticmethod
    def _run_of(changes, direction):
        last = changes[-2:]
        return len(last) == 2 and all(c[0] == direction and c[1] for c in last)

    def feed_nonclock(self, now):
        """The region holds text that is not a clock. After a few in a row: pause everything."""
        self.nc_count += 1
        if self.nc_count >= self.NC_AFTER and not self.not_clock:
            self.reset()                 # forget the old estimate; alarms stop
            self.not_clock = True
            self.nc_count = self.NC_AFTER

    def feed(self, value, now):
        """Give it one OCR reading (seconds, or None)."""
        if value is None:
            return
        self.nc_count = 0
        # clock was hidden for longer than we are willing to guess: start over
        if self.last_ok_wall is not None and now - self.last_ok_wall > self._limit():
            self.reset()

        self.hist.append((value, now))
        del self.hist[:-8]
        ch = self._changes()
        fresh = bool(ch) and ch[-1][4] == len(self.hist) - 1   # newest step involves this reading
        up2 = fresh and self._run_of(ch, "up")
        down2 = fresh and self._run_of(ch, "down")

        if self.mode == "countdown":
            self._feed_countdown(value, now, ch, fresh, up2, down2)
            return

        # Is this the pre-game countdown (clock running toward 0:00)?
        if value < 0:
            if self.mode == "idle" or (len(self.hist) >= 2 and self.hist[-2][0] < 0):
                self._enter_countdown(value, now)
            return                       # a lone negative number mid-game is a misread
        if down2 and value <= self.MAX_PREGAME:
            self._enter_countdown(-value, now)   # the minus sign was not recognised
            return

        exp = self._expected(now)
        if exp is not None and abs(value - exp) <= self.TOL:
            self._set(value, now)        # agrees with our estimate: just refine it
            return

        # The reading disagrees with the estimate. Possible reasons:
        #   * first lock / came back after a long time           -> trust after 2 readings
        #   * the game was paused while the clock was hidden      -> value is a bit BEHIND the
        #     estimate but never behind the last reading           -> trust after 2 readings
        #   * stray text from another app, or a misread           -> value is AHEAD of the
        #     estimate (a game cannot run faster than real time) or BEHIND the last reading
        #     (a clock never goes back). It must then keep advancing like a real clock
        #     for 4 readings before we believe it.
        suspicious = exp is not None and (value > exp + self.TOL or value < self.anchor_game - self.TOL)
        need = 4 if suspicious else 2
        if self.pending:
            pv, pw, cnt = self.pending
            consistent = abs((pv + (now - pw)) - value) <= 1.5
            if suspicious and value == pv:
                consistent = False       # static text is not a running clock
            cnt = cnt + 1 if consistent else 1
        else:
            cnt = 1
        self.pending = (value, now, cnt)
        if cnt >= need:
            self._set(value, now)        # hard snap to exactly what the screen says

    # ---- pre-game countdown ---------------------------------------------------
    def _enter_countdown(self, cd_value, now):
        self.not_clock = False
        self.mode = "countdown"
        self.cd_value = float(cd_value)
        self.last_ok_wall = now
        self.anchor_game = None
        self.anchor_wall = None
        self.paused = False
        self.last_val = None
        self.pending = None

    def _feed_countdown(self, value, now, ch, fresh, up2, down2):
        last = ch[-1] if fresh else None
        if value < 0:                    # minus sign read correctly
            self.cd_value = float(value)
            self.last_ok_wall = now
            return
        turnaround = last is not None and last[0] == "up" and last[1] and last[2] == 0
        if up2 or turnaround:
            # The clock now runs FORWARD: the real game has started.
            self.pending = None
            self.last_val = None
            self._set(value, now)        # snap to exactly what the screen says
        elif down2:                      # still counting down (minus sign lost)
            self.cd_value = -float(value)
            self.last_ok_wall = now
        elif value == 0 and self.cd_value is not None and self.cd_value > -3:
            self.cd_value = 0.0
            self.last_ok_wall = now

    def _set(self, value, now):
        self.not_clock = False           # a trusted clock is back: resume
        self.mode = "run"
        # pause detection only looks at readings we believe
        if value != self.last_val:
            self.last_val = value
            self.last_change_wall = now
            self.paused = False
        elif now - self.last_change_wall >= self.PAUSE_AFTER:
            self.paused = True
        self.anchor_game = float(value)
        self.anchor_wall = now
        self.last_ok_wall = now
        self.pending = None

    def state(self, now):
        """'none' | 'locked' | 'estimating' | 'countdown' | 'notclock' | 'lost'"""
        if self.not_clock:
            return "notclock"
        if self.last_ok_wall is None:
            return "none"
        age = now - self.last_ok_wall
        if self.mode == "countdown":
            return "countdown" if age <= self._limit() else "lost"
        if age <= self.LOCK_AGE:
            return "locked"
        if age <= self._limit():
            return "estimating"
        return "lost"

    def locked(self, now):
        return self.state(now) == "locked"

    def current(self, now):
        """Game time in seconds (estimated while the clock is hidden). None when unknown,
        and None during the pre-game countdown, so no alarm can run."""
        if self.state(now) in ("none", "lost", "countdown", "notclock"):
            return None
        return self._expected(now)


# ----------------------------------------------------------------------------
# Alarm engine: fires on the game clock
# ----------------------------------------------------------------------------
class AlarmEngine:
    LATE = 5.0   # never ring for a boundary that passed more than this many seconds ago

    def __init__(self):
        self.last_k = {}
        self.last_ring = {}

    def forget(self):
        self.last_k.clear()

    @staticmethod
    def _k_now(t, game):
        iv = max(1, int(t["interval"]))
        return math.floor((game + int(t.get("lead", 0)) - int(t.get("start", iv))) / iv)

    def update(self, timers, game, now=None):
        """Return the timers that must ring now. `now` (wall clock) stops double rings
        when an estimate is corrected by a few seconds."""
        fired = []
        if game is None:
            return fired
        for i, t in enumerate(timers):
            if not t.get("on", True):
                continue
            k = self._k_now(t, game)
            prev = self.last_k.get(i)
            if prev is None:
                self.last_k[i] = k       # first reading: don't ring for the past
                continue
            if k > prev:
                boundary = int(t.get("start", t["interval"])) + k * max(1, int(t["interval"])) - int(t.get("lead", 0))
                late = game - boundary
                if k - prev == 1 and k >= 0 and late <= self.LATE:
                    gap = min(15.0, max(1, int(t["interval"])) * 0.5)
                    last = self.last_ring.get(i)
                    if now is None or last is None or now - last >= gap:
                        fired.append(t)
                        self.last_ring[i] = now
                self.last_k[i] = k       # jumps (resync) are swallowed silently
            elif k < prev:
                self.last_k[i] = k       # clock went back (resync)
        return fired

    @staticmethod
    def next_in(timers, game):
        """(label, seconds_left) of the soonest alarm."""
        best = None
        if game is None:
            return None
        for t in timers:
            if not t.get("on", True):
                continue
            iv = max(1, int(t["interval"]))
            k = AlarmEngine._k_now(t, game) + 1
            if k < 0:
                k = 0
            target = int(t.get("start", iv)) + k * iv - int(t.get("lead", 0))
            left = target - game
            if best is None or left < best[1]:
                best = (t.get("label") or fmt_clock(iv), left)
        return best


# ----------------------------------------------------------------------------
# Sound: Windows MCI (built in - no pygame, nothing to compile)
# ----------------------------------------------------------------------------
class Sound:
    def __init__(self):
        self.volume = 0.8
        self.counter = 0
        self.mci = None
        if IS_WIN:
            try:
                from ctypes import wintypes
                fn = ctypes.windll.winmm.mciSendStringW
                fn.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, wintypes.HANDLE]
                fn.restype = wintypes.DWORD
                self.mci = fn
            except Exception:
                self.mci = None

    def play(self, path):
        if self.mci and path and os.path.isfile(path):
            self.counter += 1
            alias = f"dsnd{self.counter}"
            vol = int(max(0.0, min(1.0, self.volume)) * 1000)
            threading.Thread(target=self._run, args=(path, alias, vol), daemon=True).start()
            return
        self.beep()

    def _run(self, path, alias, vol):
        mci = self.mci
        try:
            if mci(f'open "{path}" alias {alias}', None, 0, None) != 0:
                self.beep()              # file Windows cannot open
                return
            mci(f"setaudio {alias} volume to {vol}", None, 0, None)
            mci(f"play {alias} wait", None, 0, None)
        except Exception:
            pass
        finally:
            try:
                mci(f"close {alias}", None, 0, None)
            except Exception:
                pass

    def beep(self):
        def run():
            try:
                if IS_WIN:
                    import winsound
                    for f in (880, 660, 880):
                        winsound.Beep(f, 120)
                else:
                    print("\a", end="", flush=True)
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()


# ----------------------------------------------------------------------------
# OCR: Windows' built-in engine (winocr). RapidOCR is used only if winocr is missing.
# ----------------------------------------------------------------------------
def make_ocr():
    """Return a function  PIL.Image -> text. Raises RuntimeError with a readable message."""
    err = ""
    try:
        import winocr
    except Exception as e:
        winocr = None
        err = str(e)
    if winocr is not None:
        langs = ["en-US", "en", "id-ID", "id"]
        chosen = {}

        def read_with(lang, img):
            res = winocr.recognize_pil_sync(img, lang)
            if isinstance(res, dict):
                return res.get("text", "") or ""
            return getattr(res, "text", "") or ""

        def run(img):
            if "lang" in chosen:
                return read_with(chosen["lang"], img)
            for lang in langs:
                try:
                    text = read_with(lang, img)
                    chosen["lang"] = lang
                    return text
                except Exception:
                    continue
            raise RuntimeError("No Windows OCR language: add English pack")
        return run
    try:
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
        eng = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)

        def run(img):
            res, _ = eng(np.array(img.convert("RGB")))
            return " ".join(r[1] for r in res) if res else ""
        return run
    except Exception:
        pass
    raise RuntimeError(("No OCR: " + err) if err else "No OCR engine - run: pip install winocr")


# ----------------------------------------------------------------------------
# Background reader: screen capture + OCR
# ----------------------------------------------------------------------------
class Reader(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.stop = False
        self.ocr = None
        self.error = None

    @staticmethod
    def _prep(img):
        """Bigger, dark text on a light background, with a margin: what OCR engines like best."""
        from PIL import Image, ImageOps, ImageStat
        g = img.convert("L")
        if g.height < 80:
            s = max(1, math.ceil(80 / g.height))
            g = g.resize((g.width * s, g.height * s), Image.LANCZOS)
        bg = int(ImageStat.Stat(g).median[0])
        if bg < 128:
            g = ImageOps.invert(g)
            bg = 255 - bg
        out = Image.new("L", (g.width + 40, g.height + 40), bg)
        out.paste(g, (20, 20))
        return out.convert("RGB")

    def run(self):
        try:
            import mss
            from PIL import Image
        except Exception as e:
            self.error = f"Missing package: {e}"[:90]
            return
        try:
            self.ocr = make_ocr()
        except Exception as e:
            self.error = str(e)[:90]
            return
        with mss.mss() as sct:
            while not self.stop:
                t0 = time.monotonic()
                region = self.app.cfg["region"]
                if region and not self.app.scan_paused:
                    try:
                        l, t, w, h = region
                        shot = sct.grab({"left": l, "top": t, "width": w, "height": h})
                        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                        text = self.ocr(self._prep(img))
                        value = parse_clock(text)
                        self.error = None
                        self.app.on_reading(img, text, value, time.monotonic())
                    except Exception as e:
                        self.error = (str(e) or type(e).__name__)[:90]
                wait = self.app.cfg["scan_ms"] / 1000.0 - (time.monotonic() - t0)
                time.sleep(max(0.05, wait))


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
BG, BG2, BG3 = "#0d0f12", "#13161b", "#1a1e25"
FG, MUTED, ACCENT, DANGER, WARN = "#e8eaf0", "#6b7280", "#6ee7b7", "#f87171", "#fbbf24"
BLUE = "#60a5fa"
SANS, MONO = "Segoe UI", "Consolas"


def flat_button(parent, text, cmd, fg=FG, bg=BG3, pad=(8, 3), font=(SANS, 9)):
    b = tk.Label(parent, text=text, fg=fg, bg=bg, font=font, padx=pad[0], pady=pad[1], cursor="hand2")
    b.bind("<Button-1>", lambda e: cmd())
    b.bind("<Enter>", lambda e: b.config(bg="#262b34"))
    b.bind("<Leave>", lambda e: b.config(bg=bg))
    return b


class RegionPicker:
    """Full-desktop dim layer; drag a rectangle, release to confirm. Esc cancels."""

    def __init__(self, root, on_done, on_close):
        self.on_done = on_done
        self.on_close = on_close
        x, y, w, h = virtual_screen()
        self.ox, self.oy = x, y
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.30)
        self.win.configure(bg="black", cursor="crosshair")
        self.cv = tk.Canvas(self.win, bg="black", highlightthickness=0)
        self.cv.pack(fill="both", expand=True)
        self.cv.create_text(w // 2, 40,
                            text="Drag a box around the game clock   -   Esc to cancel",
                            fill="white", font=(SANS, 16))
        self.start = None
        self.rect = None
        self.cv.bind("<ButtonPress-1>", self.down)
        self.cv.bind("<B1-Motion>", self.move)
        self.cv.bind("<ButtonRelease-1>", self.up)
        self.win.bind("<Escape>", lambda e: self.finish(None))
        self.win.focus_force()

    def finish(self, region):
        self.win.destroy()
        self.on_close()
        if region:
            self.on_done(region)

    def down(self, e):
        self.start = (e.x, e.y)
        self.rect = self.cv.create_rectangle(e.x, e.y, e.x, e.y, outline=ACCENT, width=2)

    def move(self, e):
        if self.start:
            self.cv.coords(self.rect, self.start[0], self.start[1], e.x, e.y)

    def up(self, e):
        if not self.start:
            return
        x0, y0 = self.start
        x1, y1 = e.x, e.y
        left, top = min(x0, x1), min(y0, y1)
        w, h = abs(x1 - x0), abs(y1 - y0)
        if w >= 8 and h >= 6:
            self.finish([left + self.ox, top + self.oy, w, h])
        else:
            self.finish(None)


class SettingsWindow:
    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title("catchclock settings")
        self.win.configure(bg=BG)
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        exclude_from_capture(self.win)
        self.rows = []

        top = tk.Frame(self.win, bg=BG)
        top.pack(fill="x", padx=16, pady=(14, 6))
        tk.Label(top, text="INTERVAL TIMERS", fg=MUTED, bg=BG, font=(SANS, 9, "bold")).pack(side="left")
        flat_button(top, "+ Add timer", self.add_timer, fg=ACCENT).pack(side="right")

        head = tk.Frame(self.win, bg=BG)
        head.pack(fill="x", padx=16)
        for text, w in (("Label", 14), ("Every (m:s)", 11), ("First at (m:s)", 13),
                        ("Early (s)", 8), ("Sound", 22)):
            tk.Label(head, text=text, fg=MUTED, bg=BG, font=(SANS, 8), width=w, anchor="w").pack(side="left")

        self.list = tk.Frame(self.win, bg=BG)
        self.list.pack(fill="both", expand=True, padx=16, pady=4)

        bottom = tk.Frame(self.win, bg=BG2)
        bottom.pack(fill="x", padx=16, pady=(4, 14))
        self.vol = tk.Scale(bottom, from_=0, to=100, orient="horizontal", length=150, bg=BG2, fg=FG,
                            troughcolor=BG3, highlightthickness=0, label="Volume",
                            command=self.on_vol, font=(SANS, 8))
        self.vol.set(int(app.cfg["volume"] * 100))
        self.vol.pack(side="left", padx=10, pady=8)
        flat_button(bottom, "Test beep", lambda: app.sound.play(""), pad=(12, 6)).pack(side="right", padx=10)
        self.build()
        self.fit()

    def build(self):
        for w in self.list.winfo_children():
            w.destroy()
        self.rows = []
        for i, t in enumerate(self.app.cfg["timers"]):
            self.row(i, t)

    def fit(self):
        self.win.update_idletasks()
        sc = self.app.scale
        w = max(int(900 * sc), self.win.winfo_reqwidth() + 20)
        h = max(int(520 * sc), self.win.winfo_reqheight() + 20)
        self.win.geometry(f"{w}x{h}")

    def row(self, i, t):
        f = tk.Frame(self.list, bg=BG2)
        f.pack(fill="x", pady=3, ipady=4)
        e_label = self.entry(f, t.get("label", ""), 14, text=True)
        e_im = self.entry(f, str(t["interval"] // 60), 3)
        tk.Label(f, text=":", fg=MUTED, bg=BG2).pack(side="left")
        e_is = self.entry(f, f"{t['interval'] % 60:02d}", 3)
        tk.Label(f, text="  ", bg=BG2).pack(side="left")
        st = int(t.get("start", t["interval"]))
        e_sm = self.entry(f, str(st // 60), 3)
        tk.Label(f, text=":", fg=MUTED, bg=BG2).pack(side="left")
        e_ss = self.entry(f, f"{st % 60:02d}", 3)
        tk.Label(f, text="  ", bg=BG2).pack(side="left")
        e_lead = self.entry(f, str(t.get("lead", 0)), 4)
        name = os.path.basename(t.get("sound", "")) or "default beep"
        lab = tk.Label(f, text=name[:22], fg=ACCENT if t.get("sound") else MUTED, bg=BG2,
                       font=(SANS, 9), width=20, anchor="w")
        lab.pack(side="left", padx=(10, 4))
        flat_button(f, "Browse", lambda i=i: self.browse(i)).pack(side="left", padx=2)
        flat_button(f, "Test", lambda i=i: self.app.sound.play(self.app.cfg["timers"][i].get("sound", ""))).pack(side="left", padx=2)
        flat_button(f, "Delete", lambda i=i: self.delete(i), fg=DANGER).pack(side="right", padx=6)
        self.rows.append(dict(label=e_label, im=e_im, is_=e_is, sm=e_sm, ss=e_ss, lead=e_lead))
        for e in (e_label, e_im, e_is, e_sm, e_ss, e_lead):
            e.bind("<FocusOut>", lambda ev: self.commit())
            e.bind("<Return>", lambda ev: self.commit())

    def entry(self, parent, value, width, text=False):
        e = tk.Entry(parent, width=width, bg=BG3, fg=FG, insertbackground=FG, relief="flat",
                     font=(SANS if text else MONO, 10), justify="left" if text else "center")
        e.insert(0, value)
        e.pack(side="left", padx=2, ipady=3)
        return e

    @staticmethod
    def _int(s, default=0):
        try:
            return max(0, int(s))
        except Exception:
            return default

    def commit(self):
        try:
            timers = self.app.cfg["timers"]
            for t, r in zip(timers, self.rows):
                iv = self._int(r["im"].get()) * 60 + self._int(r["is_"].get())
                t["interval"] = max(1, iv)
                t["start"] = self._int(r["sm"].get()) * 60 + self._int(r["ss"].get())
                t["lead"] = self._int(r["lead"].get())
                t["label"] = r["label"].get().strip() or fmt_clock(t["interval"])
                t["on"] = True
        except tk.TclError:
            return  # widgets already destroyed
        self.app.engine.forget()
        self.app.persist()

    def add_timer(self):
        self.commit()
        self.app.cfg["timers"].append({"label": "New", "interval": 60, "start": 60, "lead": 0, "sound": "", "on": True})
        self.build()
        self.app.persist()

    def delete(self, i):
        self.commit()
        del self.app.cfg["timers"][i]
        self.app.engine.forget()
        self.build()
        self.app.persist()

    def browse(self, i):
        self.commit()
        path = filedialog.askopenfilename(
            parent=self.win, title="Pick a sound",
            filetypes=[("Audio", "*.mp3 *.wav *.wma *.m4a"), ("All files", "*.*")])
        if path:
            self.app.cfg["timers"][i]["sound"] = path
            self.app.persist()
            self.build()

    def on_vol(self, v):
        self.app.cfg["volume"] = int(v) / 100
        self.app.sound.volume = self.app.cfg["volume"]
        self.app.persist()

    def close(self):
        self.commit()
        self.app.settings = None
        self.win.destroy()


class App:
    BASE_W, BASE_H = 340, 170   # at 100% Windows scaling; the real size adapts to the screen

    def __init__(self):
        enable_dpi_awareness()
        self.cfg = load_settings()
        self.root = tk.Tk()
        self.root.title("catchclock")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)
        # Windows display scaling (125%, 150%...) makes fonts bigger, so the window must grow too
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        self.W, self.H = int(self.BASE_W * self.scale), int(self.BASE_H * self.scale)
        self.alpha_now = 1.0
        self.minimized = False
        self.minimized_at = 0.0
        self.restore_pos = None
        exclude_from_capture(self.root)

        self.sync = ClockSync()
        self.engine = AlarmEngine()
        self.sound = Sound()
        self.sound.volume = self.cfg["volume"]
        self.scan_paused = False
        self.muted = False
        self.settings = None
        self.lock = threading.Lock()
        self.latest = {"img": None, "text": "", "value": None}
        self.photo = None
        self.drag = None
        self.picking = False

        self.build_ui()
        self.root.update_idletasks()
        # never smaller than what the widgets really need, so nothing is cut off
        self.W = max(self.W, self.top_bar.winfo_reqwidth() + 12)
        self.H = max(self.H, self.root.winfo_reqheight())
        self.root.geometry(f"{self.W}x{self.H}+{self.cfg['pos'][0]}+{self.cfg['pos'][1]}")
        self.nextl.config(wraplength=self.W - 24)
        self.preview.config(wraplength=self.W - 24)
        self.root.bind("<Map>", self.on_map)
        self.reader = Reader(self)
        self.reader.start()
        self.tick()

    # ---- layout -------------------------------------------------------------
    def build_ui(self):
        r = self.root
        top = tk.Frame(r, bg=BG2)
        top.pack(fill="x")
        self.top_bar = top
        # Buttons are placed FIRST so a long status text can never push them out of the window.
        flat_button(top, "×", self.quit, fg=DANGER, bg=BG2, pad=(8, 0), font=(SANS, 13)).pack(side="right")
        flat_button(top, "–", self.minimize, bg=BG2, pad=(8, 0), font=(SANS, 13)).pack(side="right")
        flat_button(top, "Settings", self.open_settings, bg=BG2, pad=(8, 2)).pack(side="right")
        self.mute_btn = flat_button(top, "Mute", self.toggle_mute, bg=BG2, pad=(8, 2))
        self.mute_btn.config(width=6)           # same width for "Mute" and "Muted"
        self.mute_btn.pack(side="right")
        flat_button(top, "CatchClock!", self.pick_region, fg=ACCENT, bg=BG2, pad=(8, 2)).pack(side="right")
        self.dot = tk.Label(top, text="●", fg=MUTED, bg=BG2, font=(SANS, 9))
        self.dot.pack(side="left", padx=(8, 2), pady=4)
        # fixed width: the bar never changes size when the status text changes
        self.status = tk.Label(top, text="NO REGION", fg=MUTED, bg=BG2, font=(SANS, 8, "bold"),
                               width=11, anchor="w")
        self.status.pack(side="left")

        body = tk.Frame(r, bg=BG)
        body.pack(fill="both", expand=True)
        self.pv_h = int(46 * self.scale)
        pv = tk.Frame(body, bg=BG, height=self.pv_h + 8)   # fixed height: layout never jumps
        pv.pack_propagate(False)
        pv.pack(fill="x", padx=8, pady=(8, 0))
        self.preview = tk.Label(pv, bg=BG, text="Click CatchClock! and box the game clock", fg=MUTED,
                                font=(SANS, 9), justify="center")
        self.preview.pack(expand=True)
        self.clock = tk.Label(body, text="--:--", fg=FG, bg=BG, font=(MONO, 30))
        self.clock.pack(pady=(0, 0))
        self.nextl = tk.Label(body, text="", fg=MUTED, bg=BG, font=(SANS, 9), justify="center", height=2)
        self.nextl.pack(pady=(0, 4))

        for w in (top, self.dot, self.status, body, pv, self.preview, self.clock, self.nextl):
            w.bind("<ButtonPress-1>", self.drag_start)
            w.bind("<B1-Motion>", self.drag_move)
            w.bind("<ButtonRelease-1>", self.drag_end)

    def drag_start(self, e):
        self.drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def drag_move(self, e):
        if self.drag:
            self.root.geometry(f"+{e.x_root - self.drag[0]}+{e.y_root - self.drag[1]}")

    def drag_end(self, e):
        self.drag = None
        self.cfg["pos"] = [self.root.winfo_x(), self.root.winfo_y()]
        self.persist()

    # ---- actions --------------------------------------------------------------
    def persist(self):
        save_settings(self.cfg)

    def pick_region(self):
        if self.picking:
            return
        self.picking = True
        self.root.withdraw()
        self.root.after(150, lambda: RegionPicker(self.root, self.region_done, self.picker_closed))

    def picker_closed(self):
        self.picking = False
        self.root.deiconify()
        self.root.attributes("-topmost", True)

    def region_done(self, region):
        self.cfg["region"] = region
        self.sync.reset()
        self.engine.forget()
        self.persist()

    def minimize(self):
        """A frameless window has no taskbar button, so give it a frame just while minimized."""
        self.restore_pos = (self.root.winfo_x(), self.root.winfo_y())
        self.minimized = True
        self.minimized_at = time.monotonic()
        self.root.overrideredirect(False)
        self.root.update_idletasks()
        self.root.iconify()

    def on_map(self, e):
        # fired when the window is shown again from the taskbar
        if e.widget is not self.root or not self.minimized:
            return
        if time.monotonic() - self.minimized_at < 0.8:
            return                        # the remap caused by minimize() itself
        self.minimized = False
        self.root.after(40, self.restore_frameless)

    def restore_frameless(self):
        if self.root.state() != "normal":
            self.minimized = True
            return
        self.root.overrideredirect(True)
        x, y = self.restore_pos or tuple(self.cfg["pos"])
        self.root.geometry(f"{self.W}x{self.H}+{x}+{y}")
        self.root.attributes("-topmost", True)
        exclude_from_capture(self.root)

    def toggle_mute(self):
        self.muted = not self.muted
        self.mute_btn.config(text="Muted" if self.muted else "Mute", fg=WARN if self.muted else FG)

    def open_settings(self):
        if self.settings is None:
            self.settings = SettingsWindow(self)

    def quit(self):
        self.reader.stop = True
        self.persist()
        self.root.destroy()

    # ---- data from reader thread ------------------------------------------------
    def on_reading(self, img, text, value, now):
        with self.lock:
            self.latest = {"img": img, "text": text, "value": value}
            if value is not None:
                self.sync.feed(value, now)
            elif text.strip():
                self.sync.feed_nonclock(now)   # something is there, but it is not a clock

    # ---- main loop (UI thread) -----------------------------------------------------
    def tick(self):
        now = time.monotonic()
        try:
            self.update_alpha()
            with self.lock:
                self.sync.hold = self.cfg.get("hold_min", 15) * 60
                state = self.sync.state(now)
                game = self.sync.current(now)
                paused = self.sync.paused
                cd = self.sync.cd_value
                img = self.latest["img"]
            if game is not None:
                for t in self.engine.update(self.cfg["timers"], game, now):
                    if not self.muted:
                        self.sound.play(t.get("sound", ""))
            else:
                self.engine.forget()
            self.paint(game, state, paused, img, cd)
        except Exception:
            pass
        self.root.after(100, self.tick)

    def update_alpha(self):
        if not self.cfg.get("fade", False):
            if self.alpha_now != 1.0:     # fading was just switched off
                self.alpha_now = 1.0
                self.root.attributes("-alpha", 1.0)
            return
        px, py = self.root.winfo_pointerxy()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        inside = x <= px <= x + self.root.winfo_width() and y <= py <= y + self.root.winfo_height()
        target = 1.0 if (inside or self.settings or self.drag) else self.cfg["idle_alpha"]
        self.alpha_now += (target - self.alpha_now) * 0.4
        if abs(target - self.alpha_now) < 0.01:
            self.alpha_now = target
        self.root.attributes("-alpha", self.alpha_now)

    def paint(self, game, state, paused, img, cd=None):
        if self.reader.error and game is None:
            self.dot.config(fg=DANGER)
            self.status.config(text="ERROR", fg=DANGER)
            self.nextl.config(text=self.reader.error[:90], fg=DANGER)
            return
        self.nextl.config(fg=MUTED)
        if state == "notclock":
            self.dot.config(fg=WARN)
            self.status.config(text="NOT A CLOCK", fg=WARN)
            self.clock.config(text="--:--", fg=WARN)
            self.nextl.config(text="This is not a clock, pausing", fg=WARN)
            self.paint_preview(img)
            return
        if state == "countdown":
            self.dot.config(fg=BLUE)
            self.status.config(text="PRE-GAME", fg=BLUE)
            self.clock.config(text=fmt_clock(cd), fg=BLUE)
            self.nextl.config(text="Alarms start when the clock runs forward")
            self.paint_preview(img)
            return
        if not self.cfg["region"]:
            self.dot.config(fg=MUTED)
            self.status.config(text="NO REGION", fg=MUTED)
        elif game is None:
            self.dot.config(fg=WARN)
            self.status.config(text="SEARCHING", fg=WARN)
        elif state == "estimating":
            self.dot.config(fg=WARN)
            self.status.config(text="ESTIMATING", fg=WARN)
        elif paused:
            self.dot.config(fg=WARN)
            self.status.config(text="PAUSED", fg=WARN)
        else:
            self.dot.config(fg=ACCENT)
            self.status.config(text="LOCKED", fg=ACCENT)

        shown = fmt_clock(game)
        if state == "estimating":
            shown = "~" + shown
        self.clock.config(text=shown, fg=WARN if state == "estimating" else (ACCENT if game is not None else FG))
        nxt = AlarmEngine.next_in(self.cfg["timers"], game)
        if nxt:
            self.nextl.config(text=f"Next: {nxt[0]}  in  {fmt_clock(max(0, math.ceil(nxt[1])))}")
        elif game is None and self.cfg["region"]:
            self.nextl.config(text="Waiting for a readable clock...")
        else:
            self.nextl.config(text="")

        self.paint_preview(img)

    def paint_preview(self, img):
        if img is not None:
            try:
                from PIL import Image, ImageTk
                maxw, maxh = self.W - 16, self.pv_h
                s = min(maxw / img.width, maxh / img.height, 3.0 * self.scale)
                if s >= 1:                       # enlarge in whole steps: sharp pixels, no blur
                    s = max(1, int(s))
                    mode = Image.NEAREST
                else:
                    mode = Image.LANCZOS
                view = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), mode)
                self.photo = ImageTk.PhotoImage(view)
                self.preview.config(image=self.photo, text="")
            except Exception:
                pass

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
