import inspect
import logging

import discord
from discord.ext import commands

WELCOME_CHANNEL_ID = 1440271167234510940
ROLE_WELCOME_CZ = 1444075970649915586
ROLE_WELCOME_EN = 1444075991118119024


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

    def _build_embed(self, member: discord.Member, language: str) -> discord.Embed:
        is_czech = language == "cs"
        title = "🎉 Vítej v roomce!" if is_czech else "🎉 Welcome to the room!"
        description = (
            f"Ahoj {member.mention}! Jsme rádi, že ses k nám přidal.\n"
            "Mrkni na užitečné odkazy níže a připoj se k diskuzi."
            if is_czech
            else (
                f"Hi {member.mention}! We're glad you've joined us.\n"
                "Check the useful links below and jump into the conversation."
            )
        )
        first_steps_title = "🧭 První kroky" if is_czech else "🧭 First steps"
        first_steps_value = (
            "• Přečti si pravidla serveru, ať víš, jak to u nás chodí.\n"
            "• Nastav si roli nebo pingni moderátory, když budeš potřebovat pomoc."
            if is_czech
            else (
                "• Read the server rules so you know how we roll.\n"
                "• Pick a role or ping the moderators if you need help."
            )
        )
        highlights_title = "✨ Co tě čeká" if is_czech else "✨ What's inside"
        highlights_value = (
            "• Eventy, soutěže a přátelská komunita.\n"
            "• Kanály pro hraní, chat i sdílení tipů."
            if is_czech
            else (
                "• Events, giveaways, and a friendly community.\n"
                "• Channels for gaming, chatting, and sharing tips."
            )
        )
        footer = (
            "Přejeme pohodový čas na serveru!"
            if is_czech
            else "Have a great time on the server!"
        )

        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name=first_steps_title, value=first_steps_value, inline=False)
        embed.add_field(name=highlights_title, value=highlights_value, inline=False)
        embed.set_footer(text=footer)
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
        needs_czech = ROLE_WELCOME_CZ in added_role_ids
        needs_english = ROLE_WELCOME_EN in added_role_ids

        if not (needs_czech or needs_english):
            return

        channel = await self._get_welcome_channel()
        if channel is None:
            return

        if needs_czech:
            await channel.send(
                content=after.mention, embed=self._build_embed(after, "cs")
            )

        if needs_english:
            await channel.send(
                content=after.mention, embed=self._build_embed(after, "en")
            )


async def setup(bot: commands.Bot):
    result = bot.add_cog(WelcomeCog(bot))
    if inspect.isawaitable(result):
        await result
