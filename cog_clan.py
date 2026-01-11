import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Awaitable, Callable, TypeVar

import discord
from discord.ext import commands
from discord.ext import tasks
from discord import app_commands
from cog_discord_writer import WritePriority, get_writer
from db import (
    add_clan_application_panel,
    delete_clan_definition,
    create_clan_application,
    get_all_clan_application_panels,
    get_clan_panel_config,
    get_clan_definition,
    get_clan_application_by_channel,
    get_open_application_by_channel,
    list_open_clan_applications,
    list_clan_definitions,
    get_next_clan_sort_order,
    get_clan_ticket_vacation,
    get_clan_ticket_category_base_name,
    remove_clan_application_panel,
    delete_clan_ticket_vacation,
    clear_ticket_last_rename,
    clear_ticket_last_move,
    set_clan_application_status,
    set_clan_panel_config,
    set_ticket_last_rename,
    set_ticket_last_move,
    save_clan_ticket_vacation,
    set_clan_ticket_category_base_name,
    mark_clan_application_deleted,
    upsert_clan_definition,
    update_clan_application_form,
    update_clan_application_last_message,
    update_clan_application_last_ping,
    get_ticket_last_rename,
    get_ticket_last_move,
)

# Category where NEW ticket channels will be created (initial intake)
TICKET_CATEGORY_ID = 1440977431577235456

# Category where tickets go while member is on vacation
VACATION_CATEGORY_ID = 1443684733061042187

# Role assigned while member is on vacation
VACATION_ROLE_ID = 1450590927054831777

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

# When True, ticket category labels are only updated by the periodic refresh task.
ONLY_PERIODIC_TICKET_REFRESH = True

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

DEFAULT_CLAN_SELECT_OPTIONS = [
    discord.SelectOption(label="Main Clan HROT", value="HROT", description="🇨🇿 & 🇺🇸"),
    discord.SelectOption(label="Second Clan HR2T", value="HR2T", description="🇨🇿"),
    discord.SelectOption(label="Third Clan TGCM", value="TGCM", description="🇺🇸"),
]

T = TypeVar("T")


async def _retry_rate_limited(
    action: str, action_coro: Callable[[], Awaitable[T]], max_attempts: int = 3
) -> T:
    last_exc: discord.HTTPException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await action_coro()
        except discord.HTTPException as exc:
            if getattr(exc, "status", None) == 429:
                retry_after = getattr(exc, "retry_after", None)
                delay = float(retry_after) if retry_after is not None else 1.0 + attempt
                await asyncio.sleep(delay)
                last_exc = exc
                continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError(f"Rate limit retry exhausted for {action}")


def _guild_clan_config(guild_id: int | None, clan_value: str):
    if guild_id is None:
        return None
    clan_key = (clan_value or "").strip().lower()
    if not clan_key:
        return None
    db_row = get_clan_definition(guild_id, clan_key)
    if not db_row:
        return None
    return db_row


def _simple_text_view(message: str) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(discord.ui.TextDisplay(content=message)))
    return view


def _clan_select_options_for_guild(guild_id: int | None) -> list[discord.SelectOption]:
    if guild_id is None:
        return []

    entries = list_clan_definitions(guild_id)
    options: list[discord.SelectOption] = []
    for entry in entries:
        label = (entry.get("display_name") or entry.get("clan_key") or "").strip() or entry.get("clan_key")
        value = entry.get("clan_key") or ""
        description = (entry.get("description") or "").strip()
        if not value:
            continue
        options.append(
            discord.SelectOption(
                label=label[:100],
                value=str(value),
                description=description[:100] if description else None,
            )
        )

    return options


