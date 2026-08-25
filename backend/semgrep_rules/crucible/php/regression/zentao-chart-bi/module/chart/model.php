<?php
/**
 * Distilled from Zentao chart/model.php getFilterFormat (~L737).
 * Original: $value = "('" . implode("', '", $default) . "')";
 */
class chartModel
{
    public function getFilterFormat($filters)
    {
        $filterFormat = [];
        foreach($filters as $field => $filter)
        {
            $default = $filter['default'];
            if(is_array($default))
            {
                // CWE-89 fragment: IN-list via implode, no SELECT keyword
                $value = "('" . implode("', '", $default) . "')";
                $filterFormat[$field] = array('operator' => 'IN', 'value' => $value);
            }
            else
            {
                $filterFormat[$field] = array(
                    'operator' => $filter['operator'],
                    'value'    => "'" . $default . "'"
                );
            }
        }
        return $filterFormat;
    }
}
