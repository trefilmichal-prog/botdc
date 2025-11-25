import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime

from config import (
    XP_PER_MESSAGE,
    COINS_PER_MESSAGE,
    XP_MESSAGE_MIN_CHARS,
    XP_COOLDOWN_SECONDS,
    XP_PER_LEVEL,
)
from db import get_or_create_user_stats, update_user_stats


class XpCog(commands.Cog, name="XpCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener("on_message")
    async def on_message_xp(self, message: discord.Message):
        if message.author.bot:
            return
        if len(message.content.strip()) < XP_MESSAGE_MIN_CHARS:
            return

        discord_id = message.author.id
        coins, exp, level, last_xp_at = get_or_create_user_stats(discord_id)

        now = datetime.utcnow()
        if last_xp_at:
            try:
                last_dt = datetime.strptime(last_xp_at, "%Y-%m-%d %H:%M:%S")
                if (now - last_dt).total_seconds() < XP_COOLDOWN_SECONDS:
                    return
            except ValueError:
                pass

        new_exp = exp + XP_PER_MESSAGE
        new_coins = coins + COINS_PER_MESSAGE
        lvl_from_exp = (new_exp // XP_PER_LEVEL) + 1
        new_level = max(level, lvl_from_exp)

        update_user_stats(
            discord_id,
            coins=new_coins,
            exp=new_exp,
            level=new_level,
            last_xp_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )

    @app_commands.command(name="profile", description="Ukáže coiny, exp a level hráče.")
    @app_commands.describe(user="Kterého uživatele zobrazit (prázdné = ty).")
    async def profile_cmd(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ):
        target = user or interaction.user
        coins, exp, level, _ = get_or_create_user_stats(target.id)

        embed = discord.Embed(
            title=f"Profil – {target.display_name}",
            color=0x00DD88,
        )
        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(name="Exp", value=str(exp), inline=True)
        embed.add_field(name="Coisy", value=str(coins), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(XpCog(bot))
