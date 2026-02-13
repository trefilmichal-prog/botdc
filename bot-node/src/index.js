import process from 'node:process';
import {
  Client,
  Collection,
  Events,
  GatewayIntentBits,
  REST,
  Routes
} from 'discord.js';
import { botCommand } from './commands/botCommand.js';
import { GuildStore } from './storage/guildStore.js';

const token = process.env.DISCORD_TOKEN;
const clientId = process.env.DISCORD_CLIENT_ID;
const guildId = process.env.DISCORD_GUILD_ID;

if (!token || !clientId) {
  throw new Error('Missing DISCORD_TOKEN or DISCORD_CLIENT_ID environment variables.');
}

const store = await GuildStore.bootstrap();
console.log(`[bootstrap] Loaded persisted state for ${Object.keys(store.state).length} guild(s).`);

const commands = [botCommand];
const commandRouter = new Collection(commands.map((command) => [command.data.name, command]));

const client = new Client({ intents: [GatewayIntentBits.Guilds] });

client.once(Events.ClientReady, async (readyClient) => {
  console.log(`[ready] Logged in as ${readyClient.user.tag}`);

  const rest = new REST({ version: '10' }).setToken(token);
  const payload = commands.map((command) => command.data.toJSON());

  if (guildId) {
    await rest.put(Routes.applicationGuildCommands(clientId, guildId), { body: payload });
    console.log(`[commands] Registered ${payload.length} guild command(s) for ${guildId}.`);
  } else {
    await rest.put(Routes.applicationCommands(clientId), { body: payload });
    console.log(`[commands] Registered ${payload.length} global command(s).`);
  }
});

client.on(Events.InteractionCreate, async (interaction) => {
  if (!interaction.isChatInputCommand()) return;

  const command = commandRouter.get(interaction.commandName);
  if (!command) return;

  try {
    await command.execute(interaction, { store, client });
  } catch (error) {
    console.error('[interaction] command failed', error);
    if (interaction.replied || interaction.deferred) {
      await interaction.followUp({ content: 'Command failed.', ephemeral: true });
      return;
    }
    await interaction.reply({ content: 'Command failed.', ephemeral: true });
  }
});

await client.login(token);
