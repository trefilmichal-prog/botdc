from __future__ import annotations

from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from db import (
    add_sz_reader_role,
    create_sz_message,
    get_sz_message,
    list_sz_reader_roles,
    list_unread_sz_message_ids,
    remove_sz_reader_role,
)


def _notice_view(message: str) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(discord.ui.TextDisplay(content=message)))
    return view


class SzReadView(discord.ui.LayoutView):
    def __init__(
        self,
        private_message_id: int,
        sender_id: int | None = None,
        recipient_id: int | None = None,
    ):
        super().__init__(timeout=None)
        self.private_message_id = int(private_message_id)

        container_items: list[discord.ui.Item] = [
            discord.ui.TextDisplay(content="## ✉️ New private message"),
        ]
        if sender_id is not None:
            container_items.append(
                discord.ui.TextDisplay(content=f"From: <@{int(sender_id)}>")
            )
        if recipient_id is not None:
            container_items.append(
                discord.ui.TextDisplay(content=f"To: <@{int(recipient_id)}>")
            )
        container_items.append(
            discord.ui.TextDisplay(content="Click **Read** to view the message content.")
        )

        self.add_item(discord.ui.Container(*container_items))
        self.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

        button = discord.ui.Button(
            label="Read",
            style=discord.ButtonStyle.primary,
            custom_id=f"sz_read:{self.private_message_id}",
        )
        button.callback = self._on_read_clicked
        self.add_item(discord.ui.ActionRow(button))

    async def _on_read_clicked(self, interaction: discord.Interaction) -> None:
        data = get_sz_message(self.private_message_id)
        if data is None:
            await interaction.response.send_message(
                view=_notice_view("This private message no longer exists."), ephemeral=True
            )
            return

        if interaction.guild_id != data["guild_id"]:
            await interaction.response.send_message(
                view=_notice_view("This private message does not belong to this server."),
                ephemeral=True,
            )
            return

        allowed = interaction.user.id == data["recipient_id"]
        if not allowed and isinstance(interaction.user, discord.Member):
            reader_roles = set(list_sz_reader_roles(data["guild_id"]))
            allowed = any(role.id in reader_roles for role in interaction.user.roles)

        if not allowed:
            await interaction.response.send_message(
                view=_notice_view(
                    "Only the intended recipient (or an allowed role) can read this private message."
                ),
                ephemeral=True,
            )
            return

        sent_at = data["created_at"]
        text_view = discord.ui.LayoutView(timeout=None)
        text_view.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(content="## 📩 Private message"),
                discord.ui.TextDisplay(content=f"**From:** <@{data['sender_id']}>"),
                discord.ui.TextDisplay(content=f"**To:** <@{data['recipient_id']}>"),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(content=data["content"]),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(content=f"Sent: `{sent_at}`"),
            )
        )
        await interaction.response.send_message(view=text_view, ephemeral=True)


class SecretMessageCog(commands.Cog, name="SecretMessageCog"):
    sz = app_commands.Group(name="sz", description="Private messages in the channel.")
    access = app_commands.Group(
        name="access",
        description="Configure roles that can read private messages.",
        parent=sz,
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._register_persistent_views()

    def _register_persistent_views(self) -> None:
        for private_message_id in list_unread_sz_message_ids(limit=2000):
            self.bot.add_view(SzReadView(private_message_id))

    @sz.command(name="sync", description="Manually sync slash commands for this server.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sync_sz(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                view=_notice_view("This command only works inside a server."),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            self.bot.tree.clear_commands(guild=interaction.guild)
            synced = await self.bot.tree.sync(guild=interaction.guild)
            await interaction.followup.send(
                view=_notice_view(
                    f"✅ Synced {len(synced)} slash command(s) for this server."
                ),
                ephemeral=True,
            )
        except Exception:
            await interaction.followup.send(
                view=_notice_view("❌ Slash command sync failed. Check bot logs."),
                ephemeral=True,
            )

    @sz.command(
        name="send",
        description="Send a private message that is revealed only after clicking Read.",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        user="Who should receive the private message", message="Message content"
    )
    async def send_sz(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        message: app_commands.Range[str, 1, 1800],
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                view=_notice_view("This command only works inside a server."),
                ephemeral=True,
            )
            return

        if user.id == interaction.user.id:
            await interaction.response.send_message(
                view=_notice_view("You cannot send a private message to yourself."),
                ephemeral=True,
            )
            return

        if user.bot:
            await interaction.response.send_message(
                view=_notice_view("You cannot send a private message to a bot."),
                ephemeral=True,
            )
            return

        private_message_id = create_sz_message(
            guild_id=interaction.guild_id,
            sender_id=interaction.user.id,
            recipient_id=user.id,
            content=message.strip(),
            created_at=datetime.utcnow().isoformat(timespec="seconds"),
        )

        posted_view = SzReadView(
            private_message_id=private_message_id,
            sender_id=interaction.user.id,
            recipient_id=user.id,
        )

        # persistent callback handler after restart
        self.bot.add_view(SzReadView(private_message_id))

        await interaction.response.send_message(view=posted_view)

    @access.command(name="add", description="Allow a role to read any private message.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(role="Role that should be allowed to read private messages")
    async def access_add(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                view=_notice_view("This command only works inside a server."),
                ephemeral=True,
            )
            return

        add_sz_reader_role(interaction.guild_id, role.id)
        await interaction.response.send_message(
            view=_notice_view(f"✅ Role {role.mention} can now read all private messages."),
            ephemeral=True,
        )

    @access.command(name="remove", description="Revoke role access to read private messages.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(role="Role to remove from private-message readers")
    async def access_remove(
        self, interaction: discord.Interaction, role: discord.Role
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                view=_notice_view("This command only works inside a server."),
                ephemeral=True,
            )
            return

        remove_sz_reader_role(interaction.guild_id, role.id)
        await interaction.response.send_message(
            view=_notice_view(
                f"🗑️ Role {role.mention} can no longer read other users' private messages."
            ),
            ephemeral=True,
        )

    @access.command(name="list", description="List roles that can read private messages.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def access_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                view=_notice_view("This command only works inside a server."),
                ephemeral=True,
            )
            return

        role_ids = list_sz_reader_roles(interaction.guild_id)
        if not role_ids:
            await interaction.response.send_message(
                view=_notice_view("No extra roles are allowed to read private messages yet."),
                ephemeral=True,
            )
            return

        mentions = "\n".join(f"• <@&{role_id}>" for role_id in role_ids)
        await interaction.response.send_message(
            view=_notice_view(f"## Allowed private-message reader roles\n{mentions}"),
            ephemeral=True,
        )
