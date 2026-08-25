<?php

// True positives — SQL fragments without SELECT keyword (Zentao-like)

function tp_implode_in_list($default) {
    // ruleid: sql-fragment-concat
    $value = "('" . implode("', '", $default) . "')";
    return $value;
}

function tp_quoted_concat($x) {
    // ruleid: sql-fragment-concat
    return "'" . $x . "'";
}

function tp_field_op_value($filters) {
    $wheres = [];
    foreach ($filters as $field => $filter) {
        // ruleid: sql-fragment-concat
        $wheres[] = "`$field` {$filter['operator']} {$filter['value']}";
    }
    return $wheres;
}

function tp_where_append($sql, $whereStr) {
    // ruleid: sql-fragment-concat
    $sql .= " where $whereStr";
    return $sql;
}

function tp_where_dot($whereStr) {
    // ruleid: sql-fragment-concat
    return " where " . $whereStr;
}

function tp_order_by_dot($col) {
    // ruleid: sql-fragment-concat
    return " ORDER BY " . $col;
}

function tp_limit_dot($n) {
    // ruleid: sql-fragment-concat
    return " LIMIT " . $n;
}

function tp_offset_dot($n) {
    // ruleid: sql-fragment-concat
    return " OFFSET " . $n;
}

function tp_like_percent($q) {
    // ruleid: sql-fragment-concat
    return "'%" . $q . "%'";
}

function tp_and_dot($frag) {
    // ruleid: sql-fragment-concat
    return " AND " . $frag;
}

function tp_or_dot($frag) {
    // ruleid: sql-fragment-concat
    return " OR " . $frag;
}

function tp_order_by_interp($sql, $col) {
    // ruleid: sql-fragment-concat
    $sql .= " ORDER BY $col";
    return $sql;
}

// True negatives

function tn_parameterized_in() {
    // ok: sql-fragment-concat
    $placeholders = implode(',', array_fill(0, 3, '?'));
    return $placeholders;
}

function tn_safe_literal_where() {
    // ok: sql-fragment-concat
    return ' where id = 1';
}

function tn_select_keyword_full_query($id) {
    // Full queries with SELECT are out of scope for this fragment rule
    // (community tainted-sql-string may still hit).
    // ok: sql-fragment-concat
    return "SELECT * FROM t WHERE id = " . (int)$id;
}

function tn_bare_dot_concat($x) {
    // Bare ".$X." is intentionally NOT matched (too noisy).
    // ok: sql-fragment-concat
    return "prefix" . $x . "suffix";
}
