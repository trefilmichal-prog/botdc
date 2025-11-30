<?php
/**
 * Jednoduchý konfigurační panel pro správu bota a překladů.
 *
 * Ukládá data do souborů config/settings.json a config/translations.json.
 */

const CONFIG_DIR = __DIR__ . '/config';
const SETTINGS_FILE = CONFIG_DIR . '/settings.json';
const TRANSLATIONS_FILE = CONFIG_DIR . '/translations.json';

function load_json(string $path, array $fallback = []): array
{
    if (!is_readable($path)) {
        return $fallback;
    }

    $data = json_decode((string) file_get_contents($path), true);

    return is_array($data) ? $data : $fallback;
}

function save_json(string $path, array $payload): bool
{
    $json = json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE);

    if ($json === false) {
        return false;
    }

    return (bool) file_put_contents($path, $json);
}

function available_languages(array $translations): array
{
    $languages = [];
    foreach ($translations as $value) {
        if (is_array($value)) {
            $languages = array_merge($languages, array_keys($value));
        }
    }

    $languages = array_values(array_unique($languages));
    sort($languages);

    return $languages;
}

$statusMessage = null;
$statusVariant = 'info';

if (!is_dir(CONFIG_DIR) && !mkdir(CONFIG_DIR, 0775, true) && !is_dir(CONFIG_DIR)) {
    $statusMessage = 'Nepodařilo se vytvořit adresář pro konfiguraci (config). Zkontrolujte oprávnění.';
    $statusVariant = 'error';
}

$settings = load_json(SETTINGS_FILE);
$translations = load_json(TRANSLATIONS_FILE);

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $settings = $_POST['settings'] ?? [];
    $translations = $_POST['translations'] ?? [];

    $settingsSaved = save_json(SETTINGS_FILE, $settings);
    $translationsSaved = save_json(TRANSLATIONS_FILE, $translations);

    if ($settingsSaved && $translationsSaved) {
        $statusMessage = 'Nastavení a překlady byly úspěšně uloženy.';
        $statusVariant = 'success';
    } else {
        $statusMessage = 'Nepodařilo se uložit data, zkontrolujte oprávnění.';
        $statusVariant = 'error';
    }
}

