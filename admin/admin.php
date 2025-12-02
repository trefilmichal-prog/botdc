<?php
session_start();
if(!isset($_SESSION['login'])) { header("Location: index.php"); exit; }

$db = new PDO('sqlite:database.sqlite');

// Ensure settings table exists for storing credentials/token/guild ID
$db->exec("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)");

// Discord guild/role configuration
$roleId = '1440268327892025438';
$discordTokenEnv = getenv('DISCORD_TOKEN');
$guildIdEnv = getenv('DISCORD_GUILD_ID');
$adminRow = $db->query("SELECT * FROM admins ORDER BY id LIMIT 1")->fetch(PDO::FETCH_ASSOC);
$discordTokenStored = get_setting($db, 'discord_token');
$guildIdStored = get_setting($db, 'discord_guild_id');
$discordToken = $discordTokenStored ? $discordTokenStored : $discordTokenEnv;
$guildId = $guildIdStored ? $guildIdStored : $guildIdEnv;

$notices = array();
$errors = array();

function get_setting($db, $key) {
    $stmt = $db->prepare("SELECT value FROM settings WHERE key = ?");
    $stmt->execute(array($key));
    $row = $stmt->fetch(PDO::FETCH_ASSOC);
    return $row ? $row['value'] : null;
}

function set_setting($db, $key, $value) {
    $stmt = $db->prepare("REPLACE INTO settings (key, value) VALUES (?, ?)");
    $stmt->execute(array($key, $value));
}

function fetch_guild_members($guildId, $token) {
    $members = array();
    $after = '0';
    $limit = 1000;

    do {
        $url = "https://discord.com/api/v10/guilds/{$guildId}/members?limit={$limit}&after={$after}";
        $ch = curl_init($url);
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($ch, CURLOPT_HTTPHEADER, array(
            "Authorization: Bot {$token}",
            'User-Agent: ezrz-dcbot-admin'
        ));

        $response = curl_exec($ch);
        $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);

        if($httpCode !== 200 || $response === false) {
            break;
        }

        $batch = json_decode($response, true);
        if(!is_array($batch)) {
            break;
        }

        $members = array_merge($members, $batch);
        $count = count($batch);
        if($count < $limit) {
            break;
        }

        $last = end($batch);
        if(!$last || !isset($last['user']['id'])) {
            break;
        }
        $after = $last['user']['id'];
    } while(true);

    return $members;
}

function filter_members_with_role($members, $roleId) {
    $result = array();
    foreach($members as $member) {
        if(isset($member['roles']) && in_array($roleId, $member['roles'])) {
            $username = isset($member['user']['username']) ? $member['user']['username'] : 'Unknown';
            $display = isset($member['nick']) && $member['nick'] !== null ? $member['nick'] : $username;

            $result[] = array(
                'id' => $member['user']['id'],
                'username' => $username,
                'display' => $display
            );
        }
    }

    usort($result, function($a, $b) {
        return strcasecmp($a['display'], $b['display']);
    });

    return $result;
}

if(isset($_POST['update_credentials'])) {
    $newUsername = isset($_POST['new_username']) ? trim($_POST['new_username']) : '';
    $newPassword = isset($_POST['new_password']) ? $_POST['new_password'] : '';

    if($newUsername !== '' && $newPassword !== '') {
        $hashed = md5($newPassword);
        if($adminRow) {
            $update = $db->prepare("UPDATE admins SET username = ?, password = ? WHERE id = ?");
            $update->execute(array($newUsername, $hashed, $adminRow['id']));
        } else {
            $insert = $db->prepare("INSERT INTO admins (username, password) VALUES (?, ?)");
            $insert->execute(array($newUsername, $hashed));
        }
        $adminRow = $db->query("SELECT * FROM admins ORDER BY id LIMIT 1")->fetch(PDO::FETCH_ASSOC);
        $notices[] = "Přihlašovací údaje byly aktualizovány.";
    } else {
        $errors[] = "Vyplňte prosím nové uživatelské jméno i heslo.";
    }
}

if(isset($_POST['save_token'])) {
    $tokenValue = isset($_POST['discord_token']) ? trim($_POST['discord_token']) : '';
    if($tokenValue !== '') {
        set_setting($db, 'discord_token', $tokenValue);
        $discordTokenStored = $tokenValue;
        $discordToken = $tokenValue;
        $notices[] = "Discord token byl uložen do databáze.";
    } else {
        $errors[] = "Token nemůže být prázdný.";
    }
}

