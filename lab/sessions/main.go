package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"

	"filippo.io/age"
)

const maxState = 1 << 20
const maxBundle = 2 << 20
const ageVersion = "v1.3.2"

// Errors are constant codes: parser and filesystem diagnostics may contain secrets.
func fail(code string) error { return errors.New(code) }

type cookie struct {
	Name         string  `json:"name"`
	Value        string  `json:"value"`
	Domain       string  `json:"domain"`
	Path         string  `json:"path"`
	Expires      float64 `json:"expires"`
	HTTPOnly     bool    `json:"httpOnly"`
	Secure       bool    `json:"secure"`
	SameSite     string  `json:"sameSite"`
	PartitionKey string  `json:"partitionKey,omitempty"`
}
type entry struct {
	Name  string `json:"name"`
	Value string `json:"value"`
}
type originState struct {
	Origin       string          `json:"origin"`
	LocalStorage []entry         `json:"localStorage"`
	IndexedDB    json.RawMessage `json:"indexedDB,omitempty"`
}
type storageState struct {
	Cookies []cookie      `json:"cookies"`
	Origins []originState `json:"origins"`
}
type bundle struct {
	SchemaVersion   int          `json:"schema_version"`
	FixtureOnly     bool         `json:"fixture_only"`
	Alias           string       `json:"alias"`
	Version         int          `json:"version"`
	StateType       string       `json:"state_type"`
	CreatedAt       string       `json:"created_at"`
	AllowedOrigins  []string     `json:"allowed_origins"`
	ProducerVersion string       `json:"producer_version"`
	State           storageState `json:"storage_state"`
}
type registry struct {
	SchemaVersion int    `json:"schema_version"`
	Version       int    `json:"version"`
	FixtureOnly   bool   `json:"fixture_only"`
	Status        string `json:"status"`
}
type stringsFlag []string

func (s *stringsFlag) String() string     { return "recipient files" }
func (s *stringsFlag) Set(v string) error { *s = append(*s, v); return nil }

var slug = regexp.MustCompile(`^[a-z][a-z0-9-]{0,47}$`)

func validAlias(s string) bool {
	if !slug.MatchString(s) {
		return false
	}
	switch strings.ToUpper(s) {
	case "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9":
		return false
	}
	return true
}
func canonicalOrigin(s string, fixture bool) (*url.URL, error) {
	u, e := url.Parse(s)
	if e != nil || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.Path != "" || u.Opaque != "" || u.String() != s {
		return nil, fail("invalid_origin")
	}
	local := u.Hostname() == "localhost" || u.Hostname() == "127.0.0.1" || u.Hostname() == "::1"
	if (fixture && !local) || (u.Scheme != "https" && !(local && u.Scheme == "http")) {
		return nil, fail("origin_not_allowed")
	}
	return u, nil
}
func decode(data []byte, v any) error {
	d := json.NewDecoder(bytes.NewReader(data))
	d.DisallowUnknownFields()
	if d.Decode(v) != nil {
		return fail("invalid_schema")
	}
	if d.Decode(new(any)) != io.EOF {
		return fail("invalid_schema")
	}
	return nil
}
func validateState(s storageState, allowed string, fixture bool) error {
	u, e := canonicalOrigin(allowed, fixture)
	if e != nil {
		return e
	}
	if s.Cookies == nil || s.Origins == nil {
		return fail("storage_state_required")
	}
	for _, c := range s.Cookies {
		if c.Name == "" || c.Domain != u.Hostname() || !strings.HasPrefix(c.Path, "/") || (c.SameSite != "Strict" && c.SameSite != "Lax" && c.SameSite != "None") || c.PartitionKey != "" {
			return fail("cookie_scope_or_schema_invalid")
		}
	}
	for _, o := range s.Origins {
		if o.Origin != allowed || o.LocalStorage == nil {
			return fail("state_origin_not_allowed")
		}
		if len(o.IndexedDB) > 0 {
			var db []any
			if json.Unmarshal(o.IndexedDB, &db) != nil || db == nil {
				return fail("indexeddb_schema_invalid")
			}
		}
	}
	return nil
}
func validateBundle(b bundle, alias, allowed string, userExport bool) error {
	if b.SchemaVersion != 1 || !validAlias(b.Alias) || b.Alias != alias || b.Version < 1 || b.StateType != "playwright_storage_state" || b.ProducerVersion != "sessionlab-1" || len(b.AllowedOrigins) != 1 || b.AllowedOrigins[0] != allowed {
		return fail("manifest_rejected")
	}
	if !b.FixtureOnly && !userExport {
		return fail("user_export_requires_explicit_flag")
	}
	if _, e := time.Parse(time.RFC3339, b.CreatedAt); e != nil {
		return fail("manifest_rejected")
	}
	return validateState(b.State, allowed, b.FixtureOnly)
}

