from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, Any, Optional, List

import discord
from discord.ext import commands
from discord import app_commands

from config import (
    GIVEAWAY_PING_ROLE_ID,
    DEFAULT_GIVEAWAY_DURATION_MINUTES,
    SETUP_MANAGER_ROLE_ID,
)
from db import (
    delete_giveaway_state,
    get_setting,
    get_active_giveaway,
    load_active_giveaways,
    save_giveaway_state,
    set_setting,
)


class GiveawayType(str, Enum):
    COIN = "coin"
    PET = "pet"
    SCREEN = "screen"  # screen giveaway – X výherců, bez pevné hodnoty


class GiveawayCog(commands.Cog, name="GiveawayCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # message_id -> stav giveaway
        self.active_giveaways: Dict[int, Dict[str, Any]] = {}

        self._restored = False

        # persistentní view pro giveaway tlačítka
        self.bot.add_view(GiveawayView(self))

    async def cog_load(self):
        await self.restore_active_giveaways()

    @commands.Cog.listener()
    async def on_ready(self):
        await self.restore_active_giveaways()

    # ---------- INTERNÍ HELPERY ----------

    async def _get_text_channel(self, channel_id: Optional[int]) -> Optional[discord.TextChannel]:
        if channel_id is None:
            return None

        channel = self.bot.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel

        try:
            fetched_channel = await self.bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

        return fetched_channel if isinstance(fetched_channel, discord.TextChannel) else None

    async def restore_active_giveaways(self):
        if self._restored:
            return

        self._restored = True
        giveaways = load_active_giveaways()

        for message_id, state in giveaways:
            channel_id = state.get("channel_id")
            if channel_id is None:
                delete_giveaway_state(message_id)
                continue

            try:
                state["type"] = GiveawayType(state["type"])
            except Exception:
                delete_giveaway_state(message_id)
                continue

            channel = await self._get_text_channel(channel_id)
            if channel is None:
                delete_giveaway_state(message_id)
                continue

            try:
                message = await channel.fetch_message(message_id)
            except (discord.NotFound, discord.Forbidden):
                delete_giveaway_state(message_id)
                continue

            self.active_giveaways[message_id] = state
            view = GiveawayView(self)

            participants: set[int] = state.get("participants", set())
            if message.embeds:
                embed = message.embeds[0].copy()
                embed.set_footer(text=f"Počet účastníků: {len(participants)}")
                await message.edit(embed=embed, view=view)
            else:
                await message.edit(view=view)

            self.bot.loop.create_task(self.schedule_giveaway_auto_end(message_id))

    async def restore_single_giveaway(self, message: Optional[discord.Message]):
        if message is None:
            return None

        if message.id in self.active_giveaways:
            return self.active_giveaways[message.id]

        state = get_active_giveaway(message.id)
        if state is None:
            return None

        try:
            state["type"] = GiveawayType(state["type"])
        except Exception:
            delete_giveaway_state(message.id)
            return None

        channel = message.channel
        if not isinstance(channel, discord.TextChannel):
            delete_giveaway_state(message.id)
            return None

        self.active_giveaways[message.id] = state
        view = GiveawayView(self)

        participants: set[int] = state.get("participants", set())
        if message.embeds:
            embed = message.embeds[0].copy()
            embed.set_footer(text=f"Počet účastníků: {len(participants)}")
            await message.edit(embed=embed, view=view)
        else:
            await message.edit(view=view)

        self.bot.loop.create_task(self.schedule_giveaway_auto_end(message.id))
        return state

    async def schedule_giveaway_auto_end(self, message_id: int):
        state = self.active_giveaways.get(message_id)
        if not state:
            return

        end_at: Optional[datetime] = state.get("end_at")
        if end_at is None:
            return

        delay_seconds = (end_at - datetime.utcnow()).total_seconds()
        if delay_seconds > 0:
            try:
                await asyncio.sleep(delay_seconds)
            except asyncio.CancelledError:
                return

        state = self.active_giveaways.get(message_id)
        if not state or state.get("ended"):
            return

        channel_id = state.get("channel_id")
        channel = await self._get_text_channel(channel_id)
        if channel is None:
            delete_giveaway_state(message_id)
            self.active_giveaways.pop(message_id, None)
            return

        try:
            message = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            delete_giveaway_state(message_id)
            self.active_giveaways.pop(message_id, None)
            return

        view = GiveawayView(self)
        await self.finalize_giveaway(message, state, view)

        await channel.send(
            f"Giveaway byla **automaticky ukončena** po {state.get('duration')} minutách, "
            f"výherci jsou zobrazeni v embedu."
        )

    def _create_giveaway_embed(
        self,
        *,
        title: str,
        color: int,
        intro_lines: list[str],
        end_at: datetime,
        host: discord.abc.User,
        extra_fields: Optional[list[tuple[str, str]]] = None,
        footer_note: str = "Počet účastníků: 0",
        block_admins: bool = False,
    ) -> discord.Embed:
        embed = discord.Embed(title=title, color=color)

        end_ts = int(end_at.timestamp())
        description_lines = intro_lines + ["✅ Klikni na tlačítko níže a připoj se."]
        embed.description = "\n".join(description_lines)

        embed.add_field(name="Pořádá", value=host.mention, inline=True)
        embed.add_field(
            name="Končí",
            value=f"<t:{end_ts}:R> (<t:{end_ts}:f>)",
            inline=True,
        )

        if block_admins:
            embed.add_field(
                name="Omezení",
                value="Administrátoři se do giveaway nemohou přihlásit.",
                inline=False,
            )

        if extra_fields:
            for name, value in extra_fields:
                embed.add_field(name=name, value=value, inline=False)

        if footer_note:
            embed.set_footer(text=footer_note)

        return embed

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
            state["ended"] = True
            delete_giveaway_state(message.id)
            self.active_giveaways.pop(message.id, None)
            return

        state["ended"] = True

        embed = message.embeds[0] if message.embeds else discord.Embed(color=0xFFD700)
        embed = embed.copy()
        embed.color = 0xFFA500

        guild = message.guild
        guild_name = guild.name if guild else "serveru"
        host_id = state.get("host_id")
        host_mention = f"<@{host_id}>" if host_id else "organizátorem giveaway"

        eligible_participants: List[int] = []
        for uid in participants:
            member = guild.get_member(uid) if guild else None
            user = member if member is not None else self.bot.get_user(uid)

            if user is None:
                continue

            if state.get("block_admins") and isinstance(user, discord.Member):
                if user.guild_permissions.administrator:
                    continue

            eligible_participants.append(uid)

        if not eligible_participants:
            embed.title = "🎁 Giveaway ukončena"
            embed.description = (
                "Nebyl nalezen žádný platný účastník pro losování. Giveaway končí bez výherce."
            )
            embed.color = 0x808080
            embed.set_footer(text="Žádní platní účastníci")

            for child in view.children:
                child.disabled = True

            await message.edit(embed=embed, view=view)

            delete_giveaway_state(message.id)
            self.active_giveaways.pop(message.id, None)
            return

        participants_list = list(eligible_participants)

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

        delete_giveaway_state(message.id)
        self.active_giveaways.pop(message.id, None)

    # ---------- SLASH COMMANDS ----------

    @app_commands.command(
        name="setupgiveaway",
        description="Nastaví tento kanál jako roomku pro giveaway (admin).",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.checks.has_role(SETUP_MANAGER_ROLE_ID)
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
        block_admins="Zabrání administrátorům přihlásit se do giveaway",
        mention_ping_role="Pingne při startu giveaway nastavenou roli (true/false)",
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
        block_admins: bool = False,
        mention_ping_role: bool = True,
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
        end_at = datetime.utcnow() + timedelta(minutes=duration)

        # ---------------------- COIN ----------------------
        if typ == GiveawayType.COIN:
            if amount is None:
                await interaction.response.send_message(
                    "Pro typ `coin` je povinný parametr `amount`.",
                    ephemeral=True,
                )
                return

            embed = self._create_giveaway_embed(
                title="🎁 Coin giveaway",
                color=0xFFD700,
                intro_lines=[
                    f"💰 **{amount} coinů** je připraveno pro výherce.",
                    "🥇 Coiny budou náhodně rozděleny až mezi 3 hráče.",
                    f"⏳ Giveaway končí za {duration} minut.",
                ],
                extra_fields=[
                    (
                        "Jak se losuje",
                        "Výhry se rozdělí rovnoměrně, prvním losovaným připadne případný zbytek coinů.",
                    )
                ],
                end_at=end_at,
                host=interaction.user,
                block_admins=block_admins,
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
                "end_at": end_at,
                "block_admins": block_admins,
            }

        # ---------------------- PET -----------------------
        elif typ == GiveawayType.PET:
            if not pet_name or not click_value:
                await interaction.response.send_message(
                    "Pro typ `pet` jsou povinné parametry `pet_name` i `click_value`.",
                    ephemeral=True,
                )
                return

            embed = self._create_giveaway_embed(
                title="🎁 Pet giveaway",
                color=0xFF69B4,
                intro_lines=[
                    f"🐾 Pet **{pet_name}** čeká na nového majitele!",
                    f"⚡ Click hodnota: `{click_value}`.",
                    "🥇 Náhodně bude vylosován 1 výherce.",
                    f"⏳ Giveaway končí za {duration} minut.",
                ],
                extra_fields=[
                    (
                        "Co získáš",
                        "Výherce obdrží peta včetně jeho click hodnoty. Pro vyzvednutí kontaktuj pořadatele.",
                    )
                ],
                end_at=end_at,
                host=interaction.user,
                block_admins=block_admins,
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
                "end_at": end_at,
                "block_admins": block_admins,
            }

        # ---------------------- SCREEN --------------------
        else:
            winners_count = int(screen_winners) if screen_winners is not None else 3

            embed = self._create_giveaway_embed(
                title="🎁 Screen giveaway",
                color=0x00BFFF,
                intro_lines=[
                    "Giveaway podle screenu / obrázku níže.",
                    "📸 Připoj se, pokud chceš být v losování.",
                    f"🥇 Losuje se až {winners_count} výherců.",
                    f"⏳ Giveaway končí za {duration} minut.",
                ],
                extra_fields=[
                    (
                        "Pravidla",
                        "Výherci budou vybráni náhodně, detaily odměn najdeš na přiloženém obrázku.",
                    )
                ],
                end_at=end_at,
                host=interaction.user,
                block_admins=block_admins,
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
                "end_at": end_at,
                "block_admins": block_admins,
            }

        if image_url:
            embed.set_image(url=image_url)

        view = GiveawayView(self)

        content = ""
        if GIVEAWAY_PING_ROLE_ID and mention_ping_role:
            content = f"<@&{GIVEAWAY_PING_ROLE_ID}>"

        msg = await channel.send(content=content, embed=embed, view=view)

        self.active_giveaways[msg.id] = state

        save_giveaway_state(msg.id, state)

        # auto-end
        self.bot.loop.create_task(self.schedule_giveaway_auto_end(msg.id))

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
        if state is None:
            state = await self.cog.restore_single_giveaway(message)
        if not state or state.get("ended"):
            await interaction.response.send_message(
                "Tato giveaway už není aktivní.",
                ephemeral=True,
            )
            return

        user_id = interaction.user.id
        participants: set[int] = state.setdefault("participants", set())

        if state.get("block_admins") and interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Administrátoři se nemohou přihlásit do této giveaway.",
                ephemeral=True,
            )
            return

        if user_id in participants:
            await interaction.response.send_message(
                "Už jsi v této giveaway přihlášen.",
                ephemeral=True,
            )
            return

        participants.add(user_id)

        save_giveaway_state(message.id, state)

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
        if state is None:
            state = await self.cog.restore_single_giveaway(message)
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
