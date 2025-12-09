import discord
from discord.ext import commands
from discord import app_commands

# Category where ticket channels will be created
TICKET_CATEGORY_ID = 1440977431577235456

# Role name that should have access to all tickets (optional)
ADMIN_ROLE_NAME = "Admin"


class Components(discord.ui.LayoutView):
    """Main public panel with clan selection."""
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


def _apply_custom_id(channel_id: int, clan_value: str) -> str:
    # Keep short to stay under Discord custom_id limits.
    return f"clan_apply|{channel_id}|{clan_value}"


def _finalize_custom_id(channel_id: int) -> str:
    return f"clan_finalize|{channel_id}"


class TicketStartView(discord.ui.LayoutView):
    """Panel inside the ticket channel to start filling the application."""
    def __init__(self, ticket_channel_id: int, clan_value: str):
        super().__init__(timeout=None)

        container = discord.ui.Container(
            discord.ui.TextDisplay(content=f"## ✅ Ticket pro clan: **{clan_value}**"),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                content=(
                    "### Co vyplnit\n"
                    "• **Roblox Display Name**\n"
                    "• **Kolik máš rebirthů** (text)\n"
                    "• **Kolik hodin denně můžeš hrát** (text)\n"
                )
            ),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                content=(
                    "### Screeny (může být více)\n"
                    "♻️ Screeny Petů\n"
                    "♻️ Tvoje Gamepassy (pokud vlastníš)\n"
                    "♻️ Tvoje Rebirthy\n"
                    "♻️ Tvojí Prestige\n\n"
                    "Screeny pošli **jako přílohy** sem do ticketu (klidně více zpráv)."
                )
            ),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.ActionRow(
                discord.ui.Button(
                    custom_id=_apply_custom_id(ticket_channel_id, clan_value),
                    label="Vyplnit přihlášku",
                    style=discord.ButtonStyle.primary,
                )
            ),
        )

        self.add_item(container)


class TicketFinalizeView(discord.ui.LayoutView):
    """Panel to confirm that all screenshots were uploaded."""
    def __init__(self, ticket_channel_id: int):
        super().__init__(timeout=None)

        container = discord.ui.Container(
            discord.ui.TextDisplay(content="## 📎 Screeny"),
            discord.ui.TextDisplay(
                content="Až pošleš všechny screeny jako přílohy do ticketu, klikni na **Hotovo**."
            ),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.ActionRow(
                discord.ui.Button(
                    custom_id=_finalize_custom_id(ticket_channel_id),
                    label="Hotovo",
                    style=discord.ButtonStyle.success,
                )
            ),
        )

        self.add_item(container)


