import discord
from discord.ext import commands
from datetime import datetime, timezone
import os
from channels import FORUM_CHANNEL_ID, OOTD_CHANNEL_ID
import random

TOKEN = os.getenv("OOTD_BOT_TOKEN") 
if not TOKEN:
    raise ValueError("OOTD_BOT_TOKEN not set in environment")

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

# Command: !archive month year
@bot.command()
async def archive(ctx, month, year, reaction: str = None):
    usage = (
        "Usage:\n"
        "`!archive <month> <year> [reaction]`\n"
        "- `<month>`: 1-12 or month name (e.g., 2 or February)\n"
        "- `<year>`: full year (e.g., 2026)\n"
        "Example: `!archive 2 2026` or `!archive February 2026`"
    )

    # Validate month
    month_map = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12
    }

    # If month is int, use it directly; if str, check map
    if isinstance(month, int):
        month_num = month
    else:
        try:
            if month.isdigit():
                month_num = int(month)
            else:
                month_num = month_map[month.lower()]
        except KeyError:
            await ctx.send(f"❌ Invalid month name.\n{usage}")
            return
        except ValueError:
            await ctx.send(f"❌ Month number must be between 1 and 12.\n{usage}")
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

    #print(type(ctx.channel.id), ctx.channel.id)
    #print(type(OOTD_CHANNEL_ID), OOTD_CHANNEL_ID)

    # Now proceed with your archival logic using month_num and year_num
    if ctx.channel.id != OOTD_CHANNEL_ID:
        await ctx.send("This command can only be used in #ootd!")
        return

    ootd_channel = ctx.channel
    forum_channel = bot.get_channel(FORUM_CHANNEL_ID)

    if forum_channel is None:
        await ctx.send("Forum channel not found!")
        return
    
    # Success — arguments are valid
    await ctx.send(f"✅ Archiving daily random OOTD posts from {month_num}/{year_num}...")
    #(reaction: {reaction})...")
    
    all_messages = []
    async for msg in ootd_channel.history(limit=None):
        all_messages.append(msg)

    filtered = [
        msg for msg in all_messages
        if msg.attachments and msg.created_at.year == year_num and msg.created_at.month == month_num
    ]

    if not filtered:
        await ctx.send("No messages found for that month/year.")
        return

    # Group by day
    days = {}
    for msg in filtered:
        day = msg.created_at.day
        if day not in days:
            days[day] = []
        days[day].append(msg)

    random_per_day = []
    for day, msgs in days.items():
        msgs_with_attachments = [m for m in msgs if m.attachments]
        if not msgs_with_attachments:
            continue
        random_msg = random.choice(msgs_with_attachments)
        random_per_day.append(random_msg)

    base_name = datetime(year_num, month_num, 1).strftime('%B %Y')
    MAX_VOL = 10

    existing_titles = []

    # Active threads
    for thread in forum_channel.threads:
        existing_titles.append(thread.name)

    # Archived threads
    async for thread in forum_channel.archived_threads(limit=None):
        existing_titles.append(thread.name)

    # Find threads matching this month/year
    matching = [name for name in existing_titles if name.startswith(base_name)]

    volumes = []

    for name in matching:
        if name == base_name:
            volumes.append(1)
        elif "Vol." in name:
            try:
                vol_num = int(name.split("Vol.")[-1].strip())
                volumes.append(vol_num)
            except:
                pass

    if not volumes:
        next_vol = 1
    else:
        highest_vol = max(volumes)

        if highest_vol >= MAX_VOL:
            await ctx.send(
                f"❌ {base_name} already has Vol. {MAX_VOL}. Maximum reached."
            )
            return

        next_vol = highest_vol + 1

    if next_vol == 1:
        final_name = base_name
    else:
        final_name = f"{base_name} Vol. {next_vol}"

    thread = await forum_channel.create_thread(
        name=final_name,
        content=f"Random OOTD images for {final_name}"
    )

    thread_obj = thread.thread

    await ctx.send(f"Forum post created: <#{thread_obj.id}>")

    for msg in random_per_day:
        author_name = getattr(msg.author, "display_name", "Unknown User")
        print(f"Message {msg.id} by {author_name}: {len(msg.attachments)} attachments")

        if not msg.attachments:
            print(f"No attachments found for message {msg.id}")
            continue

        for att in msg.attachments:
            try:
                caption = f"{msg.created_at.strftime('%b %d')} by {author_name}"
                await thread_obj.send(content=caption, file=await att.to_file())
                print(f"Sent {att.filename} from message {msg.id}")
            except Exception as e:
                print(f"Failed to send {att.filename}: {e}")

    await ctx.send(f"Posted random images for {len(random_per_day)} days!")

bot.run(TOKEN)
