// Package proxy forwards HTTP requests to local services on behalf of the server.
package proxy

import (
	"encoding/base64"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/hiveflows/portal-client/internal/ws"
)

const (
	maxBodyBytes   = 10 * 1024 * 1024 // 10 MB
	localTimeout   = 25 * time.Second  // < 30 s server-side timeout
)

// Proxy forwards incoming RequestMsgs to local HTTP services.
type Proxy struct {
	services map[string]string // name → route
	client   *http.Client
}

// New creates a Proxy with the given allowlist.
func New(services map[string]string) *Proxy {
	return &Proxy{
		services: services,
		client: &http.Client{
			Timeout: localTimeout,
			// Do not follow redirects automatically — let the caller decide.
			CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
				return http.ErrUseLastResponse
			},
		},
	}
}

// Forward executes the request and returns either a ResponseMsg or ErrorMsg.
// It never returns both non-nil.
func (p *Proxy) Forward(req *ws.RequestMsg) (*ws.ResponseMsg, *ws.ErrorMsg) {
	// 1. Allowlist check
	route, ok := p.services[req.Service]
	if !ok {
		return nil, &ws.ErrorMsg{
			Type:    ws.TypeError,
			ID:      req.ID,
			Code:    ws.ErrServiceNotAllowed,
			Message: fmt.Sprintf("service %q is not in the allowlist", req.Service),
		}
	}

	// 2. Build target URL: route + path (concat simple)
	targetURL := strings.TrimSuffix(route, "/") + req.Path

	// 3. Decode request body
	var bodyReader io.Reader
	if req.BodyB64 != "" {
		decoded, err := base64.StdEncoding.DecodeString(req.BodyB64)
		if err != nil {
			return nil, &ws.ErrorMsg{
				Type:    ws.TypeError,
				ID:      req.ID,
				Code:    ws.ErrUpstreamUnreachable,
				Message: "invalid base64 body",
			}
		}
		if len(decoded) > maxBodyBytes {
			return nil, &ws.ErrorMsg{
				Type:    ws.TypeError,
				ID:      req.ID,
				Code:    ws.ErrBodyTooLarge,
				Message: "request body exceeds 10 MB",
			}
		}
		bodyReader = strings.NewReader(string(decoded))
	}

	// 4. Build HTTP request
	httpReq, err := http.NewRequest(req.Method, targetURL, bodyReader)
	if err != nil {
		return nil, &ws.ErrorMsg{
			Type:    ws.TypeError,
			ID:      req.ID,
			Code:    ws.ErrUpstreamUnreachable,
			Message: fmt.Sprintf("build request: %v", err),
		}
	}
	for k, v := range req.Headers {
		httpReq.Header.Set(k, v)
	}

	// 5. Execute
	resp, err := p.client.Do(httpReq)
	if err != nil {
		code := ws.ErrUpstreamUnreachable
		if isTimeout(err) {
			code = ws.ErrUpstreamTimeout
		}
		return nil, &ws.ErrorMsg{
			Type:    ws.TypeError,
			ID:      req.ID,
			Code:    code,
			Message: err.Error(),
		}
	}
	defer resp.Body.Close()

	// 6. Read response body (cap at 10 MB)
	limited := &io.LimitedReader{R: resp.Body, N: int64(maxBodyBytes) + 1}
	rawBody, err := io.ReadAll(limited)
	if err != nil {
		return nil, &ws.ErrorMsg{
			Type:    ws.TypeError,
			ID:      req.ID,
			Code:    ws.ErrUpstreamUnreachable,
			Message: fmt.Sprintf("read response: %v", err),
		}
	}
	if int64(len(rawBody)) > maxBodyBytes {
		return nil, &ws.ErrorMsg{
			Type:    ws.TypeError,
			ID:      req.ID,
			Code:    ws.ErrBodyTooLarge,
			Message: "response body exceeds 10 MB",
		}
	}

	// 7. Collect response headers
	headers := make(map[string]string, len(resp.Header))
	for k, vv := range resp.Header {
		headers[k] = vv[0]
	}

	return &ws.ResponseMsg{
		Type:    ws.TypeResponse,
		ID:      req.ID,
		Status:  resp.StatusCode,
		Headers: headers,
		BodyB64: base64.StdEncoding.EncodeToString(rawBody),
	}, nil
}

func isTimeout(err error) bool {
	if err == nil {
		return false
	}
	return strings.Contains(err.Error(), "timeout") ||
		strings.Contains(err.Error(), "deadline exceeded")
}
