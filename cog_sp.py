import json
import asyncio
import re
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime
from typing import List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import ADMIN_TASK_DB_PATH, REBIRTH_DATA_URL, SETUP_MANAGER_ROLE_ID
from db import (
    add_sp_panel,
    get_all_sp_panels,
    get_sp_panel_for_guild,
    remove_sp_panel,
)


RebirthRow = Tuple[str, str, str, str, str]


class RebirthPanel(commands.Cog, name="RebirthPanel"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.refresh_loop.start()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.refresh_loop.is_running():
            self.refresh_loop.start()

        await self.refresh_sp_panels()

    def cog_unload(self):
        self.refresh_loop.cancel()

    def _get_admin_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(ADMIN_TASK_DB_PATH)

    def _ensure_rebirth_table(self) -> None:
        conn = self._get_admin_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS member_rebirths (
                user_id TEXT PRIMARY KEY,
                display_name TEXT,
                rebirths TEXT NOT NULL DEFAULT '',
                previous_rebirths TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )

        cursor.execute("PRAGMA table_info(member_rebirths)")
        columns = {row[1] for row in cursor.fetchall()}
        if "previous_rebirths" not in columns:
            cursor.execute(
                "ALTER TABLE member_rebirths ADD COLUMN previous_rebirths TEXT NOT NULL DEFAULT ''"
            )

        conn.commit()
        conn.close()

    def _parse_rebirth_to_number(self, value: str) -> Optional[float]:
        value = value.strip()
        if not value:
            return None

        suffixes = {
            "k": 3,
            "m": 6,
            "b": 9,
            "t": 12,
            "qa": 15,
            "qi": 18,
            "sx": 21,
            "sp": 24,
            "oc": 27,
            "no": 30,
            "dc": 33,
        }

        match = re.match(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*([a-zA-Z]{0,2})\s*$", value)
        if not match:
            return None

        numeric_part = match.group(1).replace(",", ".")
        suffix = match.group(2).lower()

        try:
            base_value = float(numeric_part)
        except ValueError:
            return None

        if suffix == "":
            return base_value

        if suffix not in suffixes:
            return None

        exponent = suffixes[suffix]
        return base_value * (10 ** exponent)

    def _fetch_rebirth_rows(self) -> List[RebirthRow]:
        remote_rows = self._fetch_remote_rebirth_rows()
        if remote_rows is None:
            return self._fetch_rebirth_rows_from_db()

        if remote_rows:
            self._save_rebirth_rows_to_db(remote_rows)
            return remote_rows

        cached_rows = self._fetch_rebirth_rows_from_db()
        if cached_rows:
            return cached_rows

        return []

    def _save_rebirth_rows_to_db(self, rows: List[RebirthRow]) -> None:
        self._ensure_rebirth_table()
        conn = self._get_admin_connection()
        cursor = conn.cursor()

        existing_rows: dict[str, tuple[str, str]] = {}
        cursor.execute(
            "SELECT user_id, rebirths, previous_rebirths FROM member_rebirths"
        )
        for user_id, rebirths, previous_rebirths in cursor.fetchall():
            existing_rows[user_id] = (rebirths, previous_rebirths)

        cursor.execute("DELETE FROM member_rebirths")
        def resolve_previous(
            user_id: str, rebirths: str, provided_previous: str
        ) -> str:
            existing_rebirths, existing_previous = existing_rows.get(
                user_id, ("", "")
            )

            if provided_previous:
                return provided_previous

            if not existing_rebirths:
                return ""

            if rebirths != existing_rebirths:
                return existing_rebirths

            return existing_previous

        cursor.executemany(
            """
            INSERT INTO member_rebirths (
                user_id,
                display_name,
                rebirths,
                previous_rebirths,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    user_id,
                    display_name,
                    rebirths,
                    resolve_previous(user_id, rebirths, previous_rebirths),
                    updated_at,
                )
                for user_id, display_name, rebirths, previous_rebirths, updated_at in rows
            ],
        )
        conn.commit()
        conn.close()

    def _fetch_remote_rebirth_rows(self) -> Optional[List[RebirthRow]]:
        if not REBIRTH_DATA_URL:
            return None

        try:
            request = urllib.request.Request(REBIRTH_DATA_URL)

            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status != 200:
                    return None

                payload = json.loads(response.read().decode("utf-8"))

        except (urllib.error.URLError, json.JSONDecodeError):
            return None

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return None

        rows: List[RebirthRow] = []
        for row in data:
            if not isinstance(row, dict):
                continue

            user_id = str(row.get("user_id", "")).strip()
            display_name = str(row.get("display_name", "")).strip()
            rebirths = str(row.get("rebirths", "")).strip()
            previous_rebirths = str(row.get("previous_rebirths", "")).strip()
            updated_at = str(row.get("updated_at", "")).strip()

            if not user_id:
                continue

            rows.append((user_id, display_name, rebirths, previous_rebirths, updated_at))

        return rows

    def _fetch_rebirth_rows_from_db(self) -> List[RebirthRow]:
        self._ensure_rebirth_table()
        conn = self._get_admin_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                user_id,
                display_name,
                rebirths,
                previous_rebirths,
                updated_at
            FROM member_rebirths
            """
        )
        rows: List[RebirthRow] = cursor.fetchall()
        conn.close()
        return rows

    def _build_rebirth_view(self) -> discord.ui.LayoutView:
        rows = self._fetch_rebirth_rows()
        view = discord.ui.LayoutView(timeout=None)
        header_lines = ["## Rebirth tabulka z webu", "Aktualizace každých 5 minut"]

        if not rows:
            header_lines.append("Zatím nejsou dostupná žádná data z webu.")
            view.add_item(
                discord.ui.Container(
                    *(discord.ui.TextDisplay(content=line) for line in header_lines)
                )
            )
            return view

        def sort_key(row: RebirthRow):
            parsed = self._parse_rebirth_to_number(row[2])
            # pořadí: validní hodnoty od nejvyšších, pak podle jména
            return (-(parsed or -1), row[1].lower())

        sorted_rows = sorted(rows, key=sort_key)
        row_lines = []
        for idx, (_, display_name, rebirths, previous_rebirths, _) in enumerate(
            sorted_rows, start=1
        ):
            previous = previous_rebirths or "neuvedeno"
            row_lines.append(f"**{idx}.** {display_name} – {rebirths} <- ({previous})")

        output_lines = header_lines + row_lines[:25]
        if len(row_lines) > 25:
            output_lines.append(f"Zobrazuji prvních 25 z {len(row_lines)} záznamů.")

        output_lines.append(
            f"Naposledy obnoveno: {datetime.utcnow():%Y-%m-%d %H:%M:%S} UTC"
        )
        view.add_item(
            discord.ui.Container(
                *(discord.ui.TextDisplay(content=line) for line in output_lines)
            )
        )
        return view

    async def _remove_existing_panel(self, guild_id: int) -> None:
        existing = get_sp_panel_for_guild(guild_id)
        if existing is None:
            return

        _, channel_id, message_id = existing
        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel(channel_id) if guild else None
        if isinstance(channel, discord.TextChannel):
            try:
                message = await channel.fetch_message(message_id)
                await message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        remove_sp_panel(message_id)

    @app_commands.command(
        name="setup_sp",
        description="Propojí panel s webovou tabulkou rebirthů (aktualizace každých 5 minut)",
    )
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.checks.has_role(SETUP_MANAGER_ROLE_ID)
    async def setup_sp(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Tento příkaz lze použít pouze na serveru.", ephemeral=True
            )
            return

        await self._remove_existing_panel(interaction.guild.id)

        view = self._build_rebirth_view()
        message = await channel.send(content="", view=view)
        add_sp_panel(interaction.guild.id, channel.id, message.id)

        await interaction.response.send_message(
            f"Panel s rebirthy byl odeslán do {channel.mention}.", ephemeral=True
        )

    async def refresh_sp_panels(self):
        panels = get_all_sp_panels()
        if not panels:
            return

        view = self._build_rebirth_view()

        for guild_id, channel_id, message_id in panels:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                remove_sp_panel(message_id)
                continue

            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                remove_sp_panel(message_id)
                continue

            try:
                message = await channel.fetch_message(message_id)
            except discord.NotFound:
                remove_sp_panel(message_id)
                continue
            except discord.HTTPException:
                continue

            try:
                await message.edit(content="", embeds=[], view=view)
                await asyncio.sleep(0.25)
            except discord.HTTPException:
                continue

    @tasks.loop(minutes=5)
    async def refresh_loop(self):
        try:
            await self.refresh_sp_panels()
        except Exception as exc:  # pragma: no cover - defensive logging
            print(f"[sp_panel] Chyba při obnově panelů: {exc}")

    @refresh_loop.before_loop
    async def before_refresh_loop(self):
        await self.bot.wait_until_ready()
        await self.refresh_sp_panels()
