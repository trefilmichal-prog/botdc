# clan_panel_v2_single_fixed.py

import discord
from discord.ext import commands
from discord import app_commands

TICKET_CATEGORY_ID = 1440977431577235456

class ClanTicketView(discord.ui.View):
    @discord.ui.select(
        custom_id="clan_select",
        placeholder="Vyber clan",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label="HROT"),
            discord.SelectOption(label="HR2T"),
            discord.SelectOption(label="TGMC"),
        ]
    )
    async def select_callback(
        self,
        select: discord.ui.Select,
        interaction: discord.Interaction
    ):
        guild = interaction.guild
        user = interaction.user
        clan = select.values[0]

        category = guild.get_channel(TICKET_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(
                "Kategorie ticketů není správně nastavena.",
                ephemeral=True
            )
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )
        }

        safe_name = user.name.lower().replace(" ", "-")
        ticket_name = f"🟠přihlášky-{clan}-{safe_name}"[:90]

        channel = await guild.create_text_channel(
            name=ticket_name,
            category=category,
            overwrites=overwrites,
            reason=f"Přihláška do clanu {clan}"
        )

        await channel.send(
            f"{user.mention} otevřel ticket pro **{clan}**. "
            f"Prosím pošli screeny a informace podle podmínek."
        )

        await interaction.response.send_message(
            f"Ticket vytvořen: {channel.mention}",
            ephemeral=True
        )

class ClanPanelCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="clan_panel", description="Vytvoří panel pro přihlášky clanu")
    async def clan_panel(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="PŘIHLÁŠKY DO CLANU",
            color=0x2F3136
        )

        embed.add_field(
            name="🇺🇸 Podmínky přijetí",
            value="""```
- 2SP rebirths +
- Play 24/7
- 30% index
- 10d playtime
```""",
            inline=False
        )

        embed.add_field(
            name="🇨🇿 Podmínky přijetí",
            value="""```
- 2SP rebirthů +
- Hrát 24/7
- 30% index
- 10d playtime
```""",
            inline=False
        )

        view = ClanTicketView()
        await interaction.response.send_message(embed=embed, view=view)

async def setup(bot: commands.Bot):
    await bot.add_cog(ClanPanelCog(bot))
