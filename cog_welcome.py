import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

WELCOME_CHANNEL_ID = 1440271167234510940
ROLE_WELCOME_CZ = 1444075970649915586
ROLE_WELCOME_EN = 1444075991118119024
WELCOME_TEXT = "Welcome in the Clan Server HROT"


class WelcomeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("botdc")
        self.welcome_group = app_commands.Group(
            name="welcome",
            description="Příkazy pro uvítání členů.",
        )
        self.welcome_group.command(
            name="test",
            description="Pošle uvítací zprávu s náhledem.",
        )(self.send_welcome_preview)
        self.__cog_app_commands__ = []

    async def cog_load(self) -> None:
        existing_group = self.bot.tree.get_command(
            "welcome", type=discord.AppCommandType.chat_input
        )
        if existing_group:
            self.bot.tree.remove_command(
                "welcome", type=discord.AppCommandType.chat_input
            )
        try:
            self.bot.tree.add_command(self.welcome_group)
        except app_commands.CommandAlreadyRegistered:
            pass

    async def cog_unload(self) -> None:
        existing_group = self.bot.tree.get_command(
            "welcome", type=discord.AppCommandType.chat_input
        )
        if existing_group:
            self.bot.tree.remove_command(
                "welcome", type=discord.AppCommandType.chat_input
            )

    async def _get_welcome_channel(self) -> discord.TextChannel | None:
        channel = self.bot.get_channel(WELCOME_CHANNEL_ID)
        if isinstance(channel, discord.TextChannel):
            return channel

        try:
            fetched = await self.bot.fetch_channel(WELCOME_CHANNEL_ID)
        except (discord.Forbidden, discord.HTTPException):
            self.logger.warning("Could not load the welcome channel")
            return None

        if isinstance(fetched, discord.TextChannel):
            return fetched
        return None

    def _build_view(
        self, member: discord.Member, description: str
    ) -> discord.ui.LayoutView:
        view = discord.ui.LayoutView(timeout=None)
        media_gallery = discord.ui.MediaGallery(
            discord.ui.MediaGalleryItem(url="attachment://avatar.png")
        )
        view.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(content=member.mention),
                media_gallery,
                discord.ui.TextDisplay(content="## 🎉 Welcome!"),
                discord.ui.TextDisplay(content=description),
                discord.ui.TextDisplay(content="We're glad you're here!"),
            )
        )
        return view

    @app_commands.describe(member="Člen, pro kterého se má vytvořit uvítání.")
    @app_commands.guild_only()
    async def send_welcome_preview(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ) -> None:
        selected_member = member or interaction.user
        if not isinstance(selected_member, discord.Member):
            if interaction.guild is None:
                await interaction.response.send_message(
                    "Tento příkaz lze použít jen na serveru.",
                    ephemeral=True,
                )
                return
            selected_member = await interaction.guild.fetch_member(
                selected_member.id
            )

        avatar_bytes = await selected_member.display_avatar.with_size(256).read()
        file = discord.File(io.BytesIO(avatar_bytes), filename="avatar.png")
        await interaction.response.send_message(
            files=[file],
            view=self._build_view(selected_member, WELCOME_TEXT),
        )

    @commands.Cog.listener()
    async def on_member_update(
        self, before: discord.Member, after: discord.Member
    ) -> None:
        if after.bot:
            return

        added_role_ids = {role.id for role in after.roles} - {
            role.id for role in before.roles
        }

        welcome_role_ids = {ROLE_WELCOME_CZ, ROLE_WELCOME_EN}
        if not (added_role_ids & welcome_role_ids):
            return

        channel = await self._get_welcome_channel()
        if channel is None:
            return

        avatar_bytes = await after.display_avatar.with_size(256).read()
        file = discord.File(io.BytesIO(avatar_bytes), filename="avatar.png")
        await channel.send(
            files=[file],
            view=self._build_view(after, WELCOME_TEXT),
        )
