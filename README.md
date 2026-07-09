# Cold Tires

A tiny iRacing overlay that flashes **❄ COLD TIRES** on your out lap, then
shows a green **✔ TIRES READY** the moment the coldest tire reaches
temperature. Nothing else. No dashboard, no setup screens — one EXE.

## How it works

- **Arms** when you leave pit road (`OnPitRoad` true → false while on track)
- **Disarms** on the first of:
  - the coldest tire carcass temp (`xxTempCM`) reaching the threshold → brief green confirmation
  - completing the out lap (`disarm_laps`, default 1)
  - returning to pit road
- Overlay is transparent, always-on-top, click-through, and hidden from
  Alt-Tab. iRacing must run **windowed / borderless** for it to show.

## Usage

```
cold-tires.exe            # run the overlay
cold-tires.exe --setup    # draggable banner; drag into place, close to save
cold-tires.exe --demo     # scripted fake session to preview the flash
```

**Quitting:** the overlay is click-through and hidden from Alt-Tab, so it has
no close button. Stop it from PowerShell:

```
Stop-Process -Name cold-tires -Force
```

or end **cold-tires.exe** in Task Manager (the onefile EXE shows as two
processes — killing by name gets both).

## Config

`cold-tires.json` appears next to the EXE on first run:

| key | default | meaning |
|---|---|---|
| `x`, `y` | 200, 120 | overlay position (or use `--setup`) |
| `threshold_c` | 60 | coldest tire must reach this (°C) to count as warm |
| `disarm_laps` | 1 | stop warning after this many completed laps |
| `poll_hz` | 4 | telemetry polling rate |
| `flash_hz` | 2 | banner flash rate |
| `show_temps` | true | append the coldest temp to the banner |

Threshold guidance: ~60°C suits most slicks coming off cold; wets and road
tires run lower. If the live carcass temps ever read stale for a car, the
lap-based disarm still ends the warning after the out lap.

## Build from source

```
pip install pyirsdk pyinstaller
pyinstaller --onefile --noconsole --name cold-tires app.py
```

## Dev

`python app.py --demo` previews the full arm → warm → ready cycle on a loop.
