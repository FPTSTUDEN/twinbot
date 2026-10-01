# Deployment and Troubleshooting

## Worker setup

From the Worker directory:

```powershell
Set-Location "C:\Users\LOQ\Documents\webdev\thingys\twinbot\worker"
npm install
npx wrangler login
```

Create the queue if it does not exist:

```powershell
npx wrangler queues create discord-interactions
```

Set secrets. Values are entered interactively and must not be committed:

```powershell
npx wrangler secret put DISCORD_PUBLIC_KEY
npx wrangler secret put DISCORD_TOKEN
npx wrangler secret put OPENAI_API_KEY
npx wrangler secret put OPENAI_BASE_URL
```

Optional secrets are `OPENAI_MODEL`, `OPENAI_SYSTEM_PROMPT`, and `OPENAI_REASONING_EFFORT`.

`DISCORD_TOKEN` is the bot token. The Worker uses it server-side to look up users and fetch avatar files when interaction-resolved data is incomplete. It is not the interaction token and must not be sent in a response.

## Deploy and register commands

Dry-run first:

```powershell
npx wrangler deploy --dry-run
```

Deploy:

```powershell
npx wrangler deploy
```

If command options or descriptions changed, synchronize commands from the repository root:

```powershell
Set-Location "C:\Users\LOQ\Documents\webdev\thingys\twinbot"
python register_commands.py
```

The Discord application's Interactions Endpoint URL must point to the deployed Worker URL.

## `/paper` troubleshooting

### The image says `User xxxx`

Check that the deployed Worker has the secret:

```powershell
npx wrangler secret list
```

Inspect Worker logs for:

- `paper Discord user lookup failed`: `/users/{id}` failed, often because the token is missing or invalid.
- `paper Discord member lookup failed`: guild lookup failed; user lookup may still work.
- `paper users resolved`: selected IDs and whether resolved data was present. Token values are never logged.

The bot must be in the guild for the guild-member endpoint to return a member. The global user endpoint is sufficient for username and global avatar data.

### The default avatar appears

The user object did not include an avatar hash, or the CDN fetch failed. Check for `paper avatar fetch failed`. A default avatar is expected for users without a custom avatar, but not for users whose REST response contains an avatar hash.

### Text or avatars are missing from the PNG

Do not switch back to SVG uploads. Check that:

- `worker/fonts.ts` is bundled and the resvg font option is present.
- Avatar images are resolved before `render()`.
- Templates use `href="{{avatar1}}"` and `href="{{avatar2}}"`.
- `npx wrangler deploy --dry-run` succeeds after template changes.

### Follow-up upload fails

The interaction token is used for the webhook follow-up and is separate from `DISCORD_TOKEN`. Check that the initial deferred response was returned within Discord's deadline and that the follow-up URL uses the interaction's `application_id` and `token`.

## Safe validation checklist

```powershell
Set-Location "C:\Users\LOQ\Documents\webdev\thingys\twinbot\worker"
npm install
npx wrangler deploy --dry-run
Set-Location "C:\Users\LOQ\Documents\webdev\thingys\twinbot"
git diff --check
```

After deploying, test every `/paper` template and check Worker logs for user lookup, avatar resolution, rasterization, and multipart-upload failures.