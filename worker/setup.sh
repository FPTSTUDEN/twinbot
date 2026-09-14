# cd worker
npm install -g wrangler
wrangler login

# Create the queue
wrangler queues create discord-interactions

# Set the secret
wrangler secret put DISCORD_PUBLIC_KEY

# Deploy
wrangler deploy

echo "Queue created and deployed. Please add the Interactions Endpoint URL to your Discord application settings."