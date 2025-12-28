import importlib.util
import logging
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
from cog_roblox_activity import RobloxActivityCog
from cog_secret_notifications_forwarder import SecretNotificationsForwarder
from cog_shop import ShopCog
from cog_sp import RebirthPanel
from cog_time_status import TimeStatusCog
from cog_timers import TimersCog
from cog_translation import AutoTranslateCog
from cog_updater import AutoUpdater
from cog_welcome import WelcomeCog
from cog_wood import WoodCog
from cog_xp import XpCog
from config import ALLOWED_GUILD_ID, TOKEN
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
        self.tree.interaction_check = self._check_allowed_guild

        for cog in [
            LoggingCog(self),
            AutoUpdater(self),
            XpCog(self),
            WoodCog(self),
            TimersCog(self),
            ShopCog(self),
            ClanStatsOcrCog(self),
            BasicCommandsCog(self),
            LeaderboardCog(self),
            AdminTasks(self),
            RebirthPanel(self),
            AutoTranslateCog(self),
            TimeStatusCog(self),
            WelcomeCog(self),
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

        # sync slash commandů (preferuj povolený server kvůli rychlé dostupnosti)
        if ALLOWED_GUILD_ID:
            await self.tree.sync(guild=discord.Object(id=ALLOWED_GUILD_ID))
        else:
            await self.tree.sync()

    async def _check_allowed_guild(self, interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        if guild is not None and guild.id != ALLOWED_GUILD_ID:
            raise app_commands.CheckFailure("guild_not_allowed")
        return True

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CheckFailure) and str(error) == "guild_not_allowed":
            message = "Bot je dostupný pouze na povoleném serveru."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return
        logger.exception("App command error: %s", error)

    async def on_ready(self):
        logger.info("Přihlášen jako %s (ID: %s)", self.user, self.user.id)
        await self._leave_unapproved_guilds()

    async def on_guild_join(self, guild: discord.Guild):
        if guild.id != ALLOWED_GUILD_ID:
            logger.warning(
                "Bot byl přidán na nepovolený server %s (ID: %s), odcházím.",
                guild.name,
                guild.id,
            )
            try:
                await guild.leave()
            except Exception:
                logger.exception(
                    "Odchod z nepovoleného serveru %s (ID: %s) selhal.",
                    guild.name,
                    guild.id,
                )

    async def on_message(self, message: discord.Message):
        if message.author.bot:
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
