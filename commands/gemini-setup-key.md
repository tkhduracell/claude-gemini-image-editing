---
description: Store a Gemini API key where the gemini-image MCP server will find it
---

Set up the Gemini API key for the `gemini-image` MCP server.

The key goes in `~/.config/gemini/api.key`. A file is used rather than an
environment variable because GUI-launched apps (the Claude desktop app
included) do not read `~/.zshrc`, so an exported variable never reaches the
container.

Do this:

1. **Check whether a key is already there.** Run:
   `ls -l ~/.config/gemini/api.key 2>/dev/null && echo PRESENT || echo ABSENT`
   If present, say so and ask whether to replace it before going further.

2. **Get the key from the user.** If they passed one as an argument to this
   command, use it. Otherwise ask them to paste it, and tell them it comes
   from https://aistudio.google.com/apikey

   Never echo the key back, never write it into your reply, and never put it
   in a command where it would be visible in the transcript more than once.

3. **Write it**, creating the directory and locking the permissions down:

   ```bash
   mkdir -p ~/.config/gemini && chmod 700 ~/.config/gemini
   printf '%s' 'THE_KEY' > ~/.config/gemini/api.key
   chmod 600 ~/.config/gemini/api.key
   ```

4. **Verify** by calling the `get_help` tool of the `gemini-image` MCP server
   and reporting the `Current Auth` line. It should read
   ``API key: `Set` ``. If the server is not connected yet, tell the user to
   restart Claude Code — the container reads the key at startup.

5. If `get_help` still reports `Not Set`, check in this order: the file is
   non-empty; Docker is running; and the plugin is installed
   (`claude plugin list`).