if(isset($_POST['save_guild'])) {
    $guildValue = isset($_POST['discord_guild_id']) ? trim($_POST['discord_guild_id']) : '';
    if($guildValue !== '') {
        set_setting($db, 'discord_guild_id', $guildValue);
        $guildIdStored = $guildValue;
        $guildId = $guildValue;
        $notices[] = "DISCORD_GUILD_ID byl uložen do databáze.";
    } else {
        $errors[] = "Guild ID nemůže být prázdné.";
    }
}
?>
<!DOCTYPE html>
<html>
<head>
    <title>Admin Panel</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            margin: 0;
            padding: 0;
        }
        header {
            background: linear-gradient(135deg, #4f46e5, #0ea5e9);
            padding: 24px;
            text-align: center;
            box-shadow: 0 4px 12px rgba(0,0,0,0.2);
        }
        h1 {
            margin: 0;
            font-size: 26px;
            letter-spacing: 0.5px;
        }
        .container {
            max-width: 1000px;
            margin: 32px auto;
            padding: 0 16px 48px;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 16px;
        }
        .card {
            background: #111827;
            border: 1px solid #1f2937;
            border-radius: 12px;
            padding: 18px 20px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.25);
        }
        .card h3 {
            margin-top: 0;
            color: #93c5fd;
        }
        label {
            display: block;
            margin: 10px 0 6px;
            color: #cbd5f5;
            font-size: 14px;
        }
        input[type="text"], input[type="password"] {
            width: 100%;
            padding: 10px 12px;
            border-radius: 8px;
            border: 1px solid #1f2937;
            background: #0b1221;
            color: #e2e8f0;
        }
        button {
            margin-top: 12px;
            padding: 10px 14px;
            border: none;
            border-radius: 8px;
            background: linear-gradient(135deg, #8b5cf6, #6366f1);
            color: #fff;
            cursor: pointer;
            font-weight: 600;
            width: 100%;
        }
        button:hover {
            background: linear-gradient(135deg, #a855f7, #4f46e5);
        }
        .status {
            margin-bottom: 16px;
        }
        .status p {
            margin: 6px 0;
            padding: 10px 12px;
            border-radius: 8px;
            font-size: 14px;
        }
        .notice { background: rgba(34,197,94,0.15); border: 1px solid rgba(34,197,94,0.35); color: #bbf7d0; }
        .error { background: rgba(248,113,113,0.15); border: 1px solid rgba(248,113,113,0.35); color: #fecaca; }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 12px;
            background: #0b1221;
            border-radius: 10px;
            overflow: hidden;
        }
        th, td {
            padding: 10px 12px;
            border-bottom: 1px solid #1f2937;
            text-align: left;
            font-size: 14px;
        }
        th { background: #111827; color: #cbd5f5; }
        tr:last-child td { border-bottom: none; }
        .pill {
            display: inline-block;
            padding: 6px 10px;
            border-radius: 9999px;
            font-size: 12px;
            border: 1px solid #334155;
            background: #0b1221;
        }
    </style>
</head>
<body>
    <header>
        <h1>Discord bot &mdash; Admin panel</h1>
    </header>
    <div class="container">
        <div class="status">
            <?php foreach($notices as $msg): ?>
                <p class="notice"><?php echo htmlspecialchars($msg); ?></p>
            <?php endforeach; ?>
            <?php foreach($errors as $msg): ?>
                <p class="error"><?php echo htmlspecialchars($msg); ?></p>
            <?php endforeach; ?>
        </div>

        <div class="grid">
            <div class="card">
                <h3>Přihlašovací údaje</h3>
                <p>Aktuální uživatel: <span class="pill"><?php echo htmlspecialchars($adminRow ? $adminRow['username'] : 'není nastaveno'); ?></span></p>
                <form method="POST">
                    <input type="hidden" name="update_credentials" value="1">
                    <label for="new_username">Nové uživatelské jméno</label>
                    <input type="text" id="new_username" name="new_username" placeholder="Zadejte nové uživatelské jméno">
                    <label for="new_password">Nové heslo</label>
                    <input type="password" id="new_password" name="new_password" placeholder="Zadejte nové heslo">
                    <button type="submit">Uložit nové údaje</button>
                </form>
            </div>

            <div class="card">
                <h3>Discord token</h3>
                <p>Zdroj tokenu: <span class="pill"><?php echo $discordTokenStored ? 'uložen v databázi' : ($discordTokenEnv ? 'načten z prostředí' : 'není nastaven'); ?></span></p>
                <form method="POST">
                    <input type="hidden" name="save_token" value="1">
                    <label for="discord_token">Discord Bot Token</label>
                    <input type="text" id="discord_token" name="discord_token" placeholder="Zadejte token">
                    <button type="submit">Uložit token</button>
                </form>
            </div>

            <div class="card">
                <h3>Discord Guild</h3>
                <p>Aktuální ID: <span class="pill"><?php echo $guildId ? htmlspecialchars($guildId) : 'není nastaveno'; ?></span></p>
                <form method="POST">
                    <input type="hidden" name="save_guild" value="1">
                    <label for="discord_guild_id">DISCORD_GUILD_ID</label>
                    <input type="text" id="discord_guild_id" name="discord_guild_id" placeholder="Zadejte guild ID">
                    <button type="submit">Uložit Guild ID</button>
                </form>
            </div>
        </div>

        <div class="card" style="margin-top: 16px;">
            <h3>Členové s rolí <?php echo htmlspecialchars($roleId); ?></h3>
            <?php if(!$guildId || !$discordToken): ?>
                <p class="error">Nastavte prosím DISCORD_GUILD_ID a Discord token (proměnná prostředí nebo uložený v databázi) pro načtení členů.</p>
            <?php else: ?>
                <?php
                $members = fetch_guild_members($guildId, $discordToken);
                $roleMembers = filter_members_with_role($members, $roleId);
                ?>
                <?php if(empty($roleMembers)): ?>
                    <p>Nebyli nalezeni žádní členové s touto rolí.</p>
                <?php else: ?>
                    <table>
                        <tr><th>#</th><th>ID</th><th>Přezdívka</th><th>Uživatel</th></tr>
                        <?php foreach($roleMembers as $index => $member): ?>
                            <tr>
                                <td><?php echo $index + 1; ?></td>
                                <td><?php echo htmlspecialchars($member['id']); ?></td>
                                <td><?php echo htmlspecialchars($member['display']); ?></td>
                                <td><?php echo htmlspecialchars($member['username']); ?></td>
                            </tr>
                        <?php endforeach; ?>
                    </table>
                <?php endif; ?>
            <?php endif; ?>
        </div>
    </div>
</body>
</html>
