import re
import discord
from discord.ext import commands
from discord import app_commands

# Category where NEW ticket channels will be created (initial intake)
TICKET_CATEGORY_ID = 1440977431577235456

# Optional admin role name that can manage tickets
ADMIN_ROLE_NAME = "Admin"

# Language roles:
ROLE_LANG_CZ = 1444075970649915586
ROLE_LANG_EN = 1444075991118119024

# HROT: member role depends on language role
HROT_MEMBER_ROLE_CZ = 1440268327892025438
HROT_MEMBER_ROLE_EN = 1444077881159450655

# Status emojis used in ticket channel name
STATUS_OPEN = "🟠"
STATUS_ACCEPTED = "🟢"
STATUS_DENIED = "🔴"
STATUS_SET = (STATUS_OPEN, STATUS_ACCEPTED, STATUS_DENIED)

# Clan -> "review" role id (leaders/officers) that should see the ticket (and can accept/deny)
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

# Clan -> category id where the ticket should be MOVED after ACCEPT
CLAN_CATEGORY_IDS = {
    "hrot": 1443684694968373421,
    "hr2t": 1444304658142335217,
    "tgcm": 1447423401462333480,
}


I18N = {
    "cs": {
        "modal_title": "Přihláška do clanu",
        "label_display": "Roblox Display Name",
        "ph_display": "Např. senpaicat221",
        "label_rebirths": "Kolik máš rebirthů (text)",
        "ph_rebirths": "Např. 2SP / 150k / ...",
        "label_hours": "Kolik hodin denně můžeš hrát (text)",
        "ph_hours": "Např. 6-10h, 2h, 24/7 ...",
        "must_be_in_guild": "Tahle akce musí běžet na serveru.",
        "intake_missing": "Vstupní kategorie neexistuje nebo nemám práva.",
        "create_no_perms": "Nemám práva vytvořit ticket kanál (Manage Channels).",
        "create_api_err": "Discord API chyba při vytváření ticketu:",
        "nick_owner": "Nelze měnit přezdívku **majiteli serveru**.",
        "nick_perm": "Bot nemá oprávnění **Manage Nicknames** (nebo **Administrator**).",
        "nick_hierarchy": "Role bota je **níž nebo stejně** jako role uživatele (hierarchie rolí).",
        "nick_forbidden": "Discord odmítl změnu přezdívky (oprávnění/hierarchie rolí).",
        "nick_api_err": "Discord API chyba při změně přezdívky:",
        "nick_notfound": "Uživatel nebyl nalezen (NotFound).",
        "rename_no_perms": "Nemám práva na přejmenování kanálu (Manage Channels).",
        "rename_api_err": "Discord API chyba při přejmenování kanálu:",
        "perm_no_perms": "Nemám práva nastavovat permissions (Manage Channels).",
        "perm_api_err": "Discord API chyba při nastavování permissions:",
        "summary_title": "## 📄 Přihláška",
        "summary_clan": "**Clan:**",
        "summary_user": "**Uživatel:**",
        "summary_display": "**Roblox Display Name:**",
        "summary_rebirths": "**Rebirthy:**",
        "summary_hours": "**Hodiny denně:**",
        "summary_auto": "### ✅ Automatické nastavení",
        "summary_nick": "• Přezdívka na serveru:",
        "summary_rename": "• Přejmenování ticketu:",
        "summary_access": "• Přístup pro clan roli:",
        "warn_title": "## ⚠️ Poznámka pro adminy",
        "warn_nick": "**Nick změna:**",
        "warn_diag": "**Diagnostika:**",
        "warn_rename": "**Rename ticketu:**",
        "warn_access": "**Clan role přístup:**",
        "warn_role_missing": "**Clan role přístup:** Clan role nebyla nalezena (zkontroluj ID role).",
        "ticket_created": "✅ Ticket vytvořen:",
        "select_none": "Nebyla vybrána žádná možnost.",
        "settings_invalid": "Neplatný button.",
        "ticket_invalid": "Neplatný ticket.",
        "no_perm": "Na toto nemáš oprávnění.",
        "ticket_missing": "Ticket kanál neexistuje.",
        "cant_get_applicant": "Nelze zjistit žadatele (chybí topic).",
        "applicant_left": "Žadatel už není na serveru.",
        "accept_no_role": "Pro tento clan není nastavená role.",
        "accept_role_missing": "Role pro přijetí nebyla nalezena.",
        "accept_lang_missing": "Žadatel nemá jazykovou roli (CZ/EN) pro HROT.",
        "addrole_forbidden": "Nemám práva přidat roli (Manage Roles / hierarchie).",
        "addrole_api_err": "Discord API chyba při přidání role:",
        "accepted_msg": "✅ **PŘIJATO** — schválil",
        "accepted_role_added": "Role přidána:",
        "denied_msg": "⛔ **ZAMÍTNUTO** — zamítl",
        "accepted_ephemeral": "✅ Přijato.",
        "denied_ephemeral": "⛔ Zamítnuto.",
        "unknown_action": "Neznámá akce.",
        "screenshot_title_prefix": "## Ahoj",
        "screenshot_body": (
            "Nyní nám pošli screenshoty, kde na každém screenshotu bude i viditelně tvůj nick:\n"
            "* inventář petů\n"
            "* počet rebirthů\n"
            "* všechny gamepassy\n"
            "* prestiž\n\n"
            "Screeny posílej jako přílohy sem do ticketu (může být více zpráv).\n\n"
        ),
        "screenshot_done_ping": "Až budeš mít hotovo, napiš sem zprávu a označ:",
        "screenshot_done_msg": "Až budeš mít hotovo, napiš sem zprávu pro vedení clanu.",
        "manage_title": "## ⚙️ Správa přihlášky",
        "manage_choose": "Vyber akci:",
        "btn_accept": "Přijmout",
        "btn_deny": "Zamítnout",
    },
    "en": {
        "modal_title": "Clan application",
        "label_display": "Roblox Display Name",
        "ph_display": "e.g. senpaicat221",
        "label_rebirths": "How many rebirths do you have (text)",
        "ph_rebirths": "e.g. 2SP / 150k / ...",
        "label_hours": "How many hours per day can you play (text)",
        "ph_hours": "e.g. 6-10h, 2h, 24/7 ...",
        "must_be_in_guild": "This action must be used in a server.",
        "intake_missing": "The intake category is missing or I don't have permissions.",
        "create_no_perms": "I don't have permission to create the ticket channel (Manage Channels).",
        "create_api_err": "Discord API error while creating the ticket:",
        "nick_owner": "Cannot change the **server owner's** nickname.",
        "nick_perm": "Bot is missing **Manage Nicknames** (or **Administrator**) permission.",
        "nick_hierarchy": "Bot role is **lower or equal** to the user's top role (role hierarchy).",
        "nick_forbidden": "Discord denied nickname change (permissions/role hierarchy).",
        "nick_api_err": "Discord API error while changing nickname:",
        "nick_notfound": "User not found (NotFound).",
        "rename_no_perms": "I don't have permission to rename the channel (Manage Channels).",
        "rename_api_err": "Discord API error while renaming the channel:",
        "perm_no_perms": "I don't have permission to edit channel permissions (Manage Channels).",
        "perm_api_err": "Discord API error while editing channel permissions:",
        "summary_title": "## 📄 Application",
        "summary_clan": "**Clan:**",
        "summary_user": "**User:**",
        "summary_display": "**Roblox Display Name:**",
        "summary_rebirths": "**Rebirths:**",
        "summary_hours": "**Hours per day:**",
        "summary_auto": "### ✅ Automatic setup",
        "summary_nick": "• Server nickname:",
        "summary_rename": "• Ticket rename:",
        "summary_access": "• Clan role access:",
        "warn_title": "## ⚠️ Note for admins",
        "warn_nick": "**Nickname:**",
        "warn_diag": "**Diagnostics:**",
        "warn_rename": "**Ticket rename:**",
        "warn_access": "**Clan role access:**",
        "warn_role_missing": "**Clan role access:** Clan role not found (check role ID).",
        "ticket_created": "✅ Ticket created:",
        "select_none": "No option was selected.",
        "settings_invalid": "Invalid button.",
        "ticket_invalid": "Invalid ticket.",
        "no_perm": "You don't have permission to do that.",
        "ticket_missing": "Ticket channel does not exist.",
        "cant_get_applicant": "Cannot determine applicant (missing topic).",
        "applicant_left": "The applicant is no longer on the server.",
        "accept_no_role": "No member role is configured for this clan.",
        "accept_role_missing": "Member role for accept was not found.",
        "accept_lang_missing": "Applicant is missing the language role (CZ/EN) for HROT.",
        "addrole_forbidden": "I can't add the role (Manage Roles / role hierarchy).",
        "addrole_api_err": "Discord API error while adding role:",
        "accepted_msg": "✅ **ACCEPTED** — approved by",
        "accepted_role_added": "Role added:",
        "denied_msg": "⛔ **DENIED** — denied by",
        "accepted_ephemeral": "✅ Accepted.",
        "denied_ephemeral": "⛔ Denied.",
        "unknown_action": "Unknown action.",
        "screenshot_title_prefix": "## Hi",
        "screenshot_body": (
            "Now please send screenshots where your nickname is clearly visible on each screenshot:\n"
            "* pets inventory\n"
            "* rebirth count\n"
            "* all gamepasses\n"
            "* prestige\n\n"
            "Send screenshots as attachments here in the ticket (you can send multiple messages).\n\n"
        ),
        "screenshot_done_ping": "When you're done, write a message here and mention:",
        "screenshot_done_msg": "When you're done, write a message here for clan leadership.",
        "manage_title": "## ⚙️ Application management",
        "manage_choose": "Choose an action:",
        "btn_accept": "Accept",
        "btn_deny": "Deny",
    },
}


