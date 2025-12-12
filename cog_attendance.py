from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

import discord
from discord import app_commands
from discord.ext import commands

from config import SETUP_PANEL_ROLE_ID


class AttendanceStatus:
    READY = "ready"
    NOT_READY = "not_ready"
    WAITING = "waiting"


@dataclass
class AttendanceSession:
    role_id: int
    statuses: Dict[int, str] = field(default_factory=dict)

    def sync_members(self, members: list[discord.Member]) -> None:
        member_ids = {member.id for member in members if not member.bot}

        for member_id in member_ids:
            self.statuses.setdefault(member_id, AttendanceStatus.WAITING)

        for member_id in list(self.statuses):
            if member_id not in member_ids:
                self.statuses.pop(member_id, None)

    def set_status(self, user_id: int, status: str) -> None:
        self.statuses[user_id] = status

    def get_status(self, user_id: int) -> str:
        return self.statuses.get(user_id, AttendanceStatus.WAITING)


class AttendancePanelView(discord.ui.View):
    def __init__(self, cog: "AttendanceCog", session_id: int | None, role_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.session_id = session_id
        self.role_id = role_id

    def set_message_id(self, message_id: int) -> None:
        self.session_id = message_id

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
            self.stop()
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
            return

        member = guild.get_member(interaction.user.id)
        if member is None or role not in member.roles:
            await interaction.response.send_message(
                "Tento panel je jen pro členy vybrané role.", ephemeral=True
            )
            return

        members = [m for m in role.members if not m.bot]
        session.sync_members(members)

        if status is None:
            session.statuses.pop(member.id, None)
        else:
            session.set_status(member.id, status)

        embed = self.cog.build_panel_embed(role, session)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(
        label="Ready", style=discord.ButtonStyle.success, emoji="🟢", custom_id="attendance_ready"
    )
    async def mark_ready(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._update_status(interaction, AttendanceStatus.READY)

    @discord.ui.button(
        label="Not Ready", style=discord.ButtonStyle.danger, emoji="🔴", custom_id="attendance_not_ready"
    )
    async def mark_not_ready(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._update_status(interaction, AttendanceStatus.NOT_READY)

    @discord.ui.button(
        label="Waiting", style=discord.ButtonStyle.secondary, emoji="🟡", custom_id="attendance_waiting"
    )
    async def mark_waiting(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._update_status(interaction, AttendanceStatus.WAITING)

    @discord.ui.button(
        label="Aktualizovat seznam", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="attendance_refresh"
    )
    async def refresh_members(
        self, interaction: discord.Interaction, button: discord.ui.Button
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
            self.stop()
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
            return

        members = [m for m in role.members if not m.bot]
        session.sync_members(members)
        embed = self.cog.build_panel_embed(role, session)
        await interaction.response.edit_message(embed=embed, view=self)


class AttendanceCog(commands.Cog, name="Attendance"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: Dict[int, AttendanceSession] = {}

    def build_panel_embed(
        self, role: discord.Role, session: AttendanceSession
    ) -> discord.Embed:
        members = [member for member in role.members if not member.bot]
        session.sync_members(members)

        ready_members = []
        not_ready_members = []
        waiting_members = []

        for member in sorted(members, key=lambda m: m.display_name.lower()):
            status = session.get_status(member.id)
            if status == AttendanceStatus.READY:
                ready_members.append(member)
            elif status == AttendanceStatus.NOT_READY:
                not_ready_members.append(member)
            else:
                waiting_members.append(member)

        def format_members(items: list[discord.Member], emoji: str) -> str:
            if not items:
                return "—"
            return "\n".join(f"{emoji} {member.display_name}" for member in items)

        embed = discord.Embed(
            title="🎯 Docházkový panel",
            description=(
                f"Připrav se na akci! Klikni na příslušné tlačítko a dej týmu vědět,"
                f" jestli dorazíš.\n\n" f"Role: {role.mention}"
            ),
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name=f"🟢 Ready ({len(ready_members)})",
            value=format_members(ready_members, "🟢"),
            inline=False,
        )
        embed.add_field(
            name=f"🔴 Not Ready ({len(not_ready_members)})",
            value=format_members(not_ready_members, "🔴"),
            inline=False,
        )
        embed.add_field(
            name=f"🟡 Waiting ({len(waiting_members)})",
            value=format_members(waiting_members, "🟡"),
            inline=False,
        )

        total_members = len(members)
        embed.set_footer(
            text=f"Připraveno: {len(ready_members)} · Nepřijde: {len(not_ready_members)} · Čekáme: {len(waiting_members)} · Celkem: {total_members}"
        )
        return embed

    @app_commands.command(
        name="setup_ready_panel", description="Vytvoří docházkový panel pro vybranou roli."
    )
    @app_commands.describe(role="Role členů, kteří se mají označit jako připraveni.")
    @app_commands.checks.has_role(SETUP_PANEL_ROLE_ID)
    async def setup_ready_panel(
        self, interaction: discord.Interaction, role: discord.Role
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Tento příkaz lze použít pouze na serveru.", ephemeral=True
            )
            return

        session = AttendanceSession(role.id)
        session.sync_members([member for member in role.members if not member.bot])

        view = AttendancePanelView(self, session_id=None, role_id=role.id)
        embed = self.build_panel_embed(role, session)
        await interaction.response.send_message(content=role.mention, embed=embed, view=view)
        message = await interaction.original_response()

        self.sessions[message.id] = session
        view.set_message_id(message.id)

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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AttendanceCog(bot))
