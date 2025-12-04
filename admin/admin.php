<?php
session_start();
if(!isset($_SESSION['login'])) { header("Location: index.php"); exit; }


$db = new PDO('sqlite:database.sqlite');
$db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

// Ensure settings table exists for storing credentials/token/guild ID
$db->exec("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)");

// Track manual warnings issued from the admin panel
$db->exec("CREATE TABLE IF NOT EXISTS warnings (
    user_id TEXT PRIMARY KEY,
    warn_count INTEGER NOT NULL DEFAULT 0,
    last_warned_at TEXT NOT NULL
)");

// Track manual rebirth counts for members
$db->exec("CREATE TABLE IF NOT EXISTS member_rebirths (
    user_id TEXT PRIMARY KEY,
    display_name TEXT,
    rebirths TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)");

function ensure_member_rebirths_schema($db) {
    $stmt = $db->query("PRAGMA table_info(member_rebirths)");
    $hasUpdatedAt = false;

    while($row = $stmt->fetch(PDO::FETCH_ASSOC)) {
        if(isset($row['name']) && $row['name'] === 'updated_at') {
            $hasUpdatedAt = true;
            break;
        }
    }

    if(!$hasUpdatedAt) {
        $db->beginTransaction();
        try {
            $db->exec("CREATE TABLE IF NOT EXISTS member_rebirths_new (
                user_id TEXT PRIMARY KEY,
                display_name TEXT,
                rebirths TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )");

            $db->exec("INSERT INTO member_rebirths_new (user_id, display_name, rebirths, updated_at)
                SELECT user_id, display_name, rebirths, datetime('now') FROM member_rebirths");
            $db->exec("DROP TABLE member_rebirths");
            $db->exec("ALTER TABLE member_rebirths_new RENAME TO member_rebirths");
            $db->commit();
        } catch(PDOException $e) {
            $db->rollBack();
            throw $e;
        }
    } else {
        $db->exec("UPDATE member_rebirths SET updated_at = datetime('now') WHERE updated_at IS NULL OR updated_at = ''");
    }
}
ensure_member_rebirths_schema($db);

// Discord guild/role configuration
$clans = array(
    'clan1' => array(
        'label' => 'Clan 1',
        'roles' => array('1440268327892025438', '1444077881159450655')
    ),
    'clan2' => array(
        'label' => 'Clan 2',
        'roles' => array('1444306127687778405')
    )
);
$discordTokenEnv = getenv('DISCORD_TOKEN');
$guildIdEnv = getenv('DISCORD_GUILD_ID');
$warnRole1 = getenv('WARN_ROLE_1_ID') ? getenv('WARN_ROLE_1_ID') : '1441381537542307860';
$warnRole2 = getenv('WARN_ROLE_2_ID') ? getenv('WARN_ROLE_2_ID') : '1441381594941358135';
$warnRole3 = getenv('WARN_ROLE_3_ID') ? getenv('WARN_ROLE_3_ID') : '1441381627878965349';
$kickRoles = array('1440268327892025438', '1444077881159450655', '1444306127687778405');
$adminRow = $db->query("SELECT * FROM admins ORDER BY id LIMIT 1")->fetch(PDO::FETCH_ASSOC);
$discordTokenStored = get_setting($db, 'discord_token');
$guildIdStored = get_setting($db, 'discord_guild_id');
$discordToken = $discordTokenStored ? $discordTokenStored : $discordTokenEnv;
$guildId = $guildIdStored ? $guildIdStored : $guildIdEnv;

$notices = array();
$errors = array();
$rebirthStatuses = array();
$page = isset($_GET['page']) ? $_GET['page'] : 'credentials';
$allowedPages = array('credentials', 'token', 'guild', 'members');
if(!in_array($page, $allowedPages)) {
    $page = 'credentials';
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

function filter_members_by_clan($members, $clanData) {
    $result = array();
    foreach($members as $member) {
        if(!isset($member['roles']) || !is_array($member['roles'])) {
            continue;
        }

        $matchedRole = null;
        foreach($clanData['roles'] as $roleId) {
            if(in_array($roleId, $member['roles'])) {
                $matchedRole = $roleId;
                break;
            }
        }

        if($matchedRole !== null) {
            $username = isset($member['user']['username']) ? $member['user']['username'] : 'Unknown';
            $display = isset($member['nick']) && $member['nick'] !== null ? $member['nick'] : $username;

            $result[] = array(
                'id' => $member['user']['id'],
                'username' => $username,
                'display' => $display,
                'role' => $clanData['label'],
                'role_id' => $matchedRole,
                'roles' => $member['roles']
            );
        }
    }

    usort($result, function($a, $b) {
        return strcasecmp($a['display'], $b['display']);
    });

    return $result;
}

function get_member_roles($guildId, $userId, $token) {
    $url = "https://discord.com/api/v10/guilds/{$guildId}/members/{$userId}";
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
        return null;
    }

    $data = json_decode($response, true);
    return is_array($data) && isset($data['roles']) ? $data['roles'] : null;
}

function remove_role($guildId, $userId, $roleId, $token) {
    $url = "https://discord.com/api/v10/guilds/{$guildId}/members/{$userId}/roles/{$roleId}";
    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_CUSTOMREQUEST, 'DELETE');
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_HTTPHEADER, array(
        "Authorization: Bot {$token}",
        'User-Agent: ezrz-dcbot-admin'
    ));
    curl_exec($ch);
    curl_close($ch);
}

