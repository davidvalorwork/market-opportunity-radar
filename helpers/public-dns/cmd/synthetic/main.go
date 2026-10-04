// TEST ONLY fixture helper; never configured by production factory.
package main

import (
	"bytes"
	"encoding/json"
	"io"
	"os"
	"strings"
	"time"

	"radar.local/public-dns/internal/resolver"
	"radar.local/public-dns/internal/synthetic"
)

func main() {
	// Mode comes ONLY from an explicitly created fixture config beside binary,
	// never stdin hostname/task or environment; production does not read this file.
	if len(os.Args) != 1 {
		os.Exit(1)
	}
	config, err := os.ReadFile(os.Args[0] + ".synthetic-only")
	if err != nil {
		os.Exit(1)
	}
	mode := strings.TrimSpace(string(config))
	input, _ := io.ReadAll(io.LimitReader(os.Stdin, 1025))
	if mode == "slow" {
		time.Sleep(10 * time.Second)
		return
	}
	if mode == "stdout-flood" {
		for i := 0; i < 10000; i++ {
			_, _ = os.Stdout.Write([]byte(strings.Repeat("x", 512)))
		}
		return
	}
	if mode == "stderr-secret" {
		_, _ = os.Stderr.WriteString("SYNTHETIC_SECRET_NAME_PHONE_555123456\n")
		os.Exit(1)
	}
	if mode == "stderr-flood" {
		for i := 0; i < 1000; i++ {
			_, _ = os.Stderr.Write([]byte(strings.Repeat("x", 512)))
		}
		return
	}
	if strings.HasPrefix(mode, "forged:") {
		var request map[string]any
		_ = json.Unmarshal(input, &request)
		result := map[string]any{"v": 1, "hostname": request["hostname"], "ips": []string{"1.1.1.1"},
			"dns_bytes": 100, "queries": 2, "heap_alloc": 1024, "heap_sys": 4096}
		switch strings.TrimPrefix(mode, "forged:") {
		case "host":
			result["hostname"] = "foreign.invalid"
		case "empty":
			result["ips"] = []string{}
		case "duplicate":
			result["ips"] = []string{"1.1.1.1", "1.1.1.1"}
		case "many":
			result["ips"] = strings.Split(strings.Repeat("1.1.1.1,", 17)+"1.1.1.1", ",")
		case "private":
			result["ips"] = []string{"127.0.0.1"}
		case "mapped":
			result["ips"] = []string{"::ffff:1.1.1.1"}
		case "invalid":
			result["ips"] = []string{"SYNTHETIC_BAD_IP"}
		case "unknown":
			result["secret"] = "SYNTHETIC_PRIVATE"
		case "bytes":
			result["dns_bytes"] = 999999
		case "queries":
			result["queries"] = 999999
		case "bool":
			result["v"] = true
		case "duplicate-field":
			_, _ = os.Stdout.WriteString(`{"v":1,"v":1}`)
			return
		case "malformed":
			_, _ = os.Stdout.WriteString("SYNTHETIC_PRIVATE_NOT_JSON")
			return
		}
		_ = json.NewEncoder(os.Stdout).Encode(result)
		return
	}
	delay := time.Duration(0)
	if mode == "delayed-dns" {
		delay = 150 * time.Millisecond
	}
	server, err := synthetic.New(delay, mode == "private-dns")
	if err != nil {
		os.Exit(1)
	}
	defer server.Close()
	if err = resolver.Run(bytes.NewReader(input), os.Stdout, server.Dial); err != nil {
		_, _ = os.Stderr.WriteString(resolver.ErrorCode(err) + "\n")
		os.Exit(1)
	}
}
