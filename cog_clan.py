import re
import discord
from discord.ext import commands
from discord import app_commands

# Category where NEW ticket channels will be created (initial intake)
TICKET_CATEGORY_ID = 1440977431577235456

# Optional admin role that should see all tickets
ADMIN_ROLE_NAME = "Admin"

# Status emojis used in ticket channel name
STATUS_OPEN = "🟠"
STATUS_ACCEPTED = "🟢"
STATUS_DENIED = "🔴"
STATUS_SET = (STATUS_OPEN, STATUS_ACCEPTED, STATUS_DENIED)

# Clan -> "review" role id (leaders/officers) that should see the ticket
CLAN_REVIEW_ROLE_IDS = {
    "hrot": 1440268371152339065,
    "hr2t": 1444304987986595923,
    "tgcm": 1447423174974247102,
}

# Clan -> role id that will be ASSIGNED to the applicant on accept (kept for later use)
CLAN_MEMBER_ROLE_IDS = {
    "hrot": 1440268327892025438,
    "hr2t": 1444306127687778405,
    "tgcm": 1447423249817403402,
}

# Clan -> category id where the ticket should be MOVED after ACCEPT (kept for later use)
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
    """Return a safe channel-name fragment."""
    value = (value or "").strip().lower()
    value = re.sub(r"\s+", "-", value)
    value = value.replace("/", "-").replace("\\", "-")
    value = re.sub(r"[^\w\-]", "", value, flags=re.UNICODE)
    value = re.sub(r"[-_]{2,}", "-", value).strip("-_")
    return value or "applicant"


def _review_custom_id(action: str, channel_id: int, clan_value: str) -> str:
    # action: accept / deny (kept for later use)
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


async def _move_ticket_to_clan_category(channel: discord.TextChannel, clan_value: str) -> bool:
    """Move the ticket to the configured clan category (only used on ACCEPT)."""
    cid = _category_id_for_clan(clan_value)
    if not cid:
        return False

    category = channel.guild.get_channel(cid)
    if category is None or not isinstance(category, discord.CategoryChannel):
        return False

    if channel.category_id == category.id:
        return True

    await channel.edit(category=category, reason="Clan ticket: move to clan category (accepted)")
    return True


def _apply_status_to_name(name: str, status_emoji: str) -> str:
    """Replace leading status emoji (🟠/🟢/🔴) with requested status emoji."""
    if not name:
        return name
    if name[0] in STATUS_SET:
        return status_emoji + name[1:]
    return status_emoji + name


async def _set_ticket_status(channel: discord.TextChannel, status_emoji: str) -> bool:
    """Update ticket status emoji in channel name."""
    new_name = _apply_status_to_name(channel.name, status_emoji)
    if new_name == channel.name:
        return True
    await channel.edit(name=new_name, reason="Clan ticket: update status emoji")
    return True


async def _rename_ticket_prefix(
    channel: discord.TextChannel,
    clan_value: str,
    player_name: str,
    status_emoji: str = STATUS_OPEN,
) -> bool:
    """Rename ticket to requested format: 🟠přihlášky-{clan}-{player}"""
    clan_key = (clan_value or "").strip().lower()
    if not clan_key:
        return False

    slug = _slugify_channel_part(player_name)
    name = f"{status_emoji}přihlášky-{clan_key}-{slug}"

    if len(name) > 100:
        name = name[:100].rstrip("-")
        if not name:
            name = f"{status_emoji}přihlášky-{clan_key}"

    if channel.name == name:
        return True

    await channel.edit(name=name, reason="Clan ticket: rename to requested format")
    return True


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
                    "```\n"
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
                    "```\n"
                )
            ),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),

            discord.ui.ActionRow(
                discord.ui.Select(
                    custom_id="clan_select",
                    placeholder="Vyber clan",
                    options=[
                        discord.SelectOption(label="HROT", value="HROT", description=":flag_cz: :flag_us:"),
                        discord.SelectOption(label="HR2T", value="HR2T", description=":flag_cz:"),
                        discord.SelectOption(label="TGCM", value="TGCM", description=":flag_us:"),
                    ],
                )
            ),
        )
        self.add_item(container)


class ScreenshotInstructionsView(discord.ui.LayoutView):
    """Single message with screenshot instructions (NO buttons)."""

    def __init__(self, user_mention: str, clan_value: str):
        super().__init__(timeout=None)

        role_mention = _role_mention_for_clan(clan_value)

        container = discord.ui.Container(
            discord.ui.TextDisplay(
                content=(
                    f"## Ahoj {user_mention}\n"
                    "Nyní nám pošli screenshoty, kde na každém screenshotu bude i viditelně tvůj nick:\n"
                    "* inventář petů\n"
                    "* počet rebirthů\n"
                    "* všechny gamepassy\n"
                    "* prestiž\n\n"
                    "Screeny posílej jako přílohy sem do ticketu (může být více zpráv).\n\n"
                    + (f"Až budeš mít hotovo, napiš sem zprávu a označ: {role_mention}" if role_mention else "Až budeš mít hotovo, napiš sem zprávu pro vedení clanu.")
                )
            )
        )
        self.add_item(container)


