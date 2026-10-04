// Technical public DNS helper: stdin/stdout only, no shell, forks or host argv.
package main

import (
	"os"

	"radar.local/public-dns/internal/resolver"
)

func main() {
	if len(os.Args) != 1 {
		os.Exit(1)
	}
	if err := resolver.Run(os.Stdin, os.Stdout, nil); err != nil {
		_, _ = os.Stderr.WriteString(resolver.ErrorCode(err) + "\n")
		os.Exit(1)
	}
}
