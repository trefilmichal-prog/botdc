from __future__ import annotations

from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from db import create_sz_message, get_sz_message, list_unread_sz_message_ids


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
                "This private message no longer exists.", ephemeral=True
            )
            return

        if interaction.guild_id != data["guild_id"]:
            await interaction.response.send_message(
                "This private message does not belong to this server.", ephemeral=True
            )
            return

        if interaction.user.id != data["recipient_id"]:
            await interaction.response.send_message(
                "Only the intended recipient can read this private message.", ephemeral=True
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

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._register_persistent_views()

    def _register_persistent_views(self) -> None:
        for private_message_id in list_unread_sz_message_ids(limit=2000):
            self.bot.add_view(SzReadView(private_message_id))

    @sz.command(
        name="send",
        description="Send a private message that is revealed only after clicking Read.",
    )
    @app_commands.guild_only()
    @app_commands.describe(user="Who should receive the private message", message="Message content")
    async def send_sz(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        message: app_commands.Range[str, 1, 1800],
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "This command only works inside a server.", ephemeral=True
            )
            return

        if user.id == interaction.user.id:
            await interaction.response.send_message(
                "You cannot send a private message to yourself.", ephemeral=True
            )
            return

        if user.bot:
            await interaction.response.send_message(
                "You cannot send a private message to a bot.", ephemeral=True
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
