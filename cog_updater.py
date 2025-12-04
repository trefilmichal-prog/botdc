import asyncio
import logging
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands


class AutoUpdater(commands.Cog):
    """Discord command for updating the bot from Git or a ZIP archive."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("botdc.updater")
        self.repo_path = Path(__file__).resolve().parent

    async def _download_archive(self, url: str, destination: Path) -> None:
        await asyncio.to_thread(self._download_archive_sync, url, destination)

    @staticmethod
    def _download_archive_sync(url: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url) as response:
            destination.write_bytes(response.read())

    async def _run_git_command(self, *args: str) -> tuple[int, str, str]:
        git_executable = shutil.which("git")
        if not git_executable:
            error = "Git není nainstalován nebo není v PATH."
            self.logger.error(error)
            return 1, "", error

        try:
            process = await asyncio.create_subprocess_exec(
                git_executable,
                *args,
                cwd=str(self.repo_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            return process.returncode, stdout.decode().strip(), stderr.decode().strip()
        except FileNotFoundError as exc:  # pragma: no cover - defensive guard
            error = f"Nelze spustit git: {exc}"
            self.logger.exception(error)
            return 1, "", error

    async def _ensure_clean_worktree(self) -> tuple[bool, str | None]:
        """Return (is_clean, error_msg)."""

        if not (self.repo_path / ".git").exists():
            msg = "Tento adresář není Git repozitář."
            self.logger.error(msg)
            return False, msg

        returncode, stdout, stderr = await self._run_git_command("status", "--porcelain")
        if returncode != 0:
            error_msg = stderr or stdout or "Neznámá chyba při načítání stavu repozitáře."
            self.logger.error("Nepodařilo se načíst stav repozitáře: %s", error_msg)
            return False, error_msg

        if stdout:
            return False, (
                "V repozitáři jsou neuložené změny. Nejprve je potvrďte nebo zahoďte."
            )

        return True, None

    async def _update_from_archive(self, repo_url: str, branch: str) -> tuple[bool, str]:
        """Download ZIP archive from GitHub and replace local files."""

        base_url = repo_url[:-4] if repo_url.endswith(".git") else repo_url
        archive_url = f"{base_url}/archive/refs/heads/{branch}.zip"
        self.logger.info("Stahuji archiv z %s", archive_url)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir_path = Path(tmpdir)
                archive_path = tmpdir_path / "repo.zip"
                await self._download_archive(archive_url, archive_path)

                with zipfile.ZipFile(archive_path) as archive:
                    archive.extractall(tmpdir_path)

                extracted_dirs = [p for p in tmpdir_path.iterdir() if p.is_dir()]
                if not extracted_dirs:
                    return False, "Archiv neobsahuje žádná data."

                extracted_root = extracted_dirs[0]

                for item in self.repo_path.iterdir():
                    if item.name == ".git":
                        continue
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()

                for item in extracted_root.iterdir():
                    target = self.repo_path / item.name
                    if item.is_dir():
                        shutil.copytree(item, target, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, target)

        except Exception as exc:  # pragma: no cover - defensive guard
            self.logger.exception("Aktualizace z archivu selhala: %s", exc)
            return False, f"Aktualizace z archivu selhala: {exc}"

        return True, "Aktualizace z archivu dokončena."

    @app_commands.command(
        name="updatebot",
        description="Aktualizuje bota z Git repozitáře nebo ZIP archivu.",
    )
    @app_commands.describe(
        repo_url="URL repozitáře pro pull (např. https://github.com/user/repo.git)",
        branch="Větev, která se má použít (výchozí: main)",
        via_archive="Aktualizovat stažením ZIP archivu místo použití Gitu.",
    )
    @app_commands.default_permissions(administrator=True)
    async def update_bot(
        self,
        interaction: discord.Interaction,
        repo_url: str,
        branch: str = "main",
        via_archive: bool = False,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if via_archive:
            success, message = await self._update_from_archive(repo_url, branch)
            if not success:
                await interaction.followup.send(f"❌ {message}", ephemeral=True)
                return

            await interaction.followup.send(
                "✅ Bot byl úspěšně aktualizován ze staženého archivu.",
                ephemeral=True,
            )
            return

        clean, error_msg = await self._ensure_clean_worktree()
        if not clean:
            await interaction.followup.send(
                f"❌ Aktualizaci nelze provést: {error_msg}", ephemeral=True
            )
            return

        fetch_code, fetch_out, fetch_err = await self._run_git_command(
            "fetch", repo_url, branch
        )
        if fetch_code != 0:
            message = (
                "❌ Stažení změn selhalo. Zkontrolujte URL/branch a dostupnost repozitáře.\n"
                f"Výstup: ```\n{fetch_err or fetch_out}\n```"
            )
            await interaction.followup.send(message, ephemeral=True)
            self.logger.error(
                "Git fetch selhal s kódem %s: %s", fetch_code, fetch_err or fetch_out
            )
            return

        reset_code, reset_out, reset_err = await self._run_git_command(
            "checkout",
            "-B",
            branch,
            "FETCH_HEAD",
        )
        if reset_code != 0:
            message = (
                "❌ Přepnutí na požadovanou větev selhalo.\n"
                f"Výstup: ```\n{reset_err or reset_out}\n```"
            )
            await interaction.followup.send(message, ephemeral=True)
            self.logger.error(
                "Git checkout selhal s kódem %s: %s", reset_code, reset_err or reset_out
            )
            return

        self.logger.info(
            "Bot aktualizován z %s (%s): %s", repo_url, branch, fetch_out or reset_out
        )
        await interaction.followup.send(
            "✅ Bot byl úspěšně aktualizován.\n"
            f"Výstup: ```\n{fetch_out or reset_out}\n```",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoUpdater(bot))
