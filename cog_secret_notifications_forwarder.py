import asyncio
import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List, Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    CLAN2_MEMBER_ROLE_ID,
    CLAN3_MEMBER_ROLE_ID,
    CLAN_MEMBER_ROLE_EN_ID,
    CLAN_MEMBER_ROLE_ID,
    SETUP_MANAGER_ROLE_ID,
    WINRT_LOG_PATH,
)
from cog_clan import CLAN_MEMBER_ROLE_IDS as CLAN_MEMBER_ROLE_IDS_BY_KEY
from db import (
    add_dropstats_panel,
    add_secret_drop_event,
    delete_windows_notifications,
    get_all_dropstats_panels,
    get_connection,
    get_secret_drop_breakdown_all_time,
    get_secret_notifications_role_ids,
    get_windows_notifications,
    increment_secret_drop_stat,
    list_clan_definitions,
    normalize_clan_member_name,
    remove_dropstats_panel,
    reset_secret_drop_stats,
    set_secret_notifications_role_ids,
)


CHANNEL_ID = 1454386651831734324
SETTINGS_KEY_CLAN_MEMBER_CACHE = "secret_notifications_clan_member_cache"
SETTINGS_KEY_CLAN_MEMBER_CACHE_UPDATED = (
    "secret_notifications_clan_member_cache_updated_at"
)
SETTINGS_KEY_LAST_NOTIFICATION_ID = "secret_notifications_last_notification_id"
CLAN_MEMBER_ROLE_IDS = [
    CLAN_MEMBER_ROLE_ID,
    CLAN_MEMBER_ROLE_EN_ID,
    CLAN2_MEMBER_ROLE_ID,
    CLAN3_MEMBER_ROLE_ID,
]
ROBLOX_USERNAMES_URL = "https://users.roblox.com/v1/usernames/users"
ROBLOX_USERNAME_REGEX = re.compile(r"[A-Za-z0-9_]{3,26}")
ROBLOX_USERNAME_BATCH_SIZE = 50
ROBLOX_USERNAME_REQUEST_DELAY_SECONDS = 0.6
ROBLOX_NICK_REFRESH_MINUTES = 10
CONGRATS_LINE_REGEX = re.compile(
    r"^🔥\s*Congrats!\s*:flag_[a-z]{2}:", re.IGNORECASE
)
NOTIFICATION_HEADER_SKIP_REGEX = re.compile(
    r"Secrets Hatched.*#🐾┃secrets-hatched.*REBIRTH CHAMPIONS"
)

logger = logging.getLogger("botdc.secret_notifications")
winrt_logger = logging.getLogger("botdc.winrt_notifications")
if not any(
    isinstance(handler, RotatingFileHandler)
    and getattr(handler, "baseFilename", None) == WINRT_LOG_PATH
    for handler in winrt_logger.handlers
):
    winrt_handler = RotatingFileHandler(
        WINRT_LOG_PATH,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    winrt_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    )
    winrt_logger.addHandler(winrt_handler)
    winrt_logger.setLevel(logging.INFO)
    winrt_logger.propagate = False


