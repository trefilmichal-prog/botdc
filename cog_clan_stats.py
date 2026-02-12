from typing import Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import SETUP_MANAGER_ROLE_ID
from db import get_clan_stats_channel, set_clan_stats_channel


class ClanStatsOcrCog(commands.Cog, name="ClanStatsOcr"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.stats_channel_id: Optional[int] = get_clan_stats_channel()

    @app_commands.command(
        name="setup_clan_stats_room",
        description="Nastaví kanál, kam se budou posílat statistiky clanu.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.checks.has_role(SETUP_MANAGER_ROLE_ID)
    async def setup_clan_stats_room(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        self.stats_channel_id = channel.id
        set_clan_stats_channel(channel.id)
        await interaction.response.send_message(
            f"Kanál pro clan statistiky nastaven na {channel.mention}.",
            ephemeral=True,
        )

    @app_commands.command(
        name="clan_stats_dm",
        description="Pošli ručně hodnoty clan statistik (pouze přes DM zprávu bota).",
    )
    async def submit_clan_stats(
        self,
        interaction: discord.Interaction,
        season_rebirths: str,
        weekly_rebirths: str,
        total_rebirths: str,
        weekly_hatching_points: str,
        eggs_opened: str,
    ):
        if interaction.guild is not None:
            await interaction.response.send_message(
                "Tento příkaz používej v soukromé zprávě bota.", ephemeral=True
            )
            return

        channel = await self._resolve_stats_channel()
        if channel is None:
            await interaction.response.send_message(
                "Není nastaven žádný kanál pro zapisování clan statistik. "
                "Použij na serveru `/setup_clan_stats_room`.",
                ephemeral=True,
            )
            return

        stats = {
            "season_rebirths": season_rebirths,
            "weekly_rebirths": weekly_rebirths,
            "total_rebirths": total_rebirths,
            "weekly_hatching_points": weekly_hatching_points,
            "eggs_opened": eggs_opened,
        }

        view = self._build_stats_view(interaction.user, stats)
        await channel.send(view=view)
        await interaction.response.send_message(
            "Statistiky byly odeslány do nastavené roomky.", ephemeral=True
        )

    async def _resolve_stats_channel(self) -> Optional[discord.TextChannel]:
        if self.stats_channel_id is None:
            return None

        channel = self.bot.get_channel(self.stats_channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel

        try:
            fetched = await self.bot.fetch_channel(self.stats_channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

        if isinstance(fetched, discord.TextChannel):
            return fetched
        return None

    def _build_stats_view(
        self, author: discord.User, stats: Dict[str, str]
    ) -> discord.ui.LayoutView:
        label_map = {
            "season_rebirths": "(Season 5) Rebirths",
            "weekly_rebirths": "Weekly Rebirths",
            "total_rebirths": "Total Rebirths",
            "weekly_hatching_points": "Weekly Hatching Points",
            "eggs_opened": "Eggs Opened",
        }

        lines = ["## Clan statistiky", f"Nahlásil {author.mention}"]
        for key in label_map:
            lines.append(
                f"**{label_map[key]}:** {stats.get(key, 'Nedetekováno')}"
            )

        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.Container(*(discord.ui.TextDisplay(content=line) for line in lines))
        )
        return view
