import inspect
import logging

import discord
from discord.ext import commands

WELCOME_CHANNEL_ID = 1440271167234510940
ROLE_WELCOME_CZ = 1444075970649915586
ROLE_WELCOME_EN = 1444075991118119024
WELCOME_TEXT_EN = "Welcome in the Clan Server HROT"
WELCOME_TEXT_CZ = "Vítej na Clan Server HROT"


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
            self.logger.warning("Nepodařilo se načíst uvítací kanál")
            return None

        if isinstance(fetched, discord.TextChannel):
            return fetched
        return None

    def _build_embed(self, member: discord.Member, description: str) -> discord.Embed:
        embed = discord.Embed(
            title="🎉 Welcome!",
            description=description,
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Jsme rádi, že jsi tady!")
        return embed

    @commands.Cog.listener()
    async def on_member_update(
        self, before: discord.Member, after: discord.Member
    ) -> None:
        if after.bot:
            return

        added_role_ids = {role.id for role in after.roles} - {
            role.id for role in before.roles
        }

        welcome_text = None
        for role_id, text in (
            (ROLE_WELCOME_CZ, WELCOME_TEXT_CZ),
            (ROLE_WELCOME_EN, WELCOME_TEXT_EN),
        ):
            if role_id in added_role_ids:
                welcome_text = text
                break

        if welcome_text is None:
            return

        channel = await self._get_welcome_channel()
        if channel is None:
            return

        await channel.send(
            content=after.mention, embed=self._build_embed(after, welcome_text)
        )


async def setup(bot: commands.Bot):
    result = bot.add_cog(WelcomeCog(bot))
    if inspect.isawaitable(result):
        await result
