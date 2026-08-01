package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/spf13/cobra"
)

var readScheduleCmd = &cobra.Command{
	Use:   "read-schedule [type] [period]",
	Short: "Read charge/discharge schedule configuration",
	Long: `Read AC charge, charge priority, or forced discharge schedule settings.

With no arguments, displays all schedule types.
With a type, displays that schedule's settings.
With a type and period, displays just that time window.

Each active period includes a set-schedule command to reproduce the config.

Examples:
  lux read-schedule
  lux read-schedule ac-charge
  lux read-schedule ac-charge 1
  lux read-schedule forced-discharge 2`,
	Args: cobra.MaximumNArgs(2),
	RunE: runReadSchedule,
}

func init() {
	readScheduleCmd.Flags().Int("wait", 5, "max seconds to wait for data")
	readScheduleCmd.Flags().Bool("json", false, "output as JSON")
	rootCmd.AddCommand(readScheduleCmd)
}

// scheduleTypeOrder is the display order for schedule types.
var scheduleTypeOrder = []string{"ac-charge", "charge-priority", "forced-discharge"}

func runReadSchedule(cmd *cobra.Command, args []string) error {
	wait, _ := cmd.Flags().GetInt("wait")
	jsonFlag, _ := cmd.Flags().GetBool("json")

	// Determine which types to display
	var typeKeys []string
	filterPeriod := 0 // 0 = all periods

	if len(args) >= 1 {
		typeName := strings.ToLower(args[0])
		if _, ok := scheduleTypes[typeName]; !ok {
			return fmt.Errorf("unknown schedule type %q (use: ac-charge, charge-priority, forced-discharge)", args[0])
		}
		typeKeys = []string{typeName}

		if len(args) >= 2 {
			p, err := strconv.Atoi(args[1])
			if err != nil || p < 1 || p > 3 {
				return fmt.Errorf("period must be 1, 2, or 3")
			}
			filterPeriod = p
		}
	} else {
		typeKeys = scheduleTypeOrder
	}

	client, err := newClient(cmd)
	if err != nil {
		return err
	}
	defer client.Close()

	vals, err := readHoldingRegs(client, 66, 24, wait)
	if err != nil {
		return fmt.Errorf("read schedules: %w", err)
	}

	type periodJSON struct {
		Period  int    `json:"period"`
		Start   string `json:"start"`
		End     string `json:"end"`
		Enabled bool   `json:"enabled"`
	}
	type schedJSON struct {
		Type    string       `json:"type"`
		Name    string       `json:"name"`
		Power   uint16       `json:"power"`
		SOC     uint16       `json:"soc"`
		Periods []periodJSON `json:"periods"`
	}

	var jsonResults []schedJSON

	for i, key := range typeKeys {
		sched := scheduleTypes[key]
		power := vals[sched.powerReg]
		soc := vals[sched.socReg]

		startP, endP := 0, 3
		if filterPeriod > 0 {
			startP = filterPeriod - 1
			endP = filterPeriod
		}

		var periods []periodJSON
		for p := startP; p < endP; p++ {
			sh, sm := decodeTime(vals[sched.periods[p][0]])
			eh, em := decodeTime(vals[sched.periods[p][1]])
			disabled := sh == 0 && sm == 0 && eh == 0 && em == 0
			periods = append(periods, periodJSON{
				Period:  p + 1,
				Start:   fmt.Sprintf("%02d:%02d", sh, sm),
				End:     fmt.Sprintf("%02d:%02d", eh, em),
				Enabled: !disabled,
			})
		}

		if jsonFlag {
			jsonResults = append(jsonResults, schedJSON{
				Type:    key,
				Name:    sched.name,
				Power:   power,
				SOC:     soc,
				Periods: periods,
			})
			continue
		}

		fmt.Printf("%s\n", sched.name)
		fmt.Printf("  Power: %d%%   SOC Limit: %d%%\n", power, soc)
		for _, pd := range periods {
			if !pd.Enabled {
				fmt.Printf("  Period %d: 00:00 - 00:00 (disabled)\n", pd.Period)
			} else {
				fmt.Printf("  Period %d: %s - %s\n", pd.Period, pd.Start, pd.End)
				fmt.Printf("           ^ set-schedule %s %d %s-%s --power %d --soc %d\n",
					key, pd.Period, pd.Start, pd.End, power, soc)
			}
		}
		if i < len(typeKeys)-1 {
			fmt.Println()
		}
	}

	if jsonFlag {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(jsonResults)
	}

	return nil
}
