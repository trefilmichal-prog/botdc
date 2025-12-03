import logging

import discord
from discord.ext import commands

from config import TOKEN
from db import init_db


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
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
        # načteme cogy (moduly)
        await self.load_extension("cog_xp")
        await self.load_extension("cog_wood")
        await self.load_extension("cog_timers")
        await self.load_extension("cog_giveaway")
        await self.load_extension("cog_shop")
        await self.load_extension("cog_clan")  # <-- nový modul pro přihlášky do klanu
        await self.load_extension("cog_clan2")  # druhý modul přihlášek
        await self.load_extension("cog_clan_stats")
        await self.load_extension("cog_basic")
        await self.load_extension("cog_leaderboard")
        await self.load_extension("cog_warn")
        await self.load_extension("cog_prophecy")
        await self.load_extension("cog_logging")
        await self.load_extension("cog_admin_tasks")
        await self.load_extension("cog_translation")

        # sync slash commandů
        await self.tree.sync()

    async def on_ready(self):
        logger.info("Přihlášen jako %s (ID: %s)", self.user, self.user.id)


if __name__ == "__main__":
    init_db()
    bot = MyBot()
    bot.run(TOKEN)
