import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.session.SqlSession;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import jakarta.servlet.http.HttpServletRequest;

class MybatisJavaSelectConcat {

    public void badSelectList(@RequestParam String name, SqlSession sqlSession) {
        String sql = "com.example.UserMapper.findByName" + name;
        // ruleid: mybatis-java-select-concat
        sqlSession.selectList(sql);
    }

    public void badSelectOne(@RequestBody String id, SqlSession sqlSession) {
        // ruleid: mybatis-java-select-concat
        sqlSession.selectOne("com.example.UserMapper.findById" + id);
    }

    public void badUpdate(HttpServletRequest request, SqlSession sqlSession) {
        String q = request.getParameter("q");
        // ruleid: mybatis-java-select-concat
        sqlSession.update(q);
    }

    public void badInsert(@RequestParam String stmt, SqlSession sqlSession) {
        // ruleid: mybatis-java-select-concat
        sqlSession.insert(stmt);
    }

    public void badDelete(@RequestParam String stmt, SqlSession sqlSession) {
        // ruleid: mybatis-java-select-concat
        sqlSession.delete(stmt);
    }

    static final String COL = "name";

    interface BadMapper {
        // ruleid: mybatis-java-annotation-concat
        @Select("SELECT * FROM users WHERE " + COL + " = #{v}")
        Object findByCol();
    }

    interface BadMapperSuffix {
        // ruleid: mybatis-java-annotation-concat
        @Select(COL + " = #{v}")
        Object findSuffix();
    }

    public void safeSelectList(@RequestParam String name, SqlSession sqlSession) {
        // ok: mybatis-java-select-concat
        sqlSession.selectList("com.example.UserMapper.findByName", name);
    }

    interface SafeMapper {
        // ok: mybatis-java-annotation-concat
        @Select("SELECT * FROM users WHERE name = #{name}")
        Object findSafe(String name);
    }
}
