import discord

DEFAULT_LOCALE = discord.Locale.cs


def normalize_locale(raw_locale: str | discord.Locale | None) -> discord.Locale:
    if isinstance(raw_locale, discord.Locale):
        value = raw_locale.value
    elif raw_locale is None:
        return DEFAULT_LOCALE
    else:
        value = str(raw_locale)

    if value.startswith("en"):
        return discord.Locale.en_US
    if value.startswith("cs"):
        return discord.Locale.cs
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
}


def t(key: str, locale: discord.Locale, **kwargs) -> str:
    options = STRINGS.get(key)
    if not options:
        raise KeyError(f"Missing translation key: {key}")

    lang = "en" if locale.value.startswith("en") else "cs"
    template = options.get(lang) or options.get("cs")
    return template.format(**kwargs)
