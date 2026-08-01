package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/jefflaplante/lux/internal/lux"
	"github.com/spf13/cobra"
)

var dumpCmd = &cobra.Command{
	Use:   "dump",
	Short: "Connect, collect a full register set, and output",
	Long:  `Collects register data for a configurable duration, then outputs as JSON or a formatted table.`,
	RunE:  runDump,
}

func init() {
	dumpCmd.Flags().Int("wait", 150, "max seconds to wait for data (exits early when all registers received)")
	dumpCmd.Flags().String("format", "json", "output format: json or table")
	dumpCmd.Flags().Bool("all", false, "show all registers including unlabeled")
	dumpCmd.Flags().Bool("pretty", false, "pretty-print JSON output")
	rootCmd.AddCommand(dumpCmd)
}

func runDump(cmd *cobra.Command, args []string) error {
	client, err := newClient(cmd)
	if err != nil {
		return err
	}
	defer client.Close()

	wait, _ := cmd.Flags().GetInt("wait")
	format, _ := cmd.Flags().GetString("format")
	showAll, _ := cmd.Flags().GetBool("all")
	pretty, _ := cmd.Flags().GetBool("pretty")

	timeout := time.After(time.Duration(wait) * time.Second)
	go func() {
		<-timeout
		client.Close()
	}()

	// Exit early once all defined registers have been received
	client.Listen(func(pkt *lux.Packet) {
		if lux.HaveAllRegisters(client.AllHolding(), client.AllInput()) {
			client.Close()
		}
	})

	holdingRegs := client.AllHolding()
	inputRegs := client.AllInput()

	if format == "table" {
		fmt.Printf("Timestamp: %s\n", time.Now().Format(time.RFC3339))
		printTable("Holding Registers", holdingRegs, "holding", showAll)
		printTable("Input Registers", inputRegs, "input", showAll)
		return nil
	}

	// JSON output (default) - iterate in sorted register order for deterministic output
	holding := make(map[string]any)
	holdingAnomalies := []map[string]any{}
	holdingKeys := sortedKeys(holdingRegs)
	for _, reg := range holdingKeys {
		raw := holdingRegs[reg]
		def, ok := lux.GetRegisterDef("holding", reg)
		if ok {
			entry := map[string]any{
				"register":    reg,
				"raw":         raw,
				"value":       def.FormatValue(raw),
				"description": def.Description,
			}
			if def.Unit != "" {
				entry["unit"] = def.Unit
			}
			if warning := def.Validate(raw); warning != "" {
				entry["anomaly"] = warning
				holdingAnomalies = append(holdingAnomalies, map[string]any{
					"register": reg,
					"name":     def.Name,
					"value":    def.FormatValue(raw),
					"warning":  warning,
				})
			}
			holding[def.Name] = entry
		} else if showAll {
			holding[fmt.Sprintf("r%d", reg)] = raw
		}
	}

	input := make(map[string]any)
	inputAnomalies := []map[string]any{}
	inputKeys := sortedKeys(inputRegs)
	for _, reg := range inputKeys {
		raw := inputRegs[reg]
		def, ok := lux.GetRegisterDef("input", reg)
		if ok {
			entry := map[string]any{
				"register":    reg,
				"raw":         raw,
				"value":       def.FormatValue(raw),
				"description": def.Description,
			}
			if def.Unit != "" {
				entry["unit"] = def.Unit
			}
			if warning := def.Validate(raw); warning != "" {
				entry["anomaly"] = warning
				inputAnomalies = append(inputAnomalies, map[string]any{
					"register": reg,
					"name":     def.Name,
					"value":    def.FormatValue(raw),
					"warning":  warning,
				})
			}
			input[def.Name] = entry
		} else if showAll {
			input[fmt.Sprintf("r%d", reg)] = raw
		}
	}

	anomalies := map[string]any{}
	if len(holdingAnomalies) > 0 {
		anomalies["holding"] = holdingAnomalies
	}
	if len(inputAnomalies) > 0 {
		anomalies["input"] = inputAnomalies
	}

	result := map[string]any{
		"timestamp": time.Now().Format(time.RFC3339),
		"holding":   holding,
		"input":     input,
	}
	if len(anomalies) > 0 {
		result["anomalies"] = anomalies
	}
	enc := json.NewEncoder(os.Stdout)
	if pretty {
		enc.SetIndent("", "  ")
	}
	return enc.Encode(result)
}

func sortedKeys(m map[uint16]uint16) []uint16 {
	keys := make([]uint16, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool { return keys[i] < keys[j] })
	return keys
}

func printTable(title string, regs map[uint16]uint16, regType string, showAll bool) {
	fmt.Printf("\n%s\n", title)
	fmt.Printf("%-6s %-35s %15s %8s  %s\n", "Reg", "Name", "Value", "Unit", "Status")
	fmt.Println(strings.Repeat("-", 80))

	for _, reg := range sortedKeys(regs) {
		raw := regs[reg]
		def, ok := lux.GetRegisterDef(regType, reg)
		if ok {
			val := def.FormatValueOnly(raw)
			warning := def.Validate(raw)
			if warning != "" {
				fmt.Printf("%-6d %-35s %15s %8s  ⚠ %s\n", reg, def.Name, val, def.Unit, warning)
			} else {
				fmt.Printf("%-6d %-35s %15s %8s\n", reg, def.Name, val, def.Unit)
			}
		} else if showAll {
			fmt.Printf("%-6d %-35s %15d %8s\n", reg, fmt.Sprintf("r%d", reg), raw, "")
		}
	}
}
