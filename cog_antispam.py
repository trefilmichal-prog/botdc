import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta

import discord
from discord.ext import commands

from config import (
    ANTISPAM_DUPLICATE_LIMIT,
    ANTISPAM_DUPLICATE_WINDOW_SECONDS,
    ANTISPAM_MESSAGE_LIMIT,
    ANTISPAM_NOTICE_COOLDOWN_SECONDS,
    ANTISPAM_TIME_WINDOW_SECONDS,
    ANTISPAM_TIMEOUT_SECONDS,
)


class AntiSpamCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("botdc")
        self.user_message_history: dict[int, deque[datetime]] = defaultdict(deque)
        self.user_duplicate_history: dict[int, deque[tuple[str, datetime]]] = defaultdict(
            deque
        )
        self.user_notice_timestamps: dict[int, datetime] = {}

    @staticmethod
    def _prune_timestamp_history(history: deque[datetime], window_seconds: int) -> None:
        cutoff = discord.utils.utcnow() - timedelta(seconds=window_seconds)
        while history and history[0] < cutoff:
            history.popleft()

    @staticmethod
    def _prune_duplicate_history(
        history: deque[tuple[str, datetime]], window_seconds: int
    ) -> None:
        cutoff = discord.utils.utcnow() - timedelta(seconds=window_seconds)
        while history and history[0][1] < cutoff:
            history.popleft()

    @staticmethod
    def _message_signature(message: discord.Message) -> str:
        attachments = "|".join(attachment.url for attachment in message.attachments)
        return f"{message.clean_content.strip()}::{attachments}"

    def _should_send_notice(self, user_id: int) -> bool:
        last_notice = self.user_notice_timestamps.get(user_id)
        now = discord.utils.utcnow()
        if last_notice is None or (now - last_notice).total_seconds() >= int(
            ANTISPAM_NOTICE_COOLDOWN_SECONDS
        ):
            self.user_notice_timestamps[user_id] = now
            return True
        return False

    async def _timeout_member(self, member: discord.Member, reason: str) -> bool:
        if ANTISPAM_TIMEOUT_SECONDS <= 0:
            return False

        if member.guild.me is None:
            return False

        if not member.guild.me.guild_permissions.moderate_members:
            return False

        duration = timedelta(seconds=int(ANTISPAM_TIMEOUT_SECONDS))
        try:
            await member.timeout(duration, reason=reason)
        except discord.Forbidden:
            return False
        except discord.HTTPException:
            return False

        try:
            await member.send(
                f"\N{WARNING SIGN} Byl(a) jsi dočasně umlčen(a) za spam na serveru. "
                f"Délka: {duration}. Důvod: {reason}"
            )
        except discord.Forbidden:
            pass

        return True

    async def _handle_spam(self, message: discord.Message, reason: str) -> None:
        self.logger.warning(
            "Detekován spam: user=%s channel=%s reason=%s", message.author, message.channel, reason
        )

        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

        timeout_applied = False
        if isinstance(message.author, discord.Member):
            timeout_applied = await self._timeout_member(message.author, reason)

        if self._should_send_notice(message.author.id):
            notice = (
                f"{message.author.mention}, prosím zpomal. Detekovali jsme spam ({reason})."
            )
            if timeout_applied:
                notice += " Uživatel byl dočasně umlčen."

            try:
                await message.channel.send(notice, delete_after=15)
            except (discord.Forbidden, discord.HTTPException):
                pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        now = discord.utils.utcnow()

        message_history = self.user_message_history[message.author.id]
        self._prune_timestamp_history(message_history, int(ANTISPAM_TIME_WINDOW_SECONDS))
        message_history.append(now)

        if len(message_history) > int(ANTISPAM_MESSAGE_LIMIT):
            await self._handle_spam(
                message,
                f"více než {ANTISPAM_MESSAGE_LIMIT} zpráv za {ANTISPAM_TIME_WINDOW_SECONDS} s",
            )
            return

        signature = self._message_signature(message)
        duplicate_history = self.user_duplicate_history[message.author.id]
        self._prune_duplicate_history(
            duplicate_history, int(ANTISPAM_DUPLICATE_WINDOW_SECONDS)
        )
        duplicate_history.append((signature, now))

        duplicate_count = sum(1 for content, _ in duplicate_history if content == signature)
        if duplicate_count >= int(ANTISPAM_DUPLICATE_LIMIT):
            await self._handle_spam(
                message,
                f"{ANTISPAM_DUPLICATE_LIMIT} stejných zpráv za {ANTISPAM_DUPLICATE_WINDOW_SECONDS} s",
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiSpamCog(bot))
