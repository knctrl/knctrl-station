import os
import re
import shutil
import subprocess
import logging
import threading
import time
import json
from collections import deque
from pathlib import Path
from flask import Flask, jsonify, render_template_string, send_file, abort, request, Response

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

try:
    import psutil
    from flask_cors import CORS
except ImportError as e:
    logging.critical(f"Missing Python module: {e.name}. Please run: pip install psutil flask-cors")
    exit(1)

app = Flask(__name__)
CORS(app)

# ==========================================
# --- USER CONFIGURATION ---
# ==========================================

# Point this to any looping .mp4 file on your system.
# If the file does not exist, the dashboard will default to a solid black background.
WALLPAPER_PATH = os.path.expanduser("~/path/to/your/wallpaper.mp4")

# Define the 4-digit security code required to unlock the dashboard.
SECRET_PIN = "0451" 

# ==========================================

# --- GLOBAL STATE ---
notif_history = deque(maxlen=30)
notif_unread = False

# --- D-BUS NOTIFICATION MONITOR ---
def monitor_notifications():
    global notif_unread
    try:
        proc = subprocess.Popen(
            ["dbus-monitor", "interface='org.freedesktop.Notifications',member='Notify'"],
            stdout=subprocess.PIPE, text=True
        )
        strings_found = []
        for line in iter(proc.stdout.readline, ''):
            line = line.strip()
            if "member=Notify" in line:
                strings_found = []
            elif line.startswith("string"):
                try:
                    val = line.split('"', 1)[1].rsplit('"', 1)[0]
                    strings_found.append(val)
                    if len(strings_found) == 4:
                        app_name = strings_found[0] or "System"
                        title = strings_found[2]
                        body = strings_found[3].replace('\\n', ' ').replace('&quot;', '"')
                        if title:
                            notif_history.appendleft({"app": app_name, "title": title, "body": body, "time": time.strftime("%H:%M")})
                            notif_unread = True
                except: pass
    except Exception as e:
        logging.error(f"Failed to start D-Bus monitor: {e}")

threading.Thread(target=monitor_notifications, daemon=True).start()

# --- SYSTEM CHECKS ---
def run_cmd(cmd_list):
    try:
        result = subprocess.run(cmd_list, capture_output=True, text=True, timeout=2)
        if result.returncode == 0: return result.stdout.strip()
        return None
    except: return None

