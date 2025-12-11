import inspect
import logging

import discord
from discord.ext import commands

WELCOME_CHANNEL_ID = 1440271167234510940


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

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        channel = await self._get_welcome_channel()
        if channel is None:
            return

        embed = discord.Embed(
            title="🎉 Vítej v roomce!",
            description=(
                f"Ahoj {member.mention}! Jsme rádi, že ses k nám přidal.\n"
                "Mrkni na užitečné odkazy níže a připoj se k diskuzi."
            ),
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name="🧭 První kroky",
            value=(
                "• Přečti si pravidla serveru, ať víš, jak to u nás chodí.\n"
                "• Nastav si roli nebo pingni moderátory, když budeš potřebovat pomoc."
            ),
            inline=False,
        )
        embed.add_field(
            name="✨ Co tě čeká",
            value=(
                "• Eventy, soutěže a přátelská komunita.\n"
                "• Kanály pro hraní, chat i sdílení tipů."
            ),
            inline=False,
        )
        embed.set_footer(text="Přejeme pohodový čas na serveru!")

        await channel.send(content=member.mention, embed=embed)


async def setup(bot: commands.Bot):
    result = bot.add_cog(WelcomeCog(bot))
    if inspect.isawaitable(result):
        await result