class SecretNotificationsForwarder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._clan_member_cache: dict[str, dict[str, Any]] = {}
        self._clan_member_cache_updated_at: Optional[datetime] = None
        self._received_notifications_count = 0
        self._last_processed_notification_id: Optional[int] = None
        self._secret_role_ids = self._load_secret_role_ids()
        self._load_cached_players_from_db()
        self._load_last_processed_notification_id()
        existing_group = self.bot.tree.get_command(
            "dropstats", type=discord.AppCommandType.chat_input
        )
        if existing_group:
            self.bot.tree.remove_command(
                "dropstats", type=discord.AppCommandType.chat_input
            )
        existing_secret = self.bot.tree.get_command(
            "secret", type=discord.AppCommandType.chat_input
        )
        if existing_secret:
            self.bot.tree.remove_command(
                "secret", type=discord.AppCommandType.chat_input
            )
        self.dropstats_group = app_commands.Group(
            name="dropstats", description="Statistiky dropu"
        )
        self.dropstats_group.command(
            name="leaderboard", description="Zobrazí celkový žebříček dropů."
        )(self.dropstats_leaderboard)
        self.dropstats_group.command(
            name="setup", description="Odešle do vybraného kanálu dropstats panel."
        )(self.dropstats_setup)
        self.dropstats_group.command(
            name="reset", description="Resetuje dropstats leaderboard."
        )(self.dropstats_reset)
        self.secret_group = app_commands.Group(
            name="secret", description="Secret notifikace"
        )
        self.secret_roles_group = app_commands.Group(
            name="roles",
            description="Správa rolí pro secret notifikace",
        )
        self.secret_roles_group.command(
            name="add", description="Přidá roli pro secret notifikace."
        )(self.secret_roles_add)
        self.secret_roles_group.command(
            name="remove", description="Odebere roli pro secret notifikace."
        )(self.secret_roles_remove)
        self.secret_group.add_command(self.secret_roles_group)
        self.secret_group.command(
            name="cache",
            description="Zobrazí uložená jména hráčů pro notifikace.",
        )(self.secret_cache)
        self.secret_group.command(
            name="refresh",
            description="Vynutí refresh Roblox přezdívky pro člena.",
        )(self.secret_cache_refresh)
        self.bot.tree.add_command(self.dropstats_group)
        self.bot.tree.add_command(self.secret_group)
        self.poll_notifications.start()
        self.log_notification_stats.start()
        self.refresh_clan_member_cache.start()

    def cog_unload(self):
        self.poll_notifications.cancel()
        self.log_notification_stats.cancel()
        self.refresh_clan_member_cache.cancel()
        self.bot.tree.remove_command("dropstats", type=discord.AppCommandType.chat_input)
        self.bot.tree.remove_command("secret", type=discord.AppCommandType.chat_input)

    @tasks.loop(seconds=2.5)
    async def poll_notifications(self):
        sent_ids: List[int] = []
        discarded_ids: List[int] = []
        max_processed_id: Optional[int] = None
        success = False
        try:
            channel = await self._get_channel()
            if channel is None:
                logger.warning("Kanál %s nebyl nalezen.", CHANNEL_ID)
                return

            notifications = await self._fetch_notifications()
            if notifications is None:
                return

            if not notifications:
                return

            updated_stats = False
            for notification in notifications:
                notification_id = notification.get("id")
                if isinstance(notification_id, int):
                    max_processed_id = (
                        notification_id
                        if max_processed_id is None
                        else max(max_processed_id, notification_id)
                    )
                payload = notification.get("payload", {})
                timestamp = datetime.now(timezone.utc).isoformat()
                winrt_logger.info(
                    "WinRT notification received | timestamp=%s | notification_id=%s | payload=%s",
                    timestamp,
                    notification_id,
                    json.dumps(payload, ensure_ascii=False, default=str),
                )
                lines = self._format_message_lines(payload)
                match_lines = self._format_message_lines(
                    payload, include_congrats_for_match=True
                )
                if not lines:
                    if isinstance(notification_id, int):
                        discarded_ids.append(notification_id)
                    continue
                text_body = "\n".join(lines)
                match_text = "\n".join(match_lines or lines)
                matched_players = self._find_player_mentions(match_text)
                if not matched_players:
                    if isinstance(notification_id, int):
                        discarded_ids.append(notification_id)
                    continue
                lines = self._replace_egg_lines(lines)
                mention_line = self._format_player_mentions(matched_players)
                if mention_line:
                    lines.append(f"Ping: {mention_line}")
                lines.append(
                    f"Players: {', '.join(self._format_player_names(matched_players))}"
                )
                rarity = self._detect_drop_rarity(text_body)
                self._record_drop_stats(matched_players, rarity)
                updated_stats = True
                view = self._build_view(lines)
                try:
                    await channel.send(
                        view=view,
                        allowed_mentions=discord.AllowedMentions(
                            users=True, roles=False, everyone=False
                        ),
                    )
                    if isinstance(notification_id, int):
                        sent_ids.append(notification_id)
                except Exception:
                    logger.exception("Odeslání notifikace do Discordu selhalo.")
                await asyncio.sleep(0.3)
            if updated_stats:
                await self.refresh_dropstats_panels()
            success = True
        except Exception:
            logger.exception("Neočekávaná chyba v notifikační smyčce.")
        finally:
            if success and max_processed_id is not None:
                await asyncio.to_thread(
                    self._save_last_processed_notification_id, max_processed_id
                )
            processed_ids = sent_ids + discarded_ids
            if processed_ids:
                await asyncio.to_thread(delete_windows_notifications, processed_ids)

    @poll_notifications.before_loop
    async def before_poll_notifications(self):
        await self.bot.wait_until_ready()
        logger.info("Startuji smyčku pro přeposílání secret notifikací.")
        try:
            await self.refresh_dropstats_panels()
        except Exception:
            logger.exception("Nepodařilo se načíst dropstats panely při startu.")

    @tasks.loop(minutes=5)
    async def log_notification_stats(self) -> None:
        count = self._received_notifications_count
        self._received_notifications_count = 0
        logger.info("Za posledních 5 minut přijato notifikací: %s", count)

    @log_notification_stats.before_loop
    async def before_log_notification_stats(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=10)
    async def refresh_clan_member_cache(self):
        try:
            await self._refresh_clan_member_cache()
            await self.refresh_dropstats_panels()
        except Exception:
            logger.exception("Neočekávaná chyba při obnově cache hráčů.")

    @refresh_clan_member_cache.before_loop
    async def before_refresh_clan_member_cache(self):
        await self.bot.wait_until_ready()
        logger.info("Startuji smyčku pro obnovu cache hráčů v clanu.")
        await self._refresh_clan_member_cache()
        await self.refresh_dropstats_panels()

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
            notifications = await asyncio.to_thread(get_windows_notifications)
        except Exception:
            logger.exception("Načtení Windows notifikací z DB selhalo.")
            return None
        if not isinstance(notifications, list):
            logger.error("Windows notifikace mají neočekávaný formát.")
            return None
        filtered = self._filter_notifications_since_last(notifications)
        self._received_notifications_count += len(filtered)
        return filtered

    def _filter_notifications_since_last(
        self, notifications: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        last_id = self._last_processed_notification_id
        if last_id is None:
            return notifications
        filtered: List[Dict[str, Any]] = []
        for notification in notifications:
            notification_id = notification.get("id")
            if isinstance(notification_id, int):
                if notification_id > last_id:
                    filtered.append(notification)
            else:
                filtered.append(notification)
        return filtered

    def _format_message_lines(
        self,
        notification: Dict[str, Any],
        include_congrats_for_match: bool = False,
    ) -> Optional[List[str]]:
        try:
            panel_lines = self._extract_panel_text_from_notification(
                notification, include_congrats_for_match
            )
            if panel_lines is not None:
                return self._filter_notification_lines(
                    panel_lines, include_congrats_for_match
                )

            text_joined = notification.get("text_joined")
            text_line = (
                text_joined
                or notification.get("text")
                or self._extract_text_from_raw(notification)
            )

            text_lines = (text_line or "").splitlines() or [""]
            stripped_lines = [
                stripped
                for line in text_lines
                if not self._is_filtered_notification_header(
                    stripped := self._strip_app_prefix(line)
                )
            ]
            if include_congrats_for_match:
                return stripped_lines
            return [
                self._strip_congrats_prefix(str(line))
                if CONGRATS_LINE_REGEX.match(str(line))
                else line
                for line in stripped_lines
            ]
        except Exception:
            logger.exception("Chyba při formátování notifikace.")
            return None

    def _extract_panel_text_from_notification(
        self, payload: Dict[str, Any], include_congrats_for_match: bool = False
    ) -> Optional[List[str]]:
        if "notification" not in payload:
            return None
        notification = payload.get("notification")
        if not isinstance(notification, dict):
            return None
        lines: List[str] = []
        lines.extend(self._extract_notification_header_lines(notification))
        lines.extend(
            self._extract_notification_text_lines(
                notification.get("text") or notification.get("texts"),
                include_congrats_for_match,
            )
        )
        raw_payload = self._extract_notification_raw_payload(notification)
        if raw_payload:
            raw_notification = raw_payload.get("notification")
            for source in [raw_payload, raw_notification]:
                if not isinstance(source, dict):
                    continue
                lines.extend(self._extract_notification_header_lines(source))
                lines.extend(
                    self._extract_notification_text_lines(
                        source.get("text") or source.get("texts"),
                        include_congrats_for_match,
                    )
                )
        normalized_lines: List[str] = []
        seen: set[str] = set()
        for line in lines:
            normalized = self._normalize_panel_text(line)
            if normalized in seen:
                continue
            normalized_lines.append(normalized)
            seen.add(normalized)
        return normalized_lines or None

    def _extract_notification_header_lines(
        self, notification: Dict[str, Any]
    ) -> List[str]:
        header_keys = [
            "title",
            "attribution",
            "app_display_name",
            "display_name",
            "app_name",
            "app_user_model_id",
            "app_id",
        ]
        headers: List[str] = []
        for key in header_keys:
            value = notification.get(key)
            if value is None:
                continue
            if isinstance(value, list):
                for entry in value:
                    if entry is None:
                        continue
                    normalized = self._strip_app_prefix(str(entry))
                    if normalized and not self._is_filtered_notification_header(normalized):
                        headers.append(normalized)
            else:
                normalized = self._strip_app_prefix(str(value))
                if normalized and not self._is_filtered_notification_header(normalized):
                    headers.append(normalized)
        return headers

    def _is_filtered_notification_header(self, text: str) -> bool:
        normalized = self._strip_control_and_bidi(text)
        return bool(NOTIFICATION_HEADER_SKIP_REGEX.search(normalized))

    def _extract_notification_text_lines(
        self, text_value: Any, include_congrats_for_match: bool = False
    ) -> List[str]:
        if text_value is None:
            return []
        lines: List[str] = []
        congrats_pattern = re.compile(
            r"^🔥\s*Congrats!\s*:flag_[a-z]{2}:", re.IGNORECASE
        )
        if isinstance(text_value, list):
            for entry in text_value:
                if entry is None:
                    continue
                entry_text = str(entry)
                entry_lines = entry_text.splitlines() or [entry_text]
                for line in entry_lines:
                    if self._is_filtered_notification_header(str(line)):
                        continue
                    if (
                        not include_congrats_for_match
                        and congrats_pattern.match(str(line))
                    ):
                        lines.append(self._strip_congrats_prefix(str(line)))
                        continue
                    lines.append(line)
            return lines
        if isinstance(text_value, str):
            split_lines = text_value.splitlines() or [text_value]
            if include_congrats_for_match:
                return [
                    line
                    for line in split_lines
                    if not self._is_filtered_notification_header(str(line))
                ]
            return [
                self._strip_congrats_prefix(str(line))
                if congrats_pattern.match(str(line))
                else line
                for line in split_lines
                if not self._is_filtered_notification_header(str(line))
            ]
        text_line = str(text_value)
        if self._is_filtered_notification_header(text_line):
            return []
        if not include_congrats_for_match and congrats_pattern.match(text_line):
            return [self._strip_congrats_prefix(text_line)]
        return [text_line]

    def _filter_notification_lines(
        self, lines: List[str], include_congrats_for_match: bool = False
    ) -> List[str]:
        filtered: List[str] = []
        for line in lines:
            if line is None:
                filtered.append(line)
                continue
            text_line = str(line)
            if not include_congrats_for_match and CONGRATS_LINE_REGEX.match(text_line):
                filtered.append(self._strip_congrats_prefix(text_line))
                continue
            filtered.append(line)
        return filtered

    def _strip_congrats_prefix(self, text: str) -> str:
        if not text:
            return text
        if CONGRATS_LINE_REGEX.match(text):
            return CONGRATS_LINE_REGEX.sub("", text, count=1).lstrip()
        return text

    def _extract_notification_raw_payload(
        self, notification: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        raw_json = notification.get("raw_json")
        if raw_json:
            try:
                raw_payload = json.loads(raw_json)
            except Exception:
                logger.exception("JSON parse selhal u raw_json notifikace.")
                return None
            if isinstance(raw_payload, dict):
                return raw_payload
        raw_payload = notification.get("raw")
        if isinstance(raw_payload, dict):
            return raw_payload
        return None

    def _normalize_panel_text(self, value: Any) -> str:
        if value is None:
            return "\u200b"
        try:
            text = self._strip_app_prefix(str(value))
        except Exception:
            return "\u200b"
        text = self._strip_control_and_bidi(text)
        if text.strip() == "":
            return "\u200b"
        return text

    def _strip_app_prefix(self, text: str) -> str:
        if not text:
            return text
        stripped = text.lstrip()
        if stripped.startswith("[APP]"):
            without_prefix = stripped[len("[APP]") :].lstrip()
            return self._strip_control_and_bidi(without_prefix)
        return self._strip_control_and_bidi(text)

    def _strip_control_and_bidi(self, text: str) -> str:
        if not text:
            return text
        cleaned_chars = []
        for char in text:
            codepoint = ord(char)
            if unicodedata.category(char) == "Cf":
                continue
            if 0x202A <= codepoint <= 0x202E:
                continue
            if 0x2066 <= codepoint <= 0x2069:
                continue
            if codepoint in (0x200E, 0x200F):
                continue
            cleaned_chars.append(char)
        return "".join(cleaned_chars)

    def _normalize_name(self, text: str) -> str:
        return normalize_clan_member_name(text)

    def _extract_text_from_raw(self, notification: Dict[str, Any]) -> str:
        raw_json = notification.get("raw_json")
        if raw_json:
            try:
                raw_payload = json.loads(raw_json)
            except Exception:
                logger.exception("JSON parse selhal u raw_json notifikace.")
                return ""
            text_value = raw_payload.get("notification", {}).get("text")
        else:
            raw_payload = notification.get("raw", {})
            text_value = None
            if isinstance(raw_payload, dict):
                text_value = raw_payload.get("texts") or raw_payload.get("text")
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
            normalized_text = self._normalize_name(text_line)
            if not normalized_text:
                return []
            matched_ids = []
            seen_ids = set()
            for name, entry in self._clan_member_cache.items():
                if not (
                    entry.get("roblox_username") or entry.get("roblox_nick")
                ):
                    continue
                if name and self._has_exact_name_match(normalized_text, name):
                    member_id = entry.get("id")
                    if member_id not in seen_ids:
                        matched_ids.append(int(member_id))
                        seen_ids.add(member_id)
            return matched_ids
        except Exception:
            logger.exception("Chyba při vyhledání hráče v textu notifikace.")
            return []

    def _replace_egg_lines(self, lines: List[str]) -> List[str]:
        if not lines:
            return lines
        pattern = re.compile(
            r"🥚\s*\*\*Egg:\*\*\s*([^`]+?)\s*`+\(([^)]+)\s+opened\)`+"
        )
        updated: List[str] = []
        for line in lines:
            if not line:
                updated.append(line)
                continue
            text = str(line)
            match = pattern.search(text)
            if not match:
                updated.append(line)
                continue
            egg_name = match.group(1).strip()
            opened_value = match.group(2)
            prefix = text[: match.start()].strip()
            suffix = text[match.end() :].strip()
            if prefix:
                updated.append(prefix)
            updated.append(f"🥚Egg: {egg_name} - {opened_value} opened")
            if suffix:
                updated.append(suffix)
        return updated

    def _format_player_names(self, player_ids: List[int]) -> List[str]:
        return [self._get_display_name_for_id(player_id) for player_id in player_ids]

    def _format_player_mentions(self, player_ids: List[int]) -> str:
        return ", ".join(f"<@{player_id}>" for player_id in player_ids)

    def _get_display_name_for_id(self, player_id: int) -> str:
        for entry in self._clan_member_cache.values():
            if entry.get("id") == player_id:
                return str(entry.get("name") or player_id)
        return str(player_id)

    def _has_exact_name_match(self, text: str, name: str) -> bool:
        if not text or not name:
            return False
        escaped = re.escape(name)
        pattern = rf"(?:(?<=^)|(?<=[\s\W])){escaped}(?:(?=$)|(?=[\s\W]))"
        return re.search(pattern, text) is not None

    def _build_view(self, lines: List[str]) -> discord.ui.LayoutView:
        view = discord.ui.LayoutView()
        container = discord.ui.Container()
        container.add_item(
            discord.ui.TextDisplay(content="## 🔔 Secret drop notification")
        )
        container.add_item(discord.ui.Separator())

        body_lines: List[str] = []
        player_line: Optional[str] = None
        ping_line: Optional[str] = None
        for line in lines:
            if line is None:
                body_lines.append(line)
                continue
            normalized = self._strip_app_prefix(str(line))
            if normalized.startswith("Ping:"):
                ping_line = normalized
                continue
            if normalized.startswith("Players:") or normalized.startswith("Player:"):
                player_line = normalized
                continue
            body_lines.append(normalized)

        for line in self._normalize_lines(body_lines):
            highlighted = self._highlight_keywords(line)
            container.add_item(discord.ui.TextDisplay(content=highlighted))

        player_info = None
        if player_line:
            player_info = player_line.split(":", 1)[1].strip()
        ping_info = None
        if ping_line:
            ping_info = ping_line.split(":", 1)[1].strip()

        if player_info:
            container.add_item(
                discord.ui.TextDisplay(content=f"👥 Players: {player_info}")
            )
        if ping_info:
            container.add_item(
                discord.ui.TextDisplay(content=f"📣 Pings: {ping_info}")
            )
        view.add_item(container)
        return view

    def _highlight_keywords(self, text: str) -> str:
        if not text or text.strip() == "":
            return text
        keyword_styles = [
            ("secret", "31"),
            ("divine", "36"),
            ("supreme", "34"),
            ("golden", "33"),
            ("toxic", "32"),
            ("galaxy", "35"),
            ("shiny", "37"),
        ]
        highlighted = text
        matched = False
        for keyword, color_code in keyword_styles:
            pattern = rf"\b{re.escape(keyword)}\b"

            def replace(match: re.Match[str], code: str = color_code) -> str:
                return f"\x1b[1;{code}m{match.group(0)}\x1b[0m"

            new_text, count = re.subn(
                pattern, replace, highlighted, flags=re.IGNORECASE
            )
            if count:
                matched = True
            highlighted = new_text
        if not matched:
            return text
        return f"```ansi\n{highlighted}\n```"

    def _normalize_lines(self, lines: List[str]) -> List[str]:
        normalized: List[str] = []
        for line in lines:
            if line is None:
                normalized.append("\u200b")
                continue
            text = str(line)
            if text.strip() == "":
                normalized.append("\u200b")
                continue
            while text:
                chunk = text[:4000]
                if chunk.strip() == "":
                    chunk = "\u200b"
                normalized.append(chunk)
                text = text[4000:]
        return normalized

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
                        normalized = self._normalize_name(str(name))
                        if not normalized:
                            continue
                        if isinstance(entry, dict):
                            member_id = entry.get("id")
                            display_name = entry.get("name") or name
                            roblox_username = entry.get("roblox_username")
                            roblox_nick = entry.get("roblox_nick")
                            roblox_nick_updated_at = entry.get(
                                "roblox_nick_updated_at"
                            )
                            roblox_nick_checked_at = entry.get(
                                "roblox_nick_checked_at"
                            )
                            clan_key = entry.get("clan_key")
                            clan_display = entry.get("clan_display")
                        else:
                            member_id = entry
                            display_name = name
                            roblox_username = None
                            roblox_nick = None
                            roblox_nick_updated_at = None
                            roblox_nick_checked_at = None
                            clan_key = None
                            clan_display = None
                        if isinstance(member_id, (int, str)):
                            migrated_cache_entry = {
                                "id": int(member_id),
                                "name": str(display_name),
                            }
                            if roblox_username:
                                migrated_cache_entry["roblox_username"] = str(
                                    roblox_username
                                )
                            if roblox_nick:
                                migrated_cache_entry["roblox_nick"] = str(roblox_nick)
                            if roblox_nick_updated_at:
                                migrated_cache_entry["roblox_nick_updated_at"] = str(
                                    roblox_nick_updated_at
                                )
                            if roblox_nick_checked_at:
                                migrated_cache_entry["roblox_nick_checked_at"] = str(
                                    roblox_nick_checked_at
                                )
                            migrated_cache_entry["clan_key"] = (
                                str(clan_key) if clan_key else None
                            )
                            migrated_cache_entry["clan_display"] = (
                                str(clan_display) if clan_display else None
                            )
                            migrated_cache[normalized] = migrated_cache_entry
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

    def _load_last_processed_notification_id(self) -> None:
        conn = None
        try:
            conn = get_connection()
            cursor = conn.execute(
                "SELECT value FROM settings WHERE key = ?",
                (SETTINGS_KEY_LAST_NOTIFICATION_ID,),
            )
            row = cursor.fetchone()
            if row and row[0] is not None:
                self._last_processed_notification_id = int(row[0])
        except Exception:
            logger.exception("Načtení posledního ID notifikace z DB selhalo.")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    logger.exception("Uzavření DB spojení selhalo.")

    def _load_secret_role_ids(self) -> list[int]:
        role_ids = get_secret_notifications_role_ids()
        if role_ids:
            return self._normalize_secret_role_ids(role_ids)
        fallback = [role_id for role_id in CLAN_MEMBER_ROLE_IDS if role_id]
        return self._normalize_secret_role_ids(fallback)

    def _normalize_secret_role_ids(self, role_ids: list[int]) -> list[int]:
        normalized: list[int] = []
        seen = set()
        for role_id in role_ids:
            if not role_id:
                continue
            normalized_id = int(role_id)
            if normalized_id in seen:
                continue
            seen.add(normalized_id)
            normalized.append(normalized_id)
        return normalized

    async def _refresh_clan_member_cache(self) -> None:
        channel = await self._get_channel()
        if channel is None:
            return
        guild = getattr(channel, "guild", None)
        if guild is None:
            logger.warning("Nelze načíst guild z kanálu %s.", CHANNEL_ID)
            return
        role_to_clan: dict[int, dict[str, Optional[str]]] = {}
        try:
            clan_definitions = list_clan_definitions(guild.id)
        except Exception:
            logger.exception("Načtení definic clanů z DB selhalo.")
            clan_definitions = []
        for definition in clan_definitions:
            clan_key = definition.get("clan_key")
            clan_display = definition.get("display_name") or clan_key
            for key in (
                "accept_role_id",
                "accept_role_id_cz",
                "accept_role_id_en",
            ):
                role_id = definition.get(key)
                if not role_id:
                    continue
                role_to_clan.setdefault(
                    int(role_id),
                    {
                        "clan_key": str(clan_key) if clan_key else None,
                        "clan_display": str(clan_display)
                        if clan_display
                        else None,
                    },
                )
        for clan_key, role_id in CLAN_MEMBER_ROLE_IDS_BY_KEY.items():
            if not role_id:
                continue
            role_to_clan.setdefault(
                int(role_id),
                {
                    "clan_key": str(clan_key),
                    "clan_display": str(clan_key).upper(),
                },
            )
        existing_by_id: dict[int, dict[str, Any]] = {}
        for entry in self._clan_member_cache.values():
            member_id = entry.get("id")
            if isinstance(member_id, int) and member_id not in existing_by_id:
                existing_by_id[member_id] = entry
        new_entries_by_id: dict[int, dict[str, Any]] = {}
        for role_id in self._secret_role_ids:
            role = guild.get_role(role_id)
            if role is None:
                logger.warning("Role %s nebyla nalezena pro cache hráčů.", role_id)
                continue
            for member in role.members:
                candidate_username = str(member.display_name)
                existing_entry = existing_by_id.get(member.id)
                clan_key = None
                clan_display = None
                for member_role in member.roles:
                    clan_info = role_to_clan.get(member_role.id)
                    if clan_info:
                        clan_key = clan_info.get("clan_key")
                        clan_display = clan_info.get("clan_display")
                        break
                entry: dict[str, Any] = {
                    "id": member.id,
                    "name": str(member.display_name),
                    "clan_key": clan_key,
                    "clan_display": clan_display,
                }
                if existing_entry:
                    if existing_entry.get("roblox_username"):
                        entry["roblox_username"] = existing_entry.get(
                            "roblox_username"
                        )
                    if existing_entry.get("roblox_nick"):
                        entry["roblox_nick"] = existing_entry.get("roblox_nick")
                    if existing_entry.get("roblox_nick_updated_at"):
                        entry["roblox_nick_updated_at"] = existing_entry.get(
                            "roblox_nick_updated_at"
                        )
                    if existing_entry.get("roblox_nick_checked_at"):
                        entry["roblox_nick_checked_at"] = existing_entry.get(
                            "roblox_nick_checked_at"
                        )
                previous_username = entry.get("roblox_username")
                if previous_username and str(previous_username) != candidate_username:
                    entry.pop("roblox_nick", None)
                    entry.pop("roblox_nick_updated_at", None)
                entry["roblox_username"] = candidate_username
                new_entries_by_id[member.id] = entry

        await self._refresh_roblox_nicknames(new_entries_by_id)

        new_cache: dict[str, dict[str, Any]] = {}
        for member_id, entry in new_entries_by_id.items():
            if entry.get("roblox_username"):
                self._add_cache_key(new_cache, entry["roblox_username"], entry)
            if entry.get("roblox_nick"):
                self._add_cache_key(new_cache, entry["roblox_nick"], entry)

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

    def _save_last_processed_notification_id(self, notification_id: int) -> None:
        if notification_id is None:
            return
        if (
            self._last_processed_notification_id is not None
            and notification_id <= self._last_processed_notification_id
        ):
            return
        conn = None
        try:
            conn = get_connection()
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (SETTINGS_KEY_LAST_NOTIFICATION_ID, str(notification_id)),
                )
            self._last_processed_notification_id = notification_id
        except Exception:
            logger.exception("Uložení posledního ID notifikace do DB selhalo.")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    logger.exception("Uzavření DB spojení selhalo.")

    def _add_cache_key(
        self, cache: dict[str, dict[str, Any]], key: Any, entry: dict[str, Any]
    ) -> None:
        if key is None:
            return
        normalized = self._normalize_name(str(key))
        if not normalized:
            return
        if normalized not in cache:
            cache[normalized] = entry

    def _replace_cache_nick_key(
        self,
        old_nick: Optional[str],
        new_nick: Optional[str],
        entry: dict[str, Any],
    ) -> None:
        if old_nick:
            old_key = self._normalize_name(str(old_nick))
            if (
                old_key
                and old_key in self._clan_member_cache
                and self._clan_member_cache.get(old_key) is entry
            ):
                del self._clan_member_cache[old_key]
        if new_nick:
            self._add_cache_key(self._clan_member_cache, new_nick, entry)

    def _find_member_entry_by_id(
        self, member_id: int
    ) -> Optional[dict[str, Any]]:
        for entry in self._clan_member_cache.values():
            if entry.get("id") == member_id:
                return entry
        return None

    def _find_member_entry_by_roblox_username(
        self, username: str
    ) -> Optional[dict[str, Any]]:
        if not username:
            return None
        normalized = str(username).lower()
        for entry in self._clan_member_cache.values():
            roblox_username = entry.get("roblox_username")
            if roblox_username and str(roblox_username).lower() == normalized:
                return entry
        return None

    def _parse_datetime_value(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    def _extract_roblox_username(self, names: set[str]) -> Optional[str]:
        for name in names:
            if not name:
                continue
            match = ROBLOX_USERNAME_REGEX.fullmatch(str(name).strip())
            if match:
                return match.group(0)
        return None

    async def _refresh_roblox_nicknames(
        self, entries_by_id: dict[int, dict[str, Any]]
    ) -> None:
        usernames: list[str] = []
        entries_to_refresh: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        for entry in entries_by_id.values():
            roblox_username = entry.get("roblox_username")
            if not roblox_username:
                continue
            last_checked = self._parse_datetime_value(
                entry.get("roblox_nick_checked_at")
            )
            if last_checked and now - last_checked < timedelta(
                minutes=ROBLOX_NICK_REFRESH_MINUTES
            ):
                continue
            usernames.append(str(roblox_username))
            entries_to_refresh.append(entry)
        if not usernames:
            return
        username_map = await self._fetch_roblox_display_names(usernames)
        now_iso = datetime.now(timezone.utc).isoformat()
        updated_cache = False
        for entry in entries_to_refresh:
            roblox_username = entry.get("roblox_username")
            if not roblox_username:
                continue
            display_name = username_map.get(str(roblox_username))
            if display_name:
                previous_nick = entry.get("roblox_nick")
                entry["roblox_nick"] = display_name
                entry["roblox_nick_updated_at"] = now_iso
                if previous_nick != display_name:
                    self._replace_cache_nick_key(
                        previous_nick, display_name, entry
                    )
                updated_cache = True
            entry["roblox_nick_checked_at"] = now_iso
        if updated_cache:
            self._clan_member_cache_updated_at = datetime.now(timezone.utc)
            self._save_clan_member_cache()

    async def _fetch_roblox_display_names(
        self, usernames: List[str]
    ) -> dict[str, str]:
        results: dict[str, str] = {}
        if not usernames:
            return results
        async with aiohttp.ClientSession() as session:
            for start in range(0, len(usernames), ROBLOX_USERNAME_BATCH_SIZE):
                chunk = usernames[start : start + ROBLOX_USERNAME_BATCH_SIZE]
                payload = {
                    "usernames": chunk,
                    "excludeBannedUsers": True,
                }
                try:
                    async with session.post(
                        ROBLOX_USERNAMES_URL, json=payload, timeout=15
                    ) as response:
                        if response.status == 429:
                            logger.warning(
                                "Roblox API rate limit hit while fetching usernames."
                            )
                            break
                        if response.status >= 400:
                            logger.warning(
                                "Roblox API returned status %s for usernames lookup.",
                                response.status,
                            )
                            continue
                        data = await response.json()
                except Exception:
                    logger.exception("Roblox API request failed.")
                    continue
                for item in data.get("data", []):
                    requested = item.get("requestedUsername") or item.get("name")
                    display = item.get("displayName") or item.get("name")
                    if requested and display:
                        results[str(requested)] = str(display)
                await asyncio.sleep(ROBLOX_USERNAME_REQUEST_DELAY_SECONDS)
        return results

    def _record_drop_stats(self, player_ids: List[int], rarity: Optional[str]) -> None:
        if not player_ids:
            return
        rarity_value = rarity or "unknown"
        now = datetime.now(timezone.utc)
        date_value = now.date().isoformat()
        for player_id in player_ids:
            try:
                increment_secret_drop_stat(date_value, int(player_id), 1)
                add_secret_drop_event(now, int(player_id), rarity_value)
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
            f"Dropstats panel byl odeslán do kanálu #{channel.name}.", ephemeral=True
        )

    @app_commands.checks.has_permissions(manage_channels=True)
    async def dropstats_reset(self, interaction: discord.Interaction):
        try:
            reset_secret_drop_stats()
            await self.refresh_dropstats_panels()
            view = self._build_notice_view(
                "✅ Dropstats leaderboard byl resetován."
            )
        except Exception:
            logger.exception("Reset dropstats leaderboardu selhal.")
            view = self._build_notice_view(
                "⚠️ Reset dropstats leaderboardu se nepodařil."
            )
        await interaction.response.send_message(view=view, ephemeral=True)

    async def secret_cache(self, interaction: discord.Interaction):
        view = self._build_cached_names_view()
        await interaction.response.send_message(
            view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @app_commands.describe(
        username="Roblox username pro refresh přezdívky.",
        member="Discord člen, kterému se má refreshnout Roblox přezdívka.",
    )
    async def secret_cache_refresh(
        self,
        interaction: discord.Interaction,
        username: Optional[str] = None,
        member: Optional[discord.Member] = None,
    ):
        if not username and not member:
            view = self._build_notice_view(
                "⚠️ Zadej Roblox username nebo vyber Discord člena."
            )
            await interaction.response.send_message(view=view, ephemeral=True)
            return

        entry = None
        if member is not None:
            entry = self._find_member_entry_by_id(member.id)
        if entry is None and username:
            entry = self._find_member_entry_by_roblox_username(username)
        if entry is None:
            view = self._build_notice_view("⚠️ Člen nebyl v cache nalezen.")
            await interaction.response.send_message(view=view, ephemeral=True)
            return

        roblox_username = entry.get("roblox_username")
        if not roblox_username:
            view = self._build_notice_view(
                "⚠️ Tento člen nemá uložený Roblox username."
            )
            await interaction.response.send_message(view=view, ephemeral=True)
            return

        previous_nick = entry.get("roblox_nick")
        username_map = await self._fetch_roblox_display_names(
            [str(roblox_username)]
        )
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        display_name = username_map.get(str(roblox_username))
        entry["roblox_nick_checked_at"] = now_iso
        if display_name:
            entry["roblox_nick"] = display_name
            entry["roblox_nick_updated_at"] = now_iso
            if display_name != previous_nick:
                self._replace_cache_nick_key(previous_nick, display_name, entry)
        self._clan_member_cache_updated_at = now
        self._save_clan_member_cache()

        view = self._build_secret_refresh_view(
            roblox_username=str(roblox_username),
            previous_nick=previous_nick,
            new_nick=entry.get("roblox_nick"),
            refreshed_at=now,
        )
        await interaction.response.send_message(view=view, ephemeral=True)

    async def secret_roles_add(
        self, interaction: discord.Interaction, role: discord.Role
    ):
        if role.id in self._secret_role_ids:
            view = self._build_notice_view(
                f"ℹ️ Role {role.mention} už je v seznamu."
            )
            await interaction.response.send_message(view=view, ephemeral=True)
            return
        self._secret_role_ids.append(role.id)
        self._secret_role_ids = self._normalize_secret_role_ids(
            self._secret_role_ids
        )
        set_secret_notifications_role_ids(self._secret_role_ids)
        view = self._build_notice_view(
            f"✅ Role {role.mention} byla přidána do seznamu."
        )
        await interaction.response.send_message(view=view, ephemeral=True)

    async def secret_roles_remove(
        self, interaction: discord.Interaction, role: discord.Role
    ):
        if role.id not in self._secret_role_ids:
            view = self._build_notice_view(
                f"ℹ️ Role {role.mention} nebyla v seznamu."
            )
            await interaction.response.send_message(view=view, ephemeral=True)
            return
        self._secret_role_ids = [
            role_id for role_id in self._secret_role_ids if role_id != role.id
        ]
        set_secret_notifications_role_ids(self._secret_role_ids)
        view = self._build_notice_view(
            f"✅ Role {role.mention} byla odebrána ze seznamu."
        )
        await interaction.response.send_message(view=view, ephemeral=True)

    def _build_notice_view(self, message: str) -> discord.ui.LayoutView:
        view = discord.ui.LayoutView()
        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay(content=message))
        view.add_item(container)
        return view

    def _build_secret_refresh_view(
        self,
        roblox_username: str,
        previous_nick: Optional[str],
        new_nick: Optional[str],
        refreshed_at: datetime,
    ) -> discord.ui.LayoutView:
        view = discord.ui.LayoutView()
        container = discord.ui.Container()
        container.add_item(
            discord.ui.TextDisplay(content="🔄 **Roblox přezdívka refreshnuta**")
        )
        container.add_item(discord.ui.Separator())
        before = previous_nick or "—"
        after = new_nick or "—"
        container.add_item(
            discord.ui.TextDisplay(content=f"🆔 Roblox username: `{roblox_username}`")
        )
        container.add_item(
            discord.ui.TextDisplay(content=f"🏷️ Přezdívka: **{before} → {after}**")
        )
        refreshed_ts = int(refreshed_at.timestamp())
        container.add_item(
            discord.ui.TextDisplay(
                content=f"🕒 Refresh: <t:{refreshed_ts}:F>"
            )
        )
        view.add_item(container)
        return view

    def _build_dropstats_view(self) -> discord.ui.LayoutView:
        view = discord.ui.LayoutView()
        container = discord.ui.Container()
        container.add_item(
            discord.ui.TextDisplay(content="## 🏆 Dropstats leaderboard")
        )
        container.add_item(
            discord.ui.TextDisplay(
                content=(
                    "Přehled dropů pro všechny členy clanů. "
                    "Počty se aktualizují automaticky a ukládají se pro restart bota."
                )
            )
        )
        container.add_item(discord.ui.Separator())

        members = self._get_clan_member_entries()
        if not members:
            container.add_item(
                discord.ui.TextDisplay(
                    content="⚠️ Žádní členové clanů nebyli nalezeni."
                )
            )
            view.add_item(container)
            return view

        breakdown = self._get_drop_breakdown_safe()
        totals = {
            user_id: sum(counts.values()) for user_id, counts in breakdown.items()
        }
        total_drops = sum(totals.get(user_id, 0) for user_id in members)
        total_supreme = sum(
            breakdown.get(user_id, {}).get("supreme", 0) for user_id in members
        )
        total_divine = sum(
            breakdown.get(user_id, {}).get("divine", 0) for user_id in members
        )
        total_secret = sum(
            breakdown.get(user_id, {}).get("secret", 0) for user_id in members
        )
        total_unknown = sum(
            breakdown.get(user_id, {}).get("unknown", 0) for user_id in members
        )
        container.add_item(discord.ui.TextDisplay(content="### 📊 Souhrn"))
        container.add_item(
            discord.ui.TextDisplay(
                content=(
                    f"👥 **Počet členů:** `{len(members)}`  •  "
                    f"🎁 **Celkem dropů:** `{total_drops}`"
                )
            )
        )
        container.add_item(
            discord.ui.TextDisplay(
                content=(
                    "🧮 **Celkový souhrn:** "
                    f"Su `{total_supreme}`  •  "
                    f"Divine `{total_divine}`  •  "
                    f"Secret `{total_secret}`  •  "
                    f"Unknown `{total_unknown}`"
                )
            )
        )
        container.add_item(discord.ui.Separator())
        clan_groups: dict[str, dict[str, Any]] = {}
        clan_sort_index: dict[str, int] | None = None
        clan_display_override: dict[str, str] = {}
        channel = self.bot.get_channel(CHANNEL_ID)
        guild = getattr(channel, "guild", None)
        if guild is not None:
            try:
                clan_definitions = list_clan_definitions(guild.id)
            except Exception:
                logger.exception("Načtení definic clanů pro dropstats selhalo.")
                clan_definitions = []
            if clan_definitions:
                clan_sort_index = {}
                for index, definition in enumerate(clan_definitions):
                    clan_key = definition.get("clan_key")
                    if not clan_key:
                        continue
                    clan_key_str = str(clan_key)
                    clan_sort_index[clan_key_str] = index
                    clan_display = definition.get("display_name") or clan_key
                    if clan_display:
                        clan_display_override[clan_key_str] = str(clan_display)
        for user_id, entry in members.items():
            clan_key = entry.get("clan_key")
            clan_key_str = str(clan_key) if clan_key else None
            clan_display = (
                clan_display_override.get(clan_key_str)
                if clan_key_str
                else None
            )
            if not clan_display:
                clan_display = entry.get("clan_display") or (
                    str(clan_key).upper() if clan_key else "Nezařazeno"
                )
            group_key = str(clan_key) if clan_key else "unassigned"
            group = clan_groups.setdefault(
                group_key,
                {
                    "display": clan_display,
                    "members": [],
                },
            )
            group["members"].append((user_id, entry))

        if clan_sort_index is not None:
            fallback_index = len(clan_sort_index)

            def clan_sort_key(
                item: tuple[str, dict[str, Any]]
            ) -> tuple[int, int, str]:
                group_key, group = item
                display = str(group.get("display") or "").lower()
                if group_key == "unassigned":
                    return (1, fallback_index + 1, display)
                return (
                    0,
                    clan_sort_index.get(group_key, fallback_index),
                    display,
                )

        else:

            def clan_sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, str]:
                display = str(item[1].get("display") or "").lower()
                return (1 if display == "nezařazeno" else 0, display)

        for _, clan_group in sorted(clan_groups.items(), key=clan_sort_key):
            clan_members = clan_group["members"]
            clan_members_sorted = sorted(
                clan_members,
                key=lambda item: (
                    -totals.get(item[0], 0),
                    item[1].get("name", "").lower(),
                ),
            )
            clan_total_drops = sum(
                totals.get(member_id, 0) for member_id, _ in clan_members
            )
            container.add_item(
                discord.ui.TextDisplay(
                    content=f"### 🛡️ {clan_group['display']}"
                )
            )
            container.add_item(
                discord.ui.TextDisplay(
                    content=(
                        f"👥 **Členů:** `{len(clan_members)}`  •  "
                        f"🎁 **Dropů:** `{clan_total_drops}`"
                    )
                )
            )
            medal_emojis = ["🥇", "🥈", "🥉"]
            lines = []
            for idx, (user_id, entry) in enumerate(
                clan_members_sorted, start=1
            ):
                prefix = medal_emojis[idx - 1] if idx <= 3 else f"`#{idx}`"
                counts = breakdown.get(user_id, {})
                supreme = counts.get("supreme", 0)
                divine = counts.get("divine", 0)
                secret = counts.get("secret", 0)
                unknown = counts.get("unknown", 0)
                lines.append(
                    (
                        f"{prefix} **{entry.get('name', user_id)}** — "
                        f"**{totals.get(user_id, 0)}**"
                        f"  •  `Su` {supreme}  •  `D` {divine}  •  `Se` {secret}"
                        f"  •  `Unk` {unknown}"
                    )
                )
            for chunk in self._chunk_lines(lines, max_len=1800):
                container.add_item(discord.ui.TextDisplay(content=chunk))
            container.add_item(discord.ui.Separator())

        updated_at = int(datetime.now(timezone.utc).timestamp())
        container.add_item(
            discord.ui.TextDisplay(content=f"🕒 Aktualizováno: <t:{updated_at}:R>")
        )
        view.add_item(container)
        return view

    def _build_cached_names_view(self) -> discord.ui.LayoutView:
        view = discord.ui.LayoutView()
        container = discord.ui.Container()
        container.add_item(
            discord.ui.TextDisplay(content="🗂️ **Cache hráčů pro notifikace**")
        )
        updated_at = self._clan_member_cache_updated_at
        if updated_at:
            updated_ts = int(updated_at.timestamp())
            updated_line = f"🕒 Aktualizováno: <t:{updated_ts}:R>"
        else:
            updated_line = "🕒 Aktualizováno: neznámé"
        entries_by_id: dict[int, dict[str, Any]] = {}
        for entry in self._clan_member_cache.values():
            member_id = entry.get("id")
            if isinstance(member_id, int) and member_id not in entries_by_id:
                entries_by_id[member_id] = entry
        lines: List[str] = []
        for entry in entries_by_id.values():
            roblox_username = entry.get("roblox_username") or "neznámé"
            roblox_nick = entry.get("roblox_nick") or "neznámé"
            nick_timestamp_value = entry.get(
                "roblox_nick_updated_at"
            ) or entry.get("roblox_nick_checked_at")
            nick_timestamp = self._parse_datetime_value(nick_timestamp_value)
            if nick_timestamp and nick_timestamp.tzinfo is None:
                nick_timestamp = nick_timestamp.replace(tzinfo=timezone.utc)
            if nick_timestamp:
                nick_ts = int(nick_timestamp.timestamp())
                time_label = f"<t:{nick_ts}:R>"
            else:
                time_label = "neznámé"
            line = f"{roblox_username} : {roblox_nick} : {time_label}"
            lines.append(line)
        unique_names = sorted(lines, key=str.lower)
        container.add_item(
            discord.ui.TextDisplay(
                content=f"👥 **Počet uložených členů:** `{len(unique_names)}`"
            )
        )
        container.add_item(discord.ui.TextDisplay(content=updated_line))
        container.add_item(discord.ui.Separator())
        if not unique_names:
            container.add_item(
                discord.ui.TextDisplay(
                    content="⚠️ Cache neobsahuje žádná jména."
                )
            )
            view.add_item(container)
            return view
        for chunk in self._chunk_lines(unique_names):
            container.add_item(discord.ui.TextDisplay(content=chunk))
        view.add_item(container)
        return view

    def _get_drop_breakdown_safe(self) -> dict[int, dict[str, int]]:
        try:
            return get_secret_drop_breakdown_all_time()
        except Exception:
            logger.exception("Načtení statistiky dropu selhalo.")
            return {}

    def _get_clan_member_entries(self) -> dict[int, dict[str, Optional[str]]]:
        members: dict[int, dict[str, Optional[str]]] = {}
        for entry in self._clan_member_cache.values():
            member_id = entry.get("id")
            name = entry.get("name") or str(member_id)
            if isinstance(member_id, int):
                members.setdefault(
                    member_id,
                    {
                        "name": str(name),
                        "clan_key": entry.get("clan_key"),
                        "clan_display": entry.get("clan_display"),
                    },
                )
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

    def _detect_drop_rarity(self, text_line: str) -> Optional[str]:
        if not text_line:
            return None
        lowered = text_line.lower()
        for rarity in ("secret", "divine", "supreme"):
            if re.search(rf"\b{re.escape(rarity)}\b", lowered):
                return rarity
        return None

    async def refresh_dropstats_panels(self) -> None:
        try:
            panels = get_all_dropstats_panels()
        except Exception:
            logger.exception("Načtení dropstats panelů selhalo.")
            return
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
