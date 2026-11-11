[README.md](https://github.com/user-attachments/files/34673902/README.md)
# catchclock

**CatchClock! — interval alarms that follow the in-game clock.**

Draw a box around the game clock, and catchclock reads it on screen and rings your alarms (rune timers, creep stacks, anything you repeat every few minutes) on the **real game time**. Pauses, late starts and alt-tabbing don't throw it off.

Built with Dota 2 in mind, but it only looks at the pixels you select, so it can work with any game that shows an `mm:ss` clock.

<!-- Add a screenshot or a short GIF here: docs/screenshot.png -->

## Download

1. Go to the **[Releases](../../releases)** page.
2. Download **`catchclock.exe`**.
3. Double-click it. No installer, nothing else to set up.

> **Windows may show a SmartScreen or antivirus warning** the first time. This is common for small unsigned apps. The full source code is in this repository, so you can read it or build the exe yourself (see below).

## Quick start

1. Run the game in **Windowed** or **Borderless Windowed** mode.
2. Open catchclock and click **CatchClock!**.
3. Drag a box tightly around the game clock, like using the Snipping Tool.
4. Open **Settings** and set up your timers (two are included: every 2 and every 3 minutes).
5. Play. Alarms now follow the game clock.

## Features

- **Syncs to the real game clock.** Reads your selected area about twice a second and locks onto the time shown. Alarm times are game time, not wall-clock time.
- **Multiple interval timers.** Each timer has its own label, interval, an optional "ring early by N seconds", and its **own sound**.
- **Your own sounds.** Use `mp3`, `wav`, `wma` or `m4a` files. No file chosen means a built-in beep.
- **Understands the pre-game countdown.** While the clock runs toward 0:00, alarms stay off. They start the moment the clock runs forward, and the app snaps to the exact game time.
- **Pause aware.** If the game is paused, the alarm clock stops with it.
- **Won't ring on junk.** If the box shows something that isn't a clock, it displays *"This is not a clock, pausing"* and holds all alarms until a real clock is read again.
- **Survives a hidden clock.** If the box goes empty, it keeps counting on its own estimate (up to 15 minutes) and snaps back to the exact time as soon as it can read the clock again.
- **Doesn't ring for the past.** After a pause or a resync, alarms for moments that already passed are skipped.
- **Small always-on-top window.** Drag it anywhere and minimize it to the taskbar (alarms keep running).
- **Private and light.** Everything runs locally. No network access, no account, no extra files: settings are kept in the Windows registry.

## Status indicator

| Status | Meaning |
|---|---|
| **LOCKED** | Reading the clock normally. Alarms are active. |
| **PRE-GAME** | The clock is counting toward 0:00. Alarms are off until it runs forward. |
| **PAUSED** | The game clock has stopped, so alarms are on hold. |
| **ESTIMATING** | The box is empty. Counting on its own estimate. |
| **NOT A CLOCK** | The box shows something that isn't a clock. Alarms are paused. |
| **SEARCHING** | A region is set but no clock has been read yet. |
| **OVERLAP** | This window is covering the clock box. Move it away so it can read the clock. |
| **NO REGION** | Click **CatchClock!** to choose the area to read. |
| **ERROR** | Something is missing (for example the OCR language). The message below tells you what. |

## Settings

| Option | What it does |
|---|---|
| **Every (m:s)** | How often the timer rings. The first ring comes one interval into the game (for example 2:00, 4:00, 6:00 for `2:00`). |
| **Early (s)** | Rings this many seconds before the exact time. |
| **Sound** | Pick a file for this timer, or leave it on the default beep. |
| **Volume** | Master volume for all alarm sounds. |

## Requirements

- Windows 10 (version 2004 or later) or Windows 11
- An OCR language installed in Windows, for example **English**
  (*Settings > Time & language > Language & region*)
- The game in **Windowed** or **Borderless Windowed** mode. Exclusive fullscreen can give a black capture.

## Tips for best results

- Select a **tight box** around just the digits, with as little extra background as possible.
- A larger or higher-contrast game HUD is read more reliably.
- Keep the catchclock window **off the clock box**. If it covers the box, it says OVERLAP and stops reading until you move it.
- If the clock looks wrong now and then, no problem: single bad readings are ignored.

## Troubleshooting

| Problem | Fix |
|---|---|
| `No Windows OCR language: add English pack` | Add English in *Settings > Time & language > Language & region*, then restart the app. |
| Stays on **SEARCHING** | Redo **CatchClock!** and select the digits more tightly. Make sure the game isn't in exclusive fullscreen. |
| Shows **NOT A CLOCK** over the game | The box is probably covering more than the clock, or the clock moved. Redo **CatchClock!**. |
| No sound | Check the sound file format (`mp3`, `wav`, `wma`, `m4a`), use **Test** in Settings, and make sure **Mute** is off. |
| Windows warning on first start | See the note in [Download](#download). |

## Run from source

Requires Python 3.10 or newer.

```bash
pip install mss pillow winocr
python catchclok.pyw
```

## Build the exe yourself

Put `build_exe.bat` next to `catchclok.pyw` and double-click it. The result appears in the `dist` folder.

Or run the commands manually (one line each, works in both PowerShell and CMD):

```powershell
python -m pip install --upgrade pyinstaller mss pillow winocr
```

```powershell
python -m PyInstaller --onefile --noconsole --clean --name catchclock --collect-submodules winrt --hidden-import winrt.windows.media.ocr --hidden-import winrt.windows.globalization --hidden-import winrt.windows.storage.streams --hidden-import winrt.windows.graphics.imaging --hidden-import winrt.windows.foundation --hidden-import winrt.windows.foundation.collections catchclok.pyw
```

## How it works

catchclock takes a screenshot of the area you choose, reads the text with the **built-in Windows OCR engine**, and turns it into game time. A small clock-sync layer ignores misreads, notices pauses and the pre-game countdown, and keeps its own time between readings. Alarms are scheduled on that game time.

It only looks at a screenshot of the selected area. It does not read or change the game's memory or files, and it does not inject anything into the game. You can close it at any time with the **×** button.

## License

MIT. See [LICENSE](LICENSE).

## Credits

Made with the help of [Claude](https://claude.ai).

*catchclock is a fan-made utility and is not affiliated with or endorsed by Valve. Dota 2 is a trademark of Valve Corporation.*
