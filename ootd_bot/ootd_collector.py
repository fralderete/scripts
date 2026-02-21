import discord
from discord.ext import commands
from datetime import datetime, timezone
import os
from channels import (
    FORUM_CHANNEL_ID, 
    OOTD_CHANNEL_ID, 
    BOT_CHANNEL_ID, 
    ANNOUNCEMENT_CHANNEL_ID
)
import random

TOKEN = os.getenv("OOTD_BOT_TOKEN") 
if not TOKEN:
    raise ValueError("OOTD_BOT_TOKEN not set in environment")

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 🔒 Concurrency lock
archive_lock = False

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.command()
async def archive(ctx, month, year, reaction: str = None):
    global archive_lock

    usage = (
        "Usage:\n"
        "`!archive <month> <year> [reaction]`\n"
        "- `<month>`: 1-12 or month name (e.g., 2 or February)\n"
        "- `<year>`: full year (e.g., 2026)\n"
        "Example: `!archive 2 2026` or `!archive February 2026`"
    )

    # 🔒 Prevent concurrent execution
    if archive_lock:
        await ctx.send("⚠️ Archive already running. Please wait.")
        return

    archive_lock = True

    try:
        # Restrict to bot channel
        if ctx.channel.id != BOT_CHANNEL_ID:
            await ctx.send("This command can only be used in #bot-command-channel!")
            return

        # Validate month
        month_map = {
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12
        }

        if month.isdigit():
            month_num = int(month)
        else:
            try:
                month_num = month_map[month.lower()]
            except KeyError:
                await ctx.send(f"❌ Invalid month.\n{usage}")
                return

        if not (1 <= month_num <= 12):
            await ctx.send(f"❌ Month must be between 1 and 12.\n{usage}")
            return

        # Validate year
        try:
            year_num = int(year)
            if year_num < 2023 or year_num > datetime.utcnow().year:
                raise ValueError
        except:
            await ctx.send(f"❌ Invalid year.\n{usage}")
            return

        ootd_channel = bot.get_channel(OOTD_CHANNEL_ID)
        forum_channel = bot.get_channel(FORUM_CHANNEL_ID)
        announcement_channel = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)

        if not ootd_channel or not forum_channel or not announcement_channel:
            await ctx.send("❌ One or more channels not found.")
            return

        await ctx.send(f"✅ Archiving daily random OOTD posts from {month_num}/{year_num}...")

        # ✅ Efficient date filtering (no full history scan)
        start_date = datetime(year_num, month_num, 1, tzinfo=timezone.utc)

        if month_num == 12:
            end_date = datetime(year_num + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end_date = datetime(year_num, month_num + 1, 1, tzinfo=timezone.utc)

        filtered = []

        async for msg in ootd_channel.history(after=start_date, before=end_date, limit=None):
            if msg.attachments:
                filtered.append(msg)

        if not filtered:
            await ctx.send("No messages found for that month/year.")
            return

        # Group by day
        days = {}
        for msg in filtered:
            days.setdefault(msg.created_at.day, []).append(msg)

        random_per_day = [
            random.choice(msgs)
            for msgs in days.values()
            if msgs
        ]

        base_name = datetime(year_num, month_num, 1).strftime('%B %Y')
        MAX_VOL = 3

        existing_titles = []

        for thread in forum_channel.threads:
            existing_titles.append(thread.name)

        async for thread in forum_channel.archived_threads(limit=None):
            existing_titles.append(thread.name)

        matching = [name for name in existing_titles if name.startswith(base_name)]

        volumes = set()
        for name in matching:
            if "Vol." in name:
                try:
                    vol_num = int(name.split("Vol.")[-1].strip())
                    volumes.add(vol_num)
                except:
                    pass

        # Find lowest missing volume
        next_vol = None
        for i in range(1, MAX_VOL + 1):
            if i not in volumes:
                next_vol = i
                break

        if next_vol is None:
            await ctx.send(
                f"❌ {base_name} already has Vol. 1–{MAX_VOL}. "
                f"Delete an old version to generate again."
            )
            return

        final_name = f"{base_name} Vol. {next_vol}"

        thread = await forum_channel.create_thread(
            name=final_name,
            content=f"Random OOTD images for {final_name}"
        )

        thread_obj = thread.thread

        embed = discord.Embed(
            title=f"{base_name} OOTD Archive",
            description="One random OOTD from each day has been archived.",
            color=discord.Color.blurple()
        )
        
        embed.add_field(
            name="Archive Forum Thread",
            value=f"<#{thread_obj.id}>",
            inline=False
        )
        
        embed.add_field(
            name="Days Archived",
            value=str(len(random_per_day)),
            inline=True
        )
        
        embed.set_footer(text=f"Generated by {ctx.author.display_name}")
        
        await announcement_channel.send(embed=embed)

        for msg in random_per_day:
            author_name = getattr(msg.author, "display_name", "Unknown User")

            for att in msg.attachments:
                try:
                    caption = f"{msg.created_at.strftime('%b %d')} by {author_name}"
                    await thread_obj.send(content=caption, file=await att.to_file())
                except Exception as e:
                    print(f"Failed to send {att.filename}: {e}")

    finally:
        # 🔓 Always release lock
        archive_lock = False

bot.run(TOKEN)