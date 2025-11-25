import discord
from discord.ext import commands

from config import TOKEN
from db import init_db


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

        # sync slash commandů
        await self.tree.sync()

    async def on_ready(self):
        print(f"Přihlášen jako {self.user} (ID: {self.user.id})")


if __name__ == "__main__":
    init_db()
    bot = MyBot()
    bot.run(TOKEN)
