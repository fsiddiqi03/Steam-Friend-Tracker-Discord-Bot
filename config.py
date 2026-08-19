import os

from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

STEAM_API_KEY = os.getenv("STEAM_API_KEY")
STEAM_ID = os.getenv("STEAM_ID")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))
FRIENDS_FILE = os.getenv("FRIENDS_FILE", "friends.json")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
