import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from discord.ext import commands, tasks

from channels import GUILD_CONFIG

CENTRAL_TZ = ZoneInfo("America/Chicago")


class WeeklyRepostCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.last_posted_week = None

        print("[DEBUG] WeeklyRepostCog initialized")

        self.weekly_repost.start()
        print("[DEBUG] weekly_repost loop started")

    def cog_unload(self):
        self.weekly_repost.cancel()

    def get_previous_week_range(self):
        now = datetime.now(CENTRAL_TZ)
        today = now.date()

        this_monday = today - timedelta(days=today.weekday())
        prev_monday = this_monday - timedelta(days=7)
        prev_sunday = prev_monday + timedelta(days=6)

        start_dt = datetime.combine(prev_monday, datetime.min.time(), tzinfo=CENTRAL_TZ).astimezone(timezone.utc)
        end_dt = datetime.combine(prev_sunday, datetime.max.time(), tzinfo=CENTRAL_TZ).astimezone(timezone.utc)

        return start_dt, end_dt, prev_monday, prev_sunday

    async def collect_posts(self, ootd_channel, start_dt, end_dt):
        print("[DEBUG] Collecting posts...")

        days = {}

        async for msg in ootd_channel.history(after=start_dt, before=end_dt, limit=None):
            if not msg.attachments:
                continue

            day = msg.created_at.date()
            days.setdefault(day, []).append(msg)

        print(f"[DEBUG] Collected {len(days)} days")
        return days

    async def generate_weekly_repost(self, guild_id, config):

        from PIL import Image
        from io import BytesIO
        import discord

        print(f"[DEBUG] Starting weekly repost for guild {guild_id}")

        async def compress_to_target(attachment, target_kb=2000):
            print(f"[DEBUG] Compressing {attachment.filename} ({attachment.size} bytes)")

            data = await attachment.read()

            with Image.open(BytesIO(data)) as img:
                img = img.convert("RGB")

                for size in [1280, 1024, 800, 640]:
                    img.thumbnail((size, size))

                    for quality in [85, 75, 65, 55, 45]:
                        buffer = BytesIO()
                        img.save(buffer, format="JPEG", quality=quality, optimize=True)
                        kb = buffer.tell() / 1024

                        print(f"[DEBUG] Try size={size}, quality={quality}, {kb:.1f}KB")

                        if kb <= target_kb:
                            buffer.seek(0)
                            return discord.File(buffer, filename="ootd.jpg")

            print("[WARN] Using smallest version")
            buffer.seek(0)
            return discord.File(buffer, filename="ootd.jpg")

        # Channels
        ootd_channel = self.bot.get_channel(config["OOTD_CHANNEL_ID"])
        weekly_channel = self.bot.get_channel(config["WEEKLYPOST_CHANNEL_ID"])
        announcement_channel = self.bot.get_channel(config["ANNOUNCEMENT_CHANNEL_ID"])

        print(f"[DEBUG] Channels loaded: ootd={ootd_channel}, weekly={weekly_channel}, announce={announcement_channel}")

        if not ootd_channel:
            print("[ERROR] OOTD channel not found")
            return

        if not weekly_channel:
            print("[ERROR] Weekly channel not found")
            return

        start_dt, end_dt, monday, sunday = self.get_previous_week_range()
        print(f"[DEBUG] Date range: {start_dt} → {end_dt}")

        days = await self.collect_posts(ootd_channel, start_dt, end_dt)

        if not days:
            print("[ERROR] No posts found")
            return

        files = []

        for i in range(7):
            day = monday + timedelta(days=i)

            if day not in days:
                print(f"[DEBUG] No posts for {day}")
                continue

            msg = random.choice(days[day])
            print(f"[DEBUG] Selected message {msg.id}")

            attachment = msg.attachments[0]

            try:
                f = await compress_to_target(attachment)
                files.append(f)
            except Exception as e:
                print("[ERROR] Compression failed:", e)

        print(f"[DEBUG] Prepared {len(files)} files")

        if not files:
            print("[ERROR] No files to send")
            return

        week_text = f"{monday.strftime('%b %d')} – {sunday.strftime('%b %d')}"

        try:
            print("[DEBUG] Sending header with images...")
            header_msg = await weekly_channel.send(
                content=f"Last week's OOTDs ({week_text})",
                files=files
            )
            print("[DEBUG] Header sent")
        except Exception as e:
            print("[ERROR] Failed to send header:", e)
            return

        try:
            print("[DEBUG] Creating thread...")
            thread = await header_msg.create_thread(
                name=f"Last week's OOTDs ({week_text})"
            )
            print(f"[DEBUG] Thread created: {thread.id}")
        except Exception as e:
            print("[ERROR] Thread creation failed:", e)

        if announcement_channel:
            try:
                print("[DEBUG] Sending announcement...")
                await announcement_channel.send(
                    f"Last week's OOTDs ({week_text}):\n{header_msg.jump_url}"
                )
                print("[DEBUG] Announcement sent")
            except Exception as e:
                print("[ERROR] Announcement failed:", e)

        print("[DEBUG] Weekly repost complete")

    # ✅ RELIABLE SCHEDULER
    @tasks.loop(minutes=1)
    async def weekly_repost(self):

        now = datetime.now(CENTRAL_TZ)
        print(f"[DEBUG] Tick: {now}")

        if now.weekday() != 0:
            return

        if now.hour != 12 or now.minute != 30:
            return

        start_dt, end_dt, prev_monday, prev_sunday = self.get_previous_week_range()

        if self.last_posted_week == prev_monday:
            print("[DEBUG] Already posted this week, skipping")
            return

        print("[DEBUG] Running scheduled weekly repost")

        for guild_id, config in GUILD_CONFIG.items():
            await self.generate_weekly_repost(guild_id, config)

        self.last_posted_week = prev_monday

    @weekly_repost.before_loop
    async def before_weekly_repost(self):
        print("[DEBUG] Waiting for bot ready...")
        await self.bot.wait_until_ready()
        print("[DEBUG] Bot ready, loop will begin")

    @commands.command()
    async def weeklyrepost(self, ctx):

        print("[DEBUG] Command !weeklyrepost triggered")

        if ctx.guild.id not in GUILD_CONFIG:
            print("[ERROR] Guild not configured")
            await ctx.send("Server not configured")
            return

        config = GUILD_CONFIG[ctx.guild.id]

        if ctx.channel.id != config["BOT_CHANNEL_ID"]:
            print("[ERROR] Wrong channel")
            await ctx.send(f"Use in <#{config['BOT_CHANNEL_ID']}>")
            return

        #await ctx.send("Generating weekly repost...")

        await self.generate_weekly_repost(ctx.guild.id, config)

        #await ctx.send("Done.")


async def setup(bot):
    print("[DEBUG] Loading WeeklyRepostCog...")
    await bot.add_cog(WeeklyRepostCog(bot))
