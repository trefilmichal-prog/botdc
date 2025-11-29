from __future__ import annotations

from typing import Optional, Dict, Any, List

import discord
from discord.ext import commands
from discord import app_commands

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
)

SHOP_MANAGER_ROLE_ID = 1_440_268_327_892_025_438


def _can_manage_shop(interaction: discord.Interaction) -> bool:
    user = interaction.user
    if isinstance(user, discord.Member):
        if user.guild_permissions.administrator:
            return True
        return any(role.id == SHOP_MANAGER_ROLE_ID for role in user.roles)

    return False


class ShopCog(commands.Cog, name="ShopCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # persistentní view pro všechny aktivní položky v shopu
        self._register_persistent_views()

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
    @app_commands.check(_can_manage_shop)
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

        embed = discord.Embed(
            title=title,
            description=f"Cena: **{price_coins}** coinů\nSkladem: **{stock}** ks",
            color=0x00CCFF,
        )
        if image_url:
            embed.set_image(url=image_url)

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
        if interaction.guild is None:
            await interaction.response.send_message(
                "Tento příkaz lze použít jen na serveru.", ephemeral=True
            )
            return

        view = ShopOrdersView(self, interaction.guild)
        embed = view.build_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()


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

        buyer_id = user.id
        coins, exp, level, _last, _messages = get_or_create_user_stats(buyer_id)

        price = item["price_coins"]
        if coins < price:
            await interaction.response.send_message(
                f"Nemáš dost coinů. Potřebuješ **{price}**, máš **{coins}**.",
                ephemeral=True,
            )
            return

        # Nejprve zkusíme odečíst sklad (aby dva nekoupili poslední kus)
        success, remaining_stock = decrement_shop_item_stock(self.item_id)
        if not success:
            await interaction.response.send_message(
                "Tuto položku už někdo těsně před tebou koupil – je vyprodána.",
                ephemeral=True,
            )
            return

        # Odečtení coinů kupujícímu
        new_coins = coins - price
        update_user_stats(buyer_id, coins=new_coins)

        title = item["title"]
        seller_id = item["seller_id"]

        purchase_id = create_shop_purchase(
            item_id=self.item_id,
            buyer_id=buyer_id,
            seller_id=seller_id,
            price_coins=price,
        )

        buyer_display_name: str
        if isinstance(user, discord.Member):
            buyer_display_name = user.display_name
        elif interaction.guild is not None:
            member = interaction.guild.get_member(user.id)
            buyer_display_name = member.display_name if member is not None else user.global_name or user.name
        else:
            buyer_display_name = user.global_name or user.name

        # DM prodejci
        seller_user = self.cog.bot.get_user(seller_id)
        if seller_user is None:
            for guild in self.cog.bot.guilds:
                member = guild.get_member(seller_id)
                if member is not None:
                    seller_user = member
                    break

        try:
            if seller_user is not None:
                seller_view = PurchaseCompleteView(
                    self.cog, purchase_id=purchase_id, seller_id=seller_id
                )
                await seller_user.send(
                    f"🛒 Položka **{title}** byla právě koupena uživatelem {user.mention} "
                    f"({buyer_display_name}) za **{price}** coinů. Zbývající kusy: "
                    f"**{remaining_stock}**.\nKlikni na **Hotovo**, až objednávku vyřídíš.",
                    view=seller_view,
                )
        except discord.Forbidden:
            pass

        # DM kupujícímu
        try:
            await user.send(
                f"✅ Koupil jsi si položku **{title}** za **{price}** coinů.\n"
                f"Zůstatek: **{new_coins}** coinů."
            )
        except discord.Forbidden:
            pass

        # Aktualizace zprávy v shopu
        message = interaction.message
        if message:
            if remaining_stock <= 0:
                # Vyprodáno – pokus o smazání zprávy
                try:
                    await message.delete()
                except discord.Forbidden:
                    # fallback – vypneme tlačítko a upravíme embed
                    for child in self.view.children:
                        child.disabled = True
                    embed = message.embeds[0] if message.embeds else discord.Embed()
                    embed = embed.copy()
                    embed.description = f"**{title}** – vyprodáno."
                    await message.edit(embed=embed, view=self.view)
            else:
                # jen aktualizace skladu v embedu
                embed = message.embeds[0] if message.embeds else discord.Embed()
                embed = embed.copy()
                embed.title = title
                embed.description = (
                    f"Cena: **{price}** coinů\n"
                    f"Skladem: **{remaining_stock}** ks"
                )
                await message.edit(embed=embed, view=self.view)

        await interaction.response.send_message(
            f"Koupil jsi **{title}** za **{price}** coinů.",
            ephemeral=True,
        )


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
        if self.message is None:
            try:
                self.message = await interaction.original_response()
            except discord.NotFound:
                return
        await self.message.edit(embed=embed, view=self)


async def setup(bot: commands.Bot):
    await bot.add_cog(ShopCog(bot))
