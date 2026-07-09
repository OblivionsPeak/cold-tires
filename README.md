# Cold Tires

A tiny iRacing overlay that flashes **❄ COLD TIRES** on your out lap, then
shows a green **✔ TIRES READY** the moment the coldest tire reaches
temperature. Nothing else. No dashboard, no setup screens — one EXE.

## Quick start

1. Double-click `cold-tires.exe` — a snowflake appears in the system tray
2. Right-click the snowflake → **Reposition banner**, drag the banner where
   you want it, double-click the banner to save
3. Right-click → **Preview flash (8s)** to see exactly what race day looks like
4. Drive. It arms when you leave pit road and disappears once you're up to temp

Everything lives in the tray menu:

| menu item | what it does |
|---|---|
| **Reposition banner** | banner becomes draggable; double-click it (or untick the menu) to save |
| **Preview flash (8s)** | fake warning so you can judge position/visibility |
| **Units** | Celsius or Fahrenheit for the temp readout |
| **Exit** | cleanly closes the overlay |

## How it works

- **Arms** when you leave pit road (`OnPitRoad` true → false while on track)
- **Disarms** on the first of:
  - driving `warmup_pct` of a lap since pit exit (default 0.75) → green **✔ TIRES READY**
  - the coldest tire carcass temp reaching the threshold → green confirmation
    (only used if the temps are actually updating — see below)
  - completing the out lap (`disarm_laps`, default 1) — quiet hard stop
  - returning to pit road
- **Tire temps:** iRacing only refreshes tire temps in the pit box for most
  cars, so the overlay watches whether they move after pit exit. If they do,
  they drive the disarm and show on the banner; if frozen, the banner shows
  no number and the distance heuristic handles the disarm.
- Overlay is transparent, always-on-top, click-through, and hidden from
  Alt-Tab. iRacing must run **windowed / borderless** for it to show.

## CLI (optional)

```
cold-tires.exe            # run the overlay
cold-tires.exe --setup    # start directly in reposition mode
cold-tires.exe --demo     # scripted fake session to preview the flash
```

## Config

`cold-tires.json` appears next to the EXE on first run. Position and units
are managed from the tray; the rest is for tuning:

| key | default | meaning |
|---|---|---|
| `x`, `y` | 200, 120 | overlay position (tray → Reposition banner) |
| `threshold_c` | 60 | coldest tire must reach this (°C) to count as warm — always Celsius, regardless of display units; only applies when temps stream live |
| `warmup_pct` | 0.75 | fraction of a lap after pit exit until tires count as in |
| `disarm_laps` | 1 | hard stop: give up warning after this many completed laps |
| `poll_hz` | 4 | telemetry polling rate |
| `flash_hz` | 2 | banner flash rate |
| `show_temps` | true | append the coldest temp to the banner |
| `units` | `c` | `c` or `f` — display only (tray → Units) |

Threshold guidance: ~60°C (140°F) suits most slicks coming off cold; wets and
road tires run lower. If the live carcass temps ever read stale for a car,
the lap-based disarm still ends the warning after the out lap.

## Build from source

```
pip install pyirsdk pystray pillow pyinstaller
pyinstaller --onefile --noconsole --name cold-tires app.py
```

(`pystray`/`pillow` are optional at runtime — without them the overlay still
works, minus the tray icon.)

## Dev

`python app.py --demo` previews the full arm → warm → ready cycle on a loop.
