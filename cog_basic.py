import discord
from discord import app_commands
from discord.ext import commands
from datetime import timedelta, datetime

from db import (
    get_latest_clan_application_by_user,
    mark_clan_application_deleted,
)
from i18n import get_interaction_locale, t


class BasicCommandsCog(commands.Cog, name="BasicCommands"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Zobrazí užitečné informace o Rebirth Champions.")
    async def help(self, interaction: discord.Interaction):
        locale = get_interaction_locale(interaction)
        embed = discord.Embed(
            title=t("help_title", locale),
            description=t("help_guide", locale),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
        locale = get_interaction_locale(interaction)
        if not self._can_moderate(interaction.user, user):
            await interaction.response.send_message(
                t("cannot_moderate", locale), ephemeral=True
            )
            return
        if not self._bot_can_moderate(interaction.guild, user):
            await interaction.response.send_message(
                t("bot_cannot_moderate", locale), ephemeral=True
            )
            return

        modal = KickReasonModal(self, target_member=user, locale=locale)
        await interaction.response.send_modal(modal)

    async def _remove_clan_ticket_for_member(
        self, guild: discord.Guild, member: discord.Member, reason: str, locale: discord.Locale
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
                return t("ticket_removed", locale, channel=channel_label)
            except discord.Forbidden:
                return t("ticket_remove_forbidden", locale, channel=channel_label)
            except discord.HTTPException:
                return t("ticket_remove_failed", locale, channel=channel_label)

        return t("ticket_mark_deleted", locale)

    @app_commands.command(name="ban", description="Zabanuje člena.")
    @app_commands.describe(user="Uživatel, který má být zabanován.", reason="Důvod banu.")
    @app_commands.default_permissions(ban_members=True)
    async def ban_member(
        self, interaction: discord.Interaction, user: discord.Member, reason: str | None = None
    ):
        locale = get_interaction_locale(interaction)
        if not self._can_moderate(interaction.user, user):
            await interaction.response.send_message(
                t("cannot_moderate", locale), ephemeral=True
            )
            return
        if not self._bot_can_moderate(interaction.guild, user):
            await interaction.response.send_message(
                t("bot_cannot_moderate", locale), ephemeral=True
            )
            return

        await interaction.guild.ban(user, reason=reason, delete_message_days=0)
        await interaction.response.send_message(
            t(
                "ban_success",
                locale,
                user=user.mention,
                reason=reason or t("reason_unknown", locale),
            ),
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
        locale = get_interaction_locale(interaction)
        if not self._can_moderate(interaction.user, user):
            await interaction.response.send_message(
                t("cannot_moderate", locale), ephemeral=True
            )
            return
        if not self._bot_can_moderate(interaction.guild, user):
            await interaction.response.send_message(
                t("bot_cannot_moderate", locale), ephemeral=True
            )
            return

        until = datetime.utcnow() + timedelta(minutes=duration_minutes)
        await user.timeout(until, reason=reason)
        await interaction.response.send_message(
            t(
                "mute_success",
                locale,
                user=user.mention,
                minutes=duration_minutes,
                reason=reason or t("reason_unknown", locale),
            ),
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
        locale = get_interaction_locale(interaction)
        if not self._can_moderate(interaction.user, user):
            await interaction.response.send_message(
                t("cannot_moderate", locale), ephemeral=True
            )
            return
        if not self._bot_can_moderate(interaction.guild, user):
            await interaction.response.send_message(
                t("bot_cannot_moderate", locale), ephemeral=True
            )
            return

        await user.edit(nick=nickname or None, reason="Změna přezdívky přes /setnick")
        if nickname:
            msg = t("nickname_set", locale, user=user.mention, nickname=nickname)
        else:
            msg = t("nickname_cleared", locale, user=user.mention)
        await interaction.response.send_message(msg, ephemeral=True)


class KickReasonModal(discord.ui.Modal):
    def __init__(
        self, cog: BasicCommandsCog, target_member: discord.Member, locale: discord.Locale
    ):
        super().__init__(title=t("kick_modal_title", locale), timeout=None)
        self.cog = cog
        self.target_member_id = target_member.id
        self.locale = locale

        self.reason = discord.ui.TextInput(
            label=t("kick_modal_label", locale),
            placeholder=t("kick_modal_placeholder", locale),
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=300,
        )

        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        locale = get_interaction_locale(interaction) if interaction else self.locale
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(t("guild_only", locale), ephemeral=True)
            return

        member = guild.get_member(self.target_member_id)
        if member is None:
            await interaction.response.send_message(t("user_missing", locale), ephemeral=True)
            return

        if not self.cog._can_moderate(interaction.user, member):
            await interaction.response.send_message(
                t("cannot_moderate", locale), ephemeral=True
            )
            return

        if not self.cog._bot_can_moderate(guild, member):
            await interaction.response.send_message(
                t("bot_cannot_moderate", locale), ephemeral=True
            )
            return

        reason = self.reason.value.strip() or t("reason_unknown", locale)
        await member.kick(reason=reason)

        ticket_info = await self.cog._remove_clan_ticket_for_member(guild, member, reason, locale)
        response = t("kick_success", locale, user=member.mention, reason=reason)
        if ticket_info:
            response = f"{response}\n{ticket_info}"

        await interaction.response.send_message(response, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(BasicCommandsCog(bot))