I18N = {
    "cs": {
        "modal_title": "Přihláška do clanu",
        "label_display": "Roblox username",
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
        "rename_cooldown": "Ticket lze přejmenovat nejdříve za 10 minut od poslední změny.",
        "perm_no_perms": "Nemám práva nastavovat permissions (Manage Channels).",
        "perm_api_err": "Discord API chyba při nastavování permissions:",
        "summary_title": "## 📄 Přihláška",
        "summary_clan": "**Clan:**",
        "summary_user": "**Uživatel:**",
        "summary_display": "**Roblox username:**",
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
        "accept_rename_cooldown": "Přijetí nelze dokončit. Ticket lze přejmenovat znovu za {remaining}.",
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
        "screenshot_done_ping": "Vedení clanu už bylo automaticky upozorněno:",
        "screenshot_done_msg": "Vedení clanu už bylo automaticky upozorněno.",
        "manage_title": "## ⚙️ Správa přihlášky",
        "manage_choose": "Vyber akci:",
        "btn_accept": "Přijmout",
        "btn_deny": "Zamítnout",
        "btn_vacation": "Dovolená",
        "btn_vacation_restore": "Vrátit zpět",
        "btn_move_clan1": "Přesun do HROT",
        "btn_move_clan2": "Přesun do HR2T",
        "btn_move_prefix": "Přesun do {clan}",
        "btn_kick": "Kicknout člena",
        "btn_delete": "Smazat ticket",
        "delete_no_perms": "Nemám práva smazat ticket (Manage Channels).",
        "delete_api_err": "Discord API chyba při smazání ticketu:",
        "deleted_ephemeral": "🗑️ Ticket smazán.",
        "kick_done": "👢 Člen byl vyhozen z clanu.",
        "kick_role_none": "Nenalezena žádná clan role k odebrání.",
        "kick_role_removed": "Odebrané clan role: {roles}",
        "kick_role_forbidden": "Nemám práva odebrat clan roli (Manage Roles / hierarchie).",
        "kick_role_failed": "Discord API chyba při odebrání clan role.",
        "kick_ticket_deleted": "Ticket smazán.",
        "kick_ticket_delete_forbidden": "Nemám práva smazat ticket (Manage Channels).",
        "kick_ticket_delete_failed": "Discord API chyba při smazání ticketu.",
        "move_done": "🔁 Ticket přesunut do clanu: {clan}.",
        "move_same": "Ticket už patří do clanu: {clan}.",
        "move_cooldown_remaining": "Ticket lze přesunout znovu za {remaining}.",
        "move_no_perms": "Nemám práva přesunout ticket (Manage Channels).",
        "move_api_err": "Discord API chyba při přesunu ticketu:",
        "vacation_missing_category": "Kategorie pro dovolenou neexistuje.",
        "vacation_role_missing": "Role pro dovolenou nebyla nalezena.",
        "vacation_already": "Ticket už je v režimu dovolené.",
        "vacation_set": "🛫 Ticket přesunut na dovolenou a role upraveny.",
        "vacation_restore_missing": "Ticket není označený jako dovolená.",
        "vacation_restored": "✅ Ticket a role vráceny zpět.",
        "vacation_restore_category_missing": "Původní kategorie už neexistuje.",
        "vacation_remove_forbidden": "Nemám práva odebrat roli (Manage Roles / hierarchie).",
        "vacation_remove_api_err": "Discord API chyba při odebrání role:",
        "vacation_add_forbidden": "Nemám práva přidat roli (Manage Roles / hierarchie).",
        "vacation_add_api_err": "Discord API chyba při přidání role:",
        "vacation_move_forbidden": "Nemám práva přesunout ticket (Manage Channels).",
        "vacation_move_api_err": "Discord API chyba při přesunu ticketu:",
        "ticket_reminder": "Dořešit ticket.",
        "ticket_reminder_manual_done": "Kontrola ticketů dokončena. Odesláno připomenutí: {count}.",
        "ticket_reminder_manual_none": "Kontrola ticketů dokončena. Nebylo potřeba nic připomenout.",
    },
    "en": {
        "modal_title": "Clan application",
        "label_display": "Roblox username",
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
        "rename_cooldown": "Ticket can be renamed only 10 minutes after the last change.",
        "perm_no_perms": "I don't have permission to edit channel permissions (Manage Channels).",
        "perm_api_err": "Discord API error while editing channel permissions:",
        "summary_title": "## 📄 Application",
        "summary_clan": "**Clan:**",
        "summary_user": "**User:**",
        "summary_display": "**Roblox username:**",
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
        "accept_rename_cooldown": "Cannot accept yet. The ticket can be renamed again in {remaining}.",
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
        "screenshot_done_ping": "Clan leadership has already been notified automatically:",
        "screenshot_done_msg": "Clan leadership has already been notified automatically.",
        "manage_title": "## ⚙️ Application management",
        "manage_choose": "Choose an action:",
        "btn_accept": "Accept",
        "btn_deny": "Deny",
        "btn_vacation": "Vacation",
        "btn_vacation_restore": "Restore",
        "btn_move_clan1": "Move to HROT",
        "btn_move_clan2": "Move to HR2T",
        "btn_move_prefix": "Move to {clan}",
        "btn_kick": "Kick member",
        "btn_delete": "Delete ticket",
        "delete_no_perms": "I don't have permission to delete the ticket (Manage Channels).",
        "delete_api_err": "Discord API error while deleting the ticket:",
        "deleted_ephemeral": "🗑️ Ticket deleted.",
        "kick_done": "👢 Member was kicked from the clan.",
        "kick_role_none": "No clan roles found to remove.",
        "kick_role_removed": "Removed clan roles: {roles}",
        "kick_role_forbidden": "I don't have permission to remove the clan role (Manage Roles / hierarchy).",
        "kick_role_failed": "Discord API error while removing the clan role.",
        "kick_ticket_deleted": "Ticket deleted.",
        "kick_ticket_delete_forbidden": "I don't have permission to delete the ticket (Manage Channels).",
        "kick_ticket_delete_failed": "Discord API error while deleting the ticket.",
        "move_done": "🔁 Ticket moved to clan: {clan}.",
        "move_same": "Ticket already belongs to clan: {clan}.",
        "move_cooldown_remaining": "Ticket can be moved again in {remaining}.",
        "move_no_perms": "I don't have permission to move the ticket (Manage Channels).",
        "move_api_err": "Discord API error while moving the ticket:",
        "vacation_missing_category": "Vacation category is missing.",
        "vacation_role_missing": "Vacation role was not found.",
        "vacation_already": "This ticket is already in vacation mode.",
        "vacation_set": "🛫 Ticket moved to vacation and roles updated.",
        "vacation_restore_missing": "This ticket is not marked as vacation.",
        "vacation_restored": "✅ Ticket and roles restored.",
        "vacation_restore_category_missing": "The original category no longer exists.",
        "vacation_remove_forbidden": "I don't have permission to remove the role (Manage Roles / hierarchy).",
        "vacation_remove_api_err": "Discord API error while removing role:",
        "vacation_add_forbidden": "I don't have permission to add the role (Manage Roles / hierarchy).",
        "vacation_add_api_err": "Discord API error while adding role:",
        "vacation_move_forbidden": "I don't have permission to move the ticket (Manage Channels).",
        "vacation_move_api_err": "Discord API error while moving the ticket:",
        "ticket_reminder": "Please finish the ticket.",
        "ticket_reminder_manual_done": "Ticket check complete. Reminders sent: {count}.",
        "ticket_reminder_manual_none": "Ticket check complete. No reminders were needed.",
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


def _t(lang: str, key: str, **kwargs: str) -> str:
    if lang not in I18N:
        lang = "cs"
    text = I18N[lang].get(key, I18N["cs"].get(key, key))
    if kwargs:
        return text.format(**kwargs)
    return text


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


def _review_role_id_for_clan(clan_value: str, guild_id: int | None = None):
    db_conf = _guild_clan_config(guild_id, clan_value)
    if db_conf and db_conf.get("review_role_id"):
        return db_conf["review_role_id"]
    return CLAN_REVIEW_ROLE_IDS.get((clan_value or "").strip().lower())


def _member_role_id_for_clan(clan_value: str, guild_id: int | None = None):
    db_conf = _guild_clan_config(guild_id, clan_value)
    if db_conf and db_conf.get("accept_role_id"):
        return db_conf["accept_role_id"]
    return CLAN_MEMBER_ROLE_IDS.get((clan_value or "").strip().lower())


def _member_role_id_for_accept(clan_value: str, applicant: discord.Member):
    """Return member role id for accept.

    Special rule: Member role depends on applicant language role when configured.
    - If clan has CZ/EN accept roles configured in DB, choose by language role.
    - For HROT fallback, CZ/EN roles are chosen by language role constants.
    """
    clan_key = (clan_value or "").strip().lower()
    guild_id = getattr(applicant.guild, "id", None)
    db_conf = _guild_clan_config(guild_id, clan_value)
    role_ids = {r.id for r in getattr(applicant, "roles", [])}

    lang_role: str | None = None
    if ROLE_LANG_EN in role_ids:
        lang_role = "en"
    elif ROLE_LANG_CZ in role_ids:
        lang_role = "cs"

    if db_conf:
        if lang_role == "cs" and db_conf.get("accept_role_id_cz"):
            return db_conf["accept_role_id_cz"]
        if lang_role == "en" and db_conf.get("accept_role_id_en"):
            return db_conf["accept_role_id_en"]
        if db_conf.get("accept_role_id"):
            return db_conf["accept_role_id"]

    if clan_key == "hrot":
        if lang_role == "cs":
            return HROT_MEMBER_ROLE_CZ
        if lang_role == "en":
            return HROT_MEMBER_ROLE_EN
        return None

    return _member_role_id_for_clan(clan_value, guild_id)

    return _member_role_id_for_clan(clan_value, guild_id)


def _candidate_member_role_ids_for_clan(clan_value: str, guild_id: int | None = None) -> list[int]:
    role_ids: set[int] = set()
    clan_key = (clan_value or "").strip().lower()
    db_conf = _guild_clan_config(guild_id, clan_value)

    if db_conf:
        for key in ("accept_role_id", "accept_role_id_cz", "accept_role_id_en"):
            rid = db_conf.get(key)
            if rid:
                role_ids.add(rid)

    if clan_key == "hrot":
        role_ids.update([HROT_MEMBER_ROLE_CZ, HROT_MEMBER_ROLE_EN])
    else:
        fallback_role = _member_role_id_for_clan(clan_value, guild_id)
        if fallback_role:
            role_ids.add(fallback_role)

    return [rid for rid in role_ids if rid]

def _category_id_for_clan(clan_value: str, guild_id: int | None = None):
    clan_key = (clan_value or "").strip().lower()
    if guild_id is not None:
        db_conf = _guild_clan_config(guild_id, clan_value)
        if db_conf and db_conf.get("accept_category_id"):
            return db_conf["accept_category_id"]
    return CLAN_CATEGORY_IDS.get(clan_key)


def _role_mention_for_clan(clan_value: str, guild_id: int | None = None) -> str:
    rid = _review_role_id_for_clan(clan_value, guild_id)
    return f"<@&{rid}>" if rid else ""


def _display_name_for_clan(clan_value: str, guild_id: int | None = None) -> str:
    db_conf = _guild_clan_config(guild_id, clan_value)
    if db_conf:
        display_name = (db_conf.get("display_name") or "").strip()
        if display_name:
            return display_name
    return (clan_value or "").strip().upper() or "CLAN"


def _move_label(lang: str, display_name: str) -> str:
    return _t(lang, "btn_move_prefix", clan=display_name)


async def _update_ticket_category_label(guild: discord.Guild, category_id: int | None) -> None:
    if guild is None or not category_id:
        return
    category = guild.get_channel(category_id)
    if category is None:
        try:
            category = await guild.fetch_channel(category_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
    if category is None or not isinstance(category, discord.CategoryChannel):
        return

    base_name = get_clan_ticket_category_base_name(guild.id, category_id)
    if not base_name:
        base_name = re.sub(r"\s*\(\d+\)\s*$", "", category.name or "").strip()
        if not base_name:
            base_name = category.name or "Tickets"
        set_clan_ticket_category_base_name(guild.id, category_id, base_name)

    count = len(category.text_channels)
    new_name = f"{base_name} ({count})"
    if category.name == new_name:
        return
    try:
        await _retry_rate_limited(
            "update ticket category label",
            lambda: category.edit(name=new_name, reason="Clan ticket: update category label"),
        )
    except discord.HTTPException:
        pass


def _parse_ticket_topic(topic: str):
    """Parse channel.topic for applicant and clan. Returns (applicant_id:int|None, clan:str|None)."""
    if not topic:
        return None, None
    m1 = re.search(r"clan_applicant=(\d+)", topic)
    m2 = re.search(r"clan=([A-Za-z0-9_\-]+)", topic)
    applicant_id = int(m1.group(1)) if m1 else None
    clan = m2.group(1) if m2 else None
    return applicant_id, clan


def _parse_db_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _is_reviewer(member: discord.Member, clan_value: str) -> bool:
    """Reviewer = Admin role OR clan review role OR administrator perms."""
    if member.guild_permissions.administrator:
        return True

    admin_role = discord.utils.get(member.guild.roles, name=ADMIN_ROLE_NAME)
    if admin_role and admin_role in member.roles:
        return True

    rid = _review_role_id_for_clan(clan_value, member.guild.id if member.guild else None)
    if rid:
        role = member.guild.get_role(rid)
        if role and role in member.roles:
            return True

    return False


async def _ensure_review_role_can_view(channel: discord.TextChannel, clan_value: str) -> bool:
    """Ensure the clan review role has visibility to the ticket channel."""
    rid = _review_role_id_for_clan(clan_value, channel.guild.id if channel.guild else None)
    if not rid:
        return False

    role = channel.guild.get_role(rid)
    if role is None:
        return False

    await _retry_rate_limited(
        "ensure review role visibility",
        lambda: channel.set_permissions(
            role,
            overwrite=discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
            ),
            reason="Clan ticket: ensure review role visibility",
        ),
    )
    return True


async def _swap_review_role_visibility(
    channel: discord.TextChannel, old_clan: str, new_clan: str
) -> None:
    guild_id = channel.guild.id if channel.guild else None
    old_role_id = _review_role_id_for_clan(old_clan, guild_id)
    new_role_id = _review_role_id_for_clan(new_clan, guild_id)

    if old_role_id and old_role_id != new_role_id:
        old_role = channel.guild.get_role(old_role_id)
        if old_role:
            await _retry_rate_limited(
                "remove old review role",
                lambda: channel.set_permissions(
                    old_role,
                    overwrite=None,
                    reason="Clan ticket: remove old review role",
                ),
            )

    if new_role_id:
        await _ensure_review_role_can_view(channel, new_clan)


async def _move_ticket_to_clan_category(channel: discord.TextChannel, clan_value: str) -> bool:
    """Move the ticket to the configured clan category (only used on ACCEPT)."""
    cid = _category_id_for_clan(clan_value, channel.guild.id if channel.guild else None)
    if not cid:
        return False

    category = channel.guild.get_channel(cid)
    if category is None or not isinstance(category, discord.CategoryChannel):
        return False

    if channel.category_id == category.id:
        return True

    await _retry_rate_limited(
        "move ticket to clan category",
        lambda: channel.edit(category=category, reason="Clan ticket: move to clan category (accepted)"),
    )
    return True


def _apply_status_to_name(name: str, status_emoji: str) -> str:
    """Replace leading status emoji (🟠/🟢/🔴) with requested status emoji."""
    if not name:
        return name
    if name[0] in STATUS_SET:
        return status_emoji + name[1:]
    return status_emoji + name


def _status_emoji_from_name(name: str) -> str:
    if not name:
        return STATUS_OPEN
    if name[0] in STATUS_SET:
        return name[0]
    return STATUS_OPEN


def _format_cooldown_remaining(seconds: int) -> str:
    remaining = max(0, int(seconds))
    minutes, secs = divmod(remaining, 60)
    return f"{minutes}:{secs:02d}"


async def _ensure_member_can_mention_everyone(
    channel: discord.TextChannel, member: discord.Member, reason: str
) -> bool:
    """Ensure the applicant can mention roles/everyone inside their ticket."""
    overwrite = channel.overwrites_for(member)
    changed = False

    if overwrite.view_channel is not True:
        overwrite.view_channel = True
        changed = True
    if overwrite.send_messages is not True:
        overwrite.send_messages = True
        changed = True
    if overwrite.read_message_history is not True:
        overwrite.read_message_history = True
        changed = True
    if overwrite.attach_files is not True:
        overwrite.attach_files = True
        changed = True
    if overwrite.mention_everyone is not True:
        overwrite.mention_everyone = True
        changed = True

    if not changed:
        return False

    await channel.set_permissions(member, overwrite=overwrite, reason=reason)
    return True


async def _rename_ticket_with_cooldown(
    channel: discord.TextChannel,
    lang: str,
    reason: str,
    rename_action: Callable[[], Awaitable[None]],
) -> tuple[bool, str | None]:
    last_rename_ts = get_ticket_last_rename(channel.id)
    now_ts = int(time.time())
    if last_rename_ts is not None and now_ts - last_rename_ts < 600:
        return False, _t(lang, "rename_cooldown")

    await _retry_rate_limited(reason, rename_action)
    set_ticket_last_rename(channel.id, now_ts)
    return True, None


async def _set_ticket_status(
    channel: discord.TextChannel, status_emoji: str, lang: str
) -> tuple[bool, str | None]:
    """Update ticket status emoji in channel name."""
    new_name = _apply_status_to_name(channel.name, status_emoji)
    if new_name == channel.name:
        return True, None
    return await _rename_ticket_with_cooldown(
        channel,
        lang,
        "update ticket status emoji",
        lambda: channel.edit(name=new_name, reason="Clan ticket: update status emoji"),
    )


async def _rename_ticket_prefix(
    channel: discord.TextChannel,
    clan_value: str,
    player_name: str,
    lang: str,
    status_emoji: str = STATUS_OPEN,
) -> tuple[bool, str | None]:
    """Rename ticket to requested format: 🟠přihlášky-{clan}-{player}"""
    clan_key = (clan_value or "").strip().lower()
    if not clan_key:
        return False, None

    slug = _slugify_channel_part(player_name)
    name = f"{status_emoji}přihlášky-{clan_key}-{slug}"

    if len(name) > 100:
        name = name[:100].rstrip("-")
        if not name:
            name = f"{status_emoji}přihlášky-{clan_key}"

    if channel.name == name:
        return True, None

    return await _rename_ticket_with_cooldown(
        channel,
        lang,
        "rename ticket to requested format",
        lambda: channel.edit(name=name, reason="Clan ticket: rename to requested format"),
    )


class Components(discord.ui.LayoutView):
    """Main public panel with clan selection."""

    def __init__(
        self,
        *,
        title: str,
        requirements: str,
        select_options: list[discord.SelectOption],
        clan_entries: list[dict],
    ):
        super().__init__(timeout=None)
        options = select_options or []
        select_disabled = len(options) == 0
        visible_options = options or [
            discord.SelectOption(
                label="Žádné clany nejsou nastaveny",
                value="none",
                description="Přidej clan přes /clan_panel clan",
            )
        ]

        requirements_items: list[discord.ui.TextDisplay] = []
        requirements_text = (requirements or "").strip()
        if requirements_text:
            requirements_items.append(
                discord.ui.TextDisplay(
                    content=requirements_text
                )
            )

        container = discord.ui.Container(
            discord.ui.TextDisplay(content=f"## {title}"),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),

            *requirements_items,
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),

            discord.ui.ActionRow(
                discord.ui.Select(
                    custom_id="clan_select",
                    placeholder="Choose Clan",
                    options=visible_options,
                    disabled=select_disabled,
                )
            ),

        )
        self.add_item(container)


