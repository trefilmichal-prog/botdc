import discord
from discord.ext import commands
from discord import app_commands


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


async def setup(bot: commands.Bot):
    await bot.add_cog(ClanPanelCog(bot))
