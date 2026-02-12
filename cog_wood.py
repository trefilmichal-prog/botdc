from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Tuple

import discord
from discord.ext import commands, tasks
from discord import app_commands

from config import (
    REMINDER_INTERVAL_HOURS,
    WOOD_ADMIN_ROLE_ID,
    STAFF_ROLE_ID,
    TICKET_VIEWER_ROLE_ID,
)
from cog_discord_writer import get_writer
from i18n import DEFAULT_LOCALE, get_interaction_locale, get_message_locale, normalize_locale, t
from db import (
    set_setting,
    get_setting,
    set_resource_need,
    reset_resource_need,
    get_resources_status,
    add_delivery,
    get_inactive_users,
)


class WoodResource(str, Enum):
    WOOD = "wood"
    CACTUS_WOOD = "cactus wood"
    NUCLEAR_WOOD = "nuclear wood"
    UNDERWATER_WOOD = "underwater wood"
    ROYAL_WOOD = "royal wood"
    HACKER_WOOD = "hacker wood"
    DIAMOND_WOOD = "diamond wood"
    MAGMA_WOOD = "magma wood"
    HEAVEN_WOOD = "heaven wood"
    MAGIC_WOOD = "magic wood"
    CIRCUS_WOOD = "circus wood"
    JUNGLE_WOOD = "jungle wood"
    STEAMPUNK_WOOD = "steampunk wood"
    SAKURA_WOOD = "sakura wood"


