// Package synthetic is TEST ONLY. Production entry point does not import it.
package synthetic

import (
	"context"
	"encoding/binary"
	"net"
	"sync"
	"time"
)

// Server answers only its loopback socket; never delegates, proxies or uses DNS.
type Server struct {
	conn       net.PacketConn
	done, stop chan struct{}
	once       sync.Once
	delay      time.Duration
	private    bool
}

func New(delay time.Duration, private bool) (*Server, error) {
	conn, err := net.ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		return nil, err
	}
	s := &Server{conn: conn, done: make(chan struct{}), stop: make(chan struct{}), delay: delay, private: private}
	go s.run()
	return s, nil
}
func (s *Server) Dial(ctx context.Context, network, address string) (net.Conn, error) {
	// Explicit test-only redirection; ignores system DNS destination entirely.
	return (&net.Dialer{}).DialContext(ctx, "udp", s.conn.LocalAddr().String())
}
func (s *Server) Close() { s.once.Do(func() { close(s.stop); s.conn.Close(); <-s.done }) }
func (s *Server) run() {
	defer close(s.done)
	buffer := make([]byte, 2048)
	for {
		n, address, err := s.conn.ReadFrom(buffer)
		if err != nil {
			return
		}
		query := buffer[:n]
		if n < 17 {
			continue
		}
		end := 12
		for end < n && query[end] != 0 {
			end += 1 + int(query[end])
		}
		end += 5
		if end > n {
			continue
		}
		qtype := binary.BigEndian.Uint16(query[end-4 : end-2])
		var body []byte
		if qtype == 1 {
			body = []byte{1, 1, 1, 1}
			if s.private {
				body = []byte{127, 0, 0, 1}
			}
		}
		if qtype == 28 {
			body = net.ParseIP("2606:4700:4700::1111").To16()
		}
		if body == nil {
			continue
		}
		answer := make([]byte, 12)
		copy(answer, query[:2])
		binary.BigEndian.PutUint16(answer[2:], 0x8180)
		binary.BigEndian.PutUint16(answer[4:], 1)
		binary.BigEndian.PutUint16(answer[6:], 1)
		answer = append(answer, query[12:end]...)
		answer = append(answer, 0xc0, 0x0c, byte(qtype>>8), byte(qtype), 0, 1, 0, 0, 0, 60, 0, byte(len(body)))
		answer = append(answer, body...)
		if s.delay > 0 {
			select {
			case <-time.After(s.delay):
			case <-s.stop:
				return
			}
		}
		_, _ = s.conn.WriteTo(answer, address)
	}
}
