import discord
from discord.ext import commands
from discord import app_commands

TICKET_CATEGORY_ID = 1440977431577235456  # kategorie pro tickety

# Pokud máš admin roli co má vidět tickety, dopiš sem
ADMIN_ROLE_ID = None  # nebo např. 123456789


class ClanSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Hrot 🇨🇿"),
            discord.SelectOption(label="HR2T"),
            discord.SelectOption(label="TGMC"),
        ]

        super().__init__(
            placeholder="Vyber clan",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):

        guild = interaction.guild
        user = interaction.user
        clan = self.values[0]

        category = guild.get_channel(TICKET_CATEGORY_ID)

        if not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(
                "Kategorie ticketů není správně nastavená.",
                ephemeral=True
            )
            return

        # Připravení oprávnění
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )
        }

        if ADMIN_ROLE_ID:
            admin_role = guild.get_role(ADMIN_ROLE_ID)
            if admin_role:
                overwrites[admin_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True
                )

        # Název ticketu
        safe_name = user.name.lower().replace(" ", "-")
        ticket_name = f"🟠přihlášky-{clan}-{safe_name}"

        # Vytvoření ticket kanálu
        channel = await guild.create_text_channel(
            name=ticket_name[:90],
            category=category,
            overwrites=overwrites,
            reason=f"Přihláška do clanu {clan}"
        )

        # Úvodní zpráva v ticketu
        await channel.send(
            f"{user.mention}\n"
            f"Otevřel jsi přihlášku do **{clan}**.\n"
            f"Prosím pošli screeny a informace podle podmínek."
        )

        await interaction.response.send_message(
            f"Ticket vytvořen: {channel.mention}",
            ephemeral=True
        )


class ClanView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ClanSelect())


class ClanPanelCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="clan_panel",
        description="Vytvoří panel pro přihlášky clanu"
    )
    async def clan_panel(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="PŘIHLÁŠKY DO CLANU",
            color=0x2F3136
        )

        embed.add_field(
            name="🇺🇸 Podmínky přijetí",
            value=(
                "```\n"
                "- 2SP rebirths +\n"
                "- Play 24/7\n"
                "- 30% index\n"
                "- 10d playtime\n"
                "```"
            ),
            inline=False
        )

        embed.add_field(
            name="🇨🇿 Podmínky přijetí",
            value=(
                "```\n"
                "- 2SP rebirthů +\n"
                "- Hrát 24/7\n"
                "- 30% index\n"
                "- 10d playtime\n"
                "```"
            ),
            inline=False
        )

        view = ClanView()
        await interaction.response.send_message(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(ClanPanelCog(bot))
