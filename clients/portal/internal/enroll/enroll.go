// Package enroll handles the one-shot enrollment handshake with the server.
package enroll

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"runtime"
	"strings"
	"time"
)

// Request is sent to POST /portal/enroll.
type Request struct {
	EnrollToken  string     `json:"enroll_token"`
	DeviceSlug   string     `json:"device_slug"`
	WorkspaceSlug string    `json:"workspace_slug"`
	DeviceInfo   DeviceInfo `json:"device_info"`
}

// DeviceInfo carries system metadata sent during enrollment.
type DeviceInfo struct {
	OS            string `json:"os"`
	Arch          string `json:"arch"`
	ClientVersion string `json:"client_version"`
}

// Response is returned by the server on successful enrollment.
type Response struct {
	PermanentToken string `json:"permanent_token"`
	WorkspaceSlug  string `json:"workspace_slug"`
}

// ClientVersion is set at build time via -ldflags.
var ClientVersion = "dev"

// Enroll calls POST <httpBaseURL>/portal/enroll with the one-shot token.
// wsURL is the WebSocket URL (wss://…/portal/ws); the HTTP base is derived from it.
func Enroll(wsURL, enrollToken, deviceSlug, workspaceSlug string) (*Response, error) {
	httpBase, err := wsToHTTP(wsURL)
	if err != nil {
		return nil, err
	}

	body := Request{
		EnrollToken:  enrollToken,
		DeviceSlug:   deviceSlug,
		WorkspaceSlug: workspaceSlug,
		DeviceInfo: DeviceInfo{
			OS:            runtime.GOOS,
			Arch:          runtime.GOARCH,
			ClientVersion: ClientVersion,
		},
	}

	data, err := json.Marshal(body)
	if err != nil {
		return nil, fmt.Errorf("marshal enroll request: %w", err)
	}

	client := &http.Client{Timeout: 15 * time.Second}
	endpoint := strings.TrimSuffix(httpBase, "/") + "/portal/enroll"
	resp, err := client.Post(endpoint, "application/json", bytes.NewReader(data))
	if err != nil {
		return nil, fmt.Errorf("POST %s: %w", endpoint, err)
	}
	defer resp.Body.Close()

	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("enroll failed (HTTP %d): %s", resp.StatusCode, strings.TrimSpace(string(raw)))
	}

	var result Response
	if err := json.Unmarshal(raw, &result); err != nil {
		return nil, fmt.Errorf("parse enroll response: %w", err)
	}
	return &result, nil
}

// wsToHTTP converts wss://host/path → https://host and ws://host/path → http://host.
func wsToHTTP(wsURL string) (string, error) {
	u, err := url.Parse(wsURL)
	if err != nil {
		return "", fmt.Errorf("invalid server URL %q: %w", wsURL, err)
	}
	switch u.Scheme {
	case "wss":
		u.Scheme = "https"
	case "ws":
		u.Scheme = "http"
	default:
		return "", fmt.Errorf("server URL must start with ws:// or wss://, got %q", u.Scheme)
	}
	u.Path = ""
	u.RawQuery = ""
	return u.String(), nil
}
