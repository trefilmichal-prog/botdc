from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, Any, List, Tuple

import discord
from discord.ext import commands, tasks
from discord import app_commands

from config import REMINDER_INTERVAL_HOURS, STAFF_ROLE_ID, TICKET_VIEWER_ROLE_ID
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


def build_needed_materials_embed(rows: List[Tuple[str, int, int]]) -> discord.Embed:
    embed = discord.Embed(
        title="Potřebné materiály",
        description="Některé materiály stále chybí, budeme rádi za tvoji pomoc.",
        color=0xFF8800,
    )
    for name, required, delivered in rows:
        remaining = max(required - delivered, 0)
        embed.add_field(
            name=name,
            value=f"Potřeba: **{required}**\nOdevzdáno: **{delivered}**\nZbývá: **{remaining}**",
            inline=False,
        )
    return embed


class WoodCog(commands.Cog, name="WoodCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # channel_id -> {"user_id": int, "resource": str}
        self.pending_tickets: Dict[int, Dict[str, Any]] = {}

        # persistent view pro button "Vytvořit ticket"
        self.bot.add_view(TicketButtonView(self))

        # připomínky materiálů
        self.materials_reminder_loop.start()

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

        header = discord.Embed(
            title="Suroviny – těžba dřeva (Ultimate Rebirth Champions)",
            description=(
                "Přehled, kolik čeho je potřeba a kolik už bylo odevzdáno.\n"
                "K nahlášení použij tlačítko níže."
            ),
            color=0x00AAFF,
        )

        resources_embed = discord.Embed(
            title="Přehled dřev",
            color=0x00AAFF,
        )

        rows = get_resources_status()
        if not rows:
            resources_embed.add_field(
                name="Žádná data",
                value="Zatím není nastaveno, kolik čeho je potřeba. Použij `/set_need`.",
                inline=False,
            )
        else:
            for name, required, delivered in rows:
                remaining = max(required - delivered, 0)
                emoji = "✅" if delivered >= required else "⏳"
                resources_embed.add_field(
                    name=f"{emoji} {name}",
                    value=f"Odevzdáno: **{delivered}/{required}** (zbývá {remaining})",
                    inline=False,
                )

        await msg.edit(embeds=[header, resources_embed], view=TicketButtonView(self))

    # ---------- EVENTS ----------

    @commands.Cog.listener("on_message")
    async def on_message_tickets(self, message: discord.Message):
        if message.author.bot:
            return

        ch_id = message.channel.id
        if ch_id not in self.pending_tickets:
            return

        info = self.pending_tickets[ch_id]
        if message.author.id != info["user_id"]:
            await message.channel.send(
                "Toto je ticket jiného hráče. Jen vlastník ticketu sem může zadat číslo.",
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
                "Napiš prosím jen **kladné celé číslo** (např. `64`).",
                delete_after=10,
            )
            return

        resource_name = info["resource"]
        add_delivery(message.author.id, resource_name, amount)

        await message.channel.send(
            f"Zaznamenáno: {message.author.mention} – **{amount} × {resource_name}**.\n"
            f"Ticket kanál se nyní odstraní."
        )

        self.pending_tickets.pop(ch_id, None)
        try:
            await self.update_panel()
        except Exception as e:
            print(f"Chyba update_panel po ticketu: {e}")

        try:
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

            embed = build_needed_materials_embed(needed)

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
                        "Ahoj, delší dobu jsi nic neodevzdal a **stále nám chybí suroviny**.",
                        embed=embed,
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
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_panel_cmd(self, interaction: discord.Interaction):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Tento příkaz lze použít jen v textovém kanálu.",
                ephemeral=True,
            )
            return

        header = discord.Embed(
            title="Suroviny – těžba dřeva (Ultimate Rebirth Champions)",
            description=(
                "Zde bude přehled, kolik je potřeba kterého dřeva a kolik už je odevzdáno.\n"
                "K nahlášení použij tlačítko níže."
            ),
            color=0x00AAFF,
        )
        resources_embed = discord.Embed(
            title="Přehled dřev",
            description="Zatím žádná potřeba není nastavená. Použij `/set_need`.",
            color=0x00AAFF,
        )

        view = TicketButtonView(self)
        msg = await channel.send(embeds=[header, resources_embed], view=view)

        set_setting("panel_channel_id", str(channel.id))
        set_setting("panel_message_id", str(msg.id))

        await interaction.response.send_message(
            "Panel vytvořen v tomto kanálu.",
            ephemeral=True,
        )

    @app_commands.command(
        name="set_need",
        description="Nastaví, kolik je potřeba určitého dřeva.",
    )
    @app_commands.checks.has_permissions(administrator=True)
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
        set_resource_need(resource.value, required)
        await interaction.response.send_message(
            f"Nastavena potřeba pro **{resource.value}**: **{required}** kusů.",
            ephemeral=True,
        )
        await self.update_panel()

    @app_commands.command(
        name="reset_need",
        description="Resetuje potřeby (globálně nebo pro jedno dřevo).",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        resource="Konkrétní dřevo (prázdné = všechno).",
    )
    async def reset_need_cmd(
        self,
        interaction: discord.Interaction,
        resource: WoodResource | None = None,
    ):
        if resource is None:
            reset_resource_need(None)
            msg = "Resetovány všechny potřeby a všechna odevzdaná množství."
        else:
            reset_resource_need(resource.value)
            msg = f"Resetována potřeba pro **{resource.value}**."
        await interaction.response.send_message(msg, ephemeral=True)
        await self.update_panel()

    @app_commands.command(
        name="resources",
        description="Ukáže přehled nastavených potřeb a odevzdaného množství.",
    )
    async def resources_cmd(self, interaction: discord.Interaction):
        rows = get_resources_status()
        if not rows:
            await interaction.response.send_message(
                "Zatím není nastaveno, kolik čeho je potřeba.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Aktuální stav surovin",
            color=0x00AAFF,
        )
        for name, required, delivered in rows:
            remaining = max(required - delivered, 0)
            emoji = "✅" if delivered >= required else "⏳"
            embed.add_field(
                name=f"{emoji} {name}",
                value=f"Odevzdáno: **{delivered}/{required}** (zbývá {remaining})",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class WoodSelectView(discord.ui.View):
    def __init__(self, cog: WoodCog, ticket_owner_id: int, channel_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.ticket_owner_id = ticket_owner_id
        self.channel_id = channel_id

    @discord.ui.select(
        placeholder="Vyber typ dřeva",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label=res.value, value=res.value)
            for res in WoodResource
        ],
        custom_id="wood_select",
    )
    async def select_wood(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select,
    ):
        if interaction.user.id != self.ticket_owner_id:
            await interaction.response.send_message(
                "Toto je ticket jiného hráče.",
                ephemeral=True,
            )
            return

        resource_value = select.values[0]
        self.cog.pending_tickets[self.channel_id] = {
            "user_id": self.ticket_owner_id,
            "resource": resource_value,
        }

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content=(
                f"Vybral jsi: **{resource_value}**.\n"
                f"Napiš do tohoto ticketu **jen číslo** (množství), např. `64`.\n"
                f"Po zadání se ticket uloží a kanál smaže."
            ),
            view=self,
        )


class TicketButtonView(discord.ui.View):
    def __init__(self, cog: WoodCog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Vytvořit ticket na odevzdání dřeva",
        style=discord.ButtonStyle.primary,
        custom_id="create_wood_ticket",
    )
    async def create_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Tento příkaz lze použít jen na serveru.",
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
            reason=f"Ticket na dřevo od {interaction.user} ({interaction.user.id})",
        )

        view = WoodSelectView(self.cog, interaction.user.id, ticket_channel.id)
        await ticket_channel.send(
            content=interaction.user.mention,
            embed=discord.Embed(
                title="Ticket – odevzdání dřeva",
                description=(
                    "1) V dropdown menu níže vyber typ dřeva.\n"
                    "2) Pak napiš **jen číslo** (množství).\n"
                    "3) Po zadání čísla se ticket uloží a kanál smaže."
                ),
                color=0x00AA00,
            ),
            view=view,
        )

        await interaction.response.send_message(
            f"Ticket byl vytvořen: {ticket_channel.mention}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(WoodCog(bot))
