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
from .scheduler import Scheduler

PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0b0f17">
<title>Pilot</title>
<link rel="manifest" href="manifest.webmanifest?token=__TOKEN__">
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  /* Fixed header + footer; body padding keeps content clear of both. */
  body { margin: 0; font: 15px/1.45 system-ui, sans-serif; background: #0b0f17; color: #e5e9f0;
         padding: 54px 0 74px; }
  header { position: fixed; top: 0; left: 0; right: 0; z-index: 100; height: 54px;
           padding: 0 16px; background: #121826; border-bottom: 1px solid #222c40;
           display: flex; gap: 10px; align-items: center; }
  header b { font-size: 16px; flex: none; }
  /* status stays on one line and ellipsizes, so the header height never grows */
  .muted { color: #8b95a7; font-size: 13px; flex: 1; min-width: 0;
           white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #log { padding: 10px 16px; }
  .ev { padding: 8px 10px; margin: 8px 0; border-radius: 8px; background: #121826;
        white-space: pre-wrap; overflow-wrap: anywhere; }
  .ev.task { background: #1c2640; } .ev.say { background: #163a2b; }
  .ev.tool { color: #9fc3ff; } .ev.result { color: #8b95a7; font-size: 13px; }
  .ev.info { color: #ffd58a; }
  /* label on its own line above the text -- no overlap with long content */
  .ev .k { display: block; margin-bottom: 3px; font-size: 11px; letter-spacing: .05em;
           text-transform: uppercase; color: #8b95a7; opacity: .75; }
  footer { position: fixed; bottom: 0; left: 0; right: 0; z-index: 100; background: #121826;
           padding: 10px 12px; display: flex; gap: 8px; align-items: center; border-top: 1px solid #222c40; }
  input, button { font: inherit; border-radius: 8px; border: 1px solid #2a3550; }
  #task { flex: 1; min-width: 0; padding: 11px; background: #0b0f17; color: #e5e9f0; }
  button { padding: 11px 14px; background: #2d6cf6; color: #fff; border: none; white-space: nowrap; }
  button.stop { background: #b4353a; } button:disabled { opacity: .5; }
  #attach { background: #2a3550; padding: 11px 10px; }
  #thumbs { position: fixed; bottom: 64px; left: 0; right: 0; z-index: 100;
            display: flex; gap: 6px; padding: 0 12px; flex-wrap: wrap; }
  #thumbs .t { position: relative; }
  #thumbs img { height: 54px; border-radius: 6px; border: 1px solid #2a3550; }
  #thumbs .x { position: absolute; top: -6px; right: -6px; background: #b4353a; color: #fff;
               border-radius: 50%; width: 20px; height: 20px; line-height: 20px; text-align: center; font-size: 13px; }
  #prompt { display: none; margin: 10px 12px 0; padding: 12px; background: #2a2140;
            border: 1px solid #4a3a7a; border-radius: 8px; }
  #prompt .q { margin-bottom: 8px; } #prompt .row { display: flex; gap: 8px; }
  #prompt input { flex: 1; min-width: 0; padding: 11px; background: #0b0f17; color: #e5e9f0; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: #4a556b; flex: none; }
  .dot.on { background: #41d18f; }
  #schedBtn { flex: none; background: #2a3550; padding: 7px 10px; font-size: 13px; }
  /* Timers panel: fixed between header and footer, scrolls on its own */
  #sched { display: none; position: fixed; top: 54px; bottom: 64px; left: 0; right: 0; z-index: 90;
           overflow-y: auto; background: #0b0f17; padding: 12px 16px; }
  #sched h3 { margin: 4px 0 10px; font-size: 15px; }
  #sched .form { display: grid; gap: 8px; background: #121826; padding: 12px; border-radius: 8px; }
  #sched .form .row { display: flex; gap: 8px; }
  #sched input, #sched select, #sched textarea { flex: 1; min-width: 0; padding: 10px; background: #0b0f17;
           color: #e5e9f0; font: inherit; border-radius: 8px; border: 1px solid #2a3550; }
  #sched textarea { min-height: 64px; resize: vertical; }
  #sched .note { color: #8b95a7; font-size: 12px; }
  #sched .err { color: #ff8a8a; font-size: 13px; min-height: 1em; }
  .job { background: #121826; border-radius: 8px; padding: 10px; margin: 8px 0; }
  .job .t { white-space: pre-wrap; overflow-wrap: anywhere; }
  .job .meta { color: #8b95a7; font-size: 12px; margin: 4px 0 8px; }
  .job .acts { display: flex; gap: 8px; }
  .job .acts button { padding: 7px 12px; font-size: 13px; }
  .job.off { opacity: .55; }
</style></head><body>
<header><span class="dot" id="dot"></span><b>Pilot</b><span class="muted" id="status">connecting...</span>
  <button id="schedBtn">Timers</button></header>
<div id="sched">
  <h3>Timers <span class="note">(times are IST)</span></h3>
  <div class="form">
    <textarea id="sTask" placeholder="What should Pilot do? e.g. open gmail and read my unread subjects"></textarea>
    <div class="row">
      <select id="sEvery">
        <option value="once">Once</option>
        <option value="daily">Every day</option>
        <option value="weekly">Every week</option>
      </select>
      <input id="sTime" type="time" value="09:00">
    </div>
    <div class="row">
      <input id="sDate" type="date">
      <select id="sWeekday" style="display:none">
        <option value="mon">Mon</option><option value="tue">Tue</option><option value="wed">Wed</option>
        <option value="thu">Thu</option><option value="fri">Fri</option><option value="sat">Sat</option>
        <option value="sun">Sun</option>
      </select>
    </div>
    <div class="err" id="sErr"></div>
    <div class="row"><button id="sAdd">Add timer</button></div>
    <div class="note">Unattended runs need auto-approve on, or a task may pause waiting for a confirmation.</div>
  </div>
  <div id="jobs"></div>
</div>
<div id="prompt"><div class="q" id="promptText"></div>
  <div class="row"><input id="promptInput" placeholder="your answer"><button id="promptSend">Send</button></div>
</div>
<div id="log"></div>
<div id="thumbs"></div>
<footer>
  <input type="file" id="file" accept="image/*" multiple style="display:none">
  <button id="attach" title="Attach image">+Img</button>
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
  const nearBottom = (window.innerHeight + window.scrollY) >= (document.body.scrollHeight - 140);
  const d = document.createElement('div');
  d.className = 'ev ' + ev.kind;
  d.innerHTML = '<span class="k">'+ev.kind+'</span>' + esc(ev.text);
  log.appendChild(d);
  if(nearBottom) window.scrollTo(0, document.body.scrollHeight);
}
async function api(path, opts){
  opts = opts || {}; opts.headers = Object.assign({'X-Pilot-Token': TOKEN}, opts.headers||{});
  const r = await fetch(path + (path.includes('?')?'&':'?') + 'token=' + encodeURIComponent(TOKEN), opts);
  if(!r.ok){
    let msg = 'http '+r.status;
    try { const j = await r.json(); if(j && j.error) msg = j.error; } catch(e){}
    throw new Error(msg);
  }
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
let pendingImages = [];
function renderThumbs(){
  const box = document.getElementById('thumbs'); box.innerHTML = '';
  pendingImages.forEach((url, i) => {
    const d = document.createElement('div'); d.className = 't';
    d.innerHTML = '<img src="'+url+'"><div class="x">x</div>';
    d.querySelector('.x').onclick = () => { pendingImages.splice(i,1); renderThumbs(); };
    box.appendChild(d);
  });
}
function downscale(file){
  return new Promise(res => {
    const img = new Image(); const r = new FileReader();
    r.onload = () => { img.onload = () => {
      const max = 1280; let {width:w, height:h} = img;
      if(Math.max(w,h) > max){ const s = max/Math.max(w,h); w = Math.round(w*s); h = Math.round(h*s); }
      const c = document.createElement('canvas'); c.width = w; c.height = h;
      c.getContext('2d').drawImage(img, 0, 0, w, h);
      res(c.toDataURL('image/jpeg', 0.82));
    }; img.src = r.result; };
    r.readAsDataURL(file);
  });
}
document.getElementById('attach').onclick = () => document.getElementById('file').click();
document.getElementById('file').onchange = async (e) => {
  for(const f of e.target.files){ if(pendingImages.length >= 4) break; pendingImages.push(await downscale(f)); }
  e.target.value = ''; renderThumbs();
};
document.getElementById('send').onclick = async () => {
  const t = document.getElementById('task');
  if(!t.value.trim() && !pendingImages.length) return;
  await api('/api/task', {method:'POST', body: JSON.stringify({task: t.value, images: pendingImages})});
  t.value=''; pendingImages = []; renderThumbs(); poll();
};
document.getElementById('task').addEventListener('keydown', e => { if(e.key==='Enter') document.getElementById('send').click(); });
document.getElementById('stop').onclick = () => api('/api/stop', {method:'POST'});

// --- Timers ---
const sched = document.getElementById('sched');
document.getElementById('schedBtn').onclick = () => {
  const open = sched.style.display !== 'block';
  sched.style.display = open ? 'block' : 'none';
  if(open) loadJobs();
};
function syncEveryFields(){
  const v = document.getElementById('sEvery').value;
  document.getElementById('sDate').style.display = v === 'once' ? 'block' : 'none';
  document.getElementById('sWeekday').style.display = v === 'weekly' ? 'block' : 'none';
}
document.getElementById('sEvery').onchange = syncEveryFields;
(function(){ const d = new Date(); const p = n => String(n).padStart(2,'0');
  document.getElementById('sDate').value = d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate()); })();
syncEveryFields();
async function loadJobs(){
  let jobs = [];
  try { jobs = (await api('/api/schedule')).jobs || []; } catch(e){ return; }
  const box = document.getElementById('jobs'); box.innerHTML = '';
  if(!jobs.length){ box.innerHTML = '<div class="note">No timers yet.</div>'; return; }
  for(const j of jobs){
    const d = document.createElement('div'); d.className = 'job' + (j.enabled ? '' : ' off');
    d.innerHTML = '<div class="t">'+esc(j.task)+'</div>'
      + '<div class="meta">'+esc(j.desc)+(j.enabled ? ' · next: '+esc(j.next_human) : ' · paused')+'</div>'
      + '<div class="acts"><button class="tg">'+(j.enabled?'Pause':'Resume')+'</button>'
      + '<button class="stop del">Delete</button></div>';
    d.querySelector('.tg').onclick = async () => {
      await api('/api/schedule/toggle', {method:'POST', body: JSON.stringify({id:j.id, enabled: !j.enabled})}); loadJobs();
    };
    d.querySelector('.del').onclick = async () => {
      await api('/api/schedule/delete', {method:'POST', body: JSON.stringify({id:j.id})}); loadJobs();
    };
    box.appendChild(d);
  }
}
document.getElementById('sAdd').onclick = async () => {
  const err = document.getElementById('sErr'); err.textContent = '';
  const body = {
    task: document.getElementById('sTask').value,
    every: document.getElementById('sEvery').value,
    time: document.getElementById('sTime').value,
    date: document.getElementById('sDate').value,
    weekday: document.getElementById('sWeekday').value,
  };
  try {
    await api('/api/schedule', {method:'POST', body: JSON.stringify(body)});
    document.getElementById('sTask').value = ''; loadJobs();
  } catch(e){ err.textContent = e.message || 'Could not add timer.'; }
};

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


def _make_handler(runner: AgentRunner, token: str, scheduler=None):
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
            if length > 25 * 1024 * 1024:  # 25 MB guard (a few downscaled images)
                self.rfile.read(min(length, 1024))
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
            if path == "/api/schedule":
                return self._json({"jobs": scheduler.list() if scheduler else []})
            return self._send(404, b"not found", "text/plain")

        def do_POST(self):
            path = urlparse(self.path).path
            if not self._token_ok():
                return self._json({"error": "unauthorized"}, 401)
            if path == "/api/task":
                data = self._read_json()
                task = str(data.get("task", "")).strip()
                images = data.get("images") or []
                # Keep only a few data: image URLs (the dashboard already downscales).
                images = [u for u in images if isinstance(u, str) and u.startswith("data:image/")][:4]
                if not task and not images:
                    return self._json({"error": "empty task"}, 400)
                runner.submit(task, images)
                return self._json({"ok": True})
            if path == "/api/answer":
                data = self._read_json()
                ok = runner.answer(int(data.get("id", -1)), data.get("value"))
                return self._json({"ok": ok})
            if path == "/api/stop":
                runner.stop()
                return self._json({"ok": True})
            if path == "/api/schedule":
                if not scheduler:
                    return self._json({"error": "scheduler off"}, 400)
                d = self._read_json()
                try:
                    job = scheduler.add(
                        task=str(d.get("task", "")),
                        every=str(d.get("every", "once")),
                        time_str=str(d.get("time", "")),
                        date=d.get("date"),
                        weekday=d.get("weekday"),
                        tz=d.get("tz"),
                    )
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                return self._json({"ok": True, "id": job["id"]})
            if path == "/api/schedule/delete":
                ok = scheduler and scheduler.remove(str(self._read_json().get("id", "")))
                return self._json({"ok": bool(ok)})
            if path == "/api/schedule/toggle":
                d = self._read_json()
                ok = scheduler and scheduler.toggle(str(d.get("id", "")), bool(d.get("enabled")))
                return self._json({"ok": bool(ok)})
            return self._json({"error": "not found"}, 404)

    return Handler


def run_server(config: Config) -> int:
    token = config.server.token or secrets.token_urlsafe(18)
    runner = AgentRunner(config)
    scheduler = Scheduler(
        submit=runner.submit,
        path=str(config.config_dir / "schedule.json"),
        on_event=runner._record,
    )
    handler = _make_handler(runner, token, scheduler)
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
        scheduler.close()
        runner.close()
    return 0
