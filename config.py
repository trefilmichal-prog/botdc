import importlib
import importlib.util
import json
import logging
import os

logger = logging.getLogger(__name__)

# Absolutní cesta ke kořenovému adresáři projektu
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Token bota z environment proměnné
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Chybí environment proměnná DISCORD_TOKEN s tokenem bota.")

# Povolený server pro interakce bota
ALLOWED_GUILD_ID = 1440039495058854030

# Ollama konfigurace
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))

# DeepL konfigurace
# Pokud se objeví SSL chyba, nainstalujte CA certifikáty v systému
# nebo nastavte cestu přes DEEPL_CA_BUNDLE (případně dočasně vypněte
# ověření pomocí DEEPL_SSL_VERIFY=false).
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY")
if not DEEPL_API_KEY:
    logger.warning(
        "DEEPL_API_KEY není nastaveno. DeepL překlady budou přeskočeny a použije se Ollama fallback."
    )
DEEPL_API_URL = os.getenv("DEEPL_API_URL", "https://api-free.deepl.com/v2/translate")
DEEPL_TIMEOUT = int(os.getenv("DEEPL_TIMEOUT", "30"))
DEEPL_SSL_VERIFY = os.getenv("DEEPL_SSL_VERIFY", "true").lower() == "true"
DEEPL_CA_BUNDLE = os.getenv("DEEPL_CA_BUNDLE")

if DEEPL_CA_BUNDLE:
    if not (os.path.isfile(DEEPL_CA_BUNDLE) and os.access(DEEPL_CA_BUNDLE, os.R_OK)):
        logger.warning(
            "DEEPL_CA_BUNDLE path is not readable or missing: %s", DEEPL_CA_BUNDLE
        )
        DEEPL_CA_BUNDLE = None

if not DEEPL_CA_BUNDLE:
    certifi_spec = importlib.util.find_spec("certifi")
    if certifi_spec is not None:
        certifi = importlib.import_module("certifi")
        certifi_path = certifi.where()
        if os.path.isfile(certifi_path) and os.access(certifi_path, os.R_OK):
            DEEPL_CA_BUNDLE = certifi_path

# CA bundle pro updater (přednostně UPDATER_CA_BUNDLE, pak standardní proměnné)
UPDATER_CA_BUNDLE = (
    os.getenv("UPDATER_CA_BUNDLE")
    or os.getenv("SSL_CERT_FILE")
    or os.getenv("REQUESTS_CA_BUNDLE")
)
if UPDATER_CA_BUNDLE:
    if not (
        os.path.isfile(UPDATER_CA_BUNDLE)
        and os.access(UPDATER_CA_BUNDLE, os.R_OK)
    ):
        logger.warning(
            "UPDATER_CA_BUNDLE path is not readable or missing: %s",
            UPDATER_CA_BUNDLE,
        )
        UPDATER_CA_BUNDLE = None

if not UPDATER_CA_BUNDLE:
    certifi_spec = importlib.util.find_spec("certifi")
    if certifi_spec is not None:
        certifi = importlib.import_module("certifi")
        certifi_path = certifi.where()
        if os.path.isfile(certifi_path) and os.access(certifi_path, os.R_OK):
            UPDATER_CA_BUNDLE = certifi_path
        else:
            logger.warning("certifi CA bundle path is not readable: %s", certifi_path)
    else:
        logger.warning("certifi package not available for UPDATER_CA_BUNDLE fallback.")

AUTO_RESTART_INTERVAL_MINUTES = int(
    os.getenv("AUTO_RESTART_INTERVAL_MINUTES", "30")
)

# Přehled času – cílová místnost a výchozí americká oblast
TIME_STATUS_CHANNEL_ID = 1445973251019898961
TIME_STATUS_STATE_NAME = os.getenv("TIME_STATUS_STATE_NAME", "New York")
TIME_STATUS_STATE_TIMEZONE = os.getenv(
    "TIME_STATUS_STATE_TIMEZONE", "America/New_York"
)

# Odkazy na obrázky pro clan embed
CLAN_BOOSTS_IMAGE_URL = "https://ezrz.eu/dcbot/stats.jpg"
CLAN_BANNER_IMAGE_URL = "https://ezrz.eu/dcbot/baner2.jpg"

# WinRT notifikace (Windows pouze)
WINDOWS_NOTIFICATION_WINRT_ENABLED = (
    os.getenv("WINDOWS_NOTIFICATION_WINRT_ENABLED", "true").lower() == "true"
)
WINDOWS_NOTIFICATION_WINRT_POLL_INTERVAL = float(
    os.getenv("WINDOWS_NOTIFICATION_WINRT_POLL_INTERVAL", "1")
)
WINRT_LOG_PATH = os.getenv(
    "WINRT_LOG_PATH", os.path.join(BASE_DIR, "winrt_notifications.log")
)

# Cesta k SQLite databázi
DB_PATH = os.path.join(BASE_DIR, "wood_needs.db")

