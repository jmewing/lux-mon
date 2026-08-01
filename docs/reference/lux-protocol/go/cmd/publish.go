package cmd

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	mqtt "github.com/eclipse/paho.mqtt.golang"
	"github.com/jefflaplante/lux/internal/lux"
	"github.com/spf13/cobra"
	"github.com/spf13/viper"
)

// pubLogger handles text or JSON logging with quiet mode support
type pubLogger struct {
	json  bool
	quiet bool
	w     io.Writer
}

func (l *pubLogger) info(msg string, fields map[string]any) {
	if l.quiet {
		return
	}
	if l.json {
		out := map[string]any{"ts": time.Now().UTC().Format(time.RFC3339), "level": "info", "msg": msg}
		for k, v := range fields {
			out[k] = v
		}
		json.NewEncoder(l.w).Encode(out)
	} else {
		if len(fields) > 0 {
			fmt.Fprintf(l.w, "[%s] %s %v\n", time.Now().Format("15:04:05"), msg, fields)
		} else {
			fmt.Fprintf(l.w, "[%s] %s\n", time.Now().Format("15:04:05"), msg)
		}
	}
}

func (l *pubLogger) status(msg string) {
	// Status messages always print (startup, shutdown) regardless of quiet
	if l.json {
		json.NewEncoder(l.w).Encode(map[string]any{
			"ts": time.Now().UTC().Format(time.RFC3339), "level": "info", "msg": msg,
		})
	} else {
		fmt.Fprintf(l.w, "%s\n", msg)
	}
}

func (l *pubLogger) errorf(msg string, err error) {
	// Errors always print regardless of quiet
	if l.json {
		json.NewEncoder(l.w).Encode(map[string]any{
			"ts": time.Now().UTC().Format(time.RFC3339), "level": "error", "msg": msg, "error": err.Error(),
		})
	} else {
		fmt.Fprintf(l.w, "[%s] error: %s: %v\n", time.Now().Format("15:04:05"), msg, err)
	}
}

var publishCmd = &cobra.Command{
	Use:   "publish",
	Short: "Stream inverter data to MQTT (Solar Assistant compatible)",
	Long: `Connects to the inverter and publishes register data to an MQTT broker
using Solar Assistant-compatible topic names. This enables Home Assistant
integration and remote monitoring.

Topics are published under {prefix}/inverter_1/{metric}/state with
human-readable formatted values matching Solar Assistant output.

Health check topics:
  {prefix}/inverter_1/online/state    - "true" when connected, "false" on disconnect (LWT)
  {prefix}/inverter_1/last_seen/state - RFC3339 timestamp of last successful publish

All flags can be set via environment variables with LUX_ prefix.`,
	RunE: runPublish,
}

func init() {
	publishCmd.Flags().String("broker", "", "MQTT broker URL (env: LUX_BROKER) [required]")
	publishCmd.Flags().String("prefix", "solar_assistant", "MQTT topic prefix (env: LUX_PREFIX)")
	publishCmd.Flags().String("username", "", "MQTT username (env: LUX_MQTT_USER)")
	publishCmd.Flags().String("password", "", "MQTT password (env: LUX_MQTT_PASS)")
	publishCmd.Flags().Bool("retain", true, "use MQTT retain flag (env: LUX_RETAIN)")
	publishCmd.Flags().Bool("quiet", false, "suppress per-packet logging (env: LUX_QUIET)")
	publishCmd.Flags().Bool("log-json", false, "output logs as JSON lines (env: LUX_LOG_JSON)")

	// Bind to viper for env var support
	viper.BindPFlag("broker", publishCmd.Flags().Lookup("broker"))
	viper.BindPFlag("prefix", publishCmd.Flags().Lookup("prefix"))
	viper.BindPFlag("mqtt_user", publishCmd.Flags().Lookup("username"))
	viper.BindPFlag("mqtt_pass", publishCmd.Flags().Lookup("password"))
	viper.BindPFlag("retain", publishCmd.Flags().Lookup("retain"))
	viper.BindPFlag("quiet", publishCmd.Flags().Lookup("quiet"))
	viper.BindPFlag("log_json", publishCmd.Flags().Lookup("log-json"))

	rootCmd.AddCommand(publishCmd)
}

