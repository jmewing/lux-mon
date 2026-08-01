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

// scheduleType defines the register layout for a schedule group.
type scheduleType struct {
	name     string
	powerReg uint16 // power % register
	socReg   uint16 // SOC limit register
	periods  [3][2]uint16 // 3 time windows, each [start, end] register
}

var scheduleTypes = map[string]scheduleType{
	"ac-charge": {
		name: "AC Charge", powerReg: 66, socReg: 67,
		periods: [3][2]uint16{{68, 69}, {70, 71}, {72, 73}},
	},
	"charge-priority": {
		name: "Charge Priority", powerReg: 74, socReg: 75,
		periods: [3][2]uint16{{76, 77}, {78, 79}, {80, 81}},
	},
	"forced-discharge": {
		name: "Forced Discharge", powerReg: 82, socReg: 83,
		periods: [3][2]uint16{{84, 85}, {86, 87}, {88, 89}},
	},
}

var setScheduleCmd = &cobra.Command{
	Use:   "set-schedule <type> <period> <start>-<end> [--power N] [--soc N]",
	Short: "Configure charge/discharge schedules",
	Long: `Configure AC charge, charge priority, or forced discharge time windows.

Schedule types: ac-charge, charge-priority, forced-discharge
Periods: 1, 2, or 3 (each type has 3 time windows)
Times: HH:MM-HH:MM format

Examples:
  lux set-schedule ac-charge 1 22:00-06:00
  lux set-schedule ac-charge 1 22:00-06:00 --soc 80 --power 100
  lux set-schedule forced-discharge 1 15:00-21:00 --soc 10
  lux set-schedule ac-charge 2 00:00-00:00          # disable period 2

Use "lux set-schedule show" to display all current schedules.`,
	Args: cobra.MinimumNArgs(1),
	RunE: runSetSchedule,
}

func init() {
	setScheduleCmd.Flags().IntP("power", "p", -1, "power rate % (0-100)")
	setScheduleCmd.Flags().IntP("soc", "s", -1, "SOC limit % (0-100)")
	setScheduleCmd.Flags().BoolP("yes", "y", false, "skip confirmation prompt")
	setScheduleCmd.Flags().Int("wait", 5, "max seconds to wait for read/verify")
	setScheduleCmd.Flags().Bool("json", false, "output as JSON (implies --yes)")
	rootCmd.AddCommand(setScheduleCmd)
}

