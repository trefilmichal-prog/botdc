import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Set, Tuple

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    CLAN2_MEMBER_ROLE_ID,
    CLAN_MEMBER_ROLE_EN_ID,
    CLAN_MEMBER_ROLE_ID,
    ROBLOX_ACTIVITY_CHANNEL_ID,
)


ROBLOX_USERNAMES_URL = "https://users.roblox.com/v1/usernames/users"
ROBLOX_PRESENCE_URL = "https://presence.roblox.com/v1/presence/users"
ROBLOX_USERNAME_REGEX = re.compile(r"[A-Za-z0-9_]{3,20}")


class RobloxActivityCog(commands.Cog, name="RobloxActivity"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._logger = logging.getLogger(__name__)
        self._session: Optional[aiohttp.ClientSession] = None
        self._presence_state: Dict[int, Tuple[Optional[bool], datetime]] = {}

    async def cog_load(self):
        self._session = aiohttp.ClientSession()
        self.presence_notifier.start()

    async def cog_unload(self):
        if self._session and not self._session.closed:
            await self._session.close()
        self.presence_notifier.cancel()

    def _find_roblox_username(self, member: discord.Member) -> Optional[str]:
        nickname = member.nick or member.global_name or member.name
        matches = ROBLOX_USERNAME_REGEX.findall(nickname)
        if not matches:
            return None
        return matches[0]

    async def _fetch_user_ids(self, usernames: List[str]) -> tuple[Dict[str, int], Set[str]]:
        if not self._session:
            raise RuntimeError("RobloxActivityCog session is not initialized")

        resolved: Dict[str, int] = {}
        missing: Set[str] = set()

        for i in range(0, len(usernames), 100):
            batch = usernames[i : i + 100]
            payload = {"usernames": batch, "excludeBannedUsers": True}
            try:
                async with self._session.post(
                    ROBLOX_USERNAMES_URL, json=payload, timeout=20
                ) as resp:
                    if resp.status != 200:
                        self._logger.warning(
                            "Roblox usernames API returned %s", resp.status
                        )
                        missing.update(batch)
                        continue
                    data = await resp.json()
            except aiohttp.ClientError as exc:
                self._logger.warning("Roblox usernames API error: %s", exc)
                missing.update(batch)
                continue

            found_names = set()
            for entry in data.get("data", []):
                username = entry.get("requestedUsername")
                user_id = entry.get("id")
                if not username or user_id is None:
                    continue
                resolved[username.lower()] = int(user_id)
                found_names.add(username.lower())

            for name in batch:
                if name.lower() not in found_names:
                    missing.add(name)

        return resolved, missing

    async def _fetch_presence(self, user_ids: Iterable[int]) -> Dict[int, Optional[bool]]:
        if not self._session:
            raise RuntimeError("RobloxActivityCog session is not initialized")

        result: Dict[int, Optional[bool]] = {}
        ids = list(user_ids)
        for i in range(0, len(ids), 100):
            batch = ids[i : i + 100]
            try:
                async with self._session.post(
                    ROBLOX_PRESENCE_URL, json={"userIds": batch}, timeout=20
                ) as resp:
                    if resp.status != 200:
                        self._logger.warning(
                            "Roblox presence API returned %s", resp.status
                        )
                        for user_id in batch:
                            result[user_id] = None
                        continue
                    data = await resp.json()
            except aiohttp.ClientError as exc:
                self._logger.warning("Roblox presence API error: %s", exc)
                for user_id in batch:
                    result[user_id] = None
                continue

            for entry in data.get("userPresences", []):
                user_id = entry.get("userId")
                if user_id is None:
                    continue

                # Presence types: 0=offline, 1=online, 2=in-game, 3=in-studio.
                presence_type = entry.get("userPresenceType")
                if presence_type is None:
                    result[int(user_id)] = None
                    continue

                result[int(user_id)] = presence_type != 0

        return result

    async def _collect_tracked_members(
        self, guild: discord.Guild
    ) -> Dict[str, list[discord.Member]]:
        tracked_roles = {
            CLAN_MEMBER_ROLE_ID,
            CLAN_MEMBER_ROLE_EN_ID,
            CLAN2_MEMBER_ROLE_ID,
        }
        usernames: Dict[str, list[discord.Member]] = defaultdict(list)
        for member in guild.members:
            if member.bot:
                continue
            if not any(role.id in tracked_roles for role in member.roles):
                continue
            username = self._find_roblox_username(member)
            if not username:
                continue
            usernames[username].append(member)

        return usernames

    def _format_timedelta(self, delta_seconds: float) -> str:
        seconds = int(delta_seconds)
        parts: list[str] = []

        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)

        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if seconds or not parts:
            parts.append(f"{seconds}s")

        return " ".join(parts)

    def _record_and_format_duration(
        self, user_id: int, status: Optional[bool], now: datetime
    ) -> str:
        previous = self._presence_state.get(user_id)
        if previous is None or previous[0] != status:
            self._presence_state[user_id] = (status, now)
            delta = 0.0
        else:
            delta = (now - previous[1]).total_seconds()
        return self._format_timedelta(delta)

    def _build_status_lines(
        self,
        tracked: Dict[str, list[discord.Member]],
        resolved_ids: Dict[str, int],
        presence: Dict[int, Optional[bool]],
        missing_usernames: Set[str],
        now: datetime,
    ) -> tuple[list[str], list[str], list[str]]:
        online_lines: list[str] = []
        offline_lines: list[str] = []
        unresolved_lines: list[str] = []

        for username, members in tracked.items():
            members_text = ", ".join(m.mention for m in members)
            lower = username.lower()
            if lower not in resolved_ids:
                unresolved_lines.append(
                    f"`{username}` ({members_text}) – Roblox účet nenalezen"
                )
                continue

            user_id = resolved_ids[lower]
            is_online = presence.get(user_id)
            duration = self._record_and_format_duration(user_id, is_online, now)

            if is_online is True:
                online_lines.append(
                    f"🟢 `{username}` ({members_text}) – online {duration}"
                )
            elif is_online is False:
                offline_lines.append(
                    f"🔴 `{username}` ({members_text}) – offline {duration}"
                )
            else:
                unresolved_lines.append(
                    f"`{username}` ({members_text}) – status se nepodařilo zjistit"
                )

        if missing_usernames:
            unresolved_lines.append(
                "Nebylo možné získat data pro: "
                + ", ".join(f"`{name}`" for name in sorted(missing_usernames))
            )

        return online_lines, offline_lines, unresolved_lines

    @staticmethod
    def _chunk_lines(lines: list[str], limit: int = 1024) -> list[str]:
        """Split a list of lines into strings that fit into embed field limits."""

        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        for line in lines:
            line_len = len(line)
            # If a single line is too long, truncate it hard to avoid API errors.
            if line_len > limit:
                line = line[: limit - 1] + "…"
                line_len = len(line)

            extra_len = line_len + (1 if current else 0)
            if current_len + extra_len > limit:
                chunks.append("\n".join(current))
                current = [line]
                current_len = line_len
            else:
                current.append(line)
                current_len += extra_len

        if current:
            chunks.append("\n".join(current))

        return chunks

    def _add_lines_field(
        self,
        embed: discord.Embed,
        name: str,
        lines: list[str],
        empty_message: str,
    ) -> None:
        """Add an embed field, splitting into multiple fields if necessary."""

        if not lines:
            if empty_message:
                embed.add_field(name=name, value=empty_message, inline=False)
            return

        chunks = self._chunk_lines(lines)
        for idx, chunk in enumerate(chunks):
            field_name = name if idx == 0 else f"{name} (pokračování {idx})"
            embed.add_field(name=field_name, value=chunk, inline=False)

    @app_commands.command(
        name="roblox_activity",
        description="Zkontroluje, kdo z členů clanu hraje Rebirth Champions Ultimate.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def roblox_activity(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                "Tento příkaz lze použít pouze na serveru.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        embed = await self._build_presence_embed(interaction.guild)
        if embed is None:
            await interaction.followup.send(
                "Nenašel jsem žádné členy s potřebnými rolemi a Roblox nickem v přezdívce.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _build_presence_embed(
        self, guild: discord.Guild
    ) -> Optional[discord.Embed]:
        tracked = await self._collect_tracked_members(guild)
        if not tracked:
            return None

        usernames = list(tracked.keys())
        resolved_ids, missing_usernames = await self._fetch_user_ids(usernames)
        presence = await self._fetch_presence(resolved_ids.values()) if resolved_ids else {}
        now = datetime.now(timezone.utc)

        online_lines, offline_lines, unresolved_lines = self._build_status_lines(
            tracked, resolved_ids, presence, missing_usernames, now
        )

        embed = discord.Embed(
            title="Kontrola přítomnosti na Robloxu",
            colour=discord.Color.blurple(),
            description=(
                "Monitorované role: HROT, HROT EN a HR2T. "
                "Nick v přezdívce musí obsahovat Roblox uživatelské jméno."
            ),
        )

        self._add_lines_field(
            embed,
            name="Online",
            lines=sorted(online_lines),
            empty_message="Nikdo z monitorovaných členů není právě online na Robloxu.",
        )

        self._add_lines_field(
            embed,
            name="Offline",
            lines=sorted(offline_lines),
            empty_message="",  # When empty we simply omit the field.
        )

        self._add_lines_field(
            embed,
            name="Nepodařilo se ověřit",
            lines=unresolved_lines,
            empty_message="",  # When empty we simply omit the field.
        )

        embed.set_footer(text="Automatická kontrola každých 10 minut. Stav se resetuje při změně.")
        return embed

    @tasks.loop(minutes=10)
    async def presence_notifier(self):
        channel = self.bot.get_channel(ROBLOX_ACTIVITY_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            return

        embed = await self._build_presence_embed(channel.guild)
        if embed is None:
            await channel.send(
                "Nenašel jsem žádné členy s potřebnými rolemi a Roblox nickem v přezdívce."
            )
            return

        await channel.send(embed=embed)

    @presence_notifier.before_loop
    async def _wait_for_ready(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(RobloxActivityCog(bot))
