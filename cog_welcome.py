import logging

import discord
from discord.ext import commands

WELCOME_CHANNEL_ID = 1440271167234510940
ROLE_WELCOME_CZ = 1444075970649915586
ROLE_WELCOME_EN = 1444075991118119024
WELCOME_TEXT = "Welcome in the Clan Server HROT"


class WelcomeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("botdc")

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
        view.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(content=member.mention),
                discord.ui.TextDisplay(content="## 🎉 Welcome!"),
                discord.ui.TextDisplay(content=description),
                discord.ui.TextDisplay(
                    content=f"Avatar: {member.display_avatar.url}"
                ),
                discord.ui.TextDisplay(content="We're glad you're here!"),
            )
        )
        return view

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

        await channel.send(view=self._build_view(after, WELCOME_TEXT))
