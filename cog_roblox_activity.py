import logging
import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Set

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from config import (
    CLAN2_MEMBER_ROLE_ID,
    CLAN_MEMBER_ROLE_EN_ID,
    CLAN_MEMBER_ROLE_ID,
    REBIRTH_CHAMPIONS_UNIVERSE_ID,
)


ROBLOX_USERNAMES_URL = "https://users.roblox.com/v1/usernames/users"
ROBLOX_PRESENCE_URL = "https://presence.roblox.com/v1/presence/users"
ROBLOX_USERNAME_REGEX = re.compile(r"[A-Za-z0-9_]{3,20}")


class RobloxActivityCog(commands.Cog, name="RobloxActivity"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._logger = logging.getLogger(__name__)
        self._session: Optional[aiohttp.ClientSession] = None

    async def cog_load(self):
        self._session = aiohttp.ClientSession()

    async def cog_unload(self):
        if self._session and not self._session.closed:
            await self._session.close()

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
                universe_id = entry.get("universeId")
                place_id = entry.get("placeId")
                is_playing = (
                    entry.get("userPresenceType") == 2
                    and (universe_id == REBIRTH_CHAMPIONS_UNIVERSE_ID
                         or place_id == REBIRTH_CHAMPIONS_UNIVERSE_ID)
                )
                result[int(user_id)] = bool(is_playing)

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

        tracked = await self._collect_tracked_members(interaction.guild)
        if not tracked:
            await interaction.followup.send(
                "Nenašel jsem žádné členy s potřebnými rolemi a Roblox nickem v přezdívce.",
                ephemeral=True,
            )
            return

        usernames = list(tracked.keys())
        resolved_ids, missing_usernames = await self._fetch_user_ids(usernames)
        presence = await self._fetch_presence(resolved_ids.values()) if resolved_ids else {}

        playing_lines: list[str] = []
        idle_lines: list[str] = []
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
            is_playing = presence.get(user_id)
            if is_playing is True:
                playing_lines.append(f"`{username}` ({members_text})")
            elif is_playing is False:
                idle_lines.append(f"`{username}` ({members_text})")
            else:
                unresolved_lines.append(
                    f"`{username}` ({members_text}) – status se nepodařilo zjistit"
                )

        if missing_usernames:
            unresolved_lines.append(
                "Nebylo možné získat data pro: "
                + ", ".join(f"`{name}`" for name in sorted(missing_usernames))
            )

        embed = discord.Embed(
            title="Kontrola aktivity v Rebirth Champions Ultimate",
            colour=discord.Color.blurple(),
            description=(
                "Monitorované role: HROT, HROT EN a HR2T. "
                "Nick v přezdívce musí odpovídat Roblox uživatelskému jménu."
            ),
        )

        if playing_lines:
            embed.add_field(
                name="Ve hře",
                value="\n".join(sorted(playing_lines)),
                inline=False,
            )
        else:
            embed.add_field(
                name="Ve hře",
                value="Nikdo z monitorovaných členů není právě ve hře.",
                inline=False,
            )

        if idle_lines:
            embed.add_field(
                name="Mimo hru",
                value="\n".join(sorted(idle_lines)),
                inline=False,
            )

        if unresolved_lines:
            embed.add_field(
                name="Nepodařilo se ověřit",
                value="\n".join(unresolved_lines),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RobloxActivityCog(bot))