function add_role($guildId, $userId, $roleId, $token) {
    $url = "https://discord.com/api/v10/guilds/{$guildId}/members/{$userId}/roles/{$roleId}";
    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_CUSTOMREQUEST, 'PUT');
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_HTTPHEADER, array(
        "Authorization: Bot {$token}",
        'User-Agent: ezrz-dcbot-admin'
    ));
    curl_exec($ch);
    curl_close($ch);
}

function transfer_member($guildId, $userId, $fromRoles, $toRoles, $token) {
    $roles = get_member_roles($guildId, $userId, $token);
    if($roles === null) {
        return array(false, 'Nepodařilo se načíst role uživatele.');
    }

    $hasFromRole = false;
    foreach($fromRoles as $roleId) {
        if(in_array($roleId, $roles)) {
            $hasFromRole = true;
            break;
        }
    }

    if(!$hasFromRole) {
        return array(false, 'Uživatel nemá očekávanou roli klanu.');
    }

    if(empty($toRoles)) {
        return array(false, 'Cílová role není nastavena.');
    }

    $targetRole = $toRoles[0];

    // Nejprve přidej cílovou roli, aby uživatel nepřišel o všechny clan role
    // při přesunu mezi klany (ticket by se jinak mohl smazat).
    add_role($guildId, $userId, $targetRole, $token);

    foreach($fromRoles as $roleId) {
        if(in_array($roleId, $roles) && $roleId !== $targetRole) {
            remove_role($guildId, $userId, $roleId, $token);
        }
    }

    return array(true, 'Uživatel byl převeden.');
}

function send_direct_message($userId, $token, $content) {
    $channelUrl = 'https://discord.com/api/v10/users/@me/channels';
    $payload = json_encode(array('recipient_id' => $userId));

    $ch = curl_init($channelUrl);
    curl_setopt($ch, CURLOPT_POST, true);
    curl_setopt($ch, CURLOPT_POSTFIELDS, $payload);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_HTTPHEADER, array(
        "Authorization: Bot {$token}",
        'Content-Type: application/json',
        'User-Agent: ezrz-dcbot-admin'
    ));

    $channelResponse = curl_exec($ch);
    $channelCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    if($channelCode !== 200 && $channelCode !== 201) {
        return array(false, 'Nepodařilo se vytvořit DM kanál.');
    }

    $channelData = json_decode($channelResponse, true);
    if(!is_array($channelData) || !isset($channelData['id'])) {
        return array(false, 'Neočekávaná odpověď při vytváření DM kanálu.');
    }

    $messagePayload = json_encode(array('content' => $content));
    $messageUrl = "https://discord.com/api/v10/channels/{$channelData['id']}/messages";
    $ch = curl_init($messageUrl);
    curl_setopt($ch, CURLOPT_POST, true);
    curl_setopt($ch, CURLOPT_POSTFIELDS, $messagePayload);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_HTTPHEADER, array(
        "Authorization: Bot {$token}",
        'Content-Type: application/json',
        'User-Agent: ezrz-dcbot-admin'
    ));

    curl_exec($ch);
    $messageCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    if($messageCode !== 200 && $messageCode !== 201) {
        return array(false, 'DM zprávu se nepodařilo odeslat.');
    }

    return array(true, 'DM byla odeslána.');
}

