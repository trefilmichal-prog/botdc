import os

# Token bota z environment proměnné
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Chybí environment proměnná DISCORD_TOKEN s tokenem bota.")

# Odkazy na obrázky pro clan embed
CLAN_BOOSTS_IMAGE_URL = os.getenv("CLAN_BOOSTS_IMAGE_URL", "")
CLAN_BANNER_IMAGE_URL = os.getenv("CLAN_BANNER_IMAGE_URL", "")

# Cesta k SQLite databázi
DB_PATH = "wood_needs.db"

# Role, která má přístup do ticketů s dřevem (0 = vypnuto)
STAFF_ROLE_ID = 0  # např. 123456789012345678

# Připomínky materiálů
REMINDER_INTERVAL_HOURS = 3
INACTIVE_THRESHOLD_HOURS = 24

# Role pro ping u giveaway
GIVEAWAY_PING_ROLE_ID = 1440268327892025438

# Výchozí délka giveaway (minuty), když není zadána
DEFAULT_GIVEAWAY_DURATION_MINUTES = 15

# XP / coiny za aktivitu
XP_PER_MESSAGE = 10
COINS_PER_MESSAGE = 5
XP_MESSAGE_MIN_CHARS = 5
XP_COOLDOWN_SECONDS = 30
XP_PER_LEVEL = 100

# CLAN – role pro přijaté členy
CLAN_MEMBER_ROLE_ID = 1440268327892025438

# CLAN – role pro ping nových uchazečů
CLAN_APPLICATION_PING_ROLE_ID = 1440268371152339065

# CLAN – kategorie pro ticket kanály (přihlášky do klanu)
CLAN_TICKET_CATEGORY_ID = 1440977431577235456

# CLAN – po kolika minutách se mají rozhodnuté přihlášky mazat (kanály)
CLAN_TICKET_CLEANUP_MINUTES = 60
# jak často kontrolovat staré tickety (v minutách)
CLAN_TICKET_CLEANUP_INTERVAL_MINUTES = 5

# Varování za neaktivitu
WARN_ROLE_1_ID = 1441381537542307860
WARN_ROLE_2_ID = 1441381594941358135
WARN_ROLE_3_ID = 1441381627878965349
