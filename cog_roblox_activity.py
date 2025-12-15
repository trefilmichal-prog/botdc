import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Set, Tuple

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import CLAN_MEMBER_ROLE_EN_ID, CLAN_MEMBER_ROLE_ID, ROBLOX_ACTIVITY_CHANNEL_ID
from db import get_connection


ROBLOX_USERNAMES_URL = "https://users.roblox.com/v1/usernames/users"
ROBLOX_PRESENCE_URL = "https://presence.roblox.com/v1/presence/users"
ROBLOX_USERNAME_REGEX = re.compile(r"[A-Za-z0-9_]{3,20}")


class RobloxActivityCog(commands.Cog, name="RobloxActivity"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._logger = logging.getLogger(__name__)
        self._session: Optional[aiohttp.ClientSession] = None
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

    async def cog_load(self):
        self._session = aiohttp.ClientSession()
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

        cursor.execute(
            "SELECT tracking_enabled, session_started_at, session_ended_at FROM roblox_tracking_state WHERE id = 1"
        )
        row = cursor.fetchone()
        if row:
            tracking_enabled, started_at, ended_at = row
            try:
                self._tracking_enabled = bool(int(tracking_enabled))
            except (TypeError, ValueError):
                self._tracking_enabled = True
            self._session_started_at = (
                self._parse_datetime(started_at) or datetime.now(timezone.utc)
            )
            self._session_ended_at = self._parse_datetime(ended_at)
        else:
            now = datetime.now(timezone.utc)
            self._tracking_enabled = True
            self._session_started_at = now
            self._session_ended_at = None
            cursor.execute(
                "INSERT INTO roblox_tracking_state (id, tracking_enabled, session_started_at, session_ended_at) VALUES (1, ?, ?, ?)",
                (1, self._serialize_datetime(now), None),
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

        conn.commit()
        conn.close()

    def _persist_tracking_state(self, conn=None) -> None:
        should_close = False
        if conn is None:
            conn = get_connection()
            should_close = True

        conn.execute(
            """
            INSERT INTO roblox_tracking_state (id, tracking_enabled, session_started_at, session_ended_at)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                tracking_enabled = excluded.tracking_enabled,
                session_started_at = excluded.session_started_at,
                session_ended_at = excluded.session_ended_at
            """,
            (
                1 if self._tracking_enabled else 0,
                self._serialize_datetime(self._session_started_at),
                self._serialize_datetime(self._session_ended_at),
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
        for i in range(0, len(ids), 100):
            batch = ids[i : i + 100]
            try:
                async with self._session.post(
                    ROBLOX_PRESENCE_URL, json={"userIds": batch}, timeout=20
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
                if presence_type is None:
                    result[int(user_id)] = None
                    continue

                result[int(user_id)] = presence_type != 0

        return result

    async def _collect_tracked_members(
        self, guild: discord.Guild
    ) -> Dict[str, list[discord.Member]]:
        tracked_roles = {CLAN_MEMBER_ROLE_ID, CLAN_MEMBER_ROLE_EN_ID}
        usernames: Dict[str, list[discord.Member]] = defaultdict(list)
        for member in guild.members:
            if member.bot:
                continue
            if not any(role.id in tracked_roles for role in member.roles):
                continue
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
    ) -> float:
        self._user_labels[user_id] = label

        state = self._presence_state.get(user_id)
        if state is None:
            self._presence_state[user_id] = {
                "status": status,
                "last_change": now,
                "last_update": now,
            }
            return 0.0

        previous_status = state.get("status")
        last_change = state.get("last_change", now) or now
        last_update = state.get("last_update", now) or now

        elapsed = (now - last_update).total_seconds()
        if previous_status is True:
            self._duration_totals[user_id]["online"] += elapsed
        elif previous_status is False:
            self._duration_totals[user_id]["offline"] += elapsed

        if status != previous_status:
            last_change = now

        self._presence_state[user_id] = {
            "status": status,
            "last_change": last_change,
            "last_update": now,
        }

        self._persist_user_state(user_id)

        return (now - last_change).total_seconds()

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

    def _build_status_lines(
        self,
        tracked: Dict[str, list[discord.Member]],
        resolved_ids: Dict[str, int],
        presence: Dict[int, Optional[bool]],
        missing_usernames: Set[str],
        now: datetime,
    ) -> tuple[list[str], list[str], list[str]]:
        online_lines: list[str] = []
        offline_lines: list[str] = []
        unresolved_lines: list[str] = []

        for username, members in tracked.items():
            members_text = ", ".join(m.mention for m in members)
            lower = username.lower()
            if lower not in resolved_ids:
                unresolved_lines.append(
                    f"`{username}` ({members_text}) – Roblox účet nenalezen"
                )
                continue

            user_id = resolved_ids[lower]
            is_online = presence.get(user_id)
            if self._tracking_enabled and is_online is not None:
                duration_seconds = self._update_presence_tracking(
                    user_id, is_online, f"`{username}` ({members_text})", now
                )
                duration = self._format_timedelta(duration_seconds)
            else:
                duration = "sledování vypnuto"

            if is_online is True:
                online_lines.append(
                    f"🟢 `{username}` ({members_text}) – online {duration}"
                )
            elif is_online is False:
                offline_lines.append(
                    f"🔴 `{username}` ({members_text}) – offline {duration}"
                )
            else:
                unresolved_lines.append(
                    f"`{username}` ({members_text}) – status se nepodařilo zjistit"
                )

        if missing_usernames:
            unresolved_lines.append(
                "Nebylo možné získat data pro: "
                + ", ".join(f"`{name}`" for name in sorted(missing_usernames))
            )

        return online_lines, offline_lines, unresolved_lines

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

        embed = await self._build_presence_embed(interaction.guild)
        if embed is None:
            await interaction.followup.send(
                "Nenašel jsem žádné členy s potřebnými rolemi a Roblox nickem v přezdívce.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _build_presence_embed(
        self, guild: discord.Guild
    ) -> Optional[discord.Embed]:
        tracked = await self._collect_tracked_members(guild)
        if not tracked:
            return None

        usernames = list(tracked.keys())
        resolved_ids, missing_usernames = await self._fetch_user_ids(usernames)
        presence = await self._fetch_presence(resolved_ids.values()) if resolved_ids else {}
        now = datetime.now(timezone.utc)

        online_lines, offline_lines, unresolved_lines = self._build_status_lines(
            tracked, resolved_ids, presence, missing_usernames, now
        )

        status_message = (
            "Sledování je aktivní a probíhá každých 10 minut."
            if self._tracking_enabled
            else "Sledování je vypnuté. Zapněte ho příkazem /roblox_tracking."
        )

        embed = discord.Embed(
            title="Kontrola přítomnosti na Robloxu",
            colour=discord.Color.blurple(),
            description=(
                "Monitorované role: HROT a HROT EN. "
                "Nick v přezdívce musí obsahovat Roblox uživatelské jméno. "
                + status_message
            ),
        )

        self._add_lines_field(
            embed,
            name="Online",
            lines=sorted(online_lines),
            empty_message="Nikdo z monitorovaných členů není právě online na Robloxu.",
        )

        self._add_lines_field(
            embed,
            name="Offline",
            lines=sorted(offline_lines),
            empty_message="",  # When empty we simply omit the field.
        )

        self._add_lines_field(
            embed,
            name="Nepodařilo se ověřit",
            lines=unresolved_lines,
            empty_message="",  # When empty we simply omit the field.
        )

        embed.set_footer(text="Časy se resetují při změně stavu online/offline.")
        return embed

    @tasks.loop(minutes=10)
    async def presence_notifier(self):
        if not self._tracking_enabled:
            return

        channel = self.bot.get_channel(ROBLOX_ACTIVITY_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            return

        embed = await self._build_presence_embed(channel.guild)
        if embed is None:
            await channel.send(
                "Nenašel jsem žádné členy s potřebnými rolemi a Roblox nickem v přezdívce."
            )
            return

        await channel.send(embed=embed)

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


async def setup(bot: commands.Bot):
    await bot.add_cog(RobloxActivityCog(bot))