class ScreenshotInstructionsView(discord.ui.LayoutView):
    """Single message with screenshot instructions (NO buttons)."""

    def __init__(self, user_mention: str, clan_value: str, lang: str, guild_id: int | None):
        super().__init__(timeout=None)

        role_mention = _role_mention_for_clan(clan_value, guild_id)

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

    def __init__(self, ticket_channel_id: int, clan_value: str, lang: str, guild_id: int | None):
        super().__init__(timeout=None)

        move_rows: list[discord.ui.ActionRow] = []
        clan_entries = list_clan_definitions(guild_id) if guild_id is not None else []
        move_buttons: list[discord.ui.Button] = []
        for entry in clan_entries:
            clan_key = (entry.get("clan_key") or "").strip().lower()
            if not clan_key:
                continue
            display_name = (entry.get("display_name") or clan_key).strip() or clan_key
            move_buttons.append(
                discord.ui.Button(
                    custom_id=_review_custom_id(
                        f"move_{clan_key}",
                        ticket_channel_id,
                        clan_value,
                        lang,
                    ),
                    label=_move_label(lang, display_name)[:80],
                    style=discord.ButtonStyle.secondary,
                )
            )

        max_per_row = 3
        for start in range(0, len(move_buttons), max_per_row):
            move_rows.append(discord.ui.ActionRow(*move_buttons[start:start + max_per_row]))

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
            discord.ui.ActionRow(
                discord.ui.Button(
                    custom_id=_review_custom_id("vacation", ticket_channel_id, clan_value, lang),
                    label=_t(lang, "btn_vacation"),
                    style=discord.ButtonStyle.secondary,
                ),
                discord.ui.Button(
                    custom_id=_review_custom_id("vacation_restore", ticket_channel_id, clan_value, lang),
                    label=_t(lang, "btn_vacation_restore"),
                    style=discord.ButtonStyle.secondary,
                ),
            ),
            discord.ui.ActionRow(
                discord.ui.Button(
                    custom_id=_review_custom_id("delete", ticket_channel_id, clan_value, lang),
                    label=_t(lang, "btn_delete"),
                    style=discord.ButtonStyle.danger,
                ),
                discord.ui.Button(
                    custom_id=_review_custom_id("kick", ticket_channel_id, clan_value, lang),
                    label=_t(lang, "btn_kick"),
                    style=discord.ButtonStyle.danger,
                )
            ),
            *move_rows,
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
            await interaction.edit_original_response(view=_simple_text_view(_t(lang, "intake_missing")))
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
                mention_everyone=True,
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

        review_role_id = _review_role_id_for_clan(
            self.clan_value, guild.id if guild else None
        )
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
            await interaction.edit_original_response(view=_simple_text_view(_t(lang, "create_no_perms")))
            return
        except discord.HTTPException as e:
            await interaction.edit_original_response(
                view=_simple_text_view(f"{_t(lang, 'create_api_err')} {e}")
            )
            return

        if not ONLY_PERIODIC_TICKET_REFRESH:
            try:
                await _update_ticket_category_label(guild, intake_category.id)
            except Exception:
                pass

        app_id = None
        try:
            app_id = create_clan_application(guild.id, ticket_channel.id, interaction.user.id, lang)
            update_clan_application_form(
                app_id,
                roblox_display,
                (self.hours_per_day.value or "").strip(),
                (self.rebirths.value or "").strip(),
            )
        except Exception:
            # DB errors should not block ticket creation; continue silently.
            pass

        # 1) Set user's nickname on the server to the Roblox username.
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
                nick_diag.append(_t(lang, "nick_owner"))
            if not (bot_member.guild_permissions.manage_nicknames or bot_member.guild_permissions.administrator):
                nick_diag.append(_t(lang, "nick_perm"))
            if bot_member.top_role <= member.top_role and member != guild.owner:
                nick_diag.append(_t(lang, "nick_hierarchy"))

            await member.edit(
                nick=roblox_display_nick,
                reason="Clan application: set nickname to Roblox username",
            )
            nick_ok = True

        except discord.Forbidden:
            nick_err = _t(lang, "nick_forbidden")
        except discord.HTTPException as e:
            nick_err = f"{_t(lang, 'nick_api_err')} {e}"
        except discord.NotFound:
            nick_err = _t(lang, "nick_notfound")

        # Ensure applicant can mention roles/everyone in the ticket (for new tickets)
        try:
            if member:
                await _ensure_member_can_mention_everyone(
                    ticket_channel,
                    member,
                    reason="Clan ticket: enable mentions for applicant (new ticket)",
                )
        except discord.Forbidden:
            pass
        except discord.HTTPException:
            pass

        # 2) Rename ticket channel to: 🟠přihlášky-{clan}-{player}
        rename_ok = False
        rename_err = None
        try:
            rename_ok, rename_err = await _rename_ticket_prefix(
                ticket_channel,
                self.clan_value,
                roblox_display,
                lang,
                STATUS_OPEN,
            )
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

        role_mention = _role_mention_for_clan(self.clan_value, guild.id)
        if role_mention:
            await ticket_channel.send(
                content=role_mention,
                allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
            )

        # Screenshot instructions (no button). Role mention is shown but NOT pinged by bot.
        await ticket_channel.send(
            content="",
            view=ScreenshotInstructionsView(
                interaction.user.mention,
                self.clan_value,
                lang,
                ticket_channel.guild.id if ticket_channel.guild else None,
            ),
            allowed_mentions=discord.AllowedMentions(roles=False, users=True, everyone=False),
        )

        writer = get_writer(self.bot)
        await writer.send_interaction_followup(
            interaction,
            content="",
            view=_simple_text_view(f"{_t(lang, 'ticket_created')} {ticket_channel.mention}"),
            ephemeral=True,
        )


