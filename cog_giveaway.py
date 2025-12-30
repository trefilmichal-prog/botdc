from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    DEFAULT_GIVEAWAY_DURATION_MINUTES,
    GIVEAWAY_PING_ROLE_ID,
    SETUP_MANAGER_ROLE_ID,
)
from db import (
    delete_giveaway_state,
    get_active_giveaway,
    get_setting,
    load_active_giveaways,
    save_giveaway_state,
    set_setting,
)


class GiveawayType(str, Enum):
    COIN = "coin"
    PET = "pet"
    SCREEN = "screen"
    AUCTION = "auction"


def _format_timestamp(dt: datetime) -> str:
    dt_utc = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return f"<t:{int(dt_utc.timestamp())}:R> (<t:{int(dt_utc.timestamp())}:f>)"


def _format_participants(participants: set[int]) -> str:
    count = len(participants)
    return f"👥 Účastníků: **{count}**"


def _base_intro(state: Dict[str, Any]) -> list[str]:
    intro: list[str] = [
        f"🎯 Typ giveaway: **{state['type'].value}**",
        f"👑 Pořádá: <@{state['host_id']}>",
        f"⏳ Končí: {_format_timestamp(state['end_at'])}",
    ]

    if state.get("block_admins"):
        intro.append("🚫 Administrátoři se nemohou přihlásit.")

    return intro


def _format_giveaway_content(state: Dict[str, Any]) -> str:
    intro = _base_intro(state)

    if state["type"] == GiveawayType.COIN:
        amount: int = state["amount"]
        intro.extend(
            [
                f"💰 Celkem coinů: **{amount}**",
                "🥇 Coiny se rozdělí mezi až 3 hráče.",
                _format_participants(state.get("participants", set())),
            ]
        )
    elif state["type"] == GiveawayType.PET:
        pet_name: str = state["pet_name"]
        click_value: str = state["click_value"]
        intro.extend(
            [
                f"🐾 Pet: **{pet_name}**",
                f"⚡ Hodnota: `{click_value}`",
                _format_participants(state.get("participants", set())),
            ]
        )
    elif state["type"] == GiveawayType.AUCTION:
        auction_item: str = state.get("auction_item", "neznámý předmět")
        starting_bid = int(state.get("starting_bid") or 0)
        bids: Dict[int, int] = state.get("bids", {})
        highest_bid = max(bids.values(), default=starting_bid)
        highest_bidders = [uid for uid, bid in bids.items() if bid == highest_bid]
        leader = f"<@{highest_bidders[0]}>" if highest_bidders else "zatím nikdo"
        intro.extend(
            [
                f"🏷️ Aukce o: **{auction_item}**",
                f"💸 Vyvolávací cena: **{starting_bid}** coinů",
                f"📈 Aktuální nabídka: **{highest_bid}** coinů ({leader})",
                _format_participants(state.get("participants", set())),
            ]
        )
    else:
        winners_count: int = state.get("winners_count", 3)
        intro.extend(
            [
                "📸 Giveaway podle přiloženého obrázku.",
                f"🥇 Losuje se až **{winners_count}** výherců.",
                _format_participants(state.get("participants", set())),
            ]
        )

    image_url = state.get("image_url")
    if image_url:
        intro.append(f"🖼️ Obrázek: {image_url}")

    return "\n".join(intro)


def _format_result_content(state: Dict[str, Any], winners: list[int], extra: str) -> str:
    base = _base_intro(state)
    base.append(extra)
    if winners:
        base.append("🎉 Výherci:")
        base.extend([f"• <@{uid}>" for uid in winners])
    else:
        base.append("⚠️ Nebyl nalezen žádný platný výherce.")

    return "\n".join(base)


