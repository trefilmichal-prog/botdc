from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple, TYPE_CHECKING

import discord
from discord.ext import commands
from discord import app_commands

from config import (
    CLAN_MEMBER_ROLE_ID,
    CLAN_MEMBER_ROLE_EN_ID,
    CLAN2_MEMBER_ROLE_ID,
    CLAN_APPLICATION_PING_ROLE_ID,
    CLAN_TICKET_CATEGORY_ID,
    CLAN_ACCEPTED_TICKET_CATEGORY_ID,
    CLAN2_ACCEPTED_TICKET_CATEGORY_ID,
    CLAN_VACATION_TICKET_CATEGORY_ID,
    CLAN_BOOSTS_IMAGE_URL,
    CLAN_BANNER_IMAGE_URL,
    TICKET_VIEWER_ROLE_ID,
)
from i18n import DEFAULT_LOCALE, get_interaction_locale, normalize_locale, t

if TYPE_CHECKING:  # pragma: no cover - only for type hints
    from cog_clan2 import Clan2ApplicationsCog, Clan2ApplicationModal
from db import (
    create_clan_application,
    get_open_application_by_user,
    get_latest_clan_application_by_user,
    get_clan_applications_by_user,
    get_open_application_by_channel,
    update_clan_application_form,
    set_clan_application_status,
    mark_clan_application_deleted,
)


