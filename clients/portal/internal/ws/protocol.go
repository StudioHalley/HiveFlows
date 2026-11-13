// Package ws contains the WebSocket client and protocol types.
package ws

// Message type constants (field "type" in every JSON frame).
const (
	TypeHello    = "hello"
	TypeRequest  = "request"
	TypeResponse = "response"
	TypeError    = "error"
	TypePing     = "ping"
	TypePong     = "pong"
	TypeRevoke   = "revoke"
)

// Error codes sent by the client in an ErrorMsg.
const (
	ErrServiceNotAllowed  = "service_not_allowed"
	ErrUpstreamUnreachable = "upstream_unreachable"
	ErrUpstreamTimeout    = "upstream_timeout"
	ErrBodyTooLarge       = "body_too_large"
)

// Envelope is used to peek at the "type" field before full decoding.
type Envelope struct {
	Type string `json:"type"`
}

// HelloMsg is the first message sent by the client after connecting.
type HelloMsg struct {
	Type       string       `json:"type"`        // "hello"
	Version    string       `json:"version"`     // "1"
	DeviceInfo DeviceInfo   `json:"device_info"`
	Services   []ServiceDef `json:"services"`
}

// DeviceInfo is embedded in HelloMsg.
type DeviceInfo struct {
	OS            string `json:"os"`
	Arch          string `json:"arch"`
	ClientVersion string `json:"client_version"`
}

// ServiceDef is one entry in the HELLO services list.
type ServiceDef struct {
	Name  string `json:"name"`
	Route string `json:"route"`
}

// RequestMsg is sent by the server to ask the client to forward an HTTP request.
type RequestMsg struct {
	Type    string            `json:"type"`    // "request"
	ID      string            `json:"id"`
	Service string            `json:"service"`
	Method  string            `json:"method"`
	Path    string            `json:"path"`
	Headers map[string]string `json:"headers"`
	BodyB64 string            `json:"body_b64"`
}

// ResponseMsg is sent by the client after a successful HTTP forward.
type ResponseMsg struct {
	Type    string            `json:"type"`    // "response"
	ID      string            `json:"id"`
	Status  int               `json:"status"`
	Headers map[string]string `json:"headers"`
	BodyB64 string            `json:"body_b64"`
}

// ErrorMsg is sent by the client when forwarding fails.
type ErrorMsg struct {
	Type    string `json:"type"`    // "error"
	ID      string `json:"id"`
	Code    string `json:"code"`
	Message string `json:"message"`
}

// PingMsg is sent by the client every 20 s.
type PingMsg struct {
	Type string `json:"type"` // "ping"
}
