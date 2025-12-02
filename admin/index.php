<?php
// PHP 5.6 SQLite basic admin login
session_start();
$db = new PDO('sqlite:database.sqlite');

// Create table if not exists
$db->exec("CREATE TABLE IF NOT EXISTS admins (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, password TEXT)");
$db->exec("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT, params TEXT, created_at TEXT, processed INTEGER DEFAULT 0, processed_at TEXT)");

// Ensure new columns exist even on older DBs
$columns = $db->query("PRAGMA table_info(tasks)")->fetchAll(PDO::FETCH_ASSOC);
$columnNames = array_column($columns, 'name');
if(!in_array('processed', $columnNames)) {
    $db->exec("ALTER TABLE tasks ADD COLUMN processed INTEGER NOT NULL DEFAULT 0");
}
if(!in_array('processed_at', $columnNames)) {
    $db->exec("ALTER TABLE tasks ADD COLUMN processed_at TEXT");
}

$stmt = $db->query("SELECT COUNT(*) FROM admins");
if ($stmt->fetchColumn() == 0) {
    $insert = $db->prepare("INSERT INTO admins (username, password) VALUES (?, ?)");
    $insert->execute(array('admin', md5('admin123')));
}

if(isset($_POST['username']) && isset($_POST['password'])) {
    $u = $_POST['username'];
    $p = md5($_POST['password']);
    $q = $db->prepare("SELECT * FROM admins WHERE username=? AND password=?");
    $q->execute(array($u, $p));
    if($q->fetch()) {
        $_SESSION['login'] = true;
        header("Location: admin.php");
        exit;
    } else {
        $error = "Invalid login";
    }
}
?>
<!DOCTYPE html>
<html>
<head><title>Login</title></head>
<body>
<h2>Admin Login</h2>
<form method="POST">
<input type="text" name="username" placeholder="Username"><br>
<input type="password" name="password" placeholder="Password"><br>
<button type="submit">Login</button>
</form>
<?php if(isset($error)) echo $error; ?>
</body>
</html>
