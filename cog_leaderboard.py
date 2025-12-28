import asyncio

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import CLAN_MEMBER_ROLE_ID, SETUP_MANAGER_ROLE_ID
from db import (
    add_clan_panel,
    add_leaderboard_panel,
    get_all_clan_panels,
    get_all_leaderboard_panels,
    get_top_users_by_stat,
    remove_clan_panel,
    remove_leaderboard_panel,
)
from i18n import DEFAULT_LOCALE, get_interaction_locale, t


class LeaderboardCog(commands.Cog, name="Leaderboard"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.panel_refresh_loop.start()

    def cog_unload(self):
        self.panel_refresh_loop.cancel()

    @app_commands.command(
        name="leaderboard", description="Ukáže žebříček podle coinů nebo počtu zpráv."
    )
    @app_commands.choices(
        metric=[
            app_commands.Choice(name="Coiny", value="coins"),
            app_commands.Choice(name="Zprávy", value="message_count"),
        ]
    )
    @app_commands.checks.has_role(SETUP_MANAGER_ROLE_ID)
    async def leaderboard_cmd(
        self, interaction: discord.Interaction, metric: app_commands.Choice[str]
    ):
        locale = get_interaction_locale(interaction)
        top_users = get_top_users_by_stat(metric.value, limit=10)
        if not top_users:
            await interaction.response.send_message(t("leaderboard_empty", locale))
            return

        title = (
            t("leaderboard_title_coins", locale)
            if metric.value == "coins"
            else t("leaderboard_title_messages", locale)
        )
        lines = []
        for idx, (user_id, value) in enumerate(top_users, start=1):
            mention = f"<@{user_id}>"
            lines.append(f"**{idx}.** {mention} – {value}")
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(content=f"## {title}"),
                discord.ui.TextDisplay(content="\n".join(lines)),
            )
        )
        await interaction.response.send_message(content="", view=view)

    def build_leaderboard_view(
        self, locale: discord.Locale = DEFAULT_LOCALE
    ) -> discord.ui.LayoutView:
        view = discord.ui.LayoutView(timeout=None)
        lines = [f"## {t('panel_title', locale)}"]
        for label, stat in (
            (t("panel_section_coins", locale), "coins"),
            (t("panel_section_messages", locale), "message_count"),
        ):
            top_users = get_top_users_by_stat(stat, limit=10)
            if top_users:
                lines = [
                    f"**{idx}.** <@{user_id}> – {value}"
                    for idx, (user_id, value) in enumerate(top_users, start=1)
                ]
                value = "\n".join(lines)
            else:
                value = t("panel_no_data", locale)

            lines.append(f"### {label}")
            lines.append(value)

        lines.append(t("panel_footer", locale))
        view.add_item(
            discord.ui.Container(*(discord.ui.TextDisplay(content=line) for line in lines))
        )
        return view

    @app_commands.command(
        name="setup_clan_room",
        description="Odešle do vybraného kanálu přehled členů s klanovou rolí.",
    )
    @app_commands.describe(channel="Kanál, kam se má zpráva poslat.")
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.checks.has_role(SETUP_MANAGER_ROLE_ID)
    async def setup_clan_room(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        locale = get_interaction_locale(interaction)
        role = interaction.guild.get_role(CLAN_MEMBER_ROLE_ID) if interaction.guild else None
        if role is None:
            await interaction.response.send_message(
                t("clan_setup_role_missing", locale, role_id=CLAN_MEMBER_ROLE_ID),
                ephemeral=True,
            )
            return

        view = self.build_clan_panel_view(role, locale)
        message = await channel.send(content="", view=view)
        add_clan_panel(channel.guild.id, channel.id, message.id)

        await interaction.response.send_message(
            t("clan_setup_sent", locale, channel=channel.mention),
            ephemeral=True,
        )

    @app_commands.command(
        name="setup_leaderboard",
        description="Odešle do vybraného kanálu žebříček coinů a zpráv.",
    )
    @app_commands.describe(channel="Kanál, kam se má žebříček poslat.")
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.checks.has_role(SETUP_MANAGER_ROLE_ID)
    async def setup_leaderboard_room(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        locale = get_interaction_locale(interaction)
        view = self.build_leaderboard_view(locale)
        message = await channel.send(content="", view=view)
        if interaction.guild:
            add_leaderboard_panel(interaction.guild.id, channel.id, message.id)

        await interaction.response.send_message(
            t("leaderboard_setup_sent", locale, channel=channel.mention), ephemeral=True
        )

    def build_clan_panel_view(
        self, role: discord.Role, locale: discord.Locale = DEFAULT_LOCALE
    ) -> discord.ui.LayoutView:
        members = sorted(role.members, key=lambda m: m.display_name.lower())
        if members:
            member_lines = [member.mention for member in members]
            description = "\n".join(member_lines)
        else:
            description = t("clan_panel_empty", locale)
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(content=f"## {t('clan_panel_title', locale)}"),
                discord.ui.TextDisplay(content=description),
                discord.ui.TextDisplay(content=t("panel_footer", locale)),
            )
        )
        return view

    async def refresh_clan_panels(self):
        panels = get_all_clan_panels()
        if not panels:
            return

        for guild_id, channel_id, message_id in panels:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue

            role = guild.get_role(CLAN_MEMBER_ROLE_ID) if CLAN_MEMBER_ROLE_ID else None
            if role is None:
                view = discord.ui.LayoutView(timeout=None)
                view.add_item(
                    discord.ui.Container(
                        discord.ui.TextDisplay(
                            content=f"## {t('clan_panel_title', DEFAULT_LOCALE)}"
                        ),
                        discord.ui.TextDisplay(
                            content=t("clan_panel_role_missing", DEFAULT_LOCALE)
                        ),
                    )
                )
            else:
                view = self.build_clan_panel_view(role)

            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                remove_clan_panel(message_id)
                continue

            try:
                msg = await channel.fetch_message(message_id)
            except discord.NotFound:
                remove_clan_panel(message_id)
                continue
            except discord.HTTPException:
                continue

            try:
                await msg.edit(content="", embeds=[], view=view)
                await asyncio.sleep(0.25)
            except discord.HTTPException:
                continue

    async def refresh_leaderboard_panels(self):
        panels = get_all_leaderboard_panels()
        if not panels:
            return

        view = self.build_leaderboard_view()

        for guild_id, channel_id, message_id in panels:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                remove_leaderboard_panel(message_id)
                continue

            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                remove_leaderboard_panel(message_id)
                continue

            try:
                msg = await channel.fetch_message(message_id)
            except discord.NotFound:
                remove_leaderboard_panel(message_id)
                continue
            except discord.HTTPException:
                continue

            try:
                await msg.edit(content="", embeds=[], view=view)
                await asyncio.sleep(0.25)
            except discord.HTTPException:
                continue

    @tasks.loop(minutes=5)
    async def panel_refresh_loop(self):
        try:
            await self.refresh_clan_panels()
            await self.refresh_leaderboard_panels()
        except Exception as exc:  # pragma: no cover - defensive logging
            print(t("panel_refresh_error", DEFAULT_LOCALE, error=exc))

    @panel_refresh_loop.before_loop
    async def before_panel_refresh_loop(self):
        await self.bot.wait_until_ready()
        try:
            await self.refresh_clan_panels()
            await self.refresh_leaderboard_panels()
        except Exception as exc:  # pragma: no cover - defensive logging
            print(f"[panel_refresh_loop] Chyba při počáteční obnově panelů: {exc}")

