import discord
from discord import app_commands
from discord.ext import commands

from config import CLAN_MEMBER_ROLE_ID
from db import get_top_users_by_stat


class LeaderboardCog(commands.Cog, name="Leaderboard"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="leaderboard", description="Ukáže žebříček podle coinů nebo počtu zpráv."
    )
    @app_commands.choices(
        metric=[
            app_commands.Choice(name="Coiny", value="coins"),
            app_commands.Choice(name="Zprávy", value="message_count"),
        ]
    )
    async def leaderboard_cmd(
        self, interaction: discord.Interaction, metric: app_commands.Choice[str]
    ):
        top_users = get_top_users_by_stat(metric.value, limit=10)
        if not top_users:
            await interaction.response.send_message(
                "Nikdo zatím nemá žádná data pro tento žebříček.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"Žebříček – {'Coiny' if metric.value == 'coins' else 'Zprávy'}",
            color=0x3498DB,
        )

        lines = []
        for idx, (user_id, value) in enumerate(top_users, start=1):
            mention = f"<@{user_id}>"
            lines.append(f"**{idx}.** {mention} – {value}")

        embed.description = "\n".join(lines)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="setup_clan_room",
        description="Odešle do vybraného kanálu přehled členů s klanovou rolí.",
    )
    @app_commands.describe(channel="Kanál, kam se má zpráva poslat.")
    @app_commands.default_permissions(manage_channels=True)
    async def setup_clan_room(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        role = interaction.guild.get_role(CLAN_MEMBER_ROLE_ID) if interaction.guild else None
        if role is None:
            await interaction.response.send_message(
                f"Roli s ID `{CLAN_MEMBER_ROLE_ID}` jsem na tomto serveru nenašel.",
                ephemeral=True,
            )
            return

        members = sorted(role.members, key=lambda m: m.display_name.lower())
        if members:
            member_lines = [member.mention for member in members]
            description = "\n".join(member_lines)
        else:
            description = "Zatím nikdo nemá tuto roli."

        embed = discord.Embed(
            title="Členové klanu",
            description=description,
            color=role.color if role.color.value else 0x2ECC71,
        )

        await channel.send(embed=embed)
        await interaction.response.send_message(
            f"Zpráva s přehledem členů byla odeslána do {channel.mention}.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(LeaderboardCog(bot))
