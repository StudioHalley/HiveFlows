package ws

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"runtime"
	"time"

	"github.com/gorilla/websocket"
	"github.com/hiveflows/portal-client/internal/config"
)

const (
	pingInterval     = 20 * time.Second
	stableThreshold  = 30 * time.Second // reset backoff after this long connected
	backoffInitial   = 1 * time.Second
	backoffMax       = 60 * time.Second
	writeTimeout     = 10 * time.Second
)

// ClientVersion is injected at build time via -ldflags.
var ClientVersion = "dev"

// Forwarder is the interface the WS client calls to forward HTTP requests.
type Forwarder interface {
	Forward(req *RequestMsg) (*ResponseMsg, *ErrorMsg)
}

// Client manages the persistent WebSocket connection to the HiveFlows portal.
type Client struct {
	cfg       *config.Config
	forwarder Forwarder
}

// New creates a Client. cfg must be fully populated (ServerURL, Token, etc.).
func New(cfg *config.Config, fwd Forwarder) *Client {
	return &Client{cfg: cfg, forwarder: fwd}
}

// Run connects to the server and runs forever, reconnecting with exponential backoff.
// It returns only if ctx is cancelled or a REVOKE message is received.
func (c *Client) Run(ctx context.Context) error {
	backoff := backoffInitial
	for {
		connectedAt := time.Now()
		err := c.runOnce(ctx)

		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}

		if err == errRevoked {
			log.Println("[portal] REVOKE received — stopping.")
			return err
		}

		// Reset backoff if connection was stable for long enough
		if time.Since(connectedAt) >= stableThreshold {
			backoff = backoffInitial
		}

		log.Printf("[portal] disconnected (%v), reconnecting in %s…", err, backoff)
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(backoff):
		}

		// Exponential backoff capped at backoffMax
		backoff *= 2
		if backoff > backoffMax {
			backoff = backoffMax
		}
	}
}

// errRevoked is a sentinel returned when the server sends REVOKE.
var errRevoked = &revokeError{}

type revokeError struct{}

func (e *revokeError) Error() string { return "token revoked by server" }

// runOnce performs a single connect → HELLO → read-loop cycle.
func (c *Client) runOnce(ctx context.Context) error {
	dialer := websocket.Dialer{
		HandshakeTimeout: 10 * time.Second,
	}
	header := http.Header{}
	header.Set("Authorization", "Bearer "+c.cfg.Token)

	conn, _, err := dialer.DialContext(ctx, c.cfg.ServerURL, header)
	if err != nil {
		return err
	}
	defer conn.Close()

	// Send HELLO
	services := make([]ServiceDef, 0, len(c.cfg.Services))
	for _, s := range c.cfg.Services {
		services = append(services, ServiceDef{Name: s.Name, Route: s.Route})
	}
	hello := HelloMsg{
		Type:    TypeHello,
		Version: "1",
		DeviceInfo: DeviceInfo{
			OS:            runtime.GOOS,
			Arch:          runtime.GOARCH,
			ClientVersion: ClientVersion,
		},
		Services: services,
	}
	if err := writeJSON(conn, hello); err != nil {
		return err
	}

	log.Printf("[portal] connected as %s/%s", c.cfg.Workspace, c.cfg.DeviceSlug)

	// Start ping ticker
	pingTicker := time.NewTicker(pingInterval)
	defer pingTicker.Stop()

	// Channel to signal the read loop to stop
	done := make(chan struct{})

	// Outbound messages from ping loop
	outbound := make(chan any, 8)

	// Ping goroutine
	go func() {
		for {
			select {
			case <-done:
				return
			case <-pingTicker.C:
				outbound <- PingMsg{Type: TypePing}
			}
		}
	}()

	// Write goroutine — serialises all writes to the WS
	writeErr := make(chan error, 1)
	go func() {
		for {
			select {
			case <-done:
				return
			case msg := <-outbound:
				if err := writeJSON(conn, msg); err != nil {
					writeErr <- err
					return
				}
			}
		}
	}()

	// Context watcher — closes the WS connection to unblock ReadMessage on Ctrl+C.
	go func() {
		select {
		case <-done:
		case <-ctx.Done():
			conn.Close()
		}
	}()

	defer close(done)

	// Read loop (runs in current goroutine)
	for {
		select {
		case err := <-writeErr:
			return err
		default:
		}

		_, raw, err := conn.ReadMessage()
		if err != nil {
			return err
		}

		var env Envelope
		if err := json.Unmarshal(raw, &env); err != nil {
			continue
		}

		switch env.Type {
		case TypeRequest:
			var req RequestMsg
			if err := json.Unmarshal(raw, &req); err != nil {
				continue
			}
			// Handle in goroutine so reads are not blocked
			go func(r RequestMsg) {
				respMsg, errMsg := c.forwarder.Forward(&r)
				if errMsg != nil {
					outbound <- errMsg
				} else {
					outbound <- respMsg
				}
			}(req)

		case TypePong:
			// keepalive acknowledged — nothing to do

		case TypeRevoke:
			return errRevoked
		}
	}
}

func writeJSON(conn *websocket.Conn, v any) error {
	conn.SetWriteDeadline(time.Now().Add(writeTimeout))
	return conn.WriteJSON(v)
}
