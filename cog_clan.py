import re
import discord
from discord.ext import commands
from discord import app_commands

# Category where ticket channels will be created
TICKET_CATEGORY_ID = 1440977431577235456

# Role name that should have access to all tickets (optional)
ADMIN_ROLE_NAME = "Admin"


def _sanitize_nickname(value: str) -> str:
    """Discord nickname max length is 32."""
    value = (value or "").strip()
    if not value:
        return ""
    return value[:32]


def _slugify_channel_part(value: str) -> str:
    """Return a safe channel-name fragment for the 'name' part."""
    value = (value or "").strip().lower()

    # Replace whitespace with hyphens
    value = re.sub(r"\s+", "-", value)

    # Replace common separators
    value = value.replace("_", "-").replace("/", "-").replace("\\", "-")

    # Keep only a-z, 0-9 and hyphen for stability
    value = re.sub(r"[^a-z0-9\-]", "", value)
    value = re.sub(r"\-+", "-", value).strip("-")

    return value or "applicant"


def _apply_custom_id(channel_id: int, clan_value: str) -> str:
    return f"clan_apply|{channel_id}|{clan_value}"


def _finalize_custom_id(channel_id: int) -> str:
    return f"clan_finalize|{channel_id}"


class Components(discord.ui.LayoutView):
    """Main public panel with clan selection."""

    def __init__(self):
        super().__init__(timeout=None)

        container = discord.ui.Container(
            discord.ui.TextDisplay(content="## PŘIHLÁŠKY DO CLANU"),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                content=(
                    "### 🇺🇸 Podmínky přijetí\n"
                    "```\n"
                    "- 2SP rebirths +\n"
                    "- Play 24/7\n"
                    "- 30% index\n"
                    "- 10d playtime\n"
                    "```"
                )
            ),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                content=(
                    "### 🇨🇿 Podmínky přijetí\n"
                    "```\n"
                    "- 2SP rebirthů +\n"
                    "- Hrát 24/7\n"
                    "- 30% index\n"
                    "- 10d playtime\n"
                    "```"
                )
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
                    "• **Kolik máš rebirthů**\n"
                    "• **Kolik hodin denně můžeš hrát**\n"
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
            discord.ui.TextDisplay(content="Až pošleš všechny screeny jako přílohy do ticketu, klikni na **Hotovo**."),
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
    """Modal for application input (text only)."""

    def __init__(self, ticket_channel_id: int, clan_value: str):
        super().__init__(title="Přihláška do clanu")
        self.ticket_channel_id = int(ticket_channel_id)
        self.clan_value = str(clan_value)

        self.display_name = discord.ui.TextInput(
            label="Roblox Display Name",
            placeholder="Např. senpaicat22",
            required=True,
            max_length=32,
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

        roblox_display = (self.display_name.value or "").strip()
        roblox_display_nick = _sanitize_nickname(roblox_display)

        # 1) Set user's nickname on the server to the Roblox Display Name.
        nick_ok = False
        nick_err = None
        nick_diag = []

        try:
            member = guild.get_member(interaction.user.id)
            if member is None:
                member = await guild.fetch_member(interaction.user.id)

            bot_member = guild.me
            if bot_member is None:
                bot_member = await guild.fetch_member(interaction.client.user.id)

            if member == guild.owner:
                nick_diag.append("Nelze měnit přezdívku **majiteli serveru**.")
            if not (bot_member.guild_permissions.manage_nicknames or bot_member.guild_permissions.administrator):
                nick_diag.append("Bot nemá oprávnění **Manage Nicknames** (nebo **Administrator**).")
            if bot_member.top_role <= member.top_role and member != guild.owner:
                nick_diag.append("Role bota je **níž nebo stejně** jako role uživatele (hierarchie rolí).")

            await member.edit(
                nick=roblox_display_nick,
                reason="Clan application: set nickname to Roblox Display Name",
            )
            nick_ok = True

        except discord.Forbidden:
            nick_err = "Discord odmítl změnu přezdívky (oprávnění/hierarchie rolí)."
        except discord.HTTPException as e:
            nick_err = f"Discord API chyba při změně přezdívky: {e}"
        except discord.NotFound:
            nick_err = "Uživatel nebyl nalezen (NotFound)."

        # 2) Rename ticket channel to: 🟠přihlášky-clan-jmeno-ve-hre
        chan_ok = False
        chan_err = None
        try:
            slug = _slugify_channel_part(roblox_display)
            new_name = f"🟠přihlášky-{self.clan_value}-{slug}"

            if len(new_name) > 100:
                new_name = new_name[:100].rstrip("-")
                if not new_name:
                    new_name = "🟠přihlášky"

            await ticket_channel.edit(
                name=new_name,
                reason="Clan application: rename ticket channel to Roblox Display Name",
            )
            chan_ok = True
        except discord.Forbidden:
            chan_err = "Nemám práva na přejmenování kanálu (Manage Channels)."
        except discord.HTTPException as e:
            chan_err = f"Discord API chyba při přejmenování kanálu: {e}"

        # Post application summary into ticket channel (Components V2 panel).
        summary_view = discord.ui.LayoutView(timeout=None)
        summary_container = discord.ui.Container(
            discord.ui.TextDisplay(content="## 📄 Přihláška"),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(content=f"**Clan:** {self.clan_value}"),
            discord.ui.TextDisplay(content=f"**Uživatel:** {interaction.user.mention}"),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(content=f"**Roblox Display Name:** `{roblox_display}`"),
            discord.ui.TextDisplay(content=f"**Rebirthy:** `{self.rebirths.value}`"),
            discord.ui.TextDisplay(content=f"**Hodiny denně:** `{self.hours_per_day.value}`"),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                content=(
                    "### ✅ Automatické nastavení\n"
                    f"• Přezdívka na serveru: **{'OK' if nick_ok else 'NE'}**\n"
                    f"• Přejmenování ticketu: **{'OK' if chan_ok else 'NE'}**"
                )
            ),
        )
        summary_view.add_item(summary_container)
        await ticket_channel.send(content="", view=summary_view)

        # If something failed, print reason(s) into the ticket.
        if (not nick_ok) or (not chan_ok):
            warn_view = discord.ui.LayoutView(timeout=None)

            warn_items = [
                discord.ui.TextDisplay(content="## ⚠️ Poznámka pro adminy"),
                discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            ]

            if not nick_ok:
                if nick_err:
                    warn_items.append(discord.ui.TextDisplay(content=f"**Nick změna:** {nick_err}"))
                if nick_diag:
                    diag_lines = "\n".join([f"• {x}" for x in nick_diag])
                    warn_items.append(discord.ui.TextDisplay(content=f"**Diagnostika:**\n{diag_lines}"))

            if not chan_ok and chan_err:
                warn_items.append(discord.ui.TextDisplay(content=f"**Kanál rename:** {chan_err}"))

            warn_container = discord.ui.Container(*warn_items)
            warn_view.add_item(warn_container)
            await ticket_channel.send(content="", view=warn_view)

        # Ask for screenshots + provide finalize button.
        await ticket_channel.send(content="", view=TicketFinalizeView(ticket_channel.id))

        await interaction.response.send_message(
            "✅ Přihláška byla odeslána do ticketu. Teď pošli screeny jako přílohy.",
            ephemeral=True,
        )


class ClanPanelCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="clan_panel", description="Zobrazí panel pro přihlášky do clanu")
    async def clan_panel(self, interaction: discord.Interaction):
        await interaction.response.send_message(content="", view=Components(), ephemeral=False)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return

        data = interaction.data or {}
        custom_id = data.get("custom_id", "")

        # 1) Handle select -> create ticket channel
        if custom_id == "clan_select":
            values = data.get("values") or []
            if not values:
                await interaction.response.send_message("Nebyla vybrána žádná možnost.", ephemeral=True)
                return

            clan_value = values[0]
            guild = interaction.guild
            if guild is None:
                await interaction.response.send_message("Tahle akce musí běžet na serveru.", ephemeral=True)
                return

            category = guild.get_channel(TICKET_CATEGORY_ID)
            if category is None or not isinstance(category, discord.CategoryChannel):
                await interaction.response.send_message("Kategorie neexistuje nebo nemám práva.", ephemeral=True)
                return

            # Create initial channel name. Final name is applied after modal submit.
            channel_name = f"🟠přihlášky-{clan_value}-{_slugify_channel_part(interaction.user.name)}"

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
                reason=f"Clan ticket: {clan_value}",
            )

            await ticket_channel.send(content="", view=TicketStartView(ticket_channel.id, clan_value))
            await interaction.response.send_message(f"Ticket vytvořen: {ticket_channel.mention}", ephemeral=True)
            return

        # 2) Handle "Vyplnit přihlášku" button -> open modal
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

            await interaction.response.send_modal(ClanApplicationModal(ticket_channel_id=channel_id, clan_value=clan_value))
            return

        # 3) Handle finalize button
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

            guild = interaction.guild
            if guild is None:
                await interaction.response.send_message("Tahle akce musí běžet na serveru.", ephemeral=True)
                return

            ticket_channel = guild.get_channel(channel_id)
            if ticket_channel is None or not isinstance(ticket_channel, discord.TextChannel):
                await interaction.response.send_message("Ticket kanál neexistuje.", ephemeral=True)
                return

            await ticket_channel.send(f"✅ {interaction.user.mention} označil/a přihlášku jako hotovou (screeny jsou nahrané).")
            await interaction.response.send_message("✅ Označeno jako hotovo.", ephemeral=True)
            return


async def setup(bot: commands.Bot):
    await bot.add_cog(ClanPanelCog(bot))
