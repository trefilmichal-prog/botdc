import re
import discord
from discord.ext import commands
from discord import app_commands

# Category where NEW ticket channels will be created (initial intake)
TICKET_CATEGORY_ID = 1440977431577235456

# Optional admin role that should see all tickets
ADMIN_ROLE_NAME = "Admin"

# Clan -> "review" role id (leaders/officers) that can accept/deny and should see the ticket
CLAN_REVIEW_ROLE_IDS = {
    "hrot": 1440268371152339065,
    "hr2t": 1444304987986595923,
    "tgcm": 1447423174974247102,
}

# Clan -> role id that will be ASSIGNED to the applicant on accept
CLAN_MEMBER_ROLE_IDS = {
    "hrot": 1440268327892025438,
    "hr2t": 1444306127687778405,
    "tgcm": 1447423249817403402,
}

# Clan -> category id where the ticket should be MOVED after application is filled / ready for review
CLAN_CATEGORY_IDS = {
    "hrot": 1443684694968373421,
    "hr2t": 1444304658142335217,
    "tgcm": 1447423401462333480,
}


def _sanitize_nickname(value: str) -> str:
    """Discord nickname max length is 32."""
    value = (value or "").strip()
    if not value:
        return ""
    return value[:32]


def _slugify_channel_part(value: str) -> str:
    """Return a safe channel-name fragment (lowercase a-z0-9-)."""
    value = (value or "").strip().lower()
    value = re.sub(r"\s+", "-", value)
    value = value.replace("_", "-").replace("/", "-").replace("\\", "-")
    value = re.sub(r"[^a-z0-9\-]", "", value)
    value = re.sub(r"\-+", "-", value).strip("-")
    return value or "applicant"


def _apply_custom_id(channel_id: int, clan_value: str) -> str:
    return f"clan_apply|{channel_id}|{clan_value}"


def _finalize_custom_id(channel_id: int, clan_value: str) -> str:
    return f"clan_finalize|{channel_id}|{clan_value}"


def _review_custom_id(action: str, channel_id: int, clan_value: str) -> str:
    # action: accept / deny
    return f"clan_review|{action}|{channel_id}|{clan_value}"


def _review_role_id_for_clan(clan_value: str):
    return CLAN_REVIEW_ROLE_IDS.get((clan_value or "").strip().lower())


def _member_role_id_for_clan(clan_value: str):
    return CLAN_MEMBER_ROLE_IDS.get((clan_value or "").strip().lower())


def _category_id_for_clan(clan_value: str):
    return CLAN_CATEGORY_IDS.get((clan_value or "").strip().lower())


def _role_mention_for_clan(clan_value: str) -> str:
    rid = _review_role_id_for_clan(clan_value)
    return f"<@&{rid}>" if rid else ""


def _parse_ticket_topic(topic: str):
    """Parse channel.topic for applicant and clan. Returns (applicant_id:int|None, clan:str|None)."""
    if not topic:
        return None, None
    m1 = re.search(r"clan_applicant=(\d+)", topic)
    m2 = re.search(r"clan=([A-Za-z0-9_\-]+)", topic)
    applicant_id = int(m1.group(1)) if m1 else None
    clan = m2.group(1) if m2 else None
    return applicant_id, clan


def _is_reviewer(member: discord.Member, clan_value: str) -> bool:
    """Reviewer = Admin role OR clan review role OR administrator perms."""
    if member.guild_permissions.administrator:
        return True

    admin_role = discord.utils.get(member.guild.roles, name=ADMIN_ROLE_NAME)
    if admin_role and admin_role in member.roles:
        return True

    rid = _review_role_id_for_clan(clan_value)
    if rid:
        role = member.guild.get_role(rid)
        if role and role in member.roles:
            return True

    return False


async def _ensure_review_role_can_view(channel: discord.TextChannel, clan_value: str) -> bool:
    """Ensure the clan review role has visibility to the ticket channel."""
    rid = _review_role_id_for_clan(clan_value)
    if not rid:
        return False

    role = channel.guild.get_role(rid)
    if role is None:
        return False

    await channel.set_permissions(
        role,
        overwrite=discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
        ),
        reason="Clan ticket: ensure review role visibility",
    )
    return True


async def _ensure_ticket_in_clan_category(channel: discord.TextChannel, clan_value: str) -> bool:
    """Move the ticket to the configured clan category."""
    cid = _category_id_for_clan(clan_value)
    if not cid:
        return False

    category = channel.guild.get_channel(cid)
    if category is None or not isinstance(category, discord.CategoryChannel):
        return False

    if channel.category_id == category.id:
        return True

    await channel.edit(category=category, reason="Clan ticket: move to clan category")
    return True


