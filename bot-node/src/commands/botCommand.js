import { SlashCommandBuilder } from 'discord.js';
import { legacyMapping, renderMappingPreview } from './legacyMapping.js';

const commandTree = {
  basic: ['help'],
  admin: ['kick', 'ban', 'warn', 'mute', 'setnick'],
  xp: ['profile', 'give-coins'],
  timers: ['setup', 'set', 'remove'],
  shop: ['setup', 'add-item', 'orders', 'sold'],
  wood: ['setup-panel', 'set-need', 'reset-need', 'resources'],
  giveaway: ['setup', 'start'],
  attendance: ['setup-ready-panel'],
  leaderboard: ['show', 'setup-clan-room', 'setup-board'],
  'clan-stats': ['setup-room', 'dm'],
  prophecy: ['ask'],
  updater: ['run'],
  roblox: ['activity', 'tracking', 'leaderboard', 'cookie'],
  welcome: ['config', 'test'],
  'clan-panel': ['config', 'publish'],
  'log-settings': ['set', 'show'],
  secret: ['config', 'status'],
  state: ['set', 'show'],
  legacy: ['mapping']
};

const descriptions = {
  set: 'Set key/value in per-guild persisted state.',
  show: 'Show current per-guild persisted state.',
  mapping: 'Show mapping from Python command surface to the Node subcommands.'
};

const dataBuilder = new SlashCommandBuilder()
  .setName('bot')
  .setDescription('Unified Node command surface grouped as subcommands.');

for (const [groupName, subcommands] of Object.entries(commandTree)) {
  dataBuilder.addSubcommandGroup((group) => {
    group.setName(groupName).setDescription(`${groupName} commands`);

    subcommands.forEach((subcommandName) => {
      group.addSubcommand((subcommand) => {
        subcommand
          .setName(subcommandName)
          .setDescription(descriptions[subcommandName] ?? `${groupName}/${subcommandName}`);

        if (groupName === 'state' && subcommandName === 'set') {
          subcommand
            .addStringOption((option) =>
              option
                .setName('key')
                .setDescription('State key')
                .setRequired(true)
            )
            .addStringOption((option) =>
              option
                .setName('value')
                .setDescription('State value')
                .setRequired(true)
            );
        }

        return subcommand;
      });
    });

    return group;
  });
}

export const botCommand = {
  data: dataBuilder,
  async execute(interaction, context) {
    const group = interaction.options.getSubcommandGroup();
    const sub = interaction.options.getSubcommand();

    if (!interaction.guildId) {
      await interaction.reply({ content: 'This command only works in guilds.', ephemeral: true });
      return;
    }

    if (group === 'state' && sub === 'set') {
      const key = interaction.options.getString('key', true);
      const value = interaction.options.getString('value', true);

      await context.store.patchGuild(interaction.guildId, {
        settings: { [key]: value },
        runtime: { lastUpdatedBy: interaction.user.id, lastUpdatedAt: new Date().toISOString() }
      });

      await interaction.reply({ content: `Saved ${key} for this guild.` });
      return;
    }

    if (group === 'state' && sub === 'show') {
      const state = context.store.getGuild(interaction.guildId);
      await interaction.reply({
        content: `Persisted state for guild ${interaction.guildId}:\n\`\`\`json\n${JSON.stringify(state, null, 2)}\n\`\`\``
      });
      return;
    }

    if (group === 'legacy' && sub === 'mapping') {
      await interaction.reply({
        content: `Python → Node mapping (${legacyMapping.length} entries):\n${renderMappingPreview()}`
      });
      return;
    }

    await interaction.reply({
      content: `Stub: /${interaction.commandName} ${group} ${sub} is registered. Implement handler in bot-node/src/commands/botCommand.js.`
    });
  }
};
