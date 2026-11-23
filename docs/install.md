# Install and run

Personal Finance Insights runs on **Windows, macOS and Linux**. It needs Python and nothing else: no database server, no account, no internet after the first install.

**Contents:** [Windows](#windows) · [macOS](#macos) · [Linux](#linux) · [Everyday commands](#everyday-commands) · [Settings](#settings-environment-variables) · [Backups and restore](#backups-and-restore) · [Moving or upgrading](#moving-to-another-computer-or-upgrading) · [Checking it's local only](#checking-that-its-local-only)

## Before you start

You need **Python 3.11 or newer** (3.10 also works). The first start creates a private Python environment in the app's folder (`.venv`), which takes about a minute. After that the app opens in your browser at http://127.0.0.1:8765. To stop it, close the terminal window or press **Ctrl+C** in it.

Get the code with `git clone https://github.com/prasadchawak/PersonalFinanceInsights.git`, or download it as a ZIP from GitHub and unzip it.

## Windows

1. Install Python from [python.org](https://www.python.org/downloads/). During setup, tick **"Add python.exe to PATH"**.
2. Put the folder somewhere, for example `C:\Users\you\PersonalFinanceInsights`.
3. Double-click **`run.bat`**.

## macOS

1. Install Python 3.11 or newer, either with the installer from [python.org](https://www.python.org/downloads/macos/) or with Homebrew: `brew install python@3.12`. The `python3` that comes with Xcode's command line tools is too old.
2. Open **Terminal**, go to the folder, and start the app:
   ```sh
   cd ~/PersonalFinanceInsights
   sh run.sh
   ```
   (`./run.sh` works too. If macOS says the file isn't executable, which can happen after a ZIP download, use `sh run.sh` or run `chmod +x run.sh backup.sh` once.)

## Linux

1. Install Python and its venv module:
   - **Ubuntu / Debian:** `sudo apt install python3 python3-venv`
   - **Fedora:** `sudo dnf install python3`
   - **Arch:** `sudo pacman -S python`
2. In a terminal:
   ```sh
   cd ~/PersonalFinanceInsights
   sh run.sh
   ```
3. If your distribution's Python is older than 3.10, install a newer one and point the script to it: `PYTHON=python3.12 sh run.sh`.

On a machine with no desktop, set `PFI_NO_BROWSER=1` so the app doesn't try to open a browser. It still only accepts connections from that same machine.

## Everyday commands

| | Windows | macOS / Linux |
|---|---|---|
| Start the app | double-click `run.bat` | `sh run.sh` |
| Back up all data | double-click `backup.bat` | `sh backup.sh` |
| Run the app's Python | `.venv\Scripts\python` | `.venv/bin/python` |
| Set a setting for one start | `set PFI_PORT=8766` then `run.bat` | `PFI_PORT=8766 sh run.sh` |
| Where your data is | `data\` | `data/` |

## What the first start does

`run.bat` / `run.sh` create a private Python environment in the `.venv` folder inside the app folder and install the four packages listed in `requirements.txt` (FastAPI, Uvicorn, Jinja2, python-multipart). This is the **only** time anything is downloaded. If that step was interrupted, the scripts notice the next time and rebuild `.venv`. To force a clean reinstall, delete the `.venv` folder.

## Backups and restore

On Windows double-click **`backup.bat`**; on macOS or Linux run **`sh backup.sh`**. It writes a consistent snapshot of **every organisation's database plus the organisation list** to a dated folder in `data/backups/`, and is safe to run while the app is open. Copy those files to an external drive or another place you trust.

To restore, stop the app and copy the backed-up files back into the `data` folder (`orgs.db` to `data/`, `finance.db` to `data/`, and any `orgs/*.db` to `data/orgs/`).

Recommended: turn on full-disk encryption so the database is encrypted at rest if the computer is lost: **BitLocker** (or Device Encryption) on Windows, **FileVault** on macOS, and **LUKS** on Linux (usually chosen when installing the system).

## Settings (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `PFI_PORT` | `8765` | Port to listen on |
| `PFI_DATA_DIR` | `data` in the app folder | Where the database and backups live |
| `PFI_NO_BROWSER` | unset | Set to `1` to stop the app opening the browser on start |

The older `TALLY_*` names still work.

## Moving to another computer or upgrading

- **Moving computers:** stop the app and copy the whole `data` folder into the new copy of the app. Database files work across Windows, macOS and Linux.
- **Upgrading the app:** run a backup, stop the app, replace the code (`git pull`, or unzip the new version over the old one, keeping `data`), and start it again. The database is upgraded in place on the first start; nothing is deleted.
- **Upgrading from Tally:** this app used to be called Tally. On first start, an existing `data/tally.db` is renamed to `data/finance.db` automatically (only when no `finance.db` exists yet), so all your data carries over.

## Checking that it's local only

While the app is running, you can confirm it only listens on this computer's loopback address (`127.0.0.1`):

| System | Command | You should see |
|---|---|---|
| Windows (PowerShell) | `Get-NetTCPConnection -LocalPort 8765 -State Listen` | `LocalAddress 127.0.0.1` |
| macOS | `lsof -nP -iTCP:8765 -sTCP:LISTEN` | `127.0.0.1:8765 (LISTEN)` |
| Linux | `ss -ltnp 'sport = :8765'` | `127.0.0.1:8765` |

If any of these shows `0.0.0.0` or `*:8765`, something else is listening on that port, not this app.

See also the [troubleshooting table](user-guide.md#troubleshooting).
