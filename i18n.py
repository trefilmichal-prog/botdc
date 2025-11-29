import discord


def _get_czech_locale() -> discord.Locale:
    """Return the closest available Czech locale supported by the library.

    Older discord.py versions expose the locale as ``Locale.czech`` instead of
    ``Locale.cs`` (or may omit the alias entirely), which previously caused an
    ``AttributeError`` on import. This helper looks for either attribute and
    falls back to ``Locale.try_value("cs")`` before ultimately defaulting to
    English.
    """

    for name in ("cs", "czech"):
        locale = getattr(discord.Locale, name, None)
        if locale:
            return locale

    fallback = discord.Locale.try_value("cs")
    return fallback or discord.Locale.en_US


DEFAULT_LOCALE = _get_czech_locale()


def normalize_locale(raw_locale: str | discord.Locale | None) -> discord.Locale:
    if isinstance(raw_locale, discord.Locale):
        value = raw_locale.value
    elif raw_locale is None:
        return DEFAULT_LOCALE
    else:
        value = str(raw_locale)

    if value.startswith("en"):
        return discord.Locale.en_US
    if value.startswith("cs") or value == "czech":
        return DEFAULT_LOCALE
    return DEFAULT_LOCALE


def get_interaction_locale(interaction: discord.Interaction) -> discord.Locale:
    return normalize_locale(interaction.locale or getattr(interaction, "guild_locale", None))


def get_message_locale(message: discord.Message) -> discord.Locale:
    guild_locale = getattr(message.guild, "preferred_locale", None) if message.guild else None
    return normalize_locale(guild_locale)


