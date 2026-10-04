// Validate contracts/examples golden files (and capabilities.json) with santhosh-tekuri/jsonschema v6.
package validate

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

const (
	maxBytes = 32768
	baseID   = "https://market-opportunity-radar.invalid/contracts/"
	root     = "../.."
)

func readJSON(t *testing.T, path string) any {
	t.Helper()
	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	doc, err := jsonschema.UnmarshalJSON(f)
	if err != nil {
		t.Fatalf("%s: %v", path, err)
	}
	return doc
}

func serializedSize(instance any) (int, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(instance); err != nil {
		return 0, err
	}
	return buf.Len() - 1, nil // Encode appends '\n'
}

func check(schema *jsonschema.Schema, instance any) error {
	size, err := serializedSize(instance)
	if err != nil {
		return err
	}
	if size > maxBytes {
		return fmt.Errorf("serialized size %d exceeds %d bytes", size, maxBytes)
	}
	return schema.Validate(instance)
}

func TestGoldenExamples(t *testing.T) {
	paths, _ := filepath.Glob(filepath.Join(root, "*.json"))
	versioned := regexp.MustCompile(`\.v\d+\.json$`)
	compiler := jsonschema.NewCompiler()
	compiler.DefaultDraft(jsonschema.Draft2020)
	compiler.AssertFormat()
	var names []string
	for _, path := range paths {
		if !versioned.MatchString(path) {
			continue
		}
		name := strings.TrimSuffix(filepath.Base(path), ".json")
		names = append(names, name)
		if err := compiler.AddResource(baseID+name+".json", readJSON(t, path)); err != nil {
			t.Fatal(err)
		}
	}
	sort.Strings(names)
	schemas := map[string]*jsonschema.Schema{}
	for _, name := range names {
		schema, err := compiler.Compile(baseID + name + ".json")
		if err != nil {
			t.Fatalf("compile %s: %v", name, err)
		}
		schemas[name] = schema
	}
	total := 0
	expect := func(label, name string, instance any, valid bool) {
		total++
		err := check(schemas[name], instance)
		if (err == nil) != valid {
			t.Errorf("%s: expected valid=%v, got %v", label, valid, err)
		}
	}
	for _, name := range names {
		if name == "common.v1" {
			continue
		}
		for _, group := range []string{"valid", "invalid"} {
			files, _ := filepath.Glob(filepath.Join(root, "examples", group, name, "*.json"))
			if minimum := map[string]int{"valid": 2, "invalid": 3}[group]; len(files) < minimum {
				t.Errorf("%s/%s: %d examples, want >= %d", group, name, len(files), minimum)
			}
			for _, file := range files {
				expect(group+"/"+name+"/"+filepath.Base(file), name, readJSON(t, file), group == "valid")
			}
		}
	}
	expect("capabilities.json", "capabilities.v1", readJSON(t, filepath.Join(root, "capabilities.json")), true)
	t.Logf("go/jsonschema: %d examples checked across %d schemas", total, len(names))
}
