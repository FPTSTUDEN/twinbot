import discord
import dotenv
from discord.ext import commands

dotenv.load_dotenv()

# 1. Initialize Bot with Gateway Intents
intents = discord.Intents.default()
intents.message_content = True  # Required for reading message content

bot = commands.Bot(command_prefix="!", intents=intents)

# 2. Event: Triggered when the bot logs in
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        # Sync slash commands globally across Discord
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

# 3. Slash Command Example: /ping
@bot.tree.command(name="ping", description="Replies with Pong!")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! 🏓")

# 4. Slash Command with an Argument: /greet
@bot.tree.command(name="greet", description="Greets a user")
async def greet(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.send_message(f"Hello, {user.mention}!")

# 5. Run the Bot
# TOKEN = "YOUR_BOT_TOKEN_HERE"  # Replace with your actual Bot Token
TOKEN = dotenv.get_key(dotenv.find_dotenv(), "BOT_TOKEN")  # Load
bot.run(TOKEN)