# Admin panel – cesta k SQLite databázi a výstupní roomka
# Admin úkoly nyní sdílí hlavní databázi, aby se používala pouze wood_needs.db
ADMIN_TASK_DB_PATH = DB_PATH
ADMIN_TASK_CHANNEL_ID = 1443867919015481489
REBIRTH_DATA_URL = os.getenv(
    "REBIRTH_DATA_URL", "https://ezrz.eu/dcbot/admin.php?rebirths_json=1"
)

# Secret leaderboard endpoint
# Pokud se objeví SSL chyba, nastavte cestu přes SECRET_LEADERBOARD_CA_BUNDLE
# nebo dočasně vypněte ověření pomocí SECRET_LEADERBOARD_SSL_VERIFY=false.
SECRET_LEADERBOARD_URL = os.getenv(
    "SECRET_LEADERBOARD_URL", "https://ezrz.eu/lbsecret/index.php"
)
SECRET_LEADERBOARD_TOKEN = os.getenv("SECRET_LEADERBOARD_TOKEN")
SECRET_LEADERBOARD_SSL_VERIFY = (
    os.getenv("SECRET_LEADERBOARD_SSL_VERIFY", "true").lower() == "true"
)
SECRET_LEADERBOARD_CA_BUNDLE = os.getenv("SECRET_LEADERBOARD_CA_BUNDLE")

if SECRET_LEADERBOARD_CA_BUNDLE:
    if not (
        os.path.isfile(SECRET_LEADERBOARD_CA_BUNDLE)
        and os.access(SECRET_LEADERBOARD_CA_BUNDLE, os.R_OK)
    ):
        logger.warning(
            "SECRET_LEADERBOARD_CA_BUNDLE path is not readable or missing: %s",
            SECRET_LEADERBOARD_CA_BUNDLE,
        )
        SECRET_LEADERBOARD_CA_BUNDLE = None

if not SECRET_LEADERBOARD_CA_BUNDLE:
    certifi_spec = importlib.util.find_spec("certifi")
    if certifi_spec is not None:
        certifi = importlib.import_module("certifi")
        certifi_path = certifi.where()
        if os.path.isfile(certifi_path) and os.access(certifi_path, os.R_OK):
            SECRET_LEADERBOARD_CA_BUNDLE = certifi_path

# Role, která má přístup do ticketů s dřevem (0 = vypnuto)
STAFF_ROLE_ID = 0  # např. 123456789012345678

# Role s přístupem do všech ticketů (clan i wood)
TICKET_VIEWER_ROLE_ID = 1440268371152339065

# Správce wood panelu – jediná role s přístupem k nastavení/resetu
WOOD_ADMIN_ROLE_ID = 1440268371152339065

# Připomínky materiálů
REMINDER_INTERVAL_HOURS = 3
INACTIVE_THRESHOLD_HOURS = 24

# Role pro ping u giveaway
GIVEAWAY_PING_ROLE_ID = 1440268327892025438

# Role s přístupem k /setup a /leaderboard příkazům
SETUP_MANAGER_ROLE_ID = 1_440_043_301_515_559_014
# Role s přístupem k /setup_panel příkazu (shodná s TICKET_VIEWER_ROLE_ID)
SETUP_PANEL_ROLE_ID = 1440268371152339065

# Automatické překlady
# Nastavte AUTO_TRANSLATE_ENABLED na True pro zapnutí automatického překladu
AUTO_TRANSLATE_ENABLED = os.getenv("AUTO_TRANSLATE_ENABLED", "").lower() == "true"
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

# Anti-spam ochrana
ANTISPAM_MESSAGE_LIMIT = int(os.getenv("ANTISPAM_MESSAGE_LIMIT", "6"))
ANTISPAM_TIME_WINDOW_SECONDS = int(os.getenv("ANTISPAM_TIME_WINDOW_SECONDS", "10"))
ANTISPAM_DUPLICATE_LIMIT = int(os.getenv("ANTISPAM_DUPLICATE_LIMIT", "3"))
ANTISPAM_DUPLICATE_WINDOW_SECONDS = int(
    os.getenv("ANTISPAM_DUPLICATE_WINDOW_SECONDS", "30")
)
ANTISPAM_TIMEOUT_SECONDS = int(os.getenv("ANTISPAM_TIMEOUT_SECONDS", "600"))
ANTISPAM_NOTICE_COOLDOWN_SECONDS = int(
    os.getenv("ANTISPAM_NOTICE_COOLDOWN_SECONDS", "30")
)

# Povolený server, na kterém má bot fungovat
ALLOWED_GUILD_ID = int(os.getenv("ALLOWED_GUILD_ID", "1440039495058854030"))

