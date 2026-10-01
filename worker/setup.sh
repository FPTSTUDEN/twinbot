# cd worker
npm install
npm install -g wrangler
wrangler login

# Create the queue
wrangler queues create discord-interactions

# Set the secret
wrangler secret put DISCORD_PUBLIC_KEY
# Bot token used for resolving /paper users when Discord omits resolved data.
wrangler secret put DISCORD_TOKEN
wrangler secret put OPENAI_API_KEY
wrangler secret put OPENAI_BASE_URL
# Optional; defaults to gpt-4o-mini if set, otherwise omit this command.
# wrangler secret put OPENAI_MODEL
# Optional system prompt and reasoning effort (for example: low, medium, high).
# wrangler secret put OPENAI_SYSTEM_PROMPT
# wrangler secret put OPENAI_REASONING_EFFORT

# Register slash commands after changing register_commands.py.
# Run this from the repository root:
# python register_commands.py

# Deploy
wrangler deploy

echo "Queue created and deployed. Please add the Interactions Endpoint URL to your Discord application settings."