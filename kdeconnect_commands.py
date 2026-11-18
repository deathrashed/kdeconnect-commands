#!/usr/bin/env python3
"""Build and install KDE Connect "Run Command" lists for m3 (macOS) and cachy (Linux).

Each machine's list holds commands that run ON that machine. The list on m3 runs
Mac commands directly and cachy commands over ssh, and the list on cachy does the
reverse, so both machines show the same grouped catalogue. cachy entries end in
a copyright sign. Edit CATALOGUE, then run `apply`.

  python3 kdeconnect_commands.py show
  python3 kdeconnect_commands.py apply [--dry-run]
  python3 kdeconnect_commands.py restart
  python3 kdeconnect_commands.py check
"""
import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys

MARK = "\u00a9"  # appended to cachy command names
MAC_HOST, CACHY_HOST = "mac", "cachy"  # ssh aliases
MAC_ID = "6709b44c2b2d42f7bc7b4686457322c4"  # m3's KDE Connect id (cachy's folder name for it)
CACHY_ID = "_9d0d7779_b24b_4d95_a85c_b7dbb6473053_"  # cachy's id (m3's folder name for it)
MAC_CFG = f"~/Library/Preferences/kdeconnect/{CACHY_ID}/kdeconnect_runcommand/config"
CACHY_CFG = f"~/.config/kdeconnect/{MAC_ID}/kdeconnect_runcommand/config"
MAC_CLI = "/Applications/KDE Connect.app/Contents/MacOS/kdeconnect-cli"
# ControlPath=none: sandboxes cannot bind the multiplexing socket.
SSH = ["ssh", "-o", "ControlPath=none", "-o", "ControlMaster=no", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20"]

# The daemons run with a bare environment, so each side exports what its tools need.
CACHY_ENV = ("export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus "
             "XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-1 DISPLAY=:0; ")
MAC_ENV = "export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin; "


def osa(s):
    return f"osascript -e '{s}'"


def sysevents(s):
    return osa(f'tell application "System Events" to {s}')


def spotify(verb):
    return f"pgrep -x Spotify >/dev/null && " + osa(f'tell application "Spotify" to {verb}')


def shortcut(component, name):
    return f'qdbus6 org.kde.kglobalaccel /component/{component} invokeShortcut "{name}"'


BRIGHT = ("qdbus6 org.kde.Solid.PowerManagement /org/kde/Solid/PowerManagement/Actions/BrightnessControl "
          "org.kde.Solid.PowerManagement.Actions.BrightnessControl.setBrightness")
NOW = "$(date +%Y%m%d_%H%M%S)"
SND = "/usr/share/sounds/freedesktop/stereo"
BG = ">/dev/null 2>&1 &"

# (group, name, command on macOS or None, command on cachy or None)
CATALOGUE = [
    ("Power", "Lock Screen", "pmset displaysleepnow", "loginctl lock-session"),
    ("Power", "Unlock Screen", None, "loginctl unlock-session"),
    ("Power", "Screen Off", None, "sleep 0.1 && " + shortcut("org_kde_powerdevil", "Turn Off Screen")),
    ("Power", "Sleep", "pmset sleepnow", "systemctl suspend"),
    ("Power", "Hibernate", None, "systemctl hibernate"),
    ("Power", "Log Out", sysevents("log out"), "qdbus6 org.kde.Shutdown /Shutdown logout"),
    ("Power", "Reboot", sysevents("restart"), "systemctl reboot"),
    ("Power", "Shutdown", sysevents("shut down"), "systemctl poweroff"),
    ("Power", "Keep Awake 1h", f"nohup caffeinate -dimsu -t 3600 {BG}",
     f"nohup systemd-inhibit --what=idle:sleep --who=kdeconnect --why=remote sleep 3600 {BG}"),
    ("Power", "Keep Awake Off", "pkill -x caffeinate",
     "pkill -f '[s]ystemd-inhibit --what=idle:sleep --who=kdeconnect'"),
    ("Power", "Profile Performance", None, "powerprofilesctl set performance"),
    ("Power", "Profile Balanced", None, "powerprofilesctl set balanced"),
    ("Power", "Profile Power Saver", None, "powerprofilesctl set power-saver"),
    ("Volume", "Up", osa("set volume output volume ((output volume of (get volume settings)) + 10)"),
     "wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ 5%+"),
    ("Volume", "Down", osa("set volume output volume ((output volume of (get volume settings)) - 10)"),
     "wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%-"),
    ("Volume", "Mute Toggle", osa("set volume output muted (not (output muted of (get volume settings)))"),
     "wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle"),
    ("Volume", "Set 25%", osa("set volume output volume 25"), "wpctl set-volume @DEFAULT_AUDIO_SINK@ 25%"),
    ("Volume", "Set 50%", osa("set volume output volume 50"), "wpctl set-volume @DEFAULT_AUDIO_SINK@ 50%"),
    ("Volume", "Set 100%", osa("set volume output volume 100"), "wpctl set-volume @DEFAULT_AUDIO_SINK@ 1.0"),
    ("Volume", "Mic Mute Toggle", None, "wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle"),
    # macOS has no media-key CLI, so these only reach Spotify while it is running.
    ("Media", "Play Pause", spotify("playpause"), shortcut("mediacontrol", "playpausemedia")),
    ("Media", "Next", spotify("next track"), shortcut("mediacontrol", "nextmedia")),
    ("Media", "Previous", spotify("previous track"), shortcut("mediacontrol", "previousmedia")),
    ("Media", "Stop", spotify("pause"), shortcut("mediacontrol", "stopmedia")),
    ("Display", "Brightness Up", None, shortcut("org_kde_powerdevil", "Increase Screen Brightness")),
    ("Display", "Brightness Down", None, shortcut("org_kde_powerdevil", "Decrease Screen Brightness")),
    ("Display", "Brightness Max", None, BRIGHT + " 10000"),
    ("Display", "Brightness 50%", None, BRIGHT + " 5000"),
    ("Display", "Brightness Min", None, BRIGHT + " 500"),
    ("Appearance", "Dark Mode", sysevents("tell appearance preferences to set dark mode to true"),
     "lookandfeeltool -a org.kde.breezedark.desktop"),
    ("Appearance", "Light Mode", sysevents("tell appearance preferences to set dark mode to false"),
     "lookandfeeltool -a org.kde.breeze.desktop"),
    ("Appearance", "Dark Mode Toggle",
     sysevents("tell appearance preferences to set dark mode to not dark mode"), None),
    ("Screenshot", "Full Screen", f'mkdir -p "$HOME/Desktop" && screencapture -x "$HOME/Desktop/shot_{NOW}.png"',
     f'mkdir -p "$HOME/Pictures" && spectacle -b -f -o "$HOME/Pictures/shot_{NOW}.png"'),
    ("Screenshot", "To Clipboard", "screencapture -xc", "spectacle -b -f -c"),
    ("Sound", "Ping", "afplay /System/Library/Sounds/Ping.aiff", f"paplay {SND}/message.oga"),
    ("Sound", "Alert", "afplay /System/Library/Sounds/Sosumi.aiff", f"paplay {SND}/dialog-warning.oga"),
    ("Notify", "Hello", osa('display notification "Hello" with title "KDE Connect"'),
     'notify-send "KDE Connect" "Hello"'),
    ("Clipboard", "Clear", "pbcopy </dev/null", "wl-copy --clear"),
    ("Apps", "Waterfox", "open -a Waterfox", f"nohup waterfox {BG}"),
    ("Apps", "Chrome", 'open -a "Google Chrome"', f"nohup google-chrome-stable {BG}"),
    ("Apps", "Obsidian", "open -a Obsidian", f"nohup obsidian {BG}"),
    ("Apps", "Feishin", "open -a Feishin", f"nohup feishin {BG}"),
    ("Apps", "Terminal", "open -a Ghostty", f"nohup ghostty {BG}"),
    ("Apps", "Files", "open -a Finder", f"nohup dolphin {BG}"),
    ("Apps", "Spotify", "open -a Spotify", None),
]


def entry_id(side, name):
    h = hashlib.md5(f"{side}|{name}".encode()).hexdigest()
    return f"_{h[:8]}_{h[8:12]}_{h[12:16]}_{h[16:20]}_{h[20:32]}_"


def over_ssh(host, env, cmd):
    # base64 keeps quotes intact and sidesteps fish, the login shell on cachy.
    payload = base64.b64encode((env + cmd).encode()).decode()
    return f'ssh -o BatchMode=yes -o ConnectTimeout=10 {host} "echo {payload} | base64 -d | sh"'


def build(side):
    """side is the machine whose config will hold the list: 'mac' or 'cachy'."""
    out = {}
    for group, name, mac_cmd, cachy_cmd in CATALOGUE:
        if mac_cmd:
            label = f"{group}: {name}"
            out[entry_id(side, label)] = {
                "name": label,
                "command": mac_cmd if side == "mac" else over_ssh(MAC_HOST, MAC_ENV, mac_cmd)}
        if cachy_cmd:
            label = f"{group}: {name} {MARK}"
            out[entry_id(side, label)] = {
                "name": label,
                "command": cachy_cmd if side == "cachy" else over_ssh(CACHY_HOST, CACHY_ENV, cachy_cmd)}
    return out


def config_line(entries):
    raw = json.dumps(entries, separators=(",", ":"), ensure_ascii=True)
    return 'commands="@ByteArray(' + raw.replace("\\", "\\\\").replace('"', '\\"') + ')"'


# Runs under `python3 -` on either machine, so both get identical write logic.
INSTALLER = '''
import base64, os, re, shutil, time
f = os.path.expanduser({path!r})
new = base64.b64decode({data!r}).decode()
s = open(f).read() if os.path.exists(f) else "[General]\\n"
if re.search(r"^commands=", s, re.M):
    s2 = re.sub(r"^commands=.*$", lambda m: new, s, flags=re.M)
else:
    s2 = s.rstrip("\\n") + "\\n" + new + "\\n"
if s2 == s:
    print("unchanged")
else:
    if os.path.exists(f):
        shutil.copy2(f, f + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(os.path.dirname(f), exist_ok=True)
    open(f, "w").write(s2)
    print("written")
'''


def run(cmd, stdin=None, retries=1):
    for attempt in range(retries):
        p = subprocess.run(cmd, input=stdin, capture_output=True, text=True)
        if p.returncode == 0 or "timed out" not in p.stderr or attempt == retries - 1:
            return p
        subprocess.run(["sleep", "8"])
    return p


def on_cachy(script, retries=4):
    return run(SSH + [CACHY_HOST, "bash -s"], stdin=script, retries=retries)


def apply(dry):
    for side, path in (("mac", MAC_CFG), ("cachy", CACHY_CFG)):
        entries = build(side)
        print(f"{side}: {len(entries)} entries")
        if dry:
            continue
        data = base64.b64encode(config_line(entries).encode()).decode()
        src = INSTALLER.format(path=path, data=data)
        p = run(["python3", "-"], src) if side == "mac" else run(SSH + [CACHY_HOST, "python3 -"], src, retries=4)
        print(f"{side}: {p.stdout.strip() or p.stderr.strip()}")
        if p.returncode:
            sys.exit(1)


def restart():
    # cachy: kill the daemon and let D-Bus reactivate it; fall back to a transient user unit.
    p = on_cachy("""
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus XDG_RUNTIME_DIR=/run/user/1000
pkill -x kdeconnectd; sleep 3
kdeconnect-cli -l >/dev/null 2>&1; sleep 4
pgrep -x kdeconnectd >/dev/null || { systemd-run --user --unit=kdeconnectd-manual --collect /usr/bin/kdeconnectd; sleep 5; }
pgrep -ax kdeconnectd
""")
    print("cachy:", p.stdout.strip() or p.stderr.strip())
    # Mac: quit the whole bundle, then open it normally so the daemon is a child of launchd.
    # Bracketed first letter keeps pkill -f from matching this shell's own command line.
    subprocess.run(["bash", "-c", "pkill -x kdeconnect-app; pkill -x kdeconnectd; "
                    "pkill -f '[K]DE Connect.app/Contents/MacOS/dbus-daemon'; pkill -x 'KDE Connect'; "
                    "sleep 3; open -a 'KDE Connect'; sleep 10; pgrep -lx kdeconnectd"])


def check():
    want = {"mac": set(build("mac")), "cachy": set(build("cachy"))}
    ok = True
    mac_view = subprocess.run(["bash", "-c",
        f'export DBUS_SESSION_BUS_ADDRESS="unix:path=$(launchctl getenv DBUS_LAUNCHD_SESSION_BUS_SOCKET)"; '
        f'"{MAC_CLI}" -d {CACHY_ID} --list-commands 2>/dev/null'], capture_output=True, text=True).stdout
    cachy_view = on_cachy("export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus\n"
                          f"kdeconnect-cli -d {MAC_ID} --list-commands 2>/dev/null").stdout
    # m3 sees the list stored on cachy; cachy sees the list stored on m3.
    for label, view, key in (("m3 sees cachy's list", mac_view, "cachy"), ("cachy sees m3's list", cachy_view, "mac")):
        got = set(re.findall(r"^(\S+): ", view, re.M))
        status = "ok" if got == want[key] else f"MISMATCH (have {len(got)}, want {len(want[key])})"
        ok &= got == want[key]
        print(f"{label}: {len(got)} entries, {status}")
    sys.exit(0 if ok else 1)


def show():
    for group, name, m, c in CATALOGUE:
        print(f"{group:11} {name:22} mac={'y' if m else '-'} cachy={'y' if c else '-'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["show", "apply", "restart", "check"])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    {"show": show, "apply": lambda: apply(a.dry_run), "restart": restart, "check": check}[a.action]()
