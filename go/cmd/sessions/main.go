// Command sessions is the age session vault CLI (ported from lab/sessions).
package main

import (
	"encoding/json"
	"os"
	"runtime"
	"strconv"
	"strings"
	"time"

	"radar.local/radar/internal/vault"
)

func numberFile(p string) any {
	data, e := os.ReadFile(p)
	if e != nil {
		return nil
	}
	n, e := strconv.ParseInt(strings.TrimSpace(string(data)), 10, 64)
	if e != nil {
		return nil
	}
	return n
}

func counters(p string) any {
	data, e := os.ReadFile(p)
	if e != nil {
		return nil
	}
	m := map[string]int64{}
	for _, line := range strings.Split(string(data), "\n") {
		parts := strings.Fields(line)
		if len(parts) == 2 {
			n, e := strconv.ParseInt(parts[1], 10, 64)
			if e == nil {
				m[parts[0]] = n
			}
		}
	}
	return m
}

func cgroup() map[string]any {
	return map[string]any{"memory_peak_bytes": numberFile("/sys/fs/cgroup/memory.peak"), "memory_current_bytes": numberFile("/sys/fs/cgroup/memory.current"), "memory_events": counters("/sys/fs/cgroup/memory.events"), "cpu_stat": counters("/sys/fs/cgroup/cpu.stat")}
}

func main() {
	start := time.Now()
	result, e := vault.Run(os.Args[1:])
	if e != nil {
		result = map[string]any{"status": "failed", "error_code": e.Error(), "useful_records": 0}
	}
	result["schema_version"] = 1
	result["suite"] = "sessions"
	result["timings"] = map[string]any{"execution_ms": float64(time.Since(start).Microseconds()) / 1000}
	result["versions"] = map[string]string{"go": runtime.Version(), "age": vault.AgeVersion}
	result["cgroup"] = cgroup()
	if _, ok := result["useful_records"]; !ok {
		result["useful_records"] = 0
	}
	json.NewEncoder(os.Stdout).Encode(result)
	if e != nil || result["status"] == "failed" {
		os.Exit(1)
	}
}
