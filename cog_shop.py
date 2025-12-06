from __future__ import annotations

from typing import Optional, Dict, Any, List

import discord
from discord.ext import commands
from discord import app_commands

from config import SETUP_MANAGER_ROLE_ID
from db import (
    create_shop_item,
    create_shop_purchase,
    set_shop_item_message,
    get_shop_item,
    decrement_shop_item_stock,
    get_active_shop_item_ids,
    complete_shop_purchase,
    complete_shop_purchases_for_user,
    get_pending_shop_purchases_grouped,
    get_or_create_user_stats,
    update_user_stats,
    get_setting,
    set_setting,
    get_pending_shop_sales_for_seller,
)

SHOP_MANAGER_ROLE_ID = 1_440_268_327_892_025_438


def _can_manage_shop(interaction: discord.Interaction) -> bool:
    user = interaction.user
    if isinstance(user, discord.Member):
        if user.guild_permissions.administrator:
            return True
        if any(role.id == SHOP_MANAGER_ROLE_ID for role in user.roles):
            return True

    # Fallback (včetně DM): ověříme role uživatele na jakémkoli serveru bota
    client = interaction.client
    if isinstance(client, commands.Bot):
        for guild in client.guilds:
            member = guild.get_member(user.id)
            if member is None:
                continue
            if member.guild_permissions.administrator:
                return True
            if any(role.id == SHOP_MANAGER_ROLE_ID for role in member.roles):
                return True

    return False


