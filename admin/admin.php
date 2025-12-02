<?php
session_start();
if(!isset($_SESSION['login'])) { header("Location: index.php"); exit; }

$db = new PDO('sqlite:database.sqlite');

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
<tr><th>ID</th><th>Action</th><th>Params</th><th>Created</th></tr>
<?php foreach($rows as $r): ?>
<tr>
<td><?php echo $r['id']; ?></td>
<td><?php echo $r['action']; ?></td>
<td><?php echo $r['params']; ?></td>
<td><?php echo $r['created_at']; ?></td>
</tr>
<?php endforeach; ?>
</table>

</body>
</html>