def get_sys_info():
    try:
        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory().percent
    except: cpu, ram = 0, 0

    vol = "0"
    if shutil.which("pactl"):
        try:
            pactl_out = run_cmd(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
            if pactl_out:
                match = re.search(r"(\d+)%", pactl_out)
                if match: vol = match.group(1)
        except: vol = "ERR"
    else: vol = "N/A"
    return {"cpu": cpu, "ram": ram, "vol": vol}

def get_media_info():
    if not shutil.which("playerctl"):
        return {"artist": "MISSING PKG", "title": "Install playerctl", "status": "Stopped"}
    artist = run_cmd(["playerctl", "metadata", "artist"])
    title = run_cmd(["playerctl", "metadata", "title"])
    status = run_cmd(["playerctl", "status"])

    if not status or status == "Stopped":
        return {"artist": "---", "title": "Station Idle", "status": "Stopped"}
    return {"artist": artist or "Unknown Artist", "title": title or "Unknown Audio", "status": status}

def get_workspace_info():
    try:
        active_out = subprocess.check_output(["hyprctl", "activeworkspace", "-j"], text=True)
        active_id = json.loads(active_out).get("id", 1)

        ws_out = subprocess.check_output(["hyprctl", "workspaces", "-j"], text=True)
        ws_data = json.loads(ws_out)
        occupied = [ws["id"] for ws in ws_data if ws.get("windows", 0) > 0]

        return {"active": active_id, "occupied": occupied}
    except:
        return {"active": 1, "occupied": [1]}

# --- ROUTES ---
@app.route('/')
def home(): return render_template_string(HTML_PAGE)

@app.route('/api/auth', methods=['POST'])
def api_auth():
    attempt = request.json.get('pin')
    if attempt == SECRET_PIN:
        return jsonify({"valid": True})
    return jsonify({"valid": False}), 401

@app.route('/api/telemetry')
def api_telemetry():
    global notif_unread
    payload = {
        "sys": get_sys_info(),
        "media": get_media_info(),
        "workspace": get_workspace_info(),
        "unread": notif_unread
    }
    notif_unread = False 
    return jsonify(payload)

@app.route('/api/workspace', methods=['POST'])
def api_workspace():
    direction = request.json.get('direction')
    if direction == "next":
        subprocess.Popen(["hyprctl", "dispatch", "workspace", "m+1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif direction == "prev":
        subprocess.Popen(["hyprctl", "dispatch", "workspace", "m-1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return jsonify({"status": "success"})

@app.route('/api/notifications')
def api_notifications():
    return jsonify(list(notif_history))

@app.route('/api/media', methods=['POST'])
def api_media():
    action = request.json.get('action')
    commands = {
        "toggle": ["playerctl", "play-pause"],
        "next": ["playerctl", "next"],
        "prev": ["playerctl", "previous"]
    }
    if action in commands:
        subprocess.Popen(commands[action])
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 400

@app.route('/api/power', methods=['POST'])
def api_power():
    action = request.json.get('action')
    commands = {
        "shutdown": ["systemctl", "poweroff"], "reboot": ["systemctl", "reboot"],
        "suspend": ["systemctl", "suspend"], "lock": ["loginctl", "lock-session"]
    }
    if action in commands:
        subprocess.Popen(commands[action])
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 400

@app.route('/api/launch', methods=['POST'])
def api_launch():
    app_id = request.json.get('app')
    commands = {
        "discord": ["discord"],
        "steam": ["steam"],
        "floorp": ["flatpak", "run", "one.ablaze.floorp"],
        "terminal": ["kitty"],
        "files": ["dolphin"],
        "btop": ["kitty", "-e", "btop"]
    }
    if app_id in commands:
        subprocess.Popen(commands[app_id], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return jsonify({"status": "success", "launched": app_id})
    return jsonify({"status": "error", "message": "Unknown application"}), 400

@app.route('/video/wallpaper')
def serve_video():
    if not os.path.exists(WALLPAPER_PATH): return abort(404)
    range_header = request.headers.get('Range', None)
    if not range_header: return send_file(WALLPAPER_PATH, mimetype='video/mp4')
    size = os.path.getsize(WALLPAPER_PATH)
    byte1, byte2 = 0, None
    match = re.search(r'bytes=(\d+)-(\d*)', range_header)
    groups = match.groups()
    if groups[0]: byte1 = int(groups[0])
    if groups[1]: byte2 = int(groups[1])
    if byte2 is None: byte2 = size - 1
    length = byte2 - byte1 + 1
    with open(WALLPAPER_PATH, 'rb') as f:
        f.seek(byte1)
        data = f.read(length)
    rv = Response(data, 206, mimetype='video/mp4', direct_passthrough=True)
    rv.headers.add('Content-Range', f'bytes {byte1}-{byte2}/{size}')
    rv.headers.add('Accept-Ranges', 'bytes')
    rv.headers.add('Content-Length', str(length))
    return rv

@app.route('/dont')
def cursed_home():
    try: cpu = subprocess.check_output("top -bn1 | grep 'Cpu(s)' | sed 's/.*, *\\([0-9.]*\\)%* id.*/\\1/' | awk '{print 100 - $1}'", shell=True).decode().strip()
    except: cpu = "ERR"
    try: ram = subprocess.check_output("free -m | awk 'NR==2{printf \"%.2f%%\", $3*100/$2 }'", shell=True).decode().strip()
    except: ram = "ERR"
    try: vol = subprocess.check_output("pactl get-sink-volume @DEFAULT_SINK@ | grep -o '[0-9]*%' | head -1", shell=True).decode().strip()
    except: vol = "ERR"
    
    try: 
        title = subprocess.check_output("playerctl metadata title || echo 'No Song'", shell=True).decode().strip()
        artist = subprocess.check_output("playerctl metadata artist || echo 'No Artist'", shell=True).decode().strip()
    except: title, artist = "No Song", "No Artist"

    cursed_notifs = [f"[{n['app']}] {n['title']}: {n['body']}" for n in list(notif_history)]

    html = f"""
    <html>
    <head>
        <title>System Station - WHY ARE YOU HERE</title>
        <meta http-equiv="refresh" content="3">
        <script>
            const lyrics = [
                "Never gonna give you up", "Never gonna let you down",
                "Never gonna run around and desert you", "Never gonna make you cry",
                "Never gonna say goodbye", "Never gonna tell a lie and hurt you"
            ];
            const beeMovie = [
                "According to all known laws of aviation,", "there is no way a bee should be able to fly.",
                "Its wings are too small to get its fat little body off the ground.",
                "The bee, of course, flies anyway", "because bees don't care what humans think is impossible."
            ];

            function triggerNightmare() {{
                alert("ACCESS DENIED."); alert("YOUR ATTEMPT HAS BEEN LOGGED.");
                for(let i=0; i<3; i++) {{ for(let line of lyrics) {{ alert(line); }} }}
                alert("STILL HERE?"); alert("FINE.");
                for(let line of beeMovie) {{ alert(line); }}
                alert("HAVE A NICE DAY.");
            }}
        </script>
    </head>
    <body bgcolor="cyan" text="magenta" link="blue" vlink="blue">
        <center>
            <h1><blink>SYSTEM STATION (CURSED EDITION)</blink></h1>
            <a href="/">[Take me back to safety]</a>
            <hr>
            <video src="/video/wallpaper" width="200" autoplay></video>
            <br><br>
            <table border="1" cellpadding="10" bgcolor="yellow">
                <tr><th>CPU</th><th>RAM</th><th>VOLUME</th></tr>
                <tr><td>{cpu}%</td><td>{ram}</td><td>{vol}</td></tr>
            </table>
            <br>
            <h2>NOW PLAYING:</h2>
            <h3>{title} - {artist}</h3>
            <input type="button" value="PREV" onclick="triggerNightmare()">
            <input type="button" value="PLAY_PAUSE" onclick="triggerNightmare()">
            <input type="button" value="NEXT" onclick="triggerNightmare()">
            <hr>
            <h2>POWER CONTROLS</h2>
            <input type="button" value="SHUTDOWN" onclick="triggerNightmare()">
            <input type="button" value="REBOOT" onclick="triggerNightmare()">
            <input type="button" value="SUSPEND" onclick="triggerNightmare()">
            <hr>
            <h2>NOTIFICATIONS</h2>
            <marquee direction="up" scrollamount="2" height="100">
                {'<br><br>'.join(cursed_notifs) if cursed_notifs else "No notifications."}
            </marquee>
            <hr>
            <h2>VISUALIZER</h2>
            <marquee behavior="alternate" scrollamount="50">|||||||||||||||||||||||||||||||||||||||</marquee>
        </center>
    </body>
    </html>
    """
    return html

# --- FRONTEND (HTML/CSS/JS) ---
HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no,viewport-fit=cover"/>
<title>System // Station</title>
<style>
/* --- ROOT & RESET --- */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --text: #ffffff;
  --accent: #a0aec0;
  --void: #1a202c;
  --glass: rgba(5, 7, 10, 0.85);
  --eco: #4ade80;
  --danger: #ff4a4a;
  --amber: #f6ad55;
  --ice: #76e4f7;
  --font: 'JetBrains Mono', monospace;
}

html, body {
  width: 100%; height: 100%; overflow: hidden; background: #000;
  font-family: var(--font); color: var(--text); user-select: none;
  -webkit-tap-highlight-color: transparent; touch-action: none;
}

body.css-landscape {
  position: fixed; width: 100vh; height: 100vw;
  top: calc(50vh - 50vw); left: calc(50vw - 50vh);
  transform: rotate(90deg); transform-origin: 50% 50%; overflow: hidden;
}

#bg-video {
  position: absolute; inset: 0; width: 100%; height: 100%;
  object-fit: cover; z-index: 0;
  filter: brightness(0.3) contrast(1.3) grayscale(0.6) blur(6px);
  transition: opacity 0.5s ease;
}

/* Base App Layout & Animations */
#app {
  position: relative; z-index: 1; width: 100%; height: 100%;
  display: flex; flex-direction: column; filter: blur(20px); 
  transition: filter 0.5s ease, transform 0.3s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.3s ease;
}
#app.unlocked { filter: blur(0px); }
#app.launcher-open { transform: scale(0.95); opacity: 0.5; }
#app.workspace-switch-next { transform: scale(0.93) translateX(-40px); opacity: 0.4; }
#app.workspace-switch-prev { transform: scale(0.93) translateX(40px); opacity: 0.4; }

/* --- HEADER --- */
header {
  display: flex; align-items: center; justify-content: space-between;
  padding: clamp(0.5rem, 2vh, 1rem) clamp(1rem, 3vw, 2rem); flex-shrink: 0;
}
.hdr-left { display: flex; align-items: center; }
.hdr-right { display: flex; gap: clamp(0.5rem, 2vw, 1rem); }
.hdr-btn {
  background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255,255,255,0.2); 
  color: var(--text); cursor: pointer; padding: 10px; border-radius: 12px;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.2s ease; position: relative; width: 42px; height: 42px;
}
.hdr-btn:active { transform: scale(0.9); background: rgba(255,255,255,0.2); }
.hdr-btn.active { background: var(--eco); color: #000; border-color: var(--eco); }
.hdr-btn svg { width: 20px; height: 20px; stroke: currentColor; stroke-width: 2; fill: none; stroke-linecap: round; stroke-linejoin: round; }

#notif-badge {
  position: absolute; top: 8px; right: 8px; width: 8px; height: 8px;
  background: var(--danger); border-radius: 50%; box-shadow: 0 0 8px var(--danger);
  display: none;
}
.hdr-wordmark {
  font-size: clamp(0.5rem, 2vh, 0.7rem); font-weight: 700;
  letter-spacing: 0.22em; color: rgba(160,174,192,0.4); text-transform: uppercase;
}

/* --- MAIN CONTENT --- */
main {
  flex: 1; display: flex; align-items: center; justify-content: space-around;
  padding: 0 clamp(1rem, 3vw, 2rem); gap: 2rem; overflow: hidden;
}

.time-section { display: flex; flex-direction: column; align-items: center;}
.clock-row { display: flex; align-items: baseline; }
#time-hm {
  font-size: clamp(4rem, 20vh, 8.5rem); font-weight: 800;
  background: linear-gradient(to bottom, #ffffff 20%, #cbd5e0 60%, var(--void));
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  line-height: 0.8; letter-spacing: -6px; filter: drop-shadow(0 0 15px rgba(255, 255, 255, 0.15));
}
#time-s {
  font-size: clamp(2rem, 8vh, 3rem); font-weight: 800; margin-left: 12px; opacity: 0.5; width: 70px;
  background: linear-gradient(to bottom, #ffffff, #cbd5e0); -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
#date-display {
  font-size: clamp(0.7rem, 3vh, 0.85rem); color: #fff; letter-spacing: 6px;
  margin-top: 2vh; opacity: 0.6; text-transform: uppercase; text-align: center;
}

/* WORKSPACE INDICATOR */
.workspace-pill {
  display: flex; justify-content: center; align-items: center; gap: 12px;
  background: rgba(15, 20, 25, 0.8); border: 1px solid rgba(255,255,255,0.05);
  padding: 8px 18px; border-radius: 20px; margin-top: 15px;
  box-shadow: inset 0 2px 5px rgba(0,0,0,0.5), 0 5px 15px rgba(0,0,0,0.3);
  opacity: 0; transition: opacity 0.5s ease;
}
#app.unlocked .workspace-pill { opacity: 1; }
.ws-icon {
  width: 14px; height: 14px; display: flex; justify-content: center; align-items: center;
  color: rgba(255,255,255,0.4); transition: all 0.3s ease;
}
.ws-icon.active { color: #fff; width: 18px; height: 18px; filter: drop-shadow(0 0 5px rgba(255,255,255,0.5)); }
.ws-icon.occupied { color: rgba(255,255,255,0.8); }
.ws-icon svg { width: 100%; height: 100%; fill: currentColor; }

.glass-card {
  background: var(--glass); backdrop-filter: blur(40px);
  border: 1px solid rgba(255,255,255,0.06); border-radius: 20px;
  padding: 4vh 30px; width: 100%; max-width: 500px;
  display: flex; flex-direction: column; text-align: center;
  box-shadow: 0 30px 60px rgba(0,0,0,0.7); overflow: hidden;
}
.marquee-wrap { width: 100%; overflow: hidden; white-space: nowrap; position: relative; margin-bottom: 5px; }
#track-title { display: inline-block; font-size: clamp(1rem, 4vh, 1.4rem); font-weight: bold; color: #fff; }
.scrolling { animation: scroll-text 12s linear infinite; }
@keyframes scroll-text { 0% { transform: translateX(10%); } 100% { transform: translateX(-100%); } }

#artist-name { font-size: clamp(0.6rem, 2.5vh, 0.8rem); opacity: 0.4; letter-spacing: 2px; text-transform: uppercase; margin-bottom: 3vh; }

.media-controls { 
  display: flex; align-items: center; justify-content: center; gap: 15px; margin-bottom: 3vh; 
  transition: opacity 0.3s ease, margin 0.3s ease, height 0.3s ease;
}
.m-btn {
  background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1);
  color: var(--accent); border-radius: 10px; padding: 10px 15px; cursor: pointer;
  display: flex; align-items: center; justify-content: center; transition: all 0.15s ease;
}
.m-btn:active { transform: scale(0.9); background: rgba(255,255,255,0.15); }
.m-btn.primary { color: #fff; background: rgba(255,255,255,0.1); border-color: rgba(255,255,255,0.3); }
.m-btn svg { width: 18px; height: 18px; fill: currentColor; }

.stats-grid { display: flex; justify-content: space-around; border-top: 1px solid rgba(255,255,255,0.05); padding-top: 2.5vh; }
.stat-cell { flex: 1; }
.stat-label { font-size: 0.55rem; opacity: 0.3; text-transform: uppercase; letter-spacing: 2px; margin-bottom: 5px; display: block; }
.stat-val { font-size: clamp(1.2rem, 5vh, 1.5rem); font-weight: 700; color: #fff; font-variant-numeric: tabular-nums; }

#visualizer { width: 100%; height: clamp(40px, 10vh, 80px); flex-shrink: 0; transition: opacity 0.5s ease; }

/* --- OVERLAYS --- */
.overlay {
  position: absolute; inset: 0; background: rgba(0, 0, 0, 0.8); backdrop-filter: blur(20px);
  z-index: 999; display: flex; flex-direction: column; align-items: center; justify-content: center;
  opacity: 0; pointer-events: none; transition: opacity 0.3s ease;
}
.overlay.active { opacity: 1; pointer-events: all; }
.overlay-title { font-size: clamp(0.8rem, 2.5vh, 1.2rem); font-weight: 700; letter-spacing: 4px; color: #fff; margin-bottom: 3vh; text-align: center;}
.overlay-close { 
  margin-top: clamp(1.5rem, 4vh, 3rem); opacity: 0.5; 
  font-size: clamp(0.55rem, 1.4vw, 0.7rem); letter-spacing: 2px; 
  cursor: pointer; text-align: center; transition: opacity 0.2s ease;
}
.overlay-close:active { opacity: 1; }

.power-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; width: 80%; max-width: 400px; }
.power-btn {
  background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255,255,255,0.1);
  border-radius: 20px; color: var(--text); cursor: pointer; padding: 25px 10px;
  display: flex; flex-direction: column; align-items: center; gap: 10px;
  font-family: var(--font); font-size: 0.7rem; font-weight: 700; letter-spacing: 2px;
  transition: all 0.2s ease;
}
.power-btn:active { transform: scale(0.95); }
.power-btn svg { width: 24px; height: 24px; }
.power-btn.shutdown { border-color: rgba(255,74,74,0.3); color: var(--danger); }
.power-btn.reboot { border-color: rgba(246,173,85,0.3); color: var(--amber); }
.power-btn.suspend { border-color: rgba(118,228,247,0.3); color: var(--ice); }
.power-btn.primed { background: rgba(255,74,74,0.15); border-color: var(--danger); color: var(--danger); }
.power-btn.primed::after { content: 'TAP AGAIN'; font-size: 0.5rem; letter-spacing: 2px; }

.notif-container {
  width: 85%; max-width: 550px; height: 70vh; display: flex; flex-direction: column;
  background: var(--glass); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 20px;
  overflow: hidden; box-shadow: 0 30px 60px rgba(0,0,0,0.8);
}
.notif-list { flex: 1; overflow-y: auto; padding: 15px; display: flex; flex-direction: column; gap: 10px; }
.notif-list::-webkit-scrollbar { width: 6px; }
.notif-list::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.2); border-radius: 10px; }
.notif-item { background: rgba(255, 255, 255, 0.05); border-radius: 12px; padding: 15px; text-align: left; }
.notif-app { font-size: 0.6rem; color: var(--eco); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 5px; font-weight: bold;}
.notif-title { font-size: 0.95rem; font-weight: bold; margin-bottom: 5px; color: #fff; }
.notif-body { font-size: 0.8rem; opacity: 0.7; line-height: 1.4; }
.notif-time { font-size: 0.6rem; opacity: 0.4; text-align: right; margin-top: 10px; }
.notif-empty { text-align: center; padding: 50px 0; opacity: 0.3; letter-spacing: 2px; }

/* --- APP LAUNCHER (TEXT ONLY, MONOCHROMATIC) --- */
#app-launcher-wrapper {
  position: absolute; inset: 0; z-index: 998; pointer-events: none;
  display: flex; flex-direction: column; justify-content: flex-end; align-items: center;
}
#app-launcher {
  width: 90%; max-width: 650px; padding: 35px 25px; margin-bottom: 25px;
  background: rgba(10, 15, 20, 0.85); backdrop-filter: blur(30px);
  border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 30px;
  box-shadow: 0 20px 40px rgba(0,0,0,0.8);
  display: flex; justify-content: center;
  transform: translateY(150%); transition: transform 0.4s cubic-bezier(0.16, 1, 0.3, 1);
  pointer-events: auto;
}
#app-launcher-wrapper.active { pointer-events: auto; }
#app-launcher-wrapper.active #app-launcher { transform: translateY(0); }

