package chi_fiber_sqli

import (
	"database/sql"
	"net/http"
	"strconv"

	"github.com/go-chi/chi/v5"
	"github.com/gofiber/fiber/v2"
	"gorm.io/gorm"
)

func chiURLParamBad(w http.ResponseWriter, r *http.Request, db *sql.DB) {
	id := chi.URLParam(r, "id")
	// ruleid: chi-fiber-sqli
	db.Query("SELECT * FROM users WHERE id = '" + id + "'")
}

func chiURLParamFromCtxBad(r *http.Request, db *gorm.DB) {
	id := chi.URLParamFromCtx(r.Context(), "id")
	// ruleid: chi-fiber-sqli
	db.Where("id = " + id).Find(nil)
}

func fiberQueryBad(c *fiber.Ctx, db *sql.DB) {
	name := c.Query("name")
	// ruleid: chi-fiber-sqli
	db.Exec("DELETE FROM t WHERE name = '" + name + "'")
}

func fiberParamsGorm(c *fiber.Ctx, db *gorm.DB) {
	id := c.Params("id")
	// ruleid: chi-fiber-sqli
	db.Raw("SELECT * FROM users WHERE id = " + id).Scan(nil)
}

func fiberFormValueBad(c *fiber.Ctx, db *sql.DB) {
	name := c.FormValue("name")
	// ruleid: chi-fiber-sqli
	db.QueryRow("SELECT * FROM users WHERE name = '" + name + "'")
}

func chiSafePlaceholder(r *http.Request, db *sql.DB) {
	id := chi.URLParam(r, "id")
	// ok: chi-fiber-sqli
	db.Query("SELECT * FROM users WHERE id = ?", id)
}

func fiberSafeAtoi(c *fiber.Ctx, db *gorm.DB) {
	raw := c.Params("id")
	id, _ := strconv.Atoi(raw)
	// ok: chi-fiber-sqli
	db.Where("id = ?", id).Find(nil)
}
