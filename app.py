"""Cold Tires — a tiny iRacing overlay that flashes a warning on your out lap.

Arms when you leave pit road, disarms when the coldest tire reaches the
threshold, the out lap is completed, or you return to the pits. Shows a
brief green "TIRES READY" when the tires come up to temperature.

Modes:
  (default)   run the overlay (click-through, always on top)
  --setup     draggable window; drag it where you want, close to save
  --demo      fake telemetry (pit exit + warming tires) to preview the UI
"""
import ctypes
import json
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
    'threshold_c': 60,      # coldest tire must reach this to count as warm
    'disarm_laps': 1,       # give up warning after this many completed laps
    'poll_hz': 4,
    'flash_hz': 2,
    'show_temps': True,     # append the coldest tire temp to the banner
}

def load_config():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text('utf-8')))
    except Exception:
        pass
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
            'coldest': min(temps) if len(temps) == 4 else None,
        }

class DemoTelemetry:
    """Scripted session: 4s in pits -> exit -> tires warm 30->75C over 20s."""
    def __init__(self):
        self.t0 = time.time()

    def poll(self):
        t = (time.time() - self.t0) % 32
        on_pit = t < 4
        warm = min(75.0, 30.0 + max(0.0, t - 4) * 2.25)
        return {
            'on_pit': on_pit,
            'on_track': True,
            'lap': 1,
            'coldest': warm,
        }

# ---------------------------------------------------------------- overlay

TRANS = '#010101'   # transparency key color — anything painted this is see-through
COLD_A = '#c81e2e'  # flash phase A (red)
COLD_B = '#1e50c8'  # flash phase B (blue)
READY = '#1c9e50'   # disarm confirmation (green)
W, H, R = 340, 64, 16

class Overlay:
    def __init__(self, cfg, telemetry, setup_mode=False):
        self.cfg = cfg
        self.tel = telemetry
        self.setup = setup_mode

        self.armed = False
        self.arm_lap = None
        self.ready_until = 0     # show green confirmation until this time

        self.root = tk.Tk()
        self.root.title('Cold Tires')
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.attributes('-transparentcolor', TRANS)
        self.root.geometry(f'{W}x{H}+{cfg["x"]}+{cfg["y"]}')
        self.canvas = tk.Canvas(self.root, width=W, height=H, bg=TRANS,
                                highlightthickness=0)
        self.canvas.pack()

        if setup_mode:
            self._enable_drag()
        else:
            self.root.after(50, self._click_through)

        self.root.after(0, self._tick)

    # -- window plumbing ------------------------------------------------

    def _click_through(self):
        """WS_EX_TRANSPARENT: clicks pass through to the sim underneath.
        WS_EX_TOOLWINDOW keeps it out of the taskbar and Alt-Tab."""
        GWL_EXSTYLE = -20
        WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x80000, 0x20, 0x80
        hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE,
            style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)

    def _enable_drag(self):
        drag = {}
        def down(e): drag.update(x=e.x, y=e.y)
        def move(e):
            self.root.geometry(f'+{e.x_root - drag["x"]}+{e.y_root - drag["y"]}')
        def save_and_quit(_=None):
            self.cfg['x'] = self.root.winfo_x()
            self.cfg['y'] = self.root.winfo_y()
            save_config(self.cfg)
            self.root.destroy()
        self.canvas.bind('<Button-1>', down)
        self.canvas.bind('<B1-Motion>', move)
        self.root.bind('<Escape>', save_and_quit)
        self.root.protocol('WM_DELETE_WINDOW', save_and_quit)

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
            self.ready_until = 0

        if not self.armed:
            return
        if s['on_pit']:                            # back in — cancel quietly
            self.armed = False
        elif s['coldest'] is not None and s['coldest'] >= self.cfg['threshold_c']:
            self.armed = False                     # up to temp — confirm
            self.ready_until = now + 2.5
        elif s['lap'] >= (self.arm_lap or 0) + self.cfg['disarm_laps']:
            self.armed = False                     # out lap done — stop nagging

    # -- drawing ---------------------------------------------------------

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
        if self.setup:
            self._banner(COLD_B, 'DRAG ME · CLOSE TO SAVE')
            return
        if self.armed:
            phase = int(now * self.cfg['flash_hz'] * 2) % 2
            label = '❄ COLD TIRES'
            if self.cfg['show_temps'] and s and s['coldest'] is not None:
                label += f'  ·  {s["coldest"]:.0f}°C'
            self._banner(COLD_A if phase else COLD_B, label)
        elif now < self.ready_until:
            self._banner(READY, '✔ TIRES READY')
        else:
            self.canvas.delete('all')

    def _tick(self):
        try:
            s = self.tel.poll()
        except Exception:
            s = None
        self._update_state(s)
        self._draw(s)
        self.root.after(int(1000 / self.cfg['poll_hz']), self._tick)

    def run(self):
        self.root.mainloop()


def main():
    cfg = load_config()
    save_config(cfg)  # materialize defaults next to the exe on first run
    setup = '--setup' in sys.argv
    demo = '--demo' in sys.argv
    tel = DemoTelemetry() if demo else (None if setup else Telemetry())
    if setup:
        class Never:
            def poll(self): return None
        tel = Never()
    Overlay(cfg, tel, setup_mode=setup).run()


if __name__ == '__main__':
    main()
