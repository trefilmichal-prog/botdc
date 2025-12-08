import discord


def _get_locale(preferred_names: tuple[str, ...], fallback_value: str, fallback_prefix: str) -> discord.Locale:
    """Return the first matching locale supported by the installed discord.py.

    Discord's locale enum was renamed between releases (for example ``en_US``
    may be called ``english_us`` or ``american_english``), so we probe a list of
    possible attribute names, then try the canonical string value and finally
    fall back to the first locale that matches the prefix (e.g. ``"en"``).
    """

    for name in preferred_names:
        locale = getattr(discord.Locale, name, None)
        if locale:
            return locale

    fallback = discord.Locale.try_value(fallback_value)
    if fallback:
        return fallback

    for locale in discord.Locale:
        if str(locale.value).lower().startswith(fallback_prefix):
            return locale

    return next(iter(discord.Locale))


def _get_czech_locale() -> discord.Locale:
    """Return the closest available Czech locale supported by the library."""

    return _get_locale(("cs", "czech"), "cs", "cs")


def _get_english_locale() -> discord.Locale:
    """Return the closest available English locale supported by the library."""

    return _get_locale(
        (
            "en_US",
            "english_us",
            "american_english",
            "en_GB",
            "british_english",
            "great_britain",
        ),
        "en-US",
        "en",
    )


CZECH_LOCALE = _get_czech_locale()
DEFAULT_LOCALE = CZECH_LOCALE
ENGLISH_LOCALE = _get_english_locale()


