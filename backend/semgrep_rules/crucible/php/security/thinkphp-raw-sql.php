<?php

use think\facade\Db;

// True positives

function tp_get_db_query() {
    $id = $_GET['id'];
    // ruleid: thinkphp-raw-sql
    Db::query("SELECT * FROM t WHERE id = " . $id);
}

function tp_post_db_execute() {
    $name = $_POST['name'];
    // ruleid: thinkphp-raw-sql
    Db::execute("DELETE FROM t WHERE name = '$name'");
}

function tp_request_db_raw() {
    $expr = $_REQUEST['expr'];
    // ruleid: thinkphp-raw-sql
    Db::raw($expr);
}

function tp_param_where_raw($req, $model) {
    $cond = $req->param('cond');
    // ruleid: thinkphp-raw-sql
    $model->whereRaw($cond);
}

function tp_input_order($model) {
    $col = input('sort');
    // ruleid: thinkphp-raw-sql
    $model->order($col);
}

function tp_get_field($model) {
    $cols = $_GET['fields'];
    // ruleid: thinkphp-raw-sql
    $model->field($cols);
}

// True negatives

function tn_bound_query() {
    $id = $_GET['id'];
    // ok: thinkphp-raw-sql
    Db::query('SELECT * FROM t WHERE id = ?', [$id]);
}

function tn_int_cast($model) {
    $id = (int) $_GET['id'];
    // ok: thinkphp-raw-sql
    $model->whereRaw('id = ' . $id);
}

function tn_literal_order($model) {
    // ok: thinkphp-raw-sql
    $model->order('id desc');
}
