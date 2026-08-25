<?php

// True positives — PDO / mysqli / queryWithDriver sinks

function tp_pdo_query_get() {
    $id = $_GET['id'];
    $pdo = new PDO('sqlite::memory:');
    // ruleid: pdo-mysqli-query-sink
    $pdo->query("SELECT * FROM t WHERE id = " . $id);
}

function tp_pdo_exec_post() {
    $name = $_POST['name'];
    $pdo = new PDO('sqlite::memory:');
    // ruleid: pdo-mysqli-query-sink
    $pdo->exec("DELETE FROM t WHERE name = '$name'");
}

function tp_mysqli_query() {
    $q = $_REQUEST['q'];
    $db = mysqli_connect('localhost', 'u', 'p', 'db');
    // ruleid: pdo-mysqli-query-sink
    mysqli_query($db, "SELECT * FROM t WHERE q = '$q'");
}

function tp_json_decode_then_query() {
    $raw = $_POST['filters'];
    $filters = json_decode($raw, true);
    $sql = "SELECT 1 WHERE x = " . $filters['default'];
    $pdo = new PDO('sqlite::memory:');
    // ruleid: pdo-mysqli-query-sink
    $pdo->query($sql);
}

function tp_query_with_driver() {
    $frag = $_GET['where'];
    $sql = "SELECT * FROM t" . $frag;
    $model = new stdClass();
    // ruleid: pdo-mysqli-query-sink
    $model->queryWithDriver('mysql', $sql);
}

// True negatives

function tn_prepared() {
    $id = $_GET['id'];
    $pdo = new PDO('sqlite::memory:');
    // ok: pdo-mysqli-query-sink
    $stmt = $pdo->prepare('SELECT * FROM t WHERE id = ?');
    $stmt->execute([$id]);
}

function tn_literal_query() {
    $pdo = new PDO('sqlite::memory:');
    // ok: pdo-mysqli-query-sink
    $pdo->query('SELECT 1');
}

function tn_escaped() {
    $db = mysqli_connect('localhost', 'u', 'p', 'db');
    $id = mysqli_real_escape_string($db, $_GET['id']);
    // ok: pdo-mysqli-query-sink
    mysqli_query($db, "SELECT * FROM t WHERE id = '$id'");
}
