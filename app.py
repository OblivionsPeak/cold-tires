"""Cold Tires — a tiny iRacing overlay that flashes a warning on your out lap.

Arms when you leave pit road, disarms when the coldest tire reaches the
threshold, the out lap is completed, or you return to the pits. Shows a
brief green "TIRES READY" when the tires come up to temperature.

Everything is driven from the system tray icon (snowflake):
  Reposition banner  — drag the banner into place; double-click it when done
  Preview flash      — 8s fake flash so you can judge the position
  Units              — °C / °F
  Exit               — close the overlay

CLI (all optional):
  --setup     start directly in reposition mode
  --demo      fake telemetry (pit exit + warming tires) to preview the UI
"""
import ctypes
import json
import queue
import sys
import time
import tkinter as tk
from pathlib import Path

# ---------------------------------------------------------------- config

def app_dir():
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent

CONFIG_PATH = app_dir() / 'cold-tires.json'

DEFAULTS = {
    'x': 200,               # overlay top-left, screen px
    'y': 120,
    'threshold_c': 60,      # coldest tire must reach this (deg C) to count as warm
    'warmup_pct': 0.75,     # fraction of a lap after pit exit until tires count as in
    'disarm_laps': 1,       # hard stop: give up after this many completed laps
    'poll_hz': 4,
    'flash_hz': 2,
    'show_temps': True,     # append the coldest tire temp to the banner
    'units': 'c',           # 'c' or 'f' — display only; threshold_c stays Celsius
}

def load_config():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text('utf-8')))
    except Exception:
        pass
    if cfg.get('units') not in ('c', 'f'):
        cfg['units'] = 'c'
    return cfg

def save_config(cfg):
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), 'utf-8')
    except Exception:
        pass

# ---------------------------------------------------------------- telemetry

TEMP_VARS = ['LFtempCM', 'RFtempCM', 'LRtempCM', 'RRtempCM']

class Telemetry:
    """Live iRacing telemetry via pyirsdk."""
    def __init__(self):
        import irsdk
        self.ir = irsdk.IRSDK()
        self._connected = False

    def poll(self):
        """Returns dict or None when not in a session."""
        if not self._connected:
            if not self.ir.startup():
                return None
            self._connected = True
        if not (self.ir.is_initialized and self.ir.is_connected):
            self._connected = False
            self.ir.shutdown()
            return None
        temps = []
        for v in TEMP_VARS:
            t = self.ir[v]
            if isinstance(t, (int, float)) and t > 0:
                temps.append(t)
        return {
            'on_pit': bool(self.ir['OnPitRoad']),
            'on_track': bool(self.ir['IsOnTrack']),
            'lap': self.ir['Lap'] or 0,
            'pct': self.ir['LapDistPct'] or 0.0,
            'coldest': min(temps) if len(temps) == 4 else None,
        }

class DemoTelemetry:
    """Scripted session: 4s in pits -> exit -> a lap driven over 24s with
    STATIC temps, exercising the distance-based disarm path (like the sim)."""
    def __init__(self):
        self.t0 = time.time()

    def poll(self):
        t = (time.time() - self.t0) % 32
        on_pit = t < 4
        return {
            'on_pit': on_pit,
            'on_track': True,
            'lap': 1,
            'pct': 0.0 if on_pit else min(0.999, (t - 4) / 24),
            'coldest': 34.0,   # frozen, as live telemetry turned out to be
        }

# ---------------------------------------------------------------- overlay

TRANS = '#010101'   # transparency key color — anything painted this is see-through
COLD_A = '#c81e2e'  # flash phase A (red)
COLD_B = '#1e50c8'  # flash phase B (blue)
READY = '#1c9e50'   # disarm confirmation (green)
W, H, R = 340, 64, 16

