# d2r-save-sync

Sync **offline** Diablo II: Resurrected saves between a Windows PC and a Steam Deck over the home LAN. The same Python program runs on both machines. One side listens, the other connects, and each file is copied only when its contents actually changed.

Battle.net online characters are stored on Blizzard's servers. This tool cannot sync those. It syncs the local files offline characters use, plus the small local keymap and controller files an online character leaves behind.

The command-line tool uses only the Python standard library. Python 3.9 or newer is enough. The optional window needs [PySide6](#graphical-window).

## What gets synced

Files are read from the top of the save folder only:

| File | Role |
| --- | --- |
| `Character.d2s` | Offline character |
| `Character.key`, `Character.keyo` | Key bindings |
| `Character.ctl`, `Character.ctlo` | Controller layout |
| `Character.ma0`, `Character.ma1`, … and `Character.map` | Automap / map progress |
| `SharedStashSoftCoreV2.d2i`, `SharedStashHardCoreV2.d2i`, other `SharedStash*.d2i` | Offline shared stash |

Left alone on purpose: `.bak` files, editor leftovers, anything inside `.d2r-sync-backups`, and unrelated files such as notes. Symlinks are refused so a save name cannot point at a file outside the folder.

## How a file is chosen

Each machine remembers the SHA-256 of every file at the last successful sync.

- Same bytes on both sides: nothing is copied, even if the timestamps differ.
- One side changed since that shared hash, the other did not: the changed copy wins.
- A file exists on only one side and was never synced: it is copied across.
- A file was deleted on one side and the surviving copy was not edited: the deletion is applied. The deleted bytes are backed up first.
- Both sides changed, or a deletion meets an edit, or two machines both have the file and have never synced: that is a conflict.

Modification time is used only to pick a winner **after** a conflict is detected. A two-second gap is treated as a tie, because FAT, NTFS, and ext4 do not agree about sub-second timestamps. If the clocks on the two machines differ by more than two minutes, the tool warns before using timestamps.

### Conflict policy

Set with `--conflict` or the `conflict` field in the config. The default is `keep-both`.

| Policy | What happens |
| --- | --- |
| `keep-both` | The newer copy becomes the live file on **both** machines. The older copy is written into `.d2r-sync-backups/conflicts/` on both machines. The live folder still has one character and one shared stash, which is what the game loads. |
| `newest` | The newer copy replaces the older one. The replaced file is still backed up under `.d2r-sync-backups/snapshots/`. |
| `ask` | Prompt: local, remote, both, or skip. Skip leaves the two copies different and does not update sync state, so the next run asks again. A dry run does not prompt. If stdin is not a terminal, the run stops instead of guessing. |

On a timestamp tie, `keep-both` and `newest` both keep this machine's bytes as the live file and archive the other copy.

An edit versus a delete keeps the edited file under `keep-both` and `newest`. The deletion is not applied, so a character is not thrown away because the other machine removed an older copy.

## Backups

Before a live save is overwritten or deleted, it is copied to:

```text
<save folder>/.d2r-sync-backups/snapshots/<UTC timestamp>/
<save folder>/.d2r-sync-backups/conflicts/<UTC timestamp>-from-<peer>/
```

One sync shares one snapshot folder. Conflict copies are kept as well as the snapshot, so the losing version is easy to find. The game does not load files inside `.d2r-sync-backups`.

Retention defaults to 20 snapshot folders and 20 conflict folders, and anything older than 30 days is removed. Change `backup_retention_count` and `backup_retention_days` in the config.

To restore: quit the game, copy the file you want back to the save folder under its original name, then sync again. That restored file is a new local edit, so it will be pushed if the other machine has not also changed it.

## Install

Put this repository on both machines. Any folder is fine. Examples used below:

- Windows: `C:\Games\d2r-save-sync`
- Steam Deck: `/home/deck/d2r-save-sync`

You can run it without installing:

```bash
python3 -m d2rsync --help
```

On Windows, `py -3 -m d2rsync --help` does the same thing if the Python launcher is installed.

Optional, if you want a `d2rsync` command on `PATH`:

```bash
pip install .
```

The window is an extra:

```bash
pip install ".[gui]"
python3 -m d2rsync
```

## Graphical window

`python3 -m d2rsync` with no arguments, or `python3 -m d2rsync gui`, opens a window on top of the same sync engine. The command-line subcommands are unchanged. `scripts/d2r-sync.sh` with no arguments still runs `sync`, so an existing Deck shortcut keeps working.

The window is laid out for 1280×800, with large buttons for the Steam Deck touchscreen. Keyboard and Steam Input: Enter activates the focused control, Y or F5 starts a sync from the dashboard, and Escape goes back (from the dashboard it closes the window). A typical Steam Input setup maps A to Enter, B to Escape, and Y to Y.

What the three screens do:

- **Sync** lists each character and the shared stash, with class, level, and the last-modified time on this machine and on the peer. The status badges are Send, Receive, Conflict, and Up to date. **Sync now** connects and syncs. **Dry run** shows what would happen. If D2R is running and blocking is on, the list still loads but the sync button stays disabled.
- **Result** shows the last run. A conflict card explains which copy becomes the live character and which copy is archived. The table lists every other file, the action, and the backup folder.
- **Settings** sets the save folder, host or connect mode, automatic or manual peer discovery, the peer IP and port, the shared secret, how many days of backups to keep, and whether to block while `D2R.exe` is running.

The window never asks a conflict question on stdin. If the config says `newest`, that policy is used. `keep-both` and `ask` both become keep-both in the window, so the older file is archived and nothing is left for a later prompt. `ask` remains available in the CLI.

In **Connect** mode, **Sync now** discovers the peer (or uses the manual IP) and syncs. In **Host** mode the button is **Start host**: this machine listens, and the other one connects. Network and sync work run off the UI thread. The sync button uses the same peer settings as **Test connection**, including a manual IP that has not been saved yet.

A preview while the game is open is read-only, so the dashboard can show the table with the button disabled. An actual sync still refuses.

## First-time setup

Do this once on each machine. The shared secret must be the **same** on both. It never has to be the same as a Battle.net or Steam password. Treat it like a password for the save folder.

**1. Create a config on the first machine**

```bash
python3 -m d2rsync init
```

That writes a config and prints a secret. Config locations:

| OS | Path |
| --- | --- |
| Windows | `%APPDATA%\d2r-save-sync\config.json` |
| Steam Deck / Linux | `~/.config/d2r-save-sync/config.json` |

On Linux the file is mode `0600`.

**2. Put the same secret on the other machine**

Either copy the `secret` value into the other config, or generate it yourself and pass it to both:

```bash
python3 -m d2rsync init --secret "paste-a-long-random-string"
```

`init` will not overwrite an existing config unless you pass `--force`. `--show-secret` prints the secret again. `status` prints only an 8-character fingerprint so you can check that both machines match without displaying the secret.

You can also set `D2R_SYNC_SECRET` in the environment. That overrides the file. Prefer that if you do not want the secret in a command line.

**3. Confirm the save folder**

```bash
python3 -m d2rsync path
```

If more than one folder is found, the tool uses the one whose save files were modified most recently and prints the others. Pin the right one with `save_path` in the config, or pass `--save-path`.

## Windows

Offline saves normally live here, for Battle.net, Steam, and Game Pass alike:

```text
%USERPROFILE%\Saved Games\Diablo II Resurrected
```

Some installs also use `%USERPROFILE%\Documents\Diablo II Resurrected\Save`. Both are detected. `save_path` wins when it is set.

Leave the PC listening. A double-clickable launcher:

```text
scripts\d2r-sync.bat serve
```

Or PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\d2r-sync.ps1 serve
```

The batch and PowerShell launchers pause at the end so a double-clicked window stays open. Set `D2R_SYNC_NO_PAUSE=1` to skip that.

The first time you serve, Windows Defender Firewall usually asks for permission. Allow Python on **private** networks for:

- TCP `41943` (the sync connection)
- UDP `41942` (discovery)

The serve screen prints this PC's LAN address, for example `192.168.1.20:41943`.

## Steam Deck

D2R on the Deck runs through Proton. Saves are inside a prefix, not in a normal Linux home folder. The Steam release uses app id `2536520`:

```text
~/.local/share/Steam/steamapps/compatdata/2536520/pfx/drive_c/users/steamuser/Saved Games/Diablo II Resurrected
```

Battle.net added as a non-Steam game does **not** use that app id. Proton gives it a random number under `compatdata`. This tool scans every prefix, including prefixes on an SD card library, and also checks `Documents/Diablo II Resurrected/Save` because a few Proton setups write there. `libraryfolders.vdf` is read so extra Steam libraries are included. Typical SD card mounts (`/run/media/mmcblk0p1` and `/run/media/deck/...`) are scanned too.

Flatpak Steam (`~/.var/app/com.valvesoftware.Steam`) is included when it is installed. The Deck's own Steam install is the normal `~/.local/share/Steam` path.

If `path` lists two prefixes, play the character once on the Deck and run `path` again, or set `save_path` to the folder that contains your `.d2s` files.

### Desktop Mode

```bash
chmod +x /home/deck/d2r-save-sync/scripts/d2r-sync.sh
/home/deck/d2r-save-sync/scripts/d2r-sync.sh init --secret "the-same-secret-as-the-pc"
/home/deck/d2r-save-sync/scripts/d2r-sync.sh path
/home/deck/d2r-save-sync/scripts/d2r-sync.sh sync --dry-run
/home/deck/d2r-save-sync/scripts/d2r-sync.sh sync
```

With no arguments the shell script runs `sync`, which is what you want from a shortcut. It pauses for Enter when stdin is a terminal. `D2R_SYNC_NO_PAUSE=1` skips the pause.

To open the window from a checkout that has PySide6 installed:

```bash
python3 -m d2rsync gui
```

### Game Mode shortcut

Switch to Desktop Mode, open Steam, and choose **Games → Add a Non-Steam Game to My Library**. The picker often hides shell scripts, so add any program and then edit it:

1. Right-click the new entry → **Properties**.
2. Rename it to `D2R save sync`.
3. Set **Target** to `/usr/bin/konsole`.
4. Set **Launch Options** to:

   ```text
   --nofork -e /home/deck/d2r-save-sync/scripts/d2r-sync.sh sync
   ```

5. Set **Start In** to `/home/deck/d2r-save-sync`.
6. Return to Game Mode. The shortcut is under **Non-Steam**.

Konsole keeps a readable window, and the script waits for Enter before Game Mode closes it. Quit D2R before launching the shortcut.

If Konsole is not available, point **Target** at `/bin/bash` and set **Launch Options** to:

```text
/home/deck/d2r-save-sync/scripts/d2r-sync.sh sync
```

### Standalone executable, no Python install

GitHub Actions builds two single-file programs on every push: `D2RSaveSync` for Linux, and `D2RSaveSync.exe` for Windows. Download them from the workflow artifacts. You can also build locally after `pip install -e ".[gui]" pyinstaller`:

```bash
pyinstaller --noconfirm --clean D2RSaveSync.spec
```

The Linux build is done on Ubuntu 22.04 so the binary runs on the older glibc in SteamOS. The program still needs the system graphics libraries SteamOS already has for gamescope and XWayland (`libEGL`, `libGL`, `libxkbcommon`, `libxkbcommon-x11`, `libxcb`). It does not need Python or pip on the Deck.

Copy `D2RSaveSync` to the Deck, for example `/home/deck/Applications/D2RSaveSync`, and `chmod +x` it. In Desktop Mode, open Steam and choose **Games → Add a Non-Steam Game to My Library**. Point **Target** at that file and name the entry `D2R Save Sync`. Launch options can stay empty. If the window does not appear, set **Launch Options** to:

```text
QT_QPA_PLATFORM=xcb %command%
```

Return to Game Mode. The shortcut is under **Non-Steam**. Quit D2R before syncing. The same file with no arguments opens the window. `D2RSaveSync sync` and the other subcommands still work; on Windows a console is opened for those commands because the executable itself is a windowed program. The window does not open a console of its own, including while it checks whether D2R is running.

The PC must be running `serve` (or the Deck can serve and the PC can sync; either direction works). In the window, one machine uses **Host** and the other uses **Connect**. Discovery uses a UDP broadcast, so both machines need to be on the same LAN. If the Deck is on a guest Wi-Fi that cannot see the PC, set the PC's address in the Deck config:

```json
"peer": "192.168.1.20"
```

## Everyday use

On the machine that stays on:

```bash
python3 -m d2rsync serve
```

On the other machine, after quitting the game:

```bash
python3 -m d2rsync sync --dry-run
python3 -m d2rsync sync
```

`serve --once` handles a single sync and then exits, which is useful if you do not want the listener left running.

A dry run connects and prints the plan. It does not write saves or sync state.

## Config

```json
{
  "save_path": null,
  "peer": null,
  "port": 41943,
  "discover_port": 41942,
  "secret": "generated-by-init",
  "conflict": "keep-both",
  "backup_retention_count": 20,
  "backup_retention_days": 30,
  "refuse_if_game_running": true,
  "listen_host": "0.0.0.0",
  "name": null,
  "role": "client",
  "peer_mode": "auto"
}
```

`null` or `""` for `save_path`, `peer`, and `name` means "detect automatically". `name` defaults to the hostname and is shown to the other machine. `peer` may be `192.168.1.20` or `192.168.1.20:41943`.

`role` is `client` or `host`, and `peer_mode` is `auto` or `manual`. The window reads and writes both. The CLI ignores them and still uses `serve` versus `sync`, and `--peer` versus discovery. Older config files without these fields load as client and auto.

`D2R_SYNC_CONFIG_DIR` overrides the directory that holds `config.json` and `state.json`. If you pass `--config /path/to/config.json`, state is stored beside that file instead.

## Commands

| Command | Purpose |
| --- | --- |
| `init` | Write `config.json` and a shared secret |
| `path` | Show the save folder that would be used, and any others that were found |
| `status` | List local save files and whether they match the last sync |
| `discover` | Listen for a machine that is running `serve` |
| `serve` | Wait for the other machine |
| `sync` | Connect and sync |
| `gui` | Open the window. This is also what an empty command line does |

Useful flags: `--dry-run`, `--conflict keep-both|ask|newest`, `--peer HOST`, `--save-path PATH`, `--force`, `--once`, `--no-discover`, `--verbose`.

Exit codes: `0` finished, `1` error, `2` a conflict was left unresolved, `3` the game appears to be running and `--force` was not passed.

## While the game is open

The sync refuses to run if `D2R.exe` shows up in the process list (including under Proton). Saves are often mid-write while the game is open. `--force` overrides that on both sides. If the process list cannot be read, the tool warns and continues.

## What crosses the network

Discovery is a UDP broadcast (`D2RSYNC1`) plus, on Linux, an Avahi advertisement of `_d2rsync._tcp` when `avahi-publish-service` is installed. The tool does not bind mDNS port 5353, because Avahi already owns it on SteamOS.

The TCP connection proves both sides know the secret with an HMAC. The secret is not sent. After that, every message is encrypted with an HMAC-SHA256 keystream and authenticated with a second HMAC. Python's standard library has no AES, so this is a small home-LAN construction, not a substitute for a VPN. Do not forward port `41943` to the internet. Anyone with the secret can read and replace saves.

A peer that fails the secret is rejected and delayed briefly. File names that are not real save files, or that try to leave the save folder, are rejected. Individual files are capped at 32 MiB.

## Tests

From the repository root:

```bash
python3 -m unittest discover -s tests -v
```

The sync and conflict rules are covered without a network. A second set of tests runs a real listener on `127.0.0.1`. View-model tests cover the dashboard, the conflict card, and the settings draft without Qt. The window smoke test renders the three screens offscreen and is skipped when PySide6 is not installed. Set `QT_QPA_PLATFORM=offscreen` if you run it over SSH.

## Troubleshooting

**`no peer answered`.** Start `serve` on the other machine. Confirm both are on the same subnet, then try `--peer` with the address printed by `serve`. Some access points block broadcast between Wi-Fi and Ethernet; a configured peer still works.

**Wrong characters.** Run `path`. If two Proton prefixes exist, set `save_path` to the one that contains the `.d2s` you play.

**The same conflict every time.** `ask` plus skip does that on purpose. `keep-both` or `newest` converges on one live file and keeps the other under `.d2r-sync-backups`.

**A deleted character came back.** The other machine still had an edited copy, or it had no record of the deletion. Copy the backup out if the restored file is the one you did not want, delete the live file on the machine that should not have it, and sync again while the other copy is unchanged.

**Firewall prompt.** Allow the app on private networks only. The standalone executable triggers the same prompt the first time it listens or connects.

**The window says PySide6 is missing.** Install the extra: `pip install 'd2r-save-sync[gui]'`. The CLI does not need it.

**The standalone build opens and closes immediately on the Deck.** Run it once from Konsole. A missing `.so` is almost always a graphics library; SteamOS updates include them. `QT_QPA_PLATFORM=xcb` forces the XWayland backend, which Game Mode already provides.
