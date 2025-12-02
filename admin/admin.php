<?php
session_start();
if(!isset($_SESSION['login'])) { header("Location: index.php"); exit; }

$db = new PDO('sqlite:database.sqlite');

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

</body>
</html>
