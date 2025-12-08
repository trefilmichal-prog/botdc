import discord
from discord.ext import commands
from discord import app_commands

TICKET_CATEGORY_ID = 1440977431577235456


class Components(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

        container = discord.ui.Container(
            discord.ui.TextDisplay(content="## PŘIHLÁŠKY DO CLANU"),

            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),

            discord.ui.TextDisplay(
                content="### 🇺🇸 Podmínky přijetí\n```\n- 2SP rebirths +\n- Play 24/7\n- 30% index\n- 10d playtime\n```"
            ),

            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),

            discord.ui.TextDisplay(
                content="### 🇨🇿 Podmínky přijetí\n```\n- 2SP rebirthů +\n- Hrát 24/7\n- 30% index\n- 10d playtime\n```"
            ),

            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),

            discord.ui.ActionRow(
                discord.ui.Select(
                    custom_id="clan_select",
                    placeholder="Vyber clan",
                    options=[
                        discord.SelectOption(label="HROT", value="HROT", description="🇨🇿 & 🇺🇸"),
                        discord.SelectOption(label="HR2T", value="HR2T", description="🇨🇿 only"),
                        discord.SelectOption(label="TGCM", value="TGCM", description="🇺🇸 only"),
                    ],
                )
            ),
        )

        self.add_item(container)


class ClanPanelCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="clan_panel", description="Zobrazí panel pro přihlášky do clanu")
    async def clan_panel(self, interaction: discord.Interaction):
        view = Components()
        await interaction.response.send_message(
            content="",
            view=view,
            ephemeral=False
        )

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):

        if interaction.type == discord.InteractionType.component and interaction.data.get("custom_id") == "clan_select":
            clan_value = interaction.data.get("values")[0]
            guild = interaction.guild

            category = guild.get_channel(TICKET_CATEGORY_ID)
            if category is None:
                await interaction.response.send_message(
                    "Kategorie neexistuje nebo nemám práva.",
                    ephemeral=True
                )
                return

            channel_name = f"ticket-{interaction.user.name}-{clan_value}".lower()

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            }

            admin_role = discord.utils.get(guild.roles, name="Admin")
            if admin_role:
                overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                category=category,
                reason=f"Clan ticket: {clan_value}"
            )

            await interaction.response.send_message(
                f"Ticket vytvořen: {ticket_channel.mention}",
                ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(ClanPanelCog(bot))