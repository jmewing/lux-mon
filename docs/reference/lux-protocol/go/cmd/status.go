package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/jefflaplante/lux/internal/lux"
	"github.com/spf13/cobra"
)

// Registers needed for status display
var statusInputRegs = []uint16{
	0,                // Status
	1, 2, 3,          // PV voltages
	4,                // Battery voltage
	5,                // SOC/SOH
	7, 8, 9,          // PV power
	10, 11,           // Battery charge/discharge power
	16,               // Inverter power
	26, 27,           // Grid to/from
	28, 29, 30,       // PV energy today
	64, 65, 66,       // Temperatures
}

var statusHoldingRegs = []uint16{
	21, // Master Function Flags
}

var statusCmd = &cobra.Command{
	Use:   "status",
	Short: "Display system status snapshot",
	Long:  `Connects to the inverter and displays a summary of current operating state, battery SOC, power flows, and active mode.`,
	RunE:  runStatus,
}

func init() {
	statusCmd.Flags().Int("wait", 5, "max seconds to wait for data")
	statusCmd.Flags().Bool("json", false, "output as JSON")
	rootCmd.AddCommand(statusCmd)
}

func runStatus(cmd *cobra.Command, args []string) error {
	wait, _ := cmd.Flags().GetInt("wait")
	jsonFlag, _ := cmd.Flags().GetBool("json")

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
			if statusRegsSatisfied(client) {
				client.Close()
			}
		})
	}()

	// Active requests: input 0-30, input 64-66, holding 21
	client.ReadInput(0, 31)
	client.ReadInput(64, 3)
	client.ReadHold(21, 1)

	<-done

	if jsonFlag {
		return printStatusJSON(client)
	}
	printStatusText(client)
	return nil
}

func statusRegsSatisfied(client *lux.Client) bool {
	for _, r := range statusInputRegs {
		if _, ok := client.GetInput(r); !ok {
			return false
		}
	}
	for _, r := range statusHoldingRegs {
		if _, ok := client.GetHolding(r); !ok {
			return false
		}
	}
	return true
}

func getInput(client *lux.Client, reg uint16) uint16 {
	v, _ := client.GetInput(reg)
	return v
}

func getHolding(client *lux.Client, reg uint16) uint16 {
	v, _ := client.GetHolding(reg)
	return v
}

func fmtInput(client *lux.Client, reg uint16) string {
	v, ok := client.GetInput(reg)
	if !ok {
		return "?"
	}
	def, ok := lux.GetRegisterDef("input", reg)
	if !ok {
		return fmt.Sprintf("%d", v)
	}
	return def.FormatValueOnly(v)
}

func printStatusText(client *lux.Client) {
	// Status & modes
	statusRaw := getInput(client, 0)
	statusName := "Unknown"
	if name, ok := lux.StatusCodes[statusRaw]; ok {
		statusName = name
	}
	modesRaw := getHolding(client, 21)
	modesDef, _ := lux.GetRegisterDef("holding", 21)

	fmt.Printf("Status:  %s\n", statusName)
	fmt.Printf("Modes:   %s\n", modesDef.FormatValueOnly(modesRaw))
	fmt.Println()

	// PV
	fmt.Println("PV")
	for _, pv := range []struct{ name string; vReg, pReg, eReg uint16 }{
		{"PV1", 1, 7, 28},
		{"PV2", 2, 8, 29},
		{"PV3", 3, 9, 30},
	} {
		v, _ := client.GetInput(pv.vReg)
		p, _ := client.GetInput(pv.pReg)
		e, _ := client.GetInput(pv.eReg)
		fmt.Printf("  %s: %5.1fV  %5dW  today %5.1fkWh\n",
			pv.name, float64(v)/10, p, float64(e)/10)
	}
	fmt.Println()

	// Battery
	fmt.Println("Battery")
	bv, _ := client.GetInput(4)
	socRaw := getInput(client, 5)
	soc := socRaw & 0xFF
	soh := (socRaw >> 8) & 0xFF
	chg, _ := client.GetInput(10)
	dischg, _ := client.GetInput(11)
	fmt.Printf("  Voltage: %.1fV   SOC: %d%%   SOH: %d%%\n", float64(bv)/10, soc, soh)
	fmt.Printf("  Charging: %dW   Discharging: %dW\n", chg, dischg)
	fmt.Println()

	// Grid
	fmt.Println("Grid")
	toGrid, _ := client.GetInput(26)
	fromGrid, _ := client.GetInput(27)
	fmt.Printf("  To Grid: %dW   From Grid: %dW\n", toGrid, fromGrid)
	fmt.Println()

	// Inverter
	fmt.Println("Inverter")
	invPower, _ := client.GetInput(16)
	fmt.Printf("  Output: %dW\n", invPower)
	fmt.Println()

	// Temperature
	fmt.Println("Temperature")
	t1, _ := client.GetInput(64)
	t2, _ := client.GetInput(65)
	t3, _ := client.GetInput(66)
	fmt.Printf("  Internal: %d°C   Radiator1: %d°C   Radiator2: %d°C\n", t1, t2, t3)
}

func printStatusJSON(client *lux.Client) error {
	statusRaw := getInput(client, 0)
	statusName := "Unknown"
	if name, ok := lux.StatusCodes[statusRaw]; ok {
		statusName = name
	}
	modesRaw := getHolding(client, 21)
	modesDef, _ := lux.GetRegisterDef("holding", 21)

	socRaw := getInput(client, 5)

	result := map[string]any{
		"status": statusName,
		"modes":  modesDef.FormatValueOnly(modesRaw),
		"pv": map[string]any{
			"pv1": map[string]any{"voltage": float64(getInput(client, 1)) / 10, "power": getInput(client, 7), "today_kwh": float64(getInput(client, 28)) / 10},
			"pv2": map[string]any{"voltage": float64(getInput(client, 2)) / 10, "power": getInput(client, 8), "today_kwh": float64(getInput(client, 29)) / 10},
			"pv3": map[string]any{"voltage": float64(getInput(client, 3)) / 10, "power": getInput(client, 9), "today_kwh": float64(getInput(client, 30)) / 10},
		},
		"battery": map[string]any{
			"voltage":         float64(getInput(client, 4)) / 10,
			"soc":             socRaw & 0xFF,
			"soh":             (socRaw >> 8) & 0xFF,
			"charge_power":    getInput(client, 10),
			"discharge_power": getInput(client, 11),
		},
		"grid": map[string]any{
			"to_grid":   getInput(client, 26),
			"from_grid": getInput(client, 27),
		},
		"inverter": map[string]any{
			"power": getInput(client, 16),
		},
		"temperature": map[string]any{
			"internal":  getInput(client, 64),
			"radiator1": getInput(client, 65),
			"radiator2": getInput(client, 66),
		},
	}

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(result)
}
