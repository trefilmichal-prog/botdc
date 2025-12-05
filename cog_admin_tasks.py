import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional

import discord
from discord.ext import commands, tasks

from config import ADMIN_TASK_CHANNEL_ID, ADMIN_TASK_DB_PATH


class AdminTasks(commands.Cog):
    """Polls the PHP admin SQLite DB and forwards tasks to Discord."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("botdc.admin_tasks")
        self._channel_status: Optional[str] = None
        self._ensure_schema()
        self.poll_admin_tasks.start()

    def _format_channel_reference(
        self, channel: Optional[discord.abc.GuildChannel] = None
    ) -> str:
        if isinstance(channel, discord.abc.GuildChannel):
            return f"#{channel.name} (ID {channel.id})"

        return f"<#{ADMIN_TASK_CHANNEL_ID}> (ID {ADMIN_TASK_CHANNEL_ID})"

    def cog_unload(self):
        self.poll_admin_tasks.cancel()

    def _get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(ADMIN_TASK_DB_PATH)

    def _ensure_schema(self) -> None:
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT,
                params TEXT,
                created_at TEXT,
                processed INTEGER NOT NULL DEFAULT 0,
                processed_at TEXT
            )
            """
        )

        cursor.execute("PRAGMA table_info(tasks)")
        columns = {row[1] for row in cursor.fetchall()}
        if "processed" not in columns:
            cursor.execute(
                "ALTER TABLE tasks ADD COLUMN processed INTEGER NOT NULL DEFAULT 0"
            )
        if "processed_at" not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN processed_at TEXT")

        conn.commit()
        conn.close()

    def _fetch_unprocessed_tasks(self):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, action, params, created_at FROM tasks WHERE processed = 0 ORDER BY id ASC"
        )
        rows = cursor.fetchall()
        conn.close()
        return rows

    def _mark_task_processed(self, task_id: int) -> None:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE tasks SET processed = 1, processed_at = ? WHERE id = ?",
            (datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), task_id),
        )
        conn.commit()
        conn.close()

    def _update_channel_status(
        self, status: str, log_method, message: str, *args
    ) -> None:
        if status != self._channel_status:
            log_method(message, *args)
            self._channel_status = status

    async def _get_admin_channel(self) -> Optional[discord.TextChannel]:
        channel = self.bot.get_channel(ADMIN_TASK_CHANNEL_ID)
        if channel is not None:
            if isinstance(channel, discord.TextChannel):
                self._update_channel_status(
                    "ok",
                    self.logger.info,
                    "Admin task channel %s is accessible",
                    self._format_channel_reference(channel),
                )
                return channel

            self._update_channel_status(
                "not_text",
                self.logger.warning,
                "Admin task channel %s is not a text channel",
                self._format_channel_reference(channel),
            )
            return None

        try:
            fetched = await self.bot.fetch_channel(ADMIN_TASK_CHANNEL_ID)
        except (discord.Forbidden, discord.NotFound):
            self._update_channel_status(
                "not_accessible",
                self.logger.warning,
                "Admin task channel %s is not accessible; check the channel ID and bot permissions",
                self._format_channel_reference(),
            )
            return None
        except discord.HTTPException:
            self._update_channel_status(
                "fetch_failed",
                self.logger.exception,
                "Failed to fetch admin task channel %s",
                self._format_channel_reference(),
            )
            return None

        if isinstance(fetched, discord.TextChannel):
            self._update_channel_status(
                "ok",
                self.logger.info,
                "Admin task channel %s is accessible",
                self._format_channel_reference(fetched),
            )
            return fetched

        self._update_channel_status(
            "not_text",
            self.logger.warning,
            "Admin task channel %s is not a text channel",
            self._format_channel_reference(fetched),
        )
        return None

    @tasks.loop(seconds=30)
    async def poll_admin_tasks(self):
        await self.bot.wait_until_ready()
        channel = await self._get_admin_channel()
        if channel is None:
            return

        for task_id, action, params, created_at in self._fetch_unprocessed_tasks():
            try:
                parsed = json.loads(params) if params else None
                params_str = json.dumps(parsed, indent=2, ensure_ascii=False) if parsed else params or "(žádné parametry)"
            except json.JSONDecodeError:
                params_str = params or "(žádné parametry)"

            embed = discord.Embed(
                title=f"Nový admin úkol #{task_id}",
                description=f"**Akce:** {action}\n**Vytvořeno:** {created_at}",
                color=discord.Color.blurple(),
                timestamp=datetime.utcnow(),
            )
            embed.add_field(name="Parametry", value=f"```json\n{params_str}\n```", inline=False)

            try:
                await channel.send(embed=embed)
                self.logger.info("Zpracován úkol %s (%s)", task_id, action)
                self._mark_task_processed(task_id)
            except Exception:
                self.logger.exception("Nepodařilo se odeslat admin úkol %s", task_id)

    @poll_admin_tasks.before_loop
    async def before_poll_admin_tasks(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminTasks(bot))
