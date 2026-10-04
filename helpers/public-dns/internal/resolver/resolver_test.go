package resolver_test

import (
	"bytes"
	"encoding/json"
	"testing"
	"time"

	"radar.local/public-dns/internal/resolver"
	"radar.local/public-dns/internal/synthetic"
)

func input() []byte {
	data, _ := json.Marshal(resolver.Request{V: 1, Hostname: "fixture.invalid", TimeoutMS: 1000, MaxIPs: 16, MaxDNSBytes: 16384, MaxQueries: 8})
	return data
}
func TestRealLoopbackDNS(t *testing.T) {
	s, err := synthetic.New(0, false)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	var out bytes.Buffer
	if err = resolver.Run(bytes.NewReader(input()), &out, s.Dial); err != nil {
		t.Fatal(err)
	}
	var response resolver.Response
	if json.Unmarshal(out.Bytes(), &response) != nil {
		t.Fatal("invalid output")
	}
	if len(response.IPs) != 2 || response.DNSBytes <= 0 || response.DNSBytes > 16384 || response.Queries != 2 || response.HeapSys == 0 {
		t.Fatal("invalid bounded DNS metrics")
	}
}
func TestTimeoutAndBudgets(t *testing.T) {
	for _, mode := range []string{"timeout", "bytes", "queries", "ips"} {
		t.Run(mode, func(t *testing.T) {
			var request resolver.Request
			_ = json.Unmarshal(input(), &request)
			delay := time.Duration(0)
			switch mode {
			case "timeout":
				request.TimeoutMS = 20
				delay = 50 * time.Millisecond
			case "bytes":
				request.MaxDNSBytes = 8
			case "queries":
				request.MaxQueries = 1
			case "ips":
				request.MaxIPs = 1
			}
			s, err := synthetic.New(delay, false)
			if err != nil {
				t.Fatal(err)
			}
			defer s.Close()
			data, _ := json.Marshal(request)
			var out bytes.Buffer
			err = resolver.Run(bytes.NewReader(data), &out, s.Dial)
			if err == nil || out.Len() != 0 {
				t.Fatal("budget accepted")
			}
		})
	}
}
func TestStrictInputNoDial(t *testing.T) {
	for _, bad := range []string{`{"v":1,"v":1}`, `{"v":2}`, `null`, `{"hostname":"x\nCookie:secret"}`, "x" + string(bytes.Repeat([]byte("x"), 1024))} {
		var out bytes.Buffer
		if resolver.Run(bytes.NewBufferString(bad), &out, nil) == nil || out.Len() != 0 {
			t.Fatal("invalid protocol accepted")
		}
	}
	for _, host := range []string{"localhost", "1.1.1.1", "-x.invalid", "x-.invalid", "xn--abc.invalid", "é.invalid", "a.local", "a.internal", "a.invalid."} {
		if resolver.ValidHost(host) {
			t.Fatal("invalid hostname accepted")
		}
	}
}
