<?php
session_start();

if(!isset($_SESSION['login'])) {
    header('Content-Type: application/json');
    http_response_code(401);
    echo json_encode(array('ok' => false, 'message' => 'Neautorizovaný přístup. Přihlaste se prosím.'));
    exit;
}

if($_SERVER['REQUEST_METHOD'] === 'GET') {
    header('Content-Type: text/html; charset=utf-8');
    ?>
    <!doctype html>
    <html lang="cs">
    <head>
        <meta charset="utf-8">
        <title>Rebirth formulář</title>
    </head>
    <body>
        <h1>Uložení rebirthů</h1>
        <form method="POST">
            <input type="hidden" name="ajax" value="update_rebirth">

            <label>
                ID uživatele:<br>
                <input type="text" name="user_id" required>
            </label>
            <br><br>

            <label>
                Zobrazované jméno (nepovinné):<br>
                <input type="text" name="display_name">
            </label>
            <br><br>

            <label>
                Rebirthy:<br>
                <input type="text" name="rebirths" required>
            </label>
            <br><br>

            <button type="submit">Uložit</button>
        </form>
    </body>
    </html>
    <?php
    exit;
}

if($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(array('ok' => false, 'message' => 'Metoda není podporována. Použijte POST.'));
    exit;
}

header('Content-Type: application/json');

$action = isset($_POST['ajax']) ? $_POST['ajax'] : '';
if($action !== 'update_rebirth') {
    http_response_code(400);
    echo json_encode(array('ok' => false, 'message' => 'Neznámý požadavek.'));
    exit;
}

$db = new PDO('sqlite:' . __DIR__ . '/database.sqlite');
$db->exec("CREATE TABLE IF NOT EXISTS member_rebirths (
    user_id TEXT PRIMARY KEY,
    display_name TEXT,
    rebirths TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
)");

function parse_rebirth_to_number($value) {
    if($value === null || $value === '') {
        return null;
    }

    if(is_numeric($value)) {
        return (float)$value;
    }

    $normalized = preg_replace('/[^0-9.,-]/', '', $value);
    if($normalized === null || $normalized === '') {
        return null;
    }

    $normalized = str_replace(',', '.', $normalized);
    return is_numeric($normalized) ? (float)$normalized : null;
}

function format_rebirth_number($number) {
    return is_numeric($number) ? rtrim(rtrim(number_format((float)$number, 2, '.', ''), '0'), '.') : '';
}

function describe_rebirth_delta($previous, $current) {
    $prevNumeric = parse_rebirth_to_number($previous);
    $currNumeric = parse_rebirth_to_number($current);

    if($prevNumeric === null || $currNumeric === null) {
        return '';
    }

    $delta = $currNumeric - $prevNumeric;
    if($delta === 0.0) {
        return '';
    }

    $sign = $delta > 0 ? '+' : '';
    return $sign . format_rebirth_number($delta);
}

function save_member_rebirths($db, $userId, $displayName, $rebirths) {
    $now = date('Y-m-d H:i:s');
    $stmt = $db->prepare("INSERT INTO member_rebirths (user_id, display_name, rebirths, updated_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET display_name = excluded.display_name, rebirths = excluded.rebirths, updated_at = excluded.updated_at");
    $stmt->execute(array($userId, $displayName, $rebirths, $now));
    return $now;
}

$userId = isset($_POST['user_id']) ? trim($_POST['user_id']) : '';
$rebirthInput = isset($_POST['rebirths']) ? $_POST['rebirths'] : null;
$displayName = isset($_POST['display_name']) ? trim($_POST['display_name']) : '';

if($userId === '') {
    http_response_code(400);
    echo json_encode(array('ok' => false, 'message' => 'Chybí ID uživatele.'));
    exit;
}

$rebirths = trim($rebirthInput);
$previousValue = null;

$existingStmt = $db->prepare("SELECT rebirths FROM member_rebirths WHERE user_id = ?");
$existingStmt->execute(array($userId));
$existingRow = $existingStmt->fetch(PDO::FETCH_ASSOC);
if($existingRow) {
    $previousValue = $existingRow['rebirths'];
}

if($rebirths === '') {
    http_response_code(400);
    echo json_encode(array('ok' => false, 'message' => 'Zadejte hodnotu rebirthu.'));
    exit;
}

if(mb_strlen($rebirths) > 255) {
    http_response_code(400);
    echo json_encode(array('ok' => false, 'message' => 'Hodnota rebirthu je příliš dlouhá (max. 255 znaků).'));
    exit;
}

$storedAt = save_member_rebirths($db, $userId, $displayName !== '' ? $displayName : $userId, $rebirths);
$delta = describe_rebirth_delta($previousValue, $rebirths);

echo json_encode(array(
    'ok' => true,
    'message' => 'Rebirthy byly uloženy.',
    'updated_at' => $storedAt,
    'rebirths' => $rebirths,
    'delta' => $delta
));
