import discord
from discord import app_commands
from discord.ext import commands
from datetime import timedelta, datetime

from config import (
    WARN_ROLE_1_ID,
    WARN_ROLE_2_ID,
    WARN_ROLE_3_ID,
    CLAN_MEMBER_ROLE_EN_ID,
    CLAN_MEMBER_ROLE_ID,
    CLAN2_MEMBER_ROLE_ID,
    CLAN3_MEMBER_ROLE_ID,
)
from db import (
    get_latest_clan_application_by_user,
    list_clan_definitions,
    mark_clan_application_deleted,
)
from i18n import get_interaction_locale, t


class BasicCommandsCog(commands.Cog, name="BasicCommands"):
    admin = app_commands.Group(name="admin", description="Administrátorské příkazy.")

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

    @staticmethod
    def _can_assign_role(guild: discord.Guild, role: discord.Role | None) -> bool:
        if role is None:
            return False
        me = guild.me
        if me is None:
            return False
        return me.top_role > role

    def _get_warn_roles(self, guild: discord.Guild) -> tuple[discord.Role | None, ...]:
        return (
            guild.get_role(WARN_ROLE_1_ID),
            guild.get_role(WARN_ROLE_2_ID),
            guild.get_role(WARN_ROLE_3_ID),
        )

    @admin.command(
        name="kick",
        description="Odebere clan roli a odstraní ticket člena (pokud existuje).",
    )
    @app_commands.describe(
        user="Uživatel, který má být vyhozen.", reason="Důvod odebrání z klanu."
    )
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick_member(
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

        reason_text = (reason or "").strip() or t("reason_unknown", locale)
        role_info = await self._remove_clan_roles_for_member(user, reason_text, locale)
        ticket_info = await self._remove_clan_ticket_for_member(
            interaction.guild, user, reason_text, locale
        )

        response = t("kick_success", locale, user=user.mention, reason=reason_text)
        details = [info for info in (role_info, ticket_info) if info]
        if details:
            response = f"{response}\n" + "\n".join(details)

        await interaction.response.send_message(response, ephemeral=True)

    def _get_clan_role_ids(self, guild_id: int) -> set[int]:
        role_ids = {
            role_id
            for role_id in (
                CLAN_MEMBER_ROLE_ID,
                CLAN_MEMBER_ROLE_EN_ID,
                CLAN2_MEMBER_ROLE_ID,
                CLAN3_MEMBER_ROLE_ID,
            )
            if role_id
        }
        for entry in list_clan_definitions(guild_id):
            for key in ("accept_role_id", "accept_role_id_cz", "accept_role_id_en"):
                role_id = entry.get(key)
                if role_id:
                    role_ids.add(int(role_id))
        return role_ids

    async def _remove_clan_roles_for_member(
        self, member: discord.Member, reason: str, locale: discord.Locale
    ) -> str | None:
        guild = member.guild
        role_ids = self._get_clan_role_ids(guild.id)
        roles_to_remove = [role for role in member.roles if role.id in role_ids]
        if not roles_to_remove:
            return t("clan_member_not_found", locale)

        try:
            await member.remove_roles(*roles_to_remove, reason=reason)
        except discord.Forbidden:
            return t("clan_member_role_forbidden", locale)
        except discord.HTTPException:
            return t("clan_member_role_remove_failed", locale)

        role_mentions = ", ".join(role.mention for role in roles_to_remove)
        return t("clan_member_role_removed", locale, roles=role_mentions)

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
                    reason=(
                        f"Odebrání člena z klanu {member} – odstranění ticketu "
                        f"(důvod: {reason})"
                    )
                )
                return t("ticket_removed", locale, channel=channel_label)
            except discord.Forbidden:
                return t("ticket_remove_forbidden", locale, channel=channel_label)
            except discord.HTTPException:
                return t("ticket_remove_failed", locale, channel=channel_label)

        return t("ticket_mark_deleted", locale)

    @admin.command(name="ban", description="Zabanuje člena.")
    @app_commands.describe(user="Uživatel, který má být zabanován.", reason="Důvod banu.")
    @app_commands.checks.has_permissions(ban_members=True)
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

    @admin.command(
        name="warn",
        description="Upozorní hráče za neaktivitu a přidá příslušnou varovnou roli.",
    )
    @app_commands.describe(user="Hráč, který má dostat varování za neaktivitu.")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def warn_player(self, interaction: discord.Interaction, user: discord.Member):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Příkaz lze použít pouze na serveru.", ephemeral=True
            )
            return

        warn_roles = self._get_warn_roles(guild)
        if not all(warn_roles):
            await interaction.response.send_message(
                "Chybí nastavené warn role na serveru (WARN_ROLE_1_ID/2/3).",
                ephemeral=True,
            )
            return

        warn1, warn2, warn3 = warn_roles  # type: ignore[misc]

        if not self._can_moderate(interaction.user, user):
            await interaction.response.send_message(
                "Nemůžeš udělit varování uživateli s vyšší nebo stejnou rolí.",
                ephemeral=True,
            )
            return
        if not self._bot_can_moderate(guild, user):
            await interaction.response.send_message(
                "Nemohu udělit varování kvůli hierarchii rolí.", ephemeral=True
            )
            return

        current_roles = {role.id for role in user.roles}

        if WARN_ROLE_3_ID in current_roles:
            try:
                await interaction.user.send(
                    f"\N{WARNING SIGN} {user.mention} již má maximální počet varování (3)."
                )
            except discord.Forbidden:
                pass
            await interaction.response.send_message(
                "Uživatel již má maximální počet varování (3).", ephemeral=True
            )
            return

        next_role: discord.Role
        roles_to_remove: list[discord.Role] = []
        if WARN_ROLE_2_ID in current_roles:
            next_role = warn3
            roles_to_remove.append(warn2)
            status_text = "3/3"
        elif WARN_ROLE_1_ID in current_roles:
            next_role = warn2
            roles_to_remove.append(warn1)
            status_text = "2/3"
        else:
            next_role = warn1
            roles_to_remove.extend(role for role in (warn2, warn3) if role in user.roles)
            status_text = "1/3"

        if not self._can_assign_role(guild, next_role):
            await interaction.response.send_message(
                "Nemohu přiřadit warn roli kvůli hierarchii rolí bota.", ephemeral=True
            )
            return

        for role in roles_to_remove:
            if self._can_assign_role(guild, role):
                await user.remove_roles(role, reason="Aktualizace warn úrovně")

        await user.add_roles(next_role, reason="Varování za neaktivitu")

        try:
            await user.send(
                f"\N{WARNING SIGN} Dostal/a jsi varování za neaktivitu ({status_text})."
            )
        except discord.Forbidden:
            pass

        await interaction.response.send_message(
            f"Uživatel {user.mention} obdržel varování ({status_text}).", ephemeral=True
        )

    @app_commands.command(name="mute", description="Dočasně umlčí člena (timeout).")
    @app_commands.describe(
        user="Uživatel, který má být umlčen.",
        duration_minutes="Délka v minutách (1-10080).",
        reason="Důvod umlčení.",
    )
    @app_commands.checks.has_permissions(moderate_members=True)
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
    @app_commands.checks.has_permissions(manage_nicknames=True)
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


async def setup(bot: commands.Bot):
    await bot.add_cog(BasicCommandsCog(bot))
