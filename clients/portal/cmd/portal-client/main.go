// portal-client — HiveFlows Portal client binary.
//
// Commands:
//
//	init   --server URL --token TOKEN --device-slug SLUG [--workspace SLUG] [--config PATH]
//	run    [--config PATH]
//	version
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"

	"github.com/hiveflows/portal-client/internal/config"
	"github.com/hiveflows/portal-client/internal/enroll"
	"github.com/hiveflows/portal-client/internal/proxy"
	"github.com/hiveflows/portal-client/internal/ws"
)

// Build-time variables set via -ldflags.
var (
	Version   = "dev"
	CommitHash = "unknown"
)

func main() {
	log.SetFlags(log.Ltime | log.Lshortfile)

	if len(os.Args) < 2 {
		printUsage()
		os.Exit(1)
	}

	switch os.Args[1] {
	case "init":
		runInit(os.Args[2:])
	case "run":
		runRun(os.Args[2:])
	case "version":
		fmt.Printf("portal-client %s (commit %s)\n", Version, CommitHash)
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", os.Args[1])
		printUsage()
		os.Exit(1)
	}
}

func printUsage() {
	fmt.Fprintln(os.Stderr, `portal-client — HiveFlows Portal client

Commands:
  init    --server URL --token TOKEN --device-slug SLUG
          [--workspace SLUG] [--config PATH]
          Enroll this device and write portal.yaml.

  run     [--config PATH]
          Connect to the portal server and forward requests forever.

  version
          Print version and commit hash.`)
}

// ── init ──────────────────────────────────────────────────────────────────────

func runInit(args []string) {
	fs := flag.NewFlagSet("init", flag.ExitOnError)
	serverURL  := fs.String("server", "", "WebSocket URL of the portal server (wss://…/portal/ws)")
	token      := fs.String("token", "", "One-shot enroll token")
	deviceSlug := fs.String("device-slug", "", "Desired device slug (must match the one created on the dashboard)")
	workspace  := fs.String("workspace", "", "Workspace slug (required)")
	configPath := fs.String("config", config.DefaultConfigPath(), "Path to write portal.yaml")
	_ = fs.Parse(args)

	if *serverURL == "" || *token == "" || *deviceSlug == "" || *workspace == "" {
		fmt.Fprintln(os.Stderr, "init: --server, --token, --device-slug, and --workspace are required")
		fs.Usage()
		os.Exit(1)
	}

	enroll.ClientVersion = Version

	fmt.Printf("Enrolling device %q in workspace %q…\n", *deviceSlug, *workspace)
	resp, err := enroll.Enroll(*serverURL, *token, *deviceSlug, *workspace)
	if err != nil {
		log.Fatalf("enrollment failed: %v", err)
	}

	cfg := &config.Config{
		ServerURL:  *serverURL,
		Workspace:  resp.WorkspaceSlug,
		DeviceSlug: *deviceSlug,
		Token:      resp.PermanentToken,
		Services:   []config.Service{},
	}

	if err := config.Save(*configPath, cfg); err != nil {
		log.Fatalf("save config: %v", err)
	}

	fmt.Printf("✓ Enrolled. Config written to %s\n", *configPath)
	fmt.Println("Next steps:")
	fmt.Printf("  1. Edit %s and add your services under 'services:'\n", *configPath)
	fmt.Println("  2. Run: portal-client run")
}

// ── run ───────────────────────────────────────────────────────────────────────

func runRun(args []string) {
	fs := flag.NewFlagSet("run", flag.ExitOnError)
	configPath := fs.String("config", config.DefaultConfigPath(), "Path to portal.yaml")
	_ = fs.Parse(args)

	cfg, err := config.Load(*configPath)
	if err != nil {
		log.Fatalf("load config: %v", err)
	}
	if cfg.ServerURL == "" || cfg.Token == "" || cfg.DeviceSlug == "" {
		log.Fatal("config is incomplete — run 'portal-client init' first")
	}

	ws.ClientVersion = Version

	fwd := proxy.New(cfg.ServiceMap())
	client := ws.New(cfg, fwd)

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	log.Printf("portal-client %s starting (device=%s workspace=%s)", Version, cfg.DeviceSlug, cfg.Workspace)
	if err := client.Run(ctx); err != nil {
		if err == context.Canceled {
			log.Println("portal-client stopped.")
			return
		}
		log.Fatalf("portal-client exited: %v", err)
	}
}
