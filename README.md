# Personal Agent

A personal assistant you text on Telegram. It does everyday tasks for you and can **phone people for you
from your own number**.

```
You (Telegram) ──▶ this server ──▶ Brain: DeepSeek V4-Flash or Claude (tools, memory, web search)
                                     │
                                     └─ place_call ─▶ you tap ✅ ─▶ Twilio dials, showing YOUR number
                                                                     └─▶ Retell voice AI talks (Claude Haiku 4.5)
                                                                           ├─ ask_owner ─▶ texts you mid-call
                                                                           └─ transcript ─▶ Claude ─▶ summary to you
```

## Using your own number

| Direction | How it works |
|---|---|
| **Outgoing calls** | Your mobile number is added to Twilio as a **Verified Caller ID**. Twilio places the call with `from = your number`, so the other person sees **your number**, and if they call back, **your phone** rings. Your number stays with your carrier, and nothing is ported. |
| **Texting the agent** | Through a Telegram bot that only answers your Telegram account. |
| **Incoming calls (later)** | Set up *conditional call forwarding* with your carrier (busy / no answer → a Twilio number connected to Retell). The agent then answers only when you don't. This isn't built yet. |

Verify your number: Twilio Console → *Phone Numbers → Manage → Verified Caller IDs → Add*. Twilio calls
you and you type in the code. This needs an upgraded (paid) Twilio account.

> Some countries' carriers replace or block caller IDs that don't come from the local network. Test with
> one call to a friend before relying on it. You can check Twilio's per-country "caller ID" notes in its
> international calling guidelines.

## Setup

### 1. Accounts
- **Brain model:** a DeepSeek key (platform.deepseek.com, the same one Reservay uses) by default.
  To use Claude instead, set `BRAIN_PROVIDER=anthropic` and add an Anthropic key. Switching is one line
  in `.env` and starts a fresh conversation.
- **Web search for DeepSeek (optional):** a Brave Search API key. Claude has web search built in.
- **Telegram:** create a bot with [@BotFather](https://t.me/BotFather) to get a token. Get your numeric
  user id from [@userinfobot](https://t.me/userinfobot).
- **Twilio:** upgraded account, your number verified as a caller ID, and voice calling to your target
  countries enabled under *Voice → Settings → Geo permissions*.
- **Retell:** create the voice agent by following [docs/retell-agent.md](docs/retell-agent.md).

### 2. Run locally
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill it in
ngrok http 8000        # put the https URL in PUBLIC_BASE_URL (and in Retell's webhook/function URLs)
set -a; . ./.env; set +a
uvicorn agent.main:app --port 8000
```
At startup the server registers its Telegram webhook. Send your bot `/start`.

### 3. Deploy (always on)
Any host that runs a Docker container with a persistent volume at `/data` works: Fly.io, Railway, or a
small VPS. Set the same env vars there and point `PUBLIC_BASE_URL` at the deployed URL.

```bash
fly launch --no-deploy && fly volumes create data --size 1
# add to fly.toml:  [mounts] source="data" destination="/data"
fly secrets set $(grep -v '^#' .env | xargs) && fly deploy
```

## Group chats (e.g. a trip group in another language)
Add the bot to a Telegram group you're in. It joins **openly as a bot**:
- It answers when someone **@mentions it or replies to it**, in their language, and can search the web.
  Group members can only chat with it. Calls, memories and contacts stay yours.
- Every `GROUP_SUMMARY_SECONDS` (default 60) you get a private **English summary** of new messages.
- `/ru <text>` in your private chat gives you Russian to paste into the group yourself.
- Ask it privately: "What did the London group decide about the hotel?"

Before adding it, turn off privacy mode so it can read all group messages: in @BotFather send
`/setprivacy`, pick your bot, choose **Disable**. If it's already in the group, remove it and add it again.
The bot ignores groups you're not a member of.

## Example commands
- "Call Luigi's in Kadıköy and book a table for 4 this Friday around 20:00, anything 19:00–21:00 works."
- "Call my dentist and move Thursday's appointment to next week, mornings only."
- "Remember my car plate is 34 ABC 123."
- `/reset` clears the conversation and keeps memories and contacts. `/memories` lists what it knows.

## Safety built in
- Only your Telegram account can talk to the bot. Webhooks are checked with Telegram's secret token and
  Twilio's and Retell's signatures.
- **Every call needs your tap on ✅ before it's dialed.**
- The voice agent says it's an AI assistant calling for you. It only shares what's in the brief and
  asks you when it's unsure.
- The rules on AI calls and recording consent differ by country (Türkiye, Germany/EU, US…). Use this
  for your own one-to-one errands, never for bulk or cold calls.

## Roadmap
- [ ] Gmail and Google Calendar tools
- [ ] Scheduled jobs (morning brief, reminders)
- [ ] Voice-note commands (Telegram voice → transcription)
- [ ] Incoming calls via conditional forwarding
