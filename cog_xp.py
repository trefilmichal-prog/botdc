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
from i18n import get_interaction_locale, t


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
        coins, exp, level, last_xp_at, message_count = get_or_create_user_stats(discord_id)

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
            message_count=message_count + 1,
        )

    @app_commands.command(name="profile", description="Ukáže coiny, exp a level hráče.")
    @app_commands.describe(user="Kterého uživatele zobrazit (prázdné = ty).")
    async def profile_cmd(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ):
        locale = get_interaction_locale(interaction)
        target = user or interaction.user
        coins, exp, level, _, message_count = get_or_create_user_stats(target.id)

        level_exp_base = (level - 1) * XP_PER_LEVEL
        xp_into_level = max(0, exp - level_exp_base)
        xp_for_next = XP_PER_LEVEL
        xp_remaining = max(0, xp_for_next - xp_into_level)
        progress_fraction = min(1.0, xp_into_level / xp_for_next if xp_for_next else 0)
        filled_blocks = round(progress_fraction * 10)
        progress_bar = "▰" * filled_blocks + "▱" * (10 - filled_blocks)
        progress_percent = int(progress_fraction * 100)

        embed_color = target.color if target.color.value else 0x00DD88

        embed = discord.Embed(
            title=t("profile_title", locale, name=target.display_name),
            description=t("profile_subtitle", locale),
            color=embed_color,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(
            name=t("profile_progress", locale),
            value=(
                f"{progress_bar} **{progress_percent}%**\n"
                f"{xp_into_level:,}/{xp_for_next:,} XP ({t('profile_next_level', locale)}: {xp_remaining:,} XP)"
            ),
            inline=False,
        )
        embed.add_field(name=t("profile_level", locale), value=str(level), inline=True)
        embed.add_field(name=t("profile_exp", locale), value=f"{exp:,} XP", inline=True)
        embed.add_field(name=t("profile_economy", locale), value=f"💰 {coins:,}", inline=True)
        embed.add_field(
            name=t("profile_activity", locale),
            value=f"💬 {message_count:,} {t('profile_messages', locale).lower()}",
            inline=True,
        )
        embed.set_footer(text=t("profile_footer", locale))

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="give_coins",
        description="Přidá vybranému hráči zadaný počet coinů a pošle mu zprávu.",
    )
    @app_commands.describe(
        user="Uživatel, kterému přidat coiny.",
        amount="Počet coinů k připsání.",
    )
    async def give_coins(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        amount: app_commands.Range[int, 1, 10_000_000],
    ):
        required_role_id = 1_440_043_301_515_559_014
        if not any(role.id == required_role_id for role in interaction.user.roles):
            await interaction.response.send_message(
                "❌ Tento příkaz může použít pouze oprávněná role.", ephemeral=True
            )
            return

        coins, _exp, _level, _last_xp_at, _messages = get_or_create_user_stats(user.id)
        new_coins = coins + int(amount)
        update_user_stats(user.id, coins=new_coins)

        dm_sent = False
        try:
            await user.send(
                f"Ahoj! Na serveru **{interaction.guild.name}** jsi právě obdržel **{amount} coinů**."
            )
            dm_sent = True
        except Exception:
            dm_sent = False

        dm_status = "📩 DM odeslána." if dm_sent else "⚠️ DM se nepodařilo odeslat."
        await interaction.response.send_message(
            (
                f"✅ Přidal jsi **{amount} coinů** uživateli {user.mention}.\n"
                f"Aktuální zůstatek: **{new_coins} coinů**. {dm_status}"
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(XpCog(bot))
