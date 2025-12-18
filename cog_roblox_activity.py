import asyncio
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

import aiohttp
import discord
from PIL import Image, ImageDraw, ImageFont
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    CLAN_MEMBER_ROLE_EN_ID,
    CLAN_MEMBER_ROLE_ID,
    REBIRTH_CHAMPIONS_UNIVERSE_ID,
    ROBLOX_ACTIVITY_CHANNEL_ID,
)
from db import get_connection, get_setting, set_setting


ROBLOX_USERNAMES_URL = "https://users.roblox.com/v1/usernames/users"
ROBLOX_PRESENCE_URL = "https://presence.roblox.com/v1/presence/users"
ROBLOX_AUTH_USER_URL = "https://users.roblox.com/v1/users/authenticated"
ROBLOX_FRIEND_STATUS_URL = (
    "https://friends.roblox.com/v1/users/{user_id}/friends/statuses"
)
ROBLOX_MY_FRIEND_STATUS_URL = "https://friends.roblox.com/v1/my/friends/statuses"
# Some Roblox-related usernames in our community exceed the usual 20-character
# limit (e.g., "roblox_user_1463871864" has 22 characters). Allow a slightly
# larger range so we can still pick them up from member nicknames.
ROBLOX_USERNAME_REGEX = re.compile(r"[A-Za-z0-9_]{3,26}")