func runSetSchedule(cmd *cobra.Command, args []string) error {
	if strings.ToLower(args[0]) == "show" {
		return showSchedules(cmd)
	}

	if len(args) < 3 {
		return fmt.Errorf("usage: set-schedule <type> <period> <start>-<end>")
	}

	yes, _ := cmd.Flags().GetBool("yes")
	wait, _ := cmd.Flags().GetInt("wait")
	power, _ := cmd.Flags().GetInt("power")
	soc, _ := cmd.Flags().GetInt("soc")
	jsonFlag, _ := cmd.Flags().GetBool("json")
	if jsonFlag {
		yes = true
	}

	typeName := strings.ToLower(args[0])
	sched, ok := scheduleTypes[typeName]
	if !ok {
		return fmt.Errorf("unknown schedule type %q (use: ac-charge, charge-priority, forced-discharge)", args[0])
	}

	period, err := strconv.Atoi(args[1])
	if err != nil || period < 1 || period > 3 {
		return fmt.Errorf("period must be 1, 2, or 3")
	}

	startTime, endTime, err := parseTimeRange(args[2])
	if err != nil {
		return err
	}

	// Build list of writes
	type regWrite struct {
		reg  uint16
		val  uint16
		desc string
	}
	var writes []regWrite

	startReg := sched.periods[period-1][0]
	endReg := sched.periods[period-1][1]
	writes = append(writes,
		regWrite{startReg, encodeTime(startTime[0], startTime[1]), fmt.Sprintf("Period %d Start → %02d:%02d", period, startTime[0], startTime[1])},
		regWrite{endReg, encodeTime(endTime[0], endTime[1]), fmt.Sprintf("Period %d End   → %02d:%02d", period, endTime[0], endTime[1])},
	)

	if power >= 0 {
		if power > 100 {
			return fmt.Errorf("power must be 0-100%%")
		}
		writes = append(writes, regWrite{sched.powerReg, uint16(power), fmt.Sprintf("Power → %d%%", power)})
	}
	if soc >= 0 {
		if soc > 100 {
			return fmt.Errorf("SOC must be 0-100%%")
		}
		writes = append(writes, regWrite{sched.socReg, uint16(soc), fmt.Sprintf("SOC Limit → %d%%", soc)})
	}

	// Read current values
	client, err := newClient(cmd)
	if err != nil {
		return err
	}
	defer client.Close()

	currentVals, err := readHoldingRegs(client, sched.powerReg, sched.periods[2][1]-sched.powerReg+1, wait)
	if err != nil {
		return fmt.Errorf("read current schedule: %w", err)
	}

	// Display changes
	if !jsonFlag {
		fmt.Printf("Schedule: %s\n\n", sched.name)
		for _, w := range writes {
			oldRaw := currentVals[w.reg]
			oldFmt := formatRegValue("holding", w.reg, oldRaw)
			fmt.Printf("  h%-3d %-30s %s → %s\n", w.reg, w.desc, oldFmt, formatRegValue("holding", w.reg, w.val))
		}
		fmt.Println()
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

	// Reconnect and write
	client2, err := newClient(cmd)
	if err != nil {
		return fmt.Errorf("reconnect: %w", err)
	}
	defer client2.Close()

	for _, w := range writes {
		if err := client2.WriteSingle(w.reg, w.val); err != nil {
			return fmt.Errorf("write h%d: %w", w.reg, err)
		}
		time.Sleep(100 * time.Millisecond)
	}

	if !jsonFlag {
		fmt.Print("Written. ")
	}

	// Verify
	time.Sleep(500 * time.Millisecond)
	verifyVals, err := readHoldingRegs(client2, sched.powerReg, sched.periods[2][1]-sched.powerReg+1, wait)

	if jsonFlag {
		type writeResult struct {
			Register uint16 `json:"register"`
			Name     string `json:"name"`
			OldRaw   uint16 `json:"old_raw"`
			NewRaw   uint16 `json:"new_raw"`
			Written  bool   `json:"written"`
			Verified bool   `json:"verified"`
		}
		var results []writeResult
		for _, w := range writes {
			wr := writeResult{
				Register: w.reg,
				Name:     lux.RegisterName("holding", w.reg),
				OldRaw:   currentVals[w.reg],
				NewRaw:   w.val,
				Written:  true,
			}
			if err == nil {
				wr.Verified = verifyVals[w.reg] == w.val
			}
			results = append(results, wr)
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(map[string]any{
			"schedule": typeName,
			"writes":   results,
		})
	}

	if err != nil {
		fmt.Println("Verify: could not read back registers")
		return nil
	}

	allOk := true
	for _, w := range writes {
		if verifyVals[w.reg] != w.val {
			fmt.Printf("WARNING: h%d expected %d, got %d\n", w.reg, w.val, verifyVals[w.reg])
			allOk = false
		}
	}
	if allOk {
		fmt.Println("Verified.")
	}

	return nil
}

func showSchedules(cmd *cobra.Command) error {
	wait, _ := cmd.Flags().GetInt("wait")

	client, err := newClient(cmd)
	if err != nil {
		return err
	}
	defer client.Close()

	// Read all schedule registers (66-89)
	vals, err := readHoldingRegs(client, 66, 24, wait)
	if err != nil {
		return fmt.Errorf("read schedules: %w", err)
	}

	for _, typeName := range []string{"ac-charge", "charge-priority", "forced-discharge"} {
		sched := scheduleTypes[typeName]
		fmt.Printf("%s\n", sched.name)
		fmt.Printf("  Power: %d%%   SOC Limit: %d%%\n", vals[sched.powerReg], vals[sched.socReg])
		for i, p := range sched.periods {
			startRaw := vals[p[0]]
			endRaw := vals[p[1]]
			sh, sm := decodeTime(startRaw)
			eh, em := decodeTime(endRaw)
			active := !(sh == 0 && sm == 0 && eh == 0 && em == 0)
			status := ""
			if !active {
				status = " (disabled)"
			}
			fmt.Printf("  Period %d: %02d:%02d - %02d:%02d%s\n", i+1, sh, sm, eh, em, status)
		}
		fmt.Println()
	}
	return nil
}

// parseTimeRange parses "HH:MM-HH:MM" into [hour,min],[hour,min].
func parseTimeRange(s string) ([2]int, [2]int, error) {
	parts := strings.SplitN(s, "-", 2)
	if len(parts) != 2 {
		return [2]int{}, [2]int{}, fmt.Errorf("time range must be HH:MM-HH:MM, got %q", s)
	}
	start, err := parseHHMM(parts[0])
	if err != nil {
		return [2]int{}, [2]int{}, fmt.Errorf("start time: %w", err)
	}
	end, err := parseHHMM(parts[1])
	if err != nil {
		return [2]int{}, [2]int{}, fmt.Errorf("end time: %w", err)
	}
	return start, end, nil
}

func parseHHMM(s string) ([2]int, error) {
	parts := strings.SplitN(s, ":", 2)
	if len(parts) != 2 {
		return [2]int{}, fmt.Errorf("expected HH:MM, got %q", s)
	}
	h, err := strconv.Atoi(parts[0])
	if err != nil || h < 0 || h > 23 {
		return [2]int{}, fmt.Errorf("invalid hour %q", parts[0])
	}
	m, err := strconv.Atoi(parts[1])
	if err != nil || m < 0 || m > 59 {
		return [2]int{}, fmt.Errorf("invalid minute %q", parts[1])
	}
	return [2]int{h, m}, nil
}

func encodeTime(hour, min int) uint16 {
	return uint16((min << 8) | hour)
}

func decodeTime(raw uint16) (int, int) {
	return int(raw & 0xFF), int(raw >> 8)
}

// readHoldingRegs reads a contiguous range of holding registers.
func readHoldingRegs(client *lux.Client, startReg, count uint16, waitSec int) (map[uint16]uint16, error) {
	endReg := startReg + count - 1

	timeout := time.After(time.Duration(waitSec) * time.Second)
	go func() {
		<-timeout
		client.Close()
	}()

	done := make(chan error, 1)
	go func() {
		done <- client.Listen(func(pkt *lux.Packet) {
			allPresent := true
			for r := startReg; r <= endReg; r++ {
				if _, ok := client.GetHolding(r); !ok {
					allPresent = false
					break
				}
			}
			if allPresent {
				client.Close()
			}
		})
	}()

	client.ReadHold(startReg, count)
	<-done

	result := make(map[uint16]uint16)
	for r := startReg; r <= endReg; r++ {
		if v, ok := client.GetHolding(r); ok {
			result[r] = v
		}
	}
	return result, nil
}
