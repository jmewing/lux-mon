package cmd

import (
	"fmt"
	"sort"
	"strconv"
	"strings"

	"github.com/jefflaplante/lux/internal/lux"
)

type regTarget struct {
	regType string // "holding" or "input"
	regNum  uint16
}

// parseRegArg parses a register argument into one or more targets.
// Accepts: bare number (holding default), h<num>, i<num>, or name search.
func parseRegArg(arg string) ([]regTarget, error) {
	// Try h<num> or i<num> prefix
	if len(arg) > 1 {
		prefix := strings.ToLower(arg[:1])
		rest := arg[1:]
		if prefix == "h" || prefix == "i" {
			if num, err := strconv.ParseUint(rest, 10, 16); err == nil {
				regType := "holding"
				if prefix == "i" {
					regType = "input"
				}
				return []regTarget{{regType, uint16(num)}}, nil
			}
		}
	}

	// Try bare number (defaults to holding)
	if num, err := strconv.ParseUint(arg, 10, 16); err == nil {
		return []regTarget{{"holding", uint16(num)}}, nil
	}

	// Try name lookup
	matches := lux.FindRegisterByName(arg)
	if len(matches) == 0 {
		return nil, fmt.Errorf("no register matching %q", arg)
	}
	targets := make([]regTarget, len(matches))
	for i, m := range matches {
		targets[i] = regTarget{m.Type, m.Number}
	}
	return targets, nil
}

// parseRegArgs parses multiple arguments, splitting on commas.
func parseRegArgs(args []string) ([]regTarget, error) {
	var targets []regTarget
	for _, arg := range args {
		for _, part := range strings.Split(arg, ",") {
			part = strings.TrimSpace(part)
			if part == "" {
				continue
			}
			t, err := parseRegArg(part)
			if err != nil {
				return nil, err
			}
			targets = append(targets, t...)
		}
	}
	return targets, nil
}

type regBatch struct {
	regType  string
	min, max uint16
}

// batchTargets groups targets by type, then merges overlapping/adjacent register
// ranges into single read requests. E.g. i28,i29,i30 becomes one ReadInput(28,3).
func batchTargets(targets []regTarget) []regBatch {
	byType := map[string][]uint16{}
	for _, t := range targets {
		byType[t.regType] = append(byType[t.regType], t.regNum)
	}

	var batches []regBatch
	for regType, nums := range byType {
		sort.Slice(nums, func(i, j int) bool { return nums[i] < nums[j] })
		bMin, bMax := nums[0], nums[0]
		for _, n := range nums[1:] {
			if n <= bMax+4 {
				bMax = n
			} else {
				batches = append(batches, regBatch{regType, bMin, bMax})
				bMin, bMax = n, n
			}
		}
		batches = append(batches, regBatch{regType, bMin, bMax})
	}
	return batches
}

func allTargetsSatisfied(client *lux.Client, targets []regTarget) bool {
	for _, t := range targets {
		if t.regType == "holding" {
			if _, ok := client.GetHolding(t.regNum); !ok {
				return false
			}
		} else {
			if _, ok := client.GetInput(t.regNum); !ok {
				return false
			}
		}
	}
	return true
}

// formatRegValue returns the formatted value for a register, without the name prefix.
func formatRegValue(regType string, regNum, raw uint16) string {
	value := lux.FormatRegister(regType, regNum, raw)
	if idx := strings.Index(value, ": "); idx >= 0 {
		return value[idx+2:]
	}
	return value
}
