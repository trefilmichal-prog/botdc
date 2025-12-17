from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from config import SETUP_PANEL_ROLE_ID
from db import delete_attendance_panel, load_attendance_panels, save_attendance_panel


class AttendanceStatus:
    READY = "ready"
    NOT_READY = "not_ready"
    WAITING = "waiting"


@dataclass
class AttendanceSession:
    guild_id: int
    channel_id: int
    role_id: int
    statuses: Dict[int, str] = field(default_factory=dict)

    def sync_members(self, members: List[discord.Member]) -> None:
        member_ids = {member.id for member in members if not member.bot}

        for member_id in member_ids:
            self.statuses.setdefault(member_id, AttendanceStatus.WAITING)

        for member_id in list(self.statuses):
            if member_id not in member_ids:
                self.statuses.pop(member_id, None)

    def set_status(self, user_id: int, status: str | None) -> None:
        if status is None:
            self.statuses.pop(user_id, None)
        else:
            self.statuses[user_id] = status

    def get_status(self, user_id: int) -> str:
        return self.statuses.get(user_id, AttendanceStatus.WAITING)


class AttendancePanelView(discord.ui.LayoutView):
    def __init__(self, cog: "AttendanceCog", session_id: int | None, role_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.session_id = session_id
        self.role_id = role_id

        self.summary = discord.ui.Container(
            discord.ui.TextDisplay(
                label="🎯 Docházkový panel", value="Označ svůj status tlačítky."
            ),
            discord.ui.TextDisplay(label="Role", value="Načítám…"),
        )

        self.ready_display = discord.ui.TextDisplay(label="🟢 Připraveno", value="—")
        self.not_ready_display = discord.ui.TextDisplay(
            label="🔴 Nepřijde", value="—"
        )
        self.waiting_display = discord.ui.TextDisplay(label="🟡 Čekáme", value="—")
        self.total_display = discord.ui.TextDisplay(label="👥 Celkem", value="0")

        statuses_container = discord.ui.Container(
            self.ready_display,
            self.not_ready_display,
            self.waiting_display,
            self.total_display,
        )

        actions = discord.ui.ActionRow()

        self.ready_button = discord.ui.Button(
            label="Ready",
            style=discord.ButtonStyle.success,
            emoji="🟢",
            custom_id="attendance_ready",
        )
        self.ready_button.callback = self.mark_ready
        actions.add_child(self.ready_button)

        self.not_ready_button = discord.ui.Button(
            label="Not Ready",
            style=discord.ButtonStyle.danger,
            emoji="🔴",
            custom_id="attendance_not_ready",
        )
        self.not_ready_button.callback = self.mark_not_ready
        actions.add_child(self.not_ready_button)

        self.waiting_button = discord.ui.Button(
            label="Waiting",
            style=discord.ButtonStyle.secondary,
            emoji="🟡",
            custom_id="attendance_waiting",
        )
        self.waiting_button.callback = self.mark_waiting
        actions.add_child(self.waiting_button)

        self.refresh_button = discord.ui.Button(
            label="Aktualizovat",
            style=discord.ButtonStyle.primary,
            emoji="🔄",
            custom_id="attendance_refresh",
        )
        self.refresh_button.callback = self.refresh_members
        actions.add_child(self.refresh_button)

        self.add_item(self.summary)
        self.add_item(discord.ui.Separator())
        self.add_item(statuses_container)
        self.add_item(discord.ui.Separator())
        self.add_item(actions)

    def set_message_id(self, message_id: int) -> None:
        self.session_id = message_id

    def update_displays(
        self, role: discord.Role, ready: List[discord.Member], not_ready: List[discord.Member], waiting: List[discord.Member]
    ) -> None:
        self.summary.children[1].value = role.mention
        self.ready_display.value = "\n".join(f"🟢 {m.display_name}" for m in ready) or "—"
        self.not_ready_display.value = "\n".join(
            f"🔴 {m.display_name}" for m in not_ready
        ) or "—"
        self.waiting_display.value = "\n".join(f"🟡 {m.display_name}" for m in waiting) or "—"
        total = len([m for m in role.members if not m.bot])
        self.total_display.value = str(total)

    async def _update_status(
        self, interaction: discord.Interaction, status: str | None
    ) -> None:
        if self.session_id is None:
            await interaction.response.send_message(
                "Panel ještě není připravený. Zkus to prosím znovu.", ephemeral=True
            )
            return

        session = self.cog.sessions.get(self.session_id)
        if session is None:
            await interaction.response.send_message(
                "Panel už není aktivní.", ephemeral=True
            )
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Tento panel lze použít jen na serveru.", ephemeral=True
            )
            return

        role = guild.get_role(session.role_id)
        if role is None:
            await interaction.response.send_message(
                "Role pro tento panel už neexistuje.", ephemeral=True
            )
            self.cog.deactivate_session(self.session_id)
            return

        member = guild.get_member(interaction.user.id)
        if member is None or role not in member.roles:
            await interaction.response.send_message(
                "Tento panel je jen pro členy vybrané role.", ephemeral=True
            )
            return

        members = [m for m in role.members if not m.bot]
        session.sync_members(members)
        session.set_status(member.id, status)
        ready, not_ready, waiting = self.cog.split_members(role, session)

        self.update_displays(role, ready, not_ready, waiting)
        save_attendance_panel(
            self.session_id,
            session.guild_id,
            session.channel_id,
            session.role_id,
            session.statuses,
        )

        content = self.cog.build_panel_content(role, ready, not_ready, waiting)
        await interaction.response.edit_message(content=content, view=self)

    async def mark_ready(
        self, interaction: discord.Interaction, button: discord.ui.Button | None = None
    ) -> None:
        await self._update_status(interaction, AttendanceStatus.READY)

    async def mark_not_ready(
        self, interaction: discord.Interaction, button: discord.ui.Button | None = None
    ) -> None:
        await self._update_status(interaction, AttendanceStatus.NOT_READY)

    async def mark_waiting(
        self, interaction: discord.Interaction, button: discord.ui.Button | None = None
    ) -> None:
        await self._update_status(interaction, AttendanceStatus.WAITING)

    async def refresh_members(
        self, interaction: discord.Interaction, button: discord.ui.Button | None = None
    ) -> None:
        if self.session_id is None:
            await interaction.response.send_message(
                "Panel ještě není připravený. Zkus to prosím znovu.", ephemeral=True
            )
            return

        session = self.cog.sessions.get(self.session_id)
        if session is None:
            await interaction.response.send_message(
                "Panel už není aktivní.", ephemeral=True
            )
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Tento panel lze použít jen na serveru.", ephemeral=True
            )
            return

        role = guild.get_role(session.role_id)
        if role is None:
            await interaction.response.send_message(
                "Role pro tento panel už neexistuje.", ephemeral=True
            )
            self.cog.deactivate_session(self.session_id)
            return

        members = [m for m in role.members if not m.bot]
        session.sync_members(members)
        ready, not_ready, waiting = self.cog.split_members(role, session)

        self.update_displays(role, ready, not_ready, waiting)
        save_attendance_panel(
            self.session_id,
            session.guild_id,
            session.channel_id,
            session.role_id,
            session.statuses,
        )

        content = self.cog.build_panel_content(role, ready, not_ready, waiting)
        await interaction.response.edit_message(content=content, view=self)