STRINGS: dict[str, dict[str, str]] = {
    "cannot_moderate": {
        "cs": "Nemůžeš moderovat uživatele s vyšší nebo stejnou rolí.",
        "en": "You cannot moderate a user with the same or higher role.",
    },
    "bot_cannot_moderate": {
        "cs": "Nemohu provést akci kvůli hierarchii rolí.",
        "en": "I cannot perform this action because of role hierarchy.",
    },
    "ticket_removed": {
        "cs": "Ticket {channel} byl smazán.",
        "en": "Ticket {channel} was deleted.",
    },
    "ticket_remove_forbidden": {
        "cs": "Ticket {channel} se nepodařilo smazat kvůli oprávněním.",
        "en": "I could not delete ticket {channel} because of permissions.",
    },
    "ticket_remove_failed": {
        "cs": "Při mazání ticketu {channel} došlo k chybě.",
        "en": "An error occurred while deleting ticket {channel}.",
    },
    "ticket_mark_deleted": {
        "cs": "Původní ticket se nenašel, označuji ho jako smazaný.",
        "en": "Original ticket not found; marking it as deleted.",
    },
    "ban_success": {
        "cs": "\N{HAMMER} {user} byl/a zabanován/a. Důvod: {reason}.",
        "en": "\N{HAMMER} {user} has been banned. Reason: {reason}.",
    },
    "mute_success": {
        "cs": "\N{SPEAKER WITH CANCELLATION STROKE} {user} umlčen/a na {minutes} minut. Důvod: {reason}.",
        "en": "\N{SPEAKER WITH CANCELLATION STROKE} {user} muted for {minutes} minutes. Reason: {reason}.",
    },
    "nickname_set": {
        "cs": "\N{MEMO} Přezdívka {user} nastavena na '{nickname}'.",
        "en": "\N{MEMO} Nickname for {user} set to '{nickname}'.",
    },
    "nickname_cleared": {
        "cs": "\N{MEMO} Přezdívka {user} byla smazána.",
        "en": "\N{MEMO} Nickname for {user} has been cleared.",
    },
    "kick_modal_title": {
        "cs": "Důvod kicku",
        "en": "Kick reason",
    },
    "kick_modal_label": {
        "cs": "Důvod kicku",
        "en": "Reason for kick",
    },
    "kick_modal_placeholder": {
        "cs": "Napiš stručně, proč hráče kickuješ",
        "en": "Briefly explain why you are kicking the player",
    },
    "guild_only": {
        "cs": "Tento příkaz lze použít pouze na serveru.",
        "en": "This command can only be used in a server.",
    },
    "user_missing": {
        "cs": "Uživatel už není na serveru.",
        "en": "The user is no longer on the server.",
    },
    "kick_success": {
        "cs": "\N{WAVING HAND SIGN} {user} byl/a vyhozen/a. Důvod: {reason}.",
        "en": "\N{WAVING HAND SIGN} {user} has been kicked. Reason: {reason}.",
    },
    "reason_unknown": {
        "cs": "neuveden",
        "en": "not provided",
    },
    "leaderboard_empty": {
        "cs": "Nikdo zatím nemá žádná data pro tento žebříček.",
        "en": "No one has any data for this leaderboard yet.",
    },
    "leaderboard_title_coins": {
        "cs": "Žebříček – Coiny",
        "en": "Leaderboard – Coins",
    },
    "leaderboard_title_messages": {
        "cs": "Žebříček – Zprávy",
        "en": "Leaderboard – Messages",
    },
    "panel_title": {
        "cs": "Žebříček",
        "en": "Leaderboard",
    },
    "panel_section_coins": {
        "cs": "Top Coiny",
        "en": "Top Coins",
    },
    "panel_section_messages": {
        "cs": "Top Zprávy",
        "en": "Top Messages",
    },
    "panel_no_data": {
        "cs": "Žádná data pro tento žebříček.",
        "en": "No data for this leaderboard.",
    },
    "panel_footer": {
        "cs": "Panel se aktualizuje automaticky každých 5 minut.",
        "en": "The panel updates automatically every 5 minutes.",
    },
    "clan_setup_role_missing": {
        "cs": "Roli s ID `{role_id}` jsem na tomto serveru nenašel.",
        "en": "I couldn't find a role with ID `{role_id}` on this server.",
    },
    "clan_setup_sent": {
        "cs": "Zpráva s přehledem členů byla odeslána do {channel}.",
        "en": "The clan member overview has been sent to {channel}.",
    },
    "leaderboard_setup_sent": {
        "cs": "Žebříček byl odeslán do {channel}.",
        "en": "The leaderboard has been sent to {channel}.",
    },
    "clan_panel_title": {
        "cs": "Členové klanu",
        "en": "Clan members",
    },
    "clan_panel_empty": {
        "cs": "Zatím nikdo nemá tuto roli.",
        "en": "No one has this role yet.",
    },
    "clan_panel_role_missing": {
        "cs": "Roli pro klan jsem na serveru nenašel. Zkontroluj hodnotu CLAN_MEMBER_ROLE_ID.",
        "en": "I couldn't find the clan role on the server. Check CLAN_MEMBER_ROLE_ID.",
    },
    "panel_refresh_error": {
        "cs": "[panel_refresh_loop] Chyba při obnově panelů: {error}",
        "en": "[panel_refresh_loop] Error while refreshing panels: {error}",
    },
    "mention_prompt_missing": {
        "cs": "Ahoj! Příště mi rovnou napiš otázku, ať ti můžu věštit budoucnost. 😊",
        "en": "Hi! Please include your question next time so I can tell your future. 😊",
    },
    "prophecy_prompt_message": {
        "cs": (
            "Jsi veselý český věštec pro hráče Roblox hry Rebirth Champions Ultimate."
            " Odpovídej vždy česky, ve 1–2 věty maximálně, s lehkým humorem a konkrétním tipem na další postup."
            " Vyhýbej se vulgaritám a udrž tón přátelský pro komunitu Discordu."
            " Otázka hráče: {question}"
        ),
        "en": (
            "You are a cheerful English-speaking fortune teller for Roblox game Rebirth Champions Ultimate players."
            " Always answer in English in at most 1–2 sentences with light humor and a concrete next-step tip."
            " Avoid profanity and keep a friendly Discord tone."
            " Player question: {question}"
        ),
    },
    "prophecy_prompt_slash": {
        "cs": (
            "Jsi veselý český věštec pro hráče Roblox hry Rebirth Champions Ultimate."
            " Odpovídej vždy česky, ve 2–3 větách, s lehkým humorem a konkrétním tipem na další postup."
            " Vyhýbej se vulgaritám a udrž tón přátelský pro komunitu Discordu."
        ),
        "en": (
            "You are a cheerful English-speaking fortune teller for Roblox game Rebirth Champions Ultimate players."
            " Always answer in English in 2–3 sentences with light humor and a concrete next-step tip."
            " Avoid profanity and keep a friendly Discord tone."
        ),
    },
    "prophecy_prompt_general": {
        "cs": " Dej obecnou předpověď pro nejbližší run.",
        "en": " Give a general prediction for the next run.",
    },
    "prophecy_unavailable": {
        "cs": "Nemohu se momentálně spojit s Ollamou. Zkus to prosím za chvíli.",
        "en": "I cannot reach Ollama right now. Please try again soon.",
    },
    "prophecy_title": {
        "cs": "🔮 Roblox věštba",
        "en": "🔮 Roblox prophecy",
    },
    "profile_title": {
        "cs": "Profil – {name}",
        "en": "Profile – {name}",
    },
    "profile_level": {"cs": "Level", "en": "Level"},
    "profile_exp": {"cs": "Exp", "en": "Exp"},
    "profile_coins": {"cs": "Coiny", "en": "Coins"},
    "profile_messages": {"cs": "Zprávy", "en": "Messages"},
    "guild_text_only": {
        "cs": "Tento příkaz lze použít jen v textovém kanálu.",
        "en": "This command can only be used in a text channel.",
    },
    "wood_panel_title": {
        "cs": "Suroviny – těžba dřeva (Ultimate Rebirth Champions)",
        "en": "Materials – wood mining (Ultimate Rebirth Champions)",
    },
    "wood_panel_description": {
        "cs": "Přehled, kolik čeho je potřeba a kolik už bylo odevzdáno.\nK nahlášení použij tlačítko níže.",
        "en": "Overview of required materials and what has been delivered.\nUse the button below to report your delivery.",
    },
    "wood_panel_resources_title": {"cs": "Přehled dřev", "en": "Wood overview"},
    "wood_panel_no_data_title": {"cs": "Žádná data", "en": "No data"},
    "wood_panel_no_data_body": {
        "cs": "Zatím není nastaveno, kolik čeho je potřeba. Použij `/set_need`.",
        "en": "No requirements are set yet. Use `/set_need` to configure them.",
    },
    "wood_panel_resource_field": {
        "cs": "Odevzdáno: **{delivered}/{required}** (zbývá {remaining})",
        "en": "Delivered: **{delivered}/{required}** (remaining {remaining})",
    },
    "wood_panel_empty_description": {
        "cs": "Zde bude přehled, kolik je potřeba kterého dřeva a kolik už je odevzdáno.\nK nahlášení použij tlačítko níže.",
        "en": "This will show how much of each wood type is needed and delivered.\nUse the button below to report your delivery.",
    },
    "wood_panel_no_need": {
        "cs": "Zatím žádná potřeba není nastavená. Použij `/set_need`.",
        "en": "No requirements are set yet. Use `/set_need`.",
    },
    "wood_panel_created": {
        "cs": "Panel vytvořen v tomto kanálu.",
        "en": "The panel has been created in this channel.",
    },
    "wood_need_set": {
        "cs": "Nastavena potřeba pro **{resource}**: **{required}** kusů.",
        "en": "Requirement set for **{resource}**: **{required}** pieces.",
    },
    "wood_need_reset_all": {
        "cs": "Resetovány všechny potřeby a všechna odevzdaná množství.",
        "en": "All requirements and delivered amounts have been reset.",
    },
    "wood_need_reset_single": {
        "cs": "Resetována potřeba pro **{resource}**.",
        "en": "Requirement reset for **{resource}**.",
    },
    "wood_resources_empty": {
        "cs": "Zatím není nastaveno, kolik čeho je potřeba.",
        "en": "No requirements have been configured yet.",
    },
    "wood_resources_title": {
        "cs": "Aktuální stav surovin",
        "en": "Current material status",
    },
    "wood_resources_field": {
        "cs": "Odevzdáno: **{delivered}/{required}** (zbývá {remaining})",
        "en": "Delivered: **{delivered}/{required}** (remaining {remaining})",
    },
    "wood_ticket_foreign": {
        "cs": "Toto je ticket jiného hráče. Jen vlastník ticketu sem může zadat číslo.",
        "en": "This ticket belongs to another player. Only the owner can submit a number here.",
    },
    "wood_ticket_invalid_amount": {
        "cs": "Napiš prosím jen **kladné celé číslo** (např. `64`).",
        "en": "Please enter a **positive whole number** (e.g., `64`).",
    },
    "wood_ticket_logged": {
        "cs": "Zaznamenáno: {user} – **{amount} × {resource}**.",
        "en": "Logged: {user} – **{amount} × {resource}**.",
    },
    "wood_ticket_channel_delete": {
        "cs": "Ticket kanál se nyní odstraní.",
        "en": "The ticket channel will now be deleted.",
    },
    "wood_reminder_title": {
        "cs": "Potřebné materiály",
        "en": "Required materials",
    },
    "wood_reminder_description": {
        "cs": "Některé materiály stále chybí, budeme rádi za tvoji pomoc.",
        "en": "Some materials are still missing; we would appreciate your help.",
    },
    "wood_reminder_field": {
        "cs": "Potřeba: **{required}**\nOdevzdáno: **{delivered}**\nZbývá: **{remaining}**",
        "en": "Needed: **{required}**\nDelivered: **{delivered}**\nRemaining: **{remaining}**",
    },
    "wood_reminder_intro": {
        "cs": "Ahoj, delší dobu jsi nic neodevzdal a **stále nám chybí suroviny**.",
        "en": "Hi, you haven't delivered anything for a while and **we still need materials**.",
    },
    "wood_ticket_selected": {
        "cs": "Vybral jsi: **{resource}**.",
        "en": "You selected: **{resource}**.",
    },
    "wood_ticket_enter_amount": {
        "cs": "Napiš do tohoto ticketu **jen číslo** (množství), např. `64`.",
        "en": "Enter **only a number** (amount) in this ticket, e.g., `64`.",
    },
    "wood_ticket_will_delete": {
        "cs": "Po zadání se ticket uloží a kanál smaže.",
        "en": "After you submit the number, the ticket will be saved and the channel deleted.",
    },
    "wood_ticket_select_placeholder": {
        "cs": "Vyber typ dřeva",
        "en": "Choose the wood type",
    },
    "wood_ticket_button_label": {
        "cs": "Vytvořit ticket na odevzdání dřeva",
        "en": "Create a wood delivery ticket",
    },
    "wood_ticket_audit": {
        "cs": "Ticket na dřevo od {user} ({user_id})",
        "en": "Wood ticket from {user} ({user_id})",
    },
    "wood_ticket_title": {
        "cs": "Ticket – odevzdání dřeva",
        "en": "Ticket – wood delivery",
    },
    "wood_ticket_instructions": {
        "cs": "1) V dropdown menu níže vyber typ dřeva.\n2) Pak napiš **jen číslo** (množství).\n3) Po zadání čísla se ticket uloží a kanál smaže.",
        "en": "1) Choose the wood type in the dropdown below.\n2) Then enter **only a number** (amount).\n3) After submitting the number, the ticket will be saved and the channel deleted.",
    },
    "wood_ticket_created": {
        "cs": "Ticket byl vytvořen: {channel}",
        "en": "Ticket created: {channel}",
    },
}


def t(key: str, locale: discord.Locale, **kwargs) -> str:
    options = STRINGS.get(key)
    if not options:
        raise KeyError(f"Missing translation key: {key}")

    lang = "en" if locale.value.startswith("en") else "cs"
    template = options.get(lang) or options.get("cs")
    return template.format(**kwargs)