class RobloxActivityCog(commands.Cog, name="RobloxActivity"):
    _COOKIE_SETTING_KEY = "roblox_presence_cookie"
    _AUTHORIZED_COOKIE_USER_ID = 369810917673795586
    _AUTHENTICATED_USER_OVERRIDE_ID = 4_470_228_128

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._logger = logging.getLogger(__name__)
        self._session: Optional[aiohttp.ClientSession] = None
        self._roblox_cookie: Optional[str] = None
        self._presence_state: Dict[
            int, Dict[str, Optional[datetime | bool]]
        ] = {}
        self._duration_totals: Dict[int, Dict[str, float]] = defaultdict(
            lambda: {"online": 0.0, "offline": 0.0}
        )
        self._user_labels: Dict[int, str] = {}
        self._tracking_enabled: bool = True
        self._session_started_at: datetime = datetime.now(timezone.utc)
        self._session_ended_at: Optional[datetime] = None
        self._last_channel_report: Optional[datetime] = None
        self._authenticated_user_id: Optional[int] = None
        self._skip_connection_checks: bool = False

    async def cog_load(self):
        self._session = aiohttp.ClientSession()
        self._load_cookie_from_db()
        self._load_state_from_db()
        self.presence_notifier.start()

    async def cog_unload(self):
        if self._tracking_enabled:
            self._finalize_totals(datetime.now(timezone.utc))
        else:
            self._persist_all_state()

        if self._session and not self._session.closed:
            await self._session.close()
        self.presence_notifier.cancel()

    def _serialize_datetime(self, dt: Optional[datetime]) -> Optional[str]:
        if not dt:
            return None
        return dt.astimezone(timezone.utc).isoformat()

    def _parse_datetime(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            self._logger.warning("Failed to parse datetime value: %s", value)
            return None

    def _ensure_tracking_table_columns(self, cursor) -> None:
        cursor.execute("PRAGMA table_info(roblox_tracking_state)")
        columns = {row[1] for row in cursor.fetchall()}
        if "last_channel_report_at" in columns:
            return

        try:
            cursor.execute(
                "ALTER TABLE roblox_tracking_state ADD COLUMN last_channel_report_at TEXT"
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "Could not add last_channel_report_at column: %s", exc
            )

    def _load_cookie_from_db(self) -> None:
        value = get_setting(self._COOKIE_SETTING_KEY)
        self._roblox_cookie = value.strip() if value else None

    def _persist_cookie_to_db(self) -> None:
        if self._roblox_cookie:
            set_setting(self._COOKIE_SETTING_KEY, self._roblox_cookie)
        else:
            set_setting(self._COOKIE_SETTING_KEY, "")

    def _status_to_int(self, status: Optional[bool]) -> Optional[int]:
        if status is True:
            return 1
        if status is False:
            return 0
        return None

    def _int_to_status(self, value: Optional[int]) -> Optional[bool]:
        if value is None:
            return None
        return bool(int(value))

    def _load_state_from_db(self) -> None:
        conn = get_connection()
        cursor = conn.cursor()

        self._ensure_tracking_table_columns(cursor)

        cursor.execute(
            "SELECT tracking_enabled, session_started_at, session_ended_at, last_channel_report_at FROM roblox_tracking_state WHERE id = 1"
        )
        row = cursor.fetchone()
        if row:
            tracking_enabled, started_at, ended_at, last_report_at = row
            try:
                self._tracking_enabled = bool(int(tracking_enabled))
            except (TypeError, ValueError):
                self._tracking_enabled = True
            self._session_started_at = (
                self._parse_datetime(started_at) or datetime.now(timezone.utc)
            )
            self._session_ended_at = self._parse_datetime(ended_at)
            self._last_channel_report = self._parse_datetime(last_report_at)
        else:
            now = datetime.now(timezone.utc)
            self._tracking_enabled = True
            self._session_started_at = now
            self._session_ended_at = None
            self._last_channel_report = None
            cursor.execute(
                "INSERT INTO roblox_tracking_state (id, tracking_enabled, session_started_at, session_ended_at, last_channel_report_at) VALUES (1, ?, ?, ?, ?)",
                (1, self._serialize_datetime(now), None, None),
            )

        cursor.execute(
            "SELECT user_id, online_seconds, offline_seconds, label FROM roblox_duration_totals"
        )
        for user_id, online_seconds, offline_seconds, label in cursor.fetchall():
            self._duration_totals[int(user_id)] = {
                "online": float(online_seconds or 0),
                "offline": float(offline_seconds or 0),
            }
            if label:
                self._user_labels[int(user_id)] = label

        cursor.execute(
            "SELECT user_id, status, last_change, last_update FROM roblox_presence_state"
        )
        for user_id, status, last_change, last_update in cursor.fetchall():
            self._presence_state[int(user_id)] = {
                "status": self._int_to_status(status),
                "last_change": self._parse_datetime(last_change),
                "last_update": self._parse_datetime(last_update),
            }

        if self._tracking_enabled and self._presence_state:
            now = datetime.now(timezone.utc)
            for user_id, state in list(self._presence_state.items()):
                status = state.get("status")
                last_update = state.get("last_update")
                if status is None or last_update is None:
                    continue

                elapsed = (now - last_update).total_seconds()
                if elapsed <= 0:
                    continue

                if status is True:
                    self._duration_totals[user_id]["online"] += elapsed
                elif status is False:
                    self._duration_totals[user_id]["offline"] += elapsed

                state["last_update"] = now

            self._persist_all_state()

        conn.commit()
        conn.close()

    def _persist_tracking_state(self, conn=None) -> None:
        should_close = False
        if conn is None:
            conn = get_connection()
            should_close = True

        conn.execute(
            """
            INSERT INTO roblox_tracking_state (id, tracking_enabled, session_started_at, session_ended_at, last_channel_report_at)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                tracking_enabled = excluded.tracking_enabled,
                session_started_at = excluded.session_started_at,
                session_ended_at = excluded.session_ended_at,
                last_channel_report_at = excluded.last_channel_report_at
            """,
            (
                1 if self._tracking_enabled else 0,
                self._serialize_datetime(self._session_started_at),
                self._serialize_datetime(self._session_ended_at),
                self._serialize_datetime(self._last_channel_report),
            ),
        )

        if should_close:
            conn.commit()
            conn.close()

    def _persist_user_state(self, user_id: int, conn=None) -> None:
        should_close = False
        if conn is None:
            conn = get_connection()
            should_close = True

        state = self._presence_state.get(user_id, {})
        totals = self._duration_totals.get(user_id, {"online": 0.0, "offline": 0.0})
        label = self._user_labels.get(user_id)

        conn.execute(
            """
            INSERT INTO roblox_presence_state (user_id, status, last_change, last_update)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                status = excluded.status,
                last_change = excluded.last_change,
                last_update = excluded.last_update
            """,
            (
                user_id,
                self._status_to_int(state.get("status")),
                self._serialize_datetime(state.get("last_change")),
                self._serialize_datetime(state.get("last_update")),
            ),
        )

        conn.execute(
            """
            INSERT INTO roblox_duration_totals (user_id, online_seconds, offline_seconds, label)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                online_seconds = excluded.online_seconds,
                offline_seconds = excluded.offline_seconds,
                label = excluded.label
            """,
            (user_id, totals["online"], totals["offline"], label),
        )

        if should_close:
            conn.commit()
            conn.close()

    def _persist_all_state(self) -> None:
        conn = get_connection()
        try:
            with conn:
                self._persist_tracking_state(conn)
                for user_id in set(
                    list(self._presence_state.keys()) + list(self._duration_totals.keys())
                ):
                    self._persist_user_state(user_id, conn)
        finally:
            conn.close()

    def _clear_persistence(self) -> None:
        conn = get_connection()
        try:
            with conn:
                conn.execute("DELETE FROM roblox_presence_state")
                conn.execute("DELETE FROM roblox_duration_totals")
                conn.execute("DELETE FROM roblox_tracking_state WHERE id = 1")
        finally:
            conn.close()

    def _find_roblox_username(self, member: discord.Member) -> Optional[str]:
        nickname = member.nick or member.global_name or member.name
        matches = ROBLOX_USERNAME_REGEX.findall(nickname)
        if not matches:
            return None
        return matches[0]

    async def _fetch_user_ids(self, usernames: List[str]) -> tuple[Dict[str, int], Set[str]]:
        if not self._session:
            raise RuntimeError("RobloxActivityCog session is not initialized")

        resolved: Dict[str, int] = {}
        missing: Set[str] = set()

        for i in range(0, len(usernames), 100):
            batch = usernames[i : i + 100]
            payload = {"usernames": batch, "excludeBannedUsers": True}
            try:
                async with self._session.post(
                    ROBLOX_USERNAMES_URL, json=payload, timeout=20
                ) as resp:
                    if resp.status != 200:
                        self._logger.warning(
                            "Roblox usernames API returned %s", resp.status
                        )
                        missing.update(batch)
                        continue
                    data = await resp.json()
            except aiohttp.ClientError as exc:
                self._logger.warning("Roblox usernames API error: %s", exc)
                missing.update(batch)
                continue

            found_names = set()
            for entry in data.get("data", []):
                username = entry.get("requestedUsername")
                user_id = entry.get("id")
                if not username or user_id is None:
                    continue
                resolved[username.lower()] = int(user_id)
                found_names.add(username.lower())

            for name in batch:
                if name.lower() not in found_names:
                    missing.add(name)

        return resolved, missing

    async def _fetch_presence(self, user_ids: Iterable[int]) -> Dict[int, Optional[bool]]:
        if not self._session:
            raise RuntimeError("RobloxActivityCog session is not initialized")

        result: Dict[int, Optional[bool]] = {}
        ids = list(user_ids)
        if not self._roblox_cookie:
            self._load_cookie_from_db()
        if not self._roblox_cookie:
            self._logger.warning(
                "Missing Roblox authentication cookie – cannot retrieve presence status."
            )
            for user_id in ids:
                result[user_id] = None
            return result
        for i in range(0, len(ids), 100):
            batch = ids[i : i + 100]
            try:
                async with self._session.post(
                    ROBLOX_PRESENCE_URL,
                    json={"userIds": batch},
                    timeout=20,
                    headers={"Cookie": f".ROBLOSECURITY={self._roblox_cookie}"},
                ) as resp:
                    if resp.status != 200:
                        self._logger.warning(
                            "Roblox presence API returned %s", resp.status
                        )
                        for user_id in batch:
                            result[user_id] = None
                        continue
                    data = await resp.json()
            except aiohttp.ClientError as exc:
                self._logger.warning("Roblox presence API error: %s", exc)
                for user_id in batch:
                    result[user_id] = None
                continue

            for entry in data.get("userPresences", []):
                user_id = entry.get("userId")
                if user_id is None:
                    continue

                # Presence types: 0=offline, 1=online, 2=in-game, 3=in-studio.
                presence_type = entry.get("userPresenceType")
                place_id = entry.get("placeId")
                if presence_type is None:
                    result[int(user_id)] = None
                    continue

                result[int(user_id)] = (
                    presence_type != 0
                    and place_id == REBIRTH_CHAMPIONS_UNIVERSE_ID
                )

        return result

    async def _fetch_authenticated_user_id(self) -> Optional[int]:
        if self._authenticated_user_id is not None:
            return self._authenticated_user_id

        if self._AUTHENTICATED_USER_OVERRIDE_ID:
            self._authenticated_user_id = self._AUTHENTICATED_USER_OVERRIDE_ID
            return self._authenticated_user_id

        if not self._roblox_cookie:
            self._load_cookie_from_db()

        if not self._roblox_cookie:
            return None

        if not self._session:
            raise RuntimeError("RobloxActivityCog session is not initialized")

        try:
            async with self._session.get(
                ROBLOX_AUTH_USER_URL,
                headers={"Cookie": f".ROBLOSECURITY={self._roblox_cookie}"},
                timeout=20,
            ) as resp:
                if resp.status != 200:
                    self._logger.warning(
                        "Roblox authenticated user API returned %s", resp.status
                    )
                    return None
                data = await resp.json()
        except aiohttp.ClientError as exc:
            self._logger.warning("Roblox authenticated user API error: %s", exc)
            return None

        user_id = data.get("id")
        if not isinstance(user_id, int):
            self._logger.warning("Unexpected authenticated user payload: %s", data)
            return None

        self._authenticated_user_id = user_id
        return user_id

    async def _fetch_connection_statuses(
        self, user_ids: Iterable[int]
    ) -> Dict[int, Optional[bool]]:
        result: Dict[int, Optional[bool]] = {}
        ids = [int(uid) for uid in set(user_ids)]
        if not ids:
            return result

        if self._skip_connection_checks:
            for uid in ids:
                result[uid] = None
            return result

        if not self._session:
            raise RuntimeError("RobloxActivityCog session is not initialized")

        if not self._roblox_cookie:
            self._load_cookie_from_db()

        if not self._roblox_cookie:
            self._logger.warning(
                "Missing Roblox authentication cookie – cannot retrieve friends status."
            )
            for uid in ids:
                result[uid] = None
            return result

        headers = {"Cookie": f".ROBLOSECURITY={self._roblox_cookie}"}

        def _interpret_friend_status(value) -> Optional[bool]:
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"friend", "friends", "isfriend", "isfriends"}:
                    return True
                if normalized in {"notfriend", "none", "unknown"}:
                    return False
                if "request" in normalized or "pending" in normalized:
                    return False
                return None

            try:
                numeric = int(value)
            except (TypeError, ValueError):
                return None

            if numeric in {3, 4}:
                return True
            if numeric in {1, 2}:  # outgoing or incoming request – treat as pending
                return False
            if numeric == 0:
                return False
            return None

        async def _populate_from_endpoint(url: str) -> str:
            for i in range(0, len(ids), 100):
                batch = ids[i : i + 100]
                try:
                    async with self._session.get(
                        url,
                        params={"userIds": batch},
                        headers=headers,
                        timeout=20,
                    ) as resp:
                        if resp.status == 404:
                            return "not_found"
                        if resp.status != 200:
                            self._logger.warning(
                                "Roblox friends status API returned %s", resp.status
                            )
                            for uid in batch:
                                result[uid] = None
                            continue
                        data = await resp.json()
                except aiohttp.ClientError as exc:
                    self._logger.warning("Roblox friends status API error: %s", exc)
                    for uid in batch:
                        result[uid] = None
                    continue

                payload_entries = data.get("data")
                if not isinstance(payload_entries, list):
                    self._logger.warning(
                        "Unexpected friends status payload shape: %s", data
                    )
                    for uid in batch:
                        result[uid] = None
                    continue

                for entry in payload_entries:
                    target_id = entry.get("id") or entry.get("userId")
                    status = entry.get("status") or entry.get("friendStatus")
                    if target_id is None:
                        continue
                    result[int(target_id)] = _interpret_friend_status(status)

                for uid in batch:
                    result.setdefault(uid, None)

            return "ok"

        endpoint_used = ROBLOX_MY_FRIEND_STATUS_URL
        outcome = await _populate_from_endpoint(endpoint_used)

        if outcome == "not_found":
            endpoint_used = None
            auth_user_id = await self._fetch_authenticated_user_id()
            if not auth_user_id:
                for uid in ids:
                    result.setdefault(uid, None)
                return result

            endpoint_used = ROBLOX_FRIEND_STATUS_URL.format(user_id=auth_user_id)
            outcome = await _populate_from_endpoint(endpoint_used)

        if outcome == "not_found" and endpoint_used:
            self._logger.warning(
                "Roblox friends status API returned 404 for endpoint %s; "
                "skipping connection checks",
                endpoint_used,
            )
            self._authenticated_user_id = None
            self._skip_connection_checks = True
            for uid in ids:
                result.setdefault(uid, None)

        return result

    async def _collect_tracked_members(
        self, guild: discord.Guild
    ) -> Dict[str, list[discord.Member]]:
        tracked_roles = {
            CLAN_MEMBER_ROLE_ID,
            CLAN_MEMBER_ROLE_EN_ID,
        }
        if not guild.chunked:
            try:
                await guild.chunk(cache=True)
            except discord.HTTPException:
                pass

        members_to_check: set[discord.Member] = set()
        for role_id in tracked_roles:
            role = guild.get_role(role_id)
            if not role:
                continue
            members_to_check.update(member for member in role.members if not member.bot)

        usernames: Dict[str, list[discord.Member]] = defaultdict(list)
        for member in members_to_check:
            username = self._find_roblox_username(member)
            if not username:
                continue
            usernames[username].append(member)

        return usernames

    def _format_timedelta(self, delta_seconds: float) -> str:
        seconds = int(delta_seconds)
        parts: list[str] = []

        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)

        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if seconds or not parts:
            parts.append(f"{seconds}s")

        return " ".join(parts)

    @staticmethod
    def _dedupe_label(label: str) -> str:
        parts = [part.strip() for part in label.split(" – ", 1)]
        if len(parts) == 2 and parts[0] == parts[1]:
            return parts[0]
        return label

    def _update_presence_tracking(
        self, user_id: int, status: Optional[bool], label: str, now: datetime
    ) -> tuple[float, bool, Optional[float]]:
        self._user_labels[user_id] = label

        state = self._presence_state.get(user_id)
        if state is None:
            self._presence_state[user_id] = {
                "status": status,
                "last_change": now,
                "last_update": now,
            }
            return 0.0, False, None

        previous_status = state.get("status")
        last_change = state.get("last_change", now) or now
        last_update = state.get("last_update", now) or now
        offline_transition = False
        ended_online_duration: Optional[float] = None

        elapsed = (now - last_update).total_seconds()
        if previous_status is True:
            self._duration_totals[user_id]["online"] += elapsed
        elif previous_status is False:
            self._duration_totals[user_id]["offline"] += elapsed

        if status != previous_status:
            if previous_status is True and status is False:
                offline_transition = True
                ended_online_duration = (now - last_change).total_seconds()
            last_change = now

        self._presence_state[user_id] = {
            "status": status,
            "last_change": last_change,
            "last_update": now,
        }

        self._persist_user_state(user_id)

        return (now - last_change).total_seconds(), offline_transition, ended_online_duration

    def _finalize_totals(self, now: datetime) -> None:
        if not self._tracking_enabled:
            return

        for user_id, state in list(self._presence_state.items()):
            status = state.get("status")
            last_update = state.get("last_update", now) or now
            elapsed = (now - last_update).total_seconds()
            if elapsed <= 0:
                continue

            if status is True:
                self._duration_totals[user_id]["online"] += elapsed
            elif status is False:
                self._duration_totals[user_id]["offline"] += elapsed

            state["last_update"] = now

        self._persist_all_state()

    def _build_presence_details(
        self,
        tracked: Dict[str, list[discord.Member]],
        resolved_ids: Dict[str, int],
        presence: Dict[int, Optional[bool]],
        missing_usernames: Set[str],
        now: datetime,
        *,
        mention_offline_only: bool,
        connections: Dict[int, Optional[bool]],
    ) -> tuple[
        list[str],
        list[str],
        list[str],
        list[dict],
        list[tuple[discord.Member, str, float]],
    ]:
        online_lines: list[str] = []
        offline_lines: list[str] = []
        unresolved_lines: list[str] = []
        details: list[dict] = []
        offline_notifications: list[tuple[discord.Member, str, float]] = []

        for username, members in tracked.items():
            mentions_text = ", ".join(f"**{m.mention}**" for m in members)
            names_text = ", ".join(f"**{m.display_name}**" for m in members)
            lower = username.lower()
            detail = {
                "username": username,
                "members_mentions": mentions_text,
                "members_names": names_text,
                "members_display": mentions_text,
                "status": None,
                "duration": "",
                "note": None,
            }

            if lower not in resolved_ids:
                message = f"**{username}** – Roblox account not found"
                detail["note"] = "Roblox account not found"
                unresolved_lines.append(message)
                details.append(detail)
                continue

            user_id = resolved_ids[lower]
            is_online = presence.get(user_id)
            detail["status"] = is_online
            members_text = (
                names_text
                if mention_offline_only and is_online is True
                else mentions_text
            )
            detail["members_display"] = members_text
            if self._tracking_enabled and is_online is not None:
                duration_seconds, went_offline, ended_online_duration = self._update_presence_tracking(
                    user_id, is_online, f"**{username}**", now
                )
                if went_offline:
                    session_seconds = ended_online_duration or 0.0
                    for member in members:
                        offline_notifications.append(
                            (member, username, session_seconds)
                        )
                duration = self._format_timedelta(duration_seconds)
            else:
                duration = "tracking disabled"

            detail["duration"] = duration

            if is_online is True:
                online_lines.append(f"🟢 **{username}** – online {duration}")
            elif is_online is False:
                connection_status = connections.get(user_id)
                note = None
                if connection_status is False:
                    note = "Player is not friends with senpaicat22"
                detail["note"] = note
                status_text = f"🔴 **{username}** – offline {duration}"
                if note:
                    status_text = f"{status_text} ({note})"
                offline_lines.append(status_text)
            else:
                unresolved_lines.append(
                    f"**{username}** – status could not be determined"
                )

            details.append(detail)

        if missing_usernames:
            unresolved_lines.append(
                "Could not retrieve data for: "
                + ", ".join(f"**{name}**" for name in sorted(missing_usernames))
            )

        return online_lines, offline_lines, unresolved_lines, details, offline_notifications

    @staticmethod
    def _chunk_lines(lines: list[str], limit: int = 1024) -> list[str]:
        """Split lines into chunks that stay within a safe character limit."""

        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        for line in lines:
            line_len = len(line)
            # If a single line is too long, truncate it hard to avoid API errors.
            if line_len > limit:
                line = line[: limit - 1] + "…"
                line_len = len(line)

            extra_len = line_len + (1 if current else 0)
            if current_len + extra_len > limit:
                chunks.append("\n".join(current))
                current = [line]
                current_len = line_len
            else:
                current.append(line)
                current_len += extra_len

        if current:
            chunks.append("\n".join(current))

        return chunks

    def _render_leaderboard_image(self, *_: object, **__: object):
        """Compatibility shim; image rendering has been removed."""
        return None

    @staticmethod
    def _strip_basic_markdown(value: str) -> str:
        return re.sub(r"[*_`~]", "", value)


    @app_commands.command(
        name="roblox_activity",
        description="Check which clan members are playing Rebirth Champions Ultimate.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def roblox_activity(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        player_embeds, summary_view, offline_notifications = await self._build_presence_report(
            interaction.guild, mention_offline_only=True
        )
        if summary_view is None:
            await interaction.followup.send(
                "No members with the required roles and a Roblox nickname in their display name were found.",
                ephemeral=True,
            )
            return

        await self._send_offline_notifications(offline_notifications)

        for message in player_embeds:
            await interaction.followup.send(
                content=message.get("content"),
                view=message.get("view"),
                allowed_mentions=message.get("allowed_mentions"),
                ephemeral=True,
            )

        await interaction.followup.send(
            view=summary_view,
            ephemeral=True,
        )

    async def _build_presence_report(
        self, guild: discord.Guild, *, mention_offline_only: bool
    ) -> tuple[
        list[dict],
        Optional[discord.ui.LayoutView],
        list[tuple[discord.Member, str, float]],
    ]:
        tracked = await self._collect_tracked_members(guild)
        if not tracked:
            return [], None, []

        usernames = list(tracked.keys())
        resolved_ids, missing_usernames = await self._fetch_user_ids(usernames)
        presence = await self._fetch_presence(resolved_ids.values()) if resolved_ids else {}
        offline_ids = [
            resolved_ids[name]
            for name in resolved_ids
            if presence.get(resolved_ids[name]) is False
        ]
        connections = await self._fetch_connection_statuses(offline_ids)
        now = datetime.now(timezone.utc)

        (
            online_lines,
            offline_lines,
            unresolved_lines,
            details,
            offline_notifications,
        ) = self._build_presence_details(
            tracked,
            resolved_ids,
            presence,
            missing_usernames,
            now,
            mention_offline_only=mention_offline_only,
            connections=connections,
        )

        status_message = (
            "Monitoring is active: checks every 5 minutes; reports every 30 minutes."
            if self._tracking_enabled
            else "Monitoring is disabled. Enable it with /roblox_tracking."
        )

        player_embeds: list[dict] = []
        for detail in details:
            username = detail["username"]
            status = detail["status"]
            members_text = detail.get("members_display") or detail.get(
                "members_mentions"
            )
            duration = detail["duration"] or "N/A"
            note = detail.get("note")

            if status is True:
                continue

            if status is False:
                note_suffix = f" {note}" if note else ""
                player_embeds.append(
                    {
                        "view": self._build_player_status_view(
                            username,
                            members_text,
                            status_label="is offline",
                            icon="🔴",
                            note=note,
                        ),
                        "content": None,
                        "allowed_mentions": None,
                    }
                )
                continue

            if status is None:
                player_embeds.append(
                    {
                        "view": self._build_player_status_view(
                            username,
                            members_text,
                            status_label="could not be verified",
                            icon="⚪",
                        ),
                        "content": None,
                        "allowed_mentions": None,
                    }
                )
                continue

            icon = "🟢" if status is True else "🔴" if status is False else "⚪"
            status_label = (
                "Online" if status is True else "Offline" if status is False else "Unknown"
            )
            content_lines = [
                f"{icon} **{username}**",
                f"Tracked accounts: {members_text}",
                f"Status: {status_label}",
                f"Status duration: {duration}",
            ]
            if note:
                content_lines.append(f"Note: {note}")

            player_embeds.append(
                {
                    "view": None,
                    "content": "\n".join(content_lines),
                    "allowed_mentions": None,
                }
            )

        summary_view = self._build_summary_view(
            status_message,
            online_lines,
            offline_lines,
            unresolved_lines,
        )

        return player_embeds, summary_view, offline_notifications

    def _build_player_status_view(
        self,
        username: str,
        members_text: str,
        *,
        status_label: str,
        icon: str,
        note: Optional[str] = None,
    ) -> discord.ui.LayoutView:
        sections: list[discord.ui.LayoutViewItem] = [
            discord.ui.TextDisplay(content=f"{icon} **{username}** {status_label}.")
        ]

        sections.append(discord.ui.Separator(visible=True))
        sections.append(
            discord.ui.TextDisplay(content=f"Tracked accounts: {members_text}.")
        )

        if note:
            sections.append(
                discord.ui.TextDisplay(
                    content=f"Note: {note}" if note else ""
                )
            )

        view = discord.ui.LayoutView(timeout=None)
        view.add_item(discord.ui.Container(*sections))
        return view

    def _build_summary_view(
        self,
        status_message: str,
        online_lines: list[str],
        offline_lines: list[str],
        unresolved_lines: list[str],
    ) -> Optional[discord.ui.LayoutView]:
        sections: list[discord.ui.TextDisplay] = [
            discord.ui.TextDisplay(content="Roblox clan activity summary"),
            discord.ui.Separator(visible=True),
            discord.ui.TextDisplay(content="RCU Clan Wars activities"),
            discord.ui.Separator(visible=True),
            discord.ui.TextDisplay(
                content=(
                    "RCU Clan Wars activity monitoring. "
                    "Monitored roles: HROT and HROT EN. "
                    "Nicknames must include the Roblox username. "
                    f"{status_message}"
                )
            ),
        ]

        def _maybe_add_section(title: str, lines: list[str]):
            if not lines:
                return
            chunks = self._chunk_lines(sorted(lines))
            for idx, chunk in enumerate(chunks):
                heading = title if idx == 0 else f"{title} (continued {idx})"
                sections.extend(
                    [
                        discord.ui.Separator(visible=True),
                        discord.ui.TextDisplay(
                            content=f"{heading}\n" + "\n".join(chunk.split("\n"))
                        ),
                    ]
                )

        _maybe_add_section("Online", online_lines)
        _maybe_add_section("Offline", offline_lines)
        _maybe_add_section("Could not verify", unresolved_lines)

        if len(sections) == 3 and not any(
            [online_lines, offline_lines, unresolved_lines]
        ):
            return None

        sections.append(discord.ui.Separator(visible=True))
        sections.append(
            discord.ui.TextDisplay(
                content="Timers reset when the status changes between online and offline."
            )
        )

        summary_view = discord.ui.LayoutView(timeout=None)
        summary_view.add_item(discord.ui.Container(*sections))
        return summary_view

    async def _send_offline_notifications(
        self, notifications: list[tuple[discord.Member, str, float]]
    ) -> None:
        for member, username, session_seconds in notifications:
            try:
                duration_text = self._format_timedelta(session_seconds)
                await member.send(
                    f"Your Roblox status for **{username}** changed to offline. "
                    f"The last online session lasted {duration_text}."
                )
            except discord.HTTPException as exc:
                self._logger.warning(
                    "Failed to send offline DM %s: %s", member.id, exc
                )
            await asyncio.sleep(0.2)

    @tasks.loop(minutes=30)
    async def presence_notifier(self):
        if not self._tracking_enabled:
            return

        channel = self.bot.get_channel(ROBLOX_ACTIVITY_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            return

        player_embeds, summary_view, offline_notifications = (
            await self._build_presence_report(channel.guild, mention_offline_only=True)
        )
        await self._send_offline_notifications(offline_notifications)

        if summary_view is None:
            return

        now = datetime.now(timezone.utc)
        should_report = (
            self._last_channel_report is None
            or (now - self._last_channel_report).total_seconds() >= 30 * 60
        )

        if not should_report:
            return

        self._last_channel_report = now
        self._persist_tracking_state()

        for message in player_embeds:
            try:
                await channel.send(
                    content=message.get("content"),
                    view=message.get("view"),
                    allowed_mentions=message.get("allowed_mentions"),
                )
            except discord.HTTPException as exc:
                self._logger.warning("Failed to send message for player: %s", exc)
            await asyncio.sleep(0.3)

        await channel.send(view=summary_view)

    @presence_notifier.before_loop
    async def _wait_for_ready(self):
        await self.bot.wait_until_ready()

    def _start_tracking_session(self) -> None:
        now = datetime.now(timezone.utc)
        self._tracking_enabled = True
        self._session_started_at = now
        self._session_ended_at = None
        self._presence_state.clear()
        self._duration_totals.clear()
        self._user_labels.clear()
        self._last_channel_report = None
        self._clear_persistence()
        self._persist_tracking_state()

    def _stop_tracking_session(self) -> None:
        if not self._tracking_enabled:
            return

        now = datetime.now(timezone.utc)
        self._finalize_totals(now)
        self._tracking_enabled = False
        self._session_ended_at = now
        self._persist_tracking_state()

    def _format_range(self) -> str:
        start = self._session_started_at.astimezone(timezone.utc)
        end_time = self._session_ended_at
        if end_time:
            end_time = end_time.astimezone(timezone.utc)
            return f"{start:%Y-%m-%d %H:%M UTC} – {end_time:%Y-%m-%d %H:%M UTC}"
        return f"{start:%Y-%m-%d %H:%M UTC} – right now"

    @app_commands.command(
        name="roblox_tracking",
        description="Enable or disable Roblox activity tracking.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def roblox_tracking(self, interaction: discord.Interaction, enabled: bool):
        if enabled:
            self._start_tracking_session()
            message = (
                "Roblox activity tracking has been enabled and statistics have been reset."
            )
        else:
            self._stop_tracking_session()
            message = (
                "Roblox activity tracking has been disabled. View the summary with "
                "/roblox_leaderboard."
            )

        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(
        name="roblox_leaderboard",
        description="Show total online and offline time since tracking was last enabled.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def roblox_leaderboard(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        if self._tracking_enabled:
            self._finalize_totals(datetime.now(timezone.utc))

        tracked_members = await self._collect_tracked_members(interaction.guild)
        if not tracked_members:
            await interaction.followup.send(
                "No members are currently being monitored for activity.",
                ephemeral=True,
            )
            return

        resolved_ids, _ = await self._fetch_user_ids(list(tracked_members.keys()))
        tracked_ids = {user_id for user_id in resolved_ids.values()}
        username_lookup = {user_id: username for username, user_id in resolved_ids.items()}

        filtered_totals = {
            user_id: totals
            for user_id, totals in self._duration_totals.items()
            if user_id in tracked_ids
        }

        if not filtered_totals:
            await interaction.followup.send(
                "No leaderboard data is available for the currently monitored members. "
                "Enable tracking and wait for the next activity check.",
                ephemeral=True,
            )
            return

        table_rows: list[dict[str, str]] = []
        for user_id, totals in sorted(
            filtered_totals.items(),
            key=lambda item: item[1]["online"],
            reverse=True,
        ):
            stored_label = self._user_labels.get(user_id)
            label = self._dedupe_label(stored_label) if stored_label else None
            if stored_label and label != stored_label:
                self._user_labels[user_id] = label
                self._persist_user_state(user_id)
            if not label:
                label = f"**{username_lookup.get(user_id, f'ID {user_id}')}**"
            online_text = self._format_timedelta(totals["online"])
            offline_text = self._format_timedelta(totals["offline"])
            total_time = totals["online"] + totals["offline"]
            online_ratio = (totals["online"] / total_time * 100) if total_time > 0 else 0.0
            table_rows.append(
                {
                    "label": self._strip_basic_markdown(label),
                    "online": online_text,
                    "offline": offline_text,
                    "percent": f"{online_ratio:.0f}%",
                }
            )

        await interaction.followup.send(
            file=discord.File(
                fp=self._render_leaderboard_image(table_rows),
                filename="roblox_leaderboard.png",
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="cookie",
        description="Store the Roblox cookie for presence authentication.",
    )
    async def set_cookie(self, interaction: discord.Interaction, value: str):
        if interaction.user.id != self._AUTHORIZED_COOKIE_USER_ID:
            await interaction.response.send_message(
                "You are not authorized to store the cookie.", ephemeral=True
            )
            return

        cookie = value.strip()
        if not cookie:
            await interaction.response.send_message(
                "Cookie cannot be empty.", ephemeral=True
            )
            return

        self._roblox_cookie = cookie
        self._persist_cookie_to_db()
        await interaction.response.send_message(
            "Cookie has been saved.", ephemeral=True
        )


