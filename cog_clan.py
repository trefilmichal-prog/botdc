# clan_panel_components_v2_full.py
import discord
from discord.ext import commands

TICKET_CATEGORY_ID = 1440977431577235456

class Components(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

        container = discord.ui.Container(
            discord.ui.TextDisplay(content="## PŘIHLÁŠKY DO CLANU"),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),

            discord.ui.TextDisplay(
                content="### 🇺🇸 Podmínky přijetí\n```
- 2SP rebirthů +
- Hrát 24/7
- 30% index
- 10d playtime
```"
            ),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),

            discord.ui.TextDisplay(
                content="### 🇨🇿 Podmínky přijetí\n```
- 2SP rebirthů +
- Hrát 24/7
- 30% index
- 10d playtime
```"
            ),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),

            discord.ui.ActionRow(
                discord.ui.Select(
                    custom_id="clan_select",
                    placeholder="Vyber clan",
                    options=[
                        discord.SelectOption(label="HROT", value="HROT", description="🇨🇿 & 🇺🇸"),
                        discord.SelectOption(label="HR2T", value="HR2T", description="only 🇨🇿"),
                        discord.SelectOption(label="TGCM", value="TGCM", description="only 🇺🇸"),
                    ],
                ),
            ),
        )

        self.add_item(container)

    @discord.ui.select(custom_id="clan_select")
    async def select_callback(self, select: discord.ui.Select, interaction: discord.Interaction):
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
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
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
            f"{user.mention} otevřel ticket pro **{clan}**. Prosím pošli screeny a informace."
        )

        await interaction.response.send_message(
            f"Ticket vytvořen: {channel.mention}",
            ephemeral=True
        )


class ClanPanelCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.app_commands.command(name="clan_panel", description="Vytvoří panel pro přihlášky clanu")
    async def clan_panel(self, interaction: discord.Interaction):
        view = Components()
        await interaction.response.send_message("Panel vytvořen:", view=view, ephemeral=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(ClanPanelCog(bot))