def build_needed_materials_view(
    rows: List[Tuple[str, int, int]], locale: discord.Locale
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    lines = [
        f"## {t('wood_reminder_title', locale)}",
        t("wood_reminder_description", locale),
    ]
    for name, required, delivered in rows:
        remaining = max(required - delivered, 0)
        lines.append(
            t(
                "wood_reminder_field",
                locale,
                required=required,
                delivered=delivered,
                remaining=remaining,
            )
        )
    view.add_item(
        discord.ui.Container(*(discord.ui.TextDisplay(content=line) for line in lines))
    )
    return view


def has_setup_panel_access(interaction: discord.Interaction) -> bool:
    user = interaction.user

    if isinstance(user, discord.Member):
        member = user
    elif interaction.guild is not None:
        member = interaction.guild.get_member(user.id)
    else:
        member = None

    if member is None:
        return False

    if member.guild_permissions.administrator:
        return True

    return WOOD_ADMIN_ROLE_ID != 0 and any(
        role.id == WOOD_ADMIN_ROLE_ID for role in member.roles
    )


class WoodCog(commands.Cog, name="WoodCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # channel_id -> {"user_id": int, "resource": str}
        self.pending_tickets: Dict[int, Dict[str, Any]] = {}

        # persistent view pro button "Vytvořit ticket"
        self.bot.add_view(build_ticket_button_view(self, DEFAULT_LOCALE))

        # připomínky materiálů
        self.materials_reminder_loop.start()

    @staticmethod
    def _render_progress_bar(delivered: int, required: int, length: int = 14) -> str:
        if required <= 0:
            ratio = 1
        else:
            ratio = delivered / required
        ratio = min(max(ratio, 0), 1)
        filled = int(round(ratio * length))
        empty = max(length - filled, 0)
        return f"{'█' * filled}{'░' * empty}"

    def _build_panel_view(
        self, locale: discord.Locale, rows: List[Tuple[str, int, int]]
    ) -> discord.ui.LayoutView:
        view = discord.ui.LayoutView(timeout=None)
        header_lines = [
            f"## {t('wood_panel_title', locale)}",
            t("wood_panel_description", locale),
            f"### {t('wood_panel_howto_title', locale)}",
            t("wood_panel_howto_body", locale),
            f"### {t('wood_panel_commands_title', locale)}",
            t("wood_panel_commands_body", locale),
            t("wood_panel_footer", locale),
        ]
        view.add_item(
            discord.ui.Container(
                *(discord.ui.TextDisplay(content=line) for line in header_lines)
            )
        )

        resources_lines = [f"## {t('wood_panel_resources_title', locale)}"]
        if rows:
            done_count = sum(
                1 for _, required, delivered in rows if delivered >= required
            )
            remaining_total = sum(
                max(required - delivered, 0) for _, required, delivered in rows
            )
            resources_lines.append(
                t(
                    "wood_panel_resources_summary",
                    locale,
                    done=done_count,
                    total=len(rows),
                    remaining=remaining_total,
                )
            )
            resources_lines.append(f"### {t('wood_panel_legend_title', locale)}")
            resources_lines.append(t("wood_panel_legend_body", locale))

            for name, required, delivered in rows:
                remaining = max(required - delivered, 0)
                emoji = "✅" if delivered >= required else "⏳"
                progress_bar = self._render_progress_bar(delivered, required)
                resources_lines.append(
                    f"{emoji} **{name}**\n"
                    + t(
                        "wood_panel_resource_field",
                        locale,
                        delivered=delivered,
                        required=required,
                        remaining=remaining,
                        bar=progress_bar,
                    )
                )
        else:
            resources_lines.append(t("wood_panel_no_need", locale))
            resources_lines.append(f"### {t('wood_panel_no_data_title', locale)}")
            resources_lines.append(t("wood_panel_no_data_body", locale))

        view.add_item(
            discord.ui.Container(
                *(discord.ui.TextDisplay(content=line) for line in resources_lines)
            )
        )

        view.add_item(discord.ui.ActionRow(TicketButton(self, locale)))
        return view

    async def update_panel(self):
        channel_id_str = get_setting("panel_channel_id")
        message_id_str = get_setting("panel_message_id")
        if not channel_id_str or not message_id_str:
            return
        try:
            channel_id = int(channel_id_str)
            message_id = int(message_id_str)
        except ValueError:
            return

        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            msg = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            return

        guild = channel.guild if isinstance(channel, discord.TextChannel) else None
        locale = normalize_locale(getattr(guild, "preferred_locale", None)) if guild else DEFAULT_LOCALE

        rows = get_resources_status()
        view = self._build_panel_view(locale, rows)
        writer = get_writer(self.bot)
        await writer.edit_message(msg, embeds=[], view=view)

    async def _handle_setup_access_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> bool:
        if not isinstance(error, app_commands.CheckFailure):
            return False

        locale = get_interaction_locale(interaction)
        message = t("wood_setup_forbidden", locale)

        if interaction.response.is_done():
            writer = get_writer(interaction.client)
            await writer.send_interaction_followup(
                interaction, content=message, ephemeral=True
            )
        else:
            await interaction.response.send_message(message, ephemeral=True)

        return True

    # ---------- EVENTS ----------

    @commands.Cog.listener("on_message")
    async def on_message_tickets(self, message: discord.Message):
        if message.author.bot:
            return

        ch_id = message.channel.id
        if ch_id not in self.pending_tickets:
            return

        info = self.pending_tickets[ch_id]
        locale = info.get("locale") if isinstance(info, dict) else None
        if locale is None:
            locale = get_message_locale(message)
        if message.author.id != info["user_id"]:
            await message.channel.send(
                t("wood_ticket_foreign", locale),
                delete_after=10,
            )
            return

        content = message.content.strip()
        try:
            amount = int(content)
            if amount <= 0:
                raise ValueError
        except ValueError:
            await message.channel.send(
                t("wood_ticket_invalid_amount", locale),
                delete_after=10,
            )
            return

        resource_name = info["resource"]
        add_delivery(message.author.id, resource_name, amount)

        await message.channel.send(
            t(
                "wood_ticket_logged",
                locale,
                user=message.author.mention,
                amount=amount,
                resource=resource_name,
            )
            + "\n"
            + t("wood_ticket_channel_delete", locale)
        )

        self.pending_tickets.pop(ch_id, None)
        try:
            await self.update_panel()
        except Exception as e:
            print(f"Chyba update_panel po ticketu: {e}")

        try:
            logging.getLogger("botdc").info(
                "Mazání wood ticketu vyvolal %s (%s) v kanálu %s (%s).",
                message.author,
                message.author.id,
                message.channel.name if isinstance(message.channel, discord.TextChannel) else "unknown",
                message.channel.id,
            )
            await message.channel.delete(reason="Ticket uzavřen po zadání množství.")
        except discord.Forbidden:
            print("Bot nemá právo mazat kanály.")

    # ---------- TASKS ----------

    @tasks.loop(hours=REMINDER_INTERVAL_HOURS)
    async def materials_reminder_loop(self):
        try:
            rows = get_resources_status()
            needed = [
                (name, required, delivered)
                for (name, required, delivered) in rows
                if required > delivered
            ]
            if not needed:
                return

            inactive_ids = get_inactive_users()
            if not inactive_ids:
                return

            embed_locale = DEFAULT_LOCALE
            view = build_needed_materials_view(needed, embed_locale)

            for uid in inactive_ids:
                user = self.bot.get_user(uid)
                if user is None:
                    for guild in self.bot.guilds:
                        member = guild.get_member(uid)
                        if member is not None:
                            user = member
                            break
                if user is None:
                    continue
                try:
                    await user.send(
                        t("wood_reminder_intro", embed_locale),
                        view=view,
                    )
                except discord.Forbidden:
                    continue
        except Exception as e:
            print(f"Chyba v materials_reminder_loop: {e}")

    # ---------- SLASH COMMANDS ----------

    @app_commands.command(
        name="setup_panel",
        description="Vytvoří hlavní panel se surovinami a tlačítkem pro ticket (admin).",
    )
    @app_commands.check(has_setup_panel_access)
    async def setup_panel_cmd(self, interaction: discord.Interaction):
        locale = get_interaction_locale(interaction)
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                t("guild_text_only", locale),
                ephemeral=True,
            )
            return

        rows = get_resources_status()
        view = self._build_panel_view(locale, rows)
        msg = await channel.send(view=view)

        set_setting("panel_channel_id", str(channel.id))
        set_setting("panel_message_id", str(msg.id))

        await interaction.response.send_message(
            t("wood_panel_created", locale),
            ephemeral=True,
        )

    @setup_panel_cmd.error
    async def setup_panel_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if await self._handle_setup_access_error(interaction, error):
            return
        raise error

    @app_commands.command(
        name="set_need",
        description="Nastaví, kolik je potřeba určitého dřeva.",
    )
    @app_commands.check(has_setup_panel_access)
    @app_commands.describe(
        resource="Typ dřeva",
        required="Požadované množství",
    )
    async def set_need_cmd(
        self,
        interaction: discord.Interaction,
        resource: WoodResource,
        required: app_commands.Range[int, 1, 10_000_000],
    ):
        locale = get_interaction_locale(interaction)
        set_resource_need(resource.value, required)
        await interaction.response.send_message(
            t("wood_need_set", locale, resource=resource.value, required=required),
            ephemeral=True,
        )
        await self.update_panel()

    @set_need_cmd.error
    async def set_need_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if await self._handle_setup_access_error(interaction, error):
            return
        raise error

    @app_commands.command(
        name="reset_need",
        description="Resetuje potřeby (globálně nebo pro jedno dřevo).",
    )
    @app_commands.check(has_setup_panel_access)
    @app_commands.describe(
        resource="Konkrétní dřevo (prázdné = všechno).",
    )
    async def reset_need_cmd(
        self,
        interaction: discord.Interaction,
        resource: WoodResource | None = None,
    ):
        locale = get_interaction_locale(interaction)
        if resource is None:
            reset_resource_need(None)
            msg = t("wood_need_reset_all", locale)
        else:
            reset_resource_need(resource.value)
            msg = t("wood_need_reset_single", locale, resource=resource.value)
        await interaction.response.send_message(msg, ephemeral=True)
        await self.update_panel()

    @reset_need_cmd.error
    async def reset_need_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if await self._handle_setup_access_error(interaction, error):
            return
        raise error

    @app_commands.command(
        name="resources",
        description="Ukáže přehled nastavených potřeb a odevzdaného množství.",
    )
    async def resources_cmd(self, interaction: discord.Interaction):
        locale = get_interaction_locale(interaction)
        rows = get_resources_status()
        if not rows:
            await interaction.response.send_message(
                t("wood_resources_empty", locale),
                ephemeral=True,
            )
            return

        lines = [f"## {t('wood_resources_title', locale)}"]
        for name, required, delivered in rows:
            remaining = max(required - delivered, 0)
            emoji = "✅" if delivered >= required else "⏳"
            lines.append(
                f"{emoji} **{name}**\n"
                + t(
                    "wood_resources_field",
                    locale,
                    delivered=delivered,
                    required=required,
                    remaining=remaining,
                )
            )
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.Container(*(discord.ui.TextDisplay(content=line) for line in lines))
        )
        await interaction.response.send_message(view=view, ephemeral=True)


