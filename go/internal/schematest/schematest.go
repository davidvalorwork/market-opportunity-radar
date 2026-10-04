// Package schematest validates values against the canonical JSON Schemas in
// src/radar/schemas with santhosh-tekuri/jsonschema/v6, the same loader as
// contracts/validate/go. Imported only by tests; no binary links it.
package schematest

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"testing"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

const baseID = "https://market-opportunity-radar.invalid/contracts/"

var (
	once    sync.Once
	root    string
	schemas = map[string]*jsonschema.Schema{}
	loadErr error
)

// Root is the repository root: the nearest parent of the working directory that has
// src/radar/schemas (go test runs in the package directory, Docker keeps the same layout).
func Root(t testing.TB) string {
	t.Helper()
	once.Do(load)
	if loadErr != nil {
		t.Fatal(loadErr)
	}
	return root
}

func load() {
	dir, err := filepath.Abs(".")
	for err == nil {
		if _, e := os.Stat(filepath.Join(dir, "src", "radar", "schemas", "common.v1.json")); e == nil {
			break
		}
		if filepath.Dir(dir) == dir {
			err = os.ErrNotExist
			break
		}
		dir = filepath.Dir(dir)
	}
	if err != nil {
		loadErr = err
		return
	}
	root = dir
	paths, _ := filepath.Glob(filepath.Join(root, "src", "radar", "schemas", "*.json"))
	versioned := regexp.MustCompile(`\.v\d+\.json$`)
	c := jsonschema.NewCompiler()
	c.DefaultDraft(jsonschema.Draft2020)
	c.AssertFormat()
	var names []string
	for _, p := range paths {
		if !versioned.MatchString(p) {
			continue
		}
		f, err := os.Open(p)
		if err != nil {
			loadErr = err
			return
		}
		doc, err := jsonschema.UnmarshalJSON(f)
		f.Close()
		name := strings.TrimSuffix(filepath.Base(p), ".json")
		if err == nil {
			err = c.AddResource(baseID+name+".json", doc)
		}
		if err != nil {
			loadErr = err
			return
		}
		names = append(names, name)
	}
	for _, name := range names {
		if schemas[name], loadErr = c.Compile(baseID + name + ".json"); loadErr != nil {
			return
		}
	}
}

// Validate marshals v and checks it against the named schema (e.g. "whatsapp.result.v1").
func Validate(t testing.TB, name string, v any) {
	t.Helper()
	Root(t)
	s := schemas[name]
	if s == nil {
		t.Fatalf("schema %s not found", name)
	}
	data, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	inst, err := jsonschema.UnmarshalJSON(bytes.NewReader(data))
	if err == nil {
		err = s.Validate(inst)
	}
	if err != nil {
		t.Errorf("%s rejects Go output: %v", name, err)
	}
}
