# DeepSeek Web Login (Manual Profile) for OpenClaw Web Gateway

Some sites (SSO / anti-bot) can be flaky when driven by automation. The most reliable approach is to log in once using **system Chrome** with the **same profile directory** that the web gateway uses. Then the gateway reuses the cookies/session.

## Paths

Default profile root:

- `OPENCLAW_WEB_GATEWAY_PROFILES_DIR` (default: `~/.openclaw/runtime/translation/web-profiles`)

Provider profile dirs:

- DeepSeek: `~/.openclaw/runtime/translation/web-profiles/deepseek_web`
- ChatGPT: `~/.openclaw/runtime/translation/web-profiles/chatgpt_web`

## Steps (macOS)

1. Stop the gateway (important: Chrome cannot open the same profile dir while the gateway is running):

```bash
./scripts/start.sh --gateway-stop
```

2. Open system Chrome with the provider profile dir:

```bash
open -na "Google Chrome" --args --user-data-dir="$HOME/.openclaw/runtime/translation/web-profiles/deepseek_web"
```

3. In that Chrome window, open:

- `https://chat.deepseek.com/`

4. Log in with your DeepSeek account (complete any verification if prompted).

5. Quit Chrome completely.

6. Start the gateway again:

```bash
./scripts/start.sh --gateway-start
```

7. Verify from Runtime UI (Provider -> Login), or via CLI:

```bash
./scripts/start.sh --gateway-login
```

## Notes

- If you want to keep your normal Chrome profile untouched, this method is safe: it uses a dedicated `--user-data-dir`.
- Repeat the same steps for ChatGPT if needed by changing the `--user-data-dir` to `chatgpt_web`.
- If you see `Failed to create a ProcessSingleton... SingletonLock: File exists`, it means the profile dir is already in use. Fully quit Chrome and stop the gateway, then retry.
