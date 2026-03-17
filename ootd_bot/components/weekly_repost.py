import random
from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo

from discord.ext import commands, tasks

from channels import GUILD_CONFIG

CENTRAL_TZ = ZoneInfo("America/Chicago")


class WeeklyRepostCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.last_posted_week = None
        self.weekly_repost.start()

    def cog_unload(self):
        self.weekly_repost.cancel()

    def get_previous_week_range(self):

        now = datetime.now(CENTRAL_TZ)
        today = now.date()

        this_monday = today - timedelta(days=today.weekday())
        prev_monday = this_monday - timedelta(days=7)
        prev_sunday = prev_monday + timedelta(days=6)

        start_dt = datetime.combine(
            prev_monday,
            datetime.min.time(),
            tzinfo=CENTRAL_TZ
        ).astimezone(timezone.utc)

        end_dt = datetime.combine(
            prev_sunday,
            datetime.max.time(),
            tzinfo=CENTRAL_TZ
        ).astimezone(timezone.utc)

        return start_dt, end_dt, prev_monday, prev_sunday

    def get_current_week_range(self):

        now = datetime.now(CENTRAL_TZ)
        today = now.date()

        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)

        start_dt = datetime.combine(
            monday,
            datetime.min.time(),
            tzinfo=CENTRAL_TZ
        ).astimezone(timezone.utc)

        end_dt = datetime.combine(
            sunday,
            datetime.max.time(),
            tzinfo=CENTRAL_TZ
        ).astimezone(timezone.utc)

        return start_dt, end_dt, monday, sunday

    async def collect_posts(self, ootd_channel, start_dt, end_dt):

        days = {}

        async for msg in ootd_channel.history(
            after=start_dt,
            before=end_dt,
            limit=None
        ):

            if not msg.attachments:
                continue

            day = msg.created_at.date()

            days.setdefault(day, []).append(msg)

        return days

    async def generate_weekly_repost(
        self,
        guild_id,
        config,
        start_dt=None,
        end_dt=None,
        monday=None,
        sunday=None,
        announce=True
    ):

        ootd_channel = self.bot.get_channel(config["OOTD_CHANNEL_ID"])
        announcement_channel = self.bot.get_channel(config["ANNOUNCEMENT_CHANNEL_ID"])

        if not ootd_channel:
            print(f"[Guild {guild_id}] OOTD channel missing.")
            return

        if start_dt is None:
            start_dt, end_dt, monday, sunday = self.get_previous_week_range()

        days = await self.collect_posts(ootd_channel, start_dt, end_dt)

        if not days:
            print(f"[Guild {guild_id}] No posts found.")
            return

        files = []

        for i in range(7):

            day = monday + timedelta(days=i)

            if day not in days:
                continue

            msg = random.choice(days[day])

            attachment = msg.attachments[0]

            try:
                files.append(await attachment.to_file())

            except Exception as e:
                print("Attachment failed:", e)

        if not files:
            print("No repostable attachments.")
            return

        week_text = f"{monday.strftime('%b %d')} – {sunday.strftime('%b %d')}"

        message = await ootd_channel.send(
            content=f"Last week's OOTDs ({week_text})",
            files=files
        )

        if announce and announcement_channel:

            await announcement_channel.send(
                f"Last week's OOTDs ({week_text}):\n{message.jump_url}"
            )

    @tasks.loop(time=time(hour=12, tzinfo=CENTRAL_TZ))
    async def weekly_repost(self):

        now = datetime.now(CENTRAL_TZ)

        if now.weekday() != 0:
            return

        start_dt, end_dt, prev_monday, prev_sunday = self.get_previous_week_range()

        if self.last_posted_week == prev_monday:
            return

        for guild_id, config in GUILD_CONFIG.items():

            await self.generate_weekly_repost(
                guild_id,
                config
            )

        self.last_posted_week = prev_monday

    @weekly_repost.before_loop
    async def before_weekly_repost(self):
        await self.bot.wait_until_ready()

    @commands.command()
    async def weeklyrepost(self, ctx):

        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return

        guild_id = ctx.guild.id

        if guild_id not in GUILD_CONFIG:
            await ctx.send("This server is not configured.")
            return

        config = GUILD_CONFIG[guild_id]

        if ctx.channel.id != config["BOT_CHANNEL_ID"]:
            await ctx.send(f"This command can only be used in <#{config['BOT_CHANNEL_ID']}>!")
            return

        await ctx.send("Generating weekly repost...")

        await self.generate_weekly_repost(
            guild_id,
            config
        )

        await ctx.send("Weekly repost complete.")

    @commands.command()
    async def testweek(self, ctx):

        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return

        guild_id = ctx.guild.id

        if guild_id not in GUILD_CONFIG:
            await ctx.send("This server is not configured.")
            return

        config = GUILD_CONFIG[guild_id]

        if ctx.channel.id != config["BOT_CHANNEL_ID"]:
            await ctx.send(f"This command can only be used in <#{config['BOT_CHANNEL_ID']}>!")
            return

        await ctx.send("Testing current week repost...")

        start_dt, end_dt, monday, sunday = self.get_current_week_range()

        await self.generate_weekly_repost(
            guild_id,
            config,
            start_dt,
            end_dt,
            monday,
            sunday,
            announce=False
        )

        await ctx.send("Test repost complete.")


async def setup(bot):
    await bot.add_cog(WeeklyRepostCog(bot))
