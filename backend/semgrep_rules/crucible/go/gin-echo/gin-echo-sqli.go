package gin_echo_sqli

import (
	"database/sql"
	"strconv"

	"github.com/gin-gonic/gin"
	"github.com/labstack/echo/v4"
	"gorm.io/gorm"
)

func ginQueryBad(c *gin.Context, db *sql.DB) {
	name := c.Query("name")
	// ruleid: gin-echo-sqli
	db.Query("SELECT * FROM users WHERE name = '" + name + "'")
}

func ginParamGormWhere(c *gin.Context, db *gorm.DB) {
	id := c.Param("id")
	// ruleid: gin-echo-sqli
	db.Where("id = " + id).Find(nil)
}

func ginPostFormBad(c *gin.Context, db *sql.DB) {
	name := c.PostForm("name")
	// ruleid: gin-echo-sqli
	db.Exec("DELETE FROM t WHERE name = '" + name + "'")
}

func echoQueryBad(c echo.Context, db *sql.DB) {
	name := c.QueryParam("name")
	// ruleid: gin-echo-sqli
	db.QueryRow("SELECT * FROM users WHERE name = '" + name + "'")
}

func ginSafePlaceholder(c *gin.Context, db *sql.DB) {
	name := c.Query("name")
	// ok: gin-echo-sqli
	db.Query("SELECT * FROM users WHERE name = ?", name)
}

func ginSafeAtoi(c *gin.Context, db *gorm.DB) {
	raw := c.Param("id")
	id, _ := strconv.Atoi(raw)
	// ok: gin-echo-sqli
	db.Where("id = ?", id).Find(nil)
}
