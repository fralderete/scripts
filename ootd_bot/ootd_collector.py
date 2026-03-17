import os
import asyncio

import discord
from discord.ext import commands

TOKEN = os.getenv("OOTD_BOT_TOKEN")
if not TOKEN:
    raise ValueError("OOTD_BOT_TOKEN not set in environment")

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True


class OOTDBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.load_extension("components.archive")
        await self.load_extension("components.weekly_repost")


bot = OOTDBot()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


async def main():
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