# Discord write queue rate limit (sekundy mezi write operacemi)
DISCORD_WRITE_MIN_INTERVAL_SECONDS = float(
    os.getenv("DISCORD_WRITE_MIN_INTERVAL_SECONDS", "0.5")
)
DISCORD_WRITE_OPERATION_MIN_INTERVALS_DEFAULT = {
    # Konzervativní doporučení dle běžných Discord limitů (1.0–1.2 s).
    "send_message": 1.1,
    "edit_message": 1.1,
    "delete_message": 1.1,
    "webhook_send": 1.1,
    "webhook_edit": 1.1,
    "webhook_delete": 1.1,
    "interaction_followup": 1.1,
    "interaction_response": 1.1,
    "interaction_edit": 1.1,
    "interaction_edit_original": 1.1,
    "interaction_defer": 1.1,
    "interaction_modal": 1.1,
    "add_reaction": 0.6,
    "remove_reaction": 0.6,
    "edit_member": 1.0,
    "add_roles": 1.0,
    "edit_channel": 1.2,
    "delete_channel": 1.2,
    "delete_messages": 1.2,
    "pin_message": 1.0,
    "unpin_message": 1.0,
}
DISCORD_WRITE_OPERATION_MIN_INTERVALS = dict(DISCORD_WRITE_OPERATION_MIN_INTERVALS_DEFAULT)
DISCORD_WRITE_OPERATION_MIN_INTERVALS_RAW = os.getenv(
    "DISCORD_WRITE_OPERATION_MIN_INTERVALS", ""
).strip()
if DISCORD_WRITE_OPERATION_MIN_INTERVALS_RAW:
    try:
        parsed_limits = json.loads(DISCORD_WRITE_OPERATION_MIN_INTERVALS_RAW)
        if isinstance(parsed_limits, dict):
            for key, value in parsed_limits.items():
                if value is None:
                    continue
                try:
                    DISCORD_WRITE_OPERATION_MIN_INTERVALS[str(key)] = float(value)
                except (TypeError, ValueError):
                    logger.warning(
                        "Neplatná hodnota DISCORD_WRITE_OPERATION_MIN_INTERVALS pro %s: %s",
                        key,
                        value,
                    )
        else:
            logger.warning("DISCORD_WRITE_OPERATION_MIN_INTERVALS musí být JSON objekt.")
    except json.JSONDecodeError as exc:
        logger.warning("DISCORD_WRITE_OPERATION_MIN_INTERVALS nelze načíst: %s", exc)
DISCORD_WRITE_WARMUP_SECONDS = float(os.getenv("DISCORD_WRITE_WARMUP_SECONDS", "1.5"))
DISCORD_WRITE_WARMUP_OPERATIONS = {
    "add_reaction",
    "remove_reaction",
    "edit_member",
    "add_roles",
}
DISCORD_WRITE_WARMUP_OPERATIONS_RAW = os.getenv("DISCORD_WRITE_WARMUP_OPERATIONS", "").strip()
if DISCORD_WRITE_WARMUP_OPERATIONS_RAW:
    DISCORD_WRITE_WARMUP_OPERATIONS = {
        item.strip()
        for item in DISCORD_WRITE_WARMUP_OPERATIONS_RAW.split(",")
        if item.strip()
    }

# CLAN – role pro přijaté členy
CLAN_MEMBER_ROLE_ID = 1440268327892025438
# CLAN – role pro přijaté členy (EN)
CLAN_MEMBER_ROLE_EN_ID = 1444077881159450655
# CLAN 2 – role pro přijaté členy
CLAN2_MEMBER_ROLE_ID = 1444306127687778405
# CLAN 3 – role pro přijaté členy
CLAN3_MEMBER_ROLE_ID = 1447423249817403402

# Roblox – universe ID hry Rebirth Champions Ultimate
REBIRTH_CHAMPIONS_UNIVERSE_ID = 74260430392611
# Roblox – kanál pro automatické hlášení aktivity
ROBLOX_ACTIVITY_CHANNEL_ID = 1450010299905216543

# CLAN – role pro ping nových uchazečů
CLAN_APPLICATION_PING_ROLE_ID = 1440268371152339065
# CLAN 2 – role pro ping nových uchazečů
CLAN2_APPLICATION_PING_ROLE_ID = 1444304987986595923
# CLAN 3 – role pro ping nových uchazečů
CLAN3_APPLICATION_PING_ROLE_ID = 1447423174974247102
# CLAN 2 – role s administrátorským přístupem k ticketům
CLAN2_ADMIN_ROLE_ID = 1444304987986595923
# CLAN 3 – role s administrátorským přístupem k ticketům
CLAN3_ADMIN_ROLE_ID = 1447423174974247102

# CLAN – kategorie pro ticket kanály (přihlášky do klanu)
CLAN_TICKET_CATEGORY_ID = 1440977431577235456
# CLAN – kategorie pro tickety přijatých členů
CLAN_ACCEPTED_TICKET_CATEGORY_ID = 1443684694968373421
# CLAN 2 – kategorie pro tickety přijatých členů
CLAN2_ACCEPTED_TICKET_CATEGORY_ID = 1444304658142335217
# CLAN 3 – kategorie pro tickety přijatých členů
CLAN3_ACCEPTED_TICKET_CATEGORY_ID = 1447423401462333480
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
