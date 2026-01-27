<?php
declare(strict_types=1);

const DB_PATH = __DIR__ . '/leaderboard.sqlite';
// POST endpoint předpokládá ochranu na úrovni reverse proxy (bez aplikačního ověřování).

header('Content-Type: text/html; charset=utf-8');

// DB init
if (!extension_loaded('pdo_sqlite')) {
  http_response_code(500);
  echo 'Missing PHP extension: pdo_sqlite. Please install/enable it.';
  exit;
}

try {
  $pdo = new PDO('sqlite:' . DB_PATH);
  $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

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
} catch (PDOException $e) {
  http_response_code(500);
  error_log($e->getMessage());
  echo 'Database unavailable';
  exit;
}

// Podporované rarity (držet se bot logiky): secret, mysterious, divine, supreme, aura.
$supportedRarities = ['secret', 'mysterious', 'divine', 'supreme', 'aura'];

try {
  $columns = $pdo->query("PRAGMA table_info('secret_leaderboard')")->fetchAll(PDO::FETCH_ASSOC);
  $hasRarity = false;
  foreach ($columns as $column) {
    if (($column['name'] ?? '') === 'rarity') {
      $hasRarity = true;
      break;
    }
  }
  if (!$hasRarity) {
    $pdo->exec("ALTER TABLE secret_leaderboard ADD COLUMN rarity TEXT NOT NULL DEFAULT 'secret'");
  }
} catch (PDOException $e) {
  http_response_code(500);
  error_log($e->getMessage());
  echo 'Database unavailable';
  exit;
}

// Helpers
function bad_request(string $msg): void {
  http_response_code(400);
  echo htmlspecialchars($msg, ENT_QUOTES, 'UTF-8');
  exit;
}

$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';

if ($method === 'POST') {
  $raw = file_get_contents('php://input');
  $data = json_decode($raw, true);

  if (!is_array($data)) {
    bad_request('Invalid JSON.');
  }

  $entries = $data['entries'] ?? null;
  if (!is_array($entries)) {
    bad_request('Missing entries.');
  }

  $aggregated = [];
  foreach ($entries as $row) {
    if (!is_array($row)) {
      bad_request('Invalid entry.');
    }
    if (!isset($row['user_id'], $row['rarity'], $row['count'])) {
      bad_request('Each entry must include user_id, rarity, and count.');
    }
    $userId = (int)$row['user_id'];
    $rarity = (string)$row['rarity'];
    $count = (int)$row['count'];
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
      $aggregated[$key] = ['user_id' => $userId, 'rarity' => $rarity, 'count' => 0];
    }
    $aggregated[$key]['count'] += $count;
  }

  // Uložení: smažeme staré a uložíme nové
  $pdo->beginTransaction();
  $pdo->exec("DELETE FROM secret_leaderboard");
  $stmt = $pdo->prepare("INSERT INTO secret_leaderboard (user_id, rarity, count) VALUES (:user_id, :rarity, :count)");

  foreach ($aggregated as $row) {
    $stmt->execute([
      ':user_id' => $row['user_id'],
      ':rarity' => $row['rarity'],
      ':count' => $row['count'],
    ]);
  }

  // uložíme čas posledního update
  $stmtMeta = $pdo->prepare("INSERT INTO meta (key, value) VALUES ('last_update', :v)
    ON CONFLICT(key) DO UPDATE SET value = excluded.value");
  $stmtMeta->execute([':v' => (string)($data['generated_at'] ?? date('c'))]);

  $pdo->commit();

  echo "OK";
  exit;
}

// GET – zobrazit leaderboard
$rows = $pdo->query("SELECT user_id, rarity, SUM(count) AS count
                     FROM secret_leaderboard
                     GROUP BY user_id, rarity
                     ORDER BY count DESC, user_id ASC, rarity ASC")
           ->fetchAll(PDO::FETCH_ASSOC);

$lastUpdate = $pdo->query("SELECT value FROM meta WHERE key='last_update'")
                  ->fetchColumn() ?: 'N/A';
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
  <p>Poslední update: <?= htmlspecialchars($lastUpdate, ENT_QUOTES, 'UTF-8') ?></p>

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
            <td><?= $i + 1 ?></td>
            <td><?= (int)$row['user_id'] ?></td>
            <td><?= htmlspecialchars($row['rarity'], ENT_QUOTES, 'UTF-8') ?></td>
            <td><?= (int)$row['count'] ?></td>
          </tr>
        <?php endforeach; ?>
      </tbody>
    </table>
  <?php endif; ?>
</body>
</html>
