// Package config handles portal.yaml loading, saving, and XDG path resolution.
package config

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"

	"gopkg.in/yaml.v3"
)

// Service is one entry in the allowlist.
type Service struct {
	Name  string `yaml:"name"`
	Route string `yaml:"route"` // e.g. http://192.168.1.20:8123
}

// Config is the full portal.yaml structure.
type Config struct {
	ServerURL  string    `yaml:"server_url"`  // wss://…/portal/ws
	Workspace  string    `yaml:"workspace"`
	DeviceSlug string    `yaml:"device_slug"`
	Token      string    `yaml:"token"`
	Services   []Service `yaml:"services"`
}

// DefaultConfigPath returns the platform-appropriate default path for portal.yaml.
//
//	Linux/macOS : $XDG_CONFIG_HOME/portal-client/portal.yaml
//	             (~/.config/portal-client/portal.yaml if XDG not set)
//	Windows      : %APPDATA%\portal-client\portal.yaml
func DefaultConfigPath() string {
	var base string
	switch runtime.GOOS {
	case "windows":
		base = os.Getenv("APPDATA")
		if base == "" {
			base = filepath.Join(os.Getenv("USERPROFILE"), "AppData", "Roaming")
		}
	default:
		base = os.Getenv("XDG_CONFIG_HOME")
		if base == "" {
			home, _ := os.UserHomeDir()
			base = filepath.Join(home, ".config")
		}
	}
	return filepath.Join(base, "portal-client", "portal.yaml")
}

// Load reads and parses portal.yaml from path.
func Load(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read config %s: %w", path, err)
	}
	var cfg Config
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("parse config: %w", err)
	}
	return &cfg, nil
}

// Save writes cfg to path, creating parent directories as needed.
func Save(path string, cfg *Config) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return fmt.Errorf("create config dir: %w", err)
	}
	data, err := yaml.Marshal(cfg)
	if err != nil {
		return fmt.Errorf("marshal config: %w", err)
	}
	if err := os.WriteFile(path, data, 0o600); err != nil {
		return fmt.Errorf("write config %s: %w", path, err)
	}
	return nil
}

// ServiceMap returns a name→route map for fast allowlist lookup.
func (c *Config) ServiceMap() map[string]string {
	m := make(map[string]string, len(c.Services))
	for _, s := range c.Services {
		m[s.Name] = s.Route
	}
	return m
}