class ClanApplicationModal(discord.ui.Modal):
    """Modal for application input (text only). Screenshots are sent as attachments in the ticket channel."""
    def __init__(self, ticket_channel_id: int, clan_value: str):
        super().__init__(title="Přihláška do clanu")
        self.ticket_channel_id = int(ticket_channel_id)
        self.clan_value = str(clan_value)

        self.display_name = discord.ui.TextInput(
            label="Roblox Display Name",
            placeholder="Např. senpaicat",
            required=True,
            max_length=50,
        )
        self.rebirths = discord.ui.TextInput(
            label="Kolik máš rebirthů (text)",
            placeholder="Např. 2SP / 150k / ...",
            required=True,
            max_length=120,
        )
        self.hours_per_day = discord.ui.TextInput(
            label="Kolik hodin denně můžeš hrát (text)",
            placeholder="Např. 6-10h, 2h, 24/7 ...",
            required=True,
            max_length=120,
        )

        self.add_item(self.display_name)
        self.add_item(self.rebirths)
        self.add_item(self.hours_per_day)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Tahle akce musí běžet na serveru.", ephemeral=True)
            return

        ticket_channel = guild.get_channel(self.ticket_channel_id)
        if ticket_channel is None or not isinstance(ticket_channel, discord.TextChannel):
            await interaction.response.send_message("Ticket kanál neexistuje.", ephemeral=True)
            return

        # Post application summary into ticket channel (Components V2 panel).
        summary_view = discord.ui.LayoutView(timeout=None)
        summary_container = discord.ui.Container(
            discord.ui.TextDisplay(content="## 📄 Přihláška"),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(content=f"**Clan:** {self.clan_value}"),
            discord.ui.TextDisplay(content=f"**Uživatel:** {interaction.user.mention}"),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(content=f"**Roblox Display Name:** `{self.display_name.value}`"),
            discord.ui.TextDisplay(content=f"**Rebirthy:** `{self.rebirths.value}`"),
            discord.ui.TextDisplay(content=f"**Hodiny denně:** `{self.hours_per_day.value}`"),
        )
        summary_view.add_item(summary_container)

        await ticket_channel.send(content="", view=summary_view)

        # Ask for screenshots + provide finalize button.
        await ticket_channel.send(content="", view=TicketFinalizeView(ticket_channel.id))

        await interaction.response.send_message("✅ Přihláška byla odeslána do ticketu. Teď pošli screeny jako přílohy.", ephemeral=True)


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
        # Handle select (create ticket)
        if interaction.type == discord.InteractionType.component and interaction.data.get("custom_id") == "clan_select":
            clan_value = interaction.data.get("values")[0]
            guild = interaction.guild

            if guild is None:
                await interaction.response.send_message("Tahle akce musí běžet na serveru.", ephemeral=True)
                return

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
                interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
            }

            admin_role = discord.utils.get(guild.roles, name=ADMIN_ROLE_NAME)
            if admin_role:
                overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                category=category,
                reason=f"Clan ticket: {clan_value}"
            )

            # Post ticket starter panel inside the ticket channel
            await ticket_channel.send(content="", view=TicketStartView(ticket_channel.id, clan_value))

            await interaction.response.send_message(
                f"Ticket vytvořen: {ticket_channel.mention}",
                ephemeral=True
            )
            return

        # Handle "Vyplnit přihlášku" button -> open modal
        if interaction.type == discord.InteractionType.component and isinstance(interaction.data, dict):
            custom_id = interaction.data.get("custom_id", "")
            if isinstance(custom_id, str) and custom_id.startswith("clan_apply|"):
                parts = custom_id.split("|", 2)
                if len(parts) != 3:
                    await interaction.response.send_message("Neplatný button.", ephemeral=True)
                    return

                _, channel_id_str, clan_value = parts
                try:
                    channel_id = int(channel_id_str)
                except ValueError:
                    await interaction.response.send_message("Neplatný ticket.", ephemeral=True)
                    return

                modal = ClanApplicationModal(ticket_channel_id=channel_id, clan_value=clan_value)
                await interaction.response.send_modal(modal)
                return

            # Handle finalize button
            if isinstance(custom_id, str) and custom_id.startswith("clan_finalize|"):
                parts = custom_id.split("|", 1)
                if len(parts) != 2:
                    await interaction.response.send_message("Neplatný button.", ephemeral=True)
                    return

                try:
                    channel_id = int(parts[1])
                except ValueError:
                    await interaction.response.send_message("Neplatný ticket.", ephemeral=True)
                    return

                if interaction.guild is None:
                    await interaction.response.send_message("Tahle akce musí běžet na serveru.", ephemeral=True)
                    return

                ticket_channel = interaction.guild.get_channel(channel_id)
                if ticket_channel is None or not isinstance(ticket_channel, discord.TextChannel):
                    await interaction.response.send_message("Ticket kanál neexistuje.", ephemeral=True)
                    return

                # Notify in channel + acknowledge user
                await ticket_channel.send(f"✅ {interaction.user.mention} označil/a přihlášku jako hotovou (screeny jsou nahrané).")
                await interaction.response.send_message("✅ Označeno jako hotovo.", ephemeral=True)
                return


async def setup(bot: commands.Bot):
    await bot.add_cog(ClanPanelCog(bot))