class AttendanceCog(commands.Cog, name="Attendance"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: Dict[int, AttendanceSession] = {}
        self._restored = False

    async def cog_load(self):
        await self.restore_panels()

    @commands.Cog.listener()
    async def on_ready(self):
        await self.restore_panels()

    @staticmethod
    def split_members(
        role: discord.Role, session: AttendanceSession
    ) -> Tuple[List[discord.Member], List[discord.Member], List[discord.Member]]:
        members = [member for member in role.members if not member.bot]
        session.sync_members(members)

        ready: List[discord.Member] = []
        not_ready: List[discord.Member] = []
        waiting: List[discord.Member] = []

        for member in sorted(members, key=lambda m: m.display_name.lower()):
            status = session.get_status(member.id)
            if status == AttendanceStatus.READY:
                ready.append(member)
            elif status == AttendanceStatus.NOT_READY:
                not_ready.append(member)
            else:
                waiting.append(member)

        return ready, not_ready, waiting

    @staticmethod
    def build_panel_content(
        role: discord.Role,
        ready: List[discord.Member],
        not_ready: List[discord.Member],
        waiting: List[discord.Member],
    ) -> str:
        def format_members(items: List[discord.Member], emoji: str) -> str:
            if not items:
                return "—"
            return "\n".join(f"{emoji} {member.display_name}" for member in items)

        total = len([m for m in role.members if not m.bot])

        return (
            f"🎯 Docházkový panel pro {role.mention}\n"
            "Vyber svůj status pomocí tlačítek.\n\n"
            f"🟢 Připraveno ({len(ready)}):\n{format_members(ready, '🟢')}\n\n"
            f"🔴 Nepřijde ({len(not_ready)}):\n{format_members(not_ready, '🔴')}\n\n"
            f"🟡 Čekáme ({len(waiting)}):\n{format_members(waiting, '🟡')}\n\n"
            f"Celkem členů: {total}"
        )

    def deactivate_session(self, message_id: int) -> None:
        self.sessions.pop(message_id, None)
        delete_attendance_panel(message_id)

    def build_view(self, session_id: int | None, role_id: int) -> AttendancePanelView:
        return AttendancePanelView(self, session_id=session_id, role_id=role_id)

    async def restore_panels(self):
        if self._restored:
            return

        self._restored = True
        for message_id, guild_id, channel_id, role_id, statuses in load_attendance_panels():
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                delete_attendance_panel(message_id)
                continue

            role = guild.get_role(role_id)
            if role is None:
                delete_attendance_panel(message_id)
                continue

            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                delete_attendance_panel(message_id)
                continue

            try:
                message = await channel.fetch_message(message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                delete_attendance_panel(message_id)
                continue

            session = AttendanceSession(
                guild_id=guild_id,
                channel_id=channel_id,
                role_id=role_id,
                statuses=statuses,
            )

            ready, not_ready, waiting = self.split_members(role, session)
            content = self.build_panel_content(role, ready, not_ready, waiting)

            view = self.build_view(message_id, role_id)
            view.update_displays(role, ready, not_ready, waiting)
            self.sessions[message_id] = session

            self.bot.add_view(view, message_id=message_id)
            try:
                await message.edit(content=content, view=view)
            except (discord.Forbidden, discord.HTTPException):
                delete_attendance_panel(message_id)

    @app_commands.command(
        name="setup_ready_panel", description="Vytvoří docházkový panel pro vybranou roli."
    )
    @app_commands.describe(role="Role členů, kteří se mají označit jako připraveni.")
    @app_commands.checks.has_role(SETUP_PANEL_ROLE_ID)
    async def setup_ready_panel(
        self, interaction: discord.Interaction, role: discord.Role
    ) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "Tento příkaz lze použít pouze na serveru.", ephemeral=True
            )
            return

        session = AttendanceSession(
            guild_id=interaction.guild.id,
            channel_id=interaction.channel.id,
            role_id=role.id,
        )
        session.sync_members([member for member in role.members if not member.bot])

        ready, not_ready, waiting = self.split_members(role, session)
        view = self.build_view(session_id=None, role_id=role.id)
        view.update_displays(role, ready, not_ready, waiting)
        content = self.build_panel_content(role, ready, not_ready, waiting)

        await interaction.response.send_message(
            content=content,
            view=view,
            allowed_mentions=discord.AllowedMentions(roles=[role]),
        )
        message = await interaction.original_response()

        self.sessions[message.id] = session
        view.set_message_id(message.id)

        save_attendance_panel(
            message.id,
            session.guild_id,
            session.channel_id,
            session.role_id,
            session.statuses,
        )

    @setup_ready_panel.error
    async def setup_ready_panel_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingRole):
            await interaction.response.send_message(
                "Na použití tohoto příkazu nemáš oprávnění.", ephemeral=True
            )
            return

        raise error
