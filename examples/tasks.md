# Example tasks

Run any of these with `pilot "<task>"`, or type them into `pilot --gui` / `pilot -i`.

## Web
- `open youtube and play the first result for "lofi hip hop"`
- `log into gmail with my gmail secret and tell me the subjects of my 5 newest emails`
- `go to github.com, log in with my github secret, and star the repo openai/openai-python`
- `search amazon.in for a 27 inch 4k monitor under 25000 rupees and list the top 3 with prices`
- `open google flights and find the cheapest flight from Delhi to Mumbai next Friday`

## Desktop
- `open the calculator and compute 1234 * 5678`
- `take a screenshot and tell me what windows are open`
- `open a text editor, write a short shopping list, and save it to ~/shopping.txt`

## System
- `how much free disk space do I have?`
- `find all PDFs in my Downloads folder bigger than 10 MB`
- `install vlc` (asks before running the command unless you pass `-y`)

## Logging in

Store the login once, by name, in `config.toml`:

```toml
[secrets.gmail]
username = "you@gmail.com"
password = "..."
```

or in `.env`:

```
PILOT_SECRET_GMAIL_USER=you@gmail.com
PILOT_SECRET_GMAIL_PASS=...
```

Then just say "log in with my gmail secret". The model types it into the page
without ever seeing the value. With `user_data_dir` set (on by default in the
example config) the browser remembers the session, so you usually log in once.

If the site asks for a 2-step code or a CAPTCHA, Pilot pauses and asks you.
