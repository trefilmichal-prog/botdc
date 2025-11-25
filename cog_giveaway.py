from __future__ import annotations

import asyncio
import random
from enum import Enum
from typing import Dict, Any, Optional, List

import discord
from discord.ext import commands
from discord import app_commands

from config import GIVEAWAY_PING_ROLE_ID, DEFAULT_GIVEAWAY_DURATION_MINUTES
from db import get_setting, set_setting


class GiveawayType(str, Enum):
    COIN = "coin"
    PET = "pet"
    SCREEN = "screen"  # screen giveaway – X výherců, bez pevné hodnoty


class GiveawayCog(commands.Cog, name="GiveawayCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # message_id -> stav giveaway
        self.active_giveaways: Dict[int, Dict[str, Any]] = {}

        # persistentní view pro giveaway tlačítka
        self.bot.add_view(GiveawayView(self))

    # ---------- INTERNÍ HELPERY ----------

    async def schedule_giveaway_auto_end(self, message_id: int, duration_minutes: int):
        try:
            await asyncio.sleep(duration_minutes * 60)
        except asyncio.CancelledError:
            return

        state = self.active_giveaways.get(message_id)
        if not state or state.get("ended"):
            return

        channel_id = state.get("channel_id")
        if channel_id is None:
            return

        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        try:
            message = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            return

        view = GiveawayView(self)
        await self.finalize_giveaway(message, state, view)

        await channel.send(
            f"Giveaway byla **automaticky ukončena** po {duration_minutes} minutách, "
            f"výherci jsou zobrazeni v embedu."
        )

    async def finalize_giveaway(
        self,
        message: discord.Message,
        state: Dict[str, Any],
        view: "GiveawayView",
    ):
        if state.get("ended"):
            return

        participants: set[int] = state.get("participants", set())
        if not participants:
            return

        state["ended"] = True

        embed = message.embeds[0] if message.embeds else discord.Embed(color=0xFFD700)
        embed = embed.copy()
        embed.color = 0xFFA500

        participants_list = list(participants)
        guild = message.guild
        guild_name = guild.name if guild else "serveru"
        host_id = state.get("host_id")
        host_mention = f"<@{host_id}>" if host_id else "organizátorem giveaway"

        # vizuální „rolování“
        for _ in range(5):
            candidate_id = random.choice(participants_list)
            embed.description = (
                "🎲 **Losuji výherce...**\n"
                f"Aktuální kandidát: <@{candidate_id}>"
            )
            await message.edit(embed=embed, view=view)
            await asyncio.sleep(0.8)

        gtype: GiveawayType = state["type"]
        winners_ids: List[int] = []

        if gtype == GiveawayType.COIN:
            amount: int = state["amount"]
            winners_count = min(3, len(participants_list))
            winners_ids = random.sample(participants_list, winners_count)

            base = amount // winners_count
            remainder = amount % winners_count

            winners_lines = []
            for idx, uid in enumerate(winners_ids):
                share = base + (1 if idx < remainder else 0)
                winners_lines.append(f"• <@{uid}> – **{share}** coinů")

            extra_message = f"Celkem rozdáno: **{amount}** coinů mezi {winners_count} hráče."
            embed.title = "🎉 Coin giveaway – výsledky"
            embed.description = extra_message + "\n\n" + "\n".join(winners_lines)

        elif gtype == GiveawayType.PET:
            pet_name: str = state["pet_name"]
            click_value: str = state["click_value"]
            winner_id = random.choice(participants_list)
            winners_ids = [winner_id]

            embed.title = "🎉 Pet giveaway – výsledky"
            embed.description = (
                f"Výherce peta **{pet_name}** (click hodnota: `{click_value}`):\n\n"
                f"🥇 <@{winner_id}>"
            )

        else:  # SCREEN
            configured = int(state.get("winners_count", 3))
            winners_count = min(configured, len(participants_list))
            winners_ids = random.sample(participants_list, winners_count)
            winners_lines = [f"• <@{uid}>" for uid in winners_ids]

            embed.title = "🎉 Screen giveaway – výsledky"
            embed.description = (
                f"Výherci z giveaway (nastaveno {configured} výherců, losováno {winners_count}):\n\n"
                + "\n".join(winners_lines)
            )

        embed.color = 0x00CC66
        embed.set_footer(text=f"Účastníků celkem: {len(participants_list)}")

        # vypnout tlačítka
        for child in view.children:
            child.disabled = True

        await message.edit(embed=embed, view=view)

        # DM výhercům
        for uid in winners_ids:
            user = self.bot.get_user(uid)
            if user is None and guild is not None:
                user = guild.get_member(uid)

            if user is None:
                continue

            try:
                if gtype == GiveawayType.COIN:
                    amount: int = state["amount"]
                    winners_count = len(winners_ids)
                    base = amount // winners_count
                    remainder = amount % winners_count
                    idx = winners_ids.index(uid)
                    share = base + (1 if idx < remainder else 0)

                    dm_text = (
                        f"Ahoj, gratuluji! Vyhrál jsi v **coin giveaway** na serveru **{guild_name}**.\n"
                        f"Tvoje výhra: **{share}** coinů.\n"
                        f"Prosím, ozvi se {host_mention} na serveru (přezdívka / domluva ohledně předání výhry)."
                    )

                elif gtype == GiveawayType.PET:
                    pet_name: str = state["pet_name"]
                    click_value: str = state["click_value"]
                    dm_text = (
                        f"Ahoj, gratuluji! Vyhrál jsi v **pet giveaway** na serveru **{guild_name}**.\n"
                        f"Dostáváš peta **{pet_name}** (click hodnota: `{click_value}`).\n"
                        f"Prosím, ozvi se {host_mention} na serveru (přezdívka / předání výhry)."
                    )
                else:  # SCREEN
                    dm_text = (
                        f"Ahoj, gratuluji! Vyhrál jsi v **screen giveaway** na serveru **{guild_name}**.\n"
                        f"Odměny jsou vidět v obrázku v giveaway.\n"
                        f"Prosím, ozvi se {host_mention} na serveru (přezdívka / domluva ohledně výhry)."
                    )

                await user.send(dm_text)
            except discord.Forbidden:
                pass

    # ---------- SLASH COMMANDS ----------

    @app_commands.command(
        name="setupgiveaway",
        description="Nastaví tento kanál jako roomku pro giveaway (admin).",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setupgiveaway_cmd(self, interaction: discord.Interaction):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Tento příkaz lze použít pouze v textovém kanálu.",
                ephemeral=True,
            )
            return

        set_setting("giveaway_channel_id", str(channel.id))
        await interaction.response.send_message(
            f"Tento kanál byl nastaven jako giveaway roomka: {channel.mention}",
            ephemeral=True,
        )

    @app_commands.command(
        name="start_giveaway",
        description="Spustí giveaway typu coin, pet nebo screen v nastavené roomce.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        typ="Typ giveaway (coin, pet nebo screen)",
        amount="Počet coinů (pouze pro typ coin)",
        pet_name="Název peta (pouze pro typ pet)",
        click_value="Click hodnota peta jako text (pouze pro typ pet)",
        image="Screenshot / obrázek (volitelné u coin/pet, doporučené u screen)",
        screen_winners="Počet výherců pro screen giveaway (min 1, max 10)",
        duration_minutes="Za kolik minut se má giveaway automaticky ukončit (prázdné = default z configu)",
    )
    async def start_giveaway_cmd(
        self,
        interaction: discord.Interaction,
        typ: GiveawayType,
        amount: Optional[app_commands.Range[int, 1, 10_000_000]] = None,
        pet_name: Optional[str] = None,
        click_value: Optional[str] = None,
        image: Optional[discord.Attachment] = None,
        screen_winners: Optional[app_commands.Range[int, 1, 10]] = None,
        duration_minutes: Optional[app_commands.Range[int, 1, 1440]] = None,
    ):
        channel_id_str = get_setting("giveaway_channel_id")
        if not channel_id_str:
            await interaction.response.send_message(
                "Nejprve nastav giveaway roomku příkazem `/setupgiveaway`.",
                ephemeral=True,
            )
            return

        try:
            channel_id = int(channel_id_str)
        except ValueError:
            await interaction.response.send_message(
                "Uložená giveaway roomka má neplatné ID.",
                ephemeral=True,
            )
            return

        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Giveaway roomka není textový kanál nebo se nenašla.",
                ephemeral=True,
            )
            return

        image_url: Optional[str] = image.url if image is not None else None
        duration = int(duration_minutes) if duration_minutes is not None else DEFAULT_GIVEAWAY_DURATION_MINUTES

        # ---------------------- COIN ----------------------
        if typ == GiveawayType.COIN:
            if amount is None:
                await interaction.response.send_message(
                    "Pro typ `coin` je povinný parametr `amount`.",
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                title="🎁 Coin giveaway",
                description=(
                    f"Typ: **coins**\n"
                    f"Celkem: **{amount}** coinů\n\n"
                    "Klikni na tlačítko níže a připoj se.\n"
                    "Po ukončení budou coiny **náhodně rozděleny** mezi až 3 výherce.\n"
                    f"Giveaway se automaticky ukončí za {duration} minut."
                ),
                color=0xFFD700,
            )

            state: Dict[str, Any] = {
                "type": GiveawayType.COIN,
                "amount": int(amount),
                "participants": set(),
                "ended": False,
                "channel_id": channel.id,
                "host_id": interaction.user.id,
                "image_url": image_url,
                "duration": duration,
            }

        # ---------------------- PET -----------------------
        elif typ == GiveawayType.PET:
            if not pet_name or not click_value:
                await interaction.response.send_message(
                    "Pro typ `pet` jsou povinné parametry `pet_name` i `click_value`.",
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                title="🎁 Pet giveaway",
                description=(
                    f"Pet: **{pet_name}**\n"
                    f"Click hodnota: `{click_value}`\n\n"
                    "Klikni na tlačítko níže a připoj se.\n"
                    "Po ukončení bude **náhodně vylosován jeden výherce**.\n"
                    f"Giveaway se automaticky ukončí za {duration} minut."
                ),
                color=0xFF69B4,
            )

            state = {
                "type": GiveawayType.PET,
                "pet_name": pet_name,
                "click_value": click_value,
                "participants": set(),
                "ended": False,
                "channel_id": channel.id,
                "host_id": interaction.user.id,
                "image_url": image_url,
                "duration": duration,
            }

        # ---------------------- SCREEN --------------------
        else:
            winners_count = int(screen_winners) if screen_winners is not None else 3

            embed = discord.Embed(
                title="🎁 Screen giveaway",
                description=(
                    "Giveaway podle screenu / obrázku níže.\n\n"
                    "Klikni na tlačítko níže a připoj se.\n"
                    f"Po ukončení budou **náhodně vylosováni až {winners_count} výherci**.\n"
                    f"Giveaway se automaticky ukončí za {duration} minut."
                ),
                color=0x00BFFF,
            )

            state = {
                "type": GiveawayType.SCREEN,
                "participants": set(),
                "ended": False,
                "channel_id": channel.id,
                "host_id": interaction.user.id,
                "image_url": image_url,
                "winners_count": winners_count,
                "duration": duration,
            }

        if image_url:
            embed.set_image(url=image_url)

        view = GiveawayView(self)

        content = ""
        if GIVEAWAY_PING_ROLE_ID:
            content = f"<@&{GIVEAWAY_PING_ROLE_ID}>"

        msg = await channel.send(content=content, embed=embed, view=view)

        self.active_giveaways[msg.id] = state

        # auto-end
        self.bot.loop.create_task(self.schedule_giveaway_auto_end(msg.id, duration))

        await interaction.response.send_message(
            f"Giveaway spuštěna v {channel.mention} a automaticky se ukončí za {duration} minut.",
            ephemeral=True,
        )


