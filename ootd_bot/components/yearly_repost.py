import random
from datetime import datetime, timezone
from io import BytesIO
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands
from PIL import Image

from channels import GUILD_CONFIG


CENTRAL_TZ = ZoneInfo("America/Chicago")
MAX_FILES_PER_MESSAGE = 10


class YearlyRepostCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        print("[DEBUG] YearlyRepostCog initialized")

    # ---------------------------------------------------------
    # IMAGE COMPRESSION
    # ---------------------------------------------------------

    async def compress_to_target(self, attachment, target_kb=2000):

        print(
            f"[DEBUG] Compressing {attachment.filename} "
            f"({attachment.size} bytes)"
        )

        data = await attachment.read()

        with Image.open(BytesIO(data)) as img:
            img = img.convert("RGB")

            buffer = None

            for size in [1280, 1024, 800, 640]:
                img.thumbnail((size, size))

                for quality in [85, 75, 65, 55, 45]:
                    buffer = BytesIO()

                    img.save(
                        buffer,
                        format="JPEG",
                        quality=quality,
                        optimize=True
                    )

                    kb = buffer.tell() / 1024

                    print(
                        f"[DEBUG] Try size={size}, "
                        f"quality={quality}, {kb:.1f}KB"
                    )

                    if kb <= target_kb:
                        buffer.seek(0)

                        return discord.File(
                            buffer,
                            filename="ootd.jpg"
                        )

            if buffer is None:
                raise RuntimeError(
                    "Could not create compressed image."
                )

            print("[WARN] Using smallest compressed version")

            buffer.seek(0)

            return discord.File(
                buffer,
                filename="ootd.jpg"
            )

    # ---------------------------------------------------------
    # REACTION COUNT
    #
    # Sum ALL emoji reactions.
    # The author's own reactions count normally.
    # ---------------------------------------------------------

    def get_reaction_count(self, msg):

        return sum(
            reaction.count
            for reaction in msg.reactions
        )

    # ---------------------------------------------------------
    # THREAD MESSAGE COUNT
    #
    # Only called when a reaction tie actually affects
    # selection/ranking.
    # ---------------------------------------------------------

    async def get_thread_message_count(self, msg):

        if msg.thread is None:
            return 0

        count = 0

        try:
            async for _ in msg.thread.history(limit=None):
                count += 1

        except Exception as e:
            print(
                f"[WARN] Could not read thread for "
                f"message {msg.id}: {e}"
            )

        return count

    # ---------------------------------------------------------
    # COLLECT POSTS FOR ONE MONTH
    #
    # This does NOT read thread histories.
    # ---------------------------------------------------------

    async def collect_month_posts(
        self,
        ootd_channel,
        year,
        month,
        config,
        current_year,
        current_month
    ):

        # Archive begins May 2023.
        if year == 2023 and month < 5:
            return []

        # Skip future months in the current year.
        if (
            year == current_year
            and month > current_month
        ):
            return []

        start_dt = datetime(
            year,
            month,
            1,
            tzinfo=timezone.utc
        )

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

        now_utc = datetime.now(timezone.utc)

        if start_dt > now_utc:
            return []

        if end_dt > now_utc:
            end_dt = now_utc

        excluded_ids = set(
            config.get(
                "EXCLUDED_REPOST_USER_IDS",
                []
            )
        )

        excluded_names = {
            name.lower()
            for name in config.get(
                "EXCLUDED_REPOST_USERNAMES",
                []
            )
        }

        posts = []

        print(
            f"[DEBUG] Searching {year}-{month:02d}"
        )

        async for msg in ootd_channel.history(
            after=start_dt,
            before=end_dt,
            limit=None
        ):

            if not msg.attachments:
                continue

            # Excluded user ID
            if msg.author.id in excluded_ids:
                continue

            # Excluded username/display name
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
                ).lower()
            }

            if author_names & excluded_names:
                continue

            reaction_count = self.get_reaction_count(msg)

            posts.append({
                "message": msg,
                "reactions": reaction_count,
                "month": month
            })

        print(
            f"[DEBUG] Found {len(posts)} eligible posts "
            f"for {year}-{month:02d}"
        )

        return posts

    # ---------------------------------------------------------
    # RESOLVE ONE REACTION TIE
    #
    # All posts passed here already have the same
    # reaction count.
    #
    # Rank by:
    # 1. Thread messages
    # 2. Random if thread counts also tie
    # ---------------------------------------------------------

    async def resolve_tie(self, posts):

        if len(posts) <= 1:
            return posts

        reaction_count = posts[0]["reactions"]

        print(
            f"[DEBUG] Resolving {len(posts)}-way tie "
            f"at {reaction_count} reactions"
        )

        resolved = []

        for post in posts:

            msg = post["message"]

            thread_count = (
                await self.get_thread_message_count(msg)
            )

            resolved.append({
                **post,
                "thread_messages": thread_count,
                "random_tiebreaker": random.random()
            })

            print(
                f"[DEBUG] Tie candidate {msg.id}: "
                f"{reaction_count} reactions, "
                f"{thread_count} thread messages"
            )

        resolved.sort(
            key=lambda post: (
                post["thread_messages"],
                post["random_tiebreaker"]
            ),
            reverse=True
        )

        return resolved

    # ---------------------------------------------------------
    # SELECT TOP 3 FOR A MONTH
    #
    # OPTIMIZED:
    #
    # We DO NOT resolve every reaction tie in the month.
    #
    # We work downward from the highest reaction score
    # until three positions are filled.
    #
    # Example:
    #
    # 50
    # 45
    # 41
    # 41
    # 41
    # 40
    # 40
    #
    # 50 and 45 automatically win.
    #
    # Only the three posts at 41 need thread checks because
    # they are competing for the final available slot.
    #
    # Nothing at 40 or below is examined further.
    # ---------------------------------------------------------

    async def select_monthly_top_three(self, posts):

        if not posts:
            return []

        # Group posts by reaction total.
        reaction_groups = {}

        for post in posts:
            reaction_groups.setdefault(
                post["reactions"],
                []
            ).append(post)

        winners = []

        # Highest reaction totals first.
        reaction_scores = sorted(
            reaction_groups.keys(),
            reverse=True
        )

        for reaction_score in reaction_scores:

            # Already filled all three spots.
            if len(winners) >= 3:
                break

            group = reaction_groups[
                reaction_score
            ]

            spots_remaining = 3 - len(winners)

            print(
                f"[DEBUG] Considering reaction score "
                f"{reaction_score}: "
                f"{len(group)} post(s), "
                f"{spots_remaining} spot(s) remaining"
            )

            # -------------------------------------------------
            # If the entire reaction group fits into the
            # remaining slots, every post in this group wins.
            #
            # No tiebreak is needed because nobody in this
            # reaction group is being eliminated.
            # -------------------------------------------------

            if len(group) <= spots_remaining:

                winners.extend(group)

                continue

            # -------------------------------------------------
            # More tied posts than available slots.
            #
            # NOW thread activity matters.
            # -------------------------------------------------

            resolved = await self.resolve_tie(
                group
            )

            winners.extend(
                resolved[:spots_remaining]
            )

            break

        return winners[:3]

    # ---------------------------------------------------------
    # RANK THE YEARLY FINALISTS
    #
    # At most 36 posts reach this point.
    #
    # Here we do want a complete ordering because:
    #
    # - highest-ranked images go in the main post
    # - remaining images go into the thread in order
    #
    # Thread activity is only checked for reaction ties.
    # ---------------------------------------------------------

    async def rank_yearly_finalists(self, finalists):

        if not finalists:
            return []

        reaction_groups = {}

        for post in finalists:
            reaction_groups.setdefault(
                post["reactions"],
                []
            ).append(post)

        ranked = []

        for reaction_score in sorted(
            reaction_groups.keys(),
            reverse=True
        ):

            group = reaction_groups[
                reaction_score
            ]

            if len(group) == 1:
                ranked.extend(group)

            else:
                resolved = await self.resolve_tie(
                    group
                )

                ranked.extend(resolved)

        return ranked

    # ---------------------------------------------------------
    # GENERATE YEARLY REPOST
    # ---------------------------------------------------------

    async def generate_yearly_repost(
        self,
        ctx,
        year,
        config
    ):

        now = datetime.now(CENTRAL_TZ)

        current_year = now.year
        current_month = now.month

        # -----------------------------------------------------
        # VALIDATE YEAR
        # -----------------------------------------------------

        if year < 2023:

            await ctx.send(
                "Year must be 2023 or later."
            )

            return

        if year > current_year:

            await ctx.send(
                f"{year} is in the future."
            )

            return

        # -----------------------------------------------------
        # CHANNELS
        # -----------------------------------------------------

        ootd_channel = self.bot.get_channel(
            config["OOTD_CHANNEL_ID"]
        )

        yearly_channel = self.bot.get_channel(
            config["WEEKLYPOST_CHANNEL_ID"]
        )

        if not ootd_channel:

            await ctx.send(
                "OOTD channel not found."
            )

            return

        if not yearly_channel:

            await ctx.send(
                "Weekly/yearly repost channel not found."
            )

            return

        await ctx.send(
            f"Collecting the top OOTDs of {year}..."
        )

        finalists = []

        # -----------------------------------------------------
        # TOP THREE FROM EACH MONTH
        # -----------------------------------------------------

        for month in range(1, 13):

            posts = await self.collect_month_posts(
                ootd_channel,
                year,
                month,
                config,
                current_year,
                current_month
            )

            if not posts:

                print(
                    f"[DEBUG] No eligible posts for "
                    f"{year}-{month:02d}"
                )

                continue

            monthly_top = (
                await self.select_monthly_top_three(
                    posts
                )
            )

            finalists.extend(
                monthly_top
            )

            print(
                f"[DEBUG] {year}-{month:02d}: "
                f"selected {len(monthly_top)} posts"
            )

            for position, post in enumerate(
                monthly_top,
                start=1
            ):

                msg = post["message"]

                print(
                    f"[DEBUG]   #{position}: "
                    f"message={msg.id}, "
                    f"reactions={post['reactions']}"
                )

        # -----------------------------------------------------
        # NO POSTS
        # -----------------------------------------------------

        if not finalists:

            await ctx.send(
                f"No eligible OOTD posts found for {year}."
            )

            return

        print(
            f"[DEBUG] Collected {len(finalists)} "
            f"monthly finalists"
        )

        # -----------------------------------------------------
        # RANK THE FINALISTS ACROSS THE WHOLE YEAR
        # -----------------------------------------------------

        finalists = (
            await self.rank_yearly_finalists(
                finalists
            )
        )

        print(
            "[DEBUG] Final yearly ranking complete"
        )

        # Show final ranking in terminal.
        for position, finalist in enumerate(
            finalists,
            start=1
        ):

            print(
                f"[DEBUG] Year rank #{position}: "
                f"message={finalist['message'].id}, "
                f"reactions={finalist['reactions']}, "
                f"month={finalist['month']}"
            )

        # -----------------------------------------------------
        # PREPARE IMAGES
        #
        # First attachment only.
        # -----------------------------------------------------

        prepared = []

        for position, finalist in enumerate(
            finalists,
            start=1
        ):

            msg = finalist["message"]

            attachment = msg.attachments[0]

            print(
                f"[DEBUG] Preparing yearly rank "
                f"#{position}: "
                f"message={msg.id}, "
                f"reactions={finalist['reactions']}"
            )

            try:

                file = await self.compress_to_target(
                    attachment
                )

                prepared.append({
                    "file": file,
                    "reactions":
                        finalist["reactions"],
                    "message": msg,
                    "month":
                        finalist["month"]
                })

            except Exception as e:

                print(
                    f"[ERROR] Failed to prepare "
                    f"message {msg.id}: {e}"
                )

        if not prepared:

            await ctx.send(
                "No images could be prepared."
            )

            return

        print(
            f"[DEBUG] Prepared {len(prepared)} "
            f"yearly images"
        )

        # -----------------------------------------------------
        # FIRST POST
        #
        # Up to 10 highest-ranked images.
        # -----------------------------------------------------

        first_batch = prepared[
            :MAX_FILES_PER_MESSAGE
        ]

        first_files = [
            item["file"]
            for item in first_batch
        ]

        try:

            print(
                f"[DEBUG] Sending first yearly batch "
                f"with {len(first_files)} images"
            )

            header_msg = await yearly_channel.send(
                content=(
                    f"Top OOTDs of {year}\n"
                    f"Top {len(prepared)} fits selected "
                    f"from the year's monthly leaders."
                ),
                files=first_files
            )

            print(
                f"[DEBUG] Yearly header sent: "
                f"{header_msg.id}"
            )

        except Exception as e:

            print(
                f"[ERROR] Failed to send yearly header: "
                f"{e}"
            )

            await ctx.send(
                "Failed to send yearly repost."
            )

            return

        # -----------------------------------------------------
        # CREATE THREAD
        # -----------------------------------------------------

        try:

            print(
                "[DEBUG] Creating yearly thread..."
            )

            thread = await header_msg.create_thread(
                name=f"Top OOTDs of {year}"
            )

            print(
                f"[DEBUG] Yearly thread created: "
                f"{thread.id}"
            )

        except Exception as e:

            print(
                f"[ERROR] Failed to create yearly thread: "
                f"{e}"
            )

            await ctx.send(
                "Main post created, but thread creation "
                "failed."
            )

            return

        # -----------------------------------------------------
        # REMAINING IMAGES
        #
        # Up to 10 images per thread message.
        # -----------------------------------------------------

        remaining = prepared[
            MAX_FILES_PER_MESSAGE:
        ]

        for i in range(
            0,
            len(remaining),
            MAX_FILES_PER_MESSAGE
        ):

            batch = remaining[
                i:i + MAX_FILES_PER_MESSAGE
            ]

            batch_files = [
                item["file"]
                for item in batch
            ]

            try:

                print(
                    f"[DEBUG] Sending thread batch "
                    f"with {len(batch_files)} images"
                )

                await thread.send(
                    files=batch_files
                )

            except Exception as e:

                print(
                    f"[ERROR] Failed to send yearly "
                    f"thread batch: {e}"
                )

        # -----------------------------------------------------
        # COMPLETE
        # -----------------------------------------------------

        await ctx.send(
            f"Top OOTDs of {year} posted."
        )

        print(
            f"[DEBUG] Yearly repost for {year} complete"
        )

    # ---------------------------------------------------------
    # NORMAL COMMAND
    #
    # !year 2025
    # ---------------------------------------------------------

    @commands.command()
    async def year(
        self,
        ctx,
        year: int
    ):

        if ctx.guild is None:

            await ctx.send(
                "This command can only be used "
                "in a server."
            )

            return

        if ctx.guild.id not in GUILD_CONFIG:

            await ctx.send(
                "Server not configured."
            )

            return

        config = GUILD_CONFIG[
            ctx.guild.id
        ]

        if (
            ctx.channel.id
            != config["BOT_CHANNEL_ID"]
        ):

            await ctx.send(
                f"Use in "
                f"<#{config['BOT_CHANNEL_ID']}>"
            )

            return

        await self.generate_yearly_repost(
            ctx,
            year,
            config
        )

    # ---------------------------------------------------------
    # SHORTHAND COMMAND
    #
    # Allows:
    #
    # !2023
    # !2024
    # !2025
    # !2026
    # etc.
    # ---------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(
        self,
        message
    ):

        if message.author.bot:
            return

        if message.guild is None:
            return

        content = message.content.strip()

        if not content.startswith("!"):
            return

        command_text = content[1:]

        if (
            len(command_text) != 4
            or not command_text.isdigit()
        ):
            return

        year = int(command_text)

        if (
            message.guild.id
            not in GUILD_CONFIG
        ):
            return

        config = GUILD_CONFIG[
            message.guild.id
        ]

        if (
            message.channel.id
            != config["BOT_CHANNEL_ID"]
        ):

            await message.channel.send(
                f"Use in "
                f"<#{config['BOT_CHANNEL_ID']}>"
            )

            return

        ctx = await self.bot.get_context(
            message
        )

        await self.generate_yearly_repost(
            ctx,
            year,
            config
        )


async def setup(bot):

    print(
        "[DEBUG] Loading YearlyRepostCog..."
    )

    await bot.add_cog(
        YearlyRepostCog(bot)
    )
