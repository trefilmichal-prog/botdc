import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiohttp
import discord
from discord.ext import commands, tasks

from db import get_connection


API_TOKEN = "4613641698541651646845196419864189654"
API_URL = "https://ezrz.eu/secret/api/fetch.php?limit=50&ack=1"
CHANNEL_ID = 1454386651831734324
SETTINGS_KEY_LAST_SUCCESS = "secret_notifications_last_success_at"

logger = logging.getLogger("botdc.secret_notifications")


class SecretNotificationsForwarder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        timeout = aiohttp.ClientTimeout(total=10)
        self.session = aiohttp.ClientSession(timeout=timeout)
        self.poll_notifications.start()

    def cog_unload(self):
        self.poll_notifications.cancel()
        if not self.session.closed:
            asyncio.create_task(self.session.close())

    @tasks.loop(seconds=2.5)
    async def poll_notifications(self):
        try:
            channel = await self._get_channel()
            if channel is None:
                logger.warning("Kanál %s nebyl nalezen.", CHANNEL_ID)
                return

            notifications = await self._fetch_notifications()
            if notifications is None:
                return

            logger.info("Přijaté notifikace: %s", len(notifications))
            if not notifications:
                return

            for notification in notifications:
                content = self._format_message(notification)
                if content is None:
                    continue
                try:
                    await channel.send(content)
                except Exception:
                    logger.exception("Odeslání notifikace do Discordu selhalo.")
                await asyncio.sleep(0.3)
        except Exception:
            logger.exception("Neočekávaná chyba v notifikační smyčce.")

    @poll_notifications.before_loop
    async def before_poll_notifications(self):
        await self.bot.wait_until_ready()
        logger.info("Startuji smyčku pro přeposílání secret notifikací.")

    async def _get_channel(self) -> Optional[discord.abc.Messageable]:
        try:
            channel = self.bot.get_channel(CHANNEL_ID)
            if channel is not None:
                return channel
            return await self.bot.fetch_channel(CHANNEL_ID)
        except Exception:
            logger.exception("Nepodařilo se načíst kanál %s.", CHANNEL_ID)
            return None

    async def _fetch_notifications(self) -> Optional[List[Dict[str, Any]]]:
        headers = {"X-Secret-Token": API_TOKEN}
        try:
            async with self.session.get(API_URL, headers=headers) as response:
                if response.status != 200:
                    logger.error("HTTP chyba při fetchi notifikací: %s", response.status)
                    return None
                try:
                    payload = await response.json()
                except Exception:
                    logger.exception("JSON parse selhal u odpovědi notifikací.")
                    return None
        except Exception:
            logger.exception("HTTP požadavek na notifikace selhal.")
            return None

        if not isinstance(payload, dict):
            logger.error("Neočekávaný formát JSON odpovědi.")
            return None

        if not payload.get("ok", False):
            logger.error("API vrátilo ok=false, ignoruji odpověď.")
            return None

        notifications = payload.get("notifications") or []
        if not isinstance(notifications, list):
            logger.error("Pole notifications má neočekávaný formát.")
            return None

        count = payload.get("count")
        if isinstance(count, int) and count == 0:
            self._update_last_success()
            return []

        self._update_last_success()
        return notifications

    def _format_message(self, notification: Dict[str, Any]) -> Optional[str]:
        try:
            app_display_name = notification.get("app_display_name")
            app_user_model_id = notification.get("app_user_model_id")
            app_name = app_display_name or app_user_model_id or "unknown"

            text_joined = notification.get("text_joined")
            text_line = text_joined or self._extract_text_from_raw(notification)

            creation_time = notification.get("creation_time") or notification.get(
                "created_at"
            )
            observed_at = notification.get("observed_at")
            notification_id = notification.get("id")

            line1 = f"[APP] {app_name}"
            line2 = text_line or ""
            line3 = (
                f"created: {creation_time} | observed: {observed_at} | id: {notification_id}"
            )
            return "\n".join([line1, line2, line3])
        except Exception:
            logger.exception("Chyba při formátování notifikace.")
            return None

    def _extract_text_from_raw(self, notification: Dict[str, Any]) -> str:
        raw_json = notification.get("raw_json")
        if not raw_json:
            return ""
        try:
            raw_payload = json.loads(raw_json)
        except Exception:
            logger.exception("JSON parse selhal u raw_json notifikace.")
            return ""

        text_value = raw_payload.get("notification", {}).get("text")
        if isinstance(text_value, list):
            return "\n".join(str(item) for item in text_value)
        if isinstance(text_value, str):
            return text_value
        return ""

    def _update_last_success(self) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        conn = None
        try:
            conn = get_connection()
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (SETTINGS_KEY_LAST_SUCCESS, timestamp),
                )
        except Exception:
            logger.exception("Uložení timestampu do DB selhalo.")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    logger.exception("Uzavření DB spojení selhalo.")