function warn_member($db, $guildId, $userId, $token, $warnRole1, $warnRole2, $warnRole3) {
    $roles = get_member_roles($guildId, $userId, $token);
    if($roles === null) {
        return array(false, 'Nepodařilo se načíst role uživatele. Zkontrolujte token a oprávnění bota.');
    }

    if(in_array($warnRole3, $roles)) {
        return array(false, 'Uživatel již má maximální počet varování (3/3).');
    }

    $nextRole = $warnRole1;
    $rolesToRemove = array($warnRole2, $warnRole3);
    $status = '1/3';

    if(in_array($warnRole2, $roles)) {
        $nextRole = $warnRole3;
        $rolesToRemove = array($warnRole2);
        $status = '3/3';
    } elseif(in_array($warnRole1, $roles)) {
        $nextRole = $warnRole2;
        $rolesToRemove = array($warnRole1);
        $status = '2/3';
    }

    foreach($rolesToRemove as $roleId) {
        if(in_array($roleId, $roles)) {
            remove_role($guildId, $userId, $roleId, $token);
        }
    }

    add_role($guildId, $userId, $nextRole, $token);

    $dmText = "Dostal jsi varování ({$status}). Dodržuj prosím pravidla Discord serveru.";
    list($dmOk, $dmMsg) = send_direct_message($userId, $token, $dmText);

    list($warnCount, $lastWarnedAt) = record_warning($db, $userId);
    $warnInfo = "Počet varování: {$warnCount}, poslední: {$lastWarnedAt}.";

    $finalMsg = $dmOk ? "Varování bylo uděleno ({$status}). Soukromá zpráva byla odeslána. {$warnInfo}" : "Varování bylo uděleno ({$status}). Soukromou zprávu se nepodařilo odeslat: {$dmMsg} {$warnInfo}";

    return array(true, $finalMsg, $warnCount, $lastWarnedAt);
}

function record_warning($db, $userId) {
    $now = date('Y-m-d H:i:s');
    $stmt = $db->prepare("SELECT warn_count FROM warnings WHERE user_id = ?");
    $stmt->execute(array($userId));
    $row = $stmt->fetch(PDO::FETCH_ASSOC);

    if($row) {
        $newCount = $row['warn_count'] + 1;
        $update = $db->prepare("UPDATE warnings SET warn_count = ?, last_warned_at = ? WHERE user_id = ?");
        $update->execute(array($newCount, $now, $userId));
    } else {
        $newCount = 1;
        $insert = $db->prepare("INSERT INTO warnings (user_id, warn_count, last_warned_at) VALUES (?, ?, ?)");
        $insert->execute(array($userId, $newCount, $now));
    }

    return array($newCount, $now);
}

function derive_warn_count_from_roles($roles, $warnRole1, $warnRole2, $warnRole3) {
    $warnCount = 0;

    if(in_array($warnRole1, $roles)) {
        $warnCount = 1;
    }
    if(in_array($warnRole2, $roles)) {
        $warnCount = 2;
    }
    if(in_array($warnRole3, $roles)) {
        $warnCount = 3;
    }

    return $warnCount;
}

function sync_warning_record_with_roles($db, $userId, $warnCount) {
    $stmt = $db->prepare("SELECT warn_count, last_warned_at FROM warnings WHERE user_id = ?");
    $stmt->execute(array($userId));
    $row = $stmt->fetch(PDO::FETCH_ASSOC);

    if($row) {
        if($warnCount === 0) {
            $delete = $db->prepare("DELETE FROM warnings WHERE user_id = ?");
            $delete->execute(array($userId));
            return null;
        }

        if((int)$row['warn_count'] !== $warnCount) {
            $update = $db->prepare("UPDATE warnings SET warn_count = ? WHERE user_id = ?");
            $update->execute(array($warnCount, $userId));
        }

        return $row['last_warned_at'];
    }

    return null;
}

function get_warning_map($db) {
    $stmt = $db->query("SELECT user_id, warn_count, last_warned_at FROM warnings");
    $result = array();
    while($row = $stmt->fetch(PDO::FETCH_ASSOC)) {
        $result[$row['user_id']] = array(
            'warn_count' => $row['warn_count'],
            'last_warned_at' => $row['last_warned_at']
        );
    }
    return $result;
}