class ShopCog(commands.Cog, name="ShopCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # persistentní view pro všechny aktivní položky v shopu
        self._register_persistent_views()

    @staticmethod
    def _format_number(value: int) -> str:
        return f"{value:,}".replace(",", " ")

    def _build_shop_item_embed(
        self,
        title: str,
        price_coins: int,
        stock: int,
        image_url: Optional[str],
        seller_id: int,
    ) -> discord.Embed:
        available = stock > 0
        embed = discord.Embed(
            title=f"🛍️ {title}",
            description=(
                "Klikni na **Koupit** a vyplň počet kusů.\n"
                "• Platba proběhne okamžitě po potvrzení.\n"
                "• Po vyprodání bude nabídka skryta."
            )
            if available
            else "❌ Vyprodáno – položka je dočasně nedostupná.",
            color=0x00CCFF if available else 0x6E7985,
        )
        embed.add_field(
            name="Cena",
            value=f"**{self._format_number(price_coins)}** coinů",
            inline=True,
        )
        embed.add_field(
            name="Skladem",
            value=f"**{self._format_number(stock)} ks**",
            inline=True,
        )
        embed.add_field(name="Prodejce", value=f"<@{seller_id}>", inline=False)

        if image_url:
            embed.set_image(url=image_url)

        return embed

    def _find_user_guild(self, user: discord.abc.User) -> Optional[discord.Guild]:
        """Najde guildu, kde je uživatel členem (preferenčně s právy pro shop)."""

        candidate: Optional[discord.Guild] = None
        for guild in self.bot.guilds:
            member = guild.get_member(user.id)
            if member is None:
                continue
            # preferuj guildu, kde má uživatel oprávnění spravovat shop
            if member.guild_permissions.administrator or any(
                role.id == SHOP_MANAGER_ROLE_ID for role in member.roles
            ):
                return guild
            if candidate is None:
                candidate = guild

        # fallback: vrať první guildu kvůli formátování jmen
        return candidate

    def _register_persistent_views(self):
        item_ids = get_active_shop_item_ids()
        for item_id in item_ids:
            self.bot.add_view(ShopItemView(self, item_id))

    # ---------- SLASH COMMANDS ----------

    @app_commands.command(
        name="setupshop",
        description="Nastaví tento kanál jako roomku pro shop (admin).",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.checks.has_role(SETUP_MANAGER_ROLE_ID)
    async def setupshop_cmd(self, interaction: discord.Interaction):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Tento příkaz lze použít pouze v textovém kanálu.",
                ephemeral=True,
            )
            return

        set_setting("shop_channel_id", str(channel.id))
        await interaction.response.send_message(
            f"Tento kanál byl nastaven jako shop roomka: {channel.mention}",
            ephemeral=True,
        )

    @app_commands.command(
        name="addshopitem",
        description="Přidá položku do shopu (screen, cena, počet kusů).",
    )
    @app_commands.describe(
        title="Název položky",
        price_coins="Cena v coinech",
        stock="Počet kusů skladem",
        image="Screenshot / obrázek položky",
    )
    async def addshopitem_cmd(
        self,
        interaction: discord.Interaction,
        title: str,
        price_coins: app_commands.Range[int, 1, 10_000_000],
        stock: app_commands.Range[int, 1, 10_000],
        image: discord.Attachment,
    ):
        shop_channel_id_str = get_setting("shop_channel_id")
        if not shop_channel_id_str:
            await interaction.response.send_message(
                "Nejprve nastav shop roomku příkazem `/setupshop`.",
                ephemeral=True,
            )
            return

        try:
            shop_channel_id = int(shop_channel_id_str)
        except ValueError:
            await interaction.response.send_message(
                "Uložená shop roomka má neplatné ID.",
                ephemeral=True,
            )
            return

        channel = self.bot.get_channel(shop_channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Shop roomka není textový kanál nebo se nenašla.",
                ephemeral=True,
            )
            return

        image_url = image.url if image is not None else None

        item_id = create_shop_item(
            title=title,
            image_url=image_url,
            price_coins=int(price_coins),
            stock=int(stock),
            seller_id=interaction.user.id,
        )

        embed = self._build_shop_item_embed(
            title=title,
            price_coins=int(price_coins),
            stock=int(stock),
            image_url=image_url,
            seller_id=interaction.user.id,
        )

        view = ShopItemView(self, item_id)
        msg = await channel.send(embed=embed, view=view)

        set_shop_item_message(item_id, channel.id, msg.id)

        await interaction.response.send_message(
            f"Položka **{title}** byla přidána do shopu v {channel.mention}.",
            ephemeral=True,
        )

    @app_commands.command(
        name="shoporders",
        description="Zobrazí souhrn nevyřízených objednávek ze shopu.",
    )
    @app_commands.check(_can_manage_shop)
    async def shoporders_cmd(self, interaction: discord.Interaction):
        target_guild = interaction.guild or self._find_user_guild(interaction.user)
        view = ShopOrdersView(self, target_guild)
        embed = view.build_embed()

        try:
            dm_message = await interaction.user.send(embed=embed, view=view)
            view.message = dm_message
        except discord.Forbidden:
            await interaction.response.send_message(
                "Nepodařilo se odeslat DM – zkontroluj, zda máš povolené zprávy od členů serveru.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "Souhrn nevyřízených objednávek byl odeslán do tvých DM.",
            ephemeral=True,
        )

    @app_commands.command(
        name="see_sold_shop",
        description="Ukáže ti nevyřízené objednávky tvých prodaných položek.",
    )
    async def see_sold_shop_cmd(self, interaction: discord.Interaction):
        sales = get_pending_shop_sales_for_seller(interaction.user.id)
        if not sales:
            await interaction.response.send_message(
                "Nemáš žádné nehotové trady z tvých prodejů v shopu.",
                ephemeral=True,
            )
            return

        guild = interaction.guild or self._find_user_guild(interaction.user)

        def format_buyer(buyer_id: int) -> str:
            if guild:
                member = guild.get_member(buyer_id)
                if member is not None:
                    return f"{member.mention} ({member.display_name})"
            return f"<@{buyer_id}>"

        embed = discord.Embed(
            title="Nevyřízené objednávky tvých položek",
            color=0x00CCFF,
        )

        lines = []
        for sale in sales:
            buyer_text = format_buyer(sale["buyer_id"])
            lines.append(
                f"**{sale['title']}** – {sale['price_coins']} coinů ({sale['quantity']} ks)\n"
                f"Kupující: {buyer_text}"
            )

        embed.description = "\n\n".join(lines)
        embed.set_footer(text=f"Celkem čeká: {len(sales)} objednávek")

        await interaction.response.send_message(embed=embed, ephemeral=True)


class PurchaseQuantityModal(discord.ui.Modal):
    def __init__(
        self,
        cog: ShopCog,
        item_id: int,
        item_title: str,
        price_coins: int,
        parent_view: Optional[discord.ui.View] = None,
        parent_message: Optional[discord.Message] = None,
    ):
        super().__init__(title=f"Koupit: {item_title}")
        self.cog = cog
        self.item_id = item_id
        self.price_coins = price_coins
        self.parent_view = parent_view
        self.parent_message = parent_message
        self.add_item(
            discord.ui.TextInput(
                label="Počet kusů",
                placeholder="1",
                default="1",
                min_length=1,
                max_length=5,
            )
        )

    @property
    def quantity_input(self) -> discord.ui.TextInput:
        return self.children[0]  # type: ignore[return-value]

    async def on_submit(self, interaction: discord.Interaction):
        user = interaction.user
        raw_value = str(self.quantity_input.value).strip()
        try:
            quantity = int(raw_value)
        except ValueError:
            await interaction.response.send_message(
                "Zadej prosím platný počet kusů (celé číslo).",
                ephemeral=True,
            )
            return

        if quantity <= 0:
            await interaction.response.send_message(
                "Počet kusů musí být alespoň 1.",
                ephemeral=True,
            )
            return

        item = get_shop_item(self.item_id)
        if item is None or item["is_active"] == 0 or item["stock"] <= 0:
            await interaction.response.send_message(
                "Tato položka už není dostupná (vyprodáno nebo odstraněno).",
                ephemeral=True,
            )
            return

        if quantity > item["stock"]:
            await interaction.response.send_message(
                f"Nelze koupit {quantity} ks – skladem je pouze {item['stock']} ks.",
                ephemeral=True,
            )
            return

        buyer_id = user.id
        coins, exp, level, _last, _messages = get_or_create_user_stats(buyer_id)

        price_per_piece = item["price_coins"]
        image_url = item.get("image_url")
        total_price = price_per_piece * quantity
        if coins < total_price:
            await interaction.response.send_message(
                f"Nemáš dost coinů. Potřebuješ **{total_price}**, máš **{coins}**.",
                ephemeral=True,
            )
            return

        success, remaining_stock = decrement_shop_item_stock(self.item_id, quantity)
        if not success:
            await interaction.response.send_message(
                "Tuto položku už někdo těsně před tebou koupil – je vyprodána.",
                ephemeral=True,
            )
            return

        new_coins = coins - total_price
        update_user_stats(buyer_id, coins=new_coins)

        title = item["title"]
        seller_id = item["seller_id"]

        purchase_id = create_shop_purchase(
            item_id=self.item_id,
            buyer_id=buyer_id,
            seller_id=seller_id,
            price_coins=total_price,
            quantity=quantity,
        )

        buyer_display_name: str
        if isinstance(user, discord.Member):
            buyer_display_name = user.display_name
        elif interaction.guild is not None:
            member = interaction.guild.get_member(user.id)
            buyer_display_name = (
                member.display_name if member is not None else user.global_name or user.name
            )
        else:
            buyer_display_name = user.global_name or user.name

        seller_display = f"<@{seller_id}>"
        seller_user = self.cog.bot.get_user(seller_id)
        if seller_user is not None:
            seller_display = seller_user.mention
        else:
            for guild in self.cog.bot.guilds:
                member = guild.get_member(seller_id)
                if member is not None:
                    seller_user = member
                    seller_display = member.mention
                    break

        try:
            if seller_user is not None:
                seller_view = PurchaseCompleteView(
                    self.cog, purchase_id=purchase_id, seller_id=seller_id
                )
                await seller_user.send(
                    f"🛒 Položka **{title}** byla právě koupena uživatelem {user.mention} "
                    f"({buyer_display_name}) za **{total_price}** coinů ({quantity} ks). "
                    f"Zbývající kusy: **{remaining_stock}**.\n"
                    "Klikni na **Hotovo**, až objednávku vyřídíš.",
                    view=seller_view,
                )
        except discord.Forbidden:
            pass

        try:
            await user.send(
                f"✅ Koupil jsi si položku **{title}** ({quantity} ks) za **{total_price}** coinů.\n"
                f"Prodejce: {seller_display}\n"
                f"Zůstatek: **{new_coins}** coinů."
            )
        except discord.Forbidden:
            pass

        message = self.parent_message or interaction.message
        view = self.parent_view
        if message and view:
            if remaining_stock <= 0:
                try:
                    await message.delete()
                except discord.Forbidden:
                    for child in view.children:
                        child.disabled = True
                    embed = self.cog._build_shop_item_embed(
                        title,
                        price_per_piece,
                        remaining_stock,
                        image_url,
                        seller_id,
                    )
                    await message.edit(embed=embed, view=view)
            else:
                embed = self.cog._build_shop_item_embed(
                    title,
                    price_per_piece,
                    remaining_stock,
                    image_url,
                    seller_id,
                )
                await message.edit(embed=embed, view=view)

        await interaction.response.send_message(
            f"Koupil jsi **{quantity}× {title}** za **{total_price}** coinů.",
            ephemeral=True,
        )


class BuyButton(discord.ui.Button):
    def __init__(self, cog: ShopCog, item_id: int):
        super().__init__(
            label="Koupit",
            style=discord.ButtonStyle.primary,
            custom_id=f"shop_buy_{item_id}",
        )
        self.cog = cog
        self.item_id = item_id

    async def callback(self, interaction: discord.Interaction):
        user = interaction.user
        if user.bot:
            await interaction.response.send_message(
                "Bot nemůže nakupovat.",
                ephemeral=True,
            )
            return

        # Načtení položky z DB
        item = get_shop_item(self.item_id)
        if item is None or item["is_active"] == 0 or item["stock"] <= 0:
            await interaction.response.send_message(
                "Tato položka už není dostupná (vyprodáno nebo odstraněno).",
                ephemeral=True,
            )
            return

        modal = PurchaseQuantityModal(
            self.cog,
            self.item_id,
            item["title"],
            item["price_coins"],
            parent_view=self.view,
            parent_message=interaction.message,
        )
        await interaction.response.send_modal(modal)


class ShopItemView(discord.ui.View):
    def __init__(self, cog: ShopCog, item_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(BuyButton(cog, item_id))


class PurchaseCompleteButton(discord.ui.Button):
    def __init__(self, cog: ShopCog, purchase_id: int, seller_id: int):
        super().__init__(
            label="Hotovo", style=discord.ButtonStyle.success, custom_id=f"shop_done_{purchase_id}"
        )
        self.cog = cog
        self.purchase_id = purchase_id
        self.seller_id = seller_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.seller_id and not _can_manage_shop(interaction):
            await interaction.response.send_message(
                "Tuto objednávku může označit pouze prodejce nebo manažer shopu.",
                ephemeral=True,
            )
            return

        if not complete_shop_purchase(self.purchase_id):
            await interaction.response.send_message(
                "Objednávka už byla označena jako hotová.", ephemeral=True
            )
            return

        self.disabled = True
        self.label = "Hotovo ✅"
        self.style = discord.ButtonStyle.secondary
        if interaction.message:
            try:
                await interaction.message.edit(view=self.view)
            except discord.HTTPException:
                pass
        await interaction.response.send_message("Objednávka označena jako vyřízená.", ephemeral=True)


class PurchaseCompleteView(discord.ui.View):
    def __init__(self, cog: ShopCog, purchase_id: int, seller_id: int):
        super().__init__(timeout=None)
        self.add_item(PurchaseCompleteButton(cog, purchase_id, seller_id))


class CompleteBuyerOrdersButton(discord.ui.Button):
    def __init__(self, view: ShopOrdersView, buyer_id: int, label: str):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.parent_view = view
        self.buyer_id = buyer_id

    async def callback(self, interaction: discord.Interaction):
        if not _can_manage_shop(interaction):
            await interaction.response.send_message(
                "Nemáš oprávnění spravovat objednávky v shopu.", ephemeral=True
            )
            return

        completed = complete_shop_purchases_for_user(self.buyer_id)
        if completed == 0:
            await interaction.response.send_message(
                "Žádné čekající objednávky k označení.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"Označeno jako hotové: **{completed}** objednávek.", ephemeral=True
        )
        await self.parent_view.refresh(interaction)


class ShopOrdersView(discord.ui.View):
    def __init__(self, cog: ShopCog, guild: Optional[discord.Guild]):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.message: Optional[discord.Message] = None
        self.pending: List[Dict[str, Any]] = []
        self._refresh_buttons()

    def _load_pending(self):
        self.pending = get_pending_shop_purchases_grouped()

    def _format_member(self, buyer_id: int) -> str:
        if self.guild:
            member = self.guild.get_member(buyer_id)
            if member is not None:
                return f"{member.mention} ({member.display_name})"
        return f"<@{buyer_id}>"

    def _refresh_buttons(self):
        self._load_pending()
        self.clear_items()
        for entry in self.pending[:25]:
            base_label = f"{entry['count']}× {self._format_member(entry['buyer_id'])}"
            label = base_label if len(base_label) <= 80 else base_label[:77] + "..."
            button = CompleteBuyerOrdersButton(self, buyer_id=entry["buyer_id"], label=label)
            button.emoji = "✅"
            self.add_item(button)

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Nevyřízené objednávky shopu",
            description="",
            color=0x00CCFF,
        )
        if not self.pending:
            embed.description = "Žádné nevyřízené objednávky."
            return embed

        lines = []
        total = 0
        for entry in self.pending:
            buyer_text = self._format_member(entry["buyer_id"])
            count = entry["count"]
            total += count
            lines.append(f"{buyer_text}: **{count}** ks")

        embed.description = "\n".join(lines)
        embed.set_footer(text=f"Celkem čeká: {total} položek")
        return embed

    async def refresh(self, interaction: discord.Interaction):
        self._refresh_buttons()
        embed = self.build_embed()
        target_message = interaction.message or self.message
        if target_message is None:
            try:
                target_message = await interaction.original_response()
            except discord.NotFound:
                return
        self.message = target_message
        await target_message.edit(embed=embed, view=self)


async def setup(bot: commands.Bot):
    await bot.add_cog(ShopCog(bot))
