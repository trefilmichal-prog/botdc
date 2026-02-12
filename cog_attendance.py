from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from config import SETUP_PANEL_ROLE_ID
from cog_discord_writer import get_writer
from db import (
    delete_attendance_panel,
    delete_attendance_setup_panel,
    load_attendance_panels,
    load_attendance_setup_panels,
    save_attendance_panel,
    save_attendance_setup_panel,
)


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
        self.ready_display.content = self._format_members(ready, "🟢", "Připraveno")
        self.not_ready_display.content = self._format_members(
            not_ready, "🔴", "Nepřijde"
        )
        self.waiting_display.content = self._format_members(waiting, "🟡", "Čekáme")
        self.total_display.content = f"👥 Celkem: {total}"

    @staticmethod
    def _format_members(
        members: List[discord.Member], emoji: str, label: str
    ) -> str:
        if not members:
            return f"{emoji} {label}: —"
        return f"{emoji} {label}:\n" + "\n".join(
            f"{emoji} {member.display_name}" for member in members
        )

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

        await interaction.response.edit_message(view=self)

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

        await interaction.response.edit_message(view=self)


class SetupReadyPanelView(discord.ui.LayoutView):
    ROLE_PAGE_SIZE = 25

    def __init__(
        self,
        cog: "AttendanceCog",
        guild: discord.Guild,
        selected_role_ids: List[int] | None = None,
        page_index: int = 0,
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild.id
        self.selected_role_ids = selected_role_ids or []
        self.page_index = page_index
        self.page_role_ids: List[int] = []

        self.summary = discord.ui.Container(
            discord.ui.TextDisplay(content="## Nastavení docházkového panelu"),
            discord.ui.TextDisplay(
                content="Vyber role, pro které se má panel zobrazit."
            ),
            discord.ui.TextDisplay(content="Vybrané role: —"),
        )

        options = self._build_role_options(guild)
        max_values = min(self.ROLE_PAGE_SIZE, len(options))
        self.role_select = discord.ui.Select(
            placeholder=self._build_placeholder(guild),
            min_values=0,
            max_values=max_values,
            options=options,
            custom_id="attendance_setup_role_select",
        )
        self.role_select.callback = self.on_select

        self.prev_button = discord.ui.Button(
            label="Předchozí stránka",
            style=discord.ButtonStyle.secondary,
            custom_id="attendance_setup_prev_page",
            disabled=True,
        )
        self.prev_button.callback = self.on_prev_page

        self.next_button = discord.ui.Button(
            label="Další stránka",
            style=discord.ButtonStyle.secondary,
            custom_id="attendance_setup_next_page",
            disabled=True,
        )
        self.next_button.callback = self.on_next_page

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
        self.add_item(discord.ui.ActionRow(self.prev_button, self.next_button))
        self.add_item(discord.ui.ActionRow(self.confirm_button))
        self._sync_role_page(guild)
        self._sync_selection_summary(guild)

    def _available_roles(self, guild: discord.Guild) -> List[discord.Role]:
        roles = [role for role in guild.roles if not role.is_default()]
        roles.sort(key=lambda r: r.position, reverse=True)
        return roles

    def _build_placeholder(self, guild: discord.Guild) -> str:
        total_pages = self._total_pages(guild)
        if total_pages <= 1:
            return "Zvol role"
        return f"Zvol role (strana {self.page_index + 1}/{total_pages})"

    def _total_pages(self, guild: discord.Guild) -> int:
        roles = self._available_roles(guild)
        if not roles:
            return 1
        return (len(roles) + self.ROLE_PAGE_SIZE - 1) // self.ROLE_PAGE_SIZE

    def _build_role_options(self, guild: discord.Guild) -> List[discord.SelectOption]:
        roles = self._available_roles(guild)
        total_pages = self._total_pages(guild)
        if roles:
            self.page_index = max(0, min(self.page_index, total_pages - 1))
        else:
            self.page_index = 0
        start_index = self.page_index * self.ROLE_PAGE_SIZE
        page_roles = roles[start_index : start_index + self.ROLE_PAGE_SIZE]
        self.page_role_ids = [role.id for role in page_roles]
        options = [
            discord.SelectOption(
                label=role.name,
                value=str(role.id),
                default=role.id in self.selected_role_ids,
            )
            for role in page_roles
        ]
        if not options:
            options.append(
                discord.SelectOption(
                    label="Žádné role nejsou k dispozici", value="none", default=True
                )
            )
        return options

    def _sync_role_page(self, guild: discord.Guild) -> None:
        options = self._build_role_options(guild)
        self.role_select.options = options
        self.role_select.max_values = min(self.ROLE_PAGE_SIZE, len(options))
        self.role_select.min_values = 0
        self.role_select.placeholder = self._build_placeholder(guild)
        no_roles = len(options) == 1 and options[0].value == "none"
        self.role_select.disabled = no_roles
        total_pages = self._total_pages(guild)
        self.prev_button.disabled = self.page_index <= 0
        self.next_button.disabled = self.page_index >= total_pages - 1

    def _sync_selection_summary(self, guild: discord.Guild) -> List[discord.Role]:
        roles = self.cog.get_roles_from_ids(guild, self.selected_role_ids)
        self.selected_role_ids = [role.id for role in roles]
        role_mentions = ", ".join(role.mention for role in roles) if roles else "—"
        self.summary.children[2].content = f"Vybrané role: {role_mentions}"
        self.confirm_button.disabled = not bool(roles)
        return roles

    async def on_select(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Tento panel lze použít jen na serveru.", ephemeral=True
            )
            return

        if "none" in self.role_select.values:
            self.selected_role_ids = []
        else:
            current_page_selected = [
                int(role_id) for role_id in self.role_select.values
            ]
            preserved = [
                role_id
                for role_id in self.selected_role_ids
                if role_id not in self.page_role_ids
            ]
            self.selected_role_ids = preserved + current_page_selected

        self._sync_role_page(guild)
        self._sync_selection_summary(guild)
        if interaction.message is not None:
            channel_id = interaction.channel_id
            if channel_id is None and interaction.channel is not None:
                channel_id = interaction.channel.id
            save_attendance_setup_panel(
                interaction.message.id,
                guild.id,
                channel_id or 0,
                self.selected_role_ids,
                self.page_index,
            )
        await interaction.response.edit_message(view=self)

    async def on_prev_page(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Tento panel lze použít jen na serveru.", ephemeral=True
            )
            return

        self.page_index = max(0, self.page_index - 1)
        self._sync_role_page(guild)
        self._sync_selection_summary(guild)
        if interaction.message is not None:
            channel_id = interaction.channel_id
            if channel_id is None and interaction.channel is not None:
                channel_id = interaction.channel.id
            save_attendance_setup_panel(
                interaction.message.id,
                guild.id,
                channel_id or 0,
                self.selected_role_ids,
                self.page_index,
            )
        await interaction.response.edit_message(view=self)

    async def on_next_page(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Tento panel lze použít jen na serveru.", ephemeral=True
            )
            return

        total_pages = self._total_pages(guild)
        self.page_index = min(total_pages - 1, self.page_index + 1)
        self._sync_role_page(guild)
        self._sync_selection_summary(guild)
        if interaction.message is not None:
            channel_id = interaction.channel_id
            if channel_id is None and interaction.channel is not None:
                channel_id = interaction.channel.id
            save_attendance_setup_panel(
                interaction.message.id,
                guild.id,
                channel_id or 0,
                self.selected_role_ids,
                self.page_index,
            )
        await interaction.response.edit_message(view=self)

    async def on_confirm(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)

        guild = interaction.guild
        if guild is None or interaction.channel is None:
            writer = get_writer(self.cog.bot)
            await writer.send_interaction_followup(
                interaction,
                content="Tento panel lze použít jen na serveru.",
                ephemeral=True,
            )
            return

        roles = self.cog.get_roles_from_ids(guild, self.selected_role_ids)
        if not roles:
            writer = get_writer(self.cog.bot)
            await writer.send_interaction_followup(
                interaction,
                content="Žádná z vybraných rolí není dostupná.",
                ephemeral=True,
            )
            return

        await self.cog.send_panel(interaction, roles)

        self.role_select.disabled = True
        self.prev_button.disabled = True
        self.next_button.disabled = True
        self.confirm_button.disabled = True
        role_mentions = ", ".join(role.mention for role in roles)
        self.summary.children[1].content = "Panel byl úspěšně vytvořen."
        self.summary.children[2].content = f"Role: {role_mentions}"
        await interaction.message.edit(view=self)
        if interaction.message is not None:
            delete_attendance_setup_panel(interaction.message.id)


class AttendanceCog(commands.Cog, name="Attendance"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: Dict[int, AttendanceSession] = {}
        self._restored = False
        self._setup_restored = False

    async def cog_load(self):
        return

    @commands.Cog.listener()
    async def on_ready(self):
        await self.restore_panels()
        await self.restore_setup_panels()

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
        writer = get_writer(self.bot)
        message = await writer.send_interaction_followup(
            interaction,
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
        if not self.bot.is_ready():
            return
        if self._restored:
            return

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
            view = self.build_view(message_id, session.role_ids)
            view.update_displays(roles, ready, not_ready, waiting, len(members))
            self.sessions[message_id] = session

            self.bot.add_view(view, message_id=message_id)
            try:
                writer = get_writer(self.bot)
                await writer.edit_message(message, view=view)
            except (discord.Forbidden, discord.HTTPException):
                delete_attendance_panel(message_id)

        self._restored = True

    async def restore_setup_panels(self) -> None:
        if not self.bot.is_ready():
            return
        if self._setup_restored:
            return

        for (
            message_id,
            guild_id,
            channel_id,
            selected_role_ids,
            page_index,
        ) in load_attendance_setup_panels():
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                delete_attendance_setup_panel(message_id)
                continue

            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                delete_attendance_setup_panel(message_id)
                continue

            try:
                message = await channel.fetch_message(message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                delete_attendance_setup_panel(message_id)
                continue

            view = SetupReadyPanelView(
                self,
                guild,
                selected_role_ids=selected_role_ids,
                page_index=page_index,
            )
            self.bot.add_view(view, message_id=message_id)
            try:
                writer = get_writer(self.bot)
                await writer.edit_message(message, view=view)
            except (discord.Forbidden, discord.HTTPException):
                delete_attendance_setup_panel(message_id)

        self._setup_restored = True

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
            writer = get_writer(self.bot)
            await writer.send_interaction_followup(
                interaction,
                content="Tento příkaz lze použít pouze na serveru.",
                ephemeral=True,
            )
            return

        view = SetupReadyPanelView(self, interaction.guild)
        writer = get_writer(self.bot)
        message = await writer.send_interaction_followup(
            interaction, view=view
        )
        save_attendance_setup_panel(
            message.id, interaction.guild.id, interaction.channel.id
        )

    @setup_ready_panel.error
    async def setup_ready_panel_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingRole):
            if interaction.response.is_done():
                writer = get_writer(self.bot)
                await writer.send_interaction_followup(
                    interaction,
                    content="Na použití tohoto příkazu nemáš oprávnění.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "Na použití tohoto příkazu nemáš oprávnění.", ephemeral=True
                )
            return

        raise error