function save_member_rebirths($db, $userId, $displayName, $rebirths) {
    $now = date('Y-m-d H:i:s');
    try {
        $stmt = $db->prepare("INSERT INTO member_rebirths (user_id, display_name, rebirths, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET display_name = excluded.display_name, rebirths = excluded.rebirths, updated_at = excluded.updated_at");
        $stmt->execute(array($userId, $displayName, $rebirths, $now));
    } catch(PDOException $e) {
        if(stripos($e->getMessage(), 'no such column: updated_at') !== false) {
            ensure_member_rebirths_schema($db);
            $stmt = $db->prepare("INSERT INTO member_rebirths (user_id, display_name, rebirths, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET display_name = excluded.display_name, rebirths = excluded.rebirths, updated_at = excluded.updated_at");
            $stmt->execute(array($userId, $displayName, $rebirths, $now));
        } else {
            throw $e;
        }
    }

    return $now;
}
function get_rebirth_map($db) {
    $stmt = $db->query("SELECT user_id, display_name, rebirths, updated_at FROM member_rebirths");
    $map = array();

    while($row = $stmt->fetch(PDO::FETCH_ASSOC)) {
        $map[$row['user_id']] = array(
            'display_name' => $row['display_name'],
            'rebirths' => $row['rebirths'],
            'updated_at' => $row['updated_at']
        );
    }

    return $map;
}

function kick_member($guildId, $userId, $token, $rolesToRemove) {
    $roles = get_member_roles($guildId, $userId, $token);
    if($roles === null) {
        return array(false, 'Nepodařilo se načíst role uživatele.');
    }

    $removedAny = false;
    foreach($rolesToRemove as $roleId) {
        if(in_array($roleId, $roles)) {
            remove_role($guildId, $userId, $roleId, $token);
            $removedAny = true;
        }
    }

    $dmText = 'Byl jsi vyhozen z klanu. Můžeš si podat novou přihlášku.';
    list($dmOk, $dmMsg) = send_direct_message($userId, $token, $dmText);
    $status = $removedAny ? 'Role byly odebrány.' : 'Nebyla odebrána žádná role.';
    $finalMsg = $dmOk ? "Člen byl odebrán z klanu. {$status} Soukromá zpráva byla odeslána." : "Člen byl odebrán z klanu. {$status} Soukromou zprávu se nepodařilo odeslat: {$dmMsg}";

    return array(true, $finalMsg);
}

