// Package resolver has only technical DNS, framing and resource policy.
package resolver

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/netip"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"time"
)

var ErrLimit = errors.New("dns_limit")
var ErrInvalid = errors.New("dns_failure")
var hostPattern = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$`)

type Request struct {
	V           int    `json:"v"`
	Hostname    string `json:"hostname"`
	TimeoutMS   int    `json:"timeout_ms"`
	MaxIPs      int    `json:"max_ips"`
	MaxDNSBytes int    `json:"max_dns_bytes"`
	MaxQueries  int    `json:"max_queries"`
}

type Response struct {
	V         int      `json:"v"`
	Hostname  string   `json:"hostname"`
	IPs       []string `json:"ips"`
	DNSBytes  int      `json:"dns_bytes"`
	Queries   int      `json:"queries"`
	HeapAlloc uint64   `json:"heap_alloc"`
	HeapSys   uint64   `json:"heap_sys"`
}

type Dial func(context.Context, string, string) (net.Conn, error)

func ValidHost(host string) bool {
	if len(host) > 253 || !hostPattern.MatchString(host) || strings.HasSuffix(host, ".local") ||
		strings.HasSuffix(host, ".localhost") || strings.HasSuffix(host, ".internal") {
		return false
	}
	if _, err := netip.ParseAddr(host); err == nil {
		return false
	}
	for _, label := range strings.Split(host, ".") {
		if strings.HasPrefix(label, "xn--") {
			return false
		}
	}
	return true
}

func decode(input []byte) (Request, error) {
	var request Request
	// Scalar protocol only; detect duplicates rather than accepting last-wins JSON.
	decoder := json.NewDecoder(bytes.NewReader(input))
	token, err := decoder.Token()
	if err != nil || token != json.Delim('{') {
		return request, ErrInvalid
	}
	seen := map[string]bool{}
	for decoder.More() {
		key, err := decoder.Token()
		if err != nil {
			return request, ErrInvalid
		}
		name, ok := key.(string)
		if !ok || seen[name] {
			return request, ErrInvalid
		}
		seen[name] = true
		var value json.RawMessage
		if decoder.Decode(&value) != nil {
			return request, ErrInvalid
		}
	}
	if _, err = decoder.Token(); err != nil {
		return request, ErrInvalid
	}
	if decoder.Decode(new(any)) != io.EOF {
		return request, ErrInvalid
	}
	strict := json.NewDecoder(bytes.NewReader(input))
	strict.DisallowUnknownFields()
	if strict.Decode(&request) != nil || len(seen) != 6 || request.V != 1 || !ValidHost(request.Hostname) ||
		request.TimeoutMS < 1 || request.TimeoutMS > 60000 || request.MaxIPs < 1 || request.MaxIPs > 16 ||
		request.MaxDNSBytes < 1 || request.MaxDNSBytes > 65536 || request.MaxQueries < 1 || request.MaxQueries > 32 {
		return request, ErrInvalid
	}
	return request, nil
}

// Counter is shared across A/AAAA, retries and any TCP fallback of ONE lookup.
type counter struct {
	sync.Mutex
	bytes, reserved, queries, maxBytes, maxQueries int
	exceeded                                       bool
}
type countedConn struct {
	net.Conn
	count *counter
}

func (c *countedConn) Read(p []byte) (int, error) {
	c.count.Lock()
	remaining := c.count.maxBytes - c.count.bytes - c.count.reserved
	if remaining <= 0 {
		c.count.exceeded = true
		c.count.Unlock()
		return 0, ErrLimit
	}
	if remaining > 4096 {
		remaining = 4096
	}
	if len(p) > remaining {
		p = p[:remaining]
	}
	// Reserve the read capacity across parallel DNS sockets, return unused bytes.
	c.count.reserved += len(p)
	c.count.Unlock()
	n, err := c.Conn.Read(p)
	c.count.Lock()
	c.count.reserved -= len(p)
	c.count.bytes += n
	c.count.Unlock()
	return n, err
}
func (c *countedConn) Write(p []byte) (int, error) {
	c.count.Lock()
	if len(p) > c.count.maxBytes-c.count.bytes-c.count.reserved || c.count.queries >= c.count.maxQueries {
		c.count.exceeded = true
		c.count.Unlock()
		return 0, ErrLimit
	}
	c.count.reserved += len(p)
	c.count.queries++
	c.count.Unlock()
	n, err := c.Conn.Write(p)
	c.count.Lock()
	c.count.reserved -= len(p)
	c.count.bytes += n
	c.count.Unlock()
	return n, err
}

// Preserve UDP's PacketConn marker, otherwise net.Resolver adds TCP framing.
type countedPacketConn struct{ *countedConn }

func (c *countedPacketConn) ReadFrom(p []byte) (int, net.Addr, error) {
	n, err := c.Read(p)
	return n, c.RemoteAddr(), err
}
func (c *countedPacketConn) WriteTo(p []byte, address net.Addr) (int, error) {
	if address.String() != c.RemoteAddr().String() {
		return 0, ErrInvalid
	}
	return c.Write(p)
}

func Run(input io.Reader, output io.Writer, dial Dial) error {
	data, err := io.ReadAll(io.LimitReader(input, 1025))
	if err != nil || len(data) > 1024 {
		return ErrInvalid
	}
	request, err := decode(data)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(request.TimeoutMS)*time.Millisecond)
	defer cancel()
	count := &counter{maxBytes: request.MaxDNSBytes, maxQueries: request.MaxQueries}
	if dial == nil {
		dial = (&net.Dialer{}).DialContext
	}
	r := &net.Resolver{PreferGo: true, StrictErrors: true, Dial: func(ctx context.Context, network, address string) (net.Conn, error) {
		conn, err := dial(ctx, network, address)
		if err != nil {
			return nil, err
		}
		bounded := &countedConn{conn, count}
		if _, ok := conn.(net.PacketConn); ok {
			return &countedPacketConn{bounded}, nil
		}
		return bounded, nil
	}}
	// Absolute DNS name: do not append host-configured search suffixes.
	addresses, err := r.LookupNetIP(ctx, "ip", request.Hostname+".")
	count.Lock()
	received, queries, exceeded := count.bytes, count.queries, count.exceeded
	count.Unlock()
	if exceeded {
		return ErrLimit
	}
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if err != nil || len(addresses) == 0 {
		return ErrInvalid
	}
	if len(addresses) > request.MaxIPs {
		return ErrLimit
	}
	ips := make([]string, 0, len(addresses))
	seen := map[netip.Addr]bool{}
	for _, ip := range addresses {
		if !ip.IsValid() || ip.Is4In6() || ip.Zone() != "" || seen[ip] {
			return ErrInvalid
		}
		seen[ip] = true
		ips = append(ips, ip.String())
	}
	var memory runtime.MemStats
	runtime.ReadMemStats(&memory)
	result := Response{1, request.Hostname, ips, received, queries, memory.HeapAlloc, memory.HeapSys}
	encoded, err := json.Marshal(result)
	if err != nil || len(encoded) > 2048 {
		return ErrLimit
	}
	if _, err = output.Write(encoded); err != nil {
		return ErrInvalid
	}
	return nil
}

func ErrorCode(err error) string {
	if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) {
		return "dns_timeout"
	}
	if errors.Is(err, ErrLimit) {
		return "dns_limit"
	}
	return "dns_failure"
}
