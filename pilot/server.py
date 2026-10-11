"""A small, token-protected web control panel for Pilot.

Run it on the machine Pilot controls (e.g. your Linux VM):

    pilot --serve --host 0.0.0.0 --port 8080

Then open http://<machine-ip>:8080/?token=<token> on your phone. From there you
can watch what Pilot is doing live, send it tasks, answer its questions (2FA
codes, confirmations), and stop it. "Add to Home screen" installs it like an app.

Security: every request needs the token. Prefer binding to 127.0.0.1 and using
an SSH tunnel, or restrict the port with your VM firewall / cloud security list.
Serving on 0.0.0.0 exposes control of this machine to anyone with the token.
"""
from __future__ import annotations

import hmac
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlparse

from .config import Config
from .runner import AgentRunner

PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0b0f17">
<title>Pilot</title>
<link rel="manifest" href="manifest.webmanifest?token=__TOKEN__">
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 15px/1.45 system-ui, sans-serif; background: #0b0f17; color: #e5e9f0; }
  header { padding: 12px 16px; background: #121826; position: sticky; top: 0; display: flex; gap: 10px; align-items: center; }
  header b { font-size: 16px; } .muted { color: #8b95a7; font-size: 13px; }
  #log { padding: 12px 16px 120px; }
  .ev { padding: 6px 10px; margin: 6px 0; border-radius: 8px; background: #121826; white-space: pre-wrap; word-break: break-word; }
  .ev.task { background: #1c2640; } .ev.say { background: #163a2b; }
  .ev.tool { color: #9fc3ff; } .ev.result { color: #8b95a7; font-size: 13px; }
  .ev.info { color: #ffd58a; } .ev .k { font-size: 11px; text-transform: uppercase; color: #6b7280; opacity: .6; margin-right: 6px; }
  footer { position: fixed; bottom: 0; left: 0; right: 0; background: #121826; padding: 10px 12px; display: flex; gap: 8px; border-top: 1px solid #222c40; }
  input, button { font: inherit; border-radius: 8px; border: 1px solid #2a3550; }
  #task { flex: 1; padding: 11px; background: #0b0f17; color: #e5e9f0; }
  button { padding: 11px 14px; background: #2d6cf6; color: #fff; border: none; }
  button.stop { background: #b4353a; } button:disabled { opacity: .5; }
  #prompt { display: none; padding: 12px 16px; background: #2a2140; border-bottom: 1px solid #4a3a7a; position: sticky; top: 49px; }
  #prompt .q { margin-bottom: 8px; } #prompt .row { display: flex; gap: 8px; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: #4a556b; }
  .dot.on { background: #41d18f; }
</style></head><body>
<header><span class="dot" id="dot"></span><b>Pilot</b><span class="muted" id="status">connecting...</span></header>
<div id="prompt"><div class="q" id="promptText"></div>
  <div class="row"><input id="promptInput" placeholder="your answer"><button id="promptSend">Send</button></div>
</div>
<div id="log"></div>
<footer>
  <input id="task" placeholder="Tell Pilot what to do..." autocomplete="off">
  <button id="send">Run</button><button class="stop" id="stop">Stop</button>
</footer>
<script>
const qs = new URLSearchParams(location.search);
const TOKEN = qs.get('token') || '';
let since = 0, curPrompt = null;
const log = document.getElementById('log');
function esc(s){ return s.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function add(ev){
  const d = document.createElement('div');
  d.className = 'ev ' + ev.kind;
  d.innerHTML = '<span class="k">'+ev.kind+'</span>' + esc(ev.text);
  log.appendChild(d); window.scrollTo(0, document.body.scrollHeight);
}
async function api(path, opts){
  opts = opts || {}; opts.headers = Object.assign({'X-Pilot-Token': TOKEN}, opts.headers||{});
  const r = await fetch(path + (path.includes('?')?'&':'?') + 'token=' + encodeURIComponent(TOKEN), opts);
  if(!r.ok) throw new Error('http '+r.status);
  return r.json();
}
async function poll(){
  try {
    const s = await api('/api/state?since=' + since);
    document.getElementById('dot').classList.add('on');
    for(const ev of s.events){ add(ev); since = ev.id; }
    const st = s.status;
    document.getElementById('status').textContent =
      (st.running ? 'working: ' + (st.current_task||'') : 'idle') +
      ' | ' + st.model + ' | ' + st.keys + ' key(s)';
    showPrompt(st.pending);
  } catch(e) {
    document.getElementById('dot').classList.remove('on');
    document.getElementById('status').textContent = 'disconnected';
  }
}
function showPrompt(p){
  const box = document.getElementById('prompt');
  if(p && (!curPrompt || curPrompt.id !== p.id)){
    curPrompt = p; box.style.display = 'block';
    document.getElementById('promptText').textContent = p.text;
    const inp = document.getElementById('promptInput');
    inp.style.display = p.kind === 'confirm' ? 'none' : 'block';
    document.getElementById('promptSend').textContent = p.kind === 'confirm' ? 'Allow' : 'Send';
    if(p.kind !== 'confirm') inp.focus();
  } else if(!p){ curPrompt = null; box.style.display = 'none'; }
}
async function sendAnswer(){
  if(!curPrompt) return;
  const inp = document.getElementById('promptInput');
  const value = curPrompt.kind === 'confirm' ? true : inp.value;
  await api('/api/answer', {method:'POST', body: JSON.stringify({id: curPrompt.id, value})});
  inp.value=''; curPrompt=null; document.getElementById('prompt').style.display='none'; poll();
}
document.getElementById('promptSend').onclick = sendAnswer;
document.getElementById('send').onclick = async () => {
  const t = document.getElementById('task'); if(!t.value.trim()) return;
  await api('/api/task', {method:'POST', body: JSON.stringify({task: t.value})}); t.value=''; poll();
};
document.getElementById('task').addEventListener('keydown', e => { if(e.key==='Enter') document.getElementById('send').click(); });
document.getElementById('stop').onclick = () => api('/api/stop', {method:'POST'});
setInterval(poll, 1200); poll();
if('serviceWorker' in navigator){ navigator.serviceWorker.register('sw.js?token='+encodeURIComponent(TOKEN)).catch(()=>{}); }
</script></body></html>
"""

MANIFEST = {
    "name": "Pilot", "short_name": "Pilot", "start_url": "./?token=__TOKEN__",
    "display": "standalone", "background_color": "#0b0f17", "theme_color": "#0b0f17",
    "icons": [{"src": "icon.svg?token=__TOKEN__", "sizes": "any", "type": "image/svg+xml"}],
}

ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">'
    '<rect width="128" height="128" rx="28" fill="#2d6cf6"/>'
    '<path d="M64 26 L96 96 L64 80 L32 96 Z" fill="#fff"/></svg>'
)
SW_JS = "self.addEventListener('fetch', () => {});\n"  # no-op SW, just enables install


def _make_handler(runner: AgentRunner, token: str):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # keep the console quiet
            pass

        def _token_ok(self) -> bool:
            q = parse_qs(urlparse(self.path).query)
            supplied = (self.headers.get("X-Pilot-Token") or (q.get("token") or [""])[0])
            return hmac.compare_digest(supplied, token)

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return {}

        def do_GET(self):
            path = urlparse(self.path).path
            if not self._token_ok():
                return self._send(401, b"Unauthorized: add ?token=... to the URL", "text/plain")
            if path in ("/", "/index.html"):
                return self._send(200, PAGE.replace("__TOKEN__", token).encode("utf-8"), "text/html; charset=utf-8")
            if path == "/manifest.webmanifest":
                body = json.dumps(MANIFEST).replace("__TOKEN__", token).encode("utf-8")
                return self._send(200, body, "application/manifest+json")
            if path == "/sw.js":
                return self._send(200, SW_JS.encode("utf-8"), "application/javascript")
            if path == "/icon.svg":
                return self._send(200, ICON_SVG.encode("utf-8"), "image/svg+xml")
            if path == "/api/state":
                q = parse_qs(urlparse(self.path).query)
                since = int((q.get("since") or ["0"])[0])
                return self._json({"events": runner.events_since(since), "status": runner.status()})
            return self._send(404, b"not found", "text/plain")

        def do_POST(self):
            path = urlparse(self.path).path
            if not self._token_ok():
                return self._json({"error": "unauthorized"}, 401)
            if path == "/api/task":
                task = str(self._read_json().get("task", "")).strip()
                if not task:
                    return self._json({"error": "empty task"}, 400)
                runner.submit(task)
                return self._json({"ok": True})
            if path == "/api/answer":
                data = self._read_json()
                ok = runner.answer(int(data.get("id", -1)), data.get("value"))
                return self._json({"ok": ok})
            if path == "/api/stop":
                runner.stop()
                return self._json({"ok": True})
            return self._json({"error": "not found"}, 404)

    return Handler


def run_server(config: Config) -> int:
    token = config.server.token or secrets.token_urlsafe(18)
    runner = AgentRunner(config)
    handler = _make_handler(runner, token)
    httpd = ThreadingHTTPServer((config.server.host, config.server.port), handler)

    host_shown = "127.0.0.1" if config.server.host in ("0.0.0.0", "") else config.server.host
    print("Pilot control panel is running.")
    print(f"  Local:   http://{host_shown}:{config.server.port}/?token={token}")
    if config.server.host in ("0.0.0.0", ""):
        print(f"  Network: http://<this-machine-ip>:{config.server.port}/?token={token}")
        print("  WARNING: bound to 0.0.0.0 -- anyone who can reach this port and has the")
        print("           token can control this machine. Prefer an SSH tunnel or a firewall rule.")
    print("  Open it on your phone and 'Add to Home screen' to install it like an app.")
    print("  Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        httpd.shutdown()
        runner.close()
    return 0