class GiveawayView(discord.ui.View):
    def __init__(self, cog: GiveawayCog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Připojit se do giveaway",
        style=discord.ButtonStyle.success,
        custom_id="giveaway_join",
    )
    async def join_giveaway(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        message = interaction.message
        if message is None:
            await interaction.response.send_message(
                "Nelze načíst informaci o giveaway.",
                ephemeral=True,
            )
            return

        state = self.cog.active_giveaways.get(message.id)
        if not state or state.get("ended"):
            await interaction.response.send_message(
                "Tato giveaway už není aktivní.",
                ephemeral=True,
            )
            return

        user_id = interaction.user.id
        participants: set[int] = state.setdefault("participants", set())

        if user_id in participants:
            await interaction.response.send_message(
                "Už jsi v této giveaway přihlášen.",
                ephemeral=True,
            )
            return

        participants.add(user_id)

        embed = message.embeds[0] if message.embeds else discord.Embed(color=0xFFD700)
        embed = embed.copy()
        embed.set_footer(text=f"Počet účastníků: {len(participants)}")

        await message.edit(embed=embed, view=self)
        await interaction.response.send_message(
            "Přihlásil ses do giveaway.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Ukončit giveaway",
        style=discord.ButtonStyle.danger,
        custom_id="giveaway_end",
    )
    async def end_giveaway(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Tuto giveaway může ukončit jen administrátor.",
                ephemeral=True,
            )
            return

        message = interaction.message
        if message is None:
            await interaction.response.send_message(
                "Nelze načíst informaci o giveaway.",
                ephemeral=True,
            )
            return

        state = self.cog.active_giveaways.get(message.id)
        if not state or state.get("ended"):
            await interaction.response.send_message(
                "Tato giveaway už není aktivní.",
                ephemeral=True,
            )
            return

        participants: set[int] = state.get("participants", set())
        if not participants:
            await interaction.response.send_message(
                "Nikdo se nepřihlásil, giveaway nejde ukončit.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        await self.cog.finalize_giveaway(message, state, self)
        await interaction.followup.send(
            "Giveaway byla ukončena, výherci jsou zobrazeni v embedu.",
            ephemeral=False,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(GiveawayCog(bot))
