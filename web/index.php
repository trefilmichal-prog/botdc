<?php
// PHP 5.6 kompatibilní verze (bez strict_types, bez scalar type hints, bez ??).
// Endpoint: POST (JSON) uloží data do SQLite; GET zobrazí jednoduchý leaderboard.

define('DB_PATH', __DIR__ . '/leaderboard.sqlite');

header('Content-Type: text/html; charset=utf-8');

// Volitelný debug režim: přidej ?debug=1 (používej jen dočasně).
if (isset($_GET['debug']) && $_GET['debug'] === '1') {
    ini_set('display_errors', '1');
    error_reporting(E_ALL);
} else {
    ini_set('display_errors', '0');
    error_reporting(0);
}

// DB init
if (!extension_loaded('pdo_sqlite')) {
    http_response_code(500);
    echo 'Missing PHP extension: pdo_sqlite. Please install/enable it.';
    exit;
}

try {
    $pdo = new PDO('sqlite:' . DB_PATH);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $pdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);

    // Doporučeno pro současné zápisy
    $pdo->exec("PRAGMA journal_mode = WAL;");
    $pdo->exec("PRAGMA synchronous = NORMAL;");
    $pdo->exec("PRAGMA busy_timeout = 5000;");

    $pdo->exec("
        CREATE TABLE IF NOT EXISTS secret_leaderboard (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            rarity TEXT NOT NULL,
            count INTEGER NOT NULL,
            clan_key TEXT NULL,
            clan_display TEXT NOT NULL DEFAULT 'Nezařazeno'
        );
    ");

    $pdo->exec("
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    ");
} catch (Exception $e) {
    http_response_code(500);
    error_log('DB init error: ' . $e->getMessage());
    echo 'Database unavailable';
    exit;
}

// Ověření, že sloupec rarity existuje (kompatibilní doplnění, pokud někdo měl starou DB).
try {
    $columns = $pdo->query("PRAGMA table_info('secret_leaderboard')")->fetchAll(PDO::FETCH_ASSOC);
    $hasRarity = false;
    $hasDisplayName = false;
    $hasClanKey = false;
    $hasClanDisplay = false;
    foreach ($columns as $column) {
        $name = isset($column['name']) ? $column['name'] : '';
        if ($name === 'rarity') {
            $hasRarity = true;
        }
        if ($name === 'display_name') {
            $hasDisplayName = true;
        }
        if ($name === 'clan_key') {
            $hasClanKey = true;
        }
        if ($name === 'clan_display') {
            $hasClanDisplay = true;
        }
    }
    if (!$hasRarity) {
        $pdo->exec("ALTER TABLE secret_leaderboard ADD COLUMN rarity TEXT NOT NULL DEFAULT 'secret'");
    }
    if (!$hasDisplayName) {
        $pdo->exec("ALTER TABLE secret_leaderboard ADD COLUMN display_name TEXT NOT NULL DEFAULT ''");
    }
    if (!$hasClanKey) {
        $pdo->exec("ALTER TABLE secret_leaderboard ADD COLUMN clan_key TEXT NULL");
    }
    if (!$hasClanDisplay) {
        $pdo->exec("ALTER TABLE secret_leaderboard ADD COLUMN clan_display TEXT NOT NULL DEFAULT 'Nezařazeno'");
    }
} catch (Exception $e) {
    http_response_code(500);
    error_log('DB schema check error: ' . $e->getMessage());
    echo 'Database unavailable';
    exit;
}

// Helpers
function bad_request($msg) {
    http_response_code(400);
    echo htmlspecialchars($msg, ENT_QUOTES, 'UTF-8');
    exit;
}

$method = isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : 'GET';

if ($method === 'POST') {
    $raw = file_get_contents('php://input');
    if ($raw === false) {
        bad_request('Unable to read request body.');
    }

    $data = json_decode($raw, true);
    if (!is_array($data)) {
        bad_request('Invalid JSON.');
    }

    $entries = isset($data['entries']) ? $data['entries'] : null;
    if (!is_array($entries)) {
        bad_request('Missing entries.');
    }

    $aggregated = array();
    foreach ($entries as $row) {
        if (!is_array($row)) {
            bad_request('Invalid entry.');
        }
        if (!isset($row['user_id']) || !isset($row['rarity']) || !isset($row['count'])) {
            bad_request('Each entry must include user_id, rarity, and count.');
        }

        $userId = (int)$row['user_id'];
        $rarity = strtolower(trim((string)$row['rarity']));
        $count  = (int)$row['count'];
        $displayName = '';
        if (isset($row['display_name'])) {
            $displayName = trim((string)$row['display_name']);
        }
        $clanKey = null;
        if (isset($row['clan_key'])) {
            $clanKey = trim((string)$row['clan_key']);
            if ($clanKey === '') {
                $clanKey = null;
            }
        }
        $clanDisplay = 'Nezařazeno';
        if (isset($row['clan_display'])) {
            $candidateDisplay = trim((string)$row['clan_display']);
            if ($candidateDisplay !== '') {
                $clanDisplay = $candidateDisplay;
            }
        }

        if ($userId <= 0) {
            bad_request('Invalid user_id.');
        }
        if ($count < 0) {
            bad_request('Invalid count.');
        }

        $key = $userId . ':' . $rarity . ':' . ($clanKey === null ? 'unassigned' : $clanKey);
        if (!isset($aggregated[$key])) {
            $aggregated[$key] = array(
                'user_id' => $userId,
                'display_name' => $displayName,
                'rarity' => $rarity,
                'count' => 0,
                'clan_key' => $clanKey,
                'clan_display' => $clanDisplay,
            );
        }
        if ($aggregated[$key]['display_name'] === '' && $displayName !== '') {
            $aggregated[$key]['display_name'] = $displayName;
        }
        if ($aggregated[$key]['clan_display'] === 'Nezařazeno' && $clanDisplay !== 'Nezařazeno') {
            $aggregated[$key]['clan_display'] = $clanDisplay;
        }
        $aggregated[$key]['count'] += $count;
    }

    // Uložení: smažeme staré a uložíme nové
    try {
        $pdo->beginTransaction();

        $pdo->exec("DELETE FROM secret_leaderboard");

        $stmt = $pdo->prepare("INSERT INTO secret_leaderboard (user_id, display_name, rarity, count, clan_key, clan_display) VALUES (:user_id, :display_name, :rarity, :count, :clan_key, :clan_display)");
        foreach ($aggregated as $row) {
            $stmt->execute(array(
                ':user_id' => (int)$row['user_id'],
                ':display_name' => (string)$row['display_name'],
                ':rarity'  => (string)$row['rarity'],
                ':count'   => (int)$row['count'],
                ':clan_key' => $row['clan_key'] === null ? null : (string)$row['clan_key'],
                ':clan_display' => (string)$row['clan_display'],
            ));
        }

        // Čas posledního update (SQLite kompatibilní i se starší verzí bez UPSERT ... excluded)
        $generatedAt = isset($data['generated_at']) ? (string)$data['generated_at'] : date('c');
        $stmtMeta = $pdo->prepare("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_update', :v)");
        $stmtMeta->execute(array(':v' => $generatedAt));

        $pdo->commit();

        echo "OK";
        exit;
    } catch (Exception $e) {
        if ($pdo->inTransaction()) {
            $pdo->rollBack();
        }
        http_response_code(500);
        error_log('POST save error: ' . $e->getMessage());
        echo 'Database unavailable';
        exit;
    }
}

// GET – zobrazit leaderboard
try {
    $clanTotals = $pdo->query("
        SELECT
            COALESCE(clan_key, 'unassigned') AS clan_key_group,
            MAX(COALESCE(NULLIF(TRIM(clan_display), ''), 'Nezařazeno')) AS clan_display,
            SUM(count) AS total_count
        FROM secret_leaderboard
        WHERE clan_key IS NOT NULL AND clan_key != ''
        GROUP BY clan_key_group
        ORDER BY total_count DESC, clan_display ASC
    ")->fetchAll(PDO::FETCH_ASSOC);

    $memberRows = $pdo->query("
        SELECT
            COALESCE(clan_key, 'unassigned') AS clan_key_group,
            MAX(COALESCE(NULLIF(TRIM(clan_display), ''), 'Nezařazeno')) AS clan_display,
            user_id,
            MAX(display_name) AS display_name,
            SUM(count) AS count
        FROM secret_leaderboard
        WHERE clan_key IS NOT NULL AND clan_key != ''
        GROUP BY clan_key_group, user_id
        ORDER BY clan_key_group ASC, count DESC, user_id ASC
    ")->fetchAll(PDO::FETCH_ASSOC);

    $stmtLU = $pdo->prepare("SELECT value FROM meta WHERE key = 'last_update'");
    $stmtLU->execute();
    $lastUpdate = $stmtLU->fetchColumn();
    if ($lastUpdate === false || $lastUpdate === null || $lastUpdate === '') {
        $lastUpdate = 'N/A';
    }
} catch (Exception $e) {
    http_response_code(500);
    error_log('GET query error: ' . $e->getMessage());
    echo 'Database unavailable';
    exit;
}

function display_name_label($displayName, $userId) {
    $label = trim((string)$displayName);
    if ($label === '') {
        return (string)$userId;
    }
    return $label;
}

$clanMembers = array();
foreach ($memberRows as $row) {
    $clanKey = isset($row['clan_key_group']) ? $row['clan_key_group'] : 'unassigned';
    if (!isset($clanMembers[$clanKey])) {
        $clanMembers[$clanKey] = array();
    }
    $clanMembers[$clanKey][] = $row;
}
?>
<!doctype html>
<html lang="cs">
<head>
    <meta charset="utf-8">
    <title>Secret Leaderboard</title>
    <style>
        :root {
            color-scheme: light;
            --bg-start: #16070f;
            --bg-end: #0b1026;
            --card: rgba(15, 23, 42, 0.9);
            --card-strong: rgba(15, 23, 42, 0.98);
            --text: #e2e8f0;
            --muted: #94a3b8;
            --accent: #ff2d55;
            --accent-alt: #3b82f6;
            --neon: #ff4d6d;
            --shadow: 0 24px 50px rgba(3, 7, 18, 0.55);
            --radius: 18px;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            font-family: "Inter", "Segoe UI", "Roboto", "Helvetica Neue", Arial, sans-serif;
            background: linear-gradient(135deg, var(--bg-start), var(--bg-end));
            color: var(--text);
            min-height: 100vh;
        }
        a { color: inherit; text-decoration: none; }
        .page {
            max-width: 1200px;
            margin: 0 auto;
            padding: 2.5rem 1.5rem 4rem;
        }
        .header {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 1.5rem;
            background: linear-gradient(130deg, rgba(255, 45, 85, 0.12), rgba(59, 130, 246, 0.2));
            border: 1px solid rgba(255, 45, 85, 0.35);
            border-radius: var(--radius);
            padding: 2rem;
            color: #f8fafc;
            backdrop-filter: blur(10px);
            box-shadow: 0 0 30px rgba(255, 45, 85, 0.25);
        }
        .header h1 {
            margin: 0 0 0.3rem;
            font-size: clamp(1.9rem, 2.4vw, 2.6rem);
        }
        .header p {
            margin: 0;
            color: rgba(248, 250, 252, 0.8);
        }
        .badge {
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
            padding: 0.35rem 0.75rem;
            border-radius: 999px;
            font-size: 0.85rem;
            font-weight: 600;
            letter-spacing: 0.01em;
            background: linear-gradient(135deg, rgba(255, 45, 85, 0.95), rgba(59, 130, 246, 0.9));
            color: #ffffff;
            border: 1px solid rgba(255, 255, 255, 0.2);
            box-shadow: 0 0 12px rgba(255, 45, 85, 0.5);
        }
        .layout {
            margin-top: 2rem;
            display: grid;
            gap: 2rem;
        }
        .card {
            background: var(--card);
            border-radius: var(--radius);
            padding: 1.8rem;
            box-shadow: var(--shadow);
            border: 1px solid rgba(59, 130, 246, 0.2);
        }
        .card h2 {
            margin-top: 0;
            font-size: 1.4rem;
            color: #f8fafc;
            text-shadow: 0 0 18px rgba(255, 45, 85, 0.3);
        }
        .subtle {
            color: var(--muted);
            font-size: 0.95rem;
        }
        .table-wrap {
            margin-top: 1rem;
            overflow-x: auto;
            border-radius: 12px;
            border: 1px solid rgba(255, 45, 85, 0.35);
            background: rgba(15, 23, 42, 0.95);
            box-shadow: inset 0 0 20px rgba(59, 130, 246, 0.15);
        }
        table {
            border-collapse: collapse;
            width: 100%;
            min-width: 520px;
        }
        th, td {
            padding: 0.85rem 1rem;
            text-align: left;
            font-size: 0.95rem;
        }
        th {
            background: linear-gradient(90deg, rgba(255, 45, 85, 0.2), rgba(59, 130, 246, 0.2));
            color: #f8fafc;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }
        tbody tr:nth-child(even) {
            background: rgba(59, 130, 246, 0.08);
        }
        tbody tr:hover {
            background: rgba(255, 45, 85, 0.2);
        }
        .rank {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 2.1rem;
            height: 2.1rem;
            border-radius: 12px;
            background: linear-gradient(135deg, rgba(255, 45, 85, 0.95), rgba(59, 130, 246, 0.95));
            color: #fff;
            font-weight: 700;
            font-size: 0.9rem;
            box-shadow: 0 0 12px rgba(255, 45, 85, 0.6);
        }
        .empty {
            padding: 1.5rem;
            border-radius: 12px;
            background: rgba(15, 23, 42, 0.8);
            color: var(--muted);
            border: 1px dashed rgba(59, 130, 246, 0.35);
        }
        .footer-note {
            margin-top: 2rem;
            color: rgba(248, 250, 252, 0.75);
            font-size: 0.85rem;
            text-align: center;
        }
        @media (max-width: 720px) {
            .header {
                padding: 1.5rem;
            }
            table {
                min-width: 420px;
            }
        }
    </style>
</head>
<body>
    <div class="page">
        <header class="header">
            <div>
                <h1>🎮 Secret Leaderboard</h1>
                <p>Poslední update: <?php echo htmlspecialchars($lastUpdate, ENT_QUOTES, 'UTF-8'); ?></p>
            </div>
            <span class="badge" title="Celkový přehled clanů">🎮 Leaderboard podle clanů</span>
        </header>

        <section class="layout">
            <div class="card">
                <h2>🛡️ Přehled clanů</h2>
                <p class="subtle">Souhrn všech clanů podle celkového počtu dropů.</p>
                <?php if (!$clanTotals): ?>
                    <div class="empty">Žádná data.</div>
                <?php else: ?>
                    <div class="table-wrap">
                        <table>
                            <thead>
                            <tr><th>Rank</th><th>Clan</th><th>Dropy</th></tr>
                            </thead>
                            <tbody>
                            <?php foreach ($clanTotals as $i => $row): ?>
                                <tr>
                                    <td><span class="rank" title="Pořadí"><?php echo (int)($i + 1); ?></span></td>
                                    <td><?php echo htmlspecialchars($row['clan_display'], ENT_QUOTES, 'UTF-8'); ?></td>
                                    <td><?php echo (int)$row['total_count']; ?></td>
                                </tr>
                            <?php endforeach; ?>
                            </tbody>
                        </table>
                    </div>
                <?php endif; ?>
            </div>

            <?php foreach ($clanTotals as $clan): ?>
                <?php
                $clanKey = isset($clan['clan_key_group']) ? $clan['clan_key_group'] : 'unassigned';
                if ($clanKey === '' || $clanKey === 'unassigned') {
                    continue;
                }
                $clanDisplay = isset($clan['clan_display']) ? $clan['clan_display'] : 'Nezařazeno';
                $totalCount = isset($clan['total_count']) ? (int)$clan['total_count'] : 0;
                $members = isset($clanMembers[$clanKey]) ? $clanMembers[$clanKey] : array();
                ?>
                <div class="card">
                    <h2>🏆 Top členové – <?php echo htmlspecialchars($clanDisplay, ENT_QUOTES, 'UTF-8'); ?></h2>
                    <p class="subtle">Celkem dropů: <?php echo $totalCount; ?></p>
                    <?php if (!$members): ?>
                        <div class="empty">Žádní členové.</div>
                    <?php else: ?>
                        <div class="table-wrap">
                            <table>
                                <thead>
                                <tr><th>Rank</th><th>User</th><th>Dropy</th></tr>
                                </thead>
                                <tbody>
                                <?php foreach ($members as $i => $row): ?>
                                    <tr>
                                        <td><span class="rank"><?php echo (int)($i + 1); ?></span></td>
                                        <td><?php echo htmlspecialchars(display_name_label($row['display_name'], $row['user_id']), ENT_QUOTES, 'UTF-8'); ?></td>
                                        <td><?php echo (int)$row['count']; ?></td>
                                    </tr>
                                <?php endforeach; ?>
                                </tbody>
                            </table>
                        </div>
                    <?php endif; ?>
                </div>
            <?php endforeach; ?>
        </section>

        <p class="footer-note">Layout funguje bez JavaScriptu a je optimalizovaný pro mobilní zařízení.</p>
    </div>
</body>
</html>