class GiveawayCog(commands.Cog, name="GiveawayCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_giveaways: Dict[int, Dict[str, Any]] = {}
        self._restored = False

    @staticmethod
    def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    async def cog_load(self):
        await self.restore_active_giveaways()

    @commands.Cog.listener()
    async def on_ready(self):
        await self.restore_active_giveaways()

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

            state["end_at"] = self._ensure_utc(state.get("end_at"))

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
            view = GiveawayView(self, state)
            self.bot.add_view(view, message_id=message_id)
            try:
                await message.edit(view=view)
            except discord.HTTPException as exc:
                if exc.code == 50035 and "content" in (exc.text or ""):
                    await self._recreate_giveaway_message(channel, state, message_id, message)
                    continue
                delete_giveaway_state(message_id)
                self.active_giveaways.pop(message_id, None)
                continue

            self.bot.loop.create_task(self.schedule_giveaway_auto_end(message_id))

    async def restore_single_giveaway(
        self, message: Optional[discord.Message]
    ) -> tuple[Optional[Dict[str, Any]], Optional[discord.Message]]:
        if message is None:
            return None, None

        if message.id in self.active_giveaways:
            return self.active_giveaways[message.id], message

        state = get_active_giveaway(message.id)
        if state is None:
            return None, None

        try:
            state["type"] = GiveawayType(state["type"])
        except Exception:
            delete_giveaway_state(message.id)
            return None, None

        state["end_at"] = self._ensure_utc(state.get("end_at"))

        channel = message.channel
        if not isinstance(channel, discord.TextChannel):
            delete_giveaway_state(message.id)
            return None, None

        self.active_giveaways[message.id] = state
        view = GiveawayView(self, state)
        try:
            self.bot.add_view(view, message_id=message.id)
            await message.edit(view=view)
            self.bot.loop.create_task(self.schedule_giveaway_auto_end(message.id))
            return state, message
        except discord.HTTPException as exc:
            if exc.code == 50035 and "content" in (exc.text or ""):
                new_message = await self._recreate_giveaway_message(
                    channel, state, message.id, message
                )
                return state if new_message else None, new_message

            delete_giveaway_state(message.id)
            self.active_giveaways.pop(message.id, None)
            return None, None

    async def _recreate_giveaway_message(
        self,
        channel: discord.TextChannel,
        state: Dict[str, Any],
        old_message_id: int,
        old_message: Optional[discord.Message] = None,
    ) -> Optional[discord.Message]:
        view = GiveawayView(self, state)

        try:
            new_message = await channel.send(view=view)
        except discord.HTTPException:
            return None

        try:
            if old_message is not None:
                await old_message.delete()
        except (discord.HTTPException, discord.Forbidden, discord.NotFound):
            pass

        delete_giveaway_state(old_message_id)
        self.active_giveaways.pop(old_message_id, None)

        self.active_giveaways[new_message.id] = state
        save_giveaway_state(new_message.id, state)
        self.bot.loop.create_task(self.schedule_giveaway_auto_end(new_message.id))

        return new_message

    async def schedule_giveaway_auto_end(self, message_id: int):
        while True:
            state = self.active_giveaways.get(message_id)
            if not state:
                return

            end_at: Optional[datetime] = state.get("end_at")
            if end_at is None:
                return

            end_at_utc = self._ensure_utc(end_at)
            delay_seconds = (end_at_utc - datetime.now(timezone.utc)).total_seconds()
            if delay_seconds > 0:
                try:
                    await asyncio.sleep(delay_seconds)
                except asyncio.CancelledError:
                    return

            state = self.active_giveaways.get(message_id)
            if not state or state.get("ended"):
                return

            refreshed_end = self._ensure_utc(state.get("end_at"))
            if refreshed_end and refreshed_end > datetime.now(timezone.utc):
                continue

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

        await self.finalize_giveaway(message, state)

        await channel.send(
            f"Giveaway byla **automaticky ukončena** po {state.get('duration')} minutách, "
            "výherci jsou uvedeni v hlavním příspěvku."
        )
        return

    async def finalize_giveaway(
        self, message: discord.Message, state: Dict[str, Any]
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
            summary = _format_result_content(
                state,
                [],
                "Nebyl nalezen žádný platný účastník pro losování. Giveaway končí bez výherce.",
            )
            result_view = GiveawayView(
                self,
                state,
                status_text="Status: Ukončeno",
                summary_text=summary,
                ended=True,
            )
            await message.edit(view=result_view)

            delete_giveaway_state(message.id)
            self.active_giveaways.pop(message.id, None)
            return

        participants_list = list(eligible_participants)

        rolling_view = GiveawayView(
            self,
            state,
            status_text="Status: Losuji výherce...",
            summary_text=_format_giveaway_content(state) + "\n\n🎲 Losuji výherce...",
        )
        await message.edit(view=rolling_view)
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

            extra_message = (
                f"Celkem rozdáno: **{amount}** coinů mezi {winners_count} hráče.\n"
                + "\n".join(winners_lines)
            )
            summary = _format_result_content(state, winners_ids, extra_message)

        elif gtype == GiveawayType.PET:
            pet_name: str = state["pet_name"]
            click_value: str = state["click_value"]
            winner_id = random.choice(participants_list)
            winners_ids = [winner_id]

            extra_message = (
                f"Výherce peta **{pet_name}** (hodnota `{click_value}`) je <@{winner_id}>."
            )
            summary = _format_result_content(state, winners_ids, extra_message)

        elif gtype == GiveawayType.AUCTION:
            bids: Dict[int, int] = state.get("bids", {})
            eligible_bids = {uid: bid for uid, bid in bids.items() if uid in participants_list}
            if not eligible_bids:
                summary = _format_result_content(
                    state,
                    [],
                    "Nebyla zadána žádná platná nabídka, aukce končí bez výherce.",
                )
                result_view = GiveawayView(
                    self,
                    state,
                    status_text="Status: Ukončeno",
                    summary_text=summary,
                    ended=True,
                )
                await message.edit(view=result_view)
                delete_giveaway_state(message.id)
                self.active_giveaways.pop(message.id, None)
                return

            highest_bid = max(eligible_bids.values())
            top_bidders = [uid for uid, bid in eligible_bids.items() if bid == highest_bid]
            winner_id = random.choice(top_bidders)
            winners_ids = [winner_id]

            extra_message = (
                f"Výherce aukce je <@{winner_id}> s nabídkou **{highest_bid}** coinů."
            )
            summary = _format_result_content(state, winners_ids, extra_message)

        else:
            configured = int(state.get("winners_count", 3))
            winners_count = min(configured, len(participants_list))
            winners_ids = random.sample(participants_list, winners_count)
            extra_message = (
                f"Výherci z giveaway (nastaveno {configured} výherců, losováno {winners_count})."
            )
            summary = _format_result_content(state, winners_ids, extra_message)

        result_view = GiveawayView(
            self,
            state,
            status_text="Status: Ukončeno",
            summary_text=summary,
            ended=True,
        )

        await message.edit(view=result_view)

        for uid in winners_ids:
            user = self.bot.get_user(uid)
            if user is None and guild is not None:
                user = guild.get_member(uid)

            if user is None:
                continue

            try:
                if gtype == GiveawayType.COIN:
                    amount = state["amount"]
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
                    pet_name = state["pet_name"]
                    click_value = state["click_value"]
                    dm_text = (
                        f"Ahoj, gratuluji! Vyhrál jsi v **pet giveaway** na serveru **{guild_name}**.\n"
                        f"Dostáváš peta **{pet_name}** (click hodnota: `{click_value}`).\n"
                        f"Prosím, ozvi se {host_mention} na serveru (přezdívka / předání výhry)."
                    )

                elif gtype == GiveawayType.AUCTION:
                    auction_item = state.get("auction_item", "předmět")
                    bids: Dict[int, int] = state.get("bids", {})
                    winning_bid = bids.get(uid)
                    winning_text = (
                        f"Tvoje nabídka: **{winning_bid}** coinů.\n" if winning_bid else ""
                    )
                    dm_text = (
                        f"Ahoj, gratuluji! Vyhrál jsi v **auction giveaway** na serveru **{guild_name}**.\n"
                        f"Vyhráváš aukci o **{auction_item}**.\n"
                        f"{winning_text}"
                        f"Prosím, ozvi se {host_mention} na serveru (přezdívka / domluva ohledně výhry)."
                    )

                else:
                    dm_text = (
                        f"Ahoj, gratuluji! Vyhrál jsi v **screen giveaway** na serveru **{guild_name}**.\n"
                        "Odměny jsou vidět v obrázku v giveaway.\n"
                        f"Prosím, ozvi se {host_mention} na serveru (přezdívka / domluva ohledně výhry)."
                    )

                await user.send(dm_text)
            except discord.Forbidden:
                pass

        delete_giveaway_state(message.id)
        self.active_giveaways.pop(message.id, None)

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
        description="Spustí giveaway typu coin, pet, screen nebo auction v nastavené roomce.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(
        typ=[
            app_commands.Choice(name="coin", value=GiveawayType.COIN.value),
            app_commands.Choice(name="pet", value=GiveawayType.PET.value),
            app_commands.Choice(name="screen", value=GiveawayType.SCREEN.value),
            app_commands.Choice(name="auction", value=GiveawayType.AUCTION.value),
        ]
    )
    @app_commands.describe(
        typ="Typ giveaway (coin, pet, screen nebo auction)",
        amount="Počet coinů (pouze pro typ coin)",
        pet_name="Název peta (pouze pro typ pet)",
        click_value="Click hodnota peta jako text (pouze pro typ pet)",
        auction_item="Předmět aukce (pouze pro typ auction)",
        starting_bid="Vyvolávací cena aukce (pouze pro typ auction)",
        image="Screenshot / obrázek (volitelné u coin/pet, doporučené u screen)",
        screen_winners="Počet výherců pro screen giveaway (min 1, max 10)",
        duration_minutes="Za kolik minut se má giveaway automaticky ukončit (prázdné = default z configu)",
        block_admins="Zabrání administrátorům přihlásit se do giveaway",
        mention_ping_role="Pingne při startu giveaway nastavenou roli (true/false)",
    )
    async def start_giveaway_cmd(
        self,
        interaction: discord.Interaction,
        typ: str,
        amount: Optional[app_commands.Range[int, 1, 10_000_000]] = None,
        pet_name: Optional[str] = None,
        click_value: Optional[str] = None,
        auction_item: Optional[str] = None,
        starting_bid: Optional[app_commands.Range[int, 0, 10_000_000]] = None,
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
        end_at = datetime.now(timezone.utc) + timedelta(minutes=duration)

        state: Dict[str, Any]

        try:
            giveaway_type = GiveawayType(typ)
        except ValueError:
            await interaction.response.send_message(
                "Neplatný typ giveaway.",
                ephemeral=True,
            )
            return

        if giveaway_type == GiveawayType.COIN:
            if amount is None:
                await interaction.response.send_message(
                    "Pro typ `coin` je povinný parametr `amount`.",
                    ephemeral=True,
                )
                return

            state = {
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

        elif giveaway_type == GiveawayType.PET:
            if pet_name is None or click_value is None:
                await interaction.response.send_message(
                    "Pro typ `pet` jsou povinné parametry `pet_name` a `click_value`.",
                    ephemeral=True,
                )
                return

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

        elif giveaway_type == GiveawayType.AUCTION:
            if not auction_item:
                await interaction.response.send_message(
                    "Pro typ `auction` je povinný parametr `auction_item`.",
                    ephemeral=True,
                )
                return

            starting_bid_value = int(starting_bid) if starting_bid is not None else 0

            state = {
                "type": GiveawayType.AUCTION,
                "auction_item": auction_item,
                "starting_bid": starting_bid_value,
                "bids": {},
                "participants": set(),
                "ended": False,
                "channel_id": channel.id,
                "host_id": interaction.user.id,
                "image_url": image_url,
                "duration": duration,
                "end_at": end_at,
                "block_admins": block_admins,
            }

        else:
            winners_count = int(screen_winners) if screen_winners is not None else 3

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

        view = GiveawayView(self, state)

        if GIVEAWAY_PING_ROLE_ID and mention_ping_role:
            await channel.send(f"<@&{GIVEAWAY_PING_ROLE_ID}>")

        msg = await channel.send(view=view)

        self.active_giveaways[msg.id] = state

        save_giveaway_state(msg.id, state)
        self.bot.add_view(view, message_id=msg.id)

        self.bot.loop.create_task(self.schedule_giveaway_auto_end(msg.id))

        await interaction.response.send_message(
            f"Giveaway spuštěna v {channel.mention} a automaticky se ukončí za {duration} minut.",
            ephemeral=True,
        )


class GiveawayView(discord.ui.LayoutView):
    def __init__(
        self,
        cog: GiveawayCog,
        state: Dict[str, Any],
        *,
        status_text: str = "Status: Aktivní",
        summary_text: Optional[str] = None,
        ended: bool = False,
    ):
        super().__init__(timeout=None)
        self.cog = cog
        summary_value = summary_text or _format_giveaway_content(state)
        self.content_display = discord.ui.TextDisplay(summary_value)
        self.status_display = discord.ui.TextDisplay(status_text)

        summary_container = discord.ui.Container(
            discord.ui.TextDisplay("🎁 Giveaway"),
            self.content_display,
            self.status_display,
        )

        self.join_button = discord.ui.Button(
            label="Připojit se do giveaway",
            style=discord.ButtonStyle.success,
            custom_id="giveaway_join",
        )
        self.join_button.callback = self.join_giveaway

        self.end_button = discord.ui.Button(
            label="Ukončit giveaway",
            style=discord.ButtonStyle.danger,
            custom_id="giveaway_end",
        )
        self.end_button.callback = self.end_giveaway

        if ended:
            self.join_button.disabled = True
            self.end_button.disabled = True

        gtype = state.get("type")
        bid_select = None
        if gtype == GiveawayType.AUCTION:
            self.join_button.label = "Přihodit do aukce"
            self.join_button.custom_id = "giveaway_bid"
            self.join_button.callback = self.bid_in_auction
            bid_select = self._build_bid_select(state, ended)
            actions = discord.ui.ActionRow(self.join_button, self.end_button)
        else:
            actions = discord.ui.ActionRow(self.join_button, self.end_button)

        self.add_item(summary_container)
        self.add_item(discord.ui.Separator())
        self.add_item(actions)
        if bid_select is not None:
            self.add_item(discord.ui.ActionRow(bid_select))

    def update_summary(self, text: str):
        self.content_display.text = text

    def set_status(self, text: str):
        self.status_display.text = text

    def _build_bid_select(self, state: Dict[str, Any], ended: bool) -> discord.ui.Select:
        starting_bid = int(state.get("starting_bid") or 0)
        bids: Dict[int, int] = state.get("bids", {})
        current_highest = max(bids.values(), default=starting_bid)
        increments = [10, 50, 100, 250, 500]
        options = [
            discord.SelectOption(
                label=f"Přihodit {current_highest + inc} coinů",
                value=str(current_highest + inc),
            )
            for inc in increments
        ]
        bid_select = discord.ui.Select(
            placeholder="Vyber částku pro příhoz",
            options=options,
            custom_id="giveaway_bid_select",
            min_values=1,
            max_values=1,
            disabled=ended,
        )
        bid_select.callback = self.select_bid_amount
        return bid_select

    async def join_giveaway(self, interaction: discord.Interaction):
        message = interaction.message
        if message is None:
            await interaction.response.send_message(
                "Nelze načíst informaci o giveaway.",
                ephemeral=True,
            )
            return

        state = self.cog.active_giveaways.get(message.id)
        restored_message = message
        if state is None:
            state, restored_message = await self.cog.restore_single_giveaway(message)
        if restored_message is not None and restored_message.id != message.id:
            await interaction.response.send_message(
                f"Giveaway panel byl obnoven zde: {restored_message.jump_url}",
                ephemeral=True,
            )
            return

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

        save_giveaway_state(restored_message.id, state)

        new_view = GiveawayView(self.cog, state)
        self.cog.bot.add_view(new_view, message_id=restored_message.id)
        await restored_message.edit(view=new_view)
        await interaction.response.send_message(
            "Přihlásil ses do giveaway.",
            ephemeral=True,
        )

    async def bid_in_auction(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Pro příhoz vyber částku v menu pod tímto tlačítkem.",
            ephemeral=True,
        )

    async def select_bid_amount(self, interaction: discord.Interaction):
        message = interaction.message
        if message is None:
            await interaction.response.send_message(
                "Nelze načíst informaci o aukci.",
                ephemeral=True,
            )
            return

        state = self.cog.active_giveaways.get(message.id)
        restored_message = message
        if state is None:
            state, restored_message = await self.cog.restore_single_giveaway(message)
        if restored_message is not None and restored_message.id != message.id:
            await interaction.response.send_message(
                f"Aukční panel byl obnoven zde: {restored_message.jump_url}",
                ephemeral=True,
            )
            return

        if not state or state.get("ended"):
            await interaction.response.send_message(
                "Tato aukce už není aktivní.",
                ephemeral=True,
            )
            return

        if state.get("type") != GiveawayType.AUCTION:
            await interaction.response.send_message(
                "Toto není aukce.",
                ephemeral=True,
            )
            return

        if state.get("block_admins") and interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Administrátoři se nemohou přihlásit do této aukce.",
                ephemeral=True,
            )
            return

        try:
            bid_value = int(interaction.data["values"][0])
        except (KeyError, TypeError, ValueError):
            await interaction.response.send_message(
                "Nelze zpracovat nabídku.",
                ephemeral=True,
            )
            return

        starting_bid = int(state.get("starting_bid") or 0)
        bids: Dict[int, int] = state.setdefault("bids", {})
        current_highest = max(bids.values(), default=starting_bid)

        if bid_value <= current_highest:
            await interaction.response.send_message(
                f"Musíš přihodit více než aktuálních **{current_highest}** coinů.",
                ephemeral=True,
            )
            return

        user_id = interaction.user.id
        bids[user_id] = bid_value
        participants: set[int] = state.setdefault("participants", set())
        participants.add(user_id)

        end_at = state.get("end_at")
        end_at_utc = self.cog._ensure_utc(end_at)
        extended = False
        if end_at_utc is not None:
            remaining = (end_at_utc - datetime.now(timezone.utc)).total_seconds()
            if remaining < 120:
                state["end_at"] = end_at_utc + timedelta(minutes=1)
                extended = True

        save_giveaway_state(restored_message.id, state)

        new_view = GiveawayView(self.cog, state)
        self.cog.bot.add_view(new_view, message_id=restored_message.id)
        await restored_message.edit(view=new_view)

        extra = " Aukce byla prodloužena o 1 minutu." if extended else ""
        await interaction.response.send_message(
            f"Tvoje nabídka **{bid_value}** coinů byla zapsána.{extra}",
            ephemeral=True,
        )

    async def end_giveaway(self, interaction: discord.Interaction):
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
        restored_message = message
        if state is None:
            state, restored_message = await self.cog.restore_single_giveaway(message)
        if restored_message is not None and restored_message.id != message.id:
            await interaction.response.send_message(
                f"Giveaway panel byl obnoven zde: {restored_message.jump_url}",
                ephemeral=True,
            )
            return

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
        await self.cog.finalize_giveaway(message, state)
        await interaction.edit_original_response(
            "Giveaway byla ukončena, výherci jsou zobrazeni v příspěvku.",
        )
