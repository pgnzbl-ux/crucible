<%@ page contentType="text/html;charset=UTF-8" %>
<html><body>

<!-- ruleid: jsp-scriptlet-taint -->
<% String q = request.getParameter("q"); stmt.execute("SELECT * FROM t WHERE x='" + q + "'"); %>

<!-- ruleid: jsp-scriptlet-taint -->
<% String name = request.getParameter("name"); out.print(name); %>

<!-- ruleid: jsp-scriptlet-taint -->
<% String cmd = request.getParameter("cmd"); Runtime.getRuntime().exec(cmd); %>

<!-- ok: jsp-scriptlet-taint -->
<% String id = request.getParameter("id"); int n = Integer.parseInt(id); %>

<!-- ok: jsp-scriptlet-taint -->
<p>Hello</p>

</body></html>