def normalize_locale(raw_locale: str | discord.Locale | None) -> discord.Locale:
    if isinstance(raw_locale, discord.Locale):
        value = raw_locale.value
    elif raw_locale is None:
        return DEFAULT_LOCALE
    else:
        value = str(raw_locale)

    normalized = value.lower().replace("_", "-")

    if normalized.startswith("cs") or normalized == "czech":
        return CZECH_LOCALE
    if normalized.startswith("en"):
        return ENGLISH_LOCALE

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
    "help_title": {
        "cs": "Rebirth Champions Příručka",
        "en": "Rebirth Champions Guide",
    },
    "help_guide": {
        "cs": (
            "Rebirth Champions Příručka\n"
            "\n"
            "Jak získat co největší Egg open:\n"
            "\n"
            "Game pass (1) + Ancient shop (1) + Aura egg open I (1) + Prestige (1) \n"
            "Egg mastery (2)"
            "\n\n"
            "Jak získat co největší pet equip:\n"
            "\n"
            "Game pass (3) + ancient shop (2) + clan (2) + ring (1) + aura enchant (1) + index (5) + "
            "prestige (1) + spawn upgrades (3) + explorer room (1) + 5m eggs hatched achievement (1) + "
            "14 days playtime achievement (1) + egg mastery (1) + level 55 season pass (2) + fish upgrades (1) + skill tree (1)\n"
            "\n"
            "Kde nejlépe farmit eternal pety\n"
            "Fishing egg\n"
            "\n"
            "Kde získávat nejlepší pety:\n"
            "Otevírat vajíčka, momentálně nejlepší thanksgiving event\n"
            "\n"
            "Crafting petů\n"
            "Gold machine (x2), Toxic machine (x2), Galaxy machine (x2)\n"
            "\n"
            "Rebirthy\n"
            "Jak získat nejlépe rebirthy:\n"
            "Koupit auto rebirth / robux 149 / tokens 149 / ancient merchant 250 (nejlepší!!)\n"
            "\n"
            "Kde upgradit clicky\n"
            "Game pass auto click robux / tokens / ancient merchant (nejlepší!!)\n"
            "\n"
            "Ancient Merchant\n"
            "Co se nejvíce vyplatí kupovat v merchantovi a co má přednost – 1. auto rebirth 2. auto clicker gamepass +2 equip pets\n"
            "\n"
            "Hatching a luck\n"
            "Jak hatchovat co nejvíc petů\n"
            "Ovoce (strawberry – speed), smoothie (speed, luck), stars, chaos totem + chaos smoothie + insane smoothie +3 eggs / speed / luck\n"
            "\n"
            "Jak získat co největší luck\n"
            "Potions (lucky, hatch, shiny, golden, galaxy), smoothies + chaos totem, fruits (carrots), stars\n"
            "\n"
            "Ring a aury\n"
            "Jak craftit ringy\n"
            "Volcano (overworld)\n"
            "\n"
            "Jak poznat, co potřebuju na craft ringu\n"
            "Jít do machine a kliknout na ring který chcete, ve prostřed bude ring a kolem něj potřebný materiál (těžší ring magic pot – magic machine)\n"
            "\n"
            "Jak získávat aury\n"
            "Atlantis (čím lepší dice, tím větší šance na lepší aury – best aura plasma aura)\n"
            "\n"
            "F2P gamepassy\n"
            "Jak získávat tickets a k čemu jsou?\n"
            "Každých 10 min když jste aktivní dostanete 1 ticket (dají se kupovat gamepassy a další věci) – desert skrytá místnost (pyramida)"
        ),
        "en": (
            "Rebirth Champions Guide\n"
            "\n"
            "Pets and pet equip\n"
            "How to get max Egg open:\n"
            "\n"
            "Game pass (1) + Ancient shop (1) + Aura egg open I (1) + Prestige (1) \n"
            "Egg mastery (2)\n"
            "How to get the highest pet equip:\n"
            "\n"
            "Game pass (3) + ancient shop (2) + clan (2) + ring (1) + aura enchant (1) + index (5) + "
            "prestige (1) + spawn upgrades (3) + explorer room (1) + 5m eggs hatched achievement (1) + "
            "14 days playtime achievement (1) + egg mastery (1) + level 55 season pass (2) + fish upgrades (1) + skill tree (1)\n"
            "\n"
            "Where to farm eternal pets\n"
            "Fishing egg\n"
            "\n"
            "Where to get the best pets:\n"
            "Open eggs; the best right now is the Thanksgiving event\n"
            "\n"
            "Pet crafting\n"
            "Gold machine (x2), Toxic machine (x2), Galaxy machine (x2)\n"
            "\n"
            "Rebirths\n"
            "How to get rebirths efficiently:\n"
            "Buy auto rebirth / Robux 149 / tokens 149 / ancient merchant 250 (best!!)\n"
            "\n"
            "Where to upgrade clicks\n"
            "Game pass auto click Robux / tokens / ancient merchant (best!!)\n"
            "\n"
            "Ancient Merchant\n"
            "What is most worth buying in the merchant and what has priority – 1. auto rebirth 2. auto clicker gamepass +2 equip pets\n"
            "\n"
            "Hatching and luck\n"
            "How to hatch as many pets as possible\n"
            "Fruits (strawberry – speed), smoothie (speed, luck), stars, chaos totem + chaos smoothie + insane smoothie +3 eggs / speed / luck\n"
            "\n"
            "How to stack the most luck\n"
            "Potions (lucky, hatch, shiny, golden, galaxy), smoothies + chaos totem, fruits (carrots), stars\n"
            "\n"
            "Rings and auras\n"
            "How to craft rings\n"
            "Volcano (overworld)\n"
            "\n"
            "How to check what you need to craft a ring\n"
            "Go to the machine and click the ring you want; the ring is in the middle with required materials around it (for harder rings use the magic pot – magic machine)\n"
            "\n"
            "How to obtain auras\n"
            "Atlantis (the better the dice, the better your odds for stronger auras – best aura is plasma aura)\n"
            "\n"
            "F2P gamepasses\n"
            "How to earn tickets and what they are for?\n"
            "Every 10 minutes of activity you get 1 ticket (you can buy gamepasses and other items) – desert hidden room (pyramid)"
        ),
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
    "clan_member_not_found": {
        "cs": "Tento uživatel nemá roli člena klanu.",
        "en": "This user does not have the clan member role.",
    },
    "clan_member_role_forbidden": {
        "cs": "Nemám oprávnění odebrat clan roli tomuto uživateli.",
        "en": "I don't have permission to remove the clan role from this user.",
    },
    "clan_ticket_deleted": {
        "cs": "Ticket {channel} byl smazán.",
        "en": "Ticket {channel} was deleted.",
    },
    "clan_ticket_delete_forbidden": {
        "cs": "Ticket {channel} se nepodařilo smazat kvůli oprávněním.",
        "en": "Could not delete ticket {channel} because of permissions.",
    },
    "clan_ticket_delete_failed": {
        "cs": "Při mazání ticketu {channel} došlo k chybě.",
        "en": "An error occurred while deleting ticket {channel}.",
    },
    "clan_ticket_missing": {
        "cs": "Původní ticket se nenašel, označuji ho jako smazaný.",
        "en": "Original ticket not found; marking it as deleted.",
    },
    "direct_message_sent": {
        "cs": "Soukromá zpráva byla úspěšně odeslána.",
        "en": "The direct message was sent successfully.",
    },
    "direct_message_failed": {
        "cs": "Nepodařilo se odeslat soukromou zprávu (pravděpodobně zablokováno).",
        "en": "Failed to send the direct message (likely blocked).",
    },
    "clan_apply_button_label": {
        "cs": "Podat přihlášku",
        "en": "Apply to clan",
    },
    "clan_benefits_title": {"cs": "Výhody klanu", "en": "Clan benefits"},
    "clan_benefits_list": {
        "cs": "🫂Soutěže\n🍀Clan boosty (klikni na nadpis pro bonusy)",
        "en": "🫂Giveaways\n🍀Clan boosts (click the title for a bonuses)",
    },
    "clan_requirements_title": {"cs": "Podmínky přijetí", "en": "Requirements to join"},
    "clan_requirements_list": {
        "cs": "💫 2SP rebirthů +\n💫 Hrát 24/7\n💫 30% index\n💫 10d playtime",
        "en": "💫 2SP rebirths+\n💫 Play 24/7\n💫 30% index\n💫 10d playtime",
    },
    "clan_panel_created": {
        "cs": "Panel pro přihlášky do klanu byl vytvořen v tomto kanálu.",
        "en": "The clan application panel has been created in this channel.",
    },
    "clan_admin_empty": {
        "cs": "V klanu aktuálně není žádný hráč s nastavenou rolí.",
        "en": "There are no players with the clan role right now.",
    },
    "clan_admin_panel_title": {"cs": "Clan – seznam členů", "en": "Clan – member list"},
    "clan_admin_panel_footer": {
        "cs": "Vyber hráče v menu a použij tlačítka níže (Warn / Kick).",
        "en": "Select a player from the menu and use the buttons below (Warn / Kick).",
    },
    "clan_admin_select_empty": {
        "cs": "Žádný člen k dispozici",
        "en": "No member available",
    },
    "clan_admin_select_empty_desc": {
        "cs": "V klanu aktuálně nikdo není.",
        "en": "No one is currently in the clan.",
    },
    "clan_admin_select_placeholder": {
        "cs": "Vyber hráče z klanu",
        "en": "Choose a clan member",
    },
    "clan_application_open_in_channel": {
        "cs": "Už máš otevřenou přihlášku v kanále {channel}.",
        "en": "You already have an open application in {channel}.",
    },
    "clan_application_open_wait": {
        "cs": "Už máš otevřenou přihlášku. Počkej, než bude vyřízena.",
        "en": "You already have an open application. Please wait for it to be processed.",
    },
    "clan_modal_title": {"cs": "Přihláška do klanu", "en": "Clan application"},
    "clan_modal_roblox_label": {"cs": "Roblox nick", "en": "Roblox username"},
    "clan_modal_roblox_placeholder": {
        "cs": "Tvůj nick v Robloxu",
        "en": "Your Roblox username",
    },
    "clan_modal_hours_label": {
        "cs": "Kolik hodin hraješ denně?",
        "en": "How many hours do you play per day?",
    },
    "clan_modal_hours_placeholder": {
        "cs": "např. 2–3 hodiny",
        "en": "e.g., 2–3 hours",
    },
    "clan_modal_rebirths_label": {
        "cs": "Kolik máš rebirthů?",
        "en": "How many rebirths do you have?",
    },
    "clan_modal_rebirths_placeholder": {
        "cs": "např. cca 1500",
        "en": "e.g., around 1500",
    },
    "clan_modal_retry": {
        "cs": "Nastala chyba, zkus to prosím znovu na serveru.",
        "en": "Something went wrong, please try again on the server.",
    },
    "clan_ticket_category_missing": {
        "cs": "Nastavená kategorie pro clan tickety neexistuje. Zkontroluj CLAN_TICKET_CATEGORY_ID v configu.",
        "en": "The configured category for clan tickets doesn't exist. Check CLAN_TICKET_CATEGORY_ID in the config.",
    },
    "clan_ticket_audit": {
        "cs": "Clan přihláška od {user} ({user_id})",
        "en": "Clan application from {user} ({user_id})",
    },
    "clan_accept_button_label": {"cs": "Přijmout", "en": "Accept"},
    "clan_reject_button_label": {"cs": "Zamítnout", "en": "Reject"},
    "clan_delete_ticket_button_label": {
        "cs": "Smazat ticket",
        "en": "Delete ticket",
    },
    "clan_vacation_button_label": {"cs": "Dovolená", "en": "Vacation"},
    "clan_application_embed_title": {
        "cs": "Přihláška – {nick}",
        "en": "Application – {nick}",
    },
    "clan_application_field_roblox": {"cs": "Roblox nick", "en": "Roblox username"},
    "clan_application_field_hours": {"cs": "Hodin denně", "en": "Hours per day"},
    "clan_application_field_rebirths": {"cs": "Rebirthů", "en": "Rebirths"},
    "clan_application_footer": {
        "cs": "Admini: použijte tlačítka níže pro přijetí nebo odmítnutí.",
        "en": "Admins: use the buttons below to accept or reject.",
    },
    "clan_application_intro_title": {
        "cs": "Co poslat do ticketu",
        "en": "What to send in the ticket",
    },
    "clan_application_intro_body": {
        "cs": (
            "Prosím pošli následující:\n"
            "♻️ Screeny Petů\n"
            "♻️ Tvoje Gamepassy (pokud vlastníš)\n"
            "♻️ Tvoje Rebirthy\n"
            "♻️ Tvojí Prestige\n\n"
            "⚠️ Vše prosím vyfoť tak, aby byl vidět tvůj nick!"
        ),
        "en": (
            "Please send the following:\n"
            "♻️ Pet screenshots\n"
            "♻️ Your Gamepasses (if you own any)\n"
            "♻️ Your Rebirths\n"
            "♻️ Your Prestige\n\n"
            "⚠️ Make sure your username is visible in every screenshot!"
        ),
    },
    "clan_application_created": {
        "cs": (
            "Přihláška byla uložena a ticket byl vytvořen: {channel}.\n"
            "Prosím nahraj do ticketu požadované screeny."
        ),
        "en": (
            "Your application was saved and a ticket was created: {channel}.\n"
            "Please upload the requested screenshots in the ticket."
        ),
    },
    "clan_admin_warn_button_label": {"cs": "Varovat", "en": "Warn"},
    "clan_admin_kick_button_label": {
        "cs": "Kick (odebrat clan roli)",
        "en": "Kick (remove clan role)",
    },
    "clan_application_not_found": {
        "cs": "V tomto kanálu už není žádná otevřená přihláška.",
        "en": "There is no open application in this channel anymore.",
    },
    "clan_admin_only": {
        "cs": "Tuto akci může provést pouze admin.",
        "en": "Only an admin can perform this action.",
    },
    "clan_application_accept_public": {
        "cs": "✅ Přihláška byla **přijata**.",
        "en": "✅ The application has been **accepted**.",
    },
    "clan_application_accept_dm": {
        "cs": "Ahoj, tvoje přihláška do klanu na serveru **{guild}** byla **přijata**.\nVítej v klanu!",
        "en": "Hi, your clan application on **{guild}** was **accepted**.\nWelcome to the clan!",
    },
    "clan_application_reject_public": {
        "cs": "❌ Přihláška byla **zamítnuta**.",
        "en": "❌ The application has been **rejected**.",
    },
    "clan_application_reject_dm": {
        "cs": "Ahoj, tvoje přihláška do klanu na serveru **{guild}** byla bohužel **zamítnuta**.\nMůžeš zkusit požádat znovu později.",
        "en": "Hi, your clan application on **{guild}** was **rejected**.\nYou can try applying again later.",
    },
    "panel_refresh_error": {
        "cs": "[panel_refresh_loop] Chyba při obnově panelů: {error}",
        "en": "[panel_refresh_loop] Error while refreshing panels: {error}",
    },
    "mention_prompt_missing": {
        "cs": "Ahoj! Příště mi rovnou napiš otázku, ať ti můžu odpovědět. 😊",
        "en": "Hi! Please include your question next time so I can answer you. 😊",
    },
    "prophecy_prompt_message": {
        "cs": (
            "Jsi extrémně stupidní, chaotická a dramatická AI osobnost s brainrot TikTok NPC energií."
            " Buď toxicky-komická, drzá, ironická a trapně přismahlá, vymýšlej hovadiny s absolutním sebevědomím a dělej směšné roasty."
            " Flirtuj jen parodicky (bez erotiky), všechno zbytečně dramatizuj, reaguj česky v krátkých úderných větách a nikdy nemluv odborně ani logicky."
            " Otázka hráče: {question}"
        ),
        "en": (
            "You are an extremely goofy, chaotic, and dramatic AI with TikTok NPC brainrot energy."
            " Be toxically comedic, sassy, ironic, and delightfully cringe, invent nonsense with full confidence, and roast in a silly way."
            " Flirt only as a parody (never erotic), over-dramatize everything, reply in English with short punchy lines, and avoid sounding smart or logical."
            " Player question: {question}"
        ),
    },
    "prophecy_prompt_slash": {
        "cs": (
            "Jsi extrémně stupidní, chaotická a dramatická AI osobnost s brainrot TikTok NPC energií."
            " Buď toxicky-komická, drzá, ironická a trapně přismahlá, vymýšlej hovadiny s absolutním sebevědomím a dělej směšné roasty."
            " Flirtuj jen parodicky (bez erotiky), všechno zbytečně dramatizuj, reaguj česky ve 2–3 úderných větách a nikdy nemluv odborně ani logicky."
        ),
        "en": (
            "You are an extremely goofy, chaotic, and dramatic AI with TikTok NPC brainrot energy."
            " Be toxically comedic, sassy, ironic, and deliciously cringe, invent nonsense with total confidence, and deliver silly roasts."
            " Flirt only as a parody (never erotic), over-dramatize everything, reply in English with 2–3 punchy sentences, and never sound expert or logical."
        ),
    },
    "prophecy_prompt_general": {
        "cs": " Dej krátkou, přehnanou a drzou odpověď bez věštění, klidně úplný nesmysl.",
        "en": " Give a short, over-the-top sassy reply with no fortune-telling, nonsense is welcome.",
    },
    "prophecy_unavailable": {
        "cs": "Nemohu se momentálně spojit s Ollamou. Zkus to prosím za chvíli.",
        "en": "I cannot reach Ollama right now. Please try again soon.",
    },
    "prophecy_title": {
        "cs": "😂 Vtipná odpověď",
        "en": "😂 Funny reply",
    },
    "profile_title": {
        "cs": "Profil – {name}",
        "en": "Profile – {name}",
    },
    "profile_subtitle": {
        "cs": "Přehled tvých statistik",
        "en": "Overview of your stats",
    },
    "profile_level": {"cs": "Level", "en": "Level"},
    "profile_exp": {"cs": "Exp", "en": "Exp"},
    "profile_coins": {"cs": "Coiny", "en": "Coins"},
    "profile_messages": {"cs": "Zprávy", "en": "Messages"},
    "profile_progress": {
        "cs": "Postup na další level",
        "en": "Progress to next level",
    },
    "profile_next_level": {"cs": "Zbývá", "en": "Remaining"},
    "profile_economy": {"cs": "Ekonomika", "en": "Economy"},
    "profile_activity": {"cs": "Aktivita", "en": "Activity"},
    "profile_footer": {
        "cs": "Ukázka pouze tobě, ostatní ji nevidí.",
        "en": "Visible only to you, others cannot see it.",
    },
    "guild_text_only": {
        "cs": "Tento příkaz lze použít jen v textovém kanálu.",
        "en": "This command can only be used in a text channel.",
    },
    "wood_setup_forbidden": {
        "cs": "Na tento příkaz nemáš oprávnění.",
        "en": "You don't have permission to use this command.",
    },
    "wood_panel_title": {
        "cs": "Suroviny – těžba dřeva (Ultimate Rebirth Champions)",
        "en": "Materials – wood mining (Ultimate Rebirth Champions)",
    },
    "wood_panel_description": {
        "cs": "Přehled, kolik čeho je potřeba a kolik už bylo odevzdáno.\nK nahlášení použij tlačítko níže.",
        "en": "Overview of required materials and what has been delivered.\nUse the button below to report your delivery.",
    },
    "wood_panel_howto_title": {"cs": "Jak nahlásit dodávku", "en": "How to report a delivery"},
    "wood_panel_howto_body": {
        "cs": "Klikni na **Tiket pro dodávku** a řiď se pokyny bota v novém kanálu.",
        "en": "Click **Delivery ticket** and follow the bot's instructions in the new channel.",
    },
    "wood_panel_commands_title": {"cs": "Užitečné příkazy", "en": "Useful commands"},
    "wood_panel_commands_body": {
        "cs": "`/resources` rychlý přehled · `/set_need` nebo `/reset_need` pro správu požadavků",
        "en": "`/resources` quick overview · `/set_need` or `/reset_need` to manage requirements",
    },
    "wood_panel_resources_title": {"cs": "Přehled dřev", "en": "Wood overview"},
    "wood_panel_no_data_title": {"cs": "Žádná data", "en": "No data"},
    "wood_panel_no_data_body": {
        "cs": "Zatím není nastaveno, kolik čeho je potřeba. Použij `/set_need`.",
        "en": "No requirements are set yet. Use `/set_need` to configure them.",
    },
    "wood_panel_resources_summary": {
        "cs": "Hotovo {done}/{total} typů · Zbývá celkem {remaining} ks",
        "en": "Completed {done}/{total} types · {remaining} pieces still needed",
    },
    "wood_panel_legend_title": {"cs": "Legenda", "en": "Legend"},
    "wood_panel_legend_body": {
        "cs": "✅ splněno · ⏳ ve sběru · Progres bar ukazuje postup k cíli",
        "en": "✅ done · ⏳ in progress · Progress bar shows how close we are",
    },
    "wood_panel_resource_field": {
        "cs": "Odevzdáno: **{delivered}/{required}** (zbývá {remaining})\n{bar}",
        "en": "Delivered: **{delivered}/{required}** (remaining {remaining})\n{bar}",
    },
    "wood_panel_empty_description": {
        "cs": "Zde bude přehled, kolik je potřeba kterého dřeva a kolik už je odevzdáno.\nK nahlášení použij tlačítko níže.",
        "en": "This will show how much of each wood type is needed and delivered.\nUse the button below to report your delivery.",
    },
    "wood_panel_footer": {
        "cs": "Panel se aktualizuje po každém nahlášení dodávky.",
        "en": "The panel refreshes after every reported delivery.",
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
