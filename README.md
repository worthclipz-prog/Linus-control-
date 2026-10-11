# Pilot

An AI agent that operates your computer. Tell it what you want in plain English and
it drives your browser, mouse, keyboard and terminal to do it. Runs on **Linux and
Windows**, on a desktop or a headless cloud VM, and you can drive it from your
**phone**. The brain is any vision + tool-calling model over an OpenAI-compatible
API: **GPT**, **Google Gemini** (free tier), or a **local model**.

```
pilot "log into gmail with my gmail secret and read me the subjects of my 5 newest emails"
pilot "open the calculator and work out 1234 * 5678"
pilot --gui                       # desktop window
pilot --serve --host 0.0.0.0      # phone / remote control panel
```

## Pick a brain (the model)

Pilot does not include a model; you point it at one. A subscription (ChatGPT Plus,
Gemini app, etc.) is **not** API access — Pilot needs an **API key**.

| Option | Cost | Setup |
|---|---|---|
| **Google Gemini** | **Free tier** available | Key at <https://aistudio.google.com/apikey>. Set `provider = "gemini"`, `name = "gemini-2.0-flash"` |
| **OpenAI** | Pay per use (separate from ChatGPT Plus) | Key at <https://platform.openai.com/api-keys>. `provider = "openai"`, `name = "gpt-4o"` |
| **Local model** (Ollama, LM Studio) | Free, runs on your own hardware | `provider = "ollama"`, `name = "qwen2.5vl:7b"`. Needs vision + tool calling; weaker, and slow without a GPU |
| **OpenRouter / Azure / other** | Varies | Set `base_url`, `name`, and the key |

### Gemini free tier — the limits

The free tier has a **per-minute** and a **per-day** request cap, and each *step* of a
task is one request (a 20-step task ≈ 20 requests), so a day's quota is a handful of
longer tasks. Pick a **fast** model like `gemini-2.0-flash`. Google may use free-tier
data to improve its products. The exact numbers change — check your key's limits page
in Google AI Studio.

### Stack several keys (automatic failover)

Give Pilot more than one key and it switches to the next one the moment a key is
rate-limited or out of quota — handy for stretching free tiers:

```toml
[model]
provider = "gemini"
name     = "gemini-2.0-flash"
api_keys = ["key-one", "key-two", "key-three"]
```

or in `.env`: `GEMINI_API_KEY=...`, then `PILOT_API_KEY_2=...`, `PILOT_API_KEY_3=...`.
When one runs out, the control panel/terminal shows "switching to key #2 of 3".

> **Never paste an API key, password, or private key into a chat.** Keys live only in
> your local `.env` / `config.toml` (both git-ignored) and are sent only to the model
> provider you configured. Your stored logins are never sent to the model at all.

## Install

**Windows** (PowerShell, from this folder):
```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
```

**Linux**:
```bash
bash scripts/install_linux.sh
```

Both scripts create a `.venv`, install everything, download the browsers, and copy
`config.example.toml` → `config.toml` and `.env.example` → `.env`. Then:

1. Put your key in `.env` (`OPENAI_API_KEY=...` or `GEMINI_API_KEY=...`) and set
   `provider`/`name` in `config.toml`.
2. Check everything: `pilot --doctor`

Manual install: `pip install -e .` then `python -m playwright install chromium firefox`.

## Use it

| Command | What it does |
|---|---|
| `pilot "task"` | Do one task and exit |
| `pilot -i` | Chat mode: give tasks one after another; it remembers context |
| `pilot --gui` | Small always-on-top window with a Run and a **Stop** button (on Windows, double-click `scripts\pilot.bat`) |
| `pilot --serve` | Start the web control panel (drive it from your phone) |
| `pilot --doctor` | Check that everything is installed |
| `pilot -y "task"` | Don't ask before shell commands or file writes |

More options: `pilot --help`. More example tasks: [`examples/tasks.md`](examples/tasks.md).

## Control it from your phone

`pilot --serve` starts a small web control panel on the machine Pilot runs on. From
your phone's browser you can watch, live, everything Pilot is doing, send it tasks,
answer its questions (2-factor codes, confirmations) and stop it.

```bash
pilot --serve --host 0.0.0.0 --port 8080
```

