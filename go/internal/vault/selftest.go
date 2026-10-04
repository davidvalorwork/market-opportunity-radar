package vault

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"time"

	"filippo.io/age"
)

// This event is synthetic; no path to a browser profile or network operation exists.
type transferEvent struct {
	SchemaVersion int    `json:"schema_version"`
	FixtureOnly   bool   `json:"fixture_only"`
	Operation     string `json:"operation"`
	Alias         string `json:"alias"`
	AllowedOrigin string `json:"allowed_origin"`
}

var syntheticEvent = transferEvent{1, true, "session_transfer", "demo-session", "http://localhost:8765"}

func fixtureBundle() bundle {
	return bundle{1, true, syntheticEvent.Alias, 1, "playwright_storage_state", "2026-10-03T12:00:00Z", []string{syntheticEvent.AllowedOrigin}, "sessionlab-1", storageState{
		Cookies: []cookie{{Name: "fixture-cookie", Value: "SYNTHETIC-COOKIE-NEVER-LOG", Domain: "localhost", Path: "/", Expires: -1, HTTPOnly: true, SameSite: "Lax"}},
		Origins: []originState{{Origin: syntheticEvent.AllowedOrigin, LocalStorage: []entry{{Name: "fixture-local", Value: "SYNTHETIC-LOCAL-NEVER-LOG"}}, IndexedDB: json.RawMessage(`[{"name":"fixture-db","version":1,"stores":[{"name":"fixture-store","autoIncrement":false,"records":[{"key":"fixture-key","value":"SYNTHETIC-IDB-NEVER-LOG"}],"indexes":[]}]}]`)}},
	}}
}

