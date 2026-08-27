class JdbcSqlFragmentConcat {

    String badWhere(String filter) {
        // ruleid: jdbc-sql-fragment-concat
        return " WHERE " + filter;
    }

    String badOrderBy(String col) {
        // ruleid: jdbc-sql-fragment-concat
        return " ORDER BY " + col;
    }

    String badAnd(String clause) {
        // ruleid: jdbc-sql-fragment-concat
        return " AND " + clause;
    }

    String badWhereContinued(String filter) {
        // ruleid: jdbc-sql-fragment-concat
        return " WHERE " + filter + " LIMIT 10";
    }

    String safeLiteralWhere() {
        // ok: jdbc-sql-fragment-concat
        return " WHERE id = 1";
    }

    String safeSelectConcat(int id) {
        // Full query with SELECT is out of scope for this fragment rule.
        // ok: jdbc-sql-fragment-concat
        return "SELECT * FROM t WHERE id = " + id;
    }
}