def _lang_for_member(member: discord.Member) -> str:
    """Choose language based on role. EN has priority if both are present."""
    if member is None:
        return "cs"
    role_ids = {r.id for r in getattr(member, "roles", [])}
    if ROLE_LANG_EN in role_ids:
        return "en"
    if ROLE_LANG_CZ in role_ids:
        return "cs"
    return "cs"


def _t(lang: str, key: str) -> str:
    if lang not in I18N:
        lang = "cs"
    return I18N[lang].get(key, I18N["cs"].get(key, key))


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


def _settings_custom_id(channel_id: int, clan_value: str, lang: str) -> str:
    return f"clan_settings|{channel_id}|{clan_value}|{lang}"


def _review_custom_id(action: str, channel_id: int, clan_value: str, lang: str) -> str:
    return f"clan_review|{action}|{channel_id}|{clan_value}|{lang}"


def _review_role_id_for_clan(clan_value: str):
    return CLAN_REVIEW_ROLE_IDS.get((clan_value or "").strip().lower())


def _member_role_id_for_clan(clan_value: str):
    return CLAN_MEMBER_ROLE_IDS.get((clan_value or "").strip().lower())


def _member_role_id_for_accept(clan_value: str, applicant: discord.Member):
    """Return member role id for accept.

    Special rule: For clan HROT only, the member role depends on applicant language role.
    - If applicant has ROLE_LANG_CZ -> HROT_MEMBER_ROLE_CZ
    - If applicant has ROLE_LANG_EN -> HROT_MEMBER_ROLE_EN
    """
    clan_key = (clan_value or "").strip().lower()
    if clan_key == "hrot":
        role_ids = {r.id for r in getattr(applicant, "roles", [])}
        if ROLE_LANG_CZ in role_ids:
            return HROT_MEMBER_ROLE_CZ
        if ROLE_LANG_EN in role_ids:
            return HROT_MEMBER_ROLE_EN
        return None
    return _member_role_id_for_clan(clan_value)


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
        CZ_FLAG = "\U0001F1E8\U0001F1FF"  # 🇨🇿
        US_FLAG = "\U0001F1FA\U0001F1F8"  # 🇺🇸
        container = discord.ui.Container(
            discord.ui.TextDisplay(content="## CLAN APPLICATIONS"),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),

            discord.ui.TextDisplay(
                content=(
                    "### 🇺🇸 Requirements\n"
                    "```\n"
                    "- 15SP rebirths +\n"
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
                    "- 15SP rebirthů +\n"
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
	    		placeholder="Choose Clan",
	    		options=[
	    			discord.SelectOption(
	    				label="Main Clan HROT",
	    				value="HROT",
	    				description="🇨🇿 & 🇺🇸",
	    			),
	    			discord.SelectOption(
	    				label="Second Clan HR2T",
	    				value="HR2T",
	    				description="🇨🇿",
	    			),
	    			discord.SelectOption(
	    				label="Third Clan TGCM",
	    				value="TGCM",
	    				description="🇺🇸",
	    			),
	    		],
	    	)
	    ),

        )
        self.add_item(container)