class ClanApplicationsCog(commands.Cog, name="ClanApplicationsCog"):
    """
    Ticket systém pro přihlášky do klanu + admin panel klanu.
    Tickety (kanály) se nemažou – zůstávají, jen měníme stav v DB a role.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ticket_clan_label = "clan1"

        # persistentní view – panel pro přihlášky a admin view v ticketech
        self.apply_panel_view = ClanApplyPanelView(self, DEFAULT_LOCALE)
        self.admin_view = ClanAdminView(self, DEFAULT_LOCALE)

        self.bot.add_view(self.apply_panel_view)
        self.bot.add_view(self.admin_view)

        # seznam admin panelů: guild_id -> [(channel_id, message_id), ...]
        self.admin_panels: Dict[int, List[Tuple[int, int]]] = {}

    def _normalize_ticket_base(self, base: str) -> str:
        safe_base = base.strip() or "ticket"
        safe_base = safe_base.lower().replace(" ", "-")
        return safe_base

    def build_ticket_name(self, base: str, status: str = "open") -> str:
        emoji_map = {
            "accepted": "🟢",
            "rejected": "🔴",
        }
        emoji = emoji_map.get(status, "🟠")
        normalized = self._normalize_ticket_base(base)
        clan_label = self.ticket_clan_label
        prefix = f"{clan_label}" if status == "accepted" else f"přihlášky-{clan_label}"
        name = f"{emoji}{prefix}-{normalized}"
        return name[:90]

    async def remove_clan_ticket_for_member(
        self,
        guild: discord.Guild,
        member: discord.Member,
        reason: str,
        locale: discord.Locale = DEFAULT_LOCALE,
    ) -> str | None:
        latest_app = get_latest_clan_application_by_user(guild.id, member.id)
        if latest_app is None or latest_app.get("deleted"):
            return None

        channel = guild.get_channel(latest_app["channel_id"])
        channel_label = (
            channel.mention if isinstance(channel, discord.TextChannel) else "ticket"
        )

        mark_clan_application_deleted(latest_app["id"])

        if isinstance(channel, discord.TextChannel):
            try:
                await channel.delete(
                    reason=(
                        f"Kick uživatele {member} – odstranění ticketu (důvod: {reason})"
                    )
                )
                return t("clan_ticket_deleted", locale, channel=channel_label)
            except discord.Forbidden:
                return t("clan_ticket_delete_forbidden", locale, channel=channel_label)
            except discord.HTTPException:
                return t("clan_ticket_delete_failed", locale, channel=channel_label)

        return t("clan_ticket_missing", locale)

    def _get_ticket_base_from_app(
        self, app: Dict[str, Any], guild: discord.Guild
    ) -> str:
        if app.get("roblox_nick"):
            return str(app["roblox_nick"])

        member = guild.get_member(app["user_id"])
        if member is not None:
            return member.display_name

        return "ticket"

    async def rename_ticket_channel(
        self,
        channel: discord.TextChannel,
        base: str,
        status: str,
    ):
        new_name = self.build_ticket_name(base, status)
        if channel.name == new_name:
            return

        reason_map = {
            "accepted": "Přihláška přijata – přejmenování ticketu",
            "rejected": "Přihláška zamítnuta – přejmenování ticketu",
        }
        reason = reason_map.get(status, "Aktualizace ticketu klanu")

        try:
            await channel.edit(name=new_name, reason=reason)
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ---------- SLASH COMMANDS – PŘIHLÁŠKY ----------

    @app_commands.command(
        name="setup_clan_panel",
        description="Vytvoří panel pro přihlášky do klanu v tomto kanálu (admin).",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_clan_panel_cmd(self, interaction: discord.Interaction):
        locale = get_interaction_locale(interaction)
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                t("guild_text_only", locale),
                ephemeral=True,
            )
            return

        embed_description = (
            t("clan_benefits_list", locale)
        )

        main_embed = discord.Embed(
            title=t("clan_benefits_title", locale),
            description=embed_description,
            color=0x3498DB,
        )

        if CLAN_BOOSTS_IMAGE_URL:
            main_embed.url = CLAN_BOOSTS_IMAGE_URL

        requirements_text = t("clan_requirements_list", locale)

        main_embed.add_field(
            name=t("clan_requirements_title", locale),
            value=requirements_text,
            inline=False,
        )

        if CLAN_BANNER_IMAGE_URL:
            main_embed.set_image(url=CLAN_BANNER_IMAGE_URL)

        localized_view = ClanApplyPanelView(self, locale)
        self.bot.add_view(localized_view)

        await channel.send(embed=main_embed, view=localized_view)

        await interaction.response.send_message(
            t("clan_panel_created", locale),
            ephemeral=True,
        )

    # ---------- SLASH COMMAND – ADMIN PANEL CLANU ----------

    @app_commands.command(
        name="clan_panel",
        description="Zobrazí admin panel se seznamem členů klanu (Warn / Kick).",
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    async def clan_panel_cmd(self, interaction: discord.Interaction):
        locale = get_interaction_locale(interaction)
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                t("guild_only", locale),
                ephemeral=True,
            )
            return

        embed, view = self.build_clan_admin_panel(guild, locale)
        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=False,
        )
        msg = await interaction.original_response()
        self.register_admin_panel(msg)

    @app_commands.command(
        name="clan_kick",
        description=(
            "Odebere člena z klanu (role) a smaže jeho ticket, pokud existuje."
        ),
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    async def clan_kick_cmd(
        self, interaction: discord.Interaction, member: discord.Member
    ):
        locale = get_interaction_locale(interaction)
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                t("guild_only", locale), ephemeral=True
            )
            return

        role = guild.get_role(CLAN_MEMBER_ROLE_ID) if CLAN_MEMBER_ROLE_ID else None
        if role is None:
            await interaction.response.send_message(
                t("clan_setup_role_missing", locale, role_id=CLAN_MEMBER_ROLE_ID),
                ephemeral=True,
            )
            return

        if role not in member.roles:
            await interaction.response.send_message(
                t("clan_member_not_found", locale), ephemeral=True
            )
            return

        try:
            await member.remove_roles(
                role,
                reason="Clan kick command – odebrání clan role",
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                t("clan_member_role_forbidden", locale), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        ticket_info = await self.remove_clan_ticket_for_member(
            guild, member, "Clan kick command"
        )

        try:
            await member.send(
                f"Na serveru **{guild.name}** ti byla odebrána role člena klanu. "
                f"Pokud chceš, můžeš si znovu požádat o pozvánku přes ticket."
            )
            dm_info = t("direct_message_sent", locale)
        except discord.Forbidden:
            dm_info = t("direct_message_failed", locale)

        response = (
            f"\N{WAVING HAND SIGN} {member.mention} byl/a odebrán/a z klanu (odebrána role)."
        )
        if ticket_info:
            response = f"{response}\n{ticket_info}"

        response = f"{response}\n{dm_info}"

        await interaction.followup.send(response, ephemeral=True)

    @app_commands.command(
        name="update_clan_ticket",
        description="Přesune clan tickety členů do správné kategorie.",
    )
    @app_commands.checks.has_permissions(manage_channels=True)
    async def update_clan_ticket_cmd(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Příkaz lze použít pouze na serveru.",
                ephemeral=True,
            )
            return

        target_category = guild.get_channel(CLAN_ACCEPTED_TICKET_CATEGORY_ID)
        if not isinstance(target_category, discord.CategoryChannel):
            await interaction.response.send_message(
                "Nastavená kategorie pro přijaté tickety neexistuje.",
                ephemeral=True,
            )
            return

        member_role = guild.get_role(CLAN_MEMBER_ROLE_ID) if CLAN_MEMBER_ROLE_ID else None
        if member_role is None:
            await interaction.response.send_message(
                "Role pro členy klanu nebyla nalezena.",
                ephemeral=True,
            )
            return

        moved = 0
        missing = 0
        already_ok = 0
        failed = 0

        for member in member_role.members:
            apps = get_clan_applications_by_user(guild.id, member.id)
            channel: Optional[discord.TextChannel] = None
            selected_app: Optional[Dict[str, Any]] = None

            for candidate in apps:
                candidate_channel = guild.get_channel(candidate["channel_id"])
                if isinstance(candidate_channel, discord.TextChannel):
                    channel = candidate_channel
                    selected_app = candidate
                    break

            if channel is None:
                missing += 1
                continue

            if selected_app is not None:
                base = self._get_ticket_base_from_app(selected_app, guild)
                await self.rename_ticket_channel(channel, base, "accepted")

            if channel.category_id == target_category.id:
                already_ok += 1
                continue

            try:
                await channel.edit(
                    category=target_category,
                    reason="Přemapování clan ticketů na novou kategorii",
                )
                moved += 1
            except (discord.Forbidden, discord.HTTPException):
                failed += 1

        await interaction.response.send_message(
            (
                "Hotovo. Přesunuto: {moved}, chybějící ticket: {missing}, "
                "chyby při přesunu: {failed}, již ve správné kategorii: {already_ok}."
            ).format(
                moved=moved,
                missing=missing,
                failed=failed,
                already_ok=already_ok,
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="pair_clan_ticket",
        description=(
            "Spáruje ticket z jiného bota s členem klanu podle roblox nicku v názvu."
        ),
    )
    @app_commands.checks.has_permissions(manage_channels=True)
    async def pair_clan_ticket_cmd(self, interaction: discord.Interaction):
        """
        Vyhledá člena klanu s rolí CLAN_MEMBER_ROLE_ID podle roblox nicku v názvu
        aktuálního ticket kanálu a vytvoří pro něj záznam přihlášky.
        """

        guild = interaction.guild
        channel = interaction.channel

        if guild is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Tento příkaz lze použít pouze v textovém kanálu na serveru.",
                ephemeral=True,
            )
            return

        existing = get_open_application_by_channel(channel.id)
        if existing is not None:
            await interaction.response.send_message(
                "Tento ticket už je spárovaný s přihláškou.", ephemeral=True
            )
            return

        member_role = guild.get_role(CLAN_MEMBER_ROLE_ID) if CLAN_MEMBER_ROLE_ID else None
        if member_role is None:
            await interaction.response.send_message(
                "Role pro členy klanu nebyla nalezena.", ephemeral=True
            )
            return

        channel_name = channel.name.lower()
        candidates: list[discord.Member] = []
        locale = get_interaction_locale(interaction)

        for member in member_role.members:
            normalized_nick = self._normalize_ticket_base(member.display_name)
            if normalized_nick and normalized_nick in channel_name:
                candidates.append(member)

        if not candidates:
            await interaction.response.send_message(
                "V názvu ticketu jsem nenašel žádného člena klanu s roblox nickem.",
                ephemeral=True,
            )
            return

        if len(candidates) > 1:
            names = ", ".join(m.display_name for m in candidates[:10])
            await interaction.response.send_message(
                "Nalezeno více možných členů: "
                f"{names}. Zúž název ticketu nebo uprav přezdívku.",
                ephemeral=True,
            )
            return

        target = candidates[0]
        app_id = create_clan_application(
            guild.id, channel.id, target.id, locale=str(locale.value)
        )

        # Uložíme známý roblox nick, další pole ponecháme prázdná.
        update_clan_application_form(app_id, target.display_name, "", "")

        await interaction.response.send_message(
            f"Ticket byl spárován s hráčem {target.mention} "
            f"(roblox nick: {target.display_name}).",
            ephemeral=False,
        )

    def register_admin_panel(self, message: discord.Message):
        """Uloží ID message s admin panelem pro pozdější refresh."""
        if message.guild is None:
            return
        panels = self.admin_panels.setdefault(message.guild.id, [])
        panels.append((message.channel.id, message.id))

    async def refresh_admin_panels(self, guild: discord.Guild):
        """Přegeneruje všechny admin panely v daném guildu (po přidání/odebrání role)."""
        guild_panels = self.admin_panels.get(guild.id)
        if not guild_panels:
            return

        embed, view = self.build_clan_admin_panel(guild)
        new_list: List[Tuple[int, int]] = []

        for channel_id, message_id in guild_panels:
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                continue
            try:
                msg = await channel.fetch_message(message_id)
            except discord.NotFound:
                continue
            try:
                await msg.edit(embed=embed, view=view)
            except discord.HTTPException:
                continue
            new_list.append((channel_id, message_id))

        self.admin_panels[guild.id] = new_list

    async def move_ticket_to_category(
        self, channel: discord.TextChannel, category_id: int, reason: str
    ) -> bool:
        """Přesune ticket do zadané kategorie."""

        if channel.guild is None:
            return False

        category = channel.guild.get_channel(category_id)
        if not isinstance(category, discord.CategoryChannel):
            return False

        if channel.category_id == category.id:
            return True

        try:
            await channel.edit(category=category, reason=reason)
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def move_ticket_to_accepted_category(
        self, channel: discord.TextChannel
    ) -> bool:
        """Přesune ticket do kategorie pro přijaté členy."""

        return await self.move_ticket_to_category(
            channel,
            CLAN_ACCEPTED_TICKET_CATEGORY_ID,
            "Přesun clan ticketu do kategorie přijatých členů",
        )

    def find_member_ticket_channel(
        self, guild: discord.Guild, member: discord.Member
    ) -> Optional[discord.TextChannel]:
        """Najde ticket kanál spojený s členem klanu (pokud existuje)."""

        apps = get_clan_applications_by_user(guild.id, member.id)
        for app in apps:
            channel = guild.get_channel(app["channel_id"])
            if isinstance(channel, discord.TextChannel):
                return channel

        return None

    def build_clan_admin_panel(
        self, guild: discord.Guild, locale: discord.Locale = DEFAULT_LOCALE
    ) -> tuple[discord.Embed, "ClanAdminPanelView"]:
        """
        Vytvoří embed + view se seznamem členů klanu (role CLAN_MEMBER_ROLE_ID).
        """
        role = guild.get_role(CLAN_MEMBER_ROLE_ID) if CLAN_MEMBER_ROLE_ID else None
        members: List[discord.Member] = []
        if role is not None:
            members = sorted(role.members, key=lambda m: m.display_name.lower())

        if members:
            lines = [
                f"{idx + 1}. {m.mention} (`{m.display_name}`)"
                for idx, m in enumerate(members)
            ]
            desc = "\n".join(lines[:30])
        else:
            desc = t("clan_admin_empty", locale)

        embed = discord.Embed(
            title=t("clan_admin_panel_title", locale),
            description=desc,
            color=0xE67E22,
        )
        embed.set_footer(
            text=t("clan_admin_panel_footer", locale)
        )

        options: List[discord.SelectOption] = []
        for m in members[:25]:  # limit Discordu
            label = m.display_name
            if len(label) > 100:
                label = label[:97] + "..."
            options.append(
                discord.SelectOption(
                    label=label,
                    value=str(m.id),
                    description=f"ID: {m.id}",
                )
            )

        view = ClanAdminPanelView(self, options, locale)
        return embed, view

    def _get_ticket_target_category(self, member: discord.Member) -> int | None:
        if CLAN2_MEMBER_ROLE_ID and member.get_role(CLAN2_MEMBER_ROLE_ID):
            return CLAN2_ACCEPTED_TICKET_CATEGORY_ID

        if CLAN_MEMBER_ROLE_ID and member.get_role(CLAN_MEMBER_ROLE_ID):
            return CLAN_ACCEPTED_TICKET_CATEGORY_ID

        if CLAN_MEMBER_ROLE_EN_ID and member.get_role(CLAN_MEMBER_ROLE_EN_ID):
            return CLAN_ACCEPTED_TICKET_CATEGORY_ID

        return None

    @commands.Cog.listener()
    async def on_member_update(
        self, before: discord.Member, after: discord.Member
    ) -> None:
        if before.guild is None or before.guild != after.guild:
            return

        before_roles = {r.id for r in before.roles}
        after_roles = {r.id for r in after.roles}

        if before_roles == after_roles:
            return

        target_category_id = self._get_ticket_target_category(after)
        if target_category_id is None:
            return

        ticket_channel = self.find_member_ticket_channel(after.guild, after)
        if not isinstance(ticket_channel, discord.TextChannel):
            return

        if ticket_channel.category_id == target_category_id:
            return

        apps = get_clan_applications_by_user(after.guild.id, after.id)
        selected_app: Optional[Dict[str, Any]] = None

        for candidate in apps:
            if candidate.get("channel_id") == ticket_channel.id:
                selected_app = candidate
                break

        if selected_app is not None:
            base = self._get_ticket_base_from_app(selected_app, after.guild)
            await self.rename_ticket_channel(ticket_channel, base, "accepted")

        await self.move_ticket_to_category(
            ticket_channel,
            target_category_id,
            "Automatický přesun ticketu podle změny role",
        )


# ---------- VIEW: Panel s tlačítkem "Podat přihlášku" ----------

class ClanApplyPanelView(discord.ui.View):
    def __init__(self, cog: ClanApplicationsCog, locale: discord.Locale):
        super().__init__(timeout=None)
        self.cog = cog
        self.locale = locale
        self._apply_locale()

    def _apply_locale(self):
        is_english = normalize_locale(self.locale) == DEFAULT_LOCALE

        for child in list(self.children):
            if isinstance(child, discord.ui.Button) and child.custom_id == "clan_apply_button":
                child.label = "HROT"

            if isinstance(child, discord.ui.Button) and child.custom_id == "clan2_apply_button":
                if is_english:
                    self.remove_item(child)
                else:
                    child.label = "HR2T"

    def _get_clan2_cog(self) -> "Clan2ApplicationsCog | None":
        return self.cog.bot.get_cog("Clan2ApplicationsCog")

    async def _open_application_modal(
        self,
        interaction: discord.Interaction,
        modal_factory,
        target_cog: "ClanApplicationsCog | Clan2ApplicationsCog",
    ):
        locale = get_interaction_locale(interaction)
        user = interaction.user
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                t("guild_only", locale),
                ephemeral=True,
            )
            return

        existing = get_open_application_by_user(guild.id, user.id)
        if existing is not None:
            ch_id = existing["channel_id"]
            channel = guild.get_channel(ch_id)
            if isinstance(channel, discord.TextChannel):
                await interaction.response.send_message(
                    t("clan_application_open_in_channel", locale, channel=channel.mention),
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    t("clan_application_open_wait", locale),
                    ephemeral=True,
                )
            return

        latest_app = get_latest_clan_application_by_user(guild.id, user.id)
        if (
            latest_app is not None
            and latest_app.get("deleted") == 0
            and latest_app.get("status") != "rejected"
        ):
            existing_channel = guild.get_channel(latest_app["channel_id"])
            if isinstance(existing_channel, discord.TextChannel):
                await interaction.response.send_message(
                    t(
                        "clan_application_open_in_channel",
                        locale,
                        channel=existing_channel.mention,
                    ),
                    ephemeral=True,
                )
                return

        modal = modal_factory(target_cog, locale)
        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="HROT",
        style=discord.ButtonStyle.primary,
        custom_id="clan_apply_button",
    )
    async def apply_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        # pouze otevřeme formulář, ticket se vytvoří až po submit
        await self._open_application_modal(
            interaction,
            lambda cog, loc: ClanApplicationModal(cog, loc),
            self.cog,
        )

    @discord.ui.button(
        label="HR2T",
        style=discord.ButtonStyle.primary,
        custom_id="clan2_apply_button",
    )
    async def apply_clan2_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        clan2_cog = self._get_clan2_cog()
        if clan2_cog is None:
            await interaction.response.send_message(
                "Clan2 panel není dostupný.",
                ephemeral=True,
            )
            return

        from cog_clan2 import Clan2ApplicationModal

        await self._open_application_modal(
            interaction,
            lambda cog, loc: Clan2ApplicationModal(cog, loc),
            clan2_cog,
        )


# ---------- MODAL: Přihláška – vytvoření ticketu až po submit ----------

class ClanApplicationModal(discord.ui.Modal):
    def __init__(self, cog: ClanApplicationsCog, locale: discord.Locale):
        super().__init__(timeout=None, title=t("clan_modal_title", locale))
        self.cog = cog
        self.locale = locale

        self.roblox_nick = discord.ui.TextInput(
            label=t("clan_modal_roblox_label", locale),
            placeholder=t("clan_modal_roblox_placeholder", locale),
            required=True,
            max_length=32,
        )
        self.hours_per_day = discord.ui.TextInput(
            label=t("clan_modal_hours_label", locale),
            placeholder=t("clan_modal_hours_placeholder", locale),
            required=True,
            max_length=32,
        )
        self.rebirths = discord.ui.TextInput(
            label=t("clan_modal_rebirths_label", locale),
            placeholder=t("clan_modal_rebirths_placeholder", locale),
            required=True,
            max_length=32,
        )

        self.add_item(self.roblox_nick)
        self.add_item(self.hours_per_day)
        self.add_item(self.rebirths)

    async def on_submit(self, interaction: discord.Interaction):
        locale = self.locale
        guild = interaction.guild
        user = interaction.user

        if guild is None or not isinstance(user, discord.Member):
            await interaction.response.send_message(
                t("clan_modal_retry", locale),
                ephemeral=True,
            )
            return

        # kategorie ticketů
        category = guild.get_channel(CLAN_TICKET_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(
                t("clan_ticket_category_missing", locale),
                ephemeral=True,
            )
            return

        existing_channel = None
        latest_app = get_latest_clan_application_by_user(guild.id, user.id)
        if (
            latest_app is not None
            and latest_app.get("deleted") == 0
            and latest_app.get("status") == "rejected"
        ):
            channel_candidate = guild.get_channel(latest_app["channel_id"])
            if isinstance(channel_candidate, discord.TextChannel):
                existing_channel = channel_candidate

        # kontrola, jestli mezitím nevznikla přihláška
        existing = get_open_application_by_user(guild.id, user.id)
        if existing is not None:
            ch_id = existing["channel_id"]
            channel = guild.get_channel(ch_id)
            if isinstance(channel, discord.TextChannel):
                await interaction.response.send_message(
                    t("clan_application_open_in_channel", locale, channel=channel.mention),
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    t("clan_application_open_wait", locale),
                    ephemeral=True,
                )
            return

        nick = self.roblox_nick.value.strip()
        hours_text = self.hours_per_day.value.strip()
        rebirths_text = self.rebirths.value.strip()

        # vytvoření ticket kanálu – použijeme nick pro název
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            ),
        }

        if TICKET_VIEWER_ROLE_ID:
            ticket_viewer_role = guild.get_role(TICKET_VIEWER_ROLE_ID)
            if ticket_viewer_role:
                overwrites[ticket_viewer_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )
        ch_name = self.cog.build_ticket_name(nick or user.name, "open")

        reason_text = t("clan_ticket_audit", DEFAULT_LOCALE, user=user, user_id=user.id)

        if existing_channel is None:
            ticket_channel = await guild.create_text_channel(
                name=ch_name,
                category=category,
                overwrites=overwrites,
                reason=reason_text,
            )
        else:
            ticket_channel = existing_channel
            await ticket_channel.edit(
                name=ch_name,
                category=category,
                overwrites=overwrites,
                reason=reason_text,
            )

        # záznam v DB + doplnění údajů
        app_id = create_clan_application(
            guild_id=guild.id,
            channel_id=ticket_channel.id,
            user_id=user.id,
            locale=str(locale.value),
        )
        update_clan_application_form(app_id, nick, hours_text, rebirths_text)

        # přezdívka na serveru = Roblox nick (pokud jde)
        member = guild.get_member(user.id)
        if member is not None and nick:
            try:
                await member.edit(
                    nick=nick[:32],
                    reason="Přihláška do klanu – roblox nick",
                )
            except discord.Forbidden:
                pass

        # embed s informacemi z přihlášky
        app_embed = discord.Embed(
            title=t("clan_application_embed_title", locale, nick=nick),
            color=0x2ECC71,
        )
        app_embed.add_field(
            name=t("clan_application_field_roblox", locale),
            value=nick,
            inline=False,
        )
        app_embed.add_field(
            name=t("clan_application_field_hours", locale),
            value=hours_text,
            inline=True,
        )
        app_embed.add_field(
            name=t("clan_application_field_rebirths", locale),
            value=rebirths_text,
            inline=True,
        )
        app_embed.set_footer(
            text=t("clan_application_footer", locale)
        )

        # embed s instrukcemi na screeny
        intro_embed = discord.Embed(
            title=t("clan_application_intro_title", locale),
            description=t("clan_application_intro_body", locale),
            color=0x2980B9,
        )

        content_parts = [user.mention]
        if CLAN_APPLICATION_PING_ROLE_ID:
            content_parts.insert(0, f"<@&{CLAN_APPLICATION_PING_ROLE_ID}>")

        admin_view = ClanAdminView(self.cog, locale)
        self.cog.bot.add_view(admin_view)

        await ticket_channel.send(
            content=" ".join(content_parts),
            embeds=[intro_embed, app_embed],
            view=admin_view,
        )

        await interaction.response.send_message(
            t("clan_application_created", locale, channel=ticket_channel.mention),
            ephemeral=True,
        )


# ---------- VIEW: Admin rozhodnutí (Přijmout / Zamítnout) ----------

class ClanAdminView(discord.ui.View):
    def __init__(self, cog: ClanApplicationsCog, locale: discord.Locale):
        super().__init__(timeout=None)
        self.cog = cog
        self.locale = locale
        self._apply_locale()

    def _apply_locale(self):
        label_map = {
            "clan_accept": "clan_accept_button_label",
            "clan_toggle_vacation": "clan_vacation_button_label",
            "clan_reject": "clan_reject_button_label",
        }

        for child in self.children:
            if isinstance(child, discord.ui.Button):
                key = label_map.get(child.custom_id)
                if key:
                    child.label = t(key, self.locale)

    async def _get_open_app_for_channel(
        self,
        interaction: discord.Interaction,
    ) -> Optional[Dict[str, Any]]:
        locale = get_interaction_locale(interaction)
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                t("guild_text_only", locale),
                ephemeral=True,
            )
            return None

        app = get_open_application_by_channel(channel.id)
        if app is None:
            await interaction.response.send_message(
                t("clan_application_not_found", locale),
                ephemeral=True,
            )
            return None
        return app

    def _is_admin(self, user: discord.Member) -> bool:
        perms = user.guild_permissions
        if perms.administrator or perms.manage_guild or perms.manage_roles:
            return True

        if TICKET_VIEWER_ROLE_ID:
            role = user.guild.get_role(TICKET_VIEWER_ROLE_ID)
            if role in user.roles:
                return True

        return False

    @discord.ui.button(
        label="Přijmout",
        style=discord.ButtonStyle.success,
        custom_id="clan_accept",
    )
    async def accept_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        locale = get_interaction_locale(interaction)
        guild = interaction.guild
        user = interaction.user
        if guild is None or not isinstance(user, discord.Member):
            await interaction.response.send_message(
                t("guild_only", locale),
                ephemeral=True,
            )
            return

        if not self._is_admin(user):
            await interaction.response.send_message(
                t("clan_admin_only", locale),
                ephemeral=True,
            )
            return

        app = await self._get_open_app_for_channel(interaction)
        if app is None:
            return

        app_locale = normalize_locale(app.get("locale", DEFAULT_LOCALE))
        role_id = (
            CLAN_MEMBER_ROLE_EN_ID
            if app_locale == DEFAULT_LOCALE
            else CLAN_MEMBER_ROLE_ID
        )

        set_clan_application_status(app["id"], "accepted", datetime.utcnow())

        channel = interaction.channel
        member = guild.get_member(app["user_id"])
        if isinstance(channel, discord.TextChannel):
            base = self.cog._get_ticket_base_from_app(app, guild)
            await self.cog.rename_ticket_channel(channel, base, "accepted")
            await self.cog.move_ticket_to_accepted_category(channel)
        if member is not None and role_id:
            role = guild.get_role(role_id)
            if role is not None:
                try:
                    await member.add_roles(role, reason="Přijetí do klanu")
                except discord.Forbidden:
                    pass

        await interaction.response.send_message(
            t("clan_application_accept_public", locale),
            ephemeral=False,
        )

        if member is not None:
            try:
                await member.send(
                    t("clan_application_accept_dm", app_locale, guild=guild.name)
                )
            except discord.Forbidden:
                pass

        # refresh admin panelů (nový člen)
        await self.cog.refresh_admin_panels(guild)

    @discord.ui.button(
        label="Dovolená",
        style=discord.ButtonStyle.secondary,
        custom_id="clan_toggle_vacation",
    )
    async def vacation_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        guild = interaction.guild
        user = interaction.user
        if guild is None or not isinstance(user, discord.Member):
            await interaction.response.send_message(
                "Tento ticket lze použít pouze na serveru.",
                ephemeral=True,
            )
            return

        if not self._is_admin(user):
            await interaction.response.send_message(
                "Tuto akci může provést pouze admin.",
                ephemeral=True,
            )
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Tlačítko lze použít pouze v ticket kanálu.",
                ephemeral=True,
            )
            return

        accepted_category = guild.get_channel(CLAN_ACCEPTED_TICKET_CATEGORY_ID)
        vacation_category = guild.get_channel(CLAN_VACATION_TICKET_CATEGORY_ID)

        if not isinstance(accepted_category, discord.CategoryChannel):
            await interaction.response.send_message(
                "Kategorie pro přijaté členy není správně nastavena.",
                ephemeral=True,
            )
            return

        if not isinstance(vacation_category, discord.CategoryChannel):
            await interaction.response.send_message(
                "Kategorie pro dovolenou není správně nastavena.",
                ephemeral=True,
            )
            return

        moving_to_vacation = channel.category_id != vacation_category.id
        target_category_id = (
            CLAN_VACATION_TICKET_CATEGORY_ID
            if moving_to_vacation
            else CLAN_ACCEPTED_TICKET_CATEGORY_ID
        )
        reason = (
            "Přesun clan ticketu do kategorie dovolené"
            if moving_to_vacation
            else "Přesun clan ticketu zpět z dovolené"
        )

        success = await self.cog.move_ticket_to_category(
            channel, target_category_id, reason
        )
        if not success:
            await interaction.response.send_message(
                "Nepodařilo se přesunout ticket do zvolené kategorie.",
                ephemeral=True,
            )
            return

        message = (
            f"Ticket {channel.mention} byl přesunut do kategorie dovolené."
            if moving_to_vacation
            else f"Ticket {channel.mention} byl přesunut zpět mezi členy klanu."
        )

        await interaction.response.send_message(message, ephemeral=False)

    @discord.ui.button(
        label="Zamítnout",
        style=discord.ButtonStyle.danger,
        custom_id="clan_reject",
    )
    async def reject_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        locale = get_interaction_locale(interaction)
        guild = interaction.guild
        user = interaction.user
        if guild is None or not isinstance(user, discord.Member):
            await interaction.response.send_message(
                t("guild_only", locale),
                ephemeral=True,
            )
            return

        if not self._is_admin(user):
            await interaction.response.send_message(
                t("clan_admin_only", locale),
                ephemeral=True,
            )
            return

        app = await self._get_open_app_for_channel(interaction)
        if app is None:
            return

        app_locale = normalize_locale(app.get("locale", DEFAULT_LOCALE))

        set_clan_application_status(app["id"], "rejected", datetime.utcnow())

        channel = interaction.channel
        member = guild.get_member(app["user_id"])
        if isinstance(channel, discord.TextChannel):
            base = self.cog._get_ticket_base_from_app(app, guild)
            await self.cog.rename_ticket_channel(channel, base, "rejected")

        await interaction.response.send_message(
            t("clan_application_reject_public", locale),
            ephemeral=False,
        )

        if member is not None:
            try:
                await member.send(
                    t("clan_application_reject_dm", app_locale, guild=guild.name)
                )
            except discord.Forbidden:
                pass


# ---------- VIEW: Admin panel klanu (Warn / Kick) ----------

class ClanAdminPanelView(discord.ui.View):
    def __init__(
        self,
        cog: ClanApplicationsCog,
        options: List[discord.SelectOption],
        locale: discord.Locale,
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.locale = locale
        self.selected_member_id: Optional[int] = None
        self.member_select: Optional[discord.ui.Select] = None

        if not options:
            options = [
                discord.SelectOption(
                    label=t("clan_admin_select_empty", locale),
                    value="none",
                    description=t("clan_admin_select_empty_desc", locale),
                )
            ]

        select = discord.ui.Select(
            placeholder=t("clan_admin_select_placeholder", locale),
            min_values=1,
            max_values=1,
            options=options,
            custom_id="clan_admin_select_member",
        )
        select.callback = self.on_select  # type: ignore
        self.member_select = select
        self.add_item(select)
        self._apply_locale()

    def _apply_locale(self):
        label_map = {
            "clan_admin_warn": "clan_admin_warn_button_label",
            "clan_admin_toggle_vacation": "clan_vacation_button_label",
            "clan_admin_kick": "clan_admin_kick_button_label",
        }

        for child in self.children:
            if isinstance(child, discord.ui.Button):
                key = label_map.get(child.custom_id)
                if key:
                    child.label = t(key, self.locale)

    async def on_select(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Tento panel lze použít pouze na serveru.",
                ephemeral=True,
            )
            return

        user = interaction.user
        if not isinstance(user, discord.Member) or not (
            user.guild_permissions.administrator
            or user.guild_permissions.manage_roles
        ):
            await interaction.response.send_message(
                "Tento panel může používat pouze admin (nebo člen s Manage Roles).",
                ephemeral=True,
            )
            return

        if self.member_select is None or not self.member_select.values:
            await interaction.response.send_message(
                "Nebyl vybrán žádný hráč.",
                ephemeral=True,
            )
            return

        value = self.member_select.values[0]
        if value == "none":
            self.selected_member_id = None
            await interaction.response.send_message(
                "V klanu aktuálně není žádný hráč k vybrání.",
                ephemeral=True,
            )
            return

        try:
            member_id = int(value)
        except ValueError:
            await interaction.response.send_message(
                "Neplatná hodnota výběru.",
                ephemeral=True,
            )
            return

        self.selected_member_id = member_id
        member = guild.get_member(member_id)

        if member is None:
            await interaction.response.send_message(
                "Vybraný hráč už na serveru není.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Vybrán hráč: {member.mention}. Nyní můžeš použít **Warn** nebo **Kick**.",
            ephemeral=True,
        )

    def _check_admin(self, interaction: discord.Interaction) -> Optional[str]:
        guild = interaction.guild
        user = interaction.user
        if guild is None or not isinstance(user, discord.Member):
            return "Tento panel lze použít pouze na serveru."
        if not (
            user.guild_permissions.administrator
            or user.guild_permissions.manage_roles
        ):
            return "Tento panel může používat pouze admin (nebo člen s Manage Roles)."
        return None

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

    def _get_selected_member(
        self, guild: discord.Guild
    ) -> Optional[discord.Member]:
        if self.selected_member_id is None:
            return None
        return guild.get_member(self.selected_member_id)

    @discord.ui.button(
        label="Warn",
        style=discord.ButtonStyle.secondary,
        custom_id="clan_admin_warn",
    )
    async def warn_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        err = self._check_admin(interaction)
        if err is not None:
            await interaction.response.send_message(err, ephemeral=True)
            return

        guild = interaction.guild
        assert isinstance(guild, discord.Guild)

        member = self._get_selected_member(guild)
        if member is None:
            await interaction.response.send_message(
                "Nejdřív v seznamu vyber hráče.",
                ephemeral=True,
            )
            return

        try:
            await member.send(
                f"Na serveru **{guild.name}** jsi dostal **varování (warn)** od clan administrátorů.\n"
                f"Prosím dodržuj pravidla klanu."
            )
            dm_info = "Hráči byla odeslána soukromá zpráva s varováním."
        except discord.Forbidden:
            dm_info = "Nepodařilo se odeslat DM hráči (má vypnuté soukromé zprávy)."

        await interaction.response.send_message(
            f"Warn pro {member.mention} dokončen. {dm_info}",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Dovolená",
        style=discord.ButtonStyle.secondary,
        custom_id="clan_admin_toggle_vacation",
    )
    async def vacation_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        err = self._check_admin(interaction)
        if err is not None:
            await interaction.response.send_message(err, ephemeral=True)
            return

        guild = interaction.guild
        assert isinstance(guild, discord.Guild)

        member = self._get_selected_member(guild)
        if member is None:
            await interaction.response.send_message(
                "Nejdřív v seznamu vyber hráče.",
                ephemeral=True,
            )
            return

        ticket_channel = self.cog.find_member_ticket_channel(guild, member)
        if ticket_channel is None:
            await interaction.response.send_message(
                "U vybraného hráče nebyl nalezen žádný clan ticket.",
                ephemeral=True,
            )
            return

        accepted_category = guild.get_channel(CLAN_ACCEPTED_TICKET_CATEGORY_ID)
        vacation_category = guild.get_channel(CLAN_VACATION_TICKET_CATEGORY_ID)

        if not isinstance(accepted_category, discord.CategoryChannel):
            await interaction.response.send_message(
                "Kategorie pro přijaté členy není správně nastavena.",
                ephemeral=True,
            )
            return

        if not isinstance(vacation_category, discord.CategoryChannel):
            await interaction.response.send_message(
                "Kategorie pro dovolenou není správně nastavena.",
                ephemeral=True,
            )
            return

        moving_to_vacation = ticket_channel.category_id != vacation_category.id
        target_category_id = (
            CLAN_VACATION_TICKET_CATEGORY_ID
            if moving_to_vacation
            else CLAN_ACCEPTED_TICKET_CATEGORY_ID
        )
        reason = (
            "Přesun clan ticketu do kategorie dovolené"
            if moving_to_vacation
            else "Přesun clan ticketu zpět z dovolené"
        )

        success = await self.cog.move_ticket_to_category(
            ticket_channel, target_category_id, reason
        )
        if not success:
            await interaction.response.send_message(
                "Nepodařilo se přesunout ticket do zvolené kategorie.",
                ephemeral=True,
            )
            return

        message = (
            f"Ticket {ticket_channel.mention} byl přesunut do kategorie dovolené."
            if moving_to_vacation
            else f"Ticket {ticket_channel.mention} byl přesunut zpět mezi členy klanu."
        )

        await interaction.response.send_message(message, ephemeral=True)

    @discord.ui.button(
        label="Kick (odebrat clan roli)",
        style=discord.ButtonStyle.danger,
        custom_id="clan_admin_kick",
    )
    async def kick_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        err = self._check_admin(interaction)
        if err is not None:
            await interaction.response.send_message(err, ephemeral=True)
            return

        guild = interaction.guild
        assert isinstance(guild, discord.Guild)

        member = self._get_selected_member(guild)
        if member is None:
            await interaction.response.send_message(
                "Nejdřív v seznamu vyber hráče.",
                ephemeral=True,
            )
            return

        actor = interaction.user
        assert isinstance(actor, discord.Member)

        if not self._can_moderate(actor, member):
            await interaction.response.send_message(
                "Nemůžeš vyhodit uživatele s vyšší nebo stejnou rolí.",
                ephemeral=True,
            )
            return

        if not self._bot_can_moderate(guild, member):
            await interaction.response.send_message(
                "Nemohu vyhodit uživatele kvůli hierarchii rolí.",
                ephemeral=True,
            )
            return

        reason = "Kick přes clan panel"
        await member.kick(reason=reason)

        ticket_info = await self.cog.remove_clan_ticket_for_member(
            guild, member, reason
        )
        response = (
            f"\N{WAVING HAND SIGN} {member.mention} byl/a vyhozen/a. Důvod: {reason}."
        )
        if ticket_info:
            response = f"{response}\n{ticket_info}"

        await interaction.response.send_message(response, ephemeral=True)
        # refresh všech admin panelů (odebrání člena)
        await self.cog.refresh_admin_panels(guild)


async def setup(bot: commands.Bot):
    await bot.add_cog(ClanApplicationsCog(bot))
