package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/jefflaplante/lux/internal/lux"
	"github.com/spf13/cobra"
)

const slots = 48 // 30-min resolution across 24 hours

// ANSI color codes
const (
	colorReset   = "\033[0m"
	colorYellow  = "\033[33m" // AC Charge
	colorRed     = "\033[31m" // Charge Priority (orange-ish)
	colorGreen   = "\033[32m" // Forced Discharge
	colorCyan    = "\033[36m" // Self Consumption
	colorDim     = "\033[2m"
	colorBold    = "\033[1m"
)

type modeInfo struct {
	key      string // scheduleTypes key
	label    string // display label (13 chars padded)
	letter   byte   // A, C, F
	color    string
	enableBit int   // bit in register 21
}

var modes = []modeInfo{
	{"ac-charge", "AC Charge   ", 'A', colorYellow, 7},
	{"charge-priority", "Chg Priority", 'C', colorRed, 11},
	{"forced-discharge", "Forced Disch", 'F', colorGreen, 10},
}

type modeData struct {
	info        modeInfo
	enabled     bool
	slots       [slots]bool    // composite of all periods
	periods     [3][slots]bool
	periodTimes [3][2][2]int // [period][start/end][hour/min]
	power       uint16
	soc         uint16
}

var schedulesCmd = &cobra.Command{
	Use:   "schedules",
	Short: "Display schedule time windows as a bar chart",
	Long:  `Shows a visual timeline of AC Charge, Charge Priority, and Forced Discharge schedules with a composite Active Mode row.`,
	RunE:  runSchedules,
}

func init() {
	schedulesCmd.Flags().Bool("no-color", false, "disable ANSI colors")
	schedulesCmd.Flags().Int("wait", 5, "max seconds to wait for data")
	schedulesCmd.Flags().Bool("json", false, "output as JSON")
	rootCmd.AddCommand(schedulesCmd)
}

func runSchedules(cmd *cobra.Command, args []string) error {
	noColor, _ := cmd.Flags().GetBool("no-color")
	wait, _ := cmd.Flags().GetInt("wait")
	jsonFlag, _ := cmd.Flags().GetBool("json")

	if !noColor && isNoColor() {
		noColor = true
	}

	client, err := newClient(cmd)
	if err != nil {
		return err
	}
	defer client.Close()

	// Read register 21 + schedule registers 66-89 on a single connection
	neededRegs := map[uint16]bool{21: true}
	for r := uint16(66); r <= 89; r++ {
		neededRegs[r] = true
	}

	timeout := time.After(time.Duration(wait) * time.Second)
	go func() {
		<-timeout
		client.Close()
	}()

	done := make(chan error, 1)
	go func() {
		done <- client.Listen(func(pkt *lux.Packet) {
			for r := range neededRegs {
				if _, ok := client.GetHolding(r); !ok {
					return
				}
			}
			client.Close()
		})
	}()

	// Send single read request covering regs 21-89 (dongle drops back-to-back requests)
	client.ReadHold(21, 69)

	<-done

	vals := make(map[uint16]uint16)
	for r := range neededRegs {
		if v, ok := client.GetHolding(r); ok {
			vals[r] = v
		}
	}

	masterFlags := vals[21]

	var modeResults []modeData
	for _, m := range modes {
		sched := scheduleTypes[m.key]
		md := modeData{
			info:    m,
			enabled: masterFlags&(1<<m.enableBit) != 0,
			power:   vals[sched.powerReg],
			soc:     vals[sched.socReg],
		}

		for i, p := range sched.periods {
			sh, sm := decodeTime(vals[p[0]])
			eh, em := decodeTime(vals[p[1]])
			md.periodTimes[i] = [2][2]int{{sh, sm}, {eh, em}}

			if sh == 0 && sm == 0 && eh == 0 && em == 0 {
				continue // disabled
			}
			fillSlots(&md.periods[i], sh, sm, eh, em)
			// OR into composite
			for s := 0; s < slots; s++ {
				if md.periods[i][s] {
					md.slots[s] = true
				}
			}
		}
		modeResults = append(modeResults, md)
	}

	// Build composite Active row (priority: F > C > A, else S)
	var activeSlots [slots]byte
	for s := 0; s < slots; s++ {
		activeSlots[s] = 'S' // Self Consumption default
		// Check in priority order (lowest priority first, highest overwrites)
		for _, md := range modeResults {
			if md.enabled && md.slots[s] {
				activeSlots[s] = md.info.letter
			}
		}
	}

	if jsonFlag {
		return printSchedulesJSON(modeResults, activeSlots, masterFlags)
	}

	// Render
	printTimeline(noColor)
	fmt.Println()

	// Active row
	fmt.Printf("%s%-13s%s ", colorBold, "Active", c(noColor, colorReset))
	for s := 0; s < slots; s++ {
		ch := activeSlots[s]
		switch ch {
		case 'A':
			fmt.Print(c(noColor, colorYellow) + "A" + c(noColor, colorReset))
		case 'C':
			fmt.Print(c(noColor, colorRed) + "C" + c(noColor, colorReset))
		case 'F':
			fmt.Print(c(noColor, colorGreen) + "F" + c(noColor, colorReset))
		default:
			fmt.Print(c(noColor, colorCyan) + "S" + c(noColor, colorReset))
		}
	}
	fmt.Println()
	printTimeline(noColor)
	fmt.Println()

	// Per-mode rows
	for _, md := range modeResults {
		enabledStr := c(noColor, colorDim) + "(off)" + c(noColor, colorReset)
		if md.enabled {
			enabledStr = c(noColor, colorGreen) + "(on)" + c(noColor, colorReset)
		}

		// Composite row for this mode
		fmt.Printf("%s%-13s%s", c(noColor, md.info.color), string(md.info.label), c(noColor, colorReset))
		printBar(md.slots, md.info.letter, md.info.color, noColor)
		fmt.Printf("  %s  %d%% / soc %d%%\n", enabledStr, md.power, md.soc)

		// Period detail rows (only if active)
		for i := 0; i < 3; i++ {
			pt := md.periodTimes[i]
			if pt[0][0] == 0 && pt[0][1] == 0 && pt[1][0] == 0 && pt[1][1] == 0 {
				continue
			}
			fmt.Printf("  Period %d    ", i+1)
			printBar(md.periods[i], md.info.letter, md.info.color, noColor)
			fmt.Printf("  %02d:%02d-%02d:%02d\n", pt[0][0], pt[0][1], pt[1][0], pt[1][1])
		}
	}

	printTimeline(noColor)

	// Legend
	fmt.Printf("\n%sA%s=AC Charge  %sC%s=Chg Priority  %sF%s=Forced Disch  %sS%s=Self Consumption  %s·%s=inactive\n",
		c(noColor, colorYellow), c(noColor, colorReset),
		c(noColor, colorRed), c(noColor, colorReset),
		c(noColor, colorGreen), c(noColor, colorReset),
		c(noColor, colorCyan), c(noColor, colorReset),
		c(noColor, colorDim), c(noColor, colorReset),
	)

	return nil
}

