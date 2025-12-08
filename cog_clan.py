import discord

TICKET_CATEGORY_ID = 1440977431577235456


class Components(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

        container = discord.ui.Container(

            # Nadpis
            discord.ui.TextDisplay(
                content="## PŘIHLÁŠKY DO CLANU"
            ),

            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),

            # 🇺🇸 blok
            discord.ui.TextDisplay(
                content="### 🇺🇸 Podmínky přijetí\n```\n- 2SP rebirths +\n- Play 24/7\n- 30% index\n- 10d playtime\n```"
            ),

            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),

            # 🇨🇿 blok
            discord.ui.TextDisplay(
                content="### 🇨🇿 Podmínky přijetí\n```\n- 2SP rebirthů +\n- Hrát 24/7\n- 30% index\n- 10d playtime\n```"
            ),

            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),

            # Select menu
            discord.ui.ActionRow(
                discord.ui.Select(
                    custom_id="clan_select",
                    placeholder="Vyber clan",
                    options=[
                        discord.SelectOption(label="HROT", value="HROT", description="🇨🇿 & 🇺🇸"),
                        discord.SelectOption(label="HR2T", value="HR2T", description="🇨🇿 only"),
                        discord.SelectOption(label="TGCM", value="TGCM", description="🇺🇸 only"),
                    ],
                )
            ),
        )

        # DŮLEŽITÉ – přidat container do view
        self.add_item(container)

    # handler pro select

    @discord.ui.select(custom_id="clan_select")
    async def select_callback(self, select: discord.ui.Select, interaction: discord.Interaction):

        guild = interaction.guild
        user = interaction.user
        clan = select.values[0]

        category = guild.get_channel(TICKET_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(
                "Kategorie ticketů není správně nastavena.",
                ephemeral=True
            )
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }

        safe = user.name.lower().replace(" ", "-")
        ch_name = f"🟠přihlášky-{clan}-{safe}"[:90]

        channel = await guild.create_text_channel(
            name=ch_name,
            category=category,
            overwrites=overwrites,
            reason=f"Přihláška do clanu {clan}"
        )

        await channel.send(
            f"{user.mention} otevřel ticket pro **{clan}**. Pošli screeny a info."
        )

        await interaction.response.send_message(
            f"Ticket vytvořen: {channel.mention}",
            ephemeral=True
        )
