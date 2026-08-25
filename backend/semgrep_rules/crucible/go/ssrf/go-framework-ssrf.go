package go_framework_ssrf

import (
	"context"
	"net/http"

	"github.com/gin-gonic/gin"
	"github.com/go-chi/chi/v5"
	"github.com/gofiber/fiber/v2"
	"github.com/labstack/echo/v4"
)

func ginGetBad(c *gin.Context) {
	u := c.Query("url")
	// ruleid: go-framework-ssrf
	http.Get(u)
}

func echoNewRequestBad(c echo.Context) {
	u := c.QueryParam("url")
	// ruleid: go-framework-ssrf
	http.NewRequest("GET", u, nil)
}

func chiNewRequestWithContextBad(r *http.Request) {
	u := chi.URLParam(r, "url")
	// ruleid: go-framework-ssrf
	http.NewRequestWithContext(context.Background(), "GET", u, nil)
}

func fiberClientDoBad(c *fiber.Ctx) {
	u := c.Query("url")
	// ruleid: go-framework-ssrf
	req, _ := http.NewRequest("GET", u, nil)
	client := &http.Client{}
	// ruleid: go-framework-ssrf
	client.Do(req)
}

func ginSafeLiteral(c *gin.Context) {
	_ = c.Query("url")
	// ok: go-framework-ssrf
	http.Get("https://example.com/health")
}

func echoSafeNewRequest(c echo.Context) {
	_ = c.Param("id")
	// ok: go-framework-ssrf
	http.NewRequest("GET", "https://example.com/api", nil)
}