class WoodSelectView(discord.ui.LayoutView):
    def __init__(
        self, cog: WoodCog, ticket_owner_id: int, channel_id: int, locale: discord.Locale
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.ticket_owner_id = ticket_owner_id
        self.channel_id = channel_id
        self.locale = locale

        view_lines = [
            f"## {t('wood_ticket_title', locale)}",
            f"<@{ticket_owner_id}>",
            t("wood_ticket_instructions", locale),
        ]
        self.add_item(
            discord.ui.Container(
                *(discord.ui.TextDisplay(content=line) for line in view_lines)
            )
        )

        self.select = discord.ui.Select(
            placeholder=t("wood_ticket_select_placeholder", locale),
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=res.value, value=res.value)
                for res in WoodResource
            ],
            custom_id="wood_select",
        )
        self.select.callback = self.select_wood
        self.add_item(discord.ui.ActionRow(self.select))

    async def select_wood(self, interaction: discord.Interaction):
        locale = get_interaction_locale(interaction)
        if interaction.user.id != self.ticket_owner_id:
            await interaction.response.send_message(
                t("wood_ticket_foreign", locale),
                ephemeral=True,
            )
            return

        resource_value = select.values[0]
        self.cog.pending_tickets[self.channel_id] = {
            "user_id": self.ticket_owner_id,
            "resource": resource_value,
            "locale": locale,
        }

        self.select.disabled = True

        self.clear_items()
        selected_lines = [
            f"## {t('wood_ticket_title', locale)}",
            f"<@{self.ticket_owner_id}>",
            t("wood_ticket_selected", locale, resource=resource_value),
            t("wood_ticket_enter_amount", locale),
            t("wood_ticket_will_delete", locale),
        ]
        self.add_item(
            discord.ui.Container(
                *(discord.ui.TextDisplay(content=line) for line in selected_lines)
            )
        )
        self.add_item(discord.ui.ActionRow(self.select))

        await interaction.response.edit_message(view=self)


