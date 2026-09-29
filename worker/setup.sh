# cd worker
npm install -g wrangler
wrangler login

# Create the queue
wrangler queues create discord-interactions

# Set the secret
wrangler secret put DISCORD_PUBLIC_KEY
wrangler secret put OPENAI_API_KEY
wrangler secret put OPENAI_BASE_URL
# Optional; defaults to gpt-4o-mini if set, otherwise omit this command.
# wrangler secret put OPENAI_MODEL

# Deploy
wrangler deploy

echo "Queue created and deployed. Please add the Interactions Endpoint URL to your Discord application settings."