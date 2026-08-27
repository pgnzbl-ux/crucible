<?php

use Symfony\Component\HttpFoundation\Request;

// True positives

function tp_query_get_execute_query(Request $req, $conn) {
    $id = $req->query->get('id');
    // ruleid: symfony-request-sqli
    $conn->executeQuery("SELECT * FROM t WHERE id = " . $id);
}

function tp_request_get_execute_statement(Request $req, $conn) {
    $name = $req->request->get('name');
    // ruleid: symfony-request-sqli
    $conn->executeStatement("DELETE FROM t WHERE name = '$name'");
}

function tp_req_get_create_native(Request $req, $em) {
    $col = $req->get('sort');
    // ruleid: symfony-request-sqli
    $em->createNativeQuery("SELECT * FROM t ORDER BY " . $col, null);
}

function tp_get_content_json_where(Request $req, $qb) {
    $raw = $req->getContent();
    $data = json_decode($raw, true);
    $frag = $data['where'];
    // ruleid: symfony-request-sqli
    $qb->where($frag);
}

function tp_query_and_where(Request $req, $qb) {
    $cond = $req->query->get('cond');
    // ruleid: symfony-request-sqli
    $qb->andWhere($cond);
}

function tp_query_order_by(Request $req, $qb) {
    $col = $req->query->get('order');
    // ruleid: symfony-request-sqli
    $qb->orderBy($col);
}

// True negatives

function tn_bound_execute(Request $req, $conn) {
    $id = $req->query->get('id');
    // ok: symfony-request-sqli
    $conn->executeQuery('SELECT * FROM t WHERE id = ?', [$id]);
}

function tn_int_cast(Request $req, $conn) {
    $id = (int) $req->query->get('id');
    // ok: symfony-request-sqli
    $conn->executeQuery("SELECT * FROM t WHERE id = " . $id);
}

function tn_literal_where($qb) {
    // ok: symfony-request-sqli
    $qb->where('id = 1');
}
