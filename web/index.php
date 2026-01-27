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

  // Uložení: smažeme staré a uložíme nové
  $pdo->beginTransaction();
  $pdo->exec("DELETE FROM secret_leaderboard");
  $stmt = $pdo->prepare("INSERT INTO secret_leaderboard (user_id, count) VALUES (:user_id, :count)");

  foreach ($entries as $row) {
    if (!isset($row['user_id'], $row['count'])) continue;
    $userId = (int)$row['user_id'];
    $count = (int)$row['count'];
    $stmt->execute([':user_id' => $userId, ':count' => $count]);
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
$rows = $pdo->query("SELECT user_id, count FROM secret_leaderboard ORDER BY count DESC, user_id ASC")
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
        <tr><th>#</th><th>User ID</th><th>Count</th></tr>
      </thead>
      <tbody>
        <?php foreach ($rows as $i => $row): ?>
          <tr>
            <td><?= $i + 1 ?></td>
            <td><?= (int)$row['user_id'] ?></td>
            <td><?= (int)$row['count'] ?></td>
          </tr>
        <?php endforeach; ?>
      </tbody>
    </table>
  <?php endif; ?>
</body>
</html>
