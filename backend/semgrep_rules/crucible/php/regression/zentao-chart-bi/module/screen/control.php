<?php
/**
 * Distilled from Zentao screen/control.php ajaxGetChart chain:
 * json_decode($_POST['filters']) → merge → chart getFilterFormat → bi getMultiData.
 */
class screen extends control
{
    public function ajaxGetChart()
    {
        $filters = json_decode($this->post->filters, true);
        if(empty($filters)) $filters = json_decode($_POST['filters'], true);

        $chart = $this->loadModel('chart');
        $bi    = $this->loadModel('bi');

        $filterFormat = $chart->getFilterFormat($filters);
        $sql = 'SELECT * FROM zt_story';
        $rows = $bi->getMultiData('mysql', $sql, $filterFormat);
        echo json_encode($rows);
    }
}
