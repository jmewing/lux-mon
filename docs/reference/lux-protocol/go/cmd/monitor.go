package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/jefflaplante/lux/internal/lux"
	"github.com/spf13/cobra"
)

var monitorCmd = &cobra.Command{
	Use:   "monitor",
	Short: "Connect and display live register data",
	Long:  `Connects to the inverter and streams decoded register updates in real time.`,
	RunE:  runMonitor,
}

func init() {
	monitorCmd.Flags().Int("duration", 30, "monitor duration in seconds (0 = unlimited)")
	monitorCmd.Flags().Bool("json", false, "output as JSONL (one JSON object per line)")
	rootCmd.AddCommand(monitorCmd)
}

func runMonitor(cmd *cobra.Command, args []string) error {
	client, err := newClient(cmd)
	if err != nil {
		return err
	}
	defer client.Close()

	host, _ := cmd.Flags().GetString("host")
	port, _ := cmd.Flags().GetInt("port")
	duration, _ := cmd.Flags().GetInt("duration")
	jsonFlag, _ := cmd.Flags().GetBool("json")

	if !jsonFlag {
		fmt.Printf("Connected to %s:%d. Monitoring for %ds...\n\n", host, port, duration)
	}

	jsonEnc := json.NewEncoder(os.Stdout)

	if duration > 0 {
		done := time.After(time.Duration(duration) * time.Second)
		go func() {
			<-done
			client.Close()
		}()
	}

	listenErr := client.Listen(func(pkt *lux.Packet) {
		ts := time.Now().Format("15:04:05.000")

		if pkt.TCPFunction == lux.FuncHeartbeat {
			if jsonFlag {
				jsonEnc.Encode(map[string]any{
					"timestamp": ts, "type": "heartbeat", "datalog": pkt.Datalog,
				})
			} else {
				fmt.Printf("[%s] Heartbeat from %s\n", ts, pkt.Datalog)
			}
			return
		}
		if pkt.RegisterType == "" {
			return
		}

		if jsonFlag {
			regs := make(map[string]any)
			for reg := pkt.StartRegister; reg < pkt.StartRegister+pkt.RegisterCount; reg++ {
				val, ok := pkt.Registers[reg]
				if !ok {
					continue
				}
				entry := map[string]any{
					"raw":   val,
					"value": lux.FormatRegister(pkt.RegisterType, reg, val),
				}
				name := lux.RegisterName(pkt.RegisterType, reg)
				if name != "" {
					entry["name"] = name
				}
				regs[fmt.Sprintf("%d", reg)] = entry
			}
			jsonEnc.Encode(map[string]any{
				"timestamp":     ts,
				"type":          "registers",
				"register_type": pkt.RegisterType,
				"start":         pkt.StartRegister,
				"count":         pkt.RegisterCount,
				"crc_valid":     pkt.CRCValid,
				"registers":     regs,
			})
			return
		}

		fmt.Printf("[%s] %s regs %d-%d (%d regs) CRC:%v\n",
			ts, pkt.RegisterType, pkt.StartRegister,
			pkt.StartRegister+pkt.RegisterCount-1,
			pkt.RegisterCount, pkt.CRCValid)

		for reg := pkt.StartRegister; reg < pkt.StartRegister+pkt.RegisterCount; reg++ {
			val, ok := pkt.Registers[reg]
			if !ok {
				continue
			}
			formatted := lux.FormatRegister(pkt.RegisterType, reg, val)

			warning := lux.ValidateValue(pkt.RegisterType, reg, val)
			if warning != "" {
				fmt.Printf("  [%3d] %s  ⚠️  %s\n", reg, formatted, warning)
			} else {
				fmt.Printf("  [%3d] %s\n", reg, formatted)
			}
		}

		if pkt.RegisterType == "input" {
			energyPairs := [][3]interface{}{
				{40, 41, "PV1 Energy Total"},
				{42, 43, "PV2 Energy Total"},
				{44, 45, "PV3 Energy Total"},
				{46, 47, "Inverter Energy Total"},
				{50, 51, "Battery Charge Total"},
				{52, 53, "Battery Discharge Total"},
				{56, 57, "Grid Export Total"},
				{58, 59, "Grid Import Total"},
			}
			printed := false
			for _, pair := range energyPairs {
				loReg, hiReg := uint16(pair[0].(int)), uint16(pair[1].(int))
				name := pair[2].(string)
				lo, loOk := pkt.Registers[loReg]
				hi, hiOk := pkt.Registers[hiReg]
				if loOk && hiOk {
					if !printed {
						fmt.Println("  --- Combined Energy Totals ---")
						printed = true
					}
					fmt.Printf("  %s: %s\n", name, lux.FormatEnergy32(lo, hi))
				}
			}
		}
		fmt.Println()
	})

	if listenErr != nil && !jsonFlag {
		fmt.Printf("\nDone.\n")
	}
	return nil
}
