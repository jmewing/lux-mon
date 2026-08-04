# Home Assistant Energy Dashboard — lux-mon integration

lux-mon publishes everything you need for the Home Assistant Energy Dashboard over MQTT, using the auto-discovery topics under `homeassistant/sensor/...`.

## What is auto-configured

When MQTT (and HA discovery) is enabled, lux-mon automatically creates these energy sensors with the correct `device_class` and `state_class`:

| lux-mon sensor | HA entity | Purpose in HA Energy Dashboard |
|---|---|---|
| `sensor.luxmon_solar_pv_energy_total` | Solar production | Total energy from all PV strings (sum of PV1 + PV2 + PV3) |
| `sensor.luxmon_solar_grid_import_energy_total` | Grid consumption | Energy imported from the grid |
| `sensor.luxmon_solar_grid_export_energy_total` | Return to grid | Energy exported to the grid |
| `sensor.luxmon_solar_battery_in_energy_total` | Battery systems → in | Total energy charged into the battery |
| `sensor.luxmon_solar_battery_out_energy_total` | Battery systems → out | Total energy discharged from the battery |

All of the above use:
- `device_class: energy`
- `state_class: total_increasing`
- `unit_of_measurement: kWh`

These are exactly the attributes Home Assistant looks for when adding a sensor to the Energy Dashboard.

## Prerequisites

1. lux-mon is running and **MQTT is enabled**.
2. lux-mon is pointed at the MQTT broker Home Assistant uses. Default broker settings are:
   - Host: `127.0.0.1` (or wherever lux-mon runs)
   - Port: `1883`
   - Topic prefix: `luxmon`
   - HA discovery prefix: `homeassistant`
3. Home Assistant is connected to the same MQTT broker (for example, the Mosquitto add-on or an external broker).

The installer (`scripts/install.sh`) installs and enables Mosquitto by default, so if both lux-mon and HA run on the same Pi/host, no extra broker setup is needed.

## Verify lux-mon is publishing

From the lux-mon host, watch the state topic:

```bash
mosquitto_sub -h 127.0.0.1 -t 'luxmon/luxmon_solar/state' -v
```

You should see keys such as `pv_energy_total`, `grid_import_energy_total`, etc.

To see the discovery configs:

```bash
mosquitto_sub -h 127.0.0.1 -t 'homeassistant/sensor/#' -v
```

## Add sensors to the Home Assistant Energy Dashboard

1. In Home Assistant, go to **Settings → Dashboards → Energy**.
2. Under **Solar panels**, click **Add solar production** and choose:
   - `sensor.luxmon_solar_pv_energy_total`
3. Under **Grid consumption**, click **Add consumption** and choose:
   - `sensor.luxmon_solar_grid_import_energy_total`
4. Under **Return to grid**, click **Add return** and choose:
   - `sensor.luxmon_solar_grid_export_energy_total`
5. Under **Battery systems**, click **Add battery system**:
   - Energy in: `sensor.luxmon_solar_battery_in_energy_total`
   - Energy out: `sensor.luxmon_solar_battery_out_energy_total`
6. Save. Home Assistant builds the energy graphs from the next hourly statistics cycle (the first usable data usually appears within 1–2 hours).

## If the sensors don't appear

- Check that **MQTT → HA Discovery** is enabled in the lux-mon settings UI or `.env`:
  - `LUX_MQTT_ENABLED=true`
  - `LUX_MQTT_HA_DISCOVERY=true`
- Restart the lux-mon collector service to re-publish discovery configs:
  ```bash
  sudo systemctl restart lux-mon
  ```
- In Home Assistant, go to **Developer Tools → MQTT → Listen to topic** and subscribe to `homeassistant/sensor/#` to confirm discovery messages arrive.
- If you change the `LUX_MQTT_DEVICE_ID` setting, the entity IDs will change to match the new device ID.

## Notes

- lux-mon computes `pv_energy_total` by summing `pv1_energy_total + pv2_energy_total + pv3_energy_total`. If your inverter only has two PV strings, the missing string simply contributes `0`.
- Battery-in / battery-out are the lifetime totals from the inverter, not today's values. Home Assistant's `total_increasing` state_class handles midnight rollovers correctly when the inverter resets daily counters.
- Home Assistant requires at least one new value per hour to generate statistics; because lux-mon writes every 5s, statistics populate as soon as the recorder processes the data.