class TicketReviewView(discord.ui.LayoutView):
    """Kept for later use (accept/deny buttons will be redesigned later)."""

    def __init__(self, ticket_channel_id: int, clan_value: str):
        super().__init__(timeout=None)

        container = discord.ui.Container(
            discord.ui.TextDisplay(content="## 🛡️ Rozhodnutí (admin / clan)"),
            discord.ui.TextDisplay(content="(Toto se bude upravovat později.)"),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.ActionRow(
                discord.ui.Button(
                    custom_id=_review_custom_id("accept", ticket_channel_id, clan_value),
                    label="Přijmout",
                    style=discord.ButtonStyle.success,
                    disabled=True,
                ),
                discord.ui.Button(
                    custom_id=_review_custom_id("deny", ticket_channel_id, clan_value),
                    label="Odmítnout",
                    style=discord.ButtonStyle.danger,
                    disabled=True,
                ),
            ),
        )
        self.add_item(container)


class ClanApplicationModal(discord.ui.Modal):
    """Modal for application input (text only). Ticket is created after submit."""

    def __init__(self, clan_value: str):
        super().__init__(title="Přihláška do clanu")
        self.clan_value = str(clan_value)

        self.display_name = discord.ui.TextInput(
            label="Roblox Display Name",
            placeholder="Např. senpaicat221",
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

        # Defer early to avoid interaction timeouts while creating channel
        await interaction.response.defer(ephemeral=True)

        intake_category = guild.get_channel(TICKET_CATEGORY_ID)
        if intake_category is None or not isinstance(intake_category, discord.CategoryChannel):
            await interaction.followup.send("Vstupní kategorie neexistuje nebo nemám práva.", ephemeral=True)
            return

        roblox_display = (self.display_name.value or "").strip()
        roblox_display_nick = _sanitize_nickname(roblox_display)

        # Create ticket channel first
        topic = f"clan_applicant={interaction.user.id};clan={self.clan_value}"
        tmp_name = f"ticket-{_slugify_channel_part(self.clan_value)}-{_slugify_channel_part(interaction.user.name)}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
            ),
        }

        admin_role = discord.utils.get(guild.roles, name=ADMIN_ROLE_NAME)
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
            )

        review_role_id = _review_role_id_for_clan(self.clan_value)
        if review_role_id:
            review_role = guild.get_role(review_role_id)
            if review_role:
                overwrites[review_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                )

        try:
            ticket_channel = await guild.create_text_channel(
                name=tmp_name,
                overwrites=overwrites,
                category=intake_category,
                topic=topic,
                reason=f"Clan ticket: {self.clan_value}",
            )
        except discord.Forbidden:
            await interaction.followup.send("Nemám práva vytvořit ticket kanál (Manage Channels).", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"Discord API chyba při vytváření ticketu: {e}", ephemeral=True)
            return

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

        # 2) Rename ticket channel to: 🟠přihlášky-{clan}-{player}
        rename_ok = False
        rename_err = None
        try:
            rename_ok = await _rename_ticket_prefix(ticket_channel, self.clan_value, roblox_display, STATUS_OPEN)
        except discord.Forbidden:
            rename_err = "Nemám práva na přejmenování kanálu (Manage Channels)."
        except discord.HTTPException as e:
            rename_err = f"Discord API chyba při přejmenování kanálu: {e}"

        # 3) Ensure clan review role visibility (safety)
        role_vis_ok = False
        role_vis_err = None
        try:
            role_vis_ok = await _ensure_review_role_can_view(ticket_channel, self.clan_value)
        except discord.Forbidden:
            role_vis_err = "Nemám práva nastavovat permissions (Manage Channels)."
        except discord.HTTPException as e:
            role_vis_err = f"Discord API chyba při nastavování permissions: {e}"

        # Summary (Components V2)
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
                    f"• Přístup pro clan roli: **{'OK' if role_vis_ok else 'NE'}**"
                )
            ),
        )
        summary_view.add_item(summary_container)
        await ticket_channel.send(content="", view=summary_view)

        if (not nick_ok) or (not rename_ok) or (not role_vis_ok):
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
                        discord.ui.TextDisplay(content="**Clan role přístup:** Clan role nebyla nalezena (zkontroluj ID role).")
                    )

            warn_container = discord.ui.Container(*warn_items)
            warn_view.add_item(warn_container)
            await ticket_channel.send(content="", view=warn_view)

        # Screenshot instructions (no button). Role mention is shown but NOT pinged by bot.
        await ticket_channel.send(
            content="",
            view=ScreenshotInstructionsView(interaction.user.mention, self.clan_value),
            allowed_mentions=discord.AllowedMentions(roles=False, users=True, everyone=False),
        )

        await interaction.followup.send(f"✅ Ticket vytvořen: {ticket_channel.mention}", ephemeral=True)


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

        # Select -> open application modal immediately
        if custom_id == "clan_select":
            values = data.get("values") or []
            if not values:
                await interaction.response.send_message("Nebyla vybrána žádná možnost.", ephemeral=True)
                return

            clan_value = values[0]
            await interaction.response.send_modal(ClanApplicationModal(clan_value=clan_value))
            return

        # Review accept/deny will be redesigned later; nothing else handled here for now.
        return


async def setup(bot: commands.Bot):
    await bot.add_cog(ClanPanelCog(bot))
