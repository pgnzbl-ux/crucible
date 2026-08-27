<?php
/**
 * Distilled from Zentao bi/model.php getMultiData (~L698–710).
 * WHERE fragment + queryWithDriver / PDO::query sink.
 */
class biModel
{
    public function getMultiData($driver, $sql, $filters)
    {
        $wheres = array();
        foreach($filters as $field => $filter)
        {
            $wheres[] = "`$field` {$filter['operator']} {$filter['value']}";
        }
        $whereStr = implode(' AND ', $wheres);
        $sql .= " where $whereStr";
        $rows = $this->queryWithDriver($driver, $sql);
        return $rows;
    }

    public function queryWithDriver($driver, $sql)
    {
        $pdo = new PDO('mysql:host=localhost;dbname=zentao', 'u', 'p');
        return $pdo->query($sql);
    }
}
