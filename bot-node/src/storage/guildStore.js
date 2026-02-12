import { promises as fs } from 'node:fs';
import path from 'node:path';

const DATA_DIR = path.resolve(process.cwd(), 'bot-node/data');
const DATA_FILE = path.join(DATA_DIR, 'guild-state.json');

export class GuildStore {
  constructor(initialState = {}) {
    this.state = initialState;
  }

  static async bootstrap() {
    await fs.mkdir(DATA_DIR, { recursive: true });

    try {
      const raw = await fs.readFile(DATA_FILE, 'utf8');
      return new GuildStore(JSON.parse(raw));
    } catch (error) {
      if (error.code === 'ENOENT') {
        return new GuildStore({});
      }
      throw error;
    }
  }

  getGuild(guildId) {
    if (!this.state[guildId]) {
      this.state[guildId] = { settings: {}, runtime: {} };
    }
    return this.state[guildId];
  }

  async patchGuild(guildId, patch) {
    const current = this.getGuild(guildId);
    this.state[guildId] = {
      ...current,
      ...patch,
      settings: {
        ...(current.settings ?? {}),
        ...(patch.settings ?? {})
      },
      runtime: {
        ...(current.runtime ?? {}),
        ...(patch.runtime ?? {})
      }
    };

    await this.flush();
    return this.state[guildId];
  }

  async flush() {
    await fs.writeFile(DATA_FILE, JSON.stringify(this.state, null, 2), 'utf8');
  }
}
