import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import AUTO_RESTART_INTERVAL_MINUTES
from cog_updater import BotRestartError
from db import (
    clear_restart_plan,
    get_all_enabled_restart_settings,
    get_guild_restart_runtime,
    get_guild_restart_setting,
    get_restart_plan,
    set_restart_plan,
    upsert_guild_restart_runtime,
    upsert_guild_restart_setting,
)


class RestartSchedulerCog(commands.Cog):
    restart_group = app_commands.Group(name="restart", description="Správa automatických restartů")
    settings_group = app_commands.Group(name="settings", description="Nastavení restart scheduleru", parent=restart_group)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("botdc.restart_scheduler")
        self._restoring_plan = False

    async def cog_load(self) -> None:
        await self._restore_restart_plan()
        if not self.scheduler_loop.is_running():
            self.scheduler_loop.start()

    async def cog_unload(self) -> None:
        if self.scheduler_loop.is_running():
            self.scheduler_loop.cancel()

    async def _restore_restart_plan(self) -> None:
        plan = get_restart_plan()
        if not plan:
            return
        self._restoring_plan = True
        self.logger.info("Nalezen uložený restart plán z %s (guild=%s).", plan.get("planned_restart_at"), plan.get("source_guild_id"))
        clear_restart_plan()
        self._restoring_plan = False

    @tasks.loop(minutes=1)
    async def scheduler_loop(self) -> None:
        if self._restoring_plan:
            return
        now = datetime.utcnow()
        enabled = get_all_enabled_restart_settings()
        for setting in enabled:
            guild_id = int(setting["guild_id"])
            interval_minutes = max(1, int(setting["interval_minutes"]))
            runtime = get_guild_restart_runtime(guild_id)

            next_restart_at: Optional[datetime] = None
            if runtime and runtime.get("next_restart_at"):
                try:
                    next_restart_at = datetime.fromisoformat(runtime["next_restart_at"])
                except ValueError:
                    next_restart_at = None

            if next_restart_at is None:
                next_restart_at = now + timedelta(minutes=interval_minutes)
                upsert_guild_restart_runtime(guild_id, next_restart_at, None)
                continue

            if now >= next_restart_at:
                planned_at = datetime.utcnow()
                set_restart_plan(planned_at, guild_id)
                next_due = planned_at + timedelta(minutes=interval_minutes)
                upsert_guild_restart_runtime(guild_id, next_due, planned_at)
                try:
                    await self._graceful_restart()
                except BotRestartError as exc:
                    backoff_due = datetime.utcnow() + timedelta(minutes=5)
                    upsert_guild_restart_runtime(guild_id, backoff_due, planned_at)
                    clear_restart_plan()
                    self.logger.error(
                        "Restart scheduler: restart selhal pro guild %s. Další pokus v %s. Důvod: %s",
                        guild_id,
                        backoff_due.isoformat(),
                        exc,
                    )
                    return
                return

    @scheduler_loop.before_loop
    async def before_scheduler_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def _graceful_restart(self) -> bool:
        updater = self.bot.get_cog("AutoUpdater")
        if updater and hasattr(updater, "_restart_bot"):
            try:
                result = await updater._restart_bot()
            except BotRestartError:
                raise
            except Exception as exc:  # pragma: no cover - defensive guard
                raise BotRestartError("Restart přes AutoUpdater selhal.") from exc
            if not result:
                raise BotRestartError("Restart přes AutoUpdater vrátil neúspěch.")
            return True

        await asyncio.sleep(1)
        python = sys.executable
        try:
            os.execl(python, python, *sys.argv)
        except OSError as exc:
            self.logger.exception(
                "Restart procesu (fallback) selhal (errno=%s, strerror=%s, argv=%r, executable=%r)",
                exc.errno,
                exc.strerror,
                sys.argv,
                python,
            )
            raise BotRestartError("Restart procesu (fallback) selhal.") from exc
        return True

    def _build_text_view(self, lines: list[str]) -> discord.ui.LayoutView:
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.Container(*(discord.ui.TextDisplay(content=line) for line in lines))
        )
        return view

    async def _send_view(self, interaction: discord.Interaction, lines: list[str], ephemeral: bool = True) -> None:
        view = self._build_text_view(lines)
        if interaction.response.is_done():
            await interaction.followup.send(view=view, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(view=view, ephemeral=ephemeral)

    @settings_group.command(name="set", description="Nastaví interval restartů (minuty) pro tento server")
    @app_commands.describe(interval_minutes="Interval v minutách")
    async def restart_settings_set(self, interaction: discord.Interaction, interval_minutes: app_commands.Range[int, 1, 1440]) -> None:
        if not interaction.guild_id:
            await self._send_view(interaction, ["❌ Tento příkaz funguje pouze na serveru."])
            return
        current = get_guild_restart_setting(interaction.guild_id)
        enabled = bool(current["enabled"]) if current else True
        upsert_guild_restart_setting(interaction.guild_id, enabled, int(interval_minutes))
        next_due = datetime.utcnow() + timedelta(minutes=int(interval_minutes))
        upsert_guild_restart_runtime(interaction.guild_id, next_due, None)
        await self._send_view(
            interaction,
            [
                "## 🔁 Restart scheduler",
                f"Interval byl nastaven na **{int(interval_minutes)} min**.",
                f"Stav: {'zapnuto' if enabled else 'vypnuto'}.",
            ],
        )

    @settings_group.command(name="enable", description="Zapne automatické restarty pro tento server")
    async def restart_settings_enable(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await self._send_view(interaction, ["❌ Tento příkaz funguje pouze na serveru."])
            return
        current = get_guild_restart_setting(interaction.guild_id)
        interval = int(current["interval_minutes"]) if current else AUTO_RESTART_INTERVAL_MINUTES
        upsert_guild_restart_setting(interaction.guild_id, True, interval)
        next_due = datetime.utcnow() + timedelta(minutes=interval)
        upsert_guild_restart_runtime(interaction.guild_id, next_due, None)
        await self._send_view(
            interaction,
            ["## ✅ Restart scheduler", f"Automatické restarty zapnuty (interval **{interval} min**)."],
        )

    @settings_group.command(name="disable", description="Vypne automatické restarty pro tento server")
    async def restart_settings_disable(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await self._send_view(interaction, ["❌ Tento příkaz funguje pouze na serveru."])
            return
        current = get_guild_restart_setting(interaction.guild_id)
        interval = int(current["interval_minutes"]) if current else AUTO_RESTART_INTERVAL_MINUTES
        upsert_guild_restart_setting(interaction.guild_id, False, interval)
        upsert_guild_restart_runtime(interaction.guild_id, None, None)
        await self._send_view(interaction, ["## ⏸️ Restart scheduler", "Automatické restarty vypnuty."])

    @restart_group.command(name="status", description="Ukáže stav restart scheduleru pro tento server")
    async def restart_status(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await self._send_view(interaction, ["❌ Tento příkaz funguje pouze na serveru."])
            return
        setting = get_guild_restart_setting(interaction.guild_id)
        runtime = get_guild_restart_runtime(interaction.guild_id)
        if not setting:
            await self._send_view(
                interaction,
                [
                    "## ℹ️ Restart scheduler",
                    "Nastavení zatím neexistuje.",
                    f"Výchozí interval: **{AUTO_RESTART_INTERVAL_MINUTES} min**.",
                ],
            )
            return
        next_restart_at = runtime.get("next_restart_at") if runtime else None
        last_restart_at = runtime.get("last_restart_at") if runtime else None
        await self._send_view(
            interaction,
            [
                "## ℹ️ Restart scheduler",
                f"Stav: {'zapnuto' if setting['enabled'] else 'vypnuto'}.",
                f"Interval: **{setting['interval_minutes']} min**.",
                f"Další restart: `{next_restart_at or 'nenaplánováno'}`.",
                f"Poslední plánovaný restart: `{last_restart_at or 'neznámý'}`.",
            ],
        )