class ClanPanelConfigModal(discord.ui.Modal):
    """Modal for editing clan application panel text."""

    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        title, requirements = ClanPanelCog._get_config_for_guild(guild_id)
        super().__init__(title="Úprava panelu")

        self.panel_title = discord.ui.TextInput(
            label="Nadpis panelu",
            placeholder="Např. CLAN APPLICATIONS",
            default=title[:256],
            required=False,
            max_length=256,
        )
        self.panel_requirements = discord.ui.TextInput(
            label="Text požadavků na panelu",
            placeholder="Zde uveď požadavky pro přihlášky",
            default=requirements[:4000],
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=4000,
        )

        self.add_item(self.panel_title)
        self.add_item(self.panel_requirements)

    async def on_submit(self, interaction: discord.Interaction):
        default_title, default_requirements = ClanPanelCog._default_clan_panel_config()
        final_title = (self.panel_title.value or "").strip() or default_title
        final_requirements = (self.panel_requirements.value or "").strip() or default_requirements
        set_clan_panel_config(self.guild_id, final_title, final_requirements)

        panel_cog = interaction.client.get_cog("ClanPanelCog")
        if panel_cog is not None and hasattr(panel_cog, "_refresh_clan_panels_for_guild"):
            try:
                await panel_cog._refresh_clan_panels_for_guild(self.guild_id)
            except Exception:
                pass

        await interaction.response.send_message(
            content="",
            view=_simple_text_view("Panel byl upraven."),
            ephemeral=True,
        )


class ClanPanelCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        self.clan_panel_group = app_commands.Group(
            name="clan_panel",
            description="Správa panelu pro clan přihlášky",
            default_permissions=discord.Permissions(administrator=True),
        )
        self.clan_panel_group.command(
            name="post", description="Zobrazí panel pro přihlášky do clanu"
        )(self.clan_panel)
        self.clan_panel_group.command(
            name="edit", description="Upraví text panelu s požadavky"
        )(self.clan_panel_edit)
        self.clan_panel_group.command(
            name="clan", description="Správa clanů a rolí pro přihlášky",
        )(self.clan_panel_clan)
        self.clan_panel_group.command(
            name="ticket_reminders",
            description="Ruční kontrola připomínek u ticketů",
        )(self.clan_panel_ticket_reminders)
        self.__cog_app_commands__ = []
        self._ticket_category_refresh_task.start()

    def cog_unload(self):
        return

    @staticmethod
    def _default_clan_panel_config() -> tuple[str, str]:
        return (
            "CLAN APPLICATIONS",
            "- 15SP rebirths +\n- Play 24/7\n- 30% index\n- 10d playtime\n\n"
            "- 15SP rebirthů +\n- Hrát 24/7\n- 30% index\n- 10d playtime",
        )

    @classmethod
    def _get_config_for_guild(cls, guild_id: int | None) -> tuple[str, str]:
        if guild_id is None:
            return cls._default_clan_panel_config()
        db_config = get_clan_panel_config(guild_id)
        if db_config:
            return db_config
        return cls._default_clan_panel_config()

    def _build_panel_view(self, guild_id: int | None) -> Components:
        title, requirements = self._get_config_for_guild(guild_id)
        select_options = _clan_select_options_for_guild(guild_id)
        clan_entries = list_clan_definitions(guild_id) if guild_id is not None else []
        return Components(
            title=title,
            requirements=requirements,
            select_options=select_options,
            clan_entries=clan_entries,
        )

    async def _restore_open_ticket_mentions(self):
        """Ensure all open clan tickets let the applicant mention roles after a restart."""
        for guild in self.bot.guilds:
            processed_channels: set[int] = set()

            try:
                open_apps = list_open_clan_applications(guild.id)
            except Exception:
                open_apps = []

            for app in open_apps:
                channel = guild.get_channel(app.get("channel_id"))
                if not isinstance(channel, discord.TextChannel):
                    continue
                applicant_id = app.get("user_id")
                applicant = guild.get_member(applicant_id)
                if applicant is None:
                    try:
                        applicant = await guild.fetch_member(applicant_id)
                    except discord.NotFound:
                        continue
                    except discord.HTTPException:
                        continue

                await self._ensure_ticket_mentions(
                    channel,
                    applicant,
                    reason="Clan ticket: enable mentions for open ticket (persisted)",
                )

                processed_channels.add(channel.id)

            # Fallback for tickets that might not be stored in DB (older tickets)
            for channel in guild.text_channels:
                if channel.id in processed_channels:
                    continue
                if channel.name.startswith(STATUS_ACCEPTED) or channel.name.startswith(STATUS_DENIED):
                    continue
                applicant_id, _ = _parse_ticket_topic(channel.topic or "")
                if not applicant_id:
                    continue

                applicant = guild.get_member(applicant_id)
                if applicant is None:
                    try:
                        applicant = await guild.fetch_member(applicant_id)
                    except discord.NotFound:
                        continue
                    except discord.HTTPException:
                        continue

                await self._ensure_ticket_mentions(
                    channel,
                    applicant,
                    reason="Clan ticket: enable mentions for open ticket (fallback)",
                )

    async def _refresh_ticket_category_labels(self) -> None:
        for guild in self.bot.guilds:
            category_ids: set[int] = set()
            if TICKET_CATEGORY_ID:
                category_ids.add(TICKET_CATEGORY_ID)
            if VACATION_CATEGORY_ID:
                category_ids.add(VACATION_CATEGORY_ID)

            clan_keys = set(CLAN_CATEGORY_IDS.keys())
            try:
                clan_definitions = list_clan_definitions(guild.id)
            except Exception:
                clan_definitions = []

            for entry in clan_definitions:
                clan_key = (entry.get("clan_key") or "").strip().lower()
                if clan_key:
                    clan_keys.add(clan_key)
                accept_category_id = entry.get("accept_category_id")
                if accept_category_id:
                    category_ids.add(accept_category_id)

            for clan_key in clan_keys:
                accept_category_id = _category_id_for_clan(clan_key, guild.id)
                if accept_category_id:
                    category_ids.add(accept_category_id)

            for category_id in category_ids:
                await _update_ticket_category_label(guild, category_id)

    async def _ensure_ticket_mentions(
        self,
        channel: discord.TextChannel,
        applicant: discord.Member,
        reason: str,
    ) -> None:
        try:
            await _ensure_member_can_mention_everyone(
                channel,
                applicant,
                reason=reason,
            )
        except discord.Forbidden:
            pass
        except discord.HTTPException:
            pass

    async def _maybe_send_ticket_reminder(
        self, guild: discord.Guild, app: dict, now: datetime
    ) -> bool:
        channel = guild.get_channel(app["channel_id"])
        if not isinstance(channel, discord.TextChannel):
            return False
        _, clan_value = _parse_ticket_topic(channel.topic or "")
        if not clan_value:
            return False
        role_mention = _role_mention_for_clan(clan_value, guild.id)
        if not role_mention:
            return False

        last_message_at = _parse_db_datetime(app.get("last_message_at"))
        if last_message_at is None:
            last_message_at = _parse_db_datetime(app.get("created_at"))
        if last_message_at is None:
            return False
        if int(app.get("last_message_by_bot") or 0) == 1:
            return False

        if now - last_message_at < timedelta(hours=1):
            return False

        last_ping_at = _parse_db_datetime(app.get("last_ping_at"))
        if last_ping_at is not None and now - last_ping_at < timedelta(hours=1):
            return False

        lang = app.get("locale") or "en"
        await channel.send(
            content=f"{role_mention} {_t(lang, 'ticket_reminder')}",
            allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
        )
        update_clan_application_last_message(app["id"], now, by_bot=True)
        update_clan_application_last_ping(app["id"], now)
        return True

    async def _run_ticket_reminders_for_guild(self, guild: discord.Guild) -> int:
        now = datetime.utcnow()
        try:
            open_apps = list_open_clan_applications(guild.id)
        except Exception:
            return 0
        sent = 0
        for app in open_apps:
            try:
                if await self._maybe_send_ticket_reminder(guild, app, now):
                    sent += 1
            except (discord.Forbidden, discord.HTTPException):
                continue
        return sent

    @tasks.loop(minutes=11)
    async def _ticket_category_refresh_task(self):
        await self._refresh_ticket_category_labels()

    @_ticket_category_refresh_task.before_loop
    async def _before_ticket_category_refresh_task(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=5)
    async def _ticket_reminder_task(self):
        for guild in self.bot.guilds:
            await self._run_ticket_reminders_for_guild(guild)

    @_ticket_reminder_task.before_loop
    async def _before_ticket_reminder_task(self):
        await self.bot.wait_until_ready()

    async def cog_load(self):
        existing_group = self.bot.tree.get_command("clan_panel", type=discord.AppCommandType.chat_input)
        if existing_group:
            self.bot.tree.remove_command("clan_panel", type=discord.AppCommandType.chat_input)

        try:
            self.bot.tree.add_command(self.clan_panel_group)
        except app_commands.CommandAlreadyRegistered:
            pass

        for guild_id, _, message_id in get_all_clan_application_panels():
            try:
                self.bot.add_view(self._build_panel_view(guild_id), message_id=message_id)
            except Exception:
                continue

        try:
            await self._restore_open_ticket_mentions()
        except Exception:
            pass

    async def cog_unload(self):
        existing_group = self.bot.tree.get_command("clan_panel", type=discord.AppCommandType.chat_input)
        if existing_group:
            self.bot.tree.remove_command("clan_panel", type=discord.AppCommandType.chat_input)

    @app_commands.checks.has_permissions(administrator=True)
    async def clan_panel(self, interaction: discord.Interaction):
        view = self._build_panel_view(interaction.guild.id if interaction.guild else None)
        await interaction.response.send_message(content="", view=view, ephemeral=False)

        try:
            message = await interaction.original_response()
        except discord.HTTPException:
            return

        if interaction.guild:
            add_clan_application_panel(interaction.guild.id, message.channel.id, message.id)
            try:
                self.bot.add_view(view, message_id=message.id)
            except Exception:
                pass

    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def clan_panel_ticket_reminders(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Příkaz lze použít pouze na serveru.", ephemeral=True)
            return

        lang = _lang_for_member(interaction.user) if isinstance(interaction.user, discord.Member) else "cs"
        await interaction.response.defer(ephemeral=True)
        sent = await self._run_ticket_reminders_for_guild(guild)
        view = discord.ui.LayoutView(timeout=None)
        message_key = "ticket_reminder_manual_done" if sent else "ticket_reminder_manual_none"
        view.add_item(discord.ui.TextDisplay(content=_t(lang, message_key).format(count=sent)))
        await interaction.edit_original_response(view=view)

    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def clan_panel_edit(
        self,
        interaction: discord.Interaction,
    ):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Příkaz lze použít pouze na serveru.", ephemeral=True
            )
            return

        await interaction.response.send_modal(
            ClanPanelConfigModal(guild.id)
        )

    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Přidat nebo upravit", value="upsert"),
            app_commands.Choice(name="Smazat", value="delete"),
            app_commands.Choice(name="Seznam", value="list"),
        ]
    )
    @app_commands.describe(
        clan_key="Kód clanu (např. hrot)",
        display_name="Zobrazovaný název",
        description="Popisek clanu",
        us_requirements="Požadavky pro 🇺🇸 verzi clanu",
        cz_requirements="Požadavky pro 🇨🇿 verzi clanu",
        accept_role="Výchozí role, která se má přidat po přijetí",
        accept_role_cz="Role po přijetí pro CZ hráče (podle jazykové role)",
        accept_role_en="Role po přijetí pro EN hráče (podle jazykové role)",
        accept_category="Kategorie, do které se ticket přesune po přijetí",
        review_role="Role, která uvidí ticket a spravuje ho",
        order_position="Pořadí v selectu (nižší číslo = výše)",
    )
    async def clan_panel_clan(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        clan_key: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
        us_requirements: str | None = None,
        cz_requirements: str | None = None,
        accept_role: discord.Role | None = None,
        accept_role_cz: discord.Role | None = None,
        accept_role_en: discord.Role | None = None,
        accept_category: discord.CategoryChannel | None = None,
        review_role: discord.Role | None = None,
        order_position: int | None = None,
    ):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Příkaz lze použít pouze na serveru.", ephemeral=True)
            return

        key_slug = (clan_key or "").strip().lower()
        action_value = action.value

        def _render_view(message: str, entries: list[dict]):
            view = discord.ui.LayoutView(timeout=None)
            items = [discord.ui.TextDisplay(content=f"## {message}")]
            if entries:
                for entry in entries:
                    desc = entry.get("description", "")
                    order_txt = entry.get("sort_order", 0)
                    accept_txt = f"<@&{entry['accept_role_id']}>" if entry.get("accept_role_id") else "Nenastaveno"
                    accept_cz_txt = (
                        f"<@&{entry['accept_role_id_cz']}>" if entry.get("accept_role_id_cz") else "Nenastaveno"
                    )
                    accept_en_txt = (
                        f"<@&{entry['accept_role_id_en']}>" if entry.get("accept_role_id_en") else "Nenastaveno"
                    )
                    accept_cat_txt = (
                        f"<#${entry['accept_category_id']}>".replace("$", "")
                        if entry.get("accept_category_id")
                        else "Nenastaveno"
                    )
                    review_txt = f"<@&{entry['review_role_id']}>" if entry.get("review_role_id") else "Nenastaveno"
                    items.append(
                        discord.ui.TextDisplay(
                            content=(
                                f"**{entry['clan_key']}** — {entry.get('display_name', entry['clan_key'])}\n"
                                f"{desc}\n"
                                f"• Requirements (US): {(entry.get('us_requirements') or 'Nenastaveno')}\n"
                                f"• Requirements (CZ): {(entry.get('cz_requirements') or 'Nenastaveno')}\n"
                                f"• Role po přijetí (default): {accept_txt}\n"
                                f"• Role po přijetí (CZ): {accept_cz_txt}\n"
                                f"• Role po přijetí (EN): {accept_en_txt}\n"
                                f"• Kategorie po přijetí: {accept_cat_txt}\n"
                                f"• Role reviewerů: {review_txt}\n"
                                f"• Pořadí v selectu: {order_txt}"
                            )
                        )
                    )
                    items.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large))
            else:
                items.append(discord.ui.TextDisplay(content="Žádné clany nejsou nastaveny."))

            container = discord.ui.Container(*items)
            view.add_item(container)
            return view

        if action_value == "list":
            entries = list_clan_definitions(guild.id)
            view = _render_view("Nastavené clany", entries)
            await interaction.response.send_message(content="", view=view, ephemeral=True)
            return

        if not key_slug:
            await interaction.response.send_message("Uveď kód clanu (např. hrot, hr2t, tgcm).", ephemeral=True)
            return

        if action_value == "delete":
            delete_clan_definition(guild.id, key_slug)
            entries = list_clan_definitions(guild.id)
            view = _render_view(f"Clan {key_slug} byl odstraněn.", entries)
            await interaction.response.send_message(content="", view=view, ephemeral=True)
            await self._refresh_clan_panels_for_guild(guild.id)
            return

        existing = get_clan_definition(guild.id, key_slug) or {}
        final_display = (display_name or existing.get("display_name") or key_slug).strip()
        final_desc = (description or existing.get("description") or "").strip()
        final_us_requirements = (us_requirements or existing.get("us_requirements") or "").strip()
        final_cz_requirements = (cz_requirements or existing.get("cz_requirements") or "").strip()
        final_accept_role_id = accept_role.id if accept_role else existing.get("accept_role_id")
        final_accept_role_id_cz = (
            accept_role_cz.id if accept_role_cz else existing.get("accept_role_id_cz")
        )
        final_accept_role_id_en = (
            accept_role_en.id if accept_role_en else existing.get("accept_role_id_en")
        )
        final_accept_category_id = (
            accept_category.id if accept_category else existing.get("accept_category_id")
        )
        final_review_role_id = review_role.id if review_role else existing.get("review_role_id")
        existing_sort_order = existing.get("sort_order")
        if order_position is None:
            if existing_sort_order is not None:
                final_sort_order = int(existing_sort_order)
            else:
                final_sort_order = get_next_clan_sort_order(guild.id)
        else:
            final_sort_order = max(int(order_position), 0)

        upsert_clan_definition(
            guild.id,
            key_slug,
            final_display or key_slug,
            final_desc,
            final_us_requirements,
            final_cz_requirements,
            final_accept_role_id,
            final_accept_role_id_cz,
            final_accept_role_id_en,
            final_accept_category_id,
            final_review_role_id,
            final_sort_order,
        )

        entries = list_clan_definitions(guild.id)
        view = _render_view("Clan byl uložen.", entries)
        await interaction.response.send_message(content="", view=view, ephemeral=True)
        await self._refresh_clan_panels_for_guild(guild.id)

    async def _refresh_clan_panels_for_guild(self, guild_id: int | None):
        if guild_id is None:
            return
        for stored_guild_id, channel_id, message_id in get_all_clan_application_panels():
            if stored_guild_id != guild_id:
                continue
            guild = self.bot.get_guild(stored_guild_id)
            if guild is None:
                continue
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                continue
            try:
                message = await channel.fetch_message(message_id)
            except discord.HTTPException:
                continue
            try:
                await message.edit(view=self._build_panel_view(guild_id))
                self.bot.add_view(self._build_panel_view(guild_id), message_id=message_id)
            except discord.HTTPException:
                continue

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

            ticket_channel = guild.get_channel(channel_id)
            if ticket_channel is None or not isinstance(ticket_channel, discord.TextChannel):
                await interaction.response.send_message(_t(lang, "ticket_missing"), ephemeral=True)
                return

            _, topic_clan = _parse_ticket_topic(ticket_channel.topic or "")
            if topic_clan:
                clan_value = topic_clan

            if not _is_reviewer(clicker, clan_value):
                await interaction.response.send_message(_t(lang, "no_perm"), ephemeral=True)
                return

            await interaction.response.send_message(
                content="",
                view=AdminDecisionView(channel_id, clan_value, lang, guild.id),
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

            if action == "kick":
                await interaction.response.defer(ephemeral=True)

                applicant_id, topic_clan = _parse_ticket_topic(ticket_channel.topic or "")
                if topic_clan:
                    clan_value = topic_clan

                if not applicant_id:
                    await interaction.edit_original_response(
                        view=_simple_text_view(_t(lang, "cant_get_applicant"))
                    )
                    return

                try:
                    applicant = guild.get_member(applicant_id) or await guild.fetch_member(applicant_id)
                except discord.NotFound:
                    await interaction.edit_original_response(
                        view=_simple_text_view(_t(lang, "applicant_left"))
                    )
                    return

                candidate_role_ids = _candidate_member_role_ids_for_clan(clan_value, guild.id)
                roles_to_remove = [
                    role for role in applicant.roles if role.id in set(candidate_role_ids)
                ]

                if roles_to_remove:
                    try:
                        await applicant.remove_roles(
                            *roles_to_remove,
                            reason=f"Clan kick: remove roles for {clan_value} ({clicker})",
                        )
                        role_info = _t(
                            lang,
                            "kick_role_removed",
                            roles=", ".join(role.mention for role in roles_to_remove),
                        )
                    except discord.Forbidden:
                        role_info = _t(lang, "kick_role_forbidden")
                    except discord.HTTPException:
                        role_info = _t(lang, "kick_role_failed")
                else:
                    role_info = _t(lang, "kick_role_none")

                app_record = None
                try:
                    app_record = get_clan_application_by_channel(guild.id, channel_id)
                except Exception:
                    app_record = None

                ticket_info = _t(lang, "kick_ticket_deleted")
                category_id = ticket_channel.category_id
                deleted_ok = False
                logging.getLogger("botdc").info(
                    "Mazání clan ticketu (kick) vyvolal %s (%s) pro %s (%s) v kanálu %s (%s).",
                    clicker,
                    clicker.id,
                    applicant,
                    applicant.id,
                    ticket_channel.name,
                    ticket_channel.id,
                )
                try:
                    await ticket_channel.delete(reason=f"Clan kick by {clicker} ({applicant})")
                    deleted_ok = True
                except discord.Forbidden:
                    ticket_info = _t(lang, "kick_ticket_delete_forbidden")
                except discord.HTTPException:
                    ticket_info = _t(lang, "kick_ticket_delete_failed")

                if deleted_ok:
                    if category_id and not ONLY_PERIODIC_TICKET_REFRESH:
                        try:
                            await _update_ticket_category_label(guild, category_id)
                        except Exception:
                            pass
                    try:
                        clear_ticket_last_rename(channel_id)
                        clear_ticket_last_move(channel_id)
                    except Exception:
                        pass

                if app_record:
                    try:
                        mark_clan_application_deleted(app_record["id"])
                    except Exception:
                        pass

                response_lines = [_t(lang, "kick_done"), role_info, ticket_info]
                await interaction.edit_original_response(
                    view=_simple_text_view("\n".join(response_lines))
                )
                return

            if action == "delete":
                await interaction.response.send_message(_t(lang, "deleted_ephemeral"), ephemeral=True)
                app_record = None
                category_id = ticket_channel.category_id
                deleted_ok = False
                try:
                    app_record = get_clan_application_by_channel(guild.id, channel_id)
                    logging.getLogger("botdc").info(
                        "Mazání clan ticketu vyvolal %s (%s) v kanálu %s (%s).",
                        clicker,
                        clicker.id,
                        ticket_channel.name,
                        ticket_channel.id,
                    )
                    await ticket_channel.delete(reason=f"Clan ticket deleted by {clicker}")
                    deleted_ok = True
                except discord.Forbidden:
                    await interaction.edit_original_response(
                        view=_simple_text_view(_t(lang, "delete_no_perms"))
                    )
                    return
                except discord.HTTPException as e:
                    await interaction.edit_original_response(
                        view=_simple_text_view(f"{_t(lang, 'delete_api_err')} {e}")
                    )
                    return

                if deleted_ok:
                    if category_id and not ONLY_PERIODIC_TICKET_REFRESH:
                        try:
                            await _update_ticket_category_label(guild, category_id)
                        except Exception:
                            pass
                    try:
                        clear_ticket_last_rename(channel_id)
                        clear_ticket_last_move(channel_id)
                    except Exception:
                        pass

                if app_record:
                    try:
                        mark_clan_application_deleted(app_record["id"])
                    except Exception:
                        pass

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

            if action == "vacation":
                existing = get_clan_ticket_vacation(channel_id)
                if existing:
                    await interaction.response.send_message(_t(lang, "vacation_already"), ephemeral=True)
                    return

                vacation_role = guild.get_role(VACATION_ROLE_ID)
                if vacation_role is None:
                    await interaction.response.send_message(_t(lang, "vacation_role_missing"), ephemeral=True)
                    return

                vacation_category = guild.get_channel(VACATION_CATEGORY_ID)
                if vacation_category is None or not isinstance(
                    vacation_category, discord.CategoryChannel
                ):
                    await interaction.response.send_message(
                        _t(lang, "vacation_missing_category"), ephemeral=True
                    )
                    return

                candidate_role_ids = _candidate_member_role_ids_for_clan(
                    clan_value, guild.id if guild else None
                )
                roles_to_remove = [role for role in applicant.roles if role.id in candidate_role_ids]
                removed_role_ids = [role.id for role in roles_to_remove]

                if roles_to_remove:
                    try:
                        await applicant.remove_roles(
                            *roles_to_remove,
                            reason=f"Clan vacation: remove clan roles for {clan_value}",
                        )
                    except discord.Forbidden:
                        await interaction.response.send_message(
                            _t(lang, "vacation_remove_forbidden"), ephemeral=True
                        )
                        return
                    except discord.HTTPException as e:
                        await interaction.response.send_message(
                            f"{_t(lang, 'vacation_remove_api_err')} {e}", ephemeral=True
                        )
                        return

                if vacation_role not in applicant.roles:
                    try:
                        await applicant.add_roles(
                            vacation_role,
                            reason=f"Clan vacation: add vacation role for {clan_value}",
                        )
                    except discord.Forbidden:
                        if roles_to_remove:
                            try:
                                await applicant.add_roles(
                                    *roles_to_remove,
                                    reason="Clan vacation: rollback after add failure",
                                )
                            except discord.HTTPException:
                                pass
                        await interaction.response.send_message(
                            _t(lang, "vacation_add_forbidden"), ephemeral=True
                        )
                        return
                    except discord.HTTPException as e:
                        if roles_to_remove:
                            try:
                                await applicant.add_roles(
                                    *roles_to_remove,
                                    reason="Clan vacation: rollback after add failure",
                                )
                            except discord.HTTPException:
                                pass
                        await interaction.response.send_message(
                            f"{_t(lang, 'vacation_add_api_err')} {e}", ephemeral=True
                        )
                        return

                prev_category_id = (
                    ticket_channel.category.id if ticket_channel.category else None
                )

                try:
                    await ticket_channel.edit(
                        category=vacation_category,
                        reason=f"Clan vacation: moved by {clicker}",
                    )
                except discord.Forbidden:
                    try:
                        await applicant.remove_roles(
                            vacation_role,
                            reason="Clan vacation: rollback after move failure",
                        )
                        if roles_to_remove:
                            await applicant.add_roles(
                                *roles_to_remove,
                                reason="Clan vacation: rollback after move failure",
                            )
                    except discord.HTTPException:
                        pass
                    await interaction.response.send_message(
                        _t(lang, "vacation_move_forbidden"), ephemeral=True
                    )
                    return
                except discord.HTTPException as e:
                    try:
                        await applicant.remove_roles(
                            vacation_role,
                            reason="Clan vacation: rollback after move failure",
                        )
                        if roles_to_remove:
                            await applicant.add_roles(
                                *roles_to_remove,
                                reason="Clan vacation: rollback after move failure",
                            )
                    except discord.HTTPException:
                        pass
                    await interaction.response.send_message(
                        f"{_t(lang, 'vacation_move_api_err')} {e}", ephemeral=True
                    )
                    return

                save_clan_ticket_vacation(
                    guild.id,
                    channel_id,
                    applicant.id,
                    clan_value,
                    prev_category_id,
                    removed_role_ids,
                    VACATION_ROLE_ID,
                )

                if not ONLY_PERIODIC_TICKET_REFRESH:
                    try:
                        await _update_ticket_category_label(guild, vacation_category.id)
                        if prev_category_id:
                            await _update_ticket_category_label(guild, prev_category_id)
                    except Exception:
                        pass

                await interaction.response.send_message(_t(lang, "vacation_set"), ephemeral=True)
                return

            if action == "vacation_restore":
                record = get_clan_ticket_vacation(channel_id)
                if not record:
                    await interaction.response.send_message(
                        _t(lang, "vacation_restore_missing"), ephemeral=True
                    )
                    return

                vacation_category_id = ticket_channel.category_id
                removed_role_ids = record.get("removed_role_ids") or []
                prev_category_id = record.get("prev_category_id")
                vacation_role_id = record.get("vacation_role_id") or VACATION_ROLE_ID
                vacation_role = guild.get_role(vacation_role_id)

                prev_category = None
                if prev_category_id:
                    prev_category = guild.get_channel(prev_category_id)
                    if prev_category is None or not isinstance(
                        prev_category, discord.CategoryChannel
                    ):
                        await interaction.response.send_message(
                            _t(lang, "vacation_restore_category_missing"), ephemeral=True
                        )
                        return

                roles_to_restore = [
                    guild.get_role(role_id)
                    for role_id in removed_role_ids
                    if guild.get_role(role_id) is not None
                ]

                if vacation_role and vacation_role in applicant.roles:
                    try:
                        await applicant.remove_roles(
                            vacation_role,
                            reason=f"Clan vacation: restore roles for {clan_value}",
                        )
                    except discord.Forbidden:
                        await interaction.response.send_message(
                            _t(lang, "vacation_remove_forbidden"), ephemeral=True
                        )
                        return
                    except discord.HTTPException as e:
                        await interaction.response.send_message(
                            f"{_t(lang, 'vacation_remove_api_err')} {e}", ephemeral=True
                        )
                        return

                if roles_to_restore:
                    try:
                        await applicant.add_roles(
                            *roles_to_restore,
                            reason=f"Clan vacation: restore clan roles for {clan_value}",
                        )
                    except discord.Forbidden:
                        await interaction.response.send_message(
                            _t(lang, "vacation_add_forbidden"), ephemeral=True
                        )
                        return
                    except discord.HTTPException as e:
                        await interaction.response.send_message(
                            f"{_t(lang, 'vacation_add_api_err')} {e}", ephemeral=True
                        )
                        return

                if prev_category:
                    try:
                        await ticket_channel.edit(
                            category=prev_category,
                            reason=f"Clan vacation: restored by {clicker}",
                        )
                    except discord.Forbidden:
                        if vacation_role:
                            try:
                                await applicant.add_roles(
                                    vacation_role,
                                    reason="Clan vacation: rollback after move failure",
                                )
                            except discord.HTTPException:
                                pass
                        if roles_to_restore:
                            try:
                                await applicant.remove_roles(
                                    *roles_to_restore,
                                    reason="Clan vacation: rollback after move failure",
                                )
                            except discord.HTTPException:
                                pass
                        await interaction.response.send_message(
                            _t(lang, "vacation_move_forbidden"), ephemeral=True
                        )
                        return
                    except discord.HTTPException as e:
                        if vacation_role:
                            try:
                                await applicant.add_roles(
                                    vacation_role,
                                    reason="Clan vacation: rollback after move failure",
                                )
                            except discord.HTTPException:
                                pass
                        if roles_to_restore:
                            try:
                                await applicant.remove_roles(
                                    *roles_to_restore,
                                    reason="Clan vacation: rollback after move failure",
                                )
                            except discord.HTTPException:
                                pass
                        await interaction.response.send_message(
                            f"{_t(lang, 'vacation_move_api_err')} {e}", ephemeral=True
                        )
                        return

                delete_clan_ticket_vacation(channel_id)
                if not ONLY_PERIODIC_TICKET_REFRESH:
                    try:
                        if prev_category_id:
                            await _update_ticket_category_label(guild, prev_category_id)
                        if vacation_category_id and vacation_category_id != prev_category_id:
                            await _update_ticket_category_label(guild, vacation_category_id)
                    except Exception:
                        pass
                await interaction.response.send_message(
                    _t(lang, "vacation_restored"), ephemeral=True
                )
                return

            if action.startswith("move_"):
                now_ts = int(time.time())
                last_move_ts = get_ticket_last_move(channel_id)
                if last_move_ts is not None:
                    delta = now_ts - last_move_ts
                    if delta < 600:
                        remaining = _format_cooldown_remaining(600 - delta)
                        await interaction.response.send_message(
                            _t(lang, "move_cooldown_remaining").format(remaining=remaining),
                            ephemeral=True,
                        )
                        return

                target_clan = action[5:].strip().lower()
                if not target_clan:
                    await interaction.response.send_message(_t(lang, "unknown_action"), ephemeral=True)
                    return
                current_clan = (clan_value or "").strip().lower()
                target_display = _display_name_for_clan(target_clan, guild.id)
                current_display = _display_name_for_clan(current_clan, guild.id)

                if current_clan == target_clan:
                    await interaction.response.send_message(
                        _t(lang, "move_same").format(clan=current_display), ephemeral=True
                    )
                    return

                applicant_id, _ = _parse_ticket_topic(ticket_channel.topic or "")
                if not applicant_id:
                    await interaction.response.send_message(_t(lang, "cant_get_applicant"), ephemeral=True)
                    return

                try:
                    writer = get_writer(self.bot)
                    await writer.edit_channel(
                        ticket_channel,
                        topic=f"clan_applicant={applicant_id};clan={target_clan}",
                        reason=f"Clan ticket: move to {target_clan}",
                        priority=WritePriority.NORMAL,
                    )
                except discord.Forbidden:
                    await interaction.response.send_message(_t(lang, "move_no_perms"), ephemeral=True)
                    return
                except discord.HTTPException as e:
                    await interaction.response.send_message(
                        f"{_t(lang, 'move_api_err')} {e}", ephemeral=True
                    )
                    return

                try:
                    await _swap_review_role_visibility(ticket_channel, current_clan, target_clan)
                except discord.Forbidden:
                    await interaction.response.send_message(_t(lang, "perm_no_perms"), ephemeral=True)
                    return
                except discord.HTTPException as e:
                    await interaction.response.send_message(
                        f"{_t(lang, 'perm_api_err')} {e}", ephemeral=True
                    )
                    return

                prev_category_id = ticket_channel.category_id
                target_category_id = _category_id_for_clan(target_clan, guild.id)
                try:
                    moved = await _move_ticket_to_clan_category(ticket_channel, target_clan)
                except Exception:
                    moved = False

                if moved and target_category_id and not ONLY_PERIODIC_TICKET_REFRESH:
                    try:
                        await _update_ticket_category_label(guild, target_category_id)
                        if prev_category_id and prev_category_id != target_category_id:
                            await _update_ticket_category_label(guild, prev_category_id)
                    except Exception:
                        pass

                status_emoji = _status_emoji_from_name(ticket_channel.name)
                app_record = get_clan_application_by_channel(guild.id, channel_id)
                player_name = ""
                if app_record:
                    player_name = (app_record.get("roblox_nick") or "").strip()
                if not player_name:
                    player_name = applicant.display_name

                rename_ok = False
                rename_err = None
                try:
                    rename_ok, rename_err = await _rename_ticket_prefix(
                        ticket_channel,
                        target_clan,
                        player_name,
                        lang,
                        status_emoji,
                    )
                except discord.Forbidden:
                    await interaction.response.send_message(_t(lang, "rename_no_perms"), ephemeral=True)
                    return
                except discord.HTTPException as e:
                    await interaction.response.send_message(
                        f"{_t(lang, 'rename_api_err')} {e}", ephemeral=True
                    )
                    return

                await ticket_channel.send(
                    _t(lang, "move_done").format(clan=target_display)
                )
                response_text = _t(lang, "move_done").format(clan=target_display)
                if not rename_ok and rename_err:
                    response_text = f"{response_text}\n{_t(lang, 'move_rename_skipped').format(reason=rename_err)}"
                set_ticket_last_move(channel_id, now_ts)
                await interaction.response.send_message(response_text, ephemeral=True)
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

                last_rename_ts = get_ticket_last_rename(channel_id)
                now_ts = int(time.time())
                if last_rename_ts is not None:
                    delta = now_ts - last_rename_ts
                    if delta < 600:
                        remaining = 600 - delta
                        remaining_str = _format_cooldown_remaining(remaining)
                        await interaction.response.send_message(
                            _t(lang, "accept_rename_cooldown").format(remaining=remaining_str),
                            ephemeral=True,
                        )
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
                prev_category_id = ticket_channel.category_id
                target_category_id = _category_id_for_clan(
                    clan_value, guild.id if guild else None
                )
                try:
                    moved = await _move_ticket_to_clan_category(ticket_channel, clan_value)
                except Exception:
                    moved = False

                if moved and target_category_id and not ONLY_PERIODIC_TICKET_REFRESH:
                    try:
                        await _update_ticket_category_label(guild, target_category_id)
                        if prev_category_id and prev_category_id != target_category_id:
                            await _update_ticket_category_label(guild, prev_category_id)
                    except Exception:
                        pass

                # Status emoji -> 🟢
                rename_ok = True
                rename_err = None
                try:
                    rename_ok, rename_err = await _set_ticket_status(
                        ticket_channel, STATUS_ACCEPTED, lang
                    )
                except Exception:
                    pass

                try:
                    app_record = get_clan_application_by_channel(guild.id, channel_id)
                    if app_record is None:
                        app_id = create_clan_application(guild.id, channel_id, applicant.id, lang)
                    else:
                        app_id = app_record["id"]
                    set_clan_application_status(app_id, "accepted")
                except Exception:
                    pass

                await ticket_channel.send(
                    f"{_t(lang, 'accepted_msg')} {clicker.mention}. {_t(lang, 'accepted_role_added')} {role.name}.",
                    allowed_mentions=discord.AllowedMentions(roles=False, users=True, everyone=False),
                )
                response_text = _t(lang, "accepted_ephemeral")
                if not rename_ok and rename_err:
                    response_text = f"{response_text}\n{rename_err}"
                await interaction.response.send_message(response_text, ephemeral=True)
                return

            if action == "deny":
                # Status emoji -> 🔴
                rename_ok = True
                rename_err = None
                try:
                    rename_ok, rename_err = await _set_ticket_status(
                        ticket_channel, STATUS_DENIED, lang
                    )
                except Exception:
                    pass

                try:
                    app_record = get_clan_application_by_channel(guild.id, channel_id)
                    if app_record is None:
                        app_id = create_clan_application(guild.id, channel_id, applicant.id, lang)
                    else:
                        app_id = app_record["id"]
                    set_clan_application_status(app_id, "rejected")
                except Exception:
                    pass

                await ticket_channel.send(f"{_t(lang, 'denied_msg')} {clicker.mention}.")
                response_text = _t(lang, "denied_ephemeral")
                if not rename_ok and rename_err:
                    response_text = f"{response_text}\n{rename_err}"
                await interaction.response.send_message(response_text, ephemeral=True)
                return

            await interaction.response.send_message(_t(lang, "unknown_action"), ephemeral=True)
            return

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        remove_clan_application_panel(payload.message_id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return
        applicant_id, _ = _parse_ticket_topic(message.channel.topic or "")
        if applicant_id is None:
            return
        app_record = get_open_application_by_channel(message.channel.id)
        if not app_record:
            return
        update_clan_application_last_message(app_record["id"], by_bot=message.author.bot)
