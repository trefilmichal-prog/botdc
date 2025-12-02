<?php
session_start();
if(!isset($_SESSION['login'])) { header("Location: index.php"); exit; }

$db = new PDO('sqlite:database.sqlite');

// Ensure settings table exists for storing credentials/token
$db->exec("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)");

// Discord guild/role configuration
$guildId = getenv('DISCORD_GUILD_ID');
$roleId = '1440268327892025438';
$discordTokenEnv = getenv('DISCORD_TOKEN');
$adminRow = $db->query("SELECT * FROM admins ORDER BY id LIMIT 1")->fetch(PDO::FETCH_ASSOC);
$discordTokenStored = get_setting($db, 'discord_token');
$discordToken = $discordTokenStored ? $discordTokenStored : $discordTokenEnv;

$notices = array();
$errors = array();

// Ensure table has required columns
$db->exec("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT, params TEXT, created_at TEXT, processed INTEGER DEFAULT 0, processed_at TEXT)");
$columns = $db->query("PRAGMA table_info(tasks)")->fetchAll(PDO::FETCH_ASSOC);
$columnNames = array_column($columns, 'name');
if(!in_array('processed', $columnNames)) {
    $db->exec("ALTER TABLE tasks ADD COLUMN processed INTEGER NOT NULL DEFAULT 0");
}
if(!in_array('processed_at', $columnNames)) {
    $db->exec("ALTER TABLE tasks ADD COLUMN processed_at TEXT");
}

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

if(isset($_POST['action']) && isset($_POST['params'])) {
    $ins = $db->prepare("INSERT INTO tasks (action, params, created_at) VALUES (?, ?, datetime('now'))");
    $ins->execute(array($_POST['action'], $_POST['params']));
}

$rows = $db->query("SELECT * FROM tasks ORDER BY id DESC")->fetchAll(PDO::FETCH_ASSOC);
?>
<!DOCTYPE html>
<html>
<head><title>Admin Panel</title></head>
<body>
<h2>Admin panel</h2>

<?php foreach($notices as $msg): ?>
    <p style="color: green;"><?php echo htmlspecialchars($msg); ?></p>
<?php endforeach; ?>
<?php foreach($errors as $msg): ?>
    <p style="color: red;"><?php echo htmlspecialchars($msg); ?></p>
<?php endforeach; ?>

<h3>Nastavení přístupu</h3>
<p>Aktuální přihlašovací jméno: <strong><?php echo htmlspecialchars($adminRow ? $adminRow['username'] : 'není nastaveno'); ?></strong></p>
<form method="POST">
    <input type="hidden" name="update_credentials" value="1">
    <input type="text" name="new_username" placeholder="Nové uživatelské jméno"><br>
    <input type="password" name="new_password" placeholder="Nové heslo"><br>
    <button type="submit">Uložit nové údaje</button>
</form>

<h3>Discord token</h3>
<p>Uložený token: <?php echo $discordTokenStored ? 'nastaven v databázi' : ($discordTokenEnv ? 'načten z proměnné prostředí' : 'není nastaven'); ?></p>
<form method="POST">
    <input type="hidden" name="save_token" value="1">
    <input type="text" name="discord_token" placeholder="Discord Bot Token" value="">
    <button type="submit">Uložit token</button>
</form>

<form method="POST">
<input type="text" name="action" placeholder="Action"><br>
<input type="text" name="params" placeholder="Params JSON"><br>
<button type="submit">Add task</button>
</form>

<h3>Tasks</h3>
<table border="1">
<tr><th>ID</th><th>Action</th><th>Params</th><th>Created</th><th>Processed</th><th>Processed at</th></tr>
<?php foreach($rows as $r): ?>
<tr>
<td><?php echo $r['id']; ?></td>
<td><?php echo $r['action']; ?></td>
<td><?php echo $r['params']; ?></td>
<td><?php echo $r['created_at']; ?></td>
<td><?php echo $r['processed'] ? 'Yes' : 'No'; ?></td>
<td><?php echo $r['processed_at']; ?></td>
</tr>
<?php endforeach; ?>
</table>

<h3>Členové s rolí <?php echo htmlspecialchars($roleId); ?></h3>
<?php if(!$guildId || !$discordToken): ?>
    <p style="color:red;">Nastavte prosím DISCORD_GUILD_ID a Discord token (proměnná prostředí nebo uložený v databázi) pro načtení členů.</p>
<?php else: ?>
    <?php
    $members = fetch_guild_members($guildId, $discordToken);
    $roleMembers = filter_members_with_role($members, $roleId);
    ?>
    <?php if(empty($roleMembers)): ?>
        <p>Žádní členové s touto rolí nebyli nalezeni.</p>
    <?php else: ?>
        <table border="1">
            <tr><th>#</th><th>ID</th><th>Nickname</th><th>Username</th></tr>
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

</body>
</html>
