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

// Ověření, že sloupec rarity existuje (kompatibilní doplnění, pokud někdo měl starou DB).
try {
    $columns = $pdo->query("PRAGMA table_info('secret_leaderboard')")->fetchAll(PDO::FETCH_ASSOC);
    $hasRarity = false;
    $hasDisplayName = false;
    foreach ($columns as $column) {
        $name = isset($column['name']) ? $column['name'] : '';
        if ($name === 'rarity') {
            $hasRarity = true;
        }
        if ($name === 'display_name') {
            $hasDisplayName = true;
        }
    }
    if (!$hasRarity) {
        $pdo->exec("ALTER TABLE secret_leaderboard ADD COLUMN rarity TEXT NOT NULL DEFAULT 'secret'");
    }
    if (!$hasDisplayName) {
        $pdo->exec("ALTER TABLE secret_leaderboard ADD COLUMN display_name TEXT NOT NULL DEFAULT ''");
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

        if ($userId <= 0) {
            bad_request('Invalid user_id.');
        }
        if ($count < 0) {
            bad_request('Invalid count.');
        }

        $key = $userId . ':' . $rarity;
        if (!isset($aggregated[$key])) {
            $aggregated[$key] = array(
                'user_id' => $userId,
                'display_name' => $displayName,
                'rarity' => $rarity,
                'count' => 0,
            );
        }
        if ($aggregated[$key]['display_name'] === '' && $displayName !== '') {
            $aggregated[$key]['display_name'] = $displayName;
        }
        $aggregated[$key]['count'] += $count;
    }

    // Uložení: smažeme staré a uložíme nové
    try {
        $pdo->beginTransaction();

        $pdo->exec("DELETE FROM secret_leaderboard");

        $stmt = $pdo->prepare("INSERT INTO secret_leaderboard (user_id, display_name, rarity, count) VALUES (:user_id, :display_name, :rarity, :count)");
        foreach ($aggregated as $row) {
            $stmt->execute(array(
                ':user_id' => (int)$row['user_id'],
                ':display_name' => (string)$row['display_name'],
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
    $filterRarity = isset($_GET['rarity']) ? strtolower(trim((string)$_GET['rarity'])) : 'secret';
    if ($filterRarity === '') {
        $filterRarity = 'secret';
    }

    $rarityRows = $pdo->query("
        SELECT DISTINCT rarity
        FROM secret_leaderboard
        ORDER BY rarity ASC
    ")->fetchAll(PDO::FETCH_ASSOC);

    if ($filterRarity === 'all') {
        $stmtMain = $pdo->query("
            SELECT user_id, rarity, MAX(display_name) AS display_name, SUM(count) AS count
            FROM secret_leaderboard
            GROUP BY user_id, rarity
            ORDER BY count DESC, user_id ASC, rarity ASC
        ");
        $rows = $stmtMain->fetchAll(PDO::FETCH_ASSOC);
    } else {
        $stmtMain = $pdo->prepare("
            SELECT user_id, rarity, MAX(display_name) AS display_name, SUM(count) AS count
            FROM secret_leaderboard
            WHERE rarity = :rarity
            GROUP BY user_id, rarity
            ORDER BY count DESC, user_id ASC
        ");
        $stmtMain->execute(array(':rarity' => $filterRarity));
        $rows = $stmtMain->fetchAll(PDO::FETCH_ASSOC);
    }

    $secretRows = $pdo->query("
        SELECT user_id, MAX(display_name) AS display_name, SUM(count) AS count
        FROM secret_leaderboard
        WHERE rarity = 'secret'
        GROUP BY user_id
        ORDER BY count DESC, user_id ASC
    ")->fetchAll(PDO::FETCH_ASSOC);

    $allRows = $pdo->query("
        SELECT user_id, MAX(display_name) AS display_name, SUM(count) AS count
        FROM secret_leaderboard
        GROUP BY user_id
        ORDER BY count DESC, user_id ASC
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

function rarity_badge_class($rarity) {
    $key = strtolower(trim((string)$rarity));
    $map = array(
        'common' => 'rarity-common',
        'uncommon' => 'rarity-uncommon',
        'rare' => 'rarity-rare',
        'epic' => 'rarity-epic',
        'legendary' => 'rarity-legendary',
        'mythic' => 'rarity-mythic',
        'secret' => 'rarity-secret',
    );
    return isset($map[$key]) ? $map[$key] : 'rarity-default';
}

function rarity_label($rarity) {
    $label = trim((string)$rarity);
    if ($label === '') {
        return 'unknown';
    }
    return $label;
}

function display_name_label($displayName, $userId) {
    $label = trim((string)$displayName);
    if ($label === '') {
        return (string)$userId;
    }
    return $label;
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
            --bg-start: #0f172a;
            --bg-end: #1f2937;
            --card: #f8fafc;
            --card-strong: #ffffff;
            --text: #0f172a;
            --muted: #64748b;
            --accent: #6366f1;
            --shadow: 0 20px 40px rgba(15, 23, 42, 0.15);
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
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: var(--radius);
            padding: 2rem;
            color: #f8fafc;
            backdrop-filter: blur(8px);
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
            background: rgba(255, 255, 255, 0.2);
            color: #f8fafc;
        }
        .filters {
            margin-top: 1.8rem;
            position: sticky;
            top: 1rem;
            z-index: 10;
        }
        .filters-card {
            background: var(--card-strong);
            border-radius: var(--radius);
            padding: 1.2rem 1.5rem;
            box-shadow: var(--shadow);
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 0.8rem;
        }
        .filters-card strong {
            color: var(--muted);
            font-size: 0.9rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }
        .filter-pill {
            padding: 0.45rem 0.9rem;
            border-radius: 999px;
            background: #e2e8f0;
            color: #0f172a;
            font-weight: 600;
            transition: all 0.2s ease;
        }
        .filter-pill:hover {
            background: var(--accent);
            color: #ffffff;
            transform: translateY(-1px);
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
        }
        .card h2 {
            margin-top: 0;
            font-size: 1.4rem;
        }
        .subtle {
            color: var(--muted);
            font-size: 0.95rem;
        }
        .table-wrap {
            margin-top: 1rem;
            overflow-x: auto;
            border-radius: 12px;
            border: 1px solid #e2e8f0;
            background: #ffffff;
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
            background: #f1f5f9;
            color: #0f172a;
            font-weight: 700;
        }
        tbody tr:nth-child(even) {
            background: #f8fafc;
        }
        tbody tr:hover {
            background: #e0e7ff;
        }
        .rank {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 2.1rem;
            height: 2.1rem;
            border-radius: 12px;
            background: #111827;
            color: #fff;
            font-weight: 700;
            font-size: 0.9rem;
        }
        .rarity {
            display: inline-flex;
            padding: 0.25rem 0.7rem;
            border-radius: 999px;
            font-size: 0.8rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .rarity-common { background: #e2e8f0; color: #1f2937; }
        .rarity-uncommon { background: #d1fae5; color: #065f46; }
        .rarity-rare { background: #dbeafe; color: #1d4ed8; }
        .rarity-epic { background: #f3e8ff; color: #7c3aed; }
        .rarity-legendary { background: #ffedd5; color: #c2410c; }
        .rarity-mythic { background: #fee2e2; color: #b91c1c; }
        .rarity-secret { background: #0f172a; color: #f8fafc; }
        .rarity-default { background: #e5e7eb; color: #111827; }
        .empty {
            padding: 1.5rem;
            border-radius: 12px;
            background: #f1f5f9;
            color: var(--muted);
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
            .filters-card {
                flex-direction: column;
                align-items: flex-start;
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
                <h1>Secret Leaderboard</h1>
                <p>Poslední update: <?php echo htmlspecialchars($lastUpdate, ENT_QUOTES, 'UTF-8'); ?></p>
            </div>
            <span class="badge" title="Filtrovaný pohled na rarity">Aktuální: <?php echo htmlspecialchars($filterRarity, ENT_QUOTES, 'UTF-8'); ?></span>
        </header>

        <div class="filters">
            <div class="filters-card">
                <strong>Filtr</strong>
                <a class="filter-pill" href="?rarity=secret" title="Zobrazit pouze secret rarity">Secret</a>
                <a class="filter-pill" href="?rarity=all" title="Zobrazit všechny rarity">All</a>
                <?php foreach ($rarityRows as $rarityRow): ?>
                    <?php $rarityName = isset($rarityRow['rarity']) ? $rarityRow['rarity'] : ''; ?>
                    <?php if ($rarityName !== '' && $rarityName !== 'secret'): ?>
                        <a class="filter-pill" href="?rarity=<?php echo urlencode($rarityName); ?>" title="Filtrovat <?php echo htmlspecialchars($rarityName, ENT_QUOTES, 'UTF-8'); ?>">
                            <?php echo htmlspecialchars($rarityName, ENT_QUOTES, 'UTF-8'); ?>
                        </a>
                    <?php endif; ?>
                <?php endforeach; ?>
            </div>
        </div>

        <section class="layout">
            <div class="card">
                <h2>Leaderboard detail</h2>
                <p class="subtle">Zobrazení podle filtru rarity s rychlým srovnáním pořadí.</p>
                <?php if (!$rows): ?>
                    <div class="empty">Žádná data.</div>
                <?php else: ?>
                    <div class="table-wrap">
                        <table>
                            <thead>
                            <tr><th>Rank</th><th>User</th><th>Rarity</th><th>Count</th></tr>
                            </thead>
                            <tbody>
                            <?php foreach ($rows as $i => $row): ?>
                                <tr>
                                    <td><span class="rank" title="Pořadí"><?php echo (int)($i + 1); ?></span></td>
                                    <td><?php echo htmlspecialchars(display_name_label($row['display_name'], $row['user_id']), ENT_QUOTES, 'UTF-8'); ?></td>
                                    <td>
                                        <span class="rarity <?php echo rarity_badge_class($row['rarity']); ?>" title="Rarita: <?php echo htmlspecialchars(rarity_label($row['rarity']), ENT_QUOTES, 'UTF-8'); ?>">
                                            <?php echo htmlspecialchars(rarity_label($row['rarity']), ENT_QUOTES, 'UTF-8'); ?>
                                        </span>
                                    </td>
                                    <td><?php echo (int)$row['count']; ?></td>
                                </tr>
                            <?php endforeach; ?>
                            </tbody>
                        </table>
                    </div>
                <?php endif; ?>
            </div>

            <div class="card">
                <h2>Secret</h2>
                <p class="subtle">Rychlý přehled pouze pro secret rarity.</p>
                <?php if (!$secretRows): ?>
                    <div class="empty">Žádná data.</div>
                <?php else: ?>
                    <div class="table-wrap">
                        <table>
                            <thead>
                            <tr><th>Rank</th><th>User</th><th>Count</th></tr>
                            </thead>
                            <tbody>
                            <?php foreach ($secretRows as $i => $row): ?>
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

            <div class="card">
                <h2>All rarities</h2>
                <p class="subtle">Součet napříč raritami pro rychlé porovnání.</p>
                <?php if (!$allRows): ?>
                    <div class="empty">Žádná data.</div>
                <?php else: ?>
                    <div class="table-wrap">
                        <table>
                            <thead>
                            <tr><th>Rank</th><th>User</th><th>Count</th></tr>
                            </thead>
                            <tbody>
                            <?php foreach ($allRows as $i => $row): ?>
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
        </section>

        <p class="footer-note">Layout funguje bez JavaScriptu a je optimalizovaný pro mobilní zařízení.</p>
    </div>
</body>
</html>