// Reject symlinks (including parent paths) so alias validation cannot be bypassed.
func safePath(p string) error {
	abs, e := filepath.Abs(p)
	if e != nil {
		return fail("invalid_path")
	}
	for cur := abs; ; cur = filepath.Dir(cur) {
		st, err := os.Lstat(cur)
		if err != nil && !os.IsNotExist(err) {
			return fail("path_unavailable")
		}
		if err == nil && st.Mode()&os.ModeSymlink != 0 {
			return fail("symlink_rejected")
		}
		if filepath.Dir(cur) == cur {
			break
		}
	}
	return nil
}
func privateDir(p string) error {
	if safePath(p) != nil {
		return fail("unsafe_private_path")
	}
	if os.MkdirAll(p, 0700) != nil {
		return fail("private_directory_failed")
	}
	return nil
}
func readBounded(p string, n int64) ([]byte, error) {
	if safePath(p) != nil {
		return nil, fail("unsafe_input_path")
	}
	f, e := os.Open(p)
	if e != nil {
		return nil, fail("input_unavailable")
	}
	defer f.Close()
	st, e := f.Stat()
	if e != nil || !st.Mode().IsRegular() || st.Size() > n {
		return nil, fail("input_size_or_type_rejected")
	}
	data, e := io.ReadAll(io.LimitReader(f, n+1))
	if e != nil || int64(len(data)) > n {
		return nil, fail("input_size_rejected")
	}
	return data, nil
}
func exclusive(p string, data []byte) error {
	if safePath(p) != nil {
		return fail("unsafe_output_path")
	}
	f, e := os.OpenFile(p, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if e != nil {
		return fail("output_exists_or_unavailable")
	}
	_, e = f.Write(data)
	if e == nil {
		e = f.Sync()
	}
	ce := f.Close()
	if e != nil || ce != nil {
		return fail("private_write_failed")
	}
	return nil
}
func atomicJSON(p string, v any) error {
	data, e := json.Marshal(v)
	if e != nil {
		return fail("encode_failed")
	}
	f, e := os.CreateTemp(filepath.Dir(p), ".registry-")
	if e != nil {
		return fail("registry_write_failed")
	}
	tmp := f.Name()
	defer os.Remove(tmp)
	if f.Chmod(0600) != nil {
		f.Close()
		return fail("registry_write_failed")
	}
	_, e = f.Write(data)
	if e == nil {
		e = f.Sync()
	}
	ce := f.Close()
	if e != nil || ce != nil {
		return fail("registry_write_failed")
	}
	if os.Rename(tmp, p) != nil {
		return fail("registry_publish_failed")
	}
	return nil
}
func identity(p string) (*age.X25519Identity, error) {
	data, e := readBounded(p, 4096)
	if e != nil {
		return nil, e
	}
	id, e := age.ParseX25519Identity(strings.TrimSpace(string(data)))
	if e != nil {
		return nil, fail("invalid_identity")
	}
	return id, nil
}
func recipients(paths []string) ([]age.Recipient, error) {
	if len(paths) == 0 || len(paths) > 16 {
		return nil, fail("recipient_count_rejected")
	}
	result := []age.Recipient{}
	for _, p := range paths {
		data, e := readBounded(p, 4096)
		if e != nil {
			return nil, e
		}
		r, e := age.ParseX25519Recipient(strings.TrimSpace(string(data)))
		if e != nil {
			return nil, fail("invalid_recipient")
		}
		result = append(result, r)
	}
	return result, nil
}
func encrypt(b bundle, rs []age.Recipient) ([]byte, error) {
	data, e := json.Marshal(b)
	if e != nil || len(data) > maxState {
		return nil, fail("plaintext_size_rejected")
	}
	var out bytes.Buffer
	w, e := age.Encrypt(&out, rs...)
	if e != nil {
		return nil, fail("encryption_failed")
	}
	if _, e = w.Write(data); e != nil {
		return nil, fail("encryption_failed")
	}
	if w.Close() != nil {
		return nil, fail("encryption_failed")
	}
	return out.Bytes(), nil
}
func decrypt(data []byte, id *age.X25519Identity) (bundle, error) {
	var b bundle
	if len(data) > maxBundle {
		return b, fail("ciphertext_size_rejected")
	}
	r, e := age.Decrypt(bytes.NewReader(data), id)
	if e != nil {
		return b, fail("decryption_rejected")
	}
	plain, e := io.ReadAll(io.LimitReader(r, maxState+1))
	if e != nil {
		return b, fail("decryption_rejected")
	}
	if len(plain) > maxState {
		return b, fail("plaintext_size_rejected")
	}
	if e = decode(plain, &b); e != nil {
		return b, e
	}
	return b, nil
}
func getRegistry(dir string) (registry, error) {
	var r registry
	p := filepath.Join(dir, "registry.json")
	if _, e := os.Lstat(p); os.IsNotExist(e) {
		return r, nil
	}
	data, e := readBounded(p, 4096)
	if e != nil {
		return r, e
	}
	if decode(data, &r) != nil || r.SchemaVersion != 1 || r.Version < 1 || r.Status != "unverified" {
		return r, fail("registry_rejected")
	}
	return r, nil
}

// Local CAS is protected by a fail-closed directory lock, never a remote lease.
func publish(vault, alias string, expected int, b bundle, data []byte) error {
	if !validAlias(alias) || expected < 0 {
		return fail("invalid_alias_or_version")
	}
	dir := filepath.Join(vault, "sessions", alias)
	if e := privateDir(dir); e != nil {
		return e
	}
	lock := filepath.Join(dir, ".lock")
	if os.Mkdir(lock, 0700) != nil {
		return fail("registry_locked")
	}
	defer os.Remove(lock)
	r, e := getRegistry(dir)
	if e != nil {
		return e
	}
	if r.Version != expected || b.Version != expected+1 {
		return fail("version_conflict")
	}
	if e = exclusive(filepath.Join(dir, strconv.Itoa(b.Version)+".age"), data); e != nil {
		return e
	}
	return atomicJSON(filepath.Join(dir, "registry.json"), registry{1, b.Version, b.FixtureOnly, "unverified"})
}
func run(args []string) (map[string]any, error) {
	if len(args) == 0 || args[0] == "selftest" {
		if len(args) > 1 {
			return nil, fail("invalid_arguments")
		}
		return selftest(), nil
	}
	command := args[0]
	f := flag.NewFlagSet(command, flag.ContinueOnError)
	f.SetOutput(io.Discard)
	vault := f.String("vault", "", "private vault directory")
	alias := f.String("alias", "", "safe alias")
	state := f.String("state-file", "", "user supplied storageState")
	allowed := f.String("allowed-origin", "http://localhost:8765", "explicit allowed origin")
	expected := f.Int("expected-version", 0, "CAS version; zero for first import")
	input := f.String("bundle", "", "encrypted input")
	out := f.String("out", "", "encrypted output .age")
	idpath := f.String("identity-file", "", "private identity file")
	userExport := f.Bool("user-export", false, "explicitly authorize supplied real state")
	var recipientFiles stringsFlag
	f.Var(&recipientFiles, "recipient-file", "public recipient file; repeat for each consumer")
	if f.Parse(args[1:]) != nil || f.NArg() != 0 || *vault == "" {
		return nil, fail("invalid_arguments")
	}
	if e := privateDir(*vault); e != nil {
		return nil, e
	}
	keydir := filepath.Join(*vault, "keys")
	keypath := filepath.Join(keydir, "identity.agekey")
	pubpath := filepath.Join(keydir, "recipient.txt")
	if command == "keygen" {
		if e := privateDir(keydir); e != nil {
			return nil, e
		}
		id, e := age.GenerateX25519Identity()
		if e != nil {
			return nil, fail("key_generation_failed")
		}
		if e = exclusive(keypath, []byte(id.String()+"\n")); e != nil {
			return nil, e
		}
		if e = exclusive(pubpath, []byte(id.Recipient().String()+"\n")); e != nil {
			return nil, e
		}
		return map[string]any{"status": "created", "private_key_separate": true}, nil
	}
	if !validAlias(*alias) || *expected < 0 {
		return nil, fail("invalid_alias_or_version")
	}
	dir := filepath.Join(*vault, "sessions", *alias)
	if command == "status" {
		r, e := getRegistry(dir)
		if e != nil {
			return nil, e
		}
		status := "absent"
		if r.Version > 0 {
			status = r.Status
		}
		return map[string]any{"status": status, "version": r.Version, "fixture_only": r.FixtureOnly}, nil
	}
	if *idpath == "" {
		*idpath = keypath
	}
	switch command {
	case "prepare", "renew":
		if command == "prepare" && *expected != 0 {
			return nil, fail("prepare_requires_version_zero")
		}
		if command == "renew" && *expected < 1 {
			return nil, fail("renew_requires_current_version")
		}
		data, e := readBounded(*state, maxState)
		if e != nil {
			return nil, e
		}
		var s storageState
		if e = decode(data, &s); e != nil {
			return nil, e
		}
		if e = validateState(s, *allowed, !*userExport); e != nil {
			return nil, e
		}
		rs, e := recipients(append([]string{pubpath}, recipientFiles...))
		if e != nil {
			return nil, e
		}
		b := bundle{1, !*userExport, *alias, *expected + 1, "playwright_storage_state", time.Now().UTC().Format(time.RFC3339), []string{*allowed}, "sessionlab-1", s}
		encrypted, e := encrypt(b, rs)
		if e != nil {
			return nil, e
		}
		if e = publish(*vault, *alias, *expected, b, encrypted); e != nil {
			return nil, e
		}
		return map[string]any{"status": "prepared_unverified", "version": b.Version, "fixture_only": b.FixtureOnly}, nil
	case "import":
		if !strings.HasSuffix(*input, ".age") {
			return nil, fail("age_extension_required")
		}
		data, e := readBounded(*input, maxBundle)
		if e != nil {
			return nil, e
		}
		id, e := identity(*idpath)
		if e != nil {
			return nil, e
		}
		b, e := decrypt(data, id)
		if e != nil {
			return nil, e
		}
		if e = validateBundle(b, *alias, *allowed, *userExport); e != nil {
			return nil, e
		}
		if e = publish(*vault, *alias, *expected, b, data); e != nil {
			return nil, e
		}
		return map[string]any{"status": "imported_unverified", "version": b.Version, "fixture_only": b.FixtureOnly}, nil
	case "share":
		if !strings.HasSuffix(*out, ".age") {
			return nil, fail("age_extension_required")
		}
		// Sharing directories contain ciphertext only, never the identity directory.
		outAbs, e := filepath.Abs(*out)
		if e != nil {
			return nil, fail("invalid_path")
		}
		keysAbs, _ := filepath.Abs(keydir)
		identityAbs, _ := filepath.Abs(*idpath)
		if filepath.Dir(outAbs) == filepath.Dir(identityAbs) || strings.HasPrefix(strings.ToLower(outAbs), strings.ToLower(keysAbs)+string(filepath.Separator)) {
			return nil, fail("shared_key_directory_rejected")
		}
		r, e := getRegistry(dir)
		if e != nil || r.Version < 1 {
			return nil, fail("session_unavailable")
		}
		data, e := readBounded(filepath.Join(dir, strconv.Itoa(r.Version)+".age"), maxBundle)
		if e != nil {
			return nil, e
		}
		id, e := identity(*idpath)
		if e != nil {
			return nil, e
		}
		b, e := decrypt(data, id)
		if e != nil {
			return nil, e
		}
		if e = validateBundle(b, *alias, *allowed, *userExport); e != nil {
			return nil, e
		}
		rs, e := recipients(recipientFiles)
		if e != nil {
			return nil, e
		}
		encrypted, e := encrypt(b, rs)
		if e != nil {
			return nil, e
		}
		if e = exclusive(*out, encrypted); e != nil {
			return nil, e
		}
		return map[string]any{"status": "shared_encrypted", "version": b.Version, "fixture_only": b.FixtureOnly}, nil
	default:
		return nil, fail("unknown_command")
	}
}
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
	result, e := run(os.Args[1:])
	if e != nil {
		result = map[string]any{"status": "failed", "error_code": e.Error(), "useful_records": 0}
	}
	result["schema_version"] = 1
	result["suite"] = "sessions"
	result["timings"] = map[string]any{"execution_ms": float64(time.Since(start).Microseconds()) / 1000}
	result["versions"] = map[string]string{"go": runtime.Version(), "age": ageVersion}
	result["cgroup"] = cgroup()
	if _, ok := result["useful_records"]; !ok {
		result["useful_records"] = 0
	}
	json.NewEncoder(os.Stdout).Encode(result)
	if e != nil || result["status"] == "failed" {
		os.Exit(1)
	}
}
