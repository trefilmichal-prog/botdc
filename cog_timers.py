from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import List, Tuple, Dict

import discord
from discord.ext import commands
from discord.ext import tasks
from discord import app_commands

from config import SETUP_MANAGER_ROLE_ID
from cog_discord_writer import get_writer
from db import (
    get_all_timers,
    create_or_update_timer,
    delete_timer,
    get_all_active_timers,
    upsert_active_timer,
    delete_active_timer,
    delete_active_timers_for_name,
    set_setting,
    get_setting,
)


class TimersCog(commands.Cog, name="TimersCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.running_timers: Dict[tuple[int, str], asyncio.Task[None]] = {}
        self._register_persistent_view()
        self.bot.loop.create_task(self.resume_timers())

    async def resume_timers(self):
        await self.bot.wait_until_ready()
        rows = get_all_active_timers()
        now = datetime.utcnow()
        for user_id, name, minutes, end_at_str in rows:
            try:
                end_at = datetime.strptime(end_at_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                delete_active_timer(user_id, name)
                continue
            key = (user_id, name)
            if key in self.running_timers and not self.running_timers[key].done():
                continue
            seconds = (end_at - now).total_seconds()
            if seconds <= 0:
                user = self.bot.get_user(user_id)
                if user:
                    try:
                        await user.send(
                            f"Tvůj časovač **{name}** ({minutes} min) doběhl během downtimu bota."
                        )
                    except discord.Forbidden:
                        pass
                delete_active_timer(user_id, name)
                continue
            task = self.bot.loop.create_task(
                self.run_user_timer(user_id, name, minutes, end_at, key)
            )
            self.running_timers[key] = task

    async def run_user_timer(
        self,
        user_id: int,
        timer_name: str,
        duration_minutes: int,
        end_at: datetime,
        key: tuple[int, str],
    ):
        try:
            now = datetime.utcnow()
            seconds = (end_at - now).total_seconds()
            if seconds < 0:
                seconds = 0
            await asyncio.sleep(seconds)
            user = self.bot.get_user(user_id)
            if user:
                try:
                    await user.send(
                        f"Tvůj časovač **{timer_name}** (**{duration_minutes} min**) právě skončil."
                    )
                except discord.Forbidden:
                    pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Chyba v run_user_timer: {e}")
        finally:
            self.running_timers.pop(key, None)
            delete_active_timer(user_id, timer_name)

    def _register_persistent_view(self):
        timers = get_all_timers()
        if not timers:
            return

        view = build_timers_view(self, timers)
        self.bot.add_view(view)

    async def update_timers_panel(self):
        channel_id_str = get_setting("timers_channel_id")
        message_id_str = get_setting("timers_message_id")
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

        timers = get_all_timers()
        view = build_timers_view(self, timers)
        writer = get_writer(self.bot)
        await writer.edit_message(msg, content="", embeds=[], view=view)

    # ---------- SLASH ----------

    @app_commands.command(
        name="setuptimers",
        description="Vloží do této místnosti panel s tlačítky časovačů (admin).",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.checks.has_role(SETUP_MANAGER_ROLE_ID)
    async def setuptimers_cmd(self, interaction: discord.Interaction):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Tento příkaz lze použít pouze v textovém kanálu.",
                ephemeral=True,
            )
            return

        timers = get_all_timers()
        view = build_timers_view(self, timers)
        msg = await channel.send(content="", view=view)

        set_setting("timers_channel_id", str(channel.id))
        set_setting("timers_message_id", str(msg.id))

        await interaction.response.send_message(
            "Panel časovačů vytvořen v tomto kanálu.",
            ephemeral=True,
        )

    @app_commands.command(
        name="settimer",
        description="Přidá nebo upraví definici časovače.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        name="Název časovače",
        minutes="Délka v minutách",
    )
    async def settimer_cmd(
        self,
        interaction: discord.Interaction,
        name: str,
        minutes: app_commands.Range[int, 1, 10_000],
    ):
        create_or_update_timer(name, minutes)
        await interaction.response.send_message(
            f"Časovač **{name}** nastaven na **{minutes}** minut.",
            ephemeral=True,
        )
        await self.update_timers_panel()

    @app_commands.command(
        name="removetimer",
        description="Odstraní definici časovače podle názvu.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(name="Název časovače")
    async def removetimer_cmd(
        self,
        interaction: discord.Interaction,
        name: str,
    ):
        ok = delete_timer(name)
        if not ok:
            await interaction.response.send_message(
                f"Časovač **{name}** nebyl nalezen.",
                ephemeral=True,
            )
            return

        keys_to_cancel = [k for k in self.running_timers.keys() if k[1] == name]
        for key in keys_to_cancel:
            task = self.running_timers.get(key)
            if task and not task.done():
                task.cancel()
            self.running_timers.pop(key, None)

        delete_active_timers_for_name(name)

        await interaction.response.send_message(
            f"Časovač **{name}** byl odstraněn (včetně běžících instancí).",
            ephemeral=True,
        )
        await self.update_timers_panel()


class TimerButton(discord.ui.Button):
    def __init__(self, cog: TimersCog, timer_id: int, name: str, minutes: int):
        super().__init__(
            label=name,
            style=discord.ButtonStyle.secondary,
            custom_id=f"timer_{timer_id}",
        )
        self.cog = cog
        self.timer_id = timer_id
        self.timer_name = name
        self.minutes = minutes

    async def callback(self, interaction: discord.Interaction):
        user = interaction.user
        key = (user.id, self.timer_name)
        if key in self.cog.running_timers and not self.cog.running_timers[key].done():
            await interaction.response.send_message(
                f"Časovač **{self.timer_name}** ti už běží.",
                ephemeral=True,
            )
            return

        end_at = datetime.utcnow() + timedelta(minutes=self.minutes)
        upsert_active_timer(user.id, self.timer_name, self.minutes, end_at)

        await interaction.response.send_message(
            f"Časovač **{self.timer_name}** odstartoval na **{self.minutes}** minut.",
            ephemeral=True,
        )

        task = self.cog.bot.loop.create_task(
            self.cog.run_user_timer(user.id, self.timer_name, self.minutes, end_at, key)
        )
        self.cog.running_timers[key] = task


def build_timers_view(
    cog: TimersCog, timers: List[Tuple[int, str, int]]
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    info_lines = [
        "## Panel časovačů",
        (
            "Stiskni tlačítko pro časovač, který chceš spustit.\n"
            "Každý hráč má vlastní časovače – běží odděleně.\n"
            "Stejný časovač ti nemůže běžet dvakrát současně."
        ),
    ]

    if not timers:
        info_lines.append("### Žádné časovače")
        info_lines.append("Zatím nejsou definované žádné časovače. Použij `/settimer`.")
    else:
        desc = "\n".join(f"- **{n}** – {m} min" for _id, n, m in timers)
        info_lines.append("### Dostupné časovače")
        info_lines.append(desc)

    view.add_item(
        discord.ui.Container(
            *(discord.ui.TextDisplay(content=line) for line in info_lines)
        )
    )

    buttons = [
        TimerButton(cog, tid, name, minutes) for tid, name, minutes in timers[:25]
    ]
    for idx in range(0, len(buttons), 5):
        row = discord.ui.ActionRow(*buttons[idx : idx + 5])
        view.add_item(row)
    return view
