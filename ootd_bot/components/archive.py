import random
from datetime import datetime, timezone

import discord
from discord.ext import commands

from channels import GUILD_CONFIG


class ArchiveCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.archive_locks = {}

    @commands.command()
    async def archive(self, ctx, month, year, reaction: str = None):
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return

        guild_id = ctx.guild.id

        if guild_id not in GUILD_CONFIG:
            await ctx.send("This server is not configured.")
            return

        if self.archive_locks.get(guild_id, False):
            await ctx.send(
                "⚠️ Archive already running for this server. ⚠️ Please wait for the last one to finish before calling this command again."
            )
            return

        self.archive_locks[guild_id] = True
        config = GUILD_CONFIG[guild_id]

        FORUM_CHANNEL_ID = config["FORUM_CHANNEL_ID"]
        OOTD_CHANNEL_ID = config["OOTD_CHANNEL_ID"]
        BOT_CHANNEL_ID = config["BOT_CHANNEL_ID"]
        ANNOUNCEMENT_CHANNEL_ID = config["ANNOUNCEMENT_CHANNEL_ID"]

        usage = (
            "Usage:\n"
            "`!archive <month> <year> [reaction]`\n"
            "- `<month>`: 1-12 or month name (e.g., 2 or February)\n"
            "- `<year>`: full year (e.g., 2026)\n"
            "Example: `!archive 2 2026` or `!archive February 2026`"
        )

        try:
            if ctx.channel.id != BOT_CHANNEL_ID:
                await ctx.send(f"This command can only be used in <#{BOT_CHANNEL_ID}>!")
                return

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

            try:
                year_num = int(year)
                if year_num < 2023 or year_num > datetime.utcnow().year:
                    raise ValueError
            except Exception:
                await ctx.send(f"❌ Invalid year.\n{usage}")
                return

            if year_num == 2023 and month_num < 5:
                await ctx.send("❌ Archives can only be generated from May 2023 or later.")
                return

            ootd_channel = self.bot.get_channel(OOTD_CHANNEL_ID)
            forum_channel = self.bot.get_channel(FORUM_CHANNEL_ID)
            announcement_channel = self.bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)

            if not ootd_channel or not forum_channel or not announcement_channel:
                await ctx.send("One or more channels not found.")
                return

            await ctx.send(f"Archiving daily random OOTD posts from {month_num}/{year_num}...")

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

            days = {}
            for msg in filtered:
                days.setdefault(msg.created_at.day, []).append(msg)

            random_per_day = [random.choice(msgs) for msgs in days.values() if msgs]

            base_name = datetime(year_num, month_num, 1).strftime("%B %Y")
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
                    except Exception:
                        pass

            next_vol = None
            for i in range(1, MAX_VOL + 1):
                if i not in volumes:
                    next_vol = i
                    break

            if next_vol is None:
                await ctx.send(
                    f"{base_name} already has Vol. 1–{MAX_VOL}. "
                    f"Delete an old version of the given month/year to generate again."
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
                caption = f"{msg.created_at.strftime('%b %d')} by {author_name}"

                try:
                    files = [await att.to_file() for att in msg.attachments]
                    await thread_obj.send(content=caption, files=files)
                except Exception as e:
                    print(f"Failed to send grouped attachments: {e}")

        finally:
            self.archive_locks[guild_id] = False


async def setup(bot):
    await bot.add_cog(ArchiveCog(bot))
