package cmd

import (
	"fmt"
	"os"

	"github.com/jefflaplante/lux/internal/lux"
	"github.com/spf13/cobra"
	"github.com/spf13/viper"
)

const (
	defaultPort    = 8000
	defaultDatalog = "AAAAAAAAAA"
	defaultSerial  = "1234567890"
)

var rootCmd = &cobra.Command{
	Use:   "lux",
	Short: "EG4 inverter CLI via LuxPower TCP protocol",
	Long: `lux communicates with EG4 inverters via the LuxPower TCP protocol.

It connects to the WiFi dongle's TCP port to read sensor data and
write configuration registers — battery schedules, charge/discharge
modes, SOC limits — without depending on SolarAssistant or the cloud.`,
}

func Execute() {
	if err := rootCmd.Execute(); err != nil {
		os.Exit(1)
	}
}

func init() {
	rootCmd.PersistentFlags().String("host", "", "dongle IP address (env: LUX_HOST) [required]")
	rootCmd.PersistentFlags().Int("port", defaultPort, "dongle TCP port (env: LUX_PORT)")

	// Bind env vars with LUX_ prefix
	viper.SetEnvPrefix("LUX")
	viper.AutomaticEnv()
	viper.BindPFlag("host", rootCmd.PersistentFlags().Lookup("host"))
	viper.BindPFlag("port", rootCmd.PersistentFlags().Lookup("port"))

	rootCmd.SilenceUsage = true
	rootCmd.SilenceErrors = true
}

func newClient(cmd *cobra.Command) (*lux.Client, error) {
	host := viper.GetString("host")
	if host == "" {
		return nil, fmt.Errorf("--host or LUX_HOST is required")
	}
	port := viper.GetInt("port")
	client := lux.NewClient(host, port, defaultDatalog, defaultSerial)
	if err := client.Connect(); err != nil {
		return nil, fmt.Errorf("connect to %s:%d: %w", host, port, err)
	}
	return client, nil
}