async def _rename_ticket_to_clan_and_nick(channel: discord.TextChannel, clan_value: str, nick_source: str) -> bool:
    """Rename ticket to: clan-nickname (nickname is slugified)."""
    clan_key = (clan_value or "").strip().lower()
    if not clan_key:
        return False

    slug = _slugify_channel_part(nick_source)
    name = f"{clan_key}-{slug}"

    # Channel name limit is 100.
    if len(name) > 100:
        name = name[:100].rstrip("-")
        if not name:
            name = clan_key

    if channel.name == name:
        return True

    await channel.edit(name=name, reason="Clan ticket: rename to clan-nickname")
    return True


def _best_member_nick_for_channel(member: discord.Member) -> str:
    # User requested: use "(přezdívka)" => prefer nick, fallback to display_name / username.
    if member.nick:
        return member.nick
    if member.display_name:
        return member.display_name
    return member.name


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

    def __init__(self, ticket_channel_id: int, clan_value: str):
        super().__init__(timeout=None)

        container = discord.ui.Container(
            discord.ui.TextDisplay(content="## 📎 Screeny"),
            discord.ui.TextDisplay(
                content="Až pošleš všechny screeny jako přílohy do ticketu, klikni na **Hotovo**."
            ),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.ActionRow(
                discord.ui.Button(
                    custom_id=_finalize_custom_id(ticket_channel_id, clan_value),
                    label="Hotovo",
                    style=discord.ButtonStyle.success,
                )
            ),
        )
        self.add_item(container)


