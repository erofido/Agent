# Setting up the Retell voice agent

The voice agent is the one that talks on the phone. You create it once in the
[Retell dashboard](https://dashboard.retellai.com), and this server fills in the details of each call.

## 1. Create the agent

- **Response engine:** Retell LLM (single prompt). Pick a Claude model in the model dropdown if you want
  Claude on the call too.
- **Voice:** choose a natural voice that speaks your call languages (Turkish, German, English…). A
  multilingual voice lets one agent handle all of them.
- **Language:** "Multilingual", or create one agent per language and switch `RETELL_AGENT_ID`.
- **Begin message:** leave empty, or set "Wait for user to speak first". On outbound calls the other side
  usually says "Hello?" first.

## 2. Prompt

Paste this in as the agent's prompt. Keep the `{{...}}` placeholders: the server fills them in for every call.

```
You are a polite, efficient personal assistant making a phone call on behalf of {{owner_name}}.
You are calling: {{contact_name}}. Speak {{language}}.

Goal of this call: {{goal}}

Your brief (follow it exactly; it is everything you know):
{{call_brief}}

Rules:
- Early in the call, say you are an AI assistant calling on behalf of {{owner_name}}.
  If asked, you are calling from {{owner_name}}'s own number, {{owner_phone}}.
- Stay on the goal. Be brief and natural; don't read the brief aloud.
- Only share information that appears in the brief and is needed.
- Never agree to payments, contracts, or anything outside the brief's limits.
- If you need a decision the brief doesn't cover, say "one moment please" and call the
  `ask_owner` function with a short, specific question. Then continue based on the answer.
  If there is no answer, don't commit; say {{owner_name}} will get back to them.
- If you reach voicemail, leave a short message with the goal and ask them to call back {{owner_phone}}.
- Before hanging up, confirm the key details (date, time, name, reference number).
- End the call politely once the goal is done or clearly impossible.
```

## 3. Custom function `ask_owner`

Add a **Custom Function**:

- **Name:** `ask_owner`
- **Description:** "Ask the owner a question by text and wait for their answer. Use it when you need a decision that isn't in your brief."
- **URL:** `https://YOUR_PUBLIC_BASE_URL/retell/ask-owner`
- **Timeout:** at least `ASK_OWNER_TIMEOUT + 15` seconds (75s with the default of 60)
- **Speak during execution:** on, e.g. "One moment, let me check that."
- **Parameters:**

```json
{
  "type": "object",
  "properties": {
    "question": { "type": "string", "description": "Short, specific question for the owner" }
  },
  "required": ["question"]
}
```

Also add the built-in **End Call** function, so the agent can hang up.

## 4. Webhook

Under the agent's (or the account's) **Webhook URL**, set `https://YOUR_PUBLIC_BASE_URL/retell/webhook`.
The server uses `call_started` and `call_analyzed`. `call_analyzed` carries the transcript and summary.

## 5. Post-call analysis (optional)

The default call summary is enough to start with. You can also add analysis fields such as
`appointment_datetime` or `outcome`. They arrive in `call_analysis`, and you can pass them on to Claude
in `agent/calls.py`.
