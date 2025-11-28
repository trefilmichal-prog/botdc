from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

import discord
from discord.ext import commands
from discord import app_commands

from config import (
    CLAN_MEMBER_ROLE_ID,
    CLAN_APPLICATION_PING_ROLE_ID,
    CLAN_TICKET_CATEGORY_ID,
    CLAN_ACCEPTED_TICKET_CATEGORY_ID,
    CLAN_BOOSTS_IMAGE_URL,
    CLAN_BANNER_IMAGE_URL,
    TICKET_VIEWER_ROLE_ID,
)
from db import (
    create_clan_application,
    get_open_application_by_user,
    get_latest_clan_application_by_user,
    get_clan_applications_by_user,
    get_open_application_by_channel,
    update_clan_application_form,
    set_clan_application_status,
)


class ClanApplicationsCog(commands.Cog, name="ClanApplicationsCog"):
    """
    Ticket systém pro přihlášky do klanu + admin panel klanu.
    Tickety (kanály) se nemažou – zůstávají, jen měníme stav v DB a role.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # persistentní view – panel pro přihlášky a admin view v ticketech
        self.apply_panel_view = ClanApplyPanelView(self)
        self.admin_view = ClanAdminView(self)

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
        name = f"{emoji}clan-{normalized}"
        return name[:90]

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
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Tento příkaz lze použít pouze v textovém kanálu.",
                ephemeral=True,
            )
            return

        embed_description = (
            "🫂 Skvělá CZ/SK komunita\n"
            "🎊 Soutěže\n"
            "🍀 Clan boosty (klikni na nadpis pro screen)"
        )

        main_embed = discord.Embed(
            title="Výhody klanu",
            description=embed_description,
            color=0x3498DB,
        )

        if CLAN_BOOSTS_IMAGE_URL:
            main_embed.url = CLAN_BOOSTS_IMAGE_URL

        requirements_text = (
            "💫 500SX rebirthů +\n"
            "💫 Hrát 24/7\n"
            "💫 30% index\n"
            "💫 5d playtime"
        )

        main_embed.add_field(
            name="Podmínky přijetí",
            value=requirements_text,
            inline=False,
        )

        if CLAN_BANNER_IMAGE_URL:
            main_embed.set_image(url=CLAN_BANNER_IMAGE_URL)

        await channel.send(embed=main_embed, view=self.apply_panel_view)

        await interaction.response.send_message(
            "Panel pro přihlášky do klanu byl vytvořen v tomto kanálu.",
            ephemeral=True,
        )

    # ---------- SLASH COMMAND – ADMIN PANEL CLANU ----------

    @app_commands.command(
        name="clan_panel",
        description="Zobrazí admin panel se seznamem členů klanu (Warn / Kick).",
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    async def clan_panel_cmd(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Příkaz lze použít pouze na serveru.",
                ephemeral=True,
            )
            return

        embed, view = self.build_clan_admin_panel(guild)
        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=False,
        )
        msg = await interaction.original_response()
        self.register_admin_panel(msg)

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

            for candidate in apps:
                candidate_channel = guild.get_channel(candidate["channel_id"])
                if isinstance(candidate_channel, discord.TextChannel):
                    channel = candidate_channel
                    break

            if channel is None:
                missing += 1
                continue

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

    async def move_ticket_to_accepted_category(
        self, channel: discord.TextChannel
    ) -> bool:
        """Přesune ticket do kategorie pro přijaté členy."""

        if channel.guild is None:
            return False

        category = channel.guild.get_channel(CLAN_ACCEPTED_TICKET_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            return False

        if channel.category_id == category.id:
            return True

        try:
            await channel.edit(
                category=category,
                reason="Přesun clan ticketu do kategorie přijatých členů",
            )
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    def build_clan_admin_panel(
        self, guild: discord.Guild
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
            desc = "V klanu aktuálně není žádný hráč s nastavenou rolí."

        embed = discord.Embed(
            title="Clan – seznam členů",
            description=desc,
            color=0xE67E22,
        )
        embed.set_footer(
            text="Vyber hráče v menu a použij tlačítka níže (Warn / Kick)."
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

        view = ClanAdminPanelView(self, options)
        return embed, view


# ---------- VIEW: Panel s tlačítkem "Podat přihlášku" ----------

class ClanApplyPanelView(discord.ui.View):
    def __init__(self, cog: ClanApplicationsCog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Podat přihlášku",
        style=discord.ButtonStyle.primary,
        custom_id="clan_apply_button",
    )
    async def apply_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        user = interaction.user
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Tento ticket lze použít pouze na serveru.",
                ephemeral=True,
            )
            return

        # Kontrola, zda už nemá otevřený ticket (přihlášku)
        existing = get_open_application_by_user(guild.id, user.id)
        if existing is not None:
            ch_id = existing["channel_id"]
            channel = guild.get_channel(ch_id)
            if isinstance(channel, discord.TextChannel):
                await interaction.response.send_message(
                    f"Už máš otevřenou přihlášku v kanále {channel.mention}.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "Už máš otevřenou přihlášku. Počkej, než bude vyřízena.",
                    ephemeral=True,
                )
            return

        latest_app = get_latest_clan_application_by_user(guild.id, user.id)
        if latest_app is not None and latest_app.get("deleted") == 0:
            existing_channel = guild.get_channel(latest_app["channel_id"])
            if isinstance(existing_channel, discord.TextChannel):
                await interaction.response.send_message(
                    f"Už máš vytvořený ticket v kanále {existing_channel.mention}.",
                    ephemeral=True,
                )
                return

        # pouze otevřeme formulář, ticket se vytvoří až po submit
        modal = ClanApplicationModal(self.cog)
        await interaction.response.send_modal(modal)


# ---------- MODAL: Přihláška – vytvoření ticketu až po submit ----------

class ClanApplicationModal(discord.ui.Modal, title="Přihláška do klanu"):
    def __init__(self, cog: ClanApplicationsCog):
        super().__init__(timeout=None)
        self.cog = cog

        self.roblox_nick = discord.ui.TextInput(
            label="Roblox nick",
            placeholder="Tvůj nick v Robloxu",
            required=True,
            max_length=32,
        )
        self.hours_per_day = discord.ui.TextInput(
            label="Kolik hodin hraješ denně?",
            placeholder="např. 2–3 hodiny",
            required=True,
            max_length=32,
        )
        self.rebirths = discord.ui.TextInput(
            label="Kolik máš rebirthů?",
            placeholder="např. cca 1500",
            required=True,
            max_length=32,
        )

        self.add_item(self.roblox_nick)
        self.add_item(self.hours_per_day)
        self.add_item(self.rebirths)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user

        if guild is None or not isinstance(user, discord.Member):
            await interaction.response.send_message(
                "Nastala chyba, zkus to prosím znovu na serveru.",
                ephemeral=True,
            )
            return

        # kategorie ticketů
        category = guild.get_channel(CLAN_TICKET_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(
                "Nastavená kategorie pro clan tickety neexistuje. "
                "Zkontroluj CLAN_TICKET_CATEGORY_ID v configu.",
                ephemeral=True,
            )
            return

        # kontrola, jestli mezitím nevznikla přihláška
        existing = get_open_application_by_user(guild.id, user.id)
        if existing is not None:
            ch_id = existing["channel_id"]
            channel = guild.get_channel(ch_id)
            if isinstance(channel, discord.TextChannel):
                await interaction.response.send_message(
                    f"Už máš otevřenou přihlášku v kanále {channel.mention}.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "Už máš otevřenou přihlášku. Počkej, než bude vyřízena.",
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

        ticket_channel = await guild.create_text_channel(
            name=ch_name,
            category=category,
            overwrites=overwrites,
            reason=f"Clan přihláška od {user} ({user.id})",
        )

        # záznam v DB + doplnění údajů
        app_id = create_clan_application(
            guild_id=guild.id,
            channel_id=ticket_channel.id,
            user_id=user.id,
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
            title=f"Přihláška – {nick}",
            color=0x2ECC71,
        )
        app_embed.add_field(name="Roblox nick", value=nick, inline=False)
        app_embed.add_field(name="Hodin denně", value=hours_text, inline=True)
        app_embed.add_field(name="Rebirthů", value=rebirths_text, inline=True)
        app_embed.set_footer(
            text="Admini: použijte tlačítka níže pro přijetí nebo odmítnutí."
        )

        # embed s instrukcemi na screeny
        intro_embed = discord.Embed(
            title="Co poslat do ticketu",
            description=(
                "Prosím pošli následující:\n"
                "♻️ Screeny Petů\n"
                "♻️ Tvoje Gamepassy (pokud vlastníš)\n"
                "♻️ Tvoje Rebirthy\n"
                "♻️ Tvojí Prestige\n\n"
                "⚠️ Vše prosím vyfoť tak, aby byl vidět tvůj nick!"
            ),
            color=0x2980B9,
        )

        content_parts = [user.mention]
        if CLAN_APPLICATION_PING_ROLE_ID:
            content_parts.insert(0, f"<@&{CLAN_APPLICATION_PING_ROLE_ID}>")

        await ticket_channel.send(
            content=" ".join(content_parts),
            embeds=[intro_embed, app_embed],
            view=self.cog.admin_view,
        )

        await interaction.response.send_message(
            f"Přihláška byla uložena a ticket byl vytvořen: {ticket_channel.mention}.\n"
            f"Prosím nahraj do ticketu požadované screeny.",
            ephemeral=True,
        )


# ---------- VIEW: Admin rozhodnutí (Přijmout / Zamítnout) ----------

class ClanAdminView(discord.ui.View):
    def __init__(self, cog: ClanApplicationsCog):
        super().__init__(timeout=None)
        self.cog = cog

    async def _get_open_app_for_channel(
        self,
        interaction: discord.Interaction,
    ) -> Optional[Dict[str, Any]]:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Tento ticket lze použít pouze v textovém kanálu.",
                ephemeral=True,
            )
            return None

        app = get_open_application_by_channel(channel.id)
        if app is None:
            await interaction.response.send_message(
                "V tomto kanálu už není žádná otevřená přihláška.",
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

        app = await self._get_open_app_for_channel(interaction)
        if app is None:
            return

        set_clan_application_status(app["id"], "accepted", datetime.utcnow())

        channel = interaction.channel
        member = guild.get_member(app["user_id"])
        if isinstance(channel, discord.TextChannel):
            base = self.cog._get_ticket_base_from_app(app, guild)
            await self.cog.rename_ticket_channel(channel, base, "accepted")
            await self.cog.move_ticket_to_accepted_category(channel)
        if member is not None and CLAN_MEMBER_ROLE_ID:
            role = guild.get_role(CLAN_MEMBER_ROLE_ID)
            if role is not None:
                try:
                    await member.add_roles(role, reason="Přijetí do klanu")
                except discord.Forbidden:
                    pass

        await interaction.response.send_message(
            "✅ Přihláška byla **přijata**.",
            ephemeral=False,
        )

        if member is not None:
            try:
                await member.send(
                    f"Ahoj, tvoje přihláška do klanu na serveru **{guild.name}** byla **přijata**.\n"
                    f"Vítej v klanu!"
                )
            except discord.Forbidden:
                pass

        # refresh admin panelů (nový člen)
        await self.cog.refresh_admin_panels(guild)

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

        app = await self._get_open_app_for_channel(interaction)
        if app is None:
            return

        set_clan_application_status(app["id"], "rejected", datetime.utcnow())

        channel = interaction.channel
        member = guild.get_member(app["user_id"])
        if isinstance(channel, discord.TextChannel):
            base = self.cog._get_ticket_base_from_app(app, guild)
            await self.cog.rename_ticket_channel(channel, base, "rejected")

        await interaction.response.send_message(
            "❌ Přihláška byla **zamítnuta**.",
            ephemeral=False,
        )

        if member is not None:
            try:
                await member.send(
                    f"Ahoj, tvoje přihláška do klanu na serveru **{guild.name}** byla bohužel **zamítnuta**.\n"
                    f"Můžeš zkusit požádat znovu později."
                )
            except discord.Forbidden:
                pass


# ---------- VIEW: Admin panel klanu (Warn / Kick) ----------

class ClanAdminPanelView(discord.ui.View):
    def __init__(
        self,
        cog: ClanApplicationsCog,
        options: List[discord.SelectOption],
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.selected_member_id: Optional[int] = None

        if not options:
            options = [
                discord.SelectOption(
                    label="Žádný člen k dispozici",
                    value="none",
                    description="V klanu aktuálně nikdo není.",
                )
            ]

        select = discord.ui.Select(
            placeholder="Vyber hráče z klanu",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="clan_admin_select_member",
        )
        select.callback = self.on_select  # type: ignore
        self.add_item(select)

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

        select: discord.ui.Select = self.children[0]  # type: ignore
        if not select.values:
            await interaction.response.send_message(
                "Nebyl vybrán žádný hráč.",
                ephemeral=True,
            )
            return

        value = select.values[0]
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

        role = guild.get_role(CLAN_MEMBER_ROLE_ID) if CLAN_MEMBER_ROLE_ID else None
        if role is None or role not in member.roles:
            await interaction.response.send_message(
                "U vybraného hráče už clan role není (nebo role neexistuje).",
                ephemeral=True,
            )
            return

        try:
            await member.remove_roles(role, reason="Vyhozen z klanu přes admin panel")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Nemám oprávnění odebrat tuto roli. "
                "Zkontroluj `Manage Roles` a pořadí rolí (role bota musí být nad rolí klanu).",
                ephemeral=True,
            )
            return

        try:
            await member.send(
                f"Byl jsi **odstraněn z klanu** na serveru **{guild.name}**.\n"
                f"Pokud si myslíš, že jde o omyl, kontaktuj prosím administrátora."
            )
            dm_info = "Hráči byla odeslána soukromá zpráva o vyhození."
        except discord.Forbidden:
            dm_info = "Nepodařilo se odeslat DM hráči (má vypnuté soukromé zprávy)."

        await interaction.response.send_message(
            f"Hráči {member.mention} byla odebrána clan role. {dm_info}",
            ephemeral=True,
        )

        # refresh všech admin panelů (odebrání člena)
        await self.cog.refresh_admin_panels(guild)


async def setup(bot: commands.Bot):
    await bot.add_cog(ClanApplicationsCog(bot))