class TicketReviewView(discord.ui.LayoutView):
    """Review panel for admins and clan roles to accept/deny."""

    def __init__(self, ticket_channel_id: int, clan_value: str):
        super().__init__(timeout=None)

        container = discord.ui.Container(
            discord.ui.TextDisplay(content="## 🛡️ Rozhodnutí (admin / clan)"),
            discord.ui.TextDisplay(content="Použij **Přijmout** nebo **Odmítnout**."),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.ActionRow(
                discord.ui.Button(
                    custom_id=_review_custom_id("accept", ticket_channel_id, clan_value),
                    label="Přijmout",
                    style=discord.ButtonStyle.success,
                ),
                discord.ui.Button(
                    custom_id=_review_custom_id("deny", ticket_channel_id, clan_value),
                    label="Odmítnout",
                    style=discord.ButtonStyle.danger,
                ),
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

        member: discord.Member | None = None
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

        # 2) Rename ticket channel to: clan-nickname (from Roblox display name)
        rename_ok = False
        rename_err = None
        try:
            rename_ok = await _rename_ticket_to_clan_and_nick(ticket_channel, self.clan_value, roblox_display)
        except discord.Forbidden:
            rename_err = "Nemám práva na přejmenování kanálu (Manage Channels)."
        except discord.HTTPException as e:
            rename_err = f"Discord API chyba při přejmenování kanálu: {e}"

        # 3) Ensure clan review role visibility
        role_vis_ok = False
        role_vis_err = None
        try:
            role_vis_ok = await _ensure_review_role_can_view(ticket_channel, self.clan_value)
        except discord.Forbidden:
            role_vis_err = "Nemám práva nastavovat permissions (Manage Channels)."
        except discord.HTTPException as e:
            role_vis_err = f"Discord API chyba při nastavování permissions: {e}"

        # 4) Move ticket to clan category (so the correct staff sees it)
        move_ok = False
        move_err = None
        try:
            move_ok = await _ensure_ticket_in_clan_category(ticket_channel, self.clan_value)
        except discord.Forbidden:
            move_err = "Nemám práva přesouvat kanály (Manage Channels)."
        except discord.HTTPException as e:
            move_err = f"Discord API chyba při přesunu kanálu: {e}"

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
                    f"• Přejmenování ticketu: **{'OK' if rename_ok else 'NE'}**\n"
                    f"• Přístup pro clan roli: **{'OK' if role_vis_ok else 'NE'}**\n"
                    f"• Přesun do kategorie: **{'OK' if move_ok else 'NE'}**"
                )
            ),
        )
        summary_view.add_item(summary_container)
        await ticket_channel.send(content="", view=summary_view)

        # If something failed, print reason(s) into the ticket.
        if (not nick_ok) or (not rename_ok) or (not role_vis_ok) or (not move_ok):
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

            if not rename_ok and rename_err:
                warn_items.append(discord.ui.TextDisplay(content=f"**Rename ticketu:** {rename_err}"))

            if not role_vis_ok:
                if role_vis_err:
                    warn_items.append(discord.ui.TextDisplay(content=f"**Clan role přístup:** {role_vis_err}"))
                else:
                    warn_items.append(
                        discord.ui.TextDisplay(
                            content="**Clan role přístup:** Clan role nebyla nalezena (zkontroluj ID role)."
                        )
                    )

            if not move_ok:
                if move_err:
                    warn_items.append(discord.ui.TextDisplay(content=f"**Přesun do kategorie:** {move_err}"))
                else:
                    warn_items.append(
                        discord.ui.TextDisplay(
                            content="**Přesun do kategorie:** Kategorie nebyla nalezena (zkontroluj ID)."
                        )
                    )

            warn_container = discord.ui.Container(*warn_items)
            warn_view.add_item(warn_container)
            await ticket_channel.send(content="", view=warn_view)

        # Ask for screenshots + provide finalize button (includes clan in custom_id)
        await ticket_channel.send(content="", view=TicketFinalizeView(ticket_channel.id, self.clan_value))

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

            intake_category = guild.get_channel(TICKET_CATEGORY_ID)
            if intake_category is None or not isinstance(intake_category, discord.CategoryChannel):
                await interaction.response.send_message("Vstupní kategorie neexistuje nebo nemám práva.", ephemeral=True)
                return

            # Store applicant + clan in topic for later accept/deny (persists across restarts)
            topic = f"clan_applicant={interaction.user.id};clan={clan_value}"

            # Temporary name; will be renamed after modal submit
            channel_name = f"ticket-{_slugify_channel_part(clan_value)}-{_slugify_channel_part(interaction.user.name)}"

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                interaction.user: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                ),
            }

            # Admin role (optional)
            admin_role = discord.utils.get(guild.roles, name=ADMIN_ROLE_NAME)
            if admin_role:
                overwrites[admin_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                )

            # Clan review role overwrite (so they have visibility immediately)
            review_role_id = _review_role_id_for_clan(clan_value)
            if review_role_id:
                review_role = guild.get_role(review_role_id)
                if review_role:
                    overwrites[review_role] = discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                        attach_files=True,
                    )

            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                category=intake_category,
                topic=topic,
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

        # 3) Handle finalize button -> mention clan role + ensure name/category + show review panel
        if isinstance(custom_id, str) and custom_id.startswith("clan_finalize|"):
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

            guild = interaction.guild
            if guild is None:
                await interaction.response.send_message("Tahle akce musí běžet na serveru.", ephemeral=True)
                return

            ticket_channel = guild.get_channel(channel_id)
            if ticket_channel is None or not isinstance(ticket_channel, discord.TextChannel):
                await interaction.response.send_message("Ticket kanál neexistuje.", ephemeral=True)
                return

            # Prefer stored clan from topic
            applicant_id, topic_clan = _parse_ticket_topic(ticket_channel.topic or "")
            if topic_clan:
                clan_value = topic_clan

            # Ensure review role visibility again (in case overwrites were changed).
            try:
                await _ensure_review_role_can_view(ticket_channel, clan_value)
            except Exception:
                pass

            # Ensure correct category + rename based on applicant nickname if possible
            try:
                await _ensure_ticket_in_clan_category(ticket_channel, clan_value)
            except Exception:
                pass

            if applicant_id:
                try:
                    applicant = guild.get_member(applicant_id) or await guild.fetch_member(applicant_id)
                    nick_source = _best_member_nick_for_channel(applicant)
                    await _rename_ticket_to_clan_and_nick(ticket_channel, clan_value, nick_source)
                except Exception:
                    pass

            mention = _role_mention_for_clan(clan_value)
            if mention:
                await ticket_channel.send(f"✅ {interaction.user.mention} označil/a přihlášku jako hotovou. {mention}")
            else:
                await ticket_channel.send(f"✅ {interaction.user.mention} označil/a přihlášku jako hotovou.")

            # Send review panel for admins/clan roles
            await ticket_channel.send(content="", view=TicketReviewView(ticket_channel.id, clan_value))

            await interaction.response.send_message("✅ Označeno jako hotovo.", ephemeral=True)
            return

        # 4) Handle review accept/deny
        if isinstance(custom_id, str) and custom_id.startswith("clan_review|"):
            parts = custom_id.split("|", 3)
            if len(parts) != 4:
                await interaction.response.send_message("Neplatný button.", ephemeral=True)
                return

            _, action, channel_id_str, clan_value = parts
            try:
                channel_id = int(channel_id_str)
            except ValueError:
                await interaction.response.send_message("Neplatný ticket.", ephemeral=True)
                return

            guild = interaction.guild
            if guild is None:
                await interaction.response.send_message("Tahle akce musí běžet na serveru.", ephemeral=True)
                return

            clicker = interaction.user
            if not isinstance(clicker, discord.Member):
                await interaction.response.send_message("Neplatný uživatel.", ephemeral=True)
                return

            if not _is_reviewer(clicker, clan_value):
                await interaction.response.send_message("Na toto nemáš oprávnění.", ephemeral=True)
                return

            ticket_channel = guild.get_channel(channel_id)
            if ticket_channel is None or not isinstance(ticket_channel, discord.TextChannel):
                await interaction.response.send_message("Ticket kanál neexistuje.", ephemeral=True)
                return

            applicant_id, topic_clan = _parse_ticket_topic(ticket_channel.topic or "")
            if topic_clan:
                clan_value = topic_clan

            if not applicant_id:
                await interaction.response.send_message("Nelze zjistit žadatele (chybí topic).", ephemeral=True)
                return

            try:
                applicant = guild.get_member(applicant_id) or await guild.fetch_member(applicant_id)
            except discord.NotFound:
                await interaction.response.send_message("Žadatel už není na serveru.", ephemeral=True)
                return

            if applicant.id == clicker.id:
                await interaction.response.send_message("Nemůžeš schválit/odmítnout sám sebe.", ephemeral=True)
                return

            if action == "accept":
                role_id = _member_role_id_for_clan(clan_value)
                if not role_id:
                    await interaction.response.send_message("Pro tento clan není nastavená role.", ephemeral=True)
                    return

                role = guild.get_role(role_id)
                if role is None:
                    await interaction.response.send_message("Role pro přijetí nebyla nalezena.", ephemeral=True)
                    return

                add_ok = False
                add_err = None
                try:
                    await applicant.add_roles(role, reason=f"Clan application accepted for {clan_value}")
                    add_ok = True
                except discord.Forbidden:
                    add_err = "Nemám práva přidat roli (role hierarchy / Manage Roles)."
                except discord.HTTPException as e:
                    add_err = f"Discord API chyba při přidání role: {e}"

                # Keep ticket open for communication (do NOT lock applicant)
                view = discord.ui.LayoutView(timeout=None)
                container = discord.ui.Container(
                    discord.ui.TextDisplay(content="## ✅ Přijato"),
                    discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
                    discord.ui.TextDisplay(content=f"**Clan:** {clan_value}"),
                    discord.ui.TextDisplay(content=f"**Schválil:** {clicker.mention}"),
                    discord.ui.TextDisplay(content=f"**Žadatel:** {applicant.mention}"),
                    discord.ui.TextDisplay(content="**Ticket:** zůstává otevřený pro komunikaci"),
                )
                if add_ok:
                    container.add_item(discord.ui.TextDisplay(content=f"**Role přidána:** <@&{role_id}>"))
                else:
                    container.add_item(discord.ui.TextDisplay(content=f"**Role přidána:** NE ({add_err})"))

                view.add_item(container)
                await ticket_channel.send(content="", view=view)

                await interaction.response.send_message("✅ Přijato.", ephemeral=True)
                return

            if action == "deny":
                view = discord.ui.LayoutView(timeout=None)
                container = discord.ui.Container(
                    discord.ui.TextDisplay(content="## ⛔ Odmítnuto"),
                    discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
                    discord.ui.TextDisplay(content=f"**Clan:** {clan_value}"),
                    discord.ui.TextDisplay(content=f"**Odmítl:** {clicker.mention}"),
                    discord.ui.TextDisplay(content=f"**Žadatel:** {applicant.mention}"),
                    discord.ui.TextDisplay(content="**Ticket:** zůstává otevřený pro komunikaci"),
                )
                view.add_item(container)
                await ticket_channel.send(content="", view=view)

                await interaction.response.send_message("⛔ Odmítnuto.", ephemeral=True)
                return

            await interaction.response.send_message("Neznámá akce.", ephemeral=True)
            return


async def setup(bot: commands.Bot):
    await bot.add_cog(ClanPanelCog(bot))
