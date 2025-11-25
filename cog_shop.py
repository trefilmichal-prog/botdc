from __future__ import annotations

from typing import Optional, Dict, Any, List

import discord
from discord.ext import commands
from discord import app_commands

from db import (
    create_shop_item,
    set_shop_item_message,
    get_shop_item,
    decrement_shop_item_stock,
    get_active_shop_item_ids,
    get_or_create_user_stats,
    update_user_stats,
    get_setting,
    set_setting,
)


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
    @app_commands.checks.has_permissions(administrator=True)
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
        coins, exp, level, _last = get_or_create_user_stats(buyer_id)

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
                await seller_user.send(
                    f"🛒 Položka **{title}** byla právě koupena uživatelem {user.mention} "
                    f"za **{price}** coinů. Zbývající kusy: **{remaining_stock}**."
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


async def setup(bot: commands.Bot):
    await bot.add_cog(ShopCog(bot))
