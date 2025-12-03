import os

# Token bota z environment proměnné
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Chybí environment proměnná DISCORD_TOKEN s tokenem bota.")

# Ollama konfigurace
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))

# Odkazy na obrázky pro clan embed
CLAN_BOOSTS_IMAGE_URL = "https://ezrz.eu/dcbot/stats.jpg"
CLAN_BANNER_IMAGE_URL = "https://ezrz.eu/dcbot/baner2.jpg"

# Cesta k SQLite databázi
DB_PATH = "wood_needs.db"

# Admin panel – cesta k SQLite databázi a výstupní roomka
ADMIN_TASK_DB_PATH = "admin/database.sqlite"
ADMIN_TASK_CHANNEL_ID = 1443867919015481489

# Role, která má přístup do ticketů s dřevem (0 = vypnuto)
STAFF_ROLE_ID = 0  # např. 123456789012345678

# Role s přístupem do všech ticketů (clan i wood)
TICKET_VIEWER_ROLE_ID = 1440268371152339065

# Připomínky materiálů
REMINDER_INTERVAL_HOURS = 3
INACTIVE_THRESHOLD_HOURS = 24

# Role pro ping u giveaway
GIVEAWAY_PING_ROLE_ID = 1440268327892025438

# Automatické překlady
AUTO_TRANSLATE_CHANNEL_ID = 1440270650018369628
AUTO_TRANSLATE_TARGET_CHANNEL_ID = 1444077684287078531
REACTION_TRANSLATION_BLOCKED_CHANNEL_IDS = {
    1440983832026288128,
    1444077684287078531,
    1440270650018369628,
}

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
# CLAN – role pro přijaté členy (EN)
CLAN_MEMBER_ROLE_EN_ID = 1444077881159450655
# CLAN 2 – role pro přijaté členy
CLAN2_MEMBER_ROLE_ID = 1444306127687778405

# CLAN – role pro ping nových uchazečů
CLAN_APPLICATION_PING_ROLE_ID = 1440268371152339065

# CLAN – kategorie pro ticket kanály (přihlášky do klanu)
CLAN_TICKET_CATEGORY_ID = 1440977431577235456
# CLAN – kategorie pro tickety přijatých členů
CLAN_ACCEPTED_TICKET_CATEGORY_ID = 1443684694968373421
# CLAN 2 – kategorie pro tickety přijatých členů
CLAN2_ACCEPTED_TICKET_CATEGORY_ID = 1444304658142335217
# CLAN – kategorie pro tickety členů na dovolené
CLAN_VACATION_TICKET_CATEGORY_ID = 1443684733061042187

# CLAN – po kolika minutách se mají rozhodnuté přihlášky mazat (kanály)
CLAN_TICKET_CLEANUP_MINUTES = 60
# jak často kontrolovat staré tickety (v minutách)
CLAN_TICKET_CLEANUP_INTERVAL_MINUTES = 5

# Varování za neaktivitu
WARN_ROLE_1_ID = 1441381537542307860
WARN_ROLE_2_ID = 1441381594941358135
WARN_ROLE_3_ID = 1441381627878965349
