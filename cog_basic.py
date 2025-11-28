import discord
from discord import app_commands
from discord.ext import commands
from datetime import timedelta, datetime

from db import (
    get_latest_clan_application_by_user,
    mark_clan_application_deleted,
)


class BasicCommandsCog(commands.Cog, name="BasicCommands"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def _can_moderate(actor: discord.Member, target: discord.Member) -> bool:
        if target == actor:
            return False
        if actor.guild is None:
            return False
        if actor.guild.owner_id == actor.id:
            return True
        return target.top_role < actor.top_role

    @staticmethod
    def _bot_can_moderate(guild: discord.Guild, target: discord.Member) -> bool:
        me = guild.me
        if me is None:
            return False
        if guild.owner_id == me.id:
            return True
        return target.top_role < me.top_role

    @app_commands.command(
        name="kick",
        description="Vyhodí člena ze serveru a odebere jeho ticket (pokud existuje).",
    )
    @app_commands.describe(user="Uživatel, který má být vyhozen.")
    @app_commands.default_permissions(kick_members=True)
    async def kick_member(self, interaction: discord.Interaction, user: discord.Member):
        if not self._can_moderate(interaction.user, user):
            await interaction.response.send_message(
                "Nemůžeš vyhodit uživatele s vyšší nebo stejnou rolí.", ephemeral=True
            )
            return
        if not self._bot_can_moderate(interaction.guild, user):
            await interaction.response.send_message(
                "Nemohu vyhodit uživatele kvůli hierarchii rolí.", ephemeral=True
            )
            return

        modal = KickReasonModal(self, target_member=user)
        await interaction.response.send_modal(modal)

    async def _remove_clan_ticket_for_member(
        self, guild: discord.Guild, member: discord.Member, reason: str
    ) -> str | None:
        latest_app = get_latest_clan_application_by_user(guild.id, member.id)
        if latest_app is None or latest_app.get("deleted"):
            return None

        channel = guild.get_channel(latest_app["channel_id"])
        channel_label = channel.mention if isinstance(channel, discord.TextChannel) else "ticket"

        mark_clan_application_deleted(latest_app["id"])

        if isinstance(channel, discord.TextChannel):
            try:
                await channel.delete(
                    reason=f"Kick uživatele {member} – odstranění ticketu (důvod: {reason})"
                )
                return f"Ticket {channel_label} byl smazán."
            except discord.Forbidden:
                return f"Ticket {channel_label} se nepodařilo smazat kvůli oprávněním."
            except discord.HTTPException:
                return f"Při mazání ticketu {channel_label} došlo k chybě."

        return "Původní ticket se nenašel, označuji ho jako smazaný."

    @app_commands.command(name="ban", description="Zabanuje člena.")
    @app_commands.describe(user="Uživatel, který má být zabanován.", reason="Důvod banu.")
    @app_commands.default_permissions(ban_members=True)
    async def ban_member(
        self, interaction: discord.Interaction, user: discord.Member, reason: str | None = None
    ):
        if not self._can_moderate(interaction.user, user):
            await interaction.response.send_message(
                "Nemůžeš zabanovat uživatele s vyšší nebo stejnou rolí.", ephemeral=True
            )
            return
        if not self._bot_can_moderate(interaction.guild, user):
            await interaction.response.send_message(
                "Nemohu zabanovat uživatele kvůli hierarchii rolí.", ephemeral=True
            )
            return

        await interaction.guild.ban(user, reason=reason, delete_message_days=0)
        await interaction.response.send_message(
            f"\N{HAMMER} {user.mention} byl/a zabanován/a. Důvod: {reason or 'neuveden'}.",
            ephemeral=True,
        )

    @app_commands.command(name="mute", description="Dočasně umlčí člena (timeout).")
    @app_commands.describe(
        user="Uživatel, který má být umlčen.",
        duration_minutes="Délka v minutách (1-10080).",
        reason="Důvod umlčení.",
    )
    @app_commands.default_permissions(moderate_members=True)
    async def mute_member(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        duration_minutes: app_commands.Range[int, 1, 10080],
        reason: str | None = None,
    ):
        if not self._can_moderate(interaction.user, user):
            await interaction.response.send_message(
                "Nemůžeš umlčet uživatele s vyšší nebo stejnou rolí.", ephemeral=True
            )
            return
        if not self._bot_can_moderate(interaction.guild, user):
            await interaction.response.send_message(
                "Nemohu umlčet uživatele kvůli hierarchii rolí.", ephemeral=True
            )
            return

        until = datetime.utcnow() + timedelta(minutes=duration_minutes)
        await user.timeout(until, reason=reason)
        await interaction.response.send_message(
            f"\N{SPEAKER WITH CANCELLATION STROKE} {user.mention} umlčen/a na {duration_minutes} minut."
            f" Důvod: {reason or 'neuveden'}.",
            ephemeral=True,
        )

    @app_commands.command(name="setnick", description="Nastaví nebo smaže přezdívku uživatele.")
    @app_commands.describe(
        user="Kterému uživateli upravit přezdívku.",
        nickname="Nová přezdívka (prázdné = smazat).",
    )
    @app_commands.default_permissions(manage_nicknames=True)
    async def set_nickname(
        self, interaction: discord.Interaction, user: discord.Member, nickname: str | None = None
    ):
        if not self._can_moderate(interaction.user, user):
            await interaction.response.send_message(
                "Nemůžeš měnit přezdívku uživatele s vyšší nebo stejnou rolí.", ephemeral=True
            )
            return
        if not self._bot_can_moderate(interaction.guild, user):
            await interaction.response.send_message(
                "Nemohu změnit přezdívku kvůli hierarchii rolí.", ephemeral=True
            )
            return

        await user.edit(nick=nickname or None, reason="Změna přezdívky přes /setnick")
        if nickname:
            msg = f"\N{MEMO} Přezdívka {user.mention} nastavena na '{nickname}'."
        else:
            msg = f"\N{MEMO} Přezdívka {user.mention} byla smazána."
        await interaction.response.send_message(msg, ephemeral=True)


class KickReasonModal(discord.ui.Modal, title="Důvod kicku"):
    def __init__(self, cog: BasicCommandsCog, target_member: discord.Member):
        super().__init__(timeout=None)
        self.cog = cog
        self.target_member_id = target_member.id

        self.reason = discord.ui.TextInput(
            label="Důvod kicku",
            placeholder="Napiš stručně, proč hráče kickuješ",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=300,
        )

        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Tento příkaz lze použít pouze na serveru.", ephemeral=True
            )
            return

        member = guild.get_member(self.target_member_id)
        if member is None:
            await interaction.response.send_message(
                "Uživatel už není na serveru.", ephemeral=True
            )
            return

        if not self.cog._can_moderate(interaction.user, member):
            await interaction.response.send_message(
                "Nemůžeš vyhodit uživatele s vyšší nebo stejnou rolí.", ephemeral=True
            )
            return

        if not self.cog._bot_can_moderate(guild, member):
            await interaction.response.send_message(
                "Nemohu vyhodit uživatele kvůli hierarchii rolí.", ephemeral=True
            )
            return

        reason = self.reason.value.strip() or "neuveden"
        await member.kick(reason=reason)

        ticket_info = await self.cog._remove_clan_ticket_for_member(guild, member, reason)
        response = (
            f"\N{WAVING HAND SIGN} {member.mention} byl/a vyhozen/a. Důvod: {reason}."
        )
        if ticket_info:
            response = f"{response}\n{ticket_info}"

        await interaction.response.send_message(response, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(BasicCommandsCog(bot))