func runPublish(cmd *cobra.Command, args []string) error {
	broker := viper.GetString("broker")
	if broker == "" {
		return fmt.Errorf("--broker or LUX_BROKER is required")
	}
	prefix := viper.GetString("prefix")
	username := viper.GetString("mqtt_user")
	password := viper.GetString("mqtt_pass")
	retain := viper.GetBool("retain")
	quiet := viper.GetBool("quiet")
	logJSON := viper.GetBool("log_json")

	log := &pubLogger{json: logJSON, quiet: quiet, w: os.Stderr}

	// Connect to MQTT broker (stays connected across inverter reconnects)
	onlineTopic := prefix + "/inverter_1/online/state"
	lastSeenTopic := prefix + "/inverter_1/last_seen/state"

	opts := mqtt.NewClientOptions().
		AddBroker(broker).
		SetClientID("lux-publish").
		SetAutoReconnect(true).
		SetWill(onlineTopic, "false", 1, true)

	if username != "" {
		opts.SetUsername(username)
	}
	if password != "" {
		opts.SetPassword(password)
	}

	mqttClient := mqtt.NewClient(opts)
	tok := mqttClient.Connect()
	if !tok.WaitTimeout(10 * time.Second) {
		return fmt.Errorf("MQTT connect timeout")
	}
	if tok.Error() != nil {
		return fmt.Errorf("MQTT connect: %w", tok.Error())
	}
	defer mqttClient.Disconnect(500)

	host := viper.GetString("host")
	port := viper.GetInt("port")

	log.status(fmt.Sprintf("Connected: inverter=%s:%d broker=%s prefix=%s", host, port, broker, prefix))
	log.status("Publishing (Ctrl-C to stop)...")

	// Shutdown signal handling
	shutdownCh := make(chan struct{})
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

	// Track active client so signal handler can close it
	var activeClient *lux.Client
	var activeClientMu sync.Mutex

	go func() {
		<-sigCh
		log.status("Shutting down...")
		mqttClient.Publish(onlineTopic, 1, true, "false")

		// Close active client to unblock Listen()
		activeClientMu.Lock()
		if activeClient != nil {
			activeClient.Close()
		}
		activeClientMu.Unlock()

		close(shutdownCh)

		// Restore default signal handling - second Ctrl-C will force exit
		signal.Reset(syscall.SIGINT, syscall.SIGTERM)
	}()

	var totalPublished int

	// Reconnect loop with exponential backoff
	backoff := 1 * time.Second
	maxBackoff := 60 * time.Second

	for {
		// Check shutdown before connecting
		select {
		case <-shutdownCh:
			log.status(fmt.Sprintf("Done. %d total publishes.", totalPublished))
			return nil
		default:
		}

		client, err := newClient(cmd)
		if err != nil {
			log.errorf("inverter connect failed", err)
			mqttClient.Publish(onlineTopic, 1, true, "false")
			log.status(fmt.Sprintf("Retrying in %v...", backoff))

			select {
			case <-shutdownCh:
				log.status(fmt.Sprintf("Done. %d total publishes.", totalPublished))
				return nil
			case <-time.After(backoff):
			}

			backoff = min(backoff*2, maxBackoff)
			continue
		}

		// Register client so signal handler can close it
		activeClientMu.Lock()
		activeClient = client
		activeClientMu.Unlock()

		// Connected - reset backoff
		backoff = 1 * time.Second
		mqttClient.Publish(onlineTopic, 1, true, "true")

		listenErr := client.Listen(func(pkt *lux.Packet) {
			if pkt.TCPFunction == lux.FuncHeartbeat {
				mqttClient.Publish(onlineTopic, 1, true, "true")
				return
			}
			if pkt.RegisterType == "" {
				return
			}

			// Choose the topic mapping based on register type
			var topicMap map[uint16][]lux.MQTTMapping
			switch pkt.RegisterType {
			case "input":
				topicMap = lux.InputMQTTTopics
			case "holding":
				topicMap = lux.HoldingMQTTTopics
			}

			packetPublished := 0

			// Publish individual register topics
			for reg := pkt.StartRegister; reg < pkt.StartRegister+pkt.RegisterCount; reg++ {
				raw, ok := pkt.Registers[reg]
				if !ok {
					continue
				}

				mappings, hasMappings := topicMap[reg]
				if !hasMappings {
					continue
				}

				for _, m := range mappings {
					var val string
					if m.Format != nil {
						val = m.Format(raw)
					} else {
						val = fmt.Sprintf("%d", raw)
					}
					topic := prefix + "/" + m.Topic + "/state"
					mqttClient.Publish(topic, 0, retain, val)
					packetPublished++
				}
			}

			// Publish bitmask flags from register 21
			if pkt.RegisterType == "holding" {
				if raw, ok := pkt.Registers[21]; ok {
					for bit, topic := range lux.MasterFlagTopics {
						val := "false"
						if raw&(1<<bit) != 0 {
							val = "true"
						}
						mqttClient.Publish(prefix+"/"+topic+"/state", 0, retain, val)
						packetPublished++
					}
				}
			}

			// Publish computed topics for input registers
			if pkt.RegisterType == "input" {
				computed := lux.ComputedTopics(pkt.Registers)
				for topic, val := range computed {
					mqttClient.Publish(prefix+"/"+topic+"/state", 0, retain, val)
					packetPublished++
				}
			}

			totalPublished += packetPublished

			// Publish last_seen timestamp for health monitoring
			mqttClient.Publish(lastSeenTopic, 0, true, time.Now().UTC().Format(time.RFC3339))

			log.info("published", map[string]any{
				"type":  pkt.RegisterType,
				"start": pkt.StartRegister,
				"end":   pkt.StartRegister + pkt.RegisterCount - 1,
				"count": packetPublished,
				"total": totalPublished,
			})
		})

		// Clear active client before closing to prevent double-close
		activeClientMu.Lock()
		activeClient = nil
		activeClientMu.Unlock()
		client.Close()

		// Check if this was a shutdown
		select {
		case <-shutdownCh:
			log.status(fmt.Sprintf("Done. %d total publishes.", totalPublished))
			return nil
		default:
		}

		if listenErr != nil {
			log.errorf("inverter connection lost", listenErr)
			mqttClient.Publish(onlineTopic, 1, true, "false")
			log.status("Reconnecting...")
		}
	}
}
