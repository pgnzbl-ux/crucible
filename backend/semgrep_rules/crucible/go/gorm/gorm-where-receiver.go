package gorm_where_receiver

import (
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"
	"github.com/labstack/echo/v4"
	"gorm.io/gorm"
)

type Repo struct {
	db *gorm.DB
}

func (r *Repo) ginWhereBad(c *gin.Context) {
	id := c.Param("id")
	// ruleid: gorm-where-receiver
	r.db.Where("id = " + id).Find(nil)
}

func (r *Repo) ginRawBad(c *gin.Context) {
	name := c.Query("name")
	// ruleid: gorm-where-receiver
	r.db.Raw("SELECT * FROM users WHERE name = '" + name + "'").Scan(nil)
}

func (r *Repo) echoWhereBad(c echo.Context) {
	id := c.Param("id")
	// ruleid: gorm-where-receiver
	r.db.Where("id = " + id).Find(nil)
}

func (r *Repo) httpFormWhereBad(req *http.Request) {
	name := req.FormValue("name")
	// ruleid: gorm-where-receiver
	r.db.Where("name = '" + name + "'").Find(nil)
}

func (r *Repo) httpQueryGetRaw(req *http.Request) {
	q := req.URL.Query().Get("q")
	// ruleid: gorm-where-receiver
	r.db.Raw("SELECT * FROM t WHERE q = '" + q + "'").Scan(nil)
}

func (r *Repo) findOnlyOk(c *gin.Context) {
	// Find alone is not a sink for this rule (no Where/Raw with taint).
	id := c.Param("id")
	_ = id
	// ok: gorm-where-receiver
	r.db.Find(nil)
}

func (r *Repo) firstOnlyOk(c *gin.Context) {
	id := c.Query("id")
	_ = id
	// ok: gorm-where-receiver
	r.db.First(nil)
}

func (r *Repo) safePlaceholder(c *gin.Context) {
	id := c.Param("id")
	// ok: gorm-where-receiver
	r.db.Where("id = ?", id).Find(nil)
}

func (r *Repo) safeAtoi(c *gin.Context) {
	raw := c.Param("id")
	id, _ := strconv.Atoi(raw)
	// ok: gorm-where-receiver
	r.db.Where("id = ?", id).Find(nil)
}
