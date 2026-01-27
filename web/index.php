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
            rarity TEXT NOT NULL,
            count INTEGER NOT NULL
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

// Podporované rarity (držet se bot logiky): secret, mysterious, divine, supreme, aura.
$supportedRarities = array('secret', 'mysterious', 'divine', 'supreme', 'aura');

// Ověření, že sloupec rarity existuje (kompatibilní doplnění, pokud někdo měl starou DB).
try {
    $columns = $pdo->query("PRAGMA table_info('secret_leaderboard')")->fetchAll(PDO::FETCH_ASSOC);
    $hasRarity = false;
    foreach ($columns as $column) {
        $name = isset($column['name']) ? $column['name'] : '';
        if ($name === 'rarity') {
            $hasRarity = true;
            break;
        }
    }
    if (!$hasRarity) {
        $pdo->exec("ALTER TABLE secret_leaderboard ADD COLUMN rarity TEXT NOT NULL DEFAULT 'secret'");
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
        $rarity = (string)$row['rarity'];
        $count  = (int)$row['count'];

        if ($userId <= 0) {
            bad_request('Invalid user_id.');
        }
        if (!in_array($rarity, $supportedRarities, true)) {
            bad_request('Invalid rarity.');
        }
        if ($count < 0) {
            bad_request('Invalid count.');
        }

        $key = $userId . ':' . $rarity;
        if (!isset($aggregated[$key])) {
            $aggregated[$key] = array('user_id' => $userId, 'rarity' => $rarity, 'count' => 0);
        }
        $aggregated[$key]['count'] += $count;
    }

    // Uložení: smažeme staré a uložíme nové
    try {
        $pdo->beginTransaction();

        $pdo->exec("DELETE FROM secret_leaderboard");

        $stmt = $pdo->prepare("INSERT INTO secret_leaderboard (user_id, rarity, count) VALUES (:user_id, :rarity, :count)");
        foreach ($aggregated as $row) {
            $stmt->execute(array(
                ':user_id' => (int)$row['user_id'],
                ':rarity'  => (string)$row['rarity'],
                ':count'   => (int)$row['count'],
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
    $rows = $pdo->query("
        SELECT user_id, rarity, SUM(count) AS count
        FROM secret_leaderboard
        GROUP BY user_id, rarity
        ORDER BY count DESC, user_id ASC, rarity ASC
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
?>
<!doctype html>
<html lang="cs">
<head>
    <meta charset="utf-8">
    <title>Secret Leaderboard</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 2rem; }
        table { border-collapse: collapse; width: 100%; max-width: 600px; }
        th, td { border: 1px solid #ccc; padding: 0.5rem; text-align: left; }
        th { background: #f5f5f5; }
    </style>
</head>
<body>
    <h1>Secret Leaderboard</h1>
    <p>Poslední update: <?php echo htmlspecialchars($lastUpdate, ENT_QUOTES, 'UTF-8'); ?></p>

    <?php if (!$rows): ?>
        <p>Žádná data.</p>
    <?php else: ?>
        <table>
            <thead>
            <tr><th>#</th><th>User ID</th><th>Rarity</th><th>Count</th></tr>
            </thead>
            <tbody>
            <?php foreach ($rows as $i => $row): ?>
                <tr>
                    <td><?php echo (int)($i + 1); ?></td>
                    <td><?php echo (int)$row['user_id']; ?></td>
                    <td><?php echo htmlspecialchars($row['rarity'], ENT_QUOTES, 'UTF-8'); ?></td>
                    <td><?php echo (int)$row['count']; ?></td>
                </tr>
            <?php endforeach; ?>
            </tbody>
        </table>
    <?php endif; ?>
</body>
</html>