// Selftest runs synthetic in-memory and temp-directory checks; it never touches real sessions.
func Selftest() map[string]any {
	checks := map[string]bool{}
	timings := map[string]float64{}
	check := func(name string, fn func() bool) {
		start := time.Now()
		checks[name] = fn()
		timings[name] = float64(time.Since(start).Microseconds()) / 1000
	}
	b := fixtureBundle()
	check("fixture_event", func() bool {
		return syntheticEvent.SchemaVersion == 1 && syntheticEvent.FixtureOnly && syntheticEvent.Operation == "session_transfer" && validateBundle(b, syntheticEvent.Alias, syntheticEvent.AllowedOrigin, false) == nil
	})
	a, ea := age.GenerateX25519Identity()
	second, eb := age.GenerateX25519Identity()
	outsider, ec := age.GenerateX25519Identity()
	if ea != nil || eb != nil || ec != nil {
		return map[string]any{"status": "failed", "fixture_only": true, "checks": map[string]bool{"keygen": false}, "useful_records": 0}
	}
	encrypted, ee := encrypt(b, []age.Recipient{a.Recipient(), second.Recipient()})
	check("two_authorized_recipients", func() bool {
		if ee != nil {
			return false
		}
		for _, id := range []*age.X25519Identity{a, second} {
			opened, e := decrypt(encrypted, id)
			if e != nil {
				return false
			}
			actual, _ := json.Marshal(opened)
			expected, _ := json.Marshal(b)
			if !bytes.Equal(actual, expected) {
				return false
			}
		}
		return true
	})
	check("wrong_key_rejected", func() bool { _, e := decrypt(encrypted, outsider); return e != nil })
	check("altered_ciphertext_rejected", func() bool {
		bad := append([]byte(nil), encrypted...)
		if len(bad) == 0 {
			return false
		}
		bad[len(bad)-1] ^= 1
		_, e := decrypt(bad, a)
		return e != nil
	})
	check("ciphertext_has_no_plaintext", func() bool { return !bytes.Contains(encrypted, []byte("SYNTHETIC-")) })
	check("oversized_ciphertext_rejected", func() bool { _, e := decrypt(make([]byte, maxBundle+1), a); return e != nil })
	check("aliases_windows_safe", func() bool {
		for _, s := range []string{"../escape", "..", "a/b", "a\\b", "a:stream", "con", "com1", "lpt9", "-name", "a.", "a "} {
			if validAlias(s) {
				return false
			}
		}
		return validAlias("demo-session")
	})
	check("off_origin_rejected", func() bool {
		bad := fixtureBundle()
		bad.State.Origins[0].Origin = "https://foreign.example"
		return validateBundle(bad, b.Alias, syntheticEvent.AllowedOrigin, false) != nil
	})
	check("off_domain_cookie_rejected", func() bool {
		bad := fixtureBundle()
		bad.State.Cookies[0].Domain = "foreign.example"
		return validateBundle(bad, b.Alias, syntheticEvent.AllowedOrigin, false) != nil
	})
	check("wrong_alias_rejected", func() bool { return validateBundle(b, "other-alias", syntheticEvent.AllowedOrigin, false) != nil })
	check("real_origin_fixture_rejected", func() bool { _, e := canonicalOrigin("https://example.com", true); return e != nil })
	check("origin_credentials_query_path_rejected", func() bool {
		for _, s := range []string{"http://u:p@localhost:8765", "http://localhost:8765/", "http://localhost:8765?token=x", "http://localhost:8765#x", "file://localhost"} {
			if _, e := canonicalOrigin(s, true); e == nil {
				return false
			}
		}
		return true
	})
	check("cookie_array_and_session_storage_rejected", func() bool {
		var s storageState
		return decode([]byte(`[]`), &s) != nil && decode([]byte(`{"cookies":[],"origins":[],"sessionStorage":{"secret":"synthetic"}}`), &s) != nil
	})
	check("user_export_requires_opt_in", func() bool {
		bad := fixtureBundle()
		bad.FixtureOnly = false
		return validateBundle(bad, b.Alias, syntheticEvent.AllowedOrigin, false) != nil && validateBundle(bad, b.Alias, syntheticEvent.AllowedOrigin, true) == nil
	})
	dir, et := os.MkdirTemp("", "sessionlab-selftest-")
	if et != nil {
		checks["private_temp_directory"] = false
	} else {
		defer os.RemoveAll(dir)
		vault := filepath.Join(dir, "owner-vault")
		worker := filepath.Join(dir, "worker-vault")
		shared := filepath.Join(dir, "shared")
		check("private_temp_directory", func() bool { return privateDir(shared) == nil })
		call := func(args ...string) bool { _, e := Run(args); return e == nil }
		check("cli_keygen_separate_vaults", func() bool {
			return call("keygen", "--vault", vault) && call("keygen", "--vault", worker) && !call("keygen", "--vault", vault)
		})
		statepath := filepath.Join(dir, "synthetic-state.json")
		stateJSON, _ := json.Marshal(b.State)
		check("cli_prepare", func() bool {
			return exclusive(statepath, stateJSON) == nil && call("prepare", "--vault", vault, "--alias", b.Alias, "--state-file", statepath, "--recipient-file", filepath.Join(worker, "keys", "recipient.txt"))
		})
		bundlepath := filepath.Join(shared, "demo-v1.age")
		check("cli_share", func() bool {
			return call("share", "--vault", vault, "--alias", b.Alias, "--out", bundlepath, "--recipient-file", filepath.Join(worker, "keys", "recipient.txt"))
		})
		check("private_key_never_shared", func() bool {
			if call("share", "--vault", vault, "--alias", b.Alias, "--out", filepath.Join(vault, "keys", "unsafe.age"), "--recipient-file", filepath.Join(worker, "keys", "recipient.txt")) {
				return false
			}
			entries, e := os.ReadDir(shared)
			if e != nil || len(entries) != 1 {
				return false
			}
			return entries[0].Name() == "demo-v1.age"
		})
		check("cli_import", func() bool { return call("import", "--vault", worker, "--alias", b.Alias, "--bundle", bundlepath) })
		check("cli_status_unverified", func() bool {
			r, e := Run([]string{"status", "--vault", worker, "--alias", b.Alias})
			return e == nil && r["status"] == "unverified" && r["version"] == 1
		})
		check("cas_rejects_old_worker", func() bool {
			return !call("import", "--vault", worker, "--alias", b.Alias, "--bundle", bundlepath) && !call("renew", "--vault", vault, "--alias", b.Alias, "--state-file", statepath, "--expected-version", "2")
		})
		check("active_lock_blocks_renew", func() bool {
			lock := filepath.Join(vault, "sessions", b.Alias, ".lock")
			if os.Mkdir(lock, 0700) != nil {
				return false
			}
			defer os.Remove(lock)
			return !call("renew", "--vault", vault, "--alias", b.Alias, "--state-file", statepath, "--expected-version", "1")
		})
		check("cli_renew_immutable_version", func() bool {
			if !call("renew", "--vault", vault, "--alias", b.Alias, "--state-file", statepath, "--expected-version", "1") {
				return false
			}
			r, e := getRegistry(filepath.Join(vault, "sessions", b.Alias))
			if e != nil || r.Version != 2 {
				return false
			}
			for _, v := range []int{1, 2} {
				if _, e := os.Stat(filepath.Join(vault, "sessions", b.Alias, strconv.Itoa(v)+".age")); e != nil {
					return false
				}
			}
			return true
		})
		check("oversized_state_file_rejected", func() bool {
			p := filepath.Join(dir, "synthetic-oversized.json")
			return exclusive(p, make([]byte, maxState+1)) == nil && !call("renew", "--vault", vault, "--alias", b.Alias, "--state-file", p, "--expected-version", "2")
		})
		check("failed_renew_preserves_pointer", func() bool {
			r, e := getRegistry(filepath.Join(vault, "sessions", b.Alias))
			return e == nil && r.Version == 2
		})
	}
	status := "passed"
	useful := 2
	for _, ok := range checks {
		if !ok {
			status = "failed"
			useful = 0
		}
	}
	return map[string]any{"status": status, "fixture_only": true, "checks": checks, "check_timings_ms": timings, "useful_records": useful}
}
