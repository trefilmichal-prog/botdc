from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord.ext import commands, tasks

from config import (
    TIME_STATUS_CHANNEL_ID,
    TIME_STATUS_STATE_NAME,
    TIME_STATUS_STATE_TIMEZONE,
)
from db import get_setting, set_setting


class TimeStatusCog(commands.Cog, name="TimeStatusCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.log = logging.getLogger(__name__)
        self.message_id: int | None = None
        self.state_name = get_setting("time_status_state_name") or TIME_STATUS_STATE_NAME
        self.state_timezone_name = (
            get_setting("time_status_state_timezone") or TIME_STATUS_STATE_TIMEZONE
        )

        self.status_updater.start()

    def cog_unload(self):
        self.status_updater.cancel()

    @tasks.loop(minutes=5)
    async def status_updater(self):
        await self._update_or_create_message()

    @status_updater.before_loop
    async def _wait_for_ready(self):
        await self.bot.wait_until_ready()

    async def _update_or_create_message(self):
        channel_id = self._get_channel_id()
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            self.log.warning("Time status channel %s nenalezen nebo není textový.", channel_id)
            return

        set_setting("time_status_state_name", self.state_name)
        set_setting("time_status_state_timezone", self.state_timezone_name)

        message = await self._fetch_message(channel)
        embed = self._build_embed()

        if message:
            await message.edit(embed=embed)
        else:
            message = await channel.send(embed=embed)
            set_setting("time_status_message_id", str(message.id))
            set_setting("time_status_channel_id", str(channel.id))

        self.message_id = message.id

    def _get_channel_id(self) -> int:
        channel_id_str = get_setting("time_status_channel_id")
        if channel_id_str:
            try:
                return int(channel_id_str)
            except ValueError:
                pass
        return TIME_STATUS_CHANNEL_ID

    async def _fetch_message(self, channel: discord.TextChannel) -> discord.Message | None:
        message_id_str = get_setting("time_status_message_id")
        if message_id_str:
            try:
                message_id = int(message_id_str)
            except ValueError:
                message_id = None
            if message_id:
                try:
                    return await channel.fetch_message(message_id)
                except (discord.NotFound, discord.Forbidden):
                    return None
        return None

    def _build_embed(self) -> discord.Embed:
        utc_now = datetime.now(timezone.utc)
        cz_time = utc_now.astimezone(self._get_cz_zone())
        state_time = utc_now.astimezone(self._get_state_zone())

        cz_label = self._get_english_daypart(cz_time.hour)
        state_label = self._get_czech_daypart(state_time.hour)

        embed = discord.Embed(
            title="Časový přehled",
            description=(
                f"🇨🇿 In Czech Republic is rn **{cz_label}** ({cz_time:%H:%M})\n"
                f"🇺🇸 Ve státě {self.state_name} je právě **{state_label}** ({state_time:%H:%M})"
            ),
            color=0x3498DB,
        )
        embed.set_footer(text="Automatická aktualizace každých 5 minut.")
        return embed

    def _get_cz_zone(self) -> timezone:
        try:
            return ZoneInfo("Europe/Prague")
        except ZoneInfoNotFoundError:
            self.log.warning("Time zone 'Europe/Prague' nenalezena, používám UTC+1.")
            return timezone(timedelta(hours=1))

    def _get_state_zone(self) -> timezone:
        try:
            return ZoneInfo(self.state_timezone_name)
        except ZoneInfoNotFoundError:
            self.log.warning(
                "Neplatná time zone '%s', používám UTC.", self.state_timezone_name
            )
            return timezone.utc

    @staticmethod
    def _get_english_daypart(hour: int) -> str:
        if 5 <= hour < 12:
            return "Morning"
        if 12 <= hour < 17:
            return "Afternoon"
        if 17 <= hour < 21:
            return "Evening"
        return "Night Time"

    @staticmethod
    def _get_czech_daypart(hour: int) -> str:
        if 5 <= hour < 12:
            return "Ráno"
        if 12 <= hour < 17:
            return "Odpoledne"
        if 17 <= hour < 21:
            return "Večer"
        return "Noc"


async def setup(bot: commands.Bot):
    await bot.add_cog(TimeStatusCog(bot))
