# Knctrl // Station

A hyper-customized, mobile-first command center built for Arch Linux and the Hyprland window manager. 

This project bridges a mobile device (optimized for landscape orientation) with an Arch Linux desktop. It provides real-time system telemetry, media control, power management, and gesture-based workspace navigation—all wrapped in a sleek, glassmorphic UI.

##  Features

* **Real-Time Telemetry:** Streams live CPU, RAM, and active audio volume via `psutil` and `pactl`.
* **Now Playing Integration:** Hooks into `playerctl` to display current track metadata and control playback.
* **Waybar-Style Workspace Indicator:** Talks directly to Hyprland's IPC (`hyprctl`) to generate a dynamic workspace pill. It features the Arch Linux logo for your active workspace, solid dots for occupied workspaces, and hollow dots for empty ones.
* **Gesture Navigation Engine:** Native JavaScript touch events allow for physical UI control:
  * **Swipe Left/Right:** Shifts Hyprland workspaces (`m+1` / `m-1`) with a satisfying, animated zoom-and-shift physical feedback effect.
  * **Swipe Up:** Deploys the App Drawer.
* **Monochromatic App Drawer:** A hidden 3x2 glassmorphic grid launcher that boots native Linux apps and flatpaks (Discord, Steam, Floorp, Terminal, Files, Btop) via detached subprocesses. 
* **System Logs:** Actively monitors the `org.freedesktop.Notifications` D-Bus to intercept and display desktop notifications on the mobile device.
* **PIN Security:** An intercepting lock screen that prevents unauthorized local network execution.
* **The Shadow Realm (Easter Egg):** Entering the wrong PIN or navigating to `/dont` traps the intruder in a cursed, visually aggressive 1998 Geocities nightmare loop that endlessly triggers alert popups of the *Bee Movie* script.

## Architecture

* **Backend:** Python (Flask, Flask-CORS) acting as a lightweight, localized API server.
* **Frontend:** Pure HTML/CSS/JS dynamically served by the Python script. Zero external dependencies, frameworks, or CDNs.
* **System Hooks:** `systemctl`, `loginctl`, `hyprctl`, `dbus-monitor`, `playerctl`.

##  Installation & Usage

### Prerequisites
You must be running Linux (Arch preferred + Hyprland) with the following system packages installed:
`sudo pacman -S python-psutil playerctl btop`

You also need the Python Flask library:
`pip install flask flask-cors psutil`

### Deployment
1. Clone the repository: `git clone https://github.com/YOUR_USERNAME/system-station.git`
2. Open `knctrl_station.py` and locate the **USER CONFIGURATION** block at the top.
3. Change `SECRET_PIN` to your desired 4-digit code.
4. Change `WALLPAPER_PATH` to point to a looping `.mp4` background of your choice (if left invalid, the UI safely defaults to a solid void-black background).
5. Run the server: `python knctrl_station.py`
6. On your mobile device, navigate to `http://[YOUR_PC_IP]:5000` and lock your screen to landscape orientation.

## ⚠ Disclaimer
This script executes raw shell commands (`subprocess.Popen`) based on network requests. It is designed to be run **strictly on a secure, trusted local area network (LAN)**. Do not expose port 5000 to the public internet unless you want the entire world swiping through your workspaces.
