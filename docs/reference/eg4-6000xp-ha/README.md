# EG4-6000XP-Home-Assistant-Local-Control

Ditch the cloud and take direct control of your EG4 6000XP via RS485 and Home Assistant. This project provides the register maps and configurations to manage your inverter locally.

## 🔌 Hardware Setup

### 1. Wiring the RJ45 Cable
Connect a standard CAT5/6 cable to the **INV485** port. The correct port is above the battery Can Port.
Unplug the wifi dongle because it shares the same port.
Use the following pinout for your RS485 adapter:

| Function | RJ45 Pin (T-568B) | Waveshare Terminal |
| :--- | :--- | :--- |
| **RS485 A** | Pin 8 (Brown) | A+ |
| **RS485 B** | Pin 7 (White/Brown) | B- |

### 2. Adapter Configuration
* **DIP Switches:** Set 1 & 2 to **UP** (120Ω termination).
* **Baud Rate:** 19200
* **Slave ID:** 1

## 💻 Home Assistant Configuration

Add the following to your `configuration.yaml`. Note the use of `input_type: input` (Function Code 04) for real-time data.

```yaml
modbus:

  - name: eg4_6000xp
    type: serial
    port: /dev/serial/by-id/usb-FTDI_Dual_RS232-HS-if00-port0
    baudrate: 19200
    bytesize: 8
    method: rtu
    parity: N
    stopbits: 1
    timeout: 5
    delay: 2
    sensors:
    
      # --- LOAD / EPS DATA ---
      - name: "eg4_EPS_L1_Power"
        unique_id: "6000xp_l1_power"
        address: 129
        slave: 1
        unit_of_measurement: "W"
        device_class: power
        input_type: input

      - name: "eg4_EPS_L2_Power"
        unique_id: "6000xp_l2_power"
        address: 130
        slave: 1
        unit_of_measurement: "W"
        device_class: power
        input_type: input

      - name: "eg4_EPS_Total_Power"
        unique_id: "6000xp_total_power"
        address: 24
        slave: 1
        unit_of_measurement: "W"
        device_class: power
        input_type: input

      - name: "eg4_EPS_Voltage_240V"
        unique_id: "6000xp_voltage_240v"
        address: 20
        slave: 1
        unit_of_measurement: "V"
        scale: 0.1
        precision: 1
        input_type: input

      - name: "eg4_EPS_Frequency"
        unique_id: "6000xp_frequency"
        address: 23
        slave: 1
        unit_of_measurement: "Hz"
        scale: 0.01
        precision: 2
        input_type: input

      - name: "eg4_Energy_Output_Today"
        unique_id: "6000xp_energy_output_today"
        address: 35
        slave: 1
        unit_of_measurement: "kWh"
        scale: 0.1
        precision: 1
        device_class: energy
        state_class: total_increasing
        input_type: input

      # --- SOLAR (PV) DATA ---
      - name: "eg4_PV1_Voltage"
        unique_id: "6000xp_pv1_voltage"
        address: 1
        slave: 1
        unit_of_measurement: "V"
        scale: 0.1
        precision: 1
        input_type: input

      - name: "eg4_PV2_Voltage"
        unique_id: "6000xp_pv2_voltage"
        address: 2
        slave: 1
        unit_of_measurement: "V"
        scale: 0.1
        precision: 1
        input_type: input

      - name: "eg4_PV1_Power"
        unique_id: "6000xp_pv1_power"
        address: 7
        slave: 1
        unit_of_measurement: "W"
        device_class: power
        input_type: input

      - name: "eg4_PV2_Power"
        unique_id: "6000xp_pv2_power"
        address: 8
        slave: 1
        unit_of_measurement: "W"
        device_class: power
        input_type: input

      - name: "eg4_PV1_Energy_Today"
        unique_id: "6000xp_pv1_energy_today"
        address: 28
        slave: 1
        unit_of_measurement: "kWh"
        scale: 0.1
        precision: 1
        input_type: input

      - name: "eg4_PV2_Energy_Today"
        unique_id: "6000xp_pv2_energy_today"
        address: 29
        slave: 1
        unit_of_measurement: "kWh"
        scale: 0.1
        precision: 1
        input_type: input

      # --- BATTERY DATA ---
      - name: "eg4_Battery_SOC"
        unique_id: "6000xp_battery_soc"
        address: 5
        slave: 1
        unit_of_measurement: "%"
        device_class: battery
        input_type: input
```

⚠️ Disclaimer

Proceed at your own risk. Modbus allows for direct writing to inverter registers. 
Incorrect settings can damage your battery or inverter.
Always double-check register addresses before writing commands.