class ScreenshotInstructionsView(discord.ui.LayoutView):
    """Single message with screenshot instructions (NO buttons)."""

    def __init__(self, user_mention: str, clan_value: str, lang: str):
        super().__init__(timeout=None)

        role_mention = _role_mention_for_clan(clan_value)

        body = _t(lang, "screenshot_body")
        if role_mention:
            done_line = f"{_t(lang, 'screenshot_done_ping')} {role_mention}"
        else:
            done_line = _t(lang, "screenshot_done_msg")

        container = discord.ui.Container(
            discord.ui.TextDisplay(
                content=(
                    f"{_t(lang, 'screenshot_title_prefix')} {user_mention}\n"
                    + body
                    + done_line
                )
            )
        )
        self.add_item(container)


class AdminDecisionView(discord.ui.LayoutView):
    """Ephemeral panel shown after clicking ⚙️. Only admin/clan role can use."""

    def __init__(self, ticket_channel_id: int, clan_value: str, lang: str):
        super().__init__(timeout=None)

        container = discord.ui.Container(
            discord.ui.TextDisplay(content=_t(lang, "manage_title")),
            discord.ui.TextDisplay(content=_t(lang, "manage_choose")),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.ActionRow(
                discord.ui.Button(
                    custom_id=_review_custom_id("accept", ticket_channel_id, clan_value, lang),
                    label=_t(lang, "btn_accept"),
                    style=discord.ButtonStyle.success,
                ),
                discord.ui.Button(
                    custom_id=_review_custom_id("deny", ticket_channel_id, clan_value, lang),
                    label=_t(lang, "btn_deny"),
                    style=discord.ButtonStyle.danger,
                ),
            ),
        )
        self.add_item(container)


