package cmd

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/jefflaplante/lux/internal/lux"
	"github.com/spf13/cobra"
)

var writeCmd = &cobra.Command{
	Use:   "write <register> <value>",
	Short: "Write a holding register value",
	Long: `Write a value to a single holding register on the inverter.

Includes safety checks (protected register rejection, rate limiting),
confirmation prompt, and read-back verification.

Examples:
  lux write h67 80                   set AC Charge SOC Limit to 80
  lux write 67 80                    same (bare number = holding)
  lux write "AC Charge SOC Limit" 80 by name
  lux write h67 80 --yes             skip confirmation`,
	Args: cobra.ExactArgs(2),
	RunE: runWrite,
}

func init() {
	writeCmd.Flags().BoolP("yes", "y", false, "skip confirmation prompt")
	writeCmd.Flags().Int("wait", 5, "max seconds to wait for read/verify")
	writeCmd.Flags().Bool("json", false, "output as JSON (implies --yes)")
	rootCmd.AddCommand(writeCmd)
}

func runWrite(cmd *cobra.Command, args []string) error {
	yes, _ := cmd.Flags().GetBool("yes")
	wait, _ := cmd.Flags().GetInt("wait")
	jsonFlag, _ := cmd.Flags().GetBool("json")
	if jsonFlag {
		yes = true
	}

	// Parse register
	targets, err := parseRegArg(args[0])
	if err != nil {
		return err
	}

	// Must resolve to exactly one holding register
	var holdingTargets []regTarget
	for _, t := range targets {
		if t.regType == "holding" {
			holdingTargets = append(holdingTargets, t)
		}
	}
	if len(holdingTargets) == 0 {
		return fmt.Errorf("only holding registers can be written (input registers are read-only)")
	}
	if len(holdingTargets) > 1 {
		names := make([]string, len(holdingTargets))
		for i, t := range holdingTargets {
			name := lux.RegisterName("holding", t.regNum)
			if name == "" {
				name = fmt.Sprintf("h%d", t.regNum)
			}
			names[i] = fmt.Sprintf("h%d (%s)", t.regNum, name)
		}
		return fmt.Errorf("ambiguous register match, be more specific: %s", strings.Join(names, ", "))
	}
	target := holdingTargets[0]

	// Parse value
	val, err := strconv.ParseUint(args[1], 10, 16)
	if err != nil {
		return fmt.Errorf("invalid value %q: must be 0-65535", args[1])
	}
	newVal := uint16(val)

	// Connect and read current value
	client, err := newClient(cmd)
	if err != nil {
		return err
	}
	defer client.Close()

	timeout := time.After(time.Duration(wait) * time.Second)
	go func() {
		<-timeout
		client.Close()
	}()

	done := make(chan error, 1)
	go func() {
		done <- client.Listen(func(pkt *lux.Packet) {
			if _, ok := client.GetHolding(target.regNum); ok {
				client.Close()
			}
		})
	}()

	client.ReadHold(target.regNum, 1)
	<-done

	// Show current and proposed values
	name := lux.RegisterName("holding", target.regNum)
	if name == "" {
		name = fmt.Sprintf("r%d", target.regNum)
	}

	oldRaw, hasOld := client.GetHolding(target.regNum)

	if hasOld && oldRaw == newVal {
		if jsonFlag {
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			return enc.Encode(map[string]any{
				"register": target.regNum, "name": name,
				"old_value": formatRegValue("holding", target.regNum, oldRaw), "old_raw": oldRaw,
				"new_value": formatRegValue("holding", target.regNum, newVal), "new_raw": newVal,
				"written": false, "verified": true, "verify_raw": oldRaw,
			})
		}
		fmt.Printf("Register: h%d %s\n", target.regNum, name)
		fmt.Printf("Current:  %s (raw: %d)\n", formatRegValue("holding", target.regNum, oldRaw), oldRaw)
		fmt.Println("Value unchanged, skipping write.")
		return nil
	}

	if !jsonFlag {
		fmt.Printf("Register: h%d %s\n", target.regNum, name)
		if hasOld {
			fmt.Printf("Current:  %s (raw: %d)\n", formatRegValue("holding", target.regNum, oldRaw), oldRaw)
		} else {
			fmt.Printf("Current:  (could not read)\n")
		}
		fmt.Printf("New:      %s (raw: %d)\n", formatRegValue("holding", target.regNum, newVal), newVal)
	}

	// Confirm
	if !yes {
		fmt.Print("Confirm write? [y/N] ")
		reader := bufio.NewReader(os.Stdin)
		line, _ := reader.ReadString('\n')
		line = strings.TrimSpace(strings.ToLower(line))
		if line != "y" && line != "yes" {
			fmt.Println("Cancelled.")
			return nil
		}
	}

	// Reconnect for write + verify (previous connection was closed)
	client2, err := newClient(cmd)
	if err != nil {
		return fmt.Errorf("reconnect for write: %w", err)
	}
	defer client2.Close()

	// Write
	if err := client2.WriteSingle(target.regNum, newVal); err != nil {
		return fmt.Errorf("write failed: %w", err)
	}
	if !jsonFlag {
		fmt.Print("Written. ")
	}

	// Wait for the dongle to process the write before reading back
	time.Sleep(500 * time.Millisecond)

	// Read-back verification
	timeout2 := time.After(time.Duration(wait) * time.Second)
	go func() {
		<-timeout2
		client2.Close()
	}()

	done2 := make(chan error, 1)
	go func() {
		done2 <- client2.Listen(func(pkt *lux.Packet) {
			if _, ok := client2.GetHolding(target.regNum); ok {
				client2.Close()
			}
		})
	}()

	client2.ReadHold(target.regNum, 1)
	<-done2

	verifyRaw, ok := client2.GetHolding(target.regNum)

	if jsonFlag {
		result := map[string]any{
			"register":  target.regNum,
			"name":      name,
			"old_value": formatRegValue("holding", target.regNum, oldRaw),
			"old_raw":   oldRaw,
			"new_value": formatRegValue("holding", target.regNum, newVal),
			"new_raw":   newVal,
			"written":   true,
			"verified":  ok && verifyRaw == newVal,
		}
		if ok {
			result["verify_raw"] = verifyRaw
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(result)
	}

	if !ok {
		fmt.Println("Verify: could not read back register")
	} else if verifyRaw == newVal {
		fmt.Printf("Verified: %s (raw: %d)\n", formatRegValue("holding", target.regNum, verifyRaw), verifyRaw)
	} else {
		fmt.Printf("WARNING: read-back mismatch! Expected %d, got %d\n", newVal, verifyRaw)
	}

	return nil
}