It prints a URL with an access token. Open it on your phone, then **"Add to Home
screen"** — it installs and launches like an app (it's a PWA). Want a real `.apk`? See
[Make an Android app (APK)](#make-an-android-app-apk).

**Security — this panel controls the machine, so treat the token like a password:**
- Every request needs the token; set a long one via `PILOT_SERVER_TOKEN` in `.env`.
- Safest is to keep the default `127.0.0.1` bind and reach it through an **SSH tunnel**:
  `ssh -L 8080:127.0.0.1:8080 user@your-vm-ip`, then open `http://127.0.0.1:8080/?token=...`.
- If you bind to `0.0.0.0`, anyone who can reach the port and has the token gets full
  control. Restrict the port in your cloud firewall / security list, and use HTTPS
  (e.g. behind a reverse proxy) if you can.

## Run on a Linux VM (no monitor)

On a headless server (such as an Oracle Cloud free-tier instance), Pilot runs a **real
browser inside a virtual display** (Xvfb), so sites behave as they would on a desktop.

```bash
bash scripts/install_vm_linux.sh     # installs Python, browsers, Xvfb
# put your key(s) and PILOT_SERVER_TOKEN in .env, set provider/name in config.toml
bash scripts/run_headless.sh         # starts Xvfb + the control panel
```

`run_headless.sh` honours `PILOT_HOST`, `PILOT_PORT` and `DISPLAY_NUM`. Then reach the
panel from your phone via the SSH tunnel (recommended) or an opened port, as above.

A free VM has no GPU and limited memory, so a **local** model will be slow there —
Gemini's free tier is the better brain for a free VM. The small 1 GB "Micro" shape is
too small to run comfortably; prefer the Ampere A1 shape.

### Make an Android app (APK)

The control panel is already installable from the phone browser ("Add to Home screen").
For a real `.apk` you wrap that URL — no app code to write:
- **PWABuilder** (<https://www.pwabuilder.com>): paste your panel's URL, download an
  Android package. Easiest.
- **Bubblewrap** (Google's CLI) or **Median.co**: same idea, a WebView around the URL.

Point the wrapper at a stable address (your SSH-tunnelled `localhost`, a VPN/Tailscale
address, or an HTTPS domain). I can't compile the APK for you here, but any of these
builds one in a few minutes.

## What it can do

**Browser** (the fast path, used for anything on the web)
- Open pages, click, type, scroll, press keys, go back and forward, reload
- Read page text, take screenshots, wait for text to appear
- Multiple tabs: open, list, switch
- Log in with your stored credentials (below)
- Stays logged in between runs (persistent browser profile)

**Desktop** (for native apps)
- Screenshots of the whole screen
- Mouse move, click, double-click, right-click, drag, scroll
- Typing and key combos (`ctrl+c`, `alt+tab`, `win+r` ...)
- Open apps by name

**System**
- Run shell commands (bash on Linux, PowerShell on Windows)
- Read, write and list files

**Talking to you**
- Asks you when it hits a one-time code, a CAPTCHA, or "approve on your phone" prompt, then carries on

## Browsers

Any Chromium-based browser or Firefox, set in `config.toml` under `[browser]`:

| Browser | Setting |
|---|---|
| Chromium (bundled, default) | `engine = "chromium"` |
| Your installed Google Chrome | `channel = "chrome"` |
| Microsoft Edge | `channel = "msedge"` |
| Brave, Vivaldi, Opera, any other Chromium | `executable_path = "...path to the .exe or binary..."` |
| Firefox | `engine = "firefox"` |

Or on the command line: `--engine firefox`, `--channel chrome`, `--browser-path "C:\...\brave.exe"`.

Tip: use a separate `user_data_dir` (the default is `browser-profile/`) rather than your
everyday browser profile. Chrome won't let two programs open the same profile at once.

## Logging in

Store each login once under a name, in `config.toml`:

```toml
[secrets.gmail]
username = "you@gmail.com"
password = "your-password"
```

or in `.env`:

```
PILOT_SECRET_GMAIL_USER=you@gmail.com
PILOT_SECRET_GMAIL_PASS=your-password
```

Then say "log in with my gmail secret". Pilot types the stored values straight into the
page. **The values never go to the model**, only the name `gmail`, so your passwords
aren't sent to OpenAI. Both `config.toml` and `.env` are git-ignored.

Because the browser profile is kept, you normally only log in once per site. If a site
asks for a 2-step code, Pilot asks you for it.

## Why it's fast

- **Web pages are read as structure, not pixels.** Each page becomes a short numbered list
  of buttons, links and fields (`[12] button: Sign in`), and the model acts on the numbers.
  Most steps are a small text request with no screenshot, and a click lands exactly on the
  element instead of on guessed coordinates.
- **Every action returns the new page state**, so the model doesn't spend a step looking again.
- **History is pruned.** Only the latest screenshot is kept; old tool output is trimmed. The
  request stays small however long the task runs.
- **The model is told to act, not deliberate**, and to jump straight to URLs and keyboard
  shortcuts instead of clicking through menus.

For more speed, pick a faster model in `config.toml` (`name = ...`) and use a bigger one only
for screenshot steps via `vision_name`.

## Staying in control

It's your machine, so you choose how much it asks:

- By default it asks before **shell commands** and **file writes**. Answer `a` once to allow
  everything for the session, or start with `-y` / set `auto_approve = true`.
- **Stop:** the Stop button in the GUI, `Ctrl+C` in the terminal, or, while it's moving your
  mouse, slam the mouse into any screen corner (emergency stop for the whole task).
- Step limit per task: `max_steps` (default 40).

## Limits to know

- **Sites can still block it.** Some sites detect automated browsers and show CAPTCHAs or refuse
  logins; Pilot hands those to you via `ask_user` and doesn't try to defeat them. Using your
  installed Chrome (`channel = "chrome"`) and a persistent profile helps.
- **Linux on Wayland:** mouse/keyboard control of native apps needs an X11 session ("Ubuntu on
  Xorg" at login). Browser control works on both.
- Desktop control needs a screen; on a headless server only the browser (with `headless = true`)
  and shell work.
- Desktop clicking is screenshot-based and slower and less precise than browser actions.

## Project layout

```
pilot/
  agent.py         the loop: model picks a tool, Pilot runs it, repeat
  llm.py           OpenAI-compatible client + multi-key failover
  config.py        config.toml + .env + secrets + provider/server settings
  cli.py           command line: --serve, --gui, --doctor, -i
  runner.py        background worker: event log + task queue + remote prompts
  server.py        phone/web control panel (stdlib http, token-auth, PWA)
  gui.py           tkinter window
  perception.py    screenshots
  platform_utils.py OS detection, launching apps
  tools/
    __init__.py    tool list and schemas given to the model
    browser.py     Playwright browser control (snapshots + refs)
    computer.py    mouse and keyboard (pyautogui)
    shell.py       shell commands
    files.py       file helpers
scripts/           installers: desktop (Linux/Windows) and headless VM
tests/             run with: pytest
```

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The agent tests use a scripted fake model, so they need no API key. The browser tests drive
a real headless browser through a local login page; they skip if no browser is installed
(point `PILOT_BROWSER_PATH` at any Chromium binary to run them).
