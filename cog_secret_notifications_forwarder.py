import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    CLAN2_MEMBER_ROLE_ID,
    CLAN3_MEMBER_ROLE_ID,
    CLAN_MEMBER_ROLE_EN_ID,
    CLAN_MEMBER_ROLE_ID,
    SETUP_MANAGER_ROLE_ID,
)
from db import (
    add_dropstats_panel,
    get_all_dropstats_panels,
    get_connection,
    get_secret_drop_totals,
    increment_secret_drop_stat,
    remove_dropstats_panel,
)
from db import (
    add_dropstats_panel,
    get_all_dropstats_panels,
    get_connection,
    get_secret_drop_totals,
    increment_secret_drop_stat,
    remove_dropstats_panel,
)


API_TOKEN = "4613641698541651646845196419864189654"
API_URL = "https://ezrz.eu/secret/api/fetch.php?limit=50&ack=1"
CHANNEL_ID = 1454386651831734324
SETTINGS_KEY_LAST_SUCCESS = "secret_notifications_last_success_at"
SETTINGS_KEY_CLAN_MEMBER_CACHE = "secret_notifications_clan_member_cache"
SETTINGS_KEY_CLAN_MEMBER_CACHE_UPDATED = (
    "secret_notifications_clan_member_cache_updated_at"
)
CLAN_MEMBER_ROLE_IDS = [
    CLAN_MEMBER_ROLE_ID,
    CLAN_MEMBER_ROLE_EN_ID,
    CLAN2_MEMBER_ROLE_ID,
    CLAN3_MEMBER_ROLE_ID,
]

logger = logging.getLogger("botdc.secret_notifications")
logger.disabled = True