.launcher-grid { 
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px 20px; width: 100%;
}

.text-app-btn {
  display: flex; justify-content: center; align-items: center;
  width: 100%; height: 60px; border-radius: 15px;
  background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1);
  color: #fff; font-family: var(--font); font-size: clamp(0.7rem, 2vw, 0.9rem);
  font-weight: 700; letter-spacing: 2px; text-transform: uppercase;
  cursor: pointer; transition: all 0.2s ease;
  box-shadow: 0 5px 15px rgba(0,0,0,0.3);
}

.text-app-btn:active { transform: scale(0.95); background: rgba(255,255,255,0.2); border-color: rgba(255,255,255,0.5); }

/* --- LOCK SCREEN OVERLAY --- */
#lock-screen {
  position: absolute; inset: 0; background: var(--void); z-index: 9999;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  transition: opacity 0.5s ease;
}
#lock-screen.hidden { opacity: 0; pointer-events: none; }
.pin-display { display: flex; gap: 15px; margin-bottom: 40px; }
.pin-dot { width: 15px; height: 15px; border-radius: 50%; border: 2px solid var(--accent); transition: all 0.2s ease; }
.pin-dot.filled { background: #fff; border-color: #fff; box-shadow: 0 0 10px rgba(255,255,255,0.5); }
.pin-dot.error { border-color: var(--danger); background: var(--danger); box-shadow: 0 0 10px var(--danger); }
.numpad { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; width: 280px; }
.num-btn {
  background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius: 50%;
  color: #fff; font-size: 1.5rem; font-family: var(--font); cursor: pointer;
  display: flex; justify-content: center; align-items: center; aspect-ratio: 1; transition: all 0.1s ease;
}
.num-btn:active { background: rgba(255,255,255,0.2); transform: scale(0.9); }

</style>
</head>
<body>

  <!-- LOCK SCREEN -->
  <div id="lock-screen">
    <div class="overlay-title" id="lock-msg">// SYSTEM SECURE<br><span style="font-size:0.5rem; opacity:0.5; font-weight:normal;">ENTER PIN</span></div>
    <div class="pin-display" id="pin-display">
      <div class="pin-dot"></div><div class="pin-dot"></div><div class="pin-dot"></div><div class="pin-dot"></div>
    </div>
    <div class="numpad" id="numpad">
      <button class="num-btn" onclick="enterPin('1')">1</button>
      <button class="num-btn" onclick="enterPin('2')">2</button>
      <button class="num-btn" onclick="enterPin('3')">3</button>
      <button class="num-btn" onclick="enterPin('4')">4</button>
      <button class="num-btn" onclick="enterPin('5')">5</button>
      <button class="num-btn" onclick="enterPin('6')">6</button>
      <button class="num-btn" onclick="enterPin('7')">7</button>
      <button class="num-btn" onclick="enterPin('8')">8</button>
      <button class="num-btn" onclick="enterPin('9')">9</button>
      <button class="num-btn" style="visibility:hidden;">*</button>
      <button class="num-btn" onclick="enterPin('0')">0</button>
      <button class="num-btn" onclick="clearPin()">C</button>
    </div>
  </div>

  <video id="bg-video" loop muted playsinline preload="none"><source src="/video/wallpaper" type="video/mp4"/></video>
  <video id="nosleep-video" playsinline muted loop style="position:absolute; width:5px; height:5px; opacity:0.01; pointer-events:none; z-index:9999;"></video>
  
  <!-- APP SHELL -->
  <div id="app">
    <header>
      <div class="hdr-left">
        <button class="hdr-btn" id="btn-notif" onclick="openNotifs()">
          <svg viewBox="0 0 24 24"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"></path><path d="M13.73 21a2 2 0 0 1-3.46 0"></path></svg>
          <div id="notif-badge"></div>
        </button>
      </div>
      <span class="hdr-wordmark">SYSTEM // STATION</span>
      <div class="hdr-right">
        <button class="hdr-btn btn-eco" id="btn-eco" onclick="toggleEco()">
          <svg viewBox="0 0 24 24"><path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z"></path><line x1="2" y1="22" x2="11" y2="20"></line></svg>
        </button>
        <button class="hdr-btn" onclick="openPower()">
          <svg viewBox="0 0 24 24"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"></path><line x1="12" y1="2" x2="12" y2="12"></line></svg>
        </button>
        <button class="hdr-btn" id="btn-sync" onclick="initLandscape()">
          <svg viewBox="0 0 24 24"><polyline points="15 3 21 3 21 9"></polyline><polyline points="9 21 3 21 3 15"></polyline><line x1="21" y1="3" x2="14" y2="10"></line><line x1="3" y1="21" x2="10" y2="14"></line></svg>
        </button>
      </div>
    </header>

    <main>
      <div class="time-section">
        <div class="clock-row"><span id="time-hm">00:00</span><span id="time-s">00</span></div>
        <div id="date-display">LOADING...</div>
        <div id="workspace-pill" class="workspace-pill"></div>
      </div>

      <div class="glass-card">
        <div class="marquee-wrap"><span id="track-title">Nothing Playing</span></div>
        <div id="artist-name">─</div>
        
        <div class="media-controls" id="media-ctrls">
          <button class="m-btn" onclick="mediaCtrl('prev')">
            <svg viewBox="0 0 24 24"><polygon points="19,20 9,12 19,4"/><line x1="5" y1="19" x2="5" y2="5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
          </button>
          <button class="m-btn primary" id="btn-playpause" onclick="mediaCtrl('toggle')">
            <svg viewBox="0 0 24 24"><polygon points="5,3 19,12 5,21"/></svg>
          </button>
          <button class="m-btn" onclick="mediaCtrl('next')">
            <svg viewBox="0 0 24 24"><polygon points="5,4 15,12 5,20"/><line x1="19" y1="5" x2="19" y2="19" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
          </button>
        </div>

        <div class="stats-grid">
          <div class="stat-cell"><span class="stat-label">CPU</span><span class="stat-val" id="cpu-val">─</span></div>
          <div class="stat-cell"><span class="stat-label">RAM</span><span class="stat-val" id="ram-val">─</span></div>
          <div class="stat-cell"><span class="stat-label">VOL</span><span class="stat-val" id="vol-val">─</span></div>
        </div>
      </div>
    </main>

    <canvas id="visualizer"></canvas>
  </div>

  <!-- FLOATING APP LAUNCHER (TEXT ONLY) -->
  <div id="app-launcher-wrapper">
    <div id="app-launcher">
      <div class="launcher-grid">
        <div class="text-app-btn" onclick="launchApp('discord')">DISCORD</div>
        <div class="text-app-btn" onclick="launchApp('steam')">STEAM</div>
        <div class="text-app-btn" onclick="launchApp('floorp')">FLOORP</div>
        <div class="text-app-btn" onclick="launchApp('terminal')">TERMINAL</div>
        <div class="text-app-btn" onclick="launchApp('files')">FILES</div>
        <div class="text-app-btn" onclick="launchApp('btop')">BTOP</div>
      </div>
    </div>
  </div>

  <!-- OVERLAYS -->
  <div class="overlay" id="power-overlay" onclick="overlayBackdrop(event,'power-overlay')">
    <div class="overlay-title">// POWER CONTROL</div>
    <div class="power-grid">
      <button class="power-btn shutdown" id="pbtn-shutdown" onclick="powerAction('shutdown')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="2" x2="12" y2="12"/><path d="M6.5 6.5a7 7 0 1 0 11 0"/></svg>
        SHUTDOWN
      </button>
      <button class="power-btn reboot" id="pbtn-reboot" onclick="powerAction('reboot')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.99"/></svg>
        REBOOT
      </button>
      <button class="power-btn suspend" id="pbtn-suspend" onclick="powerAction('suspend')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
        SUSPEND
      </button>
      <button class="power-btn" id="pbtn-lock" onclick="powerAction('lock')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
        LOCK
      </button>
    </div>
    <div class="overlay-close" onclick="closeOverlay('power-overlay')">TAP ANYWHERE TO CANCEL</div>
  </div>

  <div class="overlay" id="notif-overlay" onclick="overlayBackdrop(event,'notif-overlay')">
    <div class="notif-container">
      <div class="overlay-title" style="margin:20px 0 10px 0;">// SYSTEM LOGS</div>
      <div class="notif-list" id="notif-list"></div>
    </div>
    <div class="overlay-close" onclick="closeOverlay('notif-overlay')">TAP ANYWHERE TO CANCEL</div>
  </div>

<script>
// --- ICONS & STATE ---
const ICON_PLAY = `<svg viewBox="0 0 24 24"><polygon points="5,3 19,12 5,21"/></svg>`;
const ICON_PAUSE = `<svg viewBox="0 0 24 24"><rect x="6" y="3" width="4" height="18" rx="1"/><rect x="14" y="3" width="4" height="18" rx="1"/></svg>`;

const WS_ARCH = `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C11.5 2 11 2.4 10.8 2.8C9.8 5.4 8.5 8.5 7 12C6.3 13.7 5.6 15.3 5 17C4.4 18.7 3.8 20.3 3.6 21C3.5 21.3 3.6 21.5 3.8 21.7C4 21.9 4.2 21.9 4.4 21.8C7.8 21.3 11.2 20.3 14.5 18.8C14.8 18.7 15 18.4 15.1 18L15.9 13.2C15.9 12.9 15.7 12.7 15.4 12.7H8.6C8.3 12.7 8.1 12.9 8.1 13.2L8.9 18C9 18.4 9.2 18.7 9.5 18.8C12.8 20.3 16.2 21.3 19.6 21.8C19.8 21.9 20 21.9 20.2 21.7C20.4 21.5 20.5 21.3 20.4 21C20.2 20.3 19.6 18.7 19 17C18.4 15.3 17.7 13.7 17 12C15.5 8.5 14.2 5.4 13.2 2.8C13 2.4 12.5 2 12 2ZM12 9.2C12.2 9.2 12.3 9.3 12.4 9.4C12.8 9.7 13 10.2 12.9 10.7L12 16.2H12L11.1 10.7C11 10.2 11.2 9.7 11.6 9.4C11.7 9.3 11.8 9.2 12 9.2Z"/></svg>`;
const WS_FILLED = `<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="7"/></svg>`;
const WS_EMPTY = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><circle cx="12" cy="12" r="7"/></svg>`;

const S = { eco: false, fetchTimer: null, playing: false, primed: null, primedTmr: null, unlocked: false };

// --- LOCK ENGINE ---
let currentPin = "";
const dots = document.querySelectorAll('.pin-dot');

function updateDots() {
    dots.forEach((dot, index) => {
        if (index < currentPin.length) dot.classList.add('filled');
        else dot.classList.remove('filled');
    });
}

function clearPin() {
    currentPin = "";
    dots.forEach(dot => dot.classList.remove('error', 'filled'));
    document.getElementById('lock-msg').innerHTML = '// SYSTEM SECURE<br><span style="font-size:0.5rem; opacity:0.5; font-weight:normal;">ENTER PIN</span>';
    document.getElementById('lock-msg').style.color = '#fff';
    updateDots();
}

async function enterPin(num) {
    if (currentPin.length < 4) {
        currentPin += num;
        updateDots();
        if (currentPin.length === 4) {
            try {
                const res = await fetch('/api/auth', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ pin: currentPin })
                });
                const data = await res.json();
                
                if (data.valid) {
                    document.getElementById('lock-screen').classList.add('hidden');
                    document.getElementById('app').classList.add('unlocked');
                    S.unlocked = true;
                    document.getElementById('bg-video').play().catch(e=>{});
                    S.fetchTimer = setInterval(fetchTelemetry, 1000); 
                    fetchTelemetry();
                    initLandscape();
                } else {
                    dots.forEach(dot => dot.classList.add('error'));
                    document.getElementById('lock-msg').innerHTML = '// INTRUSION DETECTED<br><span style="font-size:0.5rem; opacity:0.5; font-weight:normal;">REROUTING...</span>';
                    document.getElementById('lock-msg').style.color = 'var(--danger)';
                    setTimeout(() => { window.location.href = '/dont'; }, 1000);
                }
            } catch(e) { clearPin(); }
        }
    }
}

// --- WAKELOCK ENGINE ---
let audioCtx;
const nosleepVideo = document.getElementById('nosleep-video');

async function triggerWakeLock() {
    if (!S.unlocked) return;
    nosleepVideo.play().catch(e=>{});
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === 'suspended') audioCtx.resume();
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    gain.gain.value = 0; 
    osc.connect(gain); gain.connect(audioCtx.destination); osc.start();

    if ('wakeLock' in navigator) {
        try {
            let wl = await navigator.wakeLock.request('screen');
            wl.addEventListener('release', () => { if (document.visibilityState === 'visible') setTimeout(triggerWakeLock, 500); });
        } catch (err) {}
    }
}
setInterval(() => { if (S.unlocked && nosleepVideo.paused) nosleepVideo.play().catch(e=>{}); }, 15000);
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') triggerWakeLock(); });

// --- LANDSCAPE ENGINE ---
async function initLandscape() {
    if (!S.unlocked) return;
    try {
        const el = document.documentElement;
        const rfs = el.requestFullscreen || el.webkitRequestFullscreen || el.mozRequestFullScreen;
        if (rfs) await rfs.call(el, { navigationUI: 'hide' });
    } catch(e){}

    try {
        if (screen.orientation && screen.orientation.lock) {
            await screen.orientation.lock('landscape');
            document.body.classList.remove('css-landscape');
            finalizeLandscape();
            return;
        }
    } catch (err) {}

    if (window.innerHeight > window.innerWidth) {
        document.body.classList.add('css-landscape');
    } else {
        document.body.classList.remove('css-landscape');
    }
    finalizeLandscape();
}

function finalizeLandscape() {
    document.getElementById('btn-sync').style.display = 'none'; 
    triggerWakeLock();
}

window.addEventListener('resize', () => {
    if (document.body.classList.contains('css-landscape')) {
        if (window.innerWidth > window.innerHeight) document.body.classList.remove('css-landscape');
    }
});

// --- CLOCK ---
function tickClock() {
    const n = new Date();
    document.getElementById('time-hm').textContent = `${String(n.getHours()).padStart(2,'0')}:${String(n.getMinutes()).padStart(2,'0')}`;
    document.getElementById('time-s').textContent = String(n.getSeconds()).padStart(2,'0');
    const opts = { weekday: 'long', month: 'short', day: 'numeric', year: 'numeric' };
    document.getElementById('date-display').textContent = n.toLocaleDateString('en-US', opts).toUpperCase();
}
setInterval(tickClock, 1000); tickClock();

// --- TELEMETRY ---
async function fetchTelemetry() {
    if (!S.unlocked) return;
    const mediaCtrls = document.getElementById('media-ctrls');
    try {
        const r = await fetch('/api/telemetry');
        if (!r.ok) throw new Error("API Disconnected");
        const d = await r.json();

        document.getElementById('cpu-val').textContent = Math.round(d.sys.cpu) + '%';
        document.getElementById('ram-val').textContent = Math.round(d.sys.ram) + '%';
        document.getElementById('vol-val').textContent = d.sys.vol + '%';

        const titleEl = document.getElementById('track-title');
        titleEl.textContent = d.media.title;
        titleEl.classList.remove('error-state');
        const containerW = document.querySelector('.marquee-wrap').offsetWidth;
        if (titleEl.scrollWidth > containerW && d.media.title !== "Station Idle") titleEl.classList.add('scrolling');
        else titleEl.classList.remove('scrolling');

        document.getElementById('artist-name').textContent = d.media.artist;
        S.playing = d.media.status === 'Playing';
        document.getElementById('btn-playpause').innerHTML = S.playing ? ICON_PAUSE : ICON_PLAY;

        if (d.media.status === "Stopped" || d.media.title === "Station Idle") {
            mediaCtrls.style.display = 'none';
        } else {
            mediaCtrls.style.display = 'flex';
        }

        if (d.unread) document.getElementById('notif-badge').style.display = 'block';

        // UPDATE WORKSPACE PILL
        const wsInfo = d.workspace;
        const dotsContainer = document.getElementById('workspace-pill');
        let dotsHtml = '';
        
        const maxDots = Math.max(5, wsInfo.active, ...wsInfo.occupied);
        
        for(let i=1; i<=maxDots; i++) {
            if (i === wsInfo.active) {
                dotsHtml += `<div class="ws-icon active">${WS_ARCH}</div>`;
            } else if (wsInfo.occupied.includes(i)) {
                dotsHtml += `<div class="ws-icon occupied">${WS_FILLED}</div>`;
            } else {
                dotsHtml += `<div class="ws-icon">${WS_EMPTY}</div>`;
            }
        }
        dotsContainer.innerHTML = dotsHtml;

    } catch (_) {
        document.getElementById('track-title').textContent = "API DISCONNECTED";
        document.getElementById('track-title').classList.add('error-state');
        document.getElementById('track-title').classList.remove('scrolling');
        document.getElementById('artist-name').textContent = "─";
        mediaCtrls.style.display = 'none';
    }
}

// --- ACTIONS ---
async function mediaCtrl(action) {
    try { await fetch('/api/media', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action})}); } catch(e){}
    setTimeout(fetchTelemetry, 300);
}

function toggleEco() {
    S.eco = !S.eco;
    document.getElementById('btn-eco').classList.toggle('active');
    const vid = document.getElementById('bg-video');
    const vis = document.getElementById('visualizer');
    
    if (S.eco) {
        vid.pause(); vid.style.opacity = '0'; vis.style.opacity = '0'; document.getElementById('app').style.background = '#000';
        clearInterval(S.fetchTimer); S.fetchTimer = setInterval(fetchTelemetry, 5000);
    } else {
        vid.play().catch(e=>{}); vid.style.opacity = '1'; vis.style.opacity = '0.25'; document.getElementById('app').style.background = 'transparent';
        clearInterval(S.fetchTimer); S.fetchTimer = setInterval(fetchTelemetry, 1000);
    }
    triggerWakeLock();
}

// --- OVERLAYS ---
function overlayBackdrop(e, id) { if (e.target === e.currentTarget) closeOverlay(id); }
function closeOverlay(id) { 
    document.getElementById(id).classList.remove('active'); 
    if(id==='power-overlay') resetPrimed();
}

function openPower() { document.getElementById('power-overlay').classList.add('active'); triggerWakeLock(); }
function resetPrimed() {
    clearTimeout(S.primedTmr); S.primed = null;
    document.querySelectorAll('.power-btn').forEach(b => b.classList.remove('primed'));
}
async function powerAction(action) {
    if (action === 'lock') {
        await fetch('/api/power', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action})});
        closeOverlay('power-overlay'); return;
    }
    if (S.primed === action) {
        resetPrimed();
        await fetch('/api/power', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action})});
        closeOverlay('power-overlay');
    } else {
        resetPrimed(); S.primed = action;
        document.getElementById(`pbtn-${action}`).classList.add('primed');
        S.primedTmr = setTimeout(resetPrimed, 3000);
    }
}

async function openNotifs() {
    document.getElementById('notif-badge').style.display = 'none';
    document.getElementById('notif-overlay').classList.add('active');
    triggerWakeLock();
    try {
        const r = await fetch('/api/notifications');
        const ns = await r.json();
        const list = document.getElementById('notif-list');
        if (!ns.length) { list.innerHTML = '<div class="notif-empty">NO NOTIFICATIONS</div>'; return; }
        list.innerHTML = ns.map(n => `
            <div class="notif-item">
                <div class="notif-app">${n.app}</div>
                <div class="notif-title">${n.title}</div>
                <div class="notif-body">${n.body}</div>
                <div class="notif-time">${n.time}</div>
            </div>`).join('');
    } catch(e){}
}

// --- GESTURE NAVIGATION (SWIPE ENGINE WITH FEEDBACK) ---
let touchStartX = 0;
let touchStartY = 0;
const appDiv = document.getElementById('app');
const launcherWrapper = document.getElementById('app-launcher-wrapper');

document.addEventListener('touchstart', e => { 
    if (!S.unlocked) return;
    touchStartX = e.touches[0].clientX;
    touchStartY = e.touches[0].clientY; 
}, {passive: true});

document.addEventListener('touchend', e => {
    if (!S.unlocked) return;
    const touchEndX = e.changedTouches[0].clientX;
    const touchEndY = e.changedTouches[0].clientY;
    
    const dx = touchStartX - touchEndX;
    const dy = touchStartY - touchEndY;
    
    if (Math.abs(dx) > Math.abs(dy)) {
        // Horizontal Swipe (Workspace Navigation)
        if (Math.abs(dx) > 60 && !document.querySelector('.overlay.active') && !launcherWrapper.classList.contains('active')) {
            if (dx > 0) {
                // Swipe Left -> Next Workspace (Dashboard shifts left)
                appDiv.classList.add('workspace-switch-next');
                changeWorkspace('next');
            } else {
                // Swipe Right -> Prev Workspace (Dashboard shifts right)
                appDiv.classList.add('workspace-switch-prev');
                changeWorkspace('prev');
            }
            
            // Remove the shift/zoom effect after it visually completes
            setTimeout(() => {
                appDiv.classList.remove('workspace-switch-next', 'workspace-switch-prev');
            }, 300);
        }
    } else {
        // Vertical Swipe (App Launcher)
        if (dy > 50 && !document.querySelector('.overlay.active')) {
            launcherWrapper.classList.add('active');
            appDiv.classList.add('launcher-open');
            triggerWakeLock();
        }
    }
}, {passive: true});

async function changeWorkspace(dir) {
    try { await fetch('/api/workspace', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({direction: dir})}); } catch(e){}
    setTimeout(fetchTelemetry, 150); // Fast UI snap
}

launcherWrapper.addEventListener('click', e => {
    if (e.target === launcherWrapper) closeLauncher();
});

function closeLauncher() {
    launcherWrapper.classList.remove('active');
    appDiv.classList.remove('launcher-open');
}

async function launchApp(appId) {
    try { await fetch('/api/launch', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({app: appId})}); } catch(e){}
    closeLauncher();
}

// --- VISUALIZER ---
(function initVis() {
    const canvas = document.getElementById('visualizer');
    const ctx = canvas.getContext('2d');
    const N = 60; const bars = new Float32Array(N); const targets = new Float32Array(N);
    const phases = new Float32Array(N).map((_, i) => i * 0.19 + Math.random() * 0.5);
    let W, H;
    
    function resize() { 
        W = canvas.clientWidth; H = canvas.clientHeight; 
        canvas.width = W; canvas.height = H;
        bars.fill(0); targets.fill(0);
    }
    window.addEventListener('resize', resize); resize();

    let lastTs = 0;
    function draw(ts) {
        requestAnimationFrame(draw);
        if (S.eco || !S.unlocked) return;
        const dt = Math.min((ts - lastTs) / 1000, 0.05); lastTs = ts;

        for (let i = 0; i < N; i++) {
            phases[i] += (S.playing ? 1.6 : 0.3) * dt;
            const t = phases[i]; const pos = i / N;
            const wave = (Math.sin(t)*0.4 + Math.sin(t*1.7+1)*0.25 + Math.sin(t*2.9+2)*0.15)*0.5 + 0.5;
            const bell = Math.exp(-Math.pow((pos-0.28)/0.22, 2))*0.7 + Math.exp(-Math.pow((pos-0.70)/0.18, 2))*0.3;
            const noise = S.playing ? (Math.random()-0.5)*0.28 : 0;
            targets[i] = Math.max(0.01, Math.min(0.97, (wave * bell + noise) * (S.playing ? 1.0 : 0.07) + 0.018));
            bars[i] += (targets[i] - bars[i]) * 0.10;
        }

        ctx.clearRect(0, 0, W, H);
        const barW = W / N; const gap = Math.max(1, barW * 0.16); const bw = barW - gap;
        for (let i = 0; i < N; i++) {
            const x = i * barW + gap * 0.5; const bh = Math.max(1, bars[i] * H); const y = H - bh;
            const a = 0.30 + bars[i] * 0.70;
            ctx.fillStyle = `rgba(255,255,255,${(a).toFixed(3)})`;
            ctx.fillRect(x, y, bw, bh);
        }
    }
    requestAnimationFrame(draw);
})();
</script>
</body>
</html>
""" 

if __name__ == '__main__':
    logging.info(f"Starting System Station... (PIN IS SET TO: {SECRET_PIN})")
    app.run(host='0.0.0.0', port=5000, threaded=True)
