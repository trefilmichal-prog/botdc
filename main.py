import importlib.util
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands

from cog_attendance import AttendanceCog
from cog_clan import ClanPanelCog
from cog_discord_writer import DiscordWriteCoordinatorCog
from cog_giveaway import GiveawayCog
from cog_prophecy import ProphecyCog
from cog_roblox_activity import RobloxActivityCog
from cog_secret_notifications_forwarder import SecretNotificationsForwarder

from config import TOKEN
from db import init_db



class PragueTimeFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=ZoneInfo("Europe/Prague"))
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat()


handler = logging.StreamHandler()
handler.setFormatter(
    PragueTimeFormatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
)
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger("botdc")


class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.messages = True
        intents.message_content = True

        super().__init__(command_prefix="!", intents=intents)
        self._command_cooldown = commands.CooldownMapping.from_cooldown(
            5, 10, commands.BucketType.user
        )

    async def setup_hook(self):
        async def load_extension_safe(name: str):
            try:
                await self.load_extension(name)
                logger.info("Cog %s byl úspěšně načten.", name)
            except Exception:
                logger.exception("Načtení cogu %s selhalo, pokračuji dál.", name)

        async def add_cog_safe(cog: commands.Cog):
            try:
                await self.add_cog(cog)
                logger.info("Cog %s byl úspěšně přidán.", cog.qualified_name)
            except Exception:
                logger.exception(
                    "Přidání cogu %s selhalo, pokračuji dál.",
                    getattr(cog, "qualified_name", type(cog).__name__),
                )

        await add_cog_safe(DiscordWriteCoordinatorCog(self))

        # Nejdříve načteme kritické cogy, které musí fungovat i při chybě ostatních.
        await load_extension_safe("cog_logging")
        await load_extension_safe("cog_updater")

        # Načtení ostatních modulů pokračuje i při chybách.
        for extension in [
            "cog_xp",
            "cog_wood",
            "cog_timers",
            "cog_shop",
            "cog_clan_stats",
            "cog_basic",
            "cog_leaderboard",
            "cog_antispam",
            "cog_admin_tasks",
            "cog_sp",
            "cog_translation",
            "cog_time_status",
            "cog_welcome",
        ]:
            await load_extension_safe(extension)

        existing_clan_panel = self.tree.get_command(
            "clan_panel", type=discord.AppCommandType.chat_input
        )
        if existing_clan_panel:
            self.tree.remove_command(
                "clan_panel", type=discord.AppCommandType.chat_input
            )

        for cog in [
            GiveawayCog(self),
            ClanPanelCog(self),
            AttendanceCog(self),
            ProphecyCog(self),
            RobloxActivityCog(self),
            SecretNotificationsForwarder(self),
        ]:
            await add_cog_safe(cog)

        # sync slash commandů
        await self.tree.sync()

    async def on_ready(self):
        logger.info("Přihlášen jako %s (ID: %s)", self.user, self.user.id)

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        bucket = self._command_cooldown.get_bucket(message)
        retry_after = bucket.update_rate_limit()
        if retry_after:
            logger.info(
                "Uživatel %s překročil limit příkazů, čeká %.1fs",
                message.author.id,
                retry_after,
            )
            return

        await self.process_commands(message)


if __name__ == "__main__":
    if importlib.util.find_spec("tzdata") is None:
        logger.critical(
            "Chybí balíček tzdata. Spusťte 'pip install -r requirements.txt' před startem."
        )
        raise SystemExit(1)

    init_db()
    bot = MyBot()
    bot.run(TOKEN)
