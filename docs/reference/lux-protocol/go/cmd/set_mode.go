package cmd

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/jefflaplante/lux/internal/lux"
	"github.com/spf13/cobra"
)

// modeAliases maps user-friendly names to bit positions in register 21.
// Case-insensitive lookup is done via strings.ToLower.
var modeAliases = map[string]int{
	"eps":              0,
	"overloadderate":   1,
	"drms":             2,
	"lvrt":             3,
	"antiisland":       4,
	"neutraldetect":    5,
	"gridonpowerss":    6,
	"accharge":         7,
	"ac-charge":        7,
	"seamlesseps":      8,
	"standby":          9,
	"forceddischarge":  10,
	"forced-discharge": 10,
	"chargepriority":   11,
	"charge-priority":  11,
	"iso":              12,
	"gfci":             13,
	"dci":              14,
	"feedingrid":       15,
	"feed-in-grid":     15,
}

var setModeCmd = &cobra.Command{
	Use:   "set-mode <mode> <on|off>",
	Short: "Toggle an inverter mode flag",
	Long: `Toggle a bit in the master function bitmask (register 21).

Uses read-modify-write: reads current value, flips the target bit, writes back.

Available modes:
  ac-charge, forced-discharge, charge-priority, feed-in-grid,
  eps, standby, seamless-eps, and others (see lux read h21)

Examples:
  lux set-mode ac-charge on
  lux set-mode forced-discharge off
  lux set-mode charge-priority on --yes`,
	Args: cobra.ExactArgs(2),
	RunE: runSetMode,
}

func init() {
	setModeCmd.Flags().BoolP("yes", "y", false, "skip confirmation prompt")
	setModeCmd.Flags().Int("wait", 5, "max seconds to wait for read/verify")
	setModeCmd.Flags().Bool("json", false, "output as JSON (implies --yes)")
	rootCmd.AddCommand(setModeCmd)
}

func runSetMode(cmd *cobra.Command, args []string) error {
	yes, _ := cmd.Flags().GetBool("yes")
	wait, _ := cmd.Flags().GetInt("wait")
	jsonFlag, _ := cmd.Flags().GetBool("json")
	if jsonFlag {
		yes = true
	}

	modeName := strings.ToLower(args[0])
	action := strings.ToLower(args[1])

	bit, ok := modeAliases[modeName]
	if !ok {
		// Try exact match against MasterFunctionBits values
		for b, name := range lux.MasterFunctionBits {
			if strings.EqualFold(name, modeName) {
				bit = b
				ok = true
				break
			}
		}
	}
	if !ok {
		var names []string
		for k := range modeAliases {
			names = append(names, k)
		}
		return fmt.Errorf("unknown mode %q\nAvailable: %s", args[0], strings.Join(names, ", "))
	}

	var setOn bool
	switch action {
	case "on", "enable", "1", "true":
		setOn = true
	case "off", "disable", "0", "false":
		setOn = false
	default:
		return fmt.Errorf("invalid action %q: use on or off", args[1])
	}

	bitName := lux.MasterFunctionBits[bit]

	// Read current register 21
	client, err := newClient(cmd)
	if err != nil {
		return err
	}
	defer client.Close()

	current, err := readHoldingReg(client, 21, wait)
	if err != nil {
		return fmt.Errorf("read register 21: %w", err)
	}

	// Compute new value
	var newVal uint16
	if setOn {
		newVal = current | (1 << bit)
	} else {
		newVal = current & ^(1 << bit)
	}

	def, _ := lux.GetRegisterDef("holding", 21)

	if current == newVal {
		if jsonFlag {
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			return enc.Encode(map[string]any{
				"mode": modeName, "bit": bit, "action": action,
				"old_raw": current, "new_raw": newVal,
				"written": false, "verified": true, "verify_raw": current,
			})
		}
		fmt.Printf("%s is already %s.\n", bitName, action)
		return nil
	}

	if !jsonFlag {
		fmt.Printf("Mode:    %s → %s\n", bitName, strings.ToUpper(action))
		fmt.Printf("Current: %s (raw: %d)\n", def.FormatValueOnly(current), current)
		fmt.Printf("New:     %s (raw: %d)\n", def.FormatValueOnly(newVal), newVal)
	}

	if !yes {
		fmt.Print("Confirm? [y/N] ")
		reader := bufio.NewReader(os.Stdin)
		line, _ := reader.ReadString('\n')
		line = strings.TrimSpace(strings.ToLower(line))
		if line != "y" && line != "yes" {
			fmt.Println("Cancelled.")
			return nil
		}
	}

	// Reconnect for write + verify
	client2, err := newClient(cmd)
	if err != nil {
		return fmt.Errorf("reconnect: %w", err)
	}
	defer client2.Close()

	if err := client2.WriteSingle(21, newVal); err != nil {
		return fmt.Errorf("write failed: %w", err)
	}
	if !jsonFlag {
		fmt.Print("Written. ")
	}

	time.Sleep(500 * time.Millisecond)

	verifyVal, err := readHoldingReg(client2, 21, wait)

	if jsonFlag {
		result := map[string]any{
			"mode":    modeName,
			"bit":     bit,
			"action":  action,
			"old_raw": current,
			"new_raw": newVal,
			"written": true,
		}
		if err == nil {
			result["verified"] = verifyVal == newVal
			result["verify_raw"] = verifyVal
		} else {
			result["verified"] = false
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(result)
	}

	if err != nil {
		fmt.Println("Verify: could not read back register")
	} else if verifyVal == newVal {
		fmt.Printf("Verified: %s (raw: %d)\n", def.FormatValueOnly(verifyVal), verifyVal)
	} else {
		fmt.Printf("WARNING: read-back mismatch! Expected %d, got %d\n", newVal, verifyVal)
	}

	return nil
}

// readHoldingReg reads a single holding register with active request and timeout.
func readHoldingReg(client *lux.Client, reg uint16, waitSec int) (uint16, error) {
	timeout := time.After(time.Duration(waitSec) * time.Second)
	go func() {
		<-timeout
		client.Close()
	}()

	done := make(chan error, 1)
	go func() {
		done <- client.Listen(func(pkt *lux.Packet) {
			if _, ok := client.GetHolding(reg); ok {
				client.Close()
			}
		})
	}()

	client.ReadHold(reg, 1)
	<-done

	val, ok := client.GetHolding(reg)
	if !ok {
		return 0, fmt.Errorf("register %d not received", reg)
	}
	return val, nil
}
