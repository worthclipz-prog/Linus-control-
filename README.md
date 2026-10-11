# Pilot

An AI agent that operates your computer. Tell it what you want in plain English and
it drives your browser, mouse, keyboard and terminal to do it. Runs on **Linux and
Windows**. The brain is **GPT** through the OpenAI API, or any OpenAI-compatible
model (including free local ones).

```
pilot "log into gmail with my gmail secret and read me the subjects of my 5 newest emails"
pilot "open the calculator and work out 1234 * 5678"
pilot --gui
```

## Before you start: ChatGPT Plus is not an API key

A **ChatGPT Plus** subscription only covers the chat website and apps. Software can't
use it. Pilot needs one of these instead:

| Option | Cost | Setup |
|---|---|---|
| **OpenAI API key** (recommended) | Pay per use, billed separately from Plus | Create a key at <https://platform.openai.com/api-keys> and put it in `.env` |
| **Local model** (Ollama, LM Studio) | Free, runs on your GPU | Set `base_url` and `name` in `config.toml` (see below). Needs a vision + tool-calling model; weaker than GPT |
| **Any OpenAI-compatible service** (OpenRouter, Azure OpenAI, ...) | Varies | Set `base_url`, `name`, and the key |

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

1. Put your key in `.env`: `OPENAI_API_KEY=sk-...`
2. Check everything: `pilot --doctor`

Manual install: `pip install -e .` then `python -m playwright install chromium firefox`.

## Use it

| Command | What it does |
|---|---|
| `pilot "task"` | Do one task and exit |
| `pilot -i` | Chat mode: give tasks one after another; it remembers context |
| `pilot --gui` | Small always-on-top window with a Run and a **Stop** button (on Windows, double-click `scripts\pilot.bat`) |
| `pilot --doctor` | Check that everything is installed |
| `pilot -y "task"` | Don't ask before shell commands or file writes |

More options: `pilot --help`. More example tasks: [`examples/tasks.md`](examples/tasks.md).

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
  llm.py           OpenAI-compatible client
  config.py        config.toml + .env + secrets
  cli.py           command line, --doctor
  gui.py           tkinter window
  perception.py    screenshots
  platform_utils.py OS detection, launching apps
  tools/
    __init__.py    tool list and schemas given to the model
    browser.py     Playwright browser control (snapshots + refs)
    computer.py    mouse and keyboard (pyautogui)
    shell.py       shell commands
    files.py       file helpers
scripts/           installers for Linux and Windows
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
