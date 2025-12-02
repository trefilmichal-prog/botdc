<?php
// PHP 5.6 SQLite basic admin login
session_start();
$db = new PDO('sqlite:database.sqlite');

// Create table if not exists
$db->exec("CREATE TABLE IF NOT EXISTS admins (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, password TEXT)");
// Simple key/value settings table
$db->exec("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)");

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
<head>
    <title>Login</title>
    <style>
        body {
            margin: 0;
            padding: 0;
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #0f172a, #0b1120);
            color: #e2e8f0;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .panel {
            background: #111827;
            border: 1px solid #1f2937;
            border-radius: 14px;
            padding: 28px 26px;
            width: 360px;
            box-shadow: 0 18px 40px rgba(0,0,0,0.35);
        }
        h2 {
            margin-top: 0;
            text-align: center;
            color: #93c5fd;
        }
        label {
            display: block;
            margin: 12px 0 6px;
            font-size: 14px;
        }
        input[type="text"], input[type="password"] {
            width: 100%;
            padding: 12px;
            border-radius: 10px;
            border: 1px solid #1f2937;
            background: #0b1221;
            color: #e2e8f0;
            font-size: 15px;
        }
        button {
            margin-top: 14px;
            width: 100%;
            padding: 12px;
            border: none;
            border-radius: 10px;
            background: linear-gradient(135deg, #8b5cf6, #6366f1);
            color: #fff;
            font-weight: 700;
            cursor: pointer;
            font-size: 15px;
        }
        button:hover { background: linear-gradient(135deg, #a855f7, #4f46e5); }
        .error {
            margin-top: 12px;
            background: rgba(248,113,113,0.15);
            border: 1px solid rgba(248,113,113,0.35);
            color: #fecaca;
            padding: 10px 12px;
            border-radius: 10px;
            text-align: center;
            font-size: 14px;
        }
    </style>
</head>
<body>
    <div class="panel">
        <h2>Admin Login</h2>
        <form method="POST">
            <label for="username">Username</label>
            <input type="text" id="username" name="username" placeholder="Username">
            <label for="password">Password</label>
            <input type="password" id="password" name="password" placeholder="Password">
            <button type="submit">Login</button>
        </form>
        <?php if(isset($error)): ?>
            <div class="error"><?php echo htmlspecialchars($error); ?></div>
        <?php endif; ?>
    </div>
</body>
</html>
