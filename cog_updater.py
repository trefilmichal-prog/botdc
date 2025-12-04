import asyncio
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands


class AutoUpdater(commands.Cog):
    """Discord command for updating the bot from a Git repository."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("botdc.updater")
        self.repo_path = Path(__file__).resolve().parent

    async def _run_git_command(self, *args: str) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(self.repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        return process.returncode, stdout.decode().strip(), stderr.decode().strip()

    @app_commands.command(
        name="updatebot", description="Aktualizuje bota z Git repozitáře."
    )
    @app_commands.describe(
        repo_url="URL repozitáře pro pull (např. https://github.com/user/repo.git)",
        branch="Větev, která se má použít (výchozí: main)",
    )
    @app_commands.default_permissions(administrator=True)
    async def update_bot(
        self,
        interaction: discord.Interaction,
        repo_url: str,
        branch: str = "main",
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        returncode, stdout, stderr = await self._run_git_command(
            "pull", repo_url, branch
        )

        if returncode != 0:
            message = (
                "❌ Aktualizace selhala. Zkontrolujte URL/branch a stav repozitáře.\n"
                f"Výstup: ```\n{stderr or stdout}\n```"
            )
            await interaction.followup.send(message, ephemeral=True)
            self.logger.error(
                "Git pull selhal s kódem %s: %s", returncode, stderr or stdout
            )
            return

        self.logger.info(
            "Bot aktualizován z %s (%s): %s", repo_url, branch, stdout
        )
        await interaction.followup.send(
            "✅ Bot byl úspěšně aktualizován.\n" f"Výstup: ```\n{stdout}\n```",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoUpdater(bot))
