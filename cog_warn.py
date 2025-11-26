import discord
from discord import app_commands
from discord.ext import commands

from config import WARN_ROLE_1_ID, WARN_ROLE_2_ID, WARN_ROLE_3_ID


class InactivityWarnCog(commands.Cog, name="InactivityWarn"):
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

    @app_commands.command(
        name="warn",
        description="Upozorní hráče za neaktivitu a přidá příslušnou varovnou roli.",
    )
    @app_commands.describe(user="Hráč, který má dostat varování za neaktivitu.")
    @app_commands.default_permissions(manage_roles=True)
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


async def setup(bot: commands.Bot):
    await bot.add_cog(InactivityWarnCog(bot))