if(isset($_POST['update_rebirth'])) {
    $userId = isset($_POST['user_id']) ? trim($_POST['user_id']) : '';
    $rebirthInput = isset($_POST['rebirths']) ? $_POST['rebirths'] : '';
    $displayName = isset($_POST['display_name']) ? trim($_POST['display_name']) : '';

    if($userId === '') {
        $errors[] = 'Chybí ID uživatele.';
        $rebirthStatuses[$userId] = array('text' => 'Chybí ID uživatele.', 'error' => true);
    } else {
        $rebirths = is_string($rebirthInput) ? trim($rebirthInput) : '';

        if($rebirths === '') {
            $errors[] = 'Zadejte hodnotu rebirthu.';
            $rebirthStatuses[$userId] = array('text' => 'Zadejte hodnotu rebirthu.', 'error' => true);
        } elseif(mb_strlen($rebirths) > 255) {
            $errors[] = 'Hodnota rebirthu je příliš dlouhá (max. 255 znaků).';
            $rebirthStatuses[$userId] = array('text' => 'Hodnota rebirthu je příliš dlouhá (max. 255 znaků).', 'error' => true);
        } else {
            $storedAt = save_member_rebirths($db, $userId, $displayName !== '' ? $displayName : $userId, $rebirths);
            $message = "Rebirthy pro {$userId} byly uloženy jako text v {$storedAt}.";

            $notices[] = $message;
            $rebirthStatuses[$userId] = array('text' => $message, 'error' => false);
        }
    }
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

if(isset($_POST['warn_user'])) {
    $userId = isset($_POST['target_user_id']) ? trim($_POST['target_user_id']) : '';
    if($userId === '') {
        $errors[] = "Zadejte ID uživatele, kterého chcete varovat.";
    } elseif(!$guildId || !$discordToken) {
        $errors[] = "Pro varování uživatele nastavte DISCORD_GUILD_ID a Discord token.";
    } else {
        list($ok, $msg) = warn_member($db, $guildId, $userId, $discordToken, $warnRole1, $warnRole2, $warnRole3);
        if($ok) {
            $notices[] = $msg;
        } else {
            $errors[] = $msg;
        }
    }
}

if(isset($_POST['transfer_user'])) {
    $userId = isset($_POST['target_user_id']) ? trim($_POST['target_user_id']) : '';
    $fromRoles = isset($_POST['from_roles']) ? array_filter(explode(',', $_POST['from_roles'])) : array();
    $toRoles = isset($_POST['to_roles']) ? array_filter(explode(',', $_POST['to_roles'])) : array();

    if($userId === '' || empty($fromRoles) || empty($toRoles)) {
        $errors[] = "Neplatná data pro převod člena.";
    } elseif(!$guildId || !$discordToken) {
        $errors[] = "Pro převod člena nastavte DISCORD_GUILD_ID a Discord token.";
    } else {
        list($ok, $msg) = transfer_member($guildId, $userId, $fromRoles, $toRoles, $discordToken);
        if($ok) {
            $notices[] = $msg;
        } else {
            $errors[] = $msg;
        }
    }
}

if(isset($_POST['kick_user'])) {
    $userId = isset($_POST['target_user_id']) ? trim($_POST['target_user_id']) : '';

    if($userId === '') {
        $errors[] = "Zadejte ID uživatele, kterého chcete vyhodit.";
    } elseif(!$guildId || !$discordToken) {
        $errors[] = "Pro vyhození uživatele nastavte DISCORD_GUILD_ID a Discord token.";
    } else {
        list($ok, $msg) = kick_member($guildId, $userId, $discordToken, $kickRoles);
        if($ok) {
            $notices[] = $msg;
        } else {
            $errors[] = $msg;
        }
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
        input[type="text"], input[type="password"], input[type="number"] {
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
    <style>
        .nav-bar {
            background:#0b1221;
            border-bottom:1px solid #1f2937;
        }
        .nav-grid {
            max-width:1000px;
            margin:0 auto;
            padding:12px 16px 16px;
            display:grid;
            gap:12px;
            grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
        }
        .nav-item {
            display:flex;
            gap:12px;
            align-items:flex-start;
            padding:12px 14px;
            border-radius:10px;
            text-decoration:none;
            border:1px solid #1f2937;
            background:#0f172a;
            color:#cbd5f5;
            box-shadow:0 6px 14px rgba(0,0,0,0.2);
            transition:border-color 0.2s, box-shadow 0.2s, transform 0.1s;
        }
        .nav-item:hover {
            border-color:#334155;
            box-shadow:0 10px 20px rgba(0,0,0,0.28);
            transform:translateY(-1px);
        }
        .nav-item.active {
            border-color:#6366f1;
            box-shadow:0 10px 22px rgba(99,102,241,0.18);
            background:linear-gradient(135deg,#0f172a,#111827);
        }
        .nav-icon {
            font-size:18px;
            margin-top:2px;
        }
        .nav-text {
            display:flex;
            flex-direction:column;
            gap:4px;
        }
        .nav-label {
            font-weight:700;
            color:#e5e7eb;
        }
        .nav-desc {
            font-size:13px;
            color:#94a3b8;
            line-height:1.4;
        }
        .rebirth-inline {
            display: flex;
            gap: 8px;
            align-items: center;
        }
        .rebirth-inline input[type="text"] {
            max-width: 180px;
        }
        .rebirth-status {
            margin-top: 6px;
            font-size: 12px;
            color: #cbd5f5;
        }
        .rebirth-status.error { color: #fecaca; }
    </style>
    <nav class="nav-bar">
        <div class="nav-grid">
            <?php
                $links = array(
                    'credentials' => array('label' => 'Přihlašovací údaje', 'desc' => 'Změna uživatelského jména a hesla do panelu.', 'icon' => '🔐'),
                    'token' => array('label' => 'Discord token', 'desc' => 'Uložení nebo kontrola tokenu bota.', 'icon' => '🤖'),
                    'guild' => array('label' => 'Discord Guild', 'desc' => 'Nastavení ID serveru, se kterým bot pracuje.', 'icon' => '🏰'),
                    'members' => array('label' => 'Členové', 'desc' => 'Seznam členů klanů a rychlé akce.', 'icon' => '👥')
                );
            ?>
            <?php foreach($links as $key => $data): ?>
                <?php $active = $page === $key ? 'active' : ''; ?>
                <a class="nav-item <?php echo $active; ?>" href="?page=<?php echo $key; ?>">
                    <div class="nav-icon"><?php echo $data['icon']; ?></div>
                    <div class="nav-text">
                        <span class="nav-label"><?php echo $data['label']; ?></span>
                        <span class="nav-desc"><?php echo $data['desc']; ?></span>
                    </div>
                </a>
            <?php endforeach; ?>
        </div>
    </nav>
    <div class="container">
        <div class="status">
            <?php foreach($notices as $msg): ?>
                <p class="notice"><?php echo htmlspecialchars($msg); ?></p>
            <?php endforeach; ?>
            <?php foreach($errors as $msg): ?>
                <p class="error"><?php echo htmlspecialchars($msg); ?></p>
            <?php endforeach; ?>
        </div>

        <?php if($page === 'credentials'): ?>
            <div class="grid">
                <div class="card" id="credentials">
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
            </div>
        <?php elseif($page === 'token'): ?>
            <div class="grid">
                <div class="card" id="token">
                    <h3>Discord token</h3>
                    <p>Zdroj tokenu: <span class="pill"><?php echo $discordTokenStored ? 'uložen v databázi' : ($discordTokenEnv ? 'načten z prostředí' : 'není nastaven'); ?></span></p>
                    <form method="POST">
                        <input type="hidden" name="save_token" value="1">
                        <label for="discord_token">Discord Bot Token</label>
                        <input type="text" id="discord_token" name="discord_token" placeholder="Zadejte token">
                        <button type="submit">Uložit token</button>
                    </form>
                </div>
            </div>
        <?php elseif($page === 'guild'): ?>
            <div class="grid">
                <div class="card" id="guild">
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
        <?php elseif($page === 'members'): ?>
            <?php if(!$guildId || !$discordToken): ?>
                <div class="card" style="margin-top: 16px;">
                    <h3>Členové klanů</h3>
                    <p class="error">Nastavte prosím DISCORD_GUILD_ID a Discord token (proměnná prostředí nebo uložený v databázi) pro načtení členů.</p>
                </div>
            <?php else: ?>
                <?php
                    $members = fetch_guild_members($guildId, $discordToken);
                ?>
                <?php $warningMap = get_warning_map($db); ?>
                <?php $rebirthMap = get_rebirth_map($db); ?>
                <?php foreach($clans as $clanKey => $clanData): ?>
                    <?php $roleMembers = filter_members_by_clan($members, $clanData); ?>
                    <div class="card" style="margin-top: 16px;">
                        <h3><?php echo htmlspecialchars($clanData['label']); ?> (<?php echo htmlspecialchars(implode(', ', $clanData['roles'])); ?>)</h3>
                        <?php if(empty($roleMembers)): ?>
                            <p>Nebyli nalezeni žádní členové s touto rolí.</p>
                        <?php else: ?>
                            <table>
                                <tr><th>#</th><th>Přezdívka</th><th>Role</th><th>Rebirthy</th><th>Varování</th><th>Akce</th></tr>
                                <?php foreach($roleMembers as $index => $member): ?>
                                    <?php
                                        $targetClanKey = null;
                                        foreach($clans as $otherKey => $otherClan) {
                                            if($otherKey !== $clanKey) {
                                                $targetClanKey = $otherKey;
                                                $targetClan = $otherClan;
                                                break;
                                            }
                                        }
                                        $rebirthValue = isset($rebirthMap[$member['id']]) ? $rebirthMap[$member['id']]['rebirths'] : '';
                                        $rebirthUpdated = isset($rebirthMap[$member['id']]) ? $rebirthMap[$member['id']]['updated_at'] : '—';
                                        $warnCount = derive_warn_count_from_roles($member['roles'], $warnRole1, $warnRole2, $warnRole3);
                                        if($warnCount === 0 && isset($warningMap[$member['id']])) {
                                            sync_warning_record_with_roles($db, $member['id'], $warnCount);
                                            unset($warningMap[$member['id']]);
                                        }

                                        $lastWarned = '—';
                                        if($warnCount > 0) {
                                            $lastWarned = isset($warningMap[$member['id']]) ? $warningMap[$member['id']]['last_warned_at'] : '—';
                                            $syncedTimestamp = sync_warning_record_with_roles($db, $member['id'], $warnCount);
                                            if($syncedTimestamp !== null) {
                                                $warningMap[$member['id']] = array('warn_count' => $warnCount, 'last_warned_at' => $syncedTimestamp);
                                                $lastWarned = $syncedTimestamp;
                                            }
                                        }
                                    ?>
                                    <tr>
                                        <td><?php echo $index + 1; ?></td>
                                        <td><?php echo htmlspecialchars($member['display']); ?></td>
                                        <td><?php echo htmlspecialchars($member['role']); ?></td>
                                        <td>
                                            <?php
                                                $rebirthStatusKey = $member['id'];
                                                $rebirthStatus = isset($rebirthStatuses[$rebirthStatusKey]) ? $rebirthStatuses[$rebirthStatusKey] : null;
                                                $statusText = $rebirthStatus ? $rebirthStatus['text'] : 'Naposledy: ' . $rebirthUpdated;
                                                $statusClass = 'rebirth-status' . ($rebirthStatus && $rebirthStatus['error'] ? ' error' : '');
                                            ?>
                                            <form class="rebirth-form" method="POST" action="?page=members">
                                                <input type="hidden" name="update_rebirth" value="1">
                                                <input type="hidden" name="user_id" value="<?php echo htmlspecialchars($member['id']); ?>">
                                                <input type="hidden" name="display_name" value="<?php echo htmlspecialchars($member['display']); ?>">
                                                <div class="rebirth-inline">
                                                    <input type="text" name="rebirths" value="<?php echo htmlspecialchars($rebirthValue); ?>" placeholder="Zapište rebirth">
                                                    <button type="submit" class="rebirth-submit" style="width:auto;padding:8px 12px;">Uložit</button>
                                                </div>
                                                <div class="<?php echo $statusClass; ?>"><?php echo htmlspecialchars($statusText); ?></div>
                                            </form>
                                        </td>
                                        <td>
                                            <div style="display:flex;flex-direction:column;gap:4px;">
                                                <span class="pill" style="background:rgba(251,191,36,0.15);border-color:rgba(251,191,36,0.35);color:#fcd34d;width:max-content;">Počet: <?php echo htmlspecialchars($warnCount); ?></span>
                                                <span style="font-size:12px;color:#cbd5f5;">Poslední: <?php echo htmlspecialchars($lastWarned); ?></span>
                                            </div>
                                        </td>
                                        <td style="display:flex;gap:8px;flex-wrap:wrap;">
                                            <form method="POST" style="margin:0;">
                                                <input type="hidden" name="warn_user" value="1">
                                                <input type="hidden" name="target_user_id" value="<?php echo htmlspecialchars($member['id']); ?>">
                                                <button type="submit" style="width:auto;padding:8px 12px;">/warn</button>
                                            </form>
                                            <form method="POST" style="margin:0;">
                                                <input type="hidden" name="kick_user" value="1">
                                                <input type="hidden" name="target_user_id" value="<?php echo htmlspecialchars($member['id']); ?>">
                                                <button type="submit" style="width:auto;padding:8px 12px;">Kick</button>
                                            </form>
                                            <?php if($targetClanKey !== null): ?>
                                                <form method="POST" style="margin:0;">
                                                    <input type="hidden" name="transfer_user" value="1">
                                                    <input type="hidden" name="target_user_id" value="<?php echo htmlspecialchars($member['id']); ?>">
                                                    <input type="hidden" name="from_roles" value="<?php echo htmlspecialchars(implode(',', $clanData['roles'])); ?>">
                                                    <input type="hidden" name="to_roles" value="<?php echo htmlspecialchars(implode(',', $targetClan['roles'])); ?>">
                                                    <button type="submit" style="width:auto;padding:8px 12px;">Převést do <?php echo htmlspecialchars($targetClan['label']); ?></button>
                                                </form>
                                            <?php endif; ?>
                                        </td>
                                    </tr>
                                <?php endforeach; ?>
                            </table>
                        <?php endif; ?>
                    </div>
                <?php endforeach; ?>
            <?php endif; ?>
        <?php endif; ?>
    </div>
</body>
</html>