class TicketButton(discord.ui.Button):
    def __init__(self, cog: WoodCog, locale: discord.Locale):
        super().__init__(
            label=t("wood_ticket_button_label", locale),
            style=discord.ButtonStyle.primary,
            custom_id="create_wood_ticket",
        )
        self.cog = cog
        self.locale = locale

    async def callback(self, interaction: discord.Interaction):
        locale = get_interaction_locale(interaction)
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                t("guild_only", locale),
                ephemeral=True,
            )
            return

        base_channel = interaction.channel
        category = base_channel.category if isinstance(base_channel, discord.TextChannel) else None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            ),
        }

        if STAFF_ROLE_ID:
            staff_role = guild.get_role(STAFF_ROLE_ID)
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True,
                )

        if TICKET_VIEWER_ROLE_ID:
            ticket_viewer_role = guild.get_role(TICKET_VIEWER_ROLE_ID)
            if ticket_viewer_role:
                overwrites[ticket_viewer_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )

        safe_name = interaction.user.name.lower().replace(" ", "-")
        ch_name = f"ticket-wood-{safe_name}"[:90]
        ticket_channel = await guild.create_text_channel(
            name=ch_name,
            overwrites=overwrites,
            category=category,
            reason=t("wood_ticket_audit", DEFAULT_LOCALE, user=interaction.user, user_id=interaction.user.id),
        )

        view = WoodSelectView(self.cog, interaction.user.id, ticket_channel.id, locale)
        await ticket_channel.send(view=view)

        await interaction.response.send_message(
            t("wood_ticket_created", locale, channel=ticket_channel.mention),
            ephemeral=True,
        )


def build_ticket_button_view(
    cog: WoodCog, locale: discord.Locale
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.ActionRow(TicketButton(cog, locale)))
    return view