class ClanApplicationModal(discord.ui.Modal):
    """Modal for application input (text only). Ticket is created after submit."""

    def __init__(self, clan_value: str, lang: str):
        self.lang = lang
        super().__init__(title=_t(lang, "modal_title"))
        self.clan_value = str(clan_value)

        self.display_name = discord.ui.TextInput(
            label=_t(lang, "label_display"),
            placeholder=_t(lang, "ph_display"),
            required=True,
            max_length=32,
        )
        self.rebirths = discord.ui.TextInput(
            label=_t(lang, "label_rebirths"),
            placeholder=_t(lang, "ph_rebirths"),
            required=True,
            max_length=120,
        )
        self.hours_per_day = discord.ui.TextInput(
            label=_t(lang, "label_hours"),
            placeholder=_t(lang, "ph_hours"),
            required=True,
            max_length=120,
        )

        self.add_item(self.display_name)
        self.add_item(self.rebirths)
        self.add_item(self.hours_per_day)

    async def on_submit(self, interaction: discord.Interaction):
        lang = self.lang

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(_t(lang, "must_be_in_guild"), ephemeral=True)
            return

        # Defer early to avoid interaction timeouts while creating channel
        await interaction.response.defer(ephemeral=True)

        intake_category = guild.get_channel(TICKET_CATEGORY_ID)
        if intake_category is None or not isinstance(intake_category, discord.CategoryChannel):
            await interaction.followup.send(_t(lang, "intake_missing"), ephemeral=True)
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
            await interaction.followup.send(_t(lang, "create_no_perms"), ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"{_t(lang, 'create_api_err')} {e}", ephemeral=True)
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
                nick_diag.append(_t(lang, "nick_owner"))
            if not (bot_member.guild_permissions.manage_nicknames or bot_member.guild_permissions.administrator):
                nick_diag.append(_t(lang, "nick_perm"))
            if bot_member.top_role <= member.top_role and member != guild.owner:
                nick_diag.append(_t(lang, "nick_hierarchy"))

            await member.edit(
                nick=roblox_display_nick,
                reason="Clan application: set nickname to Roblox Display Name",
            )
            nick_ok = True

        except discord.Forbidden:
            nick_err = _t(lang, "nick_forbidden")
        except discord.HTTPException as e:
            nick_err = f"{_t(lang, 'nick_api_err')} {e}"
        except discord.NotFound:
            nick_err = _t(lang, "nick_notfound")

        # 2) Rename ticket channel to: 🟠přihlášky-{clan}-{player}
        rename_ok = False
        rename_err = None
        try:
            rename_ok = await _rename_ticket_prefix(ticket_channel, self.clan_value, roblox_display, STATUS_OPEN)
        except discord.Forbidden:
            rename_err = _t(lang, "rename_no_perms")
        except discord.HTTPException as e:
            rename_err = f"{_t(lang, 'rename_api_err')} {e}"

        # 3) Ensure clan review role visibility (safety)
        role_vis_ok = False
        role_vis_err = None
        try:
            role_vis_ok = await _ensure_review_role_can_view(ticket_channel, self.clan_value)
        except discord.Forbidden:
            role_vis_err = _t(lang, "perm_no_perms")
        except discord.HTTPException as e:
            role_vis_err = f"{_t(lang, 'perm_api_err')} {e}"

        # Summary (Components V2) + ⚙️ button
        summary_view = discord.ui.LayoutView(timeout=None)
        summary_container = discord.ui.Container(
            discord.ui.TextDisplay(content=_t(lang, "summary_title")),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(content=f"{_t(lang, 'summary_clan')} {self.clan_value}"),
            discord.ui.TextDisplay(content=f"{_t(lang, 'summary_user')} {interaction.user.mention}"),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(content=f"{_t(lang, 'summary_display')} `{roblox_display}`"),
            discord.ui.TextDisplay(content=f"{_t(lang, 'summary_rebirths')} `{self.rebirths.value}`"),
            discord.ui.TextDisplay(content=f"{_t(lang, 'summary_hours')} `{self.hours_per_day.value}`"),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                content=(
                    f"{_t(lang, 'summary_auto')}\n"
                    f"{_t(lang, 'summary_nick')} **{'OK' if nick_ok else 'NE'}**\n"
                    f"{_t(lang, 'summary_rename')} **{'OK' if rename_ok else 'NE'}**\n"
                    f"{_t(lang, 'summary_access')} **{'OK' if role_vis_ok else 'NE'}**"
                )
            ),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.ActionRow(
                discord.ui.Button(
                    custom_id=_settings_custom_id(ticket_channel.id, self.clan_value, lang),
                    style=discord.ButtonStyle.secondary,
                    emoji="⚙️",
                )
            ),
        )
        summary_view.add_item(summary_container)
        await ticket_channel.send(content="", view=summary_view)

        if (not nick_ok) or (not rename_ok) or (not role_vis_ok):
            warn_view = discord.ui.LayoutView(timeout=None)
            warn_items = [
                discord.ui.TextDisplay(content=_t(lang, "warn_title")),
                discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            ]

            if not nick_ok:
                if nick_err:
                    warn_items.append(discord.ui.TextDisplay(content=f"{_t(lang, 'warn_nick')} {nick_err}"))
                if nick_diag:
                    diag_lines = "\n".join([f"• {x}" for x in nick_diag])
                    warn_items.append(discord.ui.TextDisplay(content=f"{_t(lang, 'warn_diag')}\n{diag_lines}"))

            if not rename_ok and rename_err:
                warn_items.append(discord.ui.TextDisplay(content=f"{_t(lang, 'warn_rename')} {rename_err}"))

            if not role_vis_ok:
                if role_vis_err:
                    warn_items.append(discord.ui.TextDisplay(content=f"{_t(lang, 'warn_access')} {role_vis_err}"))
                else:
                    warn_items.append(discord.ui.TextDisplay(content=_t(lang, "warn_role_missing")))

            warn_container = discord.ui.Container(*warn_items)
            warn_view.add_item(warn_container)
            await ticket_channel.send(content="", view=warn_view)

        # Screenshot instructions (no button). Role mention is shown but NOT pinged by bot.
        await ticket_channel.send(
            content="",
            view=ScreenshotInstructionsView(interaction.user.mention, self.clan_value, lang),
            allowed_mentions=discord.AllowedMentions(roles=False, users=True, everyone=False),
        )

        await interaction.followup.send(f"{_t(lang, 'ticket_created')} {ticket_channel.mention}", ephemeral=True)


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
                await interaction.response.send_message(_t("cs", "select_none"), ephemeral=True)
                return

            clan_value = values[0]

            member = interaction.user if isinstance(interaction.user, discord.Member) else None
            lang = _lang_for_member(member) if member else "cs"

            # TGCM is EN-only: force EN for the application + ticket text
            if str(clan_value).lower() == "tgcm":
                lang = "en"

            await interaction.response.send_modal(ClanApplicationModal(clan_value=clan_value, lang=lang))
            return

        # ⚙️ -> show ephemeral accept/deny panel (admin or clan review role only)
        if isinstance(custom_id, str) and custom_id.startswith("clan_settings|"):
            parts = custom_id.split("|", 3)
            if len(parts) != 4:
                await interaction.response.send_message(_t("cs", "settings_invalid"), ephemeral=True)
                return

            _, channel_id_str, clan_value, lang = parts
            try:
                channel_id = int(channel_id_str)
            except ValueError:
                await interaction.response.send_message(_t(lang, "ticket_invalid"), ephemeral=True)
                return

            guild = interaction.guild
            if guild is None:
                await interaction.response.send_message(_t(lang, "must_be_in_guild"), ephemeral=True)
                return

            clicker = interaction.user
            if not isinstance(clicker, discord.Member):
                await interaction.response.send_message(_t(lang, "ticket_invalid"), ephemeral=True)
                return

            if not _is_reviewer(clicker, clan_value):
                await interaction.response.send_message(_t(lang, "no_perm"), ephemeral=True)
                return

            await interaction.response.send_message(
                content="",
                view=AdminDecisionView(channel_id, clan_value, lang),
                ephemeral=True,
            )
            return

        # Accept/Deny actions (permission checked again)
        if isinstance(custom_id, str) and custom_id.startswith("clan_review|"):
            parts = custom_id.split("|", 4)
            if len(parts) != 5:
                await interaction.response.send_message(_t("cs", "settings_invalid"), ephemeral=True)
                return

            _, action, channel_id_str, clan_value, lang = parts
            try:
                channel_id = int(channel_id_str)
            except ValueError:
                await interaction.response.send_message(_t(lang, "ticket_invalid"), ephemeral=True)
                return

            guild = interaction.guild
            if guild is None:
                await interaction.response.send_message(_t(lang, "must_be_in_guild"), ephemeral=True)
                return

            clicker = interaction.user
            if not isinstance(clicker, discord.Member):
                await interaction.response.send_message(_t(lang, "ticket_invalid"), ephemeral=True)
                return

            if not _is_reviewer(clicker, clan_value):
                await interaction.response.send_message(_t(lang, "no_perm"), ephemeral=True)
                return

            ticket_channel = guild.get_channel(channel_id)
            if ticket_channel is None or not isinstance(ticket_channel, discord.TextChannel):
                await interaction.response.send_message(_t(lang, "ticket_missing"), ephemeral=True)
                return

            applicant_id, topic_clan = _parse_ticket_topic(ticket_channel.topic or "")
            if topic_clan:
                clan_value = topic_clan

            if not applicant_id:
                await interaction.response.send_message(_t(lang, "cant_get_applicant"), ephemeral=True)
                return

            try:
                applicant = guild.get_member(applicant_id) or await guild.fetch_member(applicant_id)
            except discord.NotFound:
                await interaction.response.send_message(_t(lang, "applicant_left"), ephemeral=True)
                return

            if action == "accept":
                role_id = _member_role_id_for_accept(clan_value, applicant)
                if not role_id:
                    clan_key = (clan_value or "").strip().lower()
                    msg_key = "accept_lang_missing" if clan_key == "hrot" else "accept_no_role"
                    await interaction.response.send_message(_t(lang, msg_key), ephemeral=True)
                    return

                role = guild.get_role(role_id)
                if role is None:
                    await interaction.response.send_message(_t(lang, "accept_role_missing"), ephemeral=True)
                    return

                try:
                    await applicant.add_roles(role, reason=f"Clan application accepted for {clan_value}")
                except discord.Forbidden:
                    await interaction.response.send_message(_t(lang, "addrole_forbidden"), ephemeral=True)
                    return
                except discord.HTTPException as e:
                    await interaction.response.send_message(f"{_t(lang, 'addrole_api_err')} {e}", ephemeral=True)
                    return

                # Move ticket to clan category
                try:
                    await _move_ticket_to_clan_category(ticket_channel, clan_value)
                except Exception:
                    pass

                # Status emoji -> 🟢
                try:
                    await _set_ticket_status(ticket_channel, STATUS_ACCEPTED)
                except Exception:
                    pass

                await ticket_channel.send(f"{_t(lang, 'accepted_msg')} {clicker.mention}. {_t(lang, 'accepted_role_added')} <@&{role_id}>.")
                await interaction.response.send_message(_t(lang, "accepted_ephemeral"), ephemeral=True)
                return

            if action == "deny":
                # Status emoji -> 🔴
                try:
                    await _set_ticket_status(ticket_channel, STATUS_DENIED)
                except Exception:
                    pass

                await ticket_channel.send(f"{_t(lang, 'denied_msg')} {clicker.mention}.")
                await interaction.response.send_message(_t(lang, "denied_ephemeral"), ephemeral=True)
                return

            await interaction.response.send_message(_t(lang, "unknown_action"), ephemeral=True)
            return


async def setup(bot: commands.Bot):
    await bot.add_cog(ClanPanelCog(bot))
