package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/jefflaplante/lux/internal/lux"
	"github.com/spf13/cobra"
)

var readCmd = &cobra.Command{
	Use:   "read <register> [register...]",
	Short: "Read specific register(s)",
	Long: `Read one or more registers from the inverter and display their values.

Registers can be specified by number or name:
  lux read 21          holding register 21 (bare number = holding)
  lux read h21         holding register 21
  lux read i5          input register 5
  lux read "SOC/SOH"   search by name (case-insensitive)
  lux read i28,i29,i30 comma-separated list`,
	Args: cobra.MinimumNArgs(1),
	RunE: runRead,
}

func init() {
	readCmd.Flags().Int("wait", 5, "max seconds to wait for data")
	readCmd.Flags().Bool("raw", false, "output raw values only (for scripting)")
	readCmd.Flags().Bool("json", false, "output as JSON")
	rootCmd.AddCommand(readCmd)
}

func runRead(cmd *cobra.Command, args []string) error {
	wait, _ := cmd.Flags().GetInt("wait")
	rawFlag, _ := cmd.Flags().GetBool("raw")
	jsonFlag, _ := cmd.Flags().GetBool("json")

	targets, err := parseRegArgs(args)
	if err != nil {
		return err
	}

	client, err := newClient(cmd)
	if err != nil {
		return err
	}
	defer client.Close()

	// Set up timeout
	timeout := time.After(time.Duration(wait) * time.Second)
	go func() {
		<-timeout
		client.Close()
	}()

	// Start listening in background (receives both broadcast and active responses)
	done := make(chan error, 1)
	go func() {
		done <- client.Listen(func(pkt *lux.Packet) {
			if allTargetsSatisfied(client, targets) {
				client.Close()
			}
		})
	}()

	// Send active read requests, batching consecutive registers of the same type
	for _, batch := range batchTargets(targets) {
		count := batch.max - batch.min + 1
		if batch.regType == "holding" {
			client.ReadHold(batch.min, count)
		} else {
			client.ReadInput(batch.min, count)
		}
	}

	<-done

	// Output results
	type result struct {
		Type     string `json:"type"`
		Register uint16 `json:"register"`
		Name     string `json:"name"`
		Value    string `json:"value"`
		Raw      uint16 `json:"raw"`
	}

	var results []result
	for _, t := range targets {
		var raw uint16
		var ok bool
		if t.regType == "holding" {
			raw, ok = client.GetHolding(t.regNum)
		} else {
			raw, ok = client.GetInput(t.regNum)
		}
		if !ok {
			fmt.Fprintf(os.Stderr, "warning: %s register %d not received\n", t.regType, t.regNum)
			continue
		}
		name := lux.RegisterName(t.regType, t.regNum)
		if name == "" {
			name = fmt.Sprintf("r%d", t.regNum)
		}
		value := formatRegValue(t.regType, t.regNum, raw)
		results = append(results, result{t.regType, t.regNum, name, value, raw})
	}

	if rawFlag {
		for _, r := range results {
			fmt.Println(r.Raw)
		}
		return nil
	}

	if jsonFlag {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(results)
	}

	// Default table output
	for _, r := range results {
		prefix := "h"
		if r.Type == "input" {
			prefix = "i"
		}
		fmt.Printf("%-5s %-35s %s  (raw: %d)\n",
			fmt.Sprintf("%s%d", prefix, r.Register),
			r.Name,
			r.Value,
			r.Raw,
		)
	}
	return nil
}
