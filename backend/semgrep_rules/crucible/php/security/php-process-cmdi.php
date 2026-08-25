<?php

use Symfony\Component\Process\Process;
use Illuminate\Support\Facades\Process as IlluminateProcess;

// True positives

function tp_get_new_process() {
    $cmd = $_GET['cmd'];
    // ruleid: php-process-cmdi
    $p = new Process($cmd);
    $p->run();
}

function tp_post_from_shell() {
    $cmd = $_POST['cmd'];
    // ruleid: php-process-cmdi
    $p = Process::fromShellCommandline($cmd);
    $p->run();
}

function tp_request_set_command_line($req) {
    $cmd = $req->get('cmd');
    $p = new Process(['echo']);
    // ruleid: php-process-cmdi
    $p->setCommandLine($cmd);
}

function tp_query_get_illuminate_run($req) {
    $cmd = $req->query->get('cmd');
    // ruleid: php-process-cmdi
    IlluminateProcess::run($cmd); // alias → Process facade ::run
}

function tp_illuminate_fqcn_run() {
    $cmd = $_GET['cmd'];
    // ruleid: php-process-cmdi
    \Illuminate\Support\Facades\Process::run($cmd);
}

function tp_get_concat_shell() {
    $name = $_GET['name'];
    $cmd = 'ls ' . $name;
    // ruleid: php-process-cmdi
    Process::fromShellCommandline($cmd);
}

// True negatives

function tn_literal_process() {
    // ok: php-process-cmdi
    $p = new Process(['ls', '-la']);
    $p->run();
}

function tn_escaped() {
    $name = escapeshellarg($_GET['name']);
    // ok: php-process-cmdi
    Process::fromShellCommandline('ls ' . $name);
}

function tn_argv_array() {
    $arg = $_GET['f'];
    // ok: php-process-cmdi
    $p = new Process(['cat', $arg]);
    $p->run();
}
