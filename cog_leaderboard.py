import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import CLAN_MEMBER_ROLE_ID
from db import add_clan_panel, get_all_clan_panels, get_top_users_by_stat, remove_clan_panel


class LeaderboardCog(commands.Cog, name="Leaderboard"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.clan_panel_refresh_loop.start()

    def cog_unload(self):
        self.clan_panel_refresh_loop.cancel()

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

        embed = self.build_clan_panel_embed(role)

        message = await channel.send(embed=embed)
        add_clan_panel(channel.guild.id, channel.id, message.id)

        await interaction.response.send_message(
            f"Zpráva s přehledem členů byla odeslána do {channel.mention}.",
            ephemeral=True,
        )

    def build_clan_panel_embed(self, role: discord.Role) -> discord.Embed:
        members = sorted(role.members, key=lambda m: m.display_name.lower())
        if members:
            member_lines = [member.mention for member in members]
            description = "\n".join(member_lines)
        else:
            description = "Zatím nikdo nemá tuto roli."

        color = role.color if role.color.value else 0x2ECC71
        embed = discord.Embed(
            title="Členové klanu",
            description=description,
            color=color,
        )
        embed.set_footer(text="Panel se aktualizuje automaticky každých 5 minut.")
        return embed

    async def refresh_clan_panels(self):
        panels = get_all_clan_panels()
        if not panels:
            return

        embed_cache: dict[int, discord.Embed] = {}

        for guild_id, channel_id, message_id in panels:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue

            role = guild.get_role(CLAN_MEMBER_ROLE_ID) if CLAN_MEMBER_ROLE_ID else None
            if role is None:
                embed = discord.Embed(
                    title="Členové klanu",
                    description=(
                        "Roli pro klan jsem na serveru nenašel. "
                        "Zkontroluj hodnotu CLAN_MEMBER_ROLE_ID."
                    ),
                    color=0xE74C3C,
                )
            else:
                if guild_id not in embed_cache:
                    embed_cache[guild_id] = self.build_clan_panel_embed(role)
                embed = embed_cache[guild_id]

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
                await msg.edit(embed=embed)
            except discord.HTTPException:
                continue

    @tasks.loop(minutes=5)
    async def clan_panel_refresh_loop(self):
        await self.refresh_clan_panels()

    @clan_panel_refresh_loop.before_loop
    async def before_clan_panel_refresh_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(LeaderboardCog(bot))