class Overlay:
    def __init__(self, cfg, telemetry, start_repositioning=False):
        self.cfg = cfg
        self.tel = telemetry

        self.armed = False
        self.arm_lap = None
        self.ready_until = 0       # show green confirmation until this time
        self.preview_until = 0     # tray "Preview flash" fake warning until this time
        self.repositioning = False
        self.actions = queue.Queue()  # tray thread -> tk thread

        self.root = tk.Tk()
        self.root.title('Cold Tires')
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.attributes('-transparentcolor', TRANS)
        self.root.geometry(f'{W}x{H}+{cfg["x"]}+{cfg["y"]}')
        self.canvas = tk.Canvas(self.root, width=W, height=H, bg=TRANS,
                                highlightthickness=0)
        self.canvas.pack()

        self._drag = {}
        self.canvas.bind('<Button-1>', self._drag_down)
        self.canvas.bind('<B1-Motion>', self._drag_move)
        self.canvas.bind('<Double-Button-1>', lambda e: self.set_repositioning(False))

        self.tray = None
        self.root.after(50, lambda: self.set_repositioning(start_repositioning))
        self.root.after(0, self._tick)

    # -- window plumbing ------------------------------------------------

    def _set_click_through(self, on):
        """WS_EX_TRANSPARENT: clicks pass through to the sim underneath.
        WS_EX_TOOLWINDOW keeps it out of the taskbar and Alt-Tab."""
        GWL_EXSTYLE = -20
        WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x80000, 0x20, 0x80
        hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED | WS_EX_TOOLWINDOW
        if on:
            style |= WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)

    def _drag_down(self, e):
        if self.repositioning:
            self._drag = {'x': e.x, 'y': e.y}

    def _drag_move(self, e):
        if self.repositioning:
            self.root.geometry(f'+{e.x_root - self._drag["x"]}+{e.y_root - self._drag["y"]}')

    # -- tray-driven actions (queued; tk objects are not thread-safe) ----

    def post(self, fn):
        self.actions.put(fn)

    def set_repositioning(self, on):
        was = self.repositioning
        self.repositioning = on
        self._set_click_through(not on)
        # save only when leaving reposition mode — saving on the startup
        # set_repositioning(False) would capture 0,0 before the WM places us
        if was and not on:
            self.cfg['x'] = self.root.winfo_x()
            self.cfg['y'] = self.root.winfo_y()
            save_config(self.cfg)
        if self.tray:
            self.tray.update_menu()

    def set_units(self, units):
        self.cfg['units'] = units
        save_config(self.cfg)
        if self.tray:
            self.tray.update_menu()

    def start_preview(self):
        self.preview_until = time.time() + 8

    def quit(self):
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.root.destroy()

    # -- state machine ---------------------------------------------------

    def _update_state(self, s):
        now = time.time()
        if s is None or not s['on_track']:
            self.armed = False
            self._prev_pit = None
            return
        prev = getattr(self, '_prev_pit', None)
        self._prev_pit = s['on_pit']

        if prev is True and not s['on_pit']:      # pit exit — arm
            self.armed = True
            self.arm_lap = s['lap']
            self.arm_progress = s['lap'] + s['pct']
            self.arm_temp = s['coldest']
            self.temps_live = False                # proven live only if they move
            self.ready_until = 0

        if not self.armed:
            return

        # iRacing only refreshes tire temps in the pit box for most cars —
        # trust them for disarm/display only once they visibly change
        if (not self.temps_live and s['coldest'] is not None
                and self.arm_temp is not None
                and abs(s['coldest'] - self.arm_temp) > 0.5):
            self.temps_live = True

        progress = (s['lap'] + s['pct']) - (self.arm_progress or 0)
        if s['on_pit']:                            # back in — cancel quietly
            self.armed = False
        elif (self.temps_live and s['coldest'] is not None
                and s['coldest'] >= self.cfg['threshold_c']):
            self.armed = False                     # measured up to temp — confirm
            self.ready_until = now + 2.5
        elif progress >= self.cfg['warmup_pct']:
            self.armed = False                     # enough distance driven — confirm
            self.ready_until = now + 2.5
        elif s['lap'] >= (self.arm_lap or 0) + self.cfg['disarm_laps']:
            self.armed = False                     # hard stop — quiet

    # -- drawing ---------------------------------------------------------

    def _fmt_temp(self, c):
        if self.cfg['units'] == 'f':
            return f'{c * 9 / 5 + 32:.0f}°F'
        return f'{c:.0f}°C'

    def _banner(self, color, text):
        c = self.canvas
        c.delete('all')
        x2, y2 = W - 2, H - 2
        c.create_rectangle(2 + R, 2, x2 - R, y2, fill=color, outline='')
        c.create_rectangle(2, 2 + R, x2, y2 - R, fill=color, outline='')
        for cx, cy in ((2 + R, 2 + R), (x2 - R, 2 + R), (2 + R, y2 - R), (x2 - R, y2 - R)):
            c.create_oval(cx - R, cy - R, cx + R, cy + R, fill=color, outline='')
        c.create_text(W / 2, H / 2, text=text, fill='#ffffff',
                      font=('Segoe UI', 17, 'bold'))

    def _draw(self, s):
        now = time.time()
        if self.repositioning:
            self._banner(COLD_B, 'DRAG ME · DOUBLE-CLICK TO SAVE')
            return
        flashing = self.armed or now < self.preview_until
        if flashing:
            phase = int(now * self.cfg['flash_hz'] * 2) % 2
            label = '❄ COLD TIRES'
            if self.armed:
                # only show a temp that's actually updating (most cars don't
                # stream tire temps — a frozen number would be misleading)
                if self.cfg['show_temps'] and self.temps_live and s and s['coldest'] is not None:
                    label += f'  ·  {self._fmt_temp(s["coldest"])}'
            elif self.cfg['show_temps']:
                label += f'  ·  {self._fmt_temp(34.0)}'   # preview sample
            self._banner(COLD_A if phase else COLD_B, label)
        elif now < self.ready_until:
            self._banner(READY, '✔ TIRES READY')
        else:
            self.canvas.delete('all')

    def _tick(self):
        while not self.actions.empty():
            try:
                self.actions.get_nowait()()
            except Exception:
                pass
        try:
            s = self.tel.poll()
        except Exception:
            s = None
        self._update_state(s)
        self._draw(s)
        self.root.after(int(1000 / self.cfg['poll_hz']), self._tick)

    def run(self):
        self.root.mainloop()

