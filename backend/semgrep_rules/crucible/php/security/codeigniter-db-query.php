<?php

// True positives — CI3

class Ci3Controller {
    public $input;
    public $db;

    function tp_ci3_get_query() {
        $id = $this->input->get('id');
        // ruleid: codeigniter-db-query
        $this->db->query("SELECT * FROM t WHERE id = " . $id);
    }

    function tp_ci3_post_order_by() {
        $col = $this->input->post('sort');
        // ruleid: codeigniter-db-query
        $this->db->order_by($col);
    }
}

// True positives — CI4

class Ci4Controller {
    public $request;

    function tp_ci4_get_query($db) {
        $id = $this->request->getGet('id');
        // ruleid: codeigniter-db-query
        $db->query("SELECT * FROM t WHERE id = " . $id);
    }

    function tp_ci4_post_query($db) {
        $name = $this->request->getPost('name');
        // ruleid: codeigniter-db-query
        $db->query("DELETE FROM t WHERE name = '$name'");
    }
}

// True negatives

class CiSafe {
    public $input;
    public $db;
    public $request;

    function tn_ci3_bound() {
        $id = $this->input->get('id');
        // ok: codeigniter-db-query
        $this->db->query('SELECT * FROM t WHERE id = ?', [$id]);
    }

    function tn_ci3_int() {
        $id = (int) $this->input->get('id');
        // ok: codeigniter-db-query
        $this->db->query("SELECT * FROM t WHERE id = " . $id);
    }

    function tn_ci4_literal($db) {
        // ok: codeigniter-db-query
        $db->query('SELECT 1');
    }

    function tn_ci3_literal_order() {
        // ok: codeigniter-db-query
        $this->db->order_by('id', 'DESC');
    }
}