class SecretNotificationsForwarder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._clan_member_cache: dict[str, dict[str, Any]] = {}
        self._clan_member_cache_updated_at: Optional[datetime] = None
        self._load_cached_players_from_db()
        self.dropstats_group = app_commands.Group(
            name="dropstats", description="Statistiky dropu"
        )
        self.dropstats_group.command(
            name="leaderboard", description="Zobrazí celkový žebříček dropů."
        )(self.dropstats_leaderboard)
        self.dropstats_group.command(
            name="setup", description="Odešle do vybraného kanálu dropstats panel."
        )(self.dropstats_setup)
        existing_group = self.bot.tree.get_command(
            "dropstats", type=discord.AppCommandType.chat_input
        )
        if existing_group:
            self.bot.tree.remove_command(
                "dropstats", type=discord.AppCommandType.chat_input
            )
        self.bot.tree.add_command(self.dropstats_group)
        self.poll_notifications.start()
        self.refresh_clan_member_cache.start()

    def cog_unload(self):
        self.poll_notifications.cancel()
        self.refresh_clan_member_cache.cancel()
        self.bot.tree.remove_command("dropstats", type=discord.AppCommandType.chat_input)

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

            if notifications:
                logger.info("Přijaté notifikace: %s", len(notifications))
            else:
                return

            updated_stats = False
            for notification in notifications:
                lines = self._format_message_lines(notification)
                if not lines:
                    continue
                text_body = "\n".join(lines[1:]) if len(lines) > 1 else ""
                if not self._should_forward(text_body):
                    continue
                matched_players = self._find_player_mentions(text_body)
                if not matched_players:
                    continue
                lines.append(f"Hráč: {' '.join(self._format_mentions(matched_players))}")
                self._record_drop_stats(matched_players)
                updated_stats = True
                view = self._build_view(lines)
                try:
                    await channel.send(
                        view=view,
                        allowed_mentions=discord.AllowedMentions(users=True),
                    )
                except Exception:
                    logger.exception("Odeslání notifikace do Discordu selhalo.")
                await asyncio.sleep(0.3)
            if updated_stats:
                await self.refresh_dropstats_panels()
        except Exception:
            logger.exception("Neočekávaná chyba v notifikační smyčce.")

    @poll_notifications.before_loop
    async def before_poll_notifications(self):
        await self.bot.wait_until_ready()
        logger.info("Startuji smyčku pro přeposílání secret notifikací.")
        await self.refresh_dropstats_panels()

    @tasks.loop(minutes=10)
    async def refresh_clan_member_cache(self):
        try:
            await self._refresh_clan_member_cache()
        except Exception:
            logger.exception("Neočekávaná chyba při obnově cache hráčů.")

    @refresh_clan_member_cache.before_loop
    async def before_refresh_clan_member_cache(self):
        await self.bot.wait_until_ready()
        logger.info("Startuji smyčku pro obnovu cache hráčů v clanu.")
        await self._refresh_clan_member_cache()

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
        try:
            payload = await asyncio.to_thread(self._fetch_notifications_sync)
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

    def _fetch_notifications_sync(self) -> Any:
        import urllib.request

        headers = {"X-Secret-Token": API_TOKEN}
        request = urllib.request.Request(API_URL, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status != 200:
                    logger.error("HTTP chyba při fetchi notifikací: %s", response.status)
                    return None
                data = response.read()
        except Exception:
            logger.exception("HTTP požadavek na notifikace selhal.")
            return None

        try:
            return json.loads(data)
        except Exception:
            logger.exception("JSON parse selhal u odpovědi notifikací.")
            return None

    def _format_message_lines(self, notification: Dict[str, Any]) -> Optional[List[str]]:
        try:
            app_display_name = notification.get("app_display_name")
            app_user_model_id = notification.get("app_user_model_id")
            app_name = app_display_name or app_user_model_id or "unknown"

            text_joined = notification.get("text_joined")
            text_line = text_joined or self._extract_text_from_raw(notification)

            line1 = f"[APP] {app_name}"
            text_lines = (text_line or "").splitlines() or [""]
            return [line1, *text_lines]
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

    def _should_forward(self, text_line: str) -> bool:
        try:
            lowered = (text_line or "").lower()
            return "hatched" in lowered or "rolled" in lowered
        except Exception:
            logger.exception("Chyba při filtrování textu notifikace.")
            return False

    def _find_player_mentions(self, text_line: str) -> List[int]:
        try:
            if not text_line:
                return []
            lower_text = text_line.lower()
            matched_ids = []
            seen_ids = set()
            for name, entry in self._clan_member_cache.items():
                if name and name in lower_text:
                    member_id = entry.get("id")
                    if member_id not in seen_ids:
                        matched_ids.append(int(member_id))
                        seen_ids.add(member_id)
            return matched_ids
        except Exception:
            logger.exception("Chyba při vyhledání hráče v textu notifikace.")
            return []

    def _format_mentions(self, player_ids: List[int]) -> List[str]:
        return [f"<@{player_id}>" for player_id in player_ids]

    def _build_view(self, lines: List[str]) -> discord.ui.LayoutView:
        view = discord.ui.LayoutView()
        container = discord.ui.Container()
        for line in self._normalize_lines(lines):
            container.add_item(discord.ui.TextDisplay(content=line))
        view.add_item(container)
        return view

    def _normalize_lines(self, lines: List[str]) -> List[str]:
        normalized: List[str] = []
        for line in lines:
            if line is None:
                normalized.append(" ")
                continue
            text = str(line)
            if text == "":
                normalized.append(" ")
                continue
            while text:
                chunk = text[:4000]
                if chunk == "":
                    chunk = " "
                normalized.append(chunk)
                text = text[4000:]
        return normalized

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

    def _load_cached_players_from_db(self) -> None:
        conn = None
        try:
            conn = get_connection()
            cursor = conn.execute(
                "SELECT key, value FROM settings WHERE key IN (?, ?)",
                (SETTINGS_KEY_CLAN_MEMBER_CACHE, SETTINGS_KEY_CLAN_MEMBER_CACHE_UPDATED),
            )
            rows = cursor.fetchall()
            data = {row[0]: row[1] for row in rows}
            cache_raw = data.get(SETTINGS_KEY_CLAN_MEMBER_CACHE)
            if cache_raw:
                cache_data = json.loads(cache_raw)
                if isinstance(cache_data, dict):
                    migrated_cache: dict[str, dict[str, Any]] = {}
                    for name, entry in cache_data.items():
                        if not name:
                            continue
                        normalized = str(name).lower()
                        if isinstance(entry, dict):
                            member_id = entry.get("id")
                            display_name = entry.get("name") or name
                        else:
                            member_id = entry
                            display_name = name
                        if isinstance(member_id, (int, str)):
                            migrated_cache[normalized] = {
                                "id": int(member_id),
                                "name": str(display_name),
                            }
                    self._clan_member_cache = migrated_cache
            updated_raw = data.get(SETTINGS_KEY_CLAN_MEMBER_CACHE_UPDATED)
            if updated_raw:
                self._clan_member_cache_updated_at = datetime.fromisoformat(updated_raw)
        except Exception:
            logger.exception("Načtení cache hráčů z DB selhalo.")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    logger.exception("Uzavření DB spojení selhalo.")

    async def _refresh_clan_member_cache(self) -> None:
        channel = await self._get_channel()
        if channel is None:
            return
        guild = getattr(channel, "guild", None)
        if guild is None:
            logger.warning("Nelze načíst guild z kanálu %s.", CHANNEL_ID)
            return
        new_cache: dict[str, dict[str, Any]] = {}
        for role_id in [rid for rid in CLAN_MEMBER_ROLE_IDS if rid]:
            role = guild.get_role(role_id)
            if role is None:
                logger.warning("Role %s nebyla nalezena pro cache hráčů.", role_id)
                continue
            for member in role.members:
                names = {member.display_name, member.name}
                global_name = getattr(member, "global_name", None)
                if global_name:
                    names.add(global_name)
                for name in names:
                    if not name:
                        continue
                    normalized = str(name).lower()
                    if normalized not in new_cache:
                        new_cache[normalized] = {
                            "id": member.id,
                            "name": str(member.display_name),
                        }

        if new_cache:
            self._clan_member_cache = new_cache
            self._clan_member_cache_updated_at = datetime.now(timezone.utc)
            self._save_clan_member_cache()
            logger.info("Obnovena cache hráčů v clanu: %s", len(new_cache))
        else:
            logger.warning("Cache hráčů v clanu nebyla obnovena (žádní členové).")

    def _save_clan_member_cache(self) -> None:
        if not self._clan_member_cache_updated_at:
            self._clan_member_cache_updated_at = datetime.now(timezone.utc)
        conn = None
        try:
            conn = get_connection()
            cache_payload = json.dumps(self._clan_member_cache)
            updated_payload = self._clan_member_cache_updated_at.isoformat()
            with conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    [
                        (SETTINGS_KEY_CLAN_MEMBER_CACHE, cache_payload),
                        (SETTINGS_KEY_CLAN_MEMBER_CACHE_UPDATED, updated_payload),
                    ],
                )
        except Exception:
            logger.exception("Uložení cache hráčů do DB selhalo.")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    logger.exception("Uzavření DB spojení selhalo.")

    def _record_drop_stats(self, player_ids: List[int]) -> None:
        if not player_ids:
            return
        date_value = datetime.now(timezone.utc).date().isoformat()
        for player_id in player_ids:
            try:
                increment_secret_drop_stat(date_value, int(player_id), 1)
            except Exception:
                logger.exception("Uložení denní statistiky dropu selhalo.")

    async def dropstats_leaderboard(self, interaction: discord.Interaction):
        view = self._build_dropstats_view()
        await interaction.response.send_message(
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.describe(channel="Kanál, kam se má dropstats panel poslat.")
    async def dropstats_setup(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        view = self._build_dropstats_view()
        message = await channel.send(
            view=view, allowed_mentions=discord.AllowedMentions.none()
        )
        if interaction.guild:
            add_dropstats_panel(interaction.guild.id, channel.id, message.id)
        await interaction.response.send_message(
            f"Dropstats panel byl odeslán do {channel.mention}.", ephemeral=True
        )

    def _build_dropstats_view(self) -> discord.ui.LayoutView:
        view = discord.ui.LayoutView()
        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay(content="🏆 Dropstats leaderboard"))
        container.add_item(
            discord.ui.TextDisplay(
                content="Celkový přehled dropů pro všechny členy clanů."
            )
        )
        container.add_item(discord.ui.Separator())

        members = self._get_clan_member_entries()
        if not members:
            container.add_item(
                discord.ui.TextDisplay(content="Žádní členové clanů nebyli nalezeni.")
            )
            view.add_item(container)
            return view

        totals = self._get_drop_totals_safe()
        sorted_members = sorted(
            members.items(),
            key=lambda item: (-totals.get(item[0], 0), item[1].lower()),
        )
        lines = [
            f"{idx}. <@{user_id}> — {totals.get(user_id, 0)}"
            for idx, (user_id, _) in enumerate(sorted_members, start=1)
        ]
        for chunk in self._chunk_lines(lines):
            container.add_item(discord.ui.TextDisplay(content=chunk))

        updated_at = int(datetime.now(timezone.utc).timestamp())
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(content=f"Aktualizováno: <t:{updated_at}:R>")
        )
        view.add_item(container)
        return view

    def _get_drop_totals_safe(self) -> dict[int, int]:
        try:
            return get_secret_drop_totals()
        except Exception:
            logger.exception("Načtení statistiky dropu selhalo.")
            return {}

    def _get_clan_member_entries(self) -> dict[int, str]:
        members: dict[int, str] = {}
        for entry in self._clan_member_cache.values():
            member_id = entry.get("id")
            name = entry.get("name") or str(member_id)
            if isinstance(member_id, int):
                members.setdefault(member_id, str(name))
        return members

    def _chunk_lines(self, lines: List[str], max_len: int = 3500) -> List[str]:
        chunks: List[str] = []
        current: List[str] = []
        current_len = 0
        for line in lines:
            addition = len(line) + (1 if current else 0)
            if current and current_len + addition > max_len:
                chunks.append("\n".join(current))
                current = [line]
                current_len = len(line)
            else:
                current.append(line)
                current_len += addition
        if current:
            chunks.append("\n".join(current))
        return chunks

    async def refresh_dropstats_panels(self) -> None:
        panels = get_all_dropstats_panels()
        if not panels:
            return

        view = self._build_dropstats_view()
        for guild_id, channel_id, message_id in panels:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                remove_dropstats_panel(message_id)
                continue

            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                remove_dropstats_panel(message_id)
                continue

            try:
                msg = await channel.fetch_message(message_id)
            except discord.NotFound:
                remove_dropstats_panel(message_id)
                continue
            except discord.HTTPException:
                continue

            try:
                await msg.edit(
                    view=view, allowed_mentions=discord.AllowedMentions.none()
                )
                await asyncio.sleep(0.25)
            except discord.HTTPException:
                continue