$languages = available_languages($translations);
?>
<!DOCTYPE html>
<html lang="cs">
<head>
    <meta charset="UTF-8">
    <title>Konfigurace Discord bota</title>
    <style>
        :root {
            color-scheme: light dark;
        }
        body {
            font-family: "Inter", system-ui, -apple-system, sans-serif;
            margin: 0 auto;
            padding: 2rem;
            max-width: 1100px;
            background: #0f172a;
            color: #e2e8f0;
        }
        h1, h2 {
            margin-top: 0;
        }
        .panel {
            background: #111827;
            border: 1px solid #1f2937;
            border-radius: 12px;
            padding: 1.5rem;
            margin-bottom: 1.5rem;
            box-shadow: 0 10px 30px rgba(0,0,0,0.25);
        }
        label {
            display: block;
            margin-bottom: .35rem;
            font-weight: 600;
        }
        input[type="text"], input[type="number"], textarea {
            width: 100%;
            padding: .75rem 1rem;
            border-radius: 10px;
            border: 1px solid #1f2937;
            background: #0b1222;
            color: #e2e8f0;
            box-sizing: border-box;
        }
        input[type="text"]:focus, input[type="number"]:focus, textarea:focus {
            outline: 2px solid #22d3ee;
            border-color: #22d3ee;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
            gap: 1rem;
        }
        .actions {
            display: flex;
            gap: .75rem;
            flex-wrap: wrap;
        }
        button {
            border: none;
            padding: .75rem 1.25rem;
            border-radius: 10px;
            cursor: pointer;
            font-weight: 700;
        }
        .primary {
            background: linear-gradient(135deg, #22d3ee, #3b82f6);
            color: #0b1222;
        }
        .ghost {
            background: transparent;
            border: 1px dashed #334155;
            color: #e2e8f0;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 1rem;
        }
        th, td {
            border: 1px solid #1f2937;
            padding: .6rem;
            text-align: left;
        }
        th {
            background: #0b1222;
        }
        .status {
            padding: .75rem 1rem;
            border-radius: 10px;
            margin-bottom: 1rem;
            font-weight: 700;
        }
        .status.info { background: #0ea5e9; color: #0b1222; }
        .status.success { background: #22c55e; color: #052e16; }
        .status.error { background: #fca5a5; color: #450a0a; }
        .muted {
            color: #94a3b8;
        }
    </style>
</head>
<body>
    <h1>Konfigurace Discord bota</h1>
    <p class="muted">Tento panel uloží nastavení i překlady do JSON souborů v adresáři <code>config</code>.</p>
    <?php if ($statusMessage): ?>
        <div class="status <?php echo htmlspecialchars($statusVariant, ENT_QUOTES, 'UTF-8'); ?>">
            <?php echo htmlspecialchars($statusMessage, ENT_QUOTES, 'UTF-8'); ?>
        </div>
    <?php endif; ?>
    <form method="post">
        <div class="panel">
            <h2>Základní nastavení</h2>
            <p class="muted">Přidejte nové položky pro konfiguraci bota nebo upravte stávající hodnoty.</p>
            <div class="actions" style="margin-bottom:1rem;">
                <input type="text" id="newSettingKey" placeholder="např. NEW_OPTION">
                <input type="text" id="newSettingValue" placeholder="Hodnota">
                <button type="button" class="ghost" onclick="addSetting()">Přidat položku</button>
            </div>
            <div class="grid" id="settingsGrid">
                <?php if (empty($settings)): ?>
                    <p class="muted">Žádné položky zatím nejsou k dispozici. Přidejte první pomocí tlačítka výše.</p>
                <?php else: ?>
                    <?php foreach ($settings as $key => $value): ?>
                        <div class="setting-field" data-key="<?php echo htmlspecialchars($key, ENT_QUOTES, 'UTF-8'); ?>">
                            <label for="settings_<?php echo htmlspecialchars($key, ENT_QUOTES, 'UTF-8'); ?>"><?php echo htmlspecialchars($key, ENT_QUOTES, 'UTF-8'); ?></label>
                            <input type="text" name="settings[<?php echo htmlspecialchars($key, ENT_QUOTES, 'UTF-8'); ?>]" id="settings_<?php echo htmlspecialchars($key, ENT_QUOTES, 'UTF-8'); ?>" value="<?php echo htmlspecialchars((string) $value, ENT_QUOTES, 'UTF-8'); ?>">
                        </div>
                    <?php endforeach; ?>
                <?php endif; ?>
            </div>
        </div>

        <div class="panel">
            <h2>Překlady</h2>
            <p class="muted">Přidejte nové jazyky nebo klíče a upravte texty pro bota.</p>
            <div class="actions">
                <input type="text" id="newLanguage" placeholder="např. de">
                <button type="button" class="ghost" onclick="addLanguage()">Přidat jazyk</button>
                <input type="text" id="newKey" placeholder="např. welcome_message">
                <button type="button" class="ghost" onclick="addKey()">Přidat nový klíč</button>
            </div>
            <table id="translationsTable">
                <thead>
                    <tr>
                        <th>Klíč</th>
                        <?php foreach ($languages as $lang): ?>
                            <th><?php echo htmlspecialchars($lang, ENT_QUOTES, 'UTF-8'); ?></th>
                        <?php endforeach; ?>
                    </tr>
                </thead>
                <tbody>
                    <?php foreach ($translations as $key => $localized): ?>
                        <tr data-key="<?php echo htmlspecialchars($key, ENT_QUOTES, 'UTF-8'); ?>">
                            <td><strong><?php echo htmlspecialchars($key, ENT_QUOTES, 'UTF-8'); ?></strong></td>
                            <?php foreach ($languages as $lang): ?>
                                <td>
                                    <input type="text" name="translations[<?php echo htmlspecialchars($key, ENT_QUOTES, 'UTF-8'); ?>][<?php echo htmlspecialchars($lang, ENT_QUOTES, 'UTF-8'); ?>]" value="<?php echo htmlspecialchars($localized[$lang] ?? '', ENT_QUOTES, 'UTF-8'); ?>">
                                </td>
                            <?php endforeach; ?>
                        </tr>
                    <?php endforeach; ?>
                </tbody>
            </table>
        </div>

        <div class="actions">
            <button type="submit" class="primary">Uložit změny</button>
            <button type="reset" class="ghost">Reset formuláře</button>
        </div>
    </form>

    <script>
        const languages = <?php echo json_encode($languages, JSON_UNESCAPED_UNICODE); ?>;
        const table = document.getElementById('translationsTable');
        const tbody = table.querySelector('tbody');
        const settingsGrid = document.getElementById('settingsGrid');

        function createInput(key, lang, value = '') {
            const input = document.createElement('input');
            input.type = 'text';
            input.name = `translations[${key}][${lang}]`;
            input.value = value;
            return input;
        }

        function createSettingField(key, value = '') {
            const wrapper = document.createElement('div');
            wrapper.className = 'setting-field';
            wrapper.dataset.key = key;

            const label = document.createElement('label');
            label.setAttribute('for', `settings_${key}`);
            label.textContent = key;

            const input = document.createElement('input');
            input.type = 'text';
            input.name = `settings[${key}]`;
            input.id = `settings_${key}`;
            input.value = value;

            wrapper.appendChild(label);
            wrapper.appendChild(input);

            return wrapper;
        }

        function addLanguage() {
            const newLang = document.getElementById('newLanguage').value.trim();
            if (!newLang || languages.includes(newLang)) return;
            languages.push(newLang);

            const headerCell = document.createElement('th');
            headerCell.textContent = newLang;
            table.querySelector('thead tr').appendChild(headerCell);

            tbody.querySelectorAll('tr').forEach(row => {
                const key = row.dataset.key;
                const cell = document.createElement('td');
                cell.appendChild(createInput(key, newLang));
                row.appendChild(cell);
            });

            document.getElementById('newLanguage').value = '';
        }

        function addKey() {
            const newKey = document.getElementById('newKey').value.trim();
            if (!newKey) return;
            if (tbody.querySelector(`[data-key="${newKey}"]`)) return;

            const row = document.createElement('tr');
            row.dataset.key = newKey;
            const keyCell = document.createElement('td');
            keyCell.innerHTML = `<strong>${newKey}</strong>`;
            row.appendChild(keyCell);

            languages.forEach(lang => {
                const cell = document.createElement('td');
                cell.appendChild(createInput(newKey, lang));
                row.appendChild(cell);
            });

            tbody.appendChild(row);
            document.getElementById('newKey').value = '';
        }

        function addSetting() {
            const key = document.getElementById('newSettingKey').value.trim();
            const value = document.getElementById('newSettingValue').value;
            if (!key || settingsGrid.querySelector(`[data-key="${key}"]`)) return;

            const placeholder = settingsGrid.querySelector('p.muted');
            if (placeholder) {
                placeholder.remove();
            }

            const field = createSettingField(key, value);
            settingsGrid.appendChild(field);
            document.getElementById('newSettingKey').value = '';
            document.getElementById('newSettingValue').value = '';
        }
    </script>
</body>
</html>
