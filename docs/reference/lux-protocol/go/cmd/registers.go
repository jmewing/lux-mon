package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"

	"github.com/jefflaplante/lux/internal/lux"
	"github.com/spf13/cobra"
)

var registersCmd = &cobra.Command{
	Use:   "registers",
	Short: "List available registers by ID and name",
	Long:  `Lists all defined holding and input registers, showing their ID, name, and description.`,
	RunE:  runRegisters,
}

func init() {
	registersCmd.Flags().BoolP("holding", "H", false, "show only holding registers")
	registersCmd.Flags().BoolP("input", "I", false, "show only input registers")
	registersCmd.Flags().StringP("filter", "f", "", "case-insensitive name filter")
	registersCmd.Flags().Bool("json", false, "output as JSON")
	rootCmd.AddCommand(registersCmd)
}

type regListEntry struct {
	Type        string `json:"type"`
	Register    uint16 `json:"register"`
	Name        string `json:"name"`
	Description string `json:"description"`
	Unit        string `json:"unit,omitempty"`
}

func runRegisters(cmd *cobra.Command, args []string) error {
	holdingOnly, _ := cmd.Flags().GetBool("holding")
	inputOnly, _ := cmd.Flags().GetBool("input")
	filter, _ := cmd.Flags().GetString("filter")
	jsonFlag, _ := cmd.Flags().GetBool("json")
	filterLower := strings.ToLower(filter)

	if jsonFlag {
		var entries []regListEntry
		if !inputOnly {
			entries = append(entries, collectRegEntries("holding", "h", lux.HoldingRegisters, filterLower)...)
		}
		if !holdingOnly {
			entries = append(entries, collectRegEntries("input", "i", lux.InputRegisters, filterLower)...)
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(entries)
	}

	if !inputOnly {
		printRegisterList("HOLDING REGISTERS", "h", lux.HoldingRegisters, filterLower)
	}
	if !holdingOnly {
		if !inputOnly {
			fmt.Println()
		}
		printRegisterList("INPUT REGISTERS", "i", lux.InputRegisters, filterLower)
	}
	return nil
}

func collectRegEntries(regType, prefix string, regs map[uint16]lux.RegisterDef, filter string) []regListEntry {
	keys := make([]uint16, 0, len(regs))
	for k := range regs {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool { return keys[i] < keys[j] })

	var entries []regListEntry
	for _, num := range keys {
		def := regs[num]
		if filter != "" && !strings.Contains(strings.ToLower(def.Name), filter) {
			continue
		}
		entries = append(entries, regListEntry{
			Type:        regType,
			Register:    num,
			Name:        def.Name,
			Description: def.Description,
			Unit:        def.Unit,
		})
	}
	return entries
}

func printRegisterList(title, prefix string, regs map[uint16]lux.RegisterDef, filter string) {
	keys := make([]uint16, 0, len(regs))
	for k := range regs {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool { return keys[i] < keys[j] })

	fmt.Println(title)
	for _, num := range keys {
		def := regs[num]
		if filter != "" && !strings.Contains(strings.ToLower(def.Name), filter) {
			continue
		}
		desc := def.Description
		if def.Unit != "" {
			desc += " [" + def.Unit + "]"
		}
		fmt.Printf("  %-6s %-35s %s\n", fmt.Sprintf("%s%d", prefix, num), def.Name, desc)
	}
}
