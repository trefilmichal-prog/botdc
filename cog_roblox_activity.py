import asyncio
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Set, Tuple

import aiohttp
import discord
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
ROBLOX_USERNAME_REGEX = re.compile(r"[A-Za-z0-9_]{3,20}")


class RobloxActivityCog(commands.Cog, name="RobloxActivity"):
    _COOKIE_SETTING_KEY = "roblox_presence_cookie"
    _AUTHORIZED_COOKIE_USER_ID = 369810917673795586

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
            self._logger.warning("Nešlo načíst časovou hodnotu: %s", value)
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
                "Nepodařilo se přidat sloupec last_channel_report_at: %s", exc
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
                "Chybí Roblox ověřovací cookie – nelze získat stav přítomnosti."
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
            summary_text = f"**{username}**"
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
                message = f"**{username}** – Roblox účet nenalezen"
                detail["note"] = "Roblox účet nenalezen"
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
                    user_id, is_online, f"**{username}** – {summary_text}", now
                )
                if went_offline:
                    session_seconds = ended_online_duration or 0.0
                    for member in members:
                        offline_notifications.append(
                            (member, username, session_seconds)
                        )
                duration = self._format_timedelta(duration_seconds)
            else:
                duration = "sledování vypnuto"

            detail["duration"] = duration

            if is_online is True:
                online_lines.append(f"🟢 **{username}** – online {duration}")
            elif is_online is False:
                offline_lines.append(f"🔴 **{username}** – offline {duration}")
            else:
                unresolved_lines.append(
                    f"**{username}** – status se nepodařilo zjistit"
                )

            details.append(detail)

        if missing_usernames:
            unresolved_lines.append(
                "Nebylo možné získat data pro: "
                + ", ".join(f"**{name}**" for name in sorted(missing_usernames))
            )

        return online_lines, offline_lines, unresolved_lines, details, offline_notifications

    @staticmethod
    def _chunk_lines(lines: list[str], limit: int = 1024) -> list[str]:
        """Split a list of lines into strings that fit into embed field limits."""

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

    def _add_lines_field(
        self,
        embed: discord.Embed,
        name: str,
        lines: list[str],
        empty_message: str,
    ) -> None:
        """Add an embed field, splitting into multiple fields if necessary."""

        if not lines:
            if empty_message:
                embed.add_field(name=name, value=empty_message, inline=False)
            return

        chunks = self._chunk_lines(lines)
        for idx, chunk in enumerate(chunks):
            field_name = name if idx == 0 else f"{name} (pokračování {idx})"
            embed.add_field(name=field_name, value=chunk, inline=False)

    @app_commands.command(
        name="roblox_activity",
        description="Zkontroluje, kdo z členů clanu hraje Rebirth Champions Ultimate.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def roblox_activity(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                "Tento příkaz lze použít pouze na serveru.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        player_embeds, summary_embed, offline_notifications = await self._build_presence_report(
            interaction.guild, mention_offline_only=True
        )
        if summary_embed is None:
            await interaction.followup.send(
                "Nenašel jsem žádné členy s potřebnými rolemi a Roblox nickem v přezdívce.",
                ephemeral=True,
            )
            return

        await self._send_offline_notifications(offline_notifications)

        for message in player_embeds:
            await interaction.followup.send(
                content=message.get("content"),
                embed=message.get("embed"),
                allowed_mentions=message.get("allowed_mentions"),
                ephemeral=True,
            )

        await interaction.followup.send(embed=summary_embed, ephemeral=True)

    async def _build_presence_report(
        self, guild: discord.Guild, *, mention_offline_only: bool
    ) -> tuple[
        list[dict],
        Optional[discord.Embed],
        list[tuple[discord.Member, str, float]],
    ]:
        tracked = await self._collect_tracked_members(guild)
        if not tracked:
            return [], None, []

        usernames = list(tracked.keys())
        resolved_ids, missing_usernames = await self._fetch_user_ids(usernames)
        presence = await self._fetch_presence(resolved_ids.values()) if resolved_ids else {}
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
        )

        status_message = (
            "Sledování je aktivní: kontrola každých 5 minut, hlášení do kanálu každých 30 minut."
            if self._tracking_enabled
            else "Sledování je vypnuté. Zapněte ho příkazem /roblox_tracking."
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

            if status is False:
                embed = discord.Embed(
                    description=f"🔴 {detail['members_mentions']} Is offline! 💩",
                    colour=discord.Color.red(),
                )
                player_embeds.append(
                    {
                        "embed": embed,
                        "content": detail["members_mentions"],
                        "allowed_mentions": discord.AllowedMentions(
                            everyone=False, roles=False, users=True
                        ),
                    }
                )
                continue

            icon = "🟢" if status is True else "🔴" if status is False else "⚪"
            status_label = (
                "Online" if status is True else "Offline" if status is False else "Neznámý"
            )
            colour = (
                discord.Color.green()
                if status is True
                else discord.Color.red()
                if status is False
                else discord.Color.light_grey()
            )

            embed = discord.Embed(
                title=f"{icon} **{username}**",
                colour=colour,
                description=f"Sledované účty: {members_text}",
            )

            embed.add_field(name="Status", value=status_label, inline=True)
            embed.add_field(name="Trvání stavu", value=duration, inline=True)
            if note:
                embed.add_field(name="Poznámka", value=note, inline=False)
            embed.set_footer(text="Časy se resetují při změně stavu online/offline.")
            player_embeds.append({"embed": embed, "content": None, "allowed_mentions": None})

        summary_embed = discord.Embed(
            title="Kontrola přítomnosti na Robloxu",
            colour=discord.Color.blurple(),
            description=(
                "Monitorované role: HROT a HROT EN. "
                "Nick v přezdívce musí obsahovat Roblox uživatelské jméno. "
                + status_message
            ),
        )

        self._add_lines_field(
            summary_embed,
            name="Online",
            lines=sorted(online_lines),
            empty_message="Nikdo z monitorovaných členů není právě online na Robloxu.",
        )

        self._add_lines_field(
            summary_embed,
            name="Offline",
            lines=sorted(offline_lines),
            empty_message="",  # When empty we simply omit the field.
        )

        self._add_lines_field(
            summary_embed,
            name="Nepodařilo se ověřit",
            lines=unresolved_lines,
            empty_message="",  # When empty we simply omit the field.
        )

        summary_embed.set_footer(
            text="Časy se resetují při změně stavu online/offline."
        )
        return player_embeds, summary_embed, offline_notifications

    async def _send_offline_notifications(
        self, notifications: list[tuple[discord.Member, str, float]]
    ) -> None:
        for member, username, session_seconds in notifications:
            try:
                duration_text = self._format_timedelta(session_seconds)
                await member.send(
                    f"Tvůj Roblox status pro **{username}** se změnil na offline. "
                    f"Poslední online úsek trval {duration_text}."
                )
            except discord.HTTPException as exc:
                self._logger.warning(
                    "Nepodařilo se odeslat DM o odhlášení %s: %s", member.id, exc
                )
            await asyncio.sleep(0.2)

    @tasks.loop(minutes=5)
    async def presence_notifier(self):
        if not self._tracking_enabled:
            return

        channel = self.bot.get_channel(ROBLOX_ACTIVITY_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            return

        player_embeds, summary_embed, offline_notifications = await self._build_presence_report(
            channel.guild, mention_offline_only=True
        )
        await self._send_offline_notifications(offline_notifications)

        if summary_embed is None:
            return

        now = datetime.now(timezone.utc)
        should_send = False
        if self._last_channel_report is None:
            should_send = True
        else:
            should_send = (now - self._last_channel_report).total_seconds() >= 30 * 60

        if not should_send:
            return

        self._last_channel_report = now
        self._persist_tracking_state()

        for message in player_embeds:
            try:
                await channel.send(
                    content=message.get("content"),
                    embed=message.get("embed"),
                    allowed_mentions=message.get("allowed_mentions"),
                )
            except discord.HTTPException as exc:
                self._logger.warning("Nepodařilo se odeslat embed pro hráče: %s", exc)
            await asyncio.sleep(0.3)

        await channel.send(embed=summary_embed)

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
        return f"{start:%Y-%m-%d %H:%M UTC} – právě teď"

    @app_commands.command(
        name="roblox_tracking",
        description="Zapne nebo vypne sledování Roblox aktivity.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def roblox_tracking(self, interaction: discord.Interaction, enabled: bool):
        if enabled:
            self._start_tracking_session()
            message = (
                "Sledování Roblox aktivity bylo zapnuto a statistiky byly resetovány."
            )
        else:
            self._stop_tracking_session()
            message = (
                "Sledování Roblox aktivity bylo vypnuto. Souhrn lze zobrazit příkazem "
                "/roblox_leaderboard."
            )

        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(
        name="roblox_leaderboard",
        description="Zobrazí celkový čas online a offline od posledního zapnutí sledování.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def roblox_leaderboard(self, interaction: discord.Interaction):
        if self._tracking_enabled:
            self._finalize_totals(datetime.now(timezone.utc))

        if not self._duration_totals:
            await interaction.response.send_message(
                "Není k dispozici žádný záznam pro leaderboard. Zapněte sledování a počkejte na kontrolu.",
                ephemeral=True,
            )
            return

        lines: list[str] = []
        for user_id, totals in sorted(
            self._duration_totals.items(),
            key=lambda item: item[1]["online"],
            reverse=True,
        ):
            label = self._user_labels.get(user_id, f"ID {user_id}")
            online_text = self._format_timedelta(totals["online"])
            offline_text = self._format_timedelta(totals["offline"])
            lines.append(f"{label}: 🟢 {online_text} | 🔴 {offline_text}")

        embed = discord.Embed(
            title="Roblox leaderboard", colour=discord.Color.green()
        )
        embed.description = f"Rozsah měření: {self._format_range()}"
        self._add_lines_field(embed, "Souhrn", lines, "")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="cookie",
        description="Uloží Roblox cookie pro ověřování přítomnosti.",
    )
    async def set_cookie(self, interaction: discord.Interaction, value: str):
        if interaction.user.id != self._AUTHORIZED_COOKIE_USER_ID:
            await interaction.response.send_message(
                "Nemáš oprávnění uložit cookie.", ephemeral=True
            )
            return

        cookie = value.strip()
        if not cookie:
            await interaction.response.send_message(
                "Cookie nemůže být prázdná.", ephemeral=True
            )
            return

        self._roblox_cookie = cookie
        self._persist_cookie_to_db()
        await interaction.response.send_message(
            "Cookie byla uložena.", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RobloxActivityCog(bot))
