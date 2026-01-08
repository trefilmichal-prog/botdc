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
    role_ids: List[int]
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
    def __init__(
        self, cog: "AttendanceCog", session_id: int | None, role_ids: List[int]
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.session_id = session_id
        self.role_ids = role_ids

        self.summary = discord.ui.Container(
            discord.ui.TextDisplay(content="## 🎯 Docházkový panel"),
            discord.ui.TextDisplay(content="Označ svůj status tlačítky."),
            discord.ui.TextDisplay(content="Role: Načítám…"),
        )

        self.ready_display = discord.ui.TextDisplay(content="🟢 Připraveno: —")
        self.not_ready_display = discord.ui.TextDisplay(content="🔴 Nepřijde: —")
        self.waiting_display = discord.ui.TextDisplay(content="🟡 Čekáme: —")
        self.total_display = discord.ui.TextDisplay(content="👥 Celkem: 0")

        statuses_container = discord.ui.Container(
            self.ready_display,
            self.not_ready_display,
            self.waiting_display,
            self.total_display,
        )

        self.ready_button = discord.ui.Button(
            label="Ready",
            style=discord.ButtonStyle.success,
            emoji="🟢",
            custom_id="attendance_ready",
        )
        self.ready_button.callback = self.mark_ready

        self.not_ready_button = discord.ui.Button(
            label="Not Ready",
            style=discord.ButtonStyle.danger,
            emoji="🔴",
            custom_id="attendance_not_ready",
        )
        self.not_ready_button.callback = self.mark_not_ready

        self.waiting_button = discord.ui.Button(
            label="Waiting",
            style=discord.ButtonStyle.secondary,
            emoji="🟡",
            custom_id="attendance_waiting",
        )
        self.waiting_button.callback = self.mark_waiting

        self.refresh_button = discord.ui.Button(
            label="Aktualizovat",
            style=discord.ButtonStyle.primary,
            emoji="🔄",
            custom_id="attendance_refresh",
        )
        self.refresh_button.callback = self.refresh_members

        actions = discord.ui.ActionRow(
            self.ready_button,
            self.not_ready_button,
            self.waiting_button,
            self.refresh_button,
        )

        self.add_item(self.summary)
        self.add_item(discord.ui.Separator())
        self.add_item(statuses_container)
        self.add_item(discord.ui.Separator())
        self.add_item(actions)

    def set_message_id(self, message_id: int) -> None:
        self.session_id = message_id

    def update_displays(
        self,
        roles: List[discord.Role],
        ready: List[discord.Member],
        not_ready: List[discord.Member],
        waiting: List[discord.Member],
        total: int,
    ) -> None:
        role_mentions = ", ".join(role.mention for role in roles) if roles else "—"
        self.summary.children[2].content = f"Role: {role_mentions}"
        self.ready_display.content = (
            "🟢 Připraveno:\n" + "\n".join(f"🟢 {m.display_name}" for m in ready)
            if ready
            else "🟢 Připraveno: —"
        )
        self.not_ready_display.content = (
            "🔴 Nepřijde:\n" + "\n".join(f"🔴 {m.display_name}" for m in not_ready)
            if not_ready
            else "🔴 Nepřijde: —"
        )
        self.waiting_display.content = (
            "🟡 Čekáme:\n" + "\n".join(f"🟡 {m.display_name}" for m in waiting)
            if waiting
            else "🟡 Čekáme: —"
        )
        self.total_display.content = f"👥 Celkem: {total}"

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

        roles = self.cog.get_roles_from_ids(guild, session.role_ids)
        if not roles:
            await interaction.response.send_message(
                "Role pro tento panel už neexistuje.", ephemeral=True
            )
            self.cog.deactivate_session(self.session_id)
            return
        session.role_ids = [role.id for role in roles]

        member = guild.get_member(interaction.user.id)
        if member is None or not any(role in member.roles for role in roles):
            await interaction.response.send_message(
                "Tento panel je jen pro členy vybrané role.", ephemeral=True
            )
            return

        session.set_status(member.id, status)
        ready, not_ready, waiting, members = self.cog.split_members(roles, session)

        self.update_displays(roles, ready, not_ready, waiting, len(members))
        save_attendance_panel(
            self.session_id,
            session.guild_id,
            session.channel_id,
            session.role_ids,
            session.statuses,
        )

        content = self.cog.build_panel_content(
            roles, ready, not_ready, waiting, len(members)
        )
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

        roles = self.cog.get_roles_from_ids(guild, session.role_ids)
        if not roles:
            await interaction.response.send_message(
                "Role pro tento panel už neexistuje.", ephemeral=True
            )
            self.cog.deactivate_session(self.session_id)
            return
        session.role_ids = [role.id for role in roles]

        ready, not_ready, waiting, members = self.cog.split_members(roles, session)

        self.update_displays(roles, ready, not_ready, waiting, len(members))
        save_attendance_panel(
            self.session_id,
            session.guild_id,
            session.channel_id,
            session.role_ids,
            session.statuses,
        )

        content = self.cog.build_panel_content(
            roles, ready, not_ready, waiting, len(members)
        )
        await interaction.response.edit_message(content=content, view=self)


class SetupReadyPanelView(discord.ui.LayoutView):
    def __init__(self, cog: "AttendanceCog", guild: discord.Guild):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild.id
        self.selected_role_ids: List[int] = []

        self.summary = discord.ui.Container(
            discord.ui.TextDisplay(content="## Nastavení docházkového panelu"),
            discord.ui.TextDisplay(
                content="Vyber role, pro které se má panel zobrazit."
            ),
            discord.ui.TextDisplay(content="Vybrané role: —"),
        )

        self.role_select = discord.ui.Select(
            placeholder="Zvol role",
            min_values=1,
            max_values=1,
            options=self._build_role_options(guild),
        )
        if len(self.role_select.options) > 1:
            self.role_select.max_values = min(25, len(self.role_select.options))
        self.role_select.callback = self.on_select

        self.confirm_button = discord.ui.Button(
            label="Vytvořit panel",
            style=discord.ButtonStyle.primary,
            custom_id="attendance_confirm",
            disabled=True,
        )
        self.confirm_button.callback = self.on_confirm

        self.add_item(self.summary)
        self.add_item(discord.ui.Separator())
        self.add_item(discord.ui.ActionRow(self.role_select))
        self.add_item(discord.ui.ActionRow(self.confirm_button))

    def _build_role_options(self, guild: discord.Guild) -> List[discord.SelectOption]:
        roles = [role for role in guild.roles if not role.is_default()]
        roles.sort(key=lambda r: r.position, reverse=True)
        options = [
            discord.SelectOption(label=role.name, value=str(role.id))
            for role in roles[:25]
        ]
        if not options:
            options.append(
                discord.SelectOption(
                    label="Žádné role nejsou k dispozici", value="none"
                )
            )
        return options

    async def on_select(self, interaction: discord.Interaction) -> None:
        if "none" in self.role_select.values:
            self.selected_role_ids = []
        else:
            self.selected_role_ids = [
                int(role_id) for role_id in self.role_select.values
            ]

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Tento panel lze použít jen na serveru.", ephemeral=True
            )
            return

        roles = self.cog.get_roles_from_ids(guild, self.selected_role_ids)
        role_mentions = ", ".join(role.mention for role in roles) if roles else "—"
        self.summary.children[2].content = f"Vybrané role: {role_mentions}"
        self.confirm_button.disabled = not bool(roles)
        await interaction.response.edit_message(view=self)

    async def on_confirm(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)

        guild = interaction.guild
        if guild is None or interaction.channel is None:
            await interaction.followup.send(
                "Tento panel lze použít jen na serveru.", ephemeral=True
            )
            return

        roles = self.cog.get_roles_from_ids(guild, self.selected_role_ids)
        if not roles:
            await interaction.followup.send(
                "Žádná z vybraných rolí není dostupná.", ephemeral=True
            )
            return

        await self.cog.send_panel(interaction, roles)

        self.role_select.disabled = True
        self.confirm_button.disabled = True
        role_mentions = ", ".join(role.mention for role in roles)
        self.summary.children[1].content = "Panel byl úspěšně vytvořen."
        self.summary.children[2].content = f"Role: {role_mentions}"
        await interaction.message.edit(view=self)


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
    def collect_members(roles: List[discord.Role]) -> List[discord.Member]:
        members: Dict[int, discord.Member] = {}
        for role in roles:
            for member in role.members:
                if member.bot:
                    continue
                members.setdefault(member.id, member)
        return list(members.values())

    @classmethod
    def split_members(
        cls,
        roles: List[discord.Role],
        session: AttendanceSession,
    ) -> Tuple[
        List[discord.Member],
        List[discord.Member],
        List[discord.Member],
        List[discord.Member],
    ]:
        members = cls.collect_members(roles)
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

        return ready, not_ready, waiting, members

    @staticmethod
    def build_panel_content(
        roles: List[discord.Role],
        ready: List[discord.Member],
        not_ready: List[discord.Member],
        waiting: List[discord.Member],
        total: int,
    ) -> str:
        def format_members(items: List[discord.Member], emoji: str) -> str:
            if not items:
                return "—"
            return "\n".join(f"{emoji} {member.display_name}" for member in items)

        role_mentions = ", ".join(role.mention for role in roles) if roles else "—"

        return (
            f"🎯 Docházkový panel pro {role_mentions}\n"
            "Vyber svůj status pomocí tlačítek.\n\n"
            f"🟢 Připraveno ({len(ready)}):\n{format_members(ready, '🟢')}\n\n"
            f"🔴 Nepřijde ({len(not_ready)}):\n{format_members(not_ready, '🔴')}\n\n"
            f"🟡 Čekáme ({len(waiting)}):\n{format_members(waiting, '🟡')}\n\n"
            f"Celkem členů: {total}"
        )

    def deactivate_session(self, message_id: int) -> None:
        self.sessions.pop(message_id, None)
        delete_attendance_panel(message_id)

    def build_view(
        self, session_id: int | None, role_ids: List[int]
    ) -> AttendancePanelView:
        return AttendancePanelView(self, session_id=session_id, role_ids=role_ids)

    @staticmethod
    def get_roles_from_ids(
        guild: discord.Guild, role_ids: List[int]
    ) -> List[discord.Role]:
        roles: List[discord.Role] = []
        for role_id in role_ids:
            role = guild.get_role(role_id)
            if role is not None:
                roles.append(role)
        return roles

    async def send_panel(
        self, interaction: discord.Interaction, roles: List[discord.Role]
    ) -> None:
        session = AttendanceSession(
            guild_id=interaction.guild.id,
            channel_id=interaction.channel.id,
            role_ids=[role.id for role in roles],
        )

        ready, not_ready, waiting, members = self.split_members(roles, session)
        view = self.build_view(session_id=None, role_ids=session.role_ids)
        view.update_displays(roles, ready, not_ready, waiting, len(members))
        content = self.build_panel_content(
            roles, ready, not_ready, waiting, len(members)
        )

        message = await interaction.followup.send(
            content=content,
            view=view,
            allowed_mentions=discord.AllowedMentions(roles=roles),
        )

        self.sessions[message.id] = session
        view.set_message_id(message.id)

        save_attendance_panel(
            message.id,
            session.guild_id,
            session.channel_id,
            session.role_ids,
            session.statuses,
        )

    async def restore_panels(self):
        if self._restored:
            return

        self._restored = True
        for (
            message_id,
            guild_id,
            channel_id,
            role_ids,
            statuses,
        ) in load_attendance_panels():
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                delete_attendance_panel(message_id)
                continue

            roles = self.get_roles_from_ids(guild, role_ids)
            if not roles:
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
                role_ids=[role.id for role in roles],
                statuses=statuses,
            )

            ready, not_ready, waiting, members = self.split_members(roles, session)
            content = self.build_panel_content(
                roles, ready, not_ready, waiting, len(members)
            )

            view = self.build_view(message_id, session.role_ids)
            view.update_displays(roles, ready, not_ready, waiting, len(members))
            self.sessions[message_id] = session

            self.bot.add_view(view, message_id=message_id)
            try:
                await message.edit(content=content, view=view)
            except (discord.Forbidden, discord.HTTPException):
                delete_attendance_panel(message_id)

    @app_commands.command(
        name="setup_ready_panel",
        description="Vytvoří docházkový panel pro vybrané role.",
    )
    @app_commands.checks.has_role(SETUP_PANEL_ROLE_ID)
    async def setup_ready_panel(
        self, interaction: discord.Interaction
    ) -> None:
        await interaction.response.defer(thinking=True)

        if interaction.guild is None or interaction.channel is None:
            await interaction.followup.send(
                "Tento příkaz lze použít pouze na serveru.", ephemeral=True
            )
            return

        view = SetupReadyPanelView(self, interaction.guild)
        await interaction.followup.send(
            content="Vyber role, pro které chceš vytvořit docházkový panel.",
            view=view,
        )

    @setup_ready_panel.error
    async def setup_ready_panel_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingRole):
            if interaction.response.is_done():
                await interaction.followup.send(
                    "Na použití tohoto příkazu nemáš oprávnění.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "Na použití tohoto příkazu nemáš oprávnění.", ephemeral=True
                )
            return

        raise error
