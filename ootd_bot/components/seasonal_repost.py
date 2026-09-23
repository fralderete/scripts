import asyncio
import random
from datetime import datetime, timezone
from io import BytesIO

import discord
from discord.ext import commands
from PIL import Image

from channels import GUILD_CONFIG


MAX_FILES_PER_MESSAGE = 10
TOP_POOL_SIZE = 200

SEASONS = {
    "spring": {3, 4, 5},
    "summer": {6, 7, 8},
    "fall": {9, 10, 11},
    "winter": {12, 1, 2},
}


class SeasonalRepostCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.guild_locks = {}

        print("[DEBUG] SeasonalRepostCog initialized")

    # ---------------------------------------------------------
    # IMAGE COMPRESSION
    # ---------------------------------------------------------

    async def compress_to_target(
        self,
        attachment,
        target_bytes=2 * 1024 * 1024
    ):
        try:
            raw = await attachment.read()

            print(
                f"[DEBUG] Compressing {attachment.filename} "
                f"({len(raw)} bytes)"
            )

            image = Image.open(BytesIO(raw))

            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")

            sizes = [1280, 1024, 800, 640]
            qualities = [85, 75, 65, 55, 45]

            for max_size in sizes:
                test_image = image.copy()

                test_image.thumbnail(
                    (max_size, max_size),
                    Image.Resampling.LANCZOS
                )

                for quality in qualities:
                    output = BytesIO()

                    test_image.save(
                        output,
                        format="JPEG",
                        quality=quality,
                        optimize=True
                    )

                    size = output.tell()

                    print(
                        f"[DEBUG] Try size={max_size}, "
                        f"quality={quality}, "
                        f"{size / 1024:.1f}KB"
                    )

                    if size <= target_bytes:
                        output.seek(0)

                        filename = (
                            attachment.filename.rsplit(".", 1)[0]
                            + ".jpg"
                        )

                        return discord.File(
                            output,
                            filename=filename
                        )

            print(
                f"[WARNING] Could not compress "
                f"{attachment.filename} under target"
            )

            return None

        except Exception as e:
            print(
                f"[ERROR] Failed to compress "
                f"{attachment.filename}: {e}"
            )

            return None

    # ---------------------------------------------------------
    # REACTION COUNT
    # ---------------------------------------------------------

    def get_reaction_count(self, msg):
        return sum(
            reaction.count
            for reaction in msg.reactions
        )

    # ---------------------------------------------------------
    # THREAD MESSAGE COUNT
    # ---------------------------------------------------------

    async def get_thread_message_count(self, msg):
        if msg.thread is None:
            return 0

        count = 0

        try:
            async for _ in msg.thread.history(
                limit=None
            ):
                count += 1

        except Exception as e:
            print(
                f"[WARNING] Could not read thread for "
                f"message {msg.id}: {e}"
            )

            return 0

        return count

    # ---------------------------------------------------------
    # COLLECT SEASON POSTS
    #
    # Only queries the relevant three calendar months for each
    # year instead of scanning the entire OOTD archive.
    #
    # Archive begins May 2023.
    # ---------------------------------------------------------

    async def collect_season_posts(
        self,
        ootd_channel,
        season,
        config
    ):
        season_months = SEASONS[season]

        excluded_ids = {
            int(user_id)
            for user_id in config.get(
                "EXCLUDED_REPOST_USER_IDS",
                []
            )
        }

        excluded_names = {
            str(name).lower()
            for name in config.get(
                "EXCLUDED_REPOST_USERNAMES",
                []
            )
        }

        now_utc = datetime.now(timezone.utc)
        current_year = now_utc.year

        posts = []

        print(
            f"[DEBUG] Searching all years for {season} "
            f"months {sorted(season_months)}"
        )

        for year in range(2023, current_year + 1):

            for month in sorted(season_months):

                # Archive starts May 2023.
                if year == 2023 and month < 5:
                    continue

                start_dt = datetime(
                    year,
                    month,
                    1,
                    tzinfo=timezone.utc
                )

                # Skip future months.
                if start_dt > now_utc:
                    continue

                if month == 12:
                    end_dt = datetime(
                        year + 1,
                        1,
                        1,
                        tzinfo=timezone.utc
                    )
                else:
                    end_dt = datetime(
                        year,
                        month + 1,
                        1,
                        tzinfo=timezone.utc
                    )

                # Current month only searches through now.
                if end_dt > now_utc:
                    end_dt = now_utc

                print(
                    f"[DEBUG] Searching "
                    f"{year}-{month:02d}"
                )

                month_count = 0

                async for msg in ootd_channel.history(
                    after=start_dt,
                    before=end_dt,
                    limit=None
                ):
                    if not msg.attachments:
                        continue

                    if msg.author.id in excluded_ids:
                        continue

                    author_names = {
                        getattr(
                            msg.author,
                            "name",
                            ""
                        ).lower(),

                        getattr(
                            msg.author,
                            "display_name",
                            ""
                        ).lower(),
                    }

                    if author_names & excluded_names:
                        continue

                    reaction_count = (
                        self.get_reaction_count(msg)
                    )

                    posts.append({
                        "message": msg,
                        "reactions": reaction_count,
                    })

                    month_count += 1

                print(
                    f"[DEBUG] Found {month_count} eligible "
                    f"posts for {year}-{month:02d}"
                )

        print(
            f"[DEBUG] Found {len(posts)} total eligible "
            f"{season} posts across all years"
        )

        return posts

    # ---------------------------------------------------------
    # SELECT TOP 50
    #
    # Primary ranking:
    #   total reaction count
    #
    # Thread counts are only fetched when a reaction tie
    # crosses the top-50 cutoff.
    #
    # Tie order:
    #   reactions
    #   thread message count
    #   random
    # ---------------------------------------------------------

    async def select_top_pool(
        self,
        posts,
        pool_size=TOP_POOL_SIZE
    ):
        if not posts:
            return []

        groups = {}

        for post in posts:
            reactions = post["reactions"]

            groups.setdefault(
                reactions,
                []
            ).append(post)

        reaction_scores = sorted(
            groups.keys(),
            reverse=True
        )

        selected = []

        for score in reaction_scores:
            group = groups[score]

            remaining = pool_size - len(selected)

            if remaining <= 0:
                break

            print(
                f"[DEBUG] Considering reaction score "
                f"{score}: {len(group)} post(s), "
                f"{remaining} pool spot(s) remaining"
            )

            # Whole group fits.
            # No reason to query thread activity.
            if len(group) <= remaining:
                selected.extend(group)
                continue

            # This tie crosses the cutoff.
            print(
                f"[DEBUG] {len(group)}-way tie at "
                f"{score} reactions affects the "
                f"top-{pool_size} cutoff"
            )

            tie_candidates = []

            for post in group:
                msg = post["message"]

                thread_count = (
                    await self.get_thread_message_count(msg)
                )

                print(
                    f"[DEBUG] Tie candidate {msg.id}: "
                    f"{score} reactions, "
                    f"{thread_count} thread messages"
                )

                tie_candidates.append({
                    "post": post,
                    "thread_count": thread_count,
                    "random_tiebreak": random.random(),
                })

            tie_candidates.sort(
                key=lambda item: (
                    item["thread_count"],
                    item["random_tiebreak"],
                ),
                reverse=True
            )

            selected.extend(
                item["post"]
                for item in tie_candidates[:remaining]
            )

            break

        return selected[:pool_size]

    # ---------------------------------------------------------
    # GENERATE SEASONAL REPOST
    # ---------------------------------------------------------

    async def generate_seasonal_repost(
        self,
        ctx,
        season,
        config
    ):
        season = season.lower().strip()

        if season not in SEASONS:
            await ctx.send(
                "Invalid season. Use spring, summer, "
                "fall, or winter."
            )
            return

        guild_id = ctx.guild.id

        if guild_id not in self.guild_locks:
            self.guild_locks[guild_id] = (
                asyncio.Lock()
            )

        lock = self.guild_locks[guild_id]

        if lock.locked():
            await ctx.send(
                "A seasonal repost is already being generated."
            )
            return

        async with lock:

            ootd_channel = self.bot.get_channel(
                config["OOTD_CHANNEL_ID"]
            )

            repost_channel = self.bot.get_channel(
                config["WEEKLYPOST_CHANNEL_ID"]
            )

            if ootd_channel is None:
                await ctx.send(
                    "Could not find the OOTD channel."
                )
                return

            if repost_channel is None:
                await ctx.send(
                    "Could not find the repost channel."
                )
                return

            await ctx.send(
                f"Collecting top {season.title()} "
                f"OOTDs across all years..."
            )

            # -------------------------------------------------
            # COLLECT ALL ELIGIBLE POSTS
            # -------------------------------------------------

            posts = await self.collect_season_posts(
                ootd_channel,
                season,
                config
            )

            if not posts:
                await ctx.send(
                    f"No eligible {season} OOTDs found."
                )
                return

            # -------------------------------------------------
            # BUILD TOP-50 POOL
            # -------------------------------------------------

            top_pool = await self.select_top_pool(
                posts,
                TOP_POOL_SIZE
            )

            if not top_pool:
                await ctx.send(
                    f"No eligible {season} OOTDs found."
                )
                return

            print(
                f"[DEBUG] {season.title()} top pool "
                f"contains {len(top_pool)} posts"
            )

            for index, post in enumerate(
                top_pool,
                start=1
            ):
                msg = post["message"]

                print(
                    f"[DEBUG] Pool #{index}: "
                    f"message={msg.id}, "
                    f"reactions={post['reactions']}, "
                    f"date={msg.created_at.date()}"
                )

            # -------------------------------------------------
            # RANDOMLY SELECT 10 FROM TOP 50
            # -------------------------------------------------

            sample_size = min(
                MAX_FILES_PER_MESSAGE,
                len(top_pool)
            )

            selected = random.sample(
                top_pool,
                sample_size
            )

            print(
                f"[DEBUG] Randomly selected "
                f"{sample_size} posts from top "
                f"{len(top_pool)}"
            )

            for index, post in enumerate(
                selected,
                start=1
            ):
                msg = post["message"]

                print(
                    f"[DEBUG] Selected #{index}: "
                    f"message={msg.id}, "
                    f"reactions={post['reactions']}, "
                    f"date={msg.created_at.date()}"
                )

            # -------------------------------------------------
            # PREPARE FIRST IMAGE FROM EACH SELECTED POST
            # -------------------------------------------------

            prepared = []

            for post in selected:
                msg = post["message"]

                if not msg.attachments:
                    continue

                attachment = msg.attachments[0]

                compressed = await self.compress_to_target(
                    attachment
                )

                if compressed is not None:
                    prepared.append(compressed)

            if not prepared:
                await ctx.send(
                    "Could not prepare any seasonal images."
                )
                return

            # -------------------------------------------------
            # SEND MAIN CHANNEL POST
            #
            # The images are attached DIRECTLY to this message.
            # This same message becomes the parent of the thread.
            # -------------------------------------------------

            try:
                print(
                    f"[DEBUG] Sending {len(prepared)} "
                    f"{season} images to channel"
                )

                repost_msg = await repost_channel.send(
                    content=(
                        f"{season.title()} OOTD Repost\n"
                        f"Fits from {season} OOTDs across the years."
                    ),
                    files=prepared
                )

                print(
                    f"[DEBUG] Seasonal channel post sent: "
                    f"{repost_msg.id}"
                )

            except Exception as e:
                print(
                    f"[ERROR] Failed to send seasonal "
                    f"post: {e}"
                )

                await ctx.send(
                    "Failed to create seasonal repost."
                )
                return

            # -------------------------------------------------
            # CREATE THREAD FROM THE IMAGE POST
            #
            # Nothing is automatically sent inside the thread.
            # -------------------------------------------------

            try:
                thread = await repost_msg.create_thread(
                    name=f"{season.title()} OOTD Repost"
                )

                print(
                    f"[DEBUG] Seasonal thread created: "
                    f"{thread.id}"
                )

            except Exception as e:
                print(
                    f"[ERROR] Seasonal post succeeded, "
                    f"but thread creation failed: {e}"
                )

                await ctx.send(
                    "Seasonal repost was posted, but "
                    "thread creation failed."
                )
                return

            # -------------------------------------------------
            # COMPLETE
            # -------------------------------------------------

            print(
                f"[DEBUG] Seasonal repost for "
                f"{season} complete"
            )

            await ctx.send(
                f"{season.title()} OOTDs posted."
            )

    # ---------------------------------------------------------
    # COMMAND
    # ---------------------------------------------------------

    @commands.command()
    async def season(
        self,
        ctx,
        season: str = None
    ):
        if ctx.guild is None:
            return

        if season is None:
            await ctx.send(
                "Usage: !season spring, "
                "!season summer, "
                "!season fall, or "
                "!season winter"
            )
            return

        season = season.lower().strip()

        if season not in SEASONS:
            await ctx.send(
                "Invalid season. Use spring, summer, "
                "fall, or winter."
            )
            return

        config = GUILD_CONFIG.get(
            ctx.guild.id
        )

        if config is None:
            await ctx.send(
                "This server is not configured."
            )
            return

        await self.generate_seasonal_repost(
            ctx,
            season,
            config
        )


async def setup(bot):
    print("[DEBUG] Loading SeasonalRepostCog...")

    await bot.add_cog(
        SeasonalRepostCog(bot)
    )
