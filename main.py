import collections
import importlib.util
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from cog_admin_tasks import AdminTasks
from cog_attendance import AttendanceCog
from cog_basic import BasicCommandsCog
from cog_clan import ClanPanelCog
from cog_clan_stats import ClanStatsOcrCog
from cog_discord_writer import DiscordWriteCoordinatorCog
from cog_giveaway import GiveawayCog
from cog_leaderboard import LeaderboardCog
from cog_logging import LoggingCog
from cog_prophecy import ProphecyCog
from cog_restart_scheduler import RestartSchedulerCog
from cog_roblox_activity import RobloxActivityCog
from cog_secret_notifications_forwarder import SecretNotificationsForwarder
from cog_shop import ShopCog
from cog_sz import SecretMessageCog
from cog_sp import RebirthPanel
from cog_time_status import TimeStatusCog
from cog_timers import TimersCog
from cog_translation import AutoTranslateCog
from cog_updater import AutoUpdater
from cog_welcome import WelcomeCog
from cog_wood import WoodCog
from cog_xp import XpCog
from config import (
    ALLOWED_GUILD_ID,
    LOG_TO_CONSOLE,
    TOKEN,
    WINDOWS_NOTIFICATION_WINRT_ENABLED,
    WINDOWS_NOTIFICATION_WINRT_POLL_INTERVAL,
)
from db import init_db
from windows_notification_listener import WindowsNotificationListener



class PragueTimeFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=ZoneInfo("Europe/Prague"))
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat()


if LOG_TO_CONSOLE:
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        PragueTimeFormatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            "%Y-%m-%d %H:%M:%S",
        )
    )
    logging.basicConfig(level=logging.INFO, handlers=[console_handler], force=True)
else:
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(logging.NullHandler())
    root_logger.setLevel(logging.INFO)
    logging.lastResort = None
logger = logging.getLogger("botdc")


class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.messages = True
        intents.message_content = True

        super().__init__(command_prefix="!", intents=intents)
        self.winrt_listener: WindowsNotificationListener | None = None
        self._recent_interactions: "collections.OrderedDict[int, float]" = collections.OrderedDict()
        self._interaction_dedupe_window_seconds = 120.0

    async def setup_hook(self):
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
        for cog in [
            LoggingCog(self),
            AutoUpdater(self),
            XpCog(self),
            WoodCog(self),
            TimersCog(self),
            ShopCog(self),
            SecretMessageCog(self),
            ClanStatsOcrCog(self),
            BasicCommandsCog(self),
            LeaderboardCog(self),
            AdminTasks(self),
            RebirthPanel(self),
            AutoTranslateCog(self),
            TimeStatusCog(self),
            WelcomeCog(self),
            RestartSchedulerCog(self),
        ]:
            await add_cog_safe(cog)

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

        if WINDOWS_NOTIFICATION_WINRT_ENABLED:
            self.winrt_listener = WindowsNotificationListener(
                poll_interval=WINDOWS_NOTIFICATION_WINRT_POLL_INTERVAL
            )
            try:
                await self.winrt_listener.start()
            except Exception:
                logger.exception("Spuštění WinRT listeneru selhalo.")
        else:
            logger.info("WinRT ingest notifikací je vypnutý v konfiguraci.")

        await self._sync_app_commands()

    async def _sync_app_commands(self) -> None:
        target_guild_id = int(ALLOWED_GUILD_ID) if ALLOWED_GUILD_ID else None

        if target_guild_id:
            try:
                pinned_guild = discord.Object(id=target_guild_id)
                # Zkopíruje globální příkazy do cílové guildy pro okamžitou dostupnost.
                self.tree.copy_global_to(guild=pinned_guild)
                synced_pinned = await self.tree.sync(guild=pinned_guild)
                logger.info(
                    "Prioritní guild sync slash commandů (%s): %s příkazů.",
                    target_guild_id,
                    len(synced_pinned),
                )
            except Exception:
                logger.exception(
                    "Prioritní guild sync slash commandů selhal pro guild %s.",
                    target_guild_id,
                )

        try:
            # Globální sync je pomalejší na propagaci, ale zajišťuje jednotné příkazy.
            synced = await self.tree.sync()
            logger.info("Globální sync slash commandů: %s příkazů.", len(synced))
        except Exception:
            logger.exception("Globální sync slash commandů selhal.")

        # Guild sync urychlí okamžitou dostupnost příkazů (např. /sz) na serverech.
        for guild in self.guilds:
            if target_guild_id and guild.id == target_guild_id:
                continue
            try:
                self.tree.copy_global_to(guild=guild)
                synced_guild = await self.tree.sync(guild=guild)
                logger.info(
                    "Guild sync slash commandů (%s): %s příkazů.",
                    guild.id,
                    len(synced_guild),
                )
            except Exception:
                logger.exception("Guild sync slash commandů selhal pro guild %s.", guild.id)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        try:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info(
                "Po přidání bota proveden guild sync (%s): %s příkazů.",
                guild.id,
                len(synced),
            )
        except Exception:
            logger.exception("Guild sync po přidání bota selhal pro guild %s.", guild.id)

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        logger.exception("App command error: %s", error)

    async def on_ready(self):
        logger.info("Přihlášen jako %s (ID: %s)", self.user, self.user.id)

    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type == discord.InteractionType.application_command:
            now = time.monotonic()
            cutoff = now - self._interaction_dedupe_window_seconds
            while self._recent_interactions:
                _, timestamp = next(iter(self._recent_interactions.items()))
                if timestamp >= cutoff:
                    break
                self._recent_interactions.popitem(last=False)
            if interaction.id in self._recent_interactions:
                logger.warning(
                    "Duplicitní interaction %s byla ignorována.", interaction.id
                )
                return
            self._recent_interactions[interaction.id] = now

        await super().on_interaction(interaction)

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        await self.process_commands(message)

    async def close(self) -> None:
        if self.winrt_listener:
            try:
                await self.winrt_listener.stop()
            except Exception:
                logger.exception("Zastavení WinRT listeneru selhalo.")
        await super().close()


if __name__ == "__main__":
    if importlib.util.find_spec("tzdata") is None:
        logger.critical(
            "Chybí balíček tzdata. Spusťte 'pip install -r requirements.txt' před startem."
        )
        raise SystemExit(1)

    init_db()
    bot = MyBot()
    bot.run(TOKEN)