func printTimeline(noColor bool) {
	fmt.Printf("             ")
	for h := 0; h <= 24; h += 3 {
		if h < 24 {
			fmt.Printf("%-6s", fmt.Sprintf("%02d", h))
		} else {
			fmt.Printf("%02d", h)
		}
	}
	fmt.Println()
}

func printBar(slotArr [slots]bool, letter byte, color string, noColor bool) {
	for s := 0; s < slots; s++ {
		if slotArr[s] {
			fmt.Print(c(noColor, color) + string(letter) + c(noColor, colorReset))
		} else {
			fmt.Print(c(noColor, colorDim) + "·" + c(noColor, colorReset))
		}
	}
}

// fillSlots marks 30-min slots as active for a time window.
// Handles wrap-around (e.g. 22:00-06:00).
func fillSlots(arr *[slots]bool, sh, sm, eh, em int) {
	startSlot := sh*2 + sm/30
	endSlot := eh*2 + em/30
	if startSlot >= slots {
		startSlot = slots - 1
	}
	if endSlot > slots {
		endSlot = slots
	}

	if endSlot <= startSlot {
		// Wraps around midnight
		for s := startSlot; s < slots; s++ {
			arr[s] = true
		}
		for s := 0; s < endSlot; s++ {
			arr[s] = true
		}
	} else {
		for s := startSlot; s < endSlot; s++ {
			arr[s] = true
		}
	}
}

// c returns the color code if colors enabled, empty string otherwise.
func c(noColor bool, code string) string {
	if noColor {
		return ""
	}
	return code
}

func isNoColor() bool {
	return os.Getenv("NO_COLOR") != ""
}

func printSchedulesJSON(modeResults []modeData, activeSlots [slots]byte, masterFlags uint16) error {
	type periodOut struct {
		Period  int    `json:"period"`
		Start   string `json:"start"`
		End     string `json:"end"`
		Enabled bool   `json:"enabled"`
	}
	type schedOut struct {
		Type    string      `json:"type"`
		Name    string      `json:"name"`
		Enabled bool        `json:"enabled"`
		Power   uint16      `json:"power"`
		SOC     uint16      `json:"soc"`
		Periods []periodOut `json:"periods"`
	}

	var scheds []schedOut
	for _, md := range modeResults {
		var periods []periodOut
		for i := 0; i < 3; i++ {
			pt := md.periodTimes[i]
			disabled := pt[0][0] == 0 && pt[0][1] == 0 && pt[1][0] == 0 && pt[1][1] == 0
			periods = append(periods, periodOut{
				Period:  i + 1,
				Start:   fmt.Sprintf("%02d:%02d", pt[0][0], pt[0][1]),
				End:     fmt.Sprintf("%02d:%02d", pt[1][0], pt[1][1]),
				Enabled: !disabled,
			})
		}
		scheds = append(scheds, schedOut{
			Type:    md.info.key,
			Name:    string(md.info.label),
			Enabled: md.enabled,
			Power:   md.power,
			SOC:     md.soc,
			Periods: periods,
		})
	}

	slotStrs := make([]string, slots)
	for i := 0; i < slots; i++ {
		slotStrs[i] = string(activeSlots[i])
	}

	result := map[string]any{
		"master_flags": masterFlags,
		"schedules":    scheds,
		"active_slots": slotStrs,
	}

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(result)
}
