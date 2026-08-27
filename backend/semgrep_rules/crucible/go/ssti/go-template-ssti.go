package go_template_ssti

import (
	"html/template"
	"net/http"
	texttemplate "text/template"

	"github.com/gin-gonic/gin"
	"github.com/go-chi/chi/v5"
	"github.com/gofiber/fiber/v2"
	"github.com/labstack/echo/v4"
)

func ginParseBad(c *gin.Context) {
	body := c.Query("tpl")
	// ruleid: go-template-ssti
	template.New("t").Parse(body)
}

func echoMustParseBad(c echo.Context) {
	body := c.FormValue("tpl")
	// ruleid: go-template-ssti
	template.Must(template.New("t").Parse(body))
}

func chiTextParseBad(r *http.Request) {
	body := chi.URLParam(r, "tpl")
	tmpl := texttemplate.New("t")
	// ruleid: go-template-ssti
	tmpl.Parse(body)
}

func fiberParseBad(c *fiber.Ctx) {
	body := c.Query("tpl")
	// ruleid: go-template-ssti
	template.New("x").Parse(body)
}

func ginParseFilesNotThisRule(c *gin.Context) {
	path := c.Query("path")
	// ok: go-template-ssti
	template.ParseFiles(path)
}

func ginSafeLiteral(c *gin.Context) {
	_ = c.Query("tpl")
	// ok: go-template-ssti
	template.New("t").Parse("Hello {{.Name}}")
}