# ---------------------------------------------------------------- tray icon

def make_tray(overlay):
    """Snowflake tray icon; menu drives the overlay via the action queue.
    Returns None when pystray is unavailable — the overlay still works."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    import math
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = 32
    for k in range(6):
        a = math.pi / 6 + k * math.pi / 3
        x2, y2 = cx + 24 * math.cos(a), cy + 24 * math.sin(a)
        d.line([cx, cy, x2, y2], fill=(120, 200, 255, 255), width=5)
        for f in (0.55, 0.8):  # side twigs
            bx, by = cx + 24 * f * math.cos(a), cy + 24 * f * math.sin(a)
            for da in (-0.5, 0.5):
                d.line([bx, by, bx + 8 * math.cos(a + da), by + 8 * math.sin(a + da)],
                       fill=(120, 200, 255, 255), width=3)
    d.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill=(120, 200, 255, 255))

    def item(label, fn, **kw):
        return pystray.MenuItem(label, lambda: overlay.post(fn), **kw)

    menu = pystray.Menu(
        item('Reposition banner',
             lambda: overlay.set_repositioning(not overlay.repositioning),
             checked=lambda _: overlay.repositioning),
        item('Preview flash (8s)', overlay.start_preview),
        pystray.MenuItem('Units', pystray.Menu(
            item('Celsius (°C)', lambda: overlay.set_units('c'),
                 radio=True, checked=lambda _: overlay.cfg['units'] == 'c'),
            item('Fahrenheit (°F)', lambda: overlay.set_units('f'),
                 radio=True, checked=lambda _: overlay.cfg['units'] == 'f'),
        )),
        pystray.Menu.SEPARATOR,
        item('Exit', overlay.quit),
    )
    tray = pystray.Icon('cold-tires', img, 'Cold Tires — right-click for options', menu)
    tray.run_detached()
    return tray


def main():
    cfg = load_config()
    save_config(cfg)  # materialize defaults next to the exe on first run
    setup = '--setup' in sys.argv
    demo = '--demo' in sys.argv
    tel = DemoTelemetry() if demo else Telemetry()
    overlay = Overlay(cfg, tel, start_repositioning=setup)
    overlay.tray = make_tray(overlay)
    overlay.run()


if __name__ == '__main__':
    main()
