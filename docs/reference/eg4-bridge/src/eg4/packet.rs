use crate::prelude::*;

use enum_dispatch::*;
use nom_derive::{Nom, Parse};
use num_enum::{IntoPrimitive, TryFromPrimitive};
use serde::Serialize;
use log::error;
use std::convert::TryFrom;

// Add these constants at the top of the file, after the imports
pub const MIN_PACKET_SIZE: usize = 20; // Minimum valid packet size (same as in packet_decoder.rs)
const MIN_TRANSLATED_DATA_SIZE: usize = 38; // header(18) + data(18) + checksum(2)
const MIN_INPUT_BLOCK_SIZE: usize = 2; // Minimum size for input register blocks (1 register = 2 bytes)
const MAX_INPUT_BLOCK_SIZE: usize = 80; // Maximum size for input register blocks (40 registers = 80 bytes)
const MIN_INPUT_ALL_SIZE: usize = 254; // Size for ReadInputAll

#[derive(Clone, Debug)]
pub enum ReadInput {
    ReadInputAll(Box<ReadInputAll>),
    ReadInput1(ReadInput1),
    ReadInput2(ReadInput2),
    ReadInput3(ReadInput3),
    ReadInput4(ReadInput4),
    ReadInput5(ReadInput5),
    ReadInput6(ReadInput6),
}

// {{{ ReadInputAll
#[derive(PartialEq, Clone, Debug, Serialize, Nom)]
#[nom(LittleEndian)]
pub struct ReadInputAll {
    pub status: u16,
    #[nom(Parse = "Utils::le_u16_checked_div10")]
    pub v_pv_1: Option<f64>,
    #[nom(Parse = "Utils::le_u16_checked_div10")]
    pub v_pv_2: Option<f64>,
    #[nom(Parse = "Utils::le_u16_checked_div10")]
    pub v_pv_3: Option<f64>,
    #[nom(Parse = "Utils::le_u16_checked_div10")]
    pub v_bat: Option<f64>,

    pub soc: i8,
    pub soh: i8,

    pub internal_fault: u16,

    #[nom(Ignore)]
    pub p_pv: u16,
    pub p_pv_1: u16,
    pub p_pv_2: u16,
    pub p_pv_3: u16,
    #[nom(Ignore)]
    pub p_battery: i32,
    pub p_charge: u16,
    pub p_discharge: u16,

    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_ac_r: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_ac_s: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_ac_t: f64,
    #[nom(Parse = "Utils::le_u16_div100")]
    pub f_ac: f64,

    pub p_inv: u16,
    pub p_rec: u16,

    #[nom(SkipBefore(2))] // IinvRMS
    #[nom(Parse = "Utils::le_u16_div1000")]
    pub pf: f64,

    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_eps_r: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_eps_s: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_eps_t: f64,
    #[nom(Parse = "Utils::le_u16_div100")]
    pub f_eps: f64,
    pub p_eps: u16,
    pub s_eps: u16,
    #[nom(Ignore)]
    pub p_grid: i32,
    pub p_to_grid: u16,
    pub p_to_user: u16,

    #[nom(Ignore)]
    pub e_pv_day: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_pv_day_1: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_pv_day_2: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_pv_day_3: f64,

    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_inv_day: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_rec_day: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_chg_day: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_dischg_day: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_eps_day: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_to_grid_day: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_to_user_day: f64,

    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_bus_1: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_bus_2: f64,

    #[nom(Ignore)]
    pub e_pv_all: f64,
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_pv_all_1: f64,
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_pv_all_2: f64,
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_pv_all_3: f64,

    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_inv_all: f64,
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_rec_all: f64,
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_chg_all: f64,
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_dischg_all: f64,
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_eps_all: f64,
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_to_grid_all: f64,
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_to_user_all: f64,

    pub fault_code: u32,
    pub warning_code: u32,

    pub t_inner: u16,
    pub t_rad_1: u16,
    pub t_rad_2: u16,
    pub t_bat: u16,
    #[nom(SkipBefore(2))] // reserved - radiator 3?
    pub runtime: u32,
    // 18 bytes of auto_test stuff here I'm not doing yet
    #[nom(SkipBefore(18))] // auto_test stuff, TODO..
    #[nom(SkipBefore(2))] // bat_brand, bat_com_type
    #[nom(Parse = "Utils::le_u16_div10")]
    pub max_chg_curr: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub max_dischg_curr: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub charge_volt_ref: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub dischg_cut_volt: f64,

    pub bat_status_0: u16,
    pub bat_status_1: u16,
    pub bat_status_2: u16,
    pub bat_status_3: u16,
    pub bat_status_4: u16,
    pub bat_status_5: u16,
    pub bat_status_6: u16,
    pub bat_status_7: u16,
    pub bat_status_8: u16,
    pub bat_status_9: u16,
    pub bat_status_inv: u16,

    pub bat_count: u16,
    pub bat_capacity: u16,

    #[nom(Parse = "Utils::le_u16_div100")]
    pub bat_current: f64,

    pub bms_event_1: u16, // FaultCode_BMS
    pub bms_event_2: u16, // WarningCode_BMS

    // TODO: probably floats but need non-zero sample data to check. just guessing at the div100.
    #[nom(Parse = "Utils::le_u16_div1000")]
    pub max_cell_voltage: f64,
    #[nom(Parse = "Utils::le_u16_div1000")]
    pub min_cell_voltage: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub max_cell_temp: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub min_cell_temp: f64,

    pub bms_fw_update_state: u16,

    pub cycle_count: u16,

    #[nom(Parse = "Utils::le_u16_div10")]
    pub vbat_inv: f64,

    // Generator and bus voltage data (previously skipped 14 bytes)
    pub gen_status: u16,          // Generator status (0=Off, 1=Starting, 2=Running, 3=Stopping, 4=Error)
    pub gen_power_factor: u16,    // Generator power factor (0-1000, divide by 1000 for actual value)
    pub gen_current: u16,         // Generator current in Amps (0-100A)
    pub gen_power_limit: u16,     // Generator power limit setting in Watts (0-10000W)
    pub gen_connect_status: u16,  // Generator connection status (0=Disconnected, 1=Connected)
    pub gen_control_mode: u16,    // Generator control mode (0=Auto, 1=Manual, 2=Test)
    pub gen_dispatch_mode: u16,   // Generator dispatch mode (0=Off, 1=On, 2=Auto)

    // Half bus voltage data (previously skipped 2 bytes)
    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_bus_half: f64,         // Half bus voltage (V) - Expected range: 0-1000V

    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_gen: f64,              // Generator voltage (V) - Expected range: 180-270V
    #[nom(Parse = "Utils::le_u16_div100")]
    pub f_gen: f64,              // Generator frequency (Hz) - Expected range: 45-65Hz when running
    pub p_gen: u16,              // Generator power output (W) - Expected range: 0-10000W
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_gen_day: f64,          // Generator daily energy production (kWh) - Expected range: 0-1000kWh
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_gen_all: f64,          // Generator total energy production (kWh) - Expected range: 0-999999kWh
    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_eps_l1: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_eps_l2: f64,
    pub p_eps_l1: u16,
    pub p_eps_l2: u16,
    pub s_eps_l1: u16,
    pub s_eps_l2: u16,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_eps_l1_day: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_eps_l2_day: f64,
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_eps_l1_all: f64,
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_eps_l2_all: f64,

    // Additional EPS values
    #[nom(Parse = "Utils::le_u16_div100")]
    pub i_eps_l1: f64,
    #[nom(Parse = "Utils::le_u16_div100")]
    pub i_eps_l2: f64,
    #[nom(Parse = "Utils::le_u16_div1000")]
    pub pf_eps_l1: f64,
    #[nom(Parse = "Utils::le_u16_div1000")]
    pub pf_eps_l2: f64,
    #[nom(Parse = "Utils::le_u16_div100")]
    pub f_eps_l1: f64,
    #[nom(Parse = "Utils::le_u16_div100")]
    pub f_eps_l2: f64,

    // following are for influx capability only
    #[nom(Parse = "Utils::current_time_for_nom")]
    pub time: UnixTime,
    #[nom(Ignore)]
    pub datalog: Serial,

    // Battery configuration from ReadInput5
    pub bat_cell_count: u16,          // Number of battery cells in series
    pub bat_parallel_count: u16,      // Number of battery cells in parallel
    pub under_freq_start: u16,        // Underfrequency load reduction starting point
    pub under_freq_end: u16,          // Underfrequency derating end point
    pub under_freq_slope: u16,        // Underfrequency load shedding slope
    pub max_compensation: u16,        // Maximum compensation amount for specific load
    pub chg_power_pct: u16,          // Charging power percentage setting
    pub dischg_power_pct: u16,       // Discharge power percentage setting
    pub ac_charge_pct: u16,          // AC charge percentage setting
    pub chg_priority_pct: u16,       // Charging priority percentage setting
    pub forced_dischg_pct: u16,      // Forced discharge percentage setting
    pub inv_power_pct: u16,          // Inverter active power percentage setting
    
    // AC charging parameters
    #[nom(Parse = "Utils::le_u16_div10")]
    pub ac_chg_start_v: f64,         // AC charging starting battery voltage
    #[nom(Parse = "Utils::le_u16_div10")]
    pub ac_chg_end_v: f64,           // AC charging cut off battery voltage
    pub ac_chg_start_soc: u16,       // AC charging starting SOC
    pub ac_chg_end_soc: u16,         // AC charging stops SOC
    
    // Battery voltage parameters
    #[nom(Parse = "Utils::le_u16_div10")]
    pub bat_low_v: f64,              // Battery undervoltage alarm point
    #[nom(Parse = "Utils::le_u16_div10")]
    pub bat_low_back_v: f64,         // Battery undervoltage alarm recovery point
    pub bat_low_soc: u16,            // Battery undervoltage alarm point (SOC)
    pub bat_low_back_soc: u16,       // Battery undervoltage alarm recovery point (SOC)
    #[nom(Parse = "Utils::le_u16_div10")]
    pub bat_low_utility_v: f64,      // Battery undervoltage to mains voltage point
    pub bat_low_utility_soc: u16,    // Battery undervoltage to mains SOC
    
    // Additional parameters
    pub ac_chg_curr: u16,            // AC charge current
    #[nom(Parse = "Utils::le_u16_div10")]
    pub ongrid_eod_v: f64,           // On-grid EOD voltage
    
    // SOC curve data
    #[nom(Parse = "Utils::le_u16_div10")]
    pub soc_volt1: f64,              // SOC curve voltage point 1
    #[nom(Parse = "Utils::le_u16_div10")]
    pub soc_volt2: f64,              // SOC curve voltage point 2
    pub soc_pct1: u16,               // SOC percentage point 1
    pub soc_pct2: u16,               // SOC percentage point 2
    pub soc_inner_resistance: u16,    // SOC curve inner resistance
    
    // Power settings
    pub max_grid_input_power: u16,    // Maximum grid input power
    pub gen_rated_power: u16,         // Generator rated power
    
    // Function enable flags
    pub function_bit_flags: u16,      // Combined function enable flags
    
    // Additional settings
    pub afci_threshold: u16,          // AFCI arc threshold
    #[nom(Parse = "Utils::le_u16_div10")]
    pub volt_watt_v1: f64,           // Volt-Watt V1 setting
    #[nom(Parse = "Utils::le_u16_div10")]
    pub volt_watt_v2: f64,           // Volt-Watt V2 setting
    pub volt_watt_delay: u16,        // Volt-Watt delay time
    pub volt_watt_p2: u16,           // Volt-Watt P2 setting

    // Extended system parameters from ReadInput6
    pub grid_voltage_high_pure: u16,     // Grid voltage high pure limit
    pub grid_voltage_low_pure: u16,      // Grid voltage low pure limit
    pub grid_freq_high_pure: u16,        // Grid frequency high pure limit
    pub grid_freq_low_pure: u16,         // Grid frequency low pure limit
    pub grid_volt_high_delay: u16,       // Grid voltage high delay
    pub grid_volt_low_delay: u16,        // Grid voltage low delay
    pub grid_freq_high_delay: u16,       // Grid frequency high delay
    pub grid_freq_low_delay: u16,        // Grid frequency low delay
    pub grid_volt_recover_high: u16,     // Grid voltage recover high threshold
    pub grid_volt_recover_low: u16,      // Grid voltage recover low threshold
    pub grid_freq_recover_high: u16,     // Grid frequency recover high threshold
    pub grid_freq_recover_low: u16,      // Grid frequency recover low threshold
    pub grid_volt_recover_delay: u16,    // Grid voltage recover delay
    pub grid_freq_recover_delay: u16,    // Grid frequency recover delay
    pub island_detect_time: u16,         // Island detection time
    pub pf_cmd_memory_en: u16,           // Power factor command memory enable
    pub pf_cmd_memory_pf: u16,           // Power factor command memory value
    pub pf_cmd_memory_p_ref: u16,        // Power factor command memory reference power
    pub pf_cmd_memory_v_ref: u16,        // Power factor command memory reference voltage
    pub pf_cmd_memory_q_ref: u16,        // Power factor command memory reference reactive power
}

impl ReadInputAll {
    pub fn calculate_derived_values(&mut self) -> Result<()> {
        debug!("Calculating derived values for ReadInputAll");
        
        // Safe conversion and addition of power values using u16
        self.p_pv = self.p_pv_1
            .checked_add(self.p_pv_2)
            .and_then(|sum| sum.checked_add(self.p_pv_3))
            .ok_or_else(|| anyhow!("Power value overflow in p_pv calculation"))?;

        // Safe conversion and subtraction for battery power
        self.p_battery = i32::from(self.p_charge)
            .checked_sub(i32::from(self.p_discharge))
            .ok_or_else(|| anyhow!("Power value overflow in p_battery calculation"))?;

        // Safe conversion and subtraction for grid power
        self.p_grid = i32::from(self.p_to_user)
            .checked_sub(i32::from(self.p_to_grid))
            .ok_or_else(|| anyhow!("Power value overflow in p_grid calculation"))?;

        // Safe addition for total PV energy - using f64 arithmetic
        self.e_pv_day = Utils::round(self.e_pv_day_1 + self.e_pv_day_2 + self.e_pv_day_3, 1);
        self.e_pv_all = Utils::round(self.e_pv_all_1 + self.e_pv_all_2 + self.e_pv_all_3, 1);

        debug!("Derived values calculated successfully");
        Ok(())
    }

    pub fn validate(&self) -> Result<()> {
        // Validate SOC and SOH
        if self.soc < 0 || self.soc > 100 {
            return Err(anyhow!("Invalid SOC value: {}", self.soc));
        }
        if self.soh < 0 || self.soh > 100 {
            return Err(anyhow!("Invalid SOH value: {}", self.soh));
        }

        // Validate power values are within reasonable ranges
        if self.p_pv_1 > 10000 || self.p_pv_2 > 10000 || self.p_pv_3 > 10000 {
            return Err(anyhow!("Invalid PV power values"));
        }

        // Validate frequencies
        if self.f_ac < 45.0 || self.f_ac > 65.0 {
            return Err(anyhow!("Invalid AC frequency: {}", self.f_ac));
        }
        if self.f_eps < 45.0 || self.f_eps > 65.0 {
            return Err(anyhow!("Invalid EPS frequency: {}", self.f_eps));
        }

        // Validate generator measurements
        if self.v_gen > 0.0 && (self.v_gen < 180.0 || self.v_gen > 270.0) {
            return Err(anyhow!("Invalid generator voltage: {}", self.v_gen));
        }
        if self.f_gen > 0.0 && (self.f_gen < 45.0 || self.f_gen > 65.0) {
            return Err(anyhow!("Invalid generator frequency: {}", self.f_gen));
        }
        if self.p_gen > 10000 {
            return Err(anyhow!("Invalid generator power: {}", self.p_gen));
        }
        if self.gen_power_factor > 1000 {
            return Err(anyhow!("Invalid generator power factor: {}", self.gen_power_factor));
        }
        if self.gen_current > 100 {
            return Err(anyhow!("Invalid generator current: {}", self.gen_current));
        }

        // Validate bus voltage
        if self.v_bus_half > 1000.0 {
            return Err(anyhow!("Invalid half bus voltage: {}", self.v_bus_half));
        }

        Ok(())
    }
}
// }}}

// {{{ ReadInput1
#[derive(Clone, Debug, Serialize, Nom)]
#[nom(LittleEndian)]
pub struct ReadInput1 {
    pub status: u16,
    #[nom(Parse = "Utils::le_u16_checked_div10")]
    pub v_pv_1: Option<f64>,
    #[nom(Parse = "Utils::le_u16_checked_div10")]
    pub v_pv_2: Option<f64>,
    #[nom(Parse = "Utils::le_u16_checked_div10")]
    pub v_pv_3: Option<f64>,
    #[nom(Parse = "Utils::le_u16_checked_div10")]
    pub v_bat: Option<f64>,

    pub soc: i8,
    pub soh: i8,

    pub internal_fault: u16,

    #[nom(Ignore)]
    pub p_pv: u16,
    pub p_pv_1: u16,
    pub p_pv_2: u16,
    pub p_pv_3: u16,
    #[nom(Ignore)]
    pub p_battery: i32,
    pub p_charge: u16,
    pub p_discharge: u16,

    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_ac_r: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_ac_s: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_ac_t: f64,
    #[nom(Parse = "Utils::le_u16_div100")]
    pub f_ac: f64,

    pub p_inv: u16,
    pub p_rec: u16,

    #[nom(SkipBefore(2))] // IinvRMS
    #[nom(Parse = "Utils::le_u16_div1000")]
    pub pf: f64,

    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_eps_r: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_eps_s: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_eps_t: f64,
    #[nom(Parse = "Utils::le_u16_div100")]
    pub f_eps: f64,
    pub p_eps: u16,
    pub s_eps: u16,
    #[nom(Ignore)]
    pub p_grid: i32,
    pub p_to_grid: u16,
    pub p_to_user: u16,

    #[nom(Ignore)]
    pub e_pv_day: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_pv_day_1: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_pv_day_2: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_pv_day_3: f64,

    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_inv_day: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_rec_day: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_chg_day: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_dischg_day: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_eps_day: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_to_grid_day: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_to_user_day: f64,

    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_bus_1: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_bus_2: f64,

    #[nom(Parse = "Utils::current_time_for_nom")]
    pub time: UnixTime,
    #[nom(Ignore)]
    pub datalog: Serial,
}

impl ReadInput1 {
    pub fn calculate_derived_values(&mut self) -> Result<()> {
        // Safe conversion and addition of power values using u16
        self.p_pv = self.p_pv_1
            .checked_add(self.p_pv_2)
            .and_then(|sum| sum.checked_add(self.p_pv_3))
            .ok_or_else(|| anyhow!("Power value overflow in p_pv calculation"))?;

        // Safe conversion and subtraction for battery power
        self.p_battery = i32::from(self.p_charge)
            .checked_sub(i32::from(self.p_discharge))
            .ok_or_else(|| anyhow!("Power value overflow in p_battery calculation"))?;

        // Safe conversion and subtraction for grid power
        self.p_grid = i32::from(self.p_to_user)
            .checked_sub(i32::from(self.p_to_grid))
            .ok_or_else(|| anyhow!("Power value overflow in p_grid calculation"))?;

        // Safe addition for total PV energy - using f64 arithmetic
        self.e_pv_day = Utils::round(self.e_pv_day_1 + self.e_pv_day_2 + self.e_pv_day_3, 1);

        Ok(())
    }
}
// }}}

// {{{ ReadInput2
#[derive(Clone, Debug, Serialize, Nom)]
#[nom(Debug, LittleEndian)]
pub struct ReadInput2 {
    // Total PV energy (derived from sum of e_pv_all_1/2/3)
    #[nom(Ignore)]
    pub e_pv_all: f64,
    // Total PV energy from string 1 (kWh)
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_pv_all_1: f64,
    // Total PV energy from string 2 (kWh)
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_pv_all_2: f64,
    // Total PV energy from string 3 (kWh)
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_pv_all_3: f64,

    // Total energy fed into grid through inverter (kWh)
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_inv_all: f64,
    // Total energy received from grid (kWh)
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_rec_all: f64,
    // Total energy used to charge battery (kWh)
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_chg_all: f64,
    // Total energy discharged from battery (kWh)
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_dischg_all: f64,
    // Total energy supplied to EPS loads (kWh)
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_eps_all: f64,
    // Total energy exported to grid (kWh)
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_to_grid_all: f64,
    // Total energy consumed from grid (kWh)
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_to_user_all: f64,

    // System fault code (bitmap)
    pub fault_code: u32,
    // System warning code (bitmap)
    pub warning_code: u32,

    // Internal temperature (°C)
    pub t_inner: u16,
    // Radiator temperature 1 (°C)
    pub t_rad_1: u16,
    // Radiator temperature 2 (°C)
    pub t_rad_2: u16,
    // Battery temperature (°C)
    pub t_bat: u16,

    // Radiator temperature 3 (°C)
    pub t_rad_3: u16,

    // Total runtime in seconds
    pub runtime: u32,

    // Auto-test data (18 bytes total)
    // Current status of auto-test (0=not running, 1=in progress)
    pub auto_test_status: u16,
    // Current stage of testing sequence
    pub auto_test_stage: u16,
    // Time remaining or timeout value for current test
    pub auto_test_timeout: u16,
    // Upper frequency limit being tested (Hz)
    pub auto_test_frequency_upper: u16,
    // Lower frequency limit being tested (Hz)
    pub auto_test_frequency_lower: u16,
    // Upper voltage limit being tested (V)
    pub auto_test_voltage_upper: u16,
    // Lower voltage limit being tested (V)
    pub auto_test_voltage_lower: u16,
    // Power reading during test (W)
    pub auto_test_power: u16,
    // Result code from auto-test
    pub auto_test_result: u16,

    // Battery information
    // Battery manufacturer/brand identifier
    // Known values:
    // 0: Unknown/Not configured
    // 1: Pylon
    // 2: Dyness
    // Add other known values as they are discovered
    pub bat_brand: u8,
    
    // Battery communication protocol type
    // Known values:
    // 0: Unknown/Not configured
    // 1: RS485
    // 2: CAN
    // Add other known values as they are discovered
    pub bat_com_type: u8,

    // Timestamp of reading
    #[nom(Parse = "Utils::current_time_for_nom")]
    pub time: UnixTime,
    // Datalog serial number
    #[nom(Ignore)]
    pub datalog: Serial,
}

impl ReadInput2 {
    pub fn calculate_derived_values(&mut self) -> Result<()> {
        // Safe addition for total PV energy - using f64 arithmetic
        self.e_pv_all = Utils::round(self.e_pv_all_1 + self.e_pv_all_2 + self.e_pv_all_3, 1);
        Ok(())
    }
}
// }}}

// {{{ ReadInput3
#[derive(Clone, Debug, Serialize, Nom)]
#[nom(LittleEndian)]
pub struct ReadInput3 {
    #[nom(SkipBefore(2))] // bat_brand, bat_com_type
    #[nom(Parse = "Utils::le_u16_div100")]
    pub max_chg_curr: f64,
    #[nom(Parse = "Utils::le_u16_div100")]
    pub max_dischg_curr: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub charge_volt_ref: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub dischg_cut_volt: f64,

    pub bat_status_0: u16,
    pub bat_status_1: u16,
    pub bat_status_2: u16,
    pub bat_status_3: u16,
    pub bat_status_4: u16,
    pub bat_status_5: u16,
    pub bat_status_6: u16,
    pub bat_status_7: u16,
    pub bat_status_8: u16,
    pub bat_status_9: u16,
    pub bat_status_inv: u16,

    pub bat_count: u16,
    pub bat_capacity: u16,

    #[nom(Parse = "Utils::le_u16_div100")]
    pub bat_current: f64,

    pub bms_event_1: u16,
    pub bms_event_2: u16,

    // TODO: probably floats but need non-zero sample data to check. just guessing at the div100.
    #[nom(Parse = "Utils::le_u16_div1000")]
    pub max_cell_voltage: f64,
    #[nom(Parse = "Utils::le_u16_div1000")]
    pub min_cell_voltage: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub max_cell_temp: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub min_cell_temp: f64,

    pub bms_fw_update_state: u16,

    pub cycle_count: u16,

    #[nom(Parse = "Utils::le_u16_div10")]
    pub vbat_inv: f64,

    // Battery module information
    #[nom(Parse = "Utils::le_u16_div10")]
    pub t_bat_1: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub t_bat_2: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub t_bat_3: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub t_bat_4: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_bat_1: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_bat_2: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_bat_3: f64,
    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_bat_4: f64,
    #[nom(Parse = "Utils::le_u16_div100")]
    pub i_bat_1: f64,
    #[nom(Parse = "Utils::le_u16_div100")]
    pub i_bat_2: f64,
    #[nom(Parse = "Utils::le_u16_div100")]
    pub i_bat_3: f64,
    #[nom(Parse = "Utils::le_u16_div100")]
    pub i_bat_4: f64,
    pub soc_1: u8,
    pub soc_2: u8,
    pub soc_3: u8,
    pub soc_4: u8,
    pub soh_1: u8,
    pub soh_2: u8,
    pub soh_3: u8,
    pub soh_4: u8,

    // following are for influx capability only
    #[nom(Parse = "Utils::current_time_for_nom")]
    pub time: UnixTime,
    #[nom(Ignore)]
    pub datalog: Serial,
}
// }}}

#[derive(Clone, Debug, Serialize, Nom)]
#[nom(LittleEndian)]
pub struct ReadInput4 {
    // Half bus voltage (V) - Expected range: 0-1000V
    #[nom(SkipBefore(2))]
    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_gen: f64,
    // Generator frequency (Hz) - Expected range: 45-65Hz when running
    #[nom(Parse = "Utils::le_u16_div100")]
    pub f_gen: f64,
    // Generator power output (W) - Expected range: 0-10000W
    pub p_gen: u16,
    // Generator daily energy production (kWh) - Expected range: 0-1000kWh
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_gen_day: f64,
    // Generator total energy production (kWh) - Expected range: 0-999999kWh
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_gen_all: f64,
    // EPS voltage L1 (V) - Expected range: 180-270V
    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_eps_l1: f64,
    // EPS voltage L2 (V) - Expected range: 180-270V
    #[nom(Parse = "Utils::le_u16_div10")]
    pub v_eps_l2: f64,
    // EPS power L1 (W) - Expected range: 0-10000W
    pub p_eps_l1: u16,
    // EPS power L2 (W) - Expected range: 0-10000W
    pub p_eps_l2: u16,
    // EPS apparent power L1 (VA) - Expected range: 0-10000VA
    pub s_eps_l1: u16,
    // EPS apparent power L2 (VA) - Expected range: 0-10000VA
    pub s_eps_l2: u16,
    // EPS daily energy L1 (kWh) - Expected range: 0-1000kWh
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_eps_l1_day: f64,
    // EPS daily energy L2 (kWh) - Expected range: 0-1000kWh
    #[nom(Parse = "Utils::le_u16_div10")]
    pub e_eps_l2_day: f64,
    // EPS total energy L1 (kWh) - Expected range: 0-999999kWh
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_eps_l1_all: f64,
    // EPS total energy L2 (kWh) - Expected range: 0-999999kWh
    #[nom(Parse = "Utils::le_u32_div10")]
    pub e_eps_l2_all: f64,

    // Additional EPS values
    // EPS current L1 (A) - Expected range: 0-50A
    #[nom(Parse = "Utils::le_u16_div100")]
    pub i_eps_l1: f64,
    // EPS current L2 (A) - Expected range: 0-50A
    #[nom(Parse = "Utils::le_u16_div100")]
    pub i_eps_l2: f64,
    // EPS power factor L1 - Expected range: 0-1.0
    #[nom(Parse = "Utils::le_u16_div1000")]
    pub pf_eps_l1: f64,
    // EPS power factor L2 - Expected range: 0-1.0
    #[nom(Parse = "Utils::le_u16_div1000")]
    pub pf_eps_l2: f64,
    // EPS frequency L1 (Hz) - Expected range: 45-65Hz
    #[nom(Parse = "Utils::le_u16_div100")]
    pub f_eps_l1: f64,
    // EPS frequency L2 (Hz) - Expected range: 45-65Hz
    #[nom(Parse = "Utils::le_u16_div100")]
    pub f_eps_l2: f64,

    // EPS data; unsure what this is
    #[nom(Ignore)]
    pub datalog: Serial,
}

// {{{ ReadInput5
#[derive(Clone, Debug, Serialize, Nom)]
#[nom(LittleEndian)]
pub struct ReadInput5 {
    // Battery configuration
    pub bat_cell_count: u16,          // Number of battery cells in series
    pub bat_parallel_count: u16,      // Number of battery cells in parallel
    pub under_freq_start: u16,        // Underfrequency load reduction starting point
    pub under_freq_end: u16,          // Underfrequency derating end point
    pub under_freq_slope: u16,        // Underfrequency load shedding slope
    pub max_compensation: u16,        // Maximum compensation amount for specific load
    pub chg_power_pct: u16,          // Charging power percentage setting
    pub dischg_power_pct: u16,       // Discharge power percentage setting
    pub ac_charge_pct: u16,          // AC charge percentage setting
    pub chg_priority_pct: u16,       // Charging priority percentage setting
    pub forced_dischg_pct: u16,      // Forced discharge percentage setting
    pub inv_power_pct: u16,          // Inverter active power percentage setting
    
    // AC charging parameters
    #[nom(Parse = "Utils::le_u16_div10")]
    pub ac_chg_start_v: f64,         // AC charging starting battery voltage
    #[nom(Parse = "Utils::le_u16_div10")]
    pub ac_chg_end_v: f64,           // AC charging cut off battery voltage
    pub ac_chg_start_soc: u16,       // AC charging starting SOC
    pub ac_chg_end_soc: u16,         // AC charging stops SOC
    
    // Battery voltage parameters
    #[nom(Parse = "Utils::le_u16_div10")]
    pub bat_low_v: f64,              // Battery undervoltage alarm point
    #[nom(Parse = "Utils::le_u16_div10")]
    pub bat_low_back_v: f64,         // Battery undervoltage alarm recovery point
    pub bat_low_soc: u16,            // Battery undervoltage alarm point (SOC)
    pub bat_low_back_soc: u16,       // Battery undervoltage alarm recovery point (SOC)
    #[nom(Parse = "Utils::le_u16_div10")]
    pub bat_low_utility_v: f64,      // Battery undervoltage to mains voltage point
    pub bat_low_utility_soc: u16,    // Battery undervoltage to mains SOC
    
    // Additional parameters
    pub ac_chg_curr: u16,            // AC charge current
    #[nom(Parse = "Utils::le_u16_div10")]
    pub ongrid_eod_v: f64,           // On-grid EOD voltage
    
    // SOC curve data
    #[nom(Parse = "Utils::le_u16_div10")]
    pub soc_volt1: f64,              // SOC curve voltage point 1
    #[nom(Parse = "Utils::le_u16_div10")]
    pub soc_volt2: f64,              // SOC curve voltage point 2
    pub soc_pct1: u16,               // SOC percentage point 1
    pub soc_pct2: u16,               // SOC percentage point 2
    pub soc_inner_resistance: u16,    // SOC curve inner resistance
    
    // Power settings
    pub max_grid_input_power: u16,    // Maximum grid input power
    pub gen_rated_power: u16,         // Generator rated power
    
    // Function enable flags
    pub function_bit_flags: u16,      // Combined function enable flags
    
    // Additional settings
    pub afci_threshold: u16,          // AFCI arc threshold
    #[nom(Parse = "Utils::le_u16_div10")]
    pub volt_watt_v1: f64,           // Volt-Watt V1 setting
    #[nom(Parse = "Utils::le_u16_div10")]
    pub volt_watt_v2: f64,           // Volt-Watt V2 setting
    pub volt_watt_delay: u16,        // Volt-Watt delay time
    pub volt_watt_p2: u16,           // Volt-Watt P2 setting
    
    // Timestamp and datalog
    #[nom(Parse = "Utils::current_time_for_nom")]
    pub time: UnixTime,
    #[nom(Ignore)]
    pub datalog: Serial,
}
// }}}

#[derive(Debug, Clone, Serialize)]
pub struct ReadInput6 {
    // Extended system parameters (registers 200-239)
    pub grid_voltage_high_pure: u16,     // Grid voltage high pure limit
    pub grid_voltage_low_pure: u16,      // Grid voltage low pure limit
    pub grid_freq_high_pure: u16,        // Grid frequency high pure limit
    pub grid_freq_low_pure: u16,         // Grid frequency low pure limit
    pub grid_volt_high_delay: u16,       // Grid voltage high delay
    pub grid_volt_low_delay: u16,        // Grid voltage low delay
    pub grid_freq_high_delay: u16,       // Grid frequency high delay
    pub grid_freq_low_delay: u16,        // Grid frequency low delay
    pub grid_volt_recover_high: u16,     // Grid voltage recover high threshold
    pub grid_volt_recover_low: u16,      // Grid voltage recover low threshold
    pub grid_freq_recover_high: u16,     // Grid frequency recover high threshold
    pub grid_freq_recover_low: u16,      // Grid frequency recover low threshold
    pub grid_volt_recover_delay: u16,    // Grid voltage recover delay
    pub grid_freq_recover_delay: u16,    // Grid frequency recover delay
    pub island_detect_time: u16,         // Island detection time
    pub pf_cmd_memory_en: u16,           // Power factor command memory enable
    pub pf_cmd_memory_pf: u16,           // Power factor command memory value
    pub pf_cmd_memory_p_ref: u16,        // Power factor command memory reference power
    pub pf_cmd_memory_v_ref: u16,        // Power factor command memory reference voltage
    pub pf_cmd_memory_q_ref: u16,        // Power factor command memory reference reactive power

    // following are for influx capability only
    #[serde(skip_serializing)]
    pub time: UnixTime,
    #[serde(skip_serializing)]
    pub datalog: Serial,
}

// {{{ ReadInputs
#[derive(Default, Clone, Debug)]
pub struct ReadInputs {
    read_input_1: Option<ReadInput1>,
    read_input_2: Option<ReadInput2>,
    read_input_3: Option<ReadInput3>,
    read_input_4: Option<ReadInput4>,
    read_input_5: Option<ReadInput5>,
    read_input_6: Option<ReadInput6>,
}

impl ReadInputs {
    pub fn set_read_input_1(&mut self, i: ReadInput1) {
        self.read_input_1 = Some(i);
    }
    pub fn set_read_input_2(&mut self, i: ReadInput2) {
        self.read_input_2 = Some(i);
    }
    pub fn set_read_input_3(&mut self, i: ReadInput3) {
        self.read_input_3 = Some(i);
    }
    pub fn set_read_input_4(&mut self, i: ReadInput4) {
        self.read_input_4 = Some(i);
    }
    pub fn set_read_input_5(&mut self, i: ReadInput5) {
        self.read_input_5 = Some(i);
    }
    pub fn set_read_input_6(&mut self, i: ReadInput6) {
        self.read_input_6 = Some(i);
    }

    pub fn to_input_all(&self) -> Option<ReadInputAll> {
        if let (Some(r1), Some(r2), Some(r3), Some(r4), Some(r5), Some(r6)) = (
            &self.read_input_1,
            &self.read_input_2,
            &self.read_input_3,
            &self.read_input_4,
            &self.read_input_5,
            &self.read_input_6,
        ) {
            let mut all = ReadInputAll {
                status: r1.status,
                v_pv_1: r1.v_pv_1,
                v_pv_2: r1.v_pv_2,
                v_pv_3: r1.v_pv_3,
                v_bat: r1.v_bat,
                soc: r1.soc,
                soh: r1.soh,
                internal_fault: r1.internal_fault,
                p_pv: r1.p_pv,
                p_pv_1: r1.p_pv_1,
                p_pv_2: r1.p_pv_2,
                p_pv_3: r1.p_pv_3,
                p_battery: r1.p_battery,
                p_charge: r1.p_charge,
                p_discharge: r1.p_discharge,
                v_ac_r: r1.v_ac_r,
                v_ac_s: r1.v_ac_s,
                v_ac_t: r1.v_ac_t,
                f_ac: r1.f_ac,
                p_inv: r1.p_inv,
                p_rec: r1.p_rec,
                pf: r1.pf,
                v_eps_r: r1.v_eps_r,
                v_eps_s: r1.v_eps_s,
                v_eps_t: r1.v_eps_t,
                f_eps: r1.f_eps,
                p_eps: r1.p_eps,
                s_eps: r1.s_eps,
                p_grid: r1.p_grid,
                p_to_grid: r1.p_to_grid,
                p_to_user: r1.p_to_user,
                e_pv_day: r1.e_pv_day,
                e_pv_day_1: r1.e_pv_day_1,
                e_pv_day_2: r1.e_pv_day_2,
                e_pv_day_3: r1.e_pv_day_3,
                e_inv_day: r1.e_inv_day,
                e_rec_day: r1.e_rec_day,
                e_chg_day: r1.e_chg_day,
                e_dischg_day: r1.e_dischg_day,
                e_eps_day: r1.e_eps_day,
                e_to_grid_day: r1.e_to_grid_day,
                e_to_user_day: r1.e_to_user_day,
                v_bus_1: r1.v_bus_1,
                v_bus_2: r1.v_bus_2,
                e_pv_all: r2.e_pv_all,
                e_pv_all_1: r2.e_pv_all_1,
                e_pv_all_2: r2.e_pv_all_2,
                e_pv_all_3: r2.e_pv_all_3,
                e_inv_all: r2.e_inv_all,
                e_rec_all: r2.e_rec_all,
                e_chg_all: r2.e_chg_all,
                e_dischg_all: r2.e_dischg_all,
                e_eps_all: r2.e_eps_all,
                e_to_grid_all: r2.e_to_grid_all,
                e_to_user_all: r2.e_to_user_all,
                fault_code: r2.fault_code,
                warning_code: r2.warning_code,
                t_inner: r2.t_inner,
                t_rad_1: r2.t_rad_1,
                t_rad_2: r2.t_rad_2,
                t_bat: r2.t_bat,
                runtime: r2.runtime,
                max_chg_curr: r3.max_chg_curr,
                max_dischg_curr: r3.max_dischg_curr,
                charge_volt_ref: r3.charge_volt_ref,
                dischg_cut_volt: r3.dischg_cut_volt,
                bat_status_0: r3.bat_status_0,
                bat_status_1: r3.bat_status_1,
                bat_status_2: r3.bat_status_2,
                bat_status_3: r3.bat_status_3,
                bat_status_4: r3.bat_status_4,
                bat_status_5: r3.bat_status_5,
                bat_status_6: r3.bat_status_6,
                bat_status_7: r3.bat_status_7,
                bat_status_8: r3.bat_status_8,
                bat_status_9: r3.bat_status_9,
                bat_status_inv: r3.bat_status_inv,
                bat_count: r3.bat_count,
                bat_capacity: r3.bat_capacity,
                bat_current: r3.bat_current,
                bms_event_1: r3.bms_event_1,
                bms_event_2: r3.bms_event_2,
                max_cell_voltage: r3.max_cell_voltage,
                min_cell_voltage: r3.min_cell_voltage,
                max_cell_temp: r3.max_cell_temp,
                min_cell_temp: r3.min_cell_temp,
                bms_fw_update_state: r3.bms_fw_update_state,
                cycle_count: r3.cycle_count,
                vbat_inv: r3.vbat_inv,
                gen_status: 0,          // Default value since not available in input
                gen_power_factor: 0,    // Default value since not available in input
                gen_current: 0,         // Default value since not available in input
                gen_power_limit: 0,     // Default value since not available in input
                gen_connect_status: 0,  // Default value since not available in input
                gen_control_mode: 0,    // Default value since not available in input
                gen_dispatch_mode: 0,   // Default value since not available in input
                v_bus_half: 0.0,       // Default value since not available in input
                v_gen: r4.v_gen,
                f_gen: r4.f_gen,
                p_gen: r4.p_gen,
                e_gen_day: r4.e_gen_day,
                e_gen_all: r4.e_gen_all,
                v_eps_l1: r4.v_eps_l1,
                v_eps_l2: r4.v_eps_l2,
                p_eps_l1: r4.p_eps_l1,
                p_eps_l2: r4.p_eps_l2,
                s_eps_l1: r4.s_eps_l1,
                s_eps_l2: r4.s_eps_l2,
                e_eps_l1_day: r4.e_eps_l1_day,
                e_eps_l2_day: r4.e_eps_l2_day,
                e_eps_l1_all: r4.e_eps_l1_all,
                e_eps_l2_all: r4.e_eps_l2_all,
                i_eps_l1: r4.i_eps_l1,
                i_eps_l2: r4.i_eps_l2,
                pf_eps_l1: r4.pf_eps_l1,
                pf_eps_l2: r4.pf_eps_l2,
                f_eps_l1: r4.f_eps_l1,
                f_eps_l2: r4.f_eps_l2,
                datalog: r1.datalog,
                time: r1.time.clone(),
                bat_cell_count: r5.bat_cell_count,
                bat_parallel_count: r5.bat_parallel_count,
                under_freq_start: r5.under_freq_start,
                under_freq_end: r5.under_freq_end,
                under_freq_slope: r5.under_freq_slope,
                max_compensation: r5.max_compensation,
                chg_power_pct: r5.chg_power_pct,
                dischg_power_pct: r5.dischg_power_pct,
                ac_charge_pct: r5.ac_charge_pct,
                chg_priority_pct: r5.chg_priority_pct,
                forced_dischg_pct: r5.forced_dischg_pct,
                inv_power_pct: r5.inv_power_pct,
                ac_chg_start_v: r5.ac_chg_start_v,
                ac_chg_end_v: r5.ac_chg_end_v,
                ac_chg_start_soc: r5.ac_chg_start_soc,
                ac_chg_end_soc: r5.ac_chg_end_soc,
                bat_low_v: r5.bat_low_v,
                bat_low_back_v: r5.bat_low_back_v,
                bat_low_soc: r5.bat_low_soc,
                bat_low_back_soc: r5.bat_low_back_soc,
                bat_low_utility_v: r5.bat_low_utility_v,
                bat_low_utility_soc: r5.bat_low_utility_soc,
                ac_chg_curr: r5.ac_chg_curr,
                ongrid_eod_v: r5.ongrid_eod_v,
                soc_volt1: r5.soc_volt1,
                soc_volt2: r5.soc_volt2,
                soc_pct1: r5.soc_pct1,
                soc_pct2: r5.soc_pct2,
                soc_inner_resistance: r5.soc_inner_resistance,
                max_grid_input_power: r5.max_grid_input_power,
                gen_rated_power: r5.gen_rated_power,
                function_bit_flags: r5.function_bit_flags,
                afci_threshold: r5.afci_threshold,
                volt_watt_v1: r5.volt_watt_v1,
                volt_watt_v2: r5.volt_watt_v2,
                volt_watt_delay: r5.volt_watt_delay,
                volt_watt_p2: r5.volt_watt_p2,
                grid_voltage_high_pure: r6.grid_voltage_high_pure,
                grid_voltage_low_pure: r6.grid_voltage_low_pure,
                grid_freq_high_pure: r6.grid_freq_high_pure,
                grid_freq_low_pure: r6.grid_freq_low_pure,
                grid_volt_high_delay: r6.grid_volt_high_delay,
                grid_volt_low_delay: r6.grid_volt_low_delay,
                grid_freq_high_delay: r6.grid_freq_high_delay,
                grid_freq_low_delay: r6.grid_freq_low_delay,
                grid_volt_recover_high: r6.grid_volt_recover_high,
                grid_volt_recover_low: r6.grid_volt_recover_low,
                grid_freq_recover_high: r6.grid_freq_recover_high,
                grid_freq_recover_low: r6.grid_freq_recover_low,
                grid_volt_recover_delay: r6.grid_volt_recover_delay,
                grid_freq_recover_delay: r6.grid_freq_recover_delay,
                island_detect_time: r6.island_detect_time,
                pf_cmd_memory_en: r6.pf_cmd_memory_en,
                pf_cmd_memory_pf: r6.pf_cmd_memory_pf,
                pf_cmd_memory_p_ref: r6.pf_cmd_memory_p_ref,
                pf_cmd_memory_v_ref: r6.pf_cmd_memory_v_ref,
                pf_cmd_memory_q_ref: r6.pf_cmd_memory_q_ref,
            };

            // Calculate derived values
            if let Err(e) = all.calculate_derived_values() {
                error!("Failed to calculate derived values: {}", e);
                return None;
            }

            // Validate the result
            if let Err(e) = all.validate() {
                error!("Validation failed for ReadInputAll: {}", e);
                return None;
            }

            Some(all)
        } else {
            None
        }
    }
}
// }}}

// {{{ TcpFunction
#[derive(Clone, Copy, Debug, Eq, PartialEq, IntoPrimitive, TryFromPrimitive)]
#[repr(u8)]
pub enum TcpFunction {
    Heartbeat = 193,
    TranslatedData = 194,
    ReadParam = 195,
    WriteParam = 196,
}
// }}}

// {{{ DeviceFunction
#[derive(Debug, Clone, Copy, PartialEq, Eq, TryFromPrimitive, IntoPrimitive, Serialize)]
#[repr(u8)]
/// Modbus function codes used for device communication
pub enum DeviceFunction {
    /// Read holding registers (0x03)
    /// Used to read the contents of holding registers in the device
    ReadHold = 0x03,
    /// Read input registers (0x04)
    /// Used to read the contents of input registers in the device
    ReadInput = 0x04,
    /// Write single holding register (0x06)
    /// Used to write a single value to a holding register
    WriteSingle = 0x06,
    /// Write multiple holding registers (0x10)
    /// Used to write multiple values to holding registers
    WriteMulti = 0x10,
    /// Update prepare (0x21)
    /// Used to prepare for a firmware update
    UpdatePrepare = 0x21,
    /// Update send data (0x22)
    /// Used to send firmware update data
    UpdateSendData = 0x22,
    /// Update reset (0x23)
    /// Used to reset after firmware update
    UpdateReset = 0x23,
    /// Read holding registers error (0x83)
    /// Error response for read holding registers
    ReadHoldError = 0x83,
    /// Read input registers error (0x84)
    /// Error response for read input registers
    ReadInputError = 0x84,
    /// Write single holding register error (0x86)
    /// Error response for write single holding register
    WriteSingleError = 0x86,
    /// Write multiple holding registers error (0x90)
    /// Error response for write multiple holding registers
    WriteMultiError = 0x90,
}
// }}}

#[derive(Clone, Copy, Debug, Eq, PartialEq, IntoPrimitive, TryFromPrimitive)]
#[repr(u16)]
pub enum Register {
    /// System Information and Configuration
    /// Basic system identification and setup parameters
    Model = 0,                    // Model information including lithium type, power rating, etc.
    SerialNum = 2,               // Device serial number (spans registers 2-6)
    FirmwareCode = 7,            // Firmware version code
    Time = 12,                   // System time (spans registers 12-14)
                                // 12 = month (MSB) year (LSB, 2 digit)
                                // 13 = hour (MSB) day (LSB)
                                // 14 = second (MSB) minute (LSB)
    ComAddr = 15,                // Communication address
    Language = 16,               // System language setting
    DeviceType = 19,             // Device type identifier
    PvInputMode = 20,            // PV input mode configuration

    /// Grid Connection Timing
    ConnectTime = 23,            // Grid connection time
    ReconnectTime = 24,          // Grid reconnection time delay

    /// AutoTest Registers
    AutoTestStatus = 171,        // Status and step information for auto testing
    AutoTestLimit = 172,         // Voltage/Frequency limit for current test step
    AutoTestDefaultTime = 173,   // Default time in milliseconds
    AutoTestTripValue = 174,     // Trip value (0.1V/0.01Hz)
    AutoTestTripTime = 175,      // Trip time in milliseconds

    /// Grid Connection Limits
    /// These registers define the voltage and frequency ranges within which the inverter can connect to the grid.
    /// The inverter will only connect when both voltage and frequency are within these limits.
    StartPvVolt = 22,           // PV voltage threshold for inverter startup
    GridVoltConnLow = 25,        // Lower voltage limit for grid connection
    GridVoltConnHigh = 26,       // Upper voltage limit for grid connection
    GridFreqConnLow = 27,        // Lower frequency limit for grid connection
    GridFreqConnHigh = 28,       // Upper frequency limit for grid connection

    /// Grid Voltage Protection Level 1 (Mild Protection)
    /// First level of grid voltage protection for minor deviations.
    /// When grid voltage goes outside these limits for the specified time,
    /// the inverter may take corrective action while maintaining operation.
    GridVoltLimit1Low = 29,      // Level 1 lower voltage threshold
    GridVoltLimit1High = 30,     // Level 1 upper voltage threshold
    GridVoltLimit1LowTime = 31,  // Time delay before acting on low voltage (ms)
    GridVoltLimit1HighTime = 32, // Time delay before acting on high voltage (ms)

    /// Grid Voltage Protection Level 2 (Moderate Protection)
    /// Second level of protection for moderate voltage deviations.
    /// Triggers more aggressive protective measures than Level 1.
    /// May reduce power output or temporarily disconnect.
    GridVoltLimit2Low = 33,      // Level 2 lower voltage threshold
    GridVoltLimit2High = 34,     // Level 2 upper voltage threshold
    GridVoltLimit2LowTime = 35,  // Time delay before acting on low voltage (ms)
    GridVoltLimit2HighTime = 36, // Time delay before acting on high voltage (ms)

    /// Grid Voltage Protection Level 3 (Severe Protection)
    /// Highest level of protection for severe voltage deviations.
    /// Will likely result in immediate disconnection from the grid
    /// to protect the inverter and connected equipment.
    GridVoltLimit3Low = 37,      // Level 3 lower voltage threshold
    GridVoltLimit3High = 38,     // Level 3 upper voltage threshold
    GridVoltLimit3LowTime = 39,  // Time delay before acting on low voltage (ms)
    GridVoltLimit3HighTime = 40, // Time delay before acting on high voltage (ms)

    /// Additional Grid Voltage Protection
    /// Moving average high voltage limit provides protection against
    /// sustained high voltage conditions that might not trigger the
    /// instantaneous protection levels.
    GridVoltMovAvgHigh = 41,     // Moving average high voltage limit

    /// Grid Frequency Protection Level 1 (Mild Protection)
    /// First level of grid frequency protection for minor deviations.
    /// When grid frequency goes outside these limits for the specified time,
    /// the inverter may take corrective action while maintaining operation.
    GridFreqLimit1Low = 42,      // Level 1 lower frequency threshold
    GridFreqLimit1High = 43,     // Level 1 upper frequency threshold
    GridFreqLimit1LowTime = 44,  // Time delay before acting on low frequency (ms)
    GridFreqLimit1HighTime = 45, // Time delay before acting on high frequency (ms)

    /// Grid Frequency Protection Level 2 (Moderate Protection)
    /// Second level of protection for moderate frequency deviations.
    /// Triggers more aggressive protective measures than Level 1.
    /// May reduce power output or temporarily disconnect.
    GridFreqLimit2Low = 46,      // Level 2 lower frequency threshold
    GridFreqLimit2High = 47,     // Level 2 upper frequency threshold
    GridFreqLimit2LowTime = 48,  // Time delay before acting on low frequency (ms)
    GridFreqLimit2HighTime = 49, // Time delay before acting on high frequency (ms)

    /// Grid Frequency Protection Level 3 (Severe Protection)
    /// Highest level of protection for severe frequency deviations.
    /// Will likely result in immediate disconnection from the grid
    /// to protect the inverter and connected equipment.
    GridFreqLimit3Low = 50,      // Level 3 lower frequency threshold
    GridFreqLimit3High = 51,     // Level 3 upper frequency threshold
    GridFreqLimit3LowTime = 52,  // Time delay before acting on low frequency (ms)
    GridFreqLimit3HighTime = 53, // Time delay before acting on high frequency (ms)

    /// Power Quality Control
    /// Settings for reactive power control and power quality management
    MaxQPercentForQv = 54,       // Maximum reactive power percentage for Q(V) control
    V1L = 55,                    // Lower voltage threshold 1 for Q(V) curve
    V2L = 56,                    // Lower voltage threshold 2 for Q(V) curve
    V1H = 57,                    // Upper voltage threshold 1 for Q(V) curve
    V2H = 58,                    // Upper voltage threshold 2 for Q(V) curve
    ReactivePowerCmdType = 59,   // Reactive power command type
    ActivePowerPercentCmd = 60,  // Active power percentage command
    ReactivePowerPercentCmd = 61,// Reactive power percentage command
    PfCmd = 62,                 // Power factor command
    PowerSoftStartSlope = 63,   // Power soft start slope

    // ... existing registers ...
    Register21 = 21,             // Function enable/disable flags
    ChargePowerPercentCmd = 64,  // System Charge Rate (%)
    DischgPowerPercentCmd = 65,  // System Discharge Rate (%)
    AcChargePowerCmd = 66,       // Grid Charge Power Rate (%)
    AcChargeSocLimit = 67,       // AC Charge SOC Limit (%)
    ChargePriorityPowerCmd = 74, // Charge Priority Charge Rate (%)
    ChargePrioritySocLimit = 75, // Charge Priority SOC Limit (%)
    ForcedDischgSocLimit = 83,   // Forced Discharge SOC Limit (%)
    DischgCutOffSocEod = 105,    // Discharge cut-off SOC (%)
    EpsDischgCutoffSocEod = 125, // EPS Discharge cut-off SOC (%)
    AcChargeStartSocLimit = 160, // SOC at which AC charging will begin (%)
    AcChargeEndSocLimit = 161,   // SOC at which AC charging will end (%)
}

#[derive(Clone, Debug, Eq, PartialEq, IntoPrimitive, TryFromPrimitive)]
#[repr(u16)]
pub enum RegisterBit {
    // Register 21
    AcChargeEnable = 1 << 7,
    ForcedDischargeEnable = 1 << 10,
    ChargePriorityEnable = 1 << 11,
}

// Register21Bits {{{
#[derive(Clone, Debug, Serialize)]
pub struct Register21Bits {
    pub eps_en: String,
    pub ovf_load_derate_en: String,
    pub drms_en: String,
    pub lvrt_en: String,
    pub anti_island_en: String,
    pub neutral_detect_en: String,
    pub grid_on_power_ss_en: String,
    pub ac_charge_en: String,
    pub sw_seamless_en: String,
    pub set_to_standby: String,
    pub forced_discharge_en: String,
    pub charge_priority_en: String,
    pub iso_en: String,
    pub gfci_en: String,
    pub dci_en: String,
    pub feed_in_grid_en: String,
}

impl Register21Bits {
    fn is_bit_set(data: u16, bit: u16) -> String {
        if (data & bit) == bit {
            "ON".to_string()
        } else {
            "OFF".to_string()
        }
    }

    pub fn new(data: u16) -> Self {
        Self {
            eps_en: Self::is_bit_set(data, 1 << 0),
            ovf_load_derate_en: Self::is_bit_set(data, 1 << 1),
            drms_en: Self::is_bit_set(data, 1 << 2),
            lvrt_en: Self::is_bit_set(data, 1 << 3),
            anti_island_en: Self::is_bit_set(data, 1 << 4),
            neutral_detect_en: Self::is_bit_set(data, 1 << 5),
            grid_on_power_ss_en: Self::is_bit_set(data, 1 << 6),
            ac_charge_en: Self::is_bit_set(data, 1 << 7),
            sw_seamless_en: Self::is_bit_set(data, 1 << 8),
            set_to_standby: Self::is_bit_set(data, 1 << 9),
            forced_discharge_en: Self::is_bit_set(data, 1 << 10),
            charge_priority_en: Self::is_bit_set(data, 1 << 11),
            iso_en: Self::is_bit_set(data, 1 << 12),
            gfci_en: Self::is_bit_set(data, 1 << 13),
            dci_en: Self::is_bit_set(data, 1 << 14),
            feed_in_grid_en: Self::is_bit_set(data, 1 << 15),
        }
    }
}
// }}}

// Register110Bits {{{
#[derive(Clone, Debug, Serialize)]
pub struct Register110Bits {
    pub ub_pv_grid_off_en: String,
    pub ub_run_without_grid: String,
    pub ub_micro_grid_en: String,
}
impl Register110Bits {
    fn is_bit_set(data: u16, bit: u16) -> String {
        if (data & bit) == bit {
            "ON".to_string()
        } else {
            "OFF".to_string()
        }
    }

    pub fn new(data: u16) -> Self {
        Self {
            ub_pv_grid_off_en: Self::is_bit_set(data, 1 << 0),
            ub_run_without_grid: Self::is_bit_set(data, 1 << 1),
            ub_micro_grid_en: Self::is_bit_set(data, 1 << 2),
        }
    }
}
// }}}

#[enum_dispatch]
pub trait PacketCommon {
    fn datalog(&self) -> Serial;
    fn set_datalog(&mut self, datalog: Serial);
    fn inverter(&self) -> Option<Serial>;
    fn set_inverter(&mut self, serial: Serial);
    fn protocol(&self) -> u16;
    fn tcp_function(&self) -> TcpFunction;
    fn bytes(&self) -> Vec<u8>;

    fn register(&self) -> u16 {
        unimplemented!("register() not implemented");
    }
    fn value(&self) -> u16 {
        unimplemented!("value() not implemented");
    }
}

pub struct TcpFrameFactory {
    datalog: Serial,
}

impl TcpFrameFactory {
    pub fn new(datalog: Serial) -> Self {
        Self { datalog }
    }

    pub fn create_frame(&self, packet: &Packet) -> Result<Vec<u8>> {
        let data_bytes = packet.bytes();
        let mut frame = Vec::new();
        
        // Add datalog
        frame.extend_from_slice(&self.datalog.data());
        
        // Add packet type
        frame.push(match packet {
            Packet::TranslatedData(_) => 0x01,
            Packet::ReadParam(_) => 0x02,
            Packet::WriteParam(_) => 0x03,
            Packet::Heartbeat(_) => 0x04,
        });
        
        // Add data length (2 bytes)
        let len = data_bytes.len() as u16;
        frame.extend_from_slice(&len.to_be_bytes());
        
        // Add data
        frame.extend_from_slice(&data_bytes);
        
        Ok(frame)
    }
}

#[enum_dispatch(PacketCommon)]
#[derive(Eq, PartialEq, Debug, Clone)]
pub enum Packet {
    Heartbeat(Heartbeat),
    TranslatedData(TranslatedData),
    ReadParam(ReadParam),
    WriteParam(WriteParam),
}

#[derive(PartialEq)]
enum PacketSource {
    Inverter,
    Client,
}

/////////////
//
// HEARTBEATS
//
/////////////

#[derive(Eq, PartialEq, Clone, Debug)]
pub struct Heartbeat {
    pub datalog: Serial,
}
impl Heartbeat {
    fn decode(input: &[u8]) -> Result<Self> {
        let len = input.len();
        if len < 19 {
            bail!("heartbeat packet too short");
        }

        // assert that the final byte is 0, meaning 0 data bytes follow it
        if input[18] != 0 {
            bail!("heartbeat with non-zero ({}) length byte?", input[18]);
        }

        let datalog = Serial::new(&input[8..18])?;

        Ok(Self { datalog })
    }
}

impl PacketCommon for Heartbeat {
    fn protocol(&self) -> u16 {
        2
    }

    fn datalog(&self) -> Serial {
        self.datalog
    }
    fn set_datalog(&mut self, datalog: Serial) {
        self.datalog = datalog;
    }
    fn inverter(&self) -> Option<Serial> {
        None
    }
    fn set_inverter(&mut self, _datalog: Serial) {}

    fn tcp_function(&self) -> TcpFunction {
        TcpFunction::Heartbeat
    }

    fn bytes(&self) -> Vec<u8> {
        vec![0]
    }
}

/////////////
//
// TRANSLATED DATA
//
/////////////

#[derive(Eq, PartialEq, Clone, Debug, Serialize)]
pub struct TranslatedData {
    pub datalog: Serial,
    pub device_function: DeviceFunction, // ReadHold or ReadInput etc..
    pub inverter: Serial,                // inverter serial
    pub register: u16,                   // first register of values
    pub values: Vec<u8>,                 // undecoded, since can be u16 or u32s?
}
impl TranslatedData {
    pub fn pairs(&self) -> Vec<(u16, u16)> {
        self.values
            .chunks(2)
            .enumerate()
            .map(|(pos, value)| (self.register + pos as u16, Utils::u16ify(value, 0)))
            .collect()
    }

    pub fn read_input(&self) -> Result<ReadInput> {
        // Validate minimum value size before processing
        if self.values.len() < MIN_INPUT_BLOCK_SIZE {
            bail!("Input values too short: {} bytes", self.values.len());
        }

        match (self.register, self.values.len()) {
            (0, len) if len == MIN_INPUT_ALL_SIZE => Ok(ReadInput::ReadInputAll(Box::new(self.read_input_all()?))),
            (0, len) if len <= MAX_INPUT_BLOCK_SIZE => Ok(ReadInput::ReadInput1(self.read_input1()?)),
            (40, len) if len <= MAX_INPUT_BLOCK_SIZE => Ok(ReadInput::ReadInput2(self.read_input2()?)),
            (80, len) if len <= MAX_INPUT_BLOCK_SIZE => Ok(ReadInput::ReadInput3(self.read_input3()?)),
            (120, len) if len <= MAX_INPUT_BLOCK_SIZE => Ok(ReadInput::ReadInput4(self.read_input4()?)),
            (160, len) if len <= MAX_INPUT_BLOCK_SIZE => Ok(ReadInput::ReadInput5(self.read_input5()?)),
            (200, len) if len <= MAX_INPUT_BLOCK_SIZE => Ok(ReadInput::ReadInput6(self.read_input6()?)),
            (r1, r2) => {
                let valid_registers = vec![0, 40, 80, 120, 160, 200];
                let register_info = if valid_registers.contains(&r1) {
                    format!("Register {} is valid, but received incorrect length", r1)
                } else {
                    format!("Register {} is invalid. Valid registers are: {:?}", r1, valid_registers)
                };
                
                bail!("Invalid ReadInput block: {} - received length={}, expected between {} and {} bytes or {}. \
                      Each input block should start at a multiple of 40 up to 200.", 
                      register_info, r2, MIN_INPUT_BLOCK_SIZE, MAX_INPUT_BLOCK_SIZE, MIN_INPUT_ALL_SIZE)
            }
        }
    }

    fn read_input_all(&self) -> Result<ReadInputAll> {
        match ReadInputAll::parse(&self.values) {
            Ok((_, mut r)) => {
                r.p_pv = u16::from(r.p_pv_1)
                    .checked_add(u16::from(r.p_pv_2))
                    .and_then(|sum| sum.checked_add(r.p_pv_3))
                    .ok_or_else(|| anyhow!("Power value overflow in p_pv calculation"))?;
                r.p_grid = i32::from(r.p_to_user)
                    .checked_sub(i32::from(r.p_to_grid))
                    .ok_or_else(|| anyhow!("Power value overflow in p_grid calculation"))?;
                r.p_battery = i32::from(r.p_charge)
                    .checked_sub(i32::from(r.p_discharge))
                    .ok_or_else(|| anyhow!("Power value overflow in p_battery calculation"))?;
                r.e_pv_day = Utils::round(r.e_pv_day_1 + r.e_pv_day_2 + r.e_pv_day_3, 1);
                r.e_pv_all = Utils::round(r.e_pv_all_1 + r.e_pv_all_2 + r.e_pv_all_3, 1);
                r.datalog = self.datalog;
                Ok(r)
            }
            Err(e) => Err(anyhow!("Failed to parse ReadInputAll packet: {:?}. This may indicate corrupted or incomplete data from the inverter", e)),
        }
    }

    fn read_input1(&self) -> Result<ReadInput1> {
        match ReadInput1::parse(&self.values) {
            Ok((_, mut r)) => {
                r.p_pv = u16::from(r.p_pv_1)
                    .checked_add(u16::from(r.p_pv_2))
                    .and_then(|sum| sum.checked_add(r.p_pv_3))
                    .ok_or_else(|| anyhow!("Power value overflow in p_pv calculation"))?;
                r.p_grid = i32::from(r.p_to_user)
                    .checked_sub(i32::from(r.p_to_grid))
                    .ok_or_else(|| anyhow!("Power value overflow in p_grid calculation"))?;
                r.p_battery = i32::from(r.p_charge)
                    .checked_sub(i32::from(r.p_discharge))
                    .ok_or_else(|| anyhow!("Power value overflow in p_battery calculation"))?;
                r.e_pv_day = Utils::round(r.e_pv_day_1 + r.e_pv_day_2 + r.e_pv_day_3, 1);
                r.datalog = self.datalog;
                Ok(r)
            }
            Err(e) => Err(anyhow!("Failed to parse ReadInput1 packet: {:?}. This may indicate corrupted or incomplete data from the inverter", e)),
        }
    }

    fn read_input2(&self) -> Result<ReadInput2> {
        // Validate minimum size for ReadInput2 including bat_brand and bat_com_type
        let min_size = 80;  // Minimum size for ReadInput2 block
        if self.values.len() < min_size {
            return Err(anyhow!(
                "ReadInput2 data too short: {} bytes (minimum required: {}). Cannot read bat_brand and bat_com_type",
                self.values.len(),
                min_size
            ));
        }

        match ReadInput2::parse(&self.values) {
            Ok((_, mut r)) => {
                // Log the raw values for debugging
                debug!("ReadInput2 bat_brand: {}, bat_com_type: {}", r.bat_brand, r.bat_com_type);
                
                // Validate battery brand and communication type
                if r.bat_brand == 0 {
                    warn!("ReadInput2: bat_brand is 0, this may indicate missing or invalid data");
                }
                if r.bat_com_type == 0 {
                    warn!("ReadInput2: bat_com_type is 0, this may indicate missing or invalid data");
                }

                // Validate battery brand value
                match r.bat_brand {
                    0 => warn!("ReadInput2: bat_brand is Unknown/Not configured"),
                    1 => debug!("ReadInput2: bat_brand is Pylon"),
                    2 => debug!("ReadInput2: bat_brand is Dyness"),
                    unknown => warn!("ReadInput2: Unknown bat_brand value: {}", unknown),
                }

                // Validate battery communication type
                match r.bat_com_type {
                    0 => warn!("ReadInput2: bat_com_type is Unknown/Not configured"),
                    1 => debug!("ReadInput2: bat_com_type is RS485"),
                    2 => debug!("ReadInput2: bat_com_type is CAN"),
                    unknown => warn!("ReadInput2: Unknown bat_com_type value: {}", unknown),
                }
                
                r.e_pv_all = Utils::round(r.e_pv_all_1 + r.e_pv_all_2 + r.e_pv_all_3, 1);
                r.datalog = self.datalog;
                Ok(r)
            }
            Err(e) => {
                // Provide more detailed error information
                let error_msg = format!(
                    "Failed to parse ReadInput2: {:?}. Values length: {}, Raw values: {:?}",
                    e,
                    self.values.len(),
                    self.values
                );
                error!("{}", error_msg);
                Err(anyhow!(error_msg))
            }
        }
    }

    fn read_input3(&self) -> Result<ReadInput3> {
        match ReadInput3::parse(&self.values) {
            Ok((_, mut r)) => {
                r.datalog = self.datalog;
                Ok(r)
            }
            Err(e) => Err(anyhow!("Failed to parse ReadInput3 packet: {:?}. This may indicate corrupted or incomplete data from the inverter", e)),
        }
    }

    fn read_input4(&self) -> Result<ReadInput4> {
        match ReadInput4::parse(&self.values) {
            Ok((_, mut r)) => {
                r.datalog = self.datalog;
                Ok(r)
            }
            Err(e) => Err(anyhow!("Failed to parse ReadInput4 packet: {:?}. This may indicate corrupted or incomplete data from the inverter", e)),
        }
    }

    fn read_input5(&self) -> Result<ReadInput5> {
        match ReadInput5::parse(&self.values) {
            Ok((_, mut r)) => {
                r.datalog = self.datalog;
                Ok(r)
            }
            Err(_) => Err(anyhow!("Failed to parse ReadInput5")),
        }
    }

    fn read_input6(&self) -> Result<ReadInput6> {
        let mut input = ReadInput6 {
            grid_voltage_high_pure: 0,
            grid_voltage_low_pure: 0,
            grid_freq_high_pure: 0,
            grid_freq_low_pure: 0,
            grid_volt_high_delay: 0,
            grid_volt_low_delay: 0,
            grid_freq_high_delay: 0,
            grid_freq_low_delay: 0,
            grid_volt_recover_high: 0,
            grid_volt_recover_low: 0,
            grid_freq_recover_high: 0,
            grid_freq_recover_low: 0,
            grid_volt_recover_delay: 0,
            grid_freq_recover_delay: 0,
            island_detect_time: 0,
            pf_cmd_memory_en: 0,
            pf_cmd_memory_pf: 0,
            pf_cmd_memory_p_ref: 0,
            pf_cmd_memory_v_ref: 0,
            pf_cmd_memory_q_ref: 0,
            time: UnixTime::now(),
            datalog: self.datalog,
        };

        let pairs = self.pairs();
        for (i, (_, value)) in pairs.iter().enumerate() {
            match i {
                0 => input.grid_voltage_high_pure = *value,
                1 => input.grid_voltage_low_pure = *value,
                2 => input.grid_freq_high_pure = *value,
                3 => input.grid_freq_low_pure = *value,
                4 => input.grid_volt_high_delay = *value,
                5 => input.grid_volt_low_delay = *value,
                6 => input.grid_freq_high_delay = *value,
                7 => input.grid_freq_low_delay = *value,
                8 => input.grid_volt_recover_high = *value,
                9 => input.grid_volt_recover_low = *value,
                10 => input.grid_freq_recover_high = *value,
                11 => input.grid_freq_recover_low = *value,
                12 => input.grid_volt_recover_delay = *value,
                13 => input.grid_freq_recover_delay = *value,
                14 => input.island_detect_time = *value,
                15 => input.pf_cmd_memory_en = *value,
                16 => input.pf_cmd_memory_pf = *value,
                17 => input.pf_cmd_memory_p_ref = *value,
                18 => input.pf_cmd_memory_v_ref = *value,
                19 => input.pf_cmd_memory_q_ref = *value,
                _ => {}
            }
        }

        Ok(input)
    }

    fn decode(input: &[u8]) -> Result<Self> {
        let len = input.len();
        if len < MIN_TRANSLATED_DATA_SIZE {
            bail!("TranslatedData::decode packet too short: {} bytes (minimum {})", 
                len, MIN_TRANSLATED_DATA_SIZE);
        }

        let protocol = Utils::u16ify(input, 2);
        let datalog = Serial::new(&input[8..18])?;

        let data = &input[20..len - 2];

        // Validate data length
        if data.len() < 14 { // Minimum data size: address(1) + function(1) + serial(10) + register(2)
            bail!("TranslatedData::decode data section too short: {} bytes", data.len());
        }

        let checksum = &input[len - 2..];
        if Self::checksum(data) != checksum {
            bail!(
                "TranslatedData::decode checksum mismatch - got {:?}, expected {:?}",
                checksum,
                Self::checksum(data)
            );
        }

        //let address = data[0]; // 0=client, 1=inverter?
        let device_function = DeviceFunction::try_from(data[1])?;
        let inverter = Serial::new(&data[2..12])?;
        let register = Utils::u16ify(data, 12);

        let mut value_len = 2;
        let mut value_offset = 14;

        if Self::has_value_length_byte(PacketSource::Inverter, protocol, device_function) {
            value_len = data[value_offset] as usize;
            value_offset += 1;
        }

        let values = data[value_offset..].to_vec();

        if values.len() != value_len {
            bail!(
                "TranslatedData::decode mismatch: values.len()={}, value_length_byte={}",
                values.len(),
                value_len
            );
        }

        Ok(Self {
            datalog,
            device_function,
            inverter,
            register,
            values,
        })
    }

    fn has_value_length_byte(
        source: PacketSource,
        protocol: u16,
        device_function: DeviceFunction,
    ) -> bool {
        let p1 = protocol == 1;
        let psi = source == PacketSource::Inverter;
        match device_function {
            DeviceFunction::ReadHold | DeviceFunction::ReadInput => !p1 && psi,
            DeviceFunction::WriteSingle => false,
            DeviceFunction::WriteMulti => !p1 && !psi,
            DeviceFunction::UpdatePrepare | DeviceFunction::UpdateSendData | DeviceFunction::UpdateReset => false,
            DeviceFunction::ReadHoldError | DeviceFunction::ReadInputError | DeviceFunction::WriteSingleError | DeviceFunction::WriteMultiError => false,
        }
    }

    fn checksum(data: &[u8]) -> [u8; 2] {
        crc16::State::<crc16::MODBUS>::calculate(data).to_le_bytes()
    }
}

impl PacketCommon for TranslatedData {
    fn protocol(&self) -> u16 {
        if self.device_function == DeviceFunction::WriteMulti {
            2
        } else {
            1
        }
    }

    fn datalog(&self) -> Serial {
        self.datalog
    }
    fn set_datalog(&mut self, datalog: Serial) {
        self.datalog = datalog;
    }

    fn inverter(&self) -> Option<Serial> {
        Some(self.inverter)
    }
    fn set_inverter(&mut self, serial: Serial) {
        self.inverter = serial;
    }

    fn tcp_function(&self) -> TcpFunction {
        TcpFunction::TranslatedData
    }

    fn bytes(&self) -> Vec<u8> {
        let mut data = vec![0; 16];

        // data[2] (address) is 0 when writing to inverter, 1 when reading from it
        data[3] = self.device_function as u8;

        // experimental: looks like maybe you don't need to fill this in..
        data[4..14].copy_from_slice(&self.inverter.data());
        //data[4..14].copy_from_slice(&[0; 10]);

        data[14..16].copy_from_slice(&self.register.to_le_bytes());

        if self.device_function == DeviceFunction::WriteMulti {
            let register_count = self.pairs().len() as u16;
            data.extend_from_slice(&register_count.to_le_bytes());
        }

        if Self::has_value_length_byte(PacketSource::Client, self.protocol(), self.device_function)
        {
            let len = self.values.len() as u8;
            data.extend_from_slice(&[len]);
        }

        let mut m = Vec::new();
        for i in &self.values {
            m.extend_from_slice(&i.to_le_bytes());
        }
        data.append(&mut m);

        // the first two bytes are the data length, excluding checksum which we'll add next
        let data_length = data.len() as u16;
        data[0..2].copy_from_slice(&data_length.to_le_bytes());

        // checksum does not include the first two bytes (data length)
        data.extend_from_slice(&Self::checksum(&data[2..]));

        data
    }

    fn register(&self) -> u16 {
        self.register
    }

    fn value(&self) -> u16 {
        Utils::u16ify(&self.values, 0)
    }
}

/////////////
//
// READ PARAM
//
/////////////

#[derive(Eq, PartialEq, Clone, Debug)]
pub struct ReadParam {
    pub datalog: Serial,
    pub register: u16,   // first register of values
    pub values: Vec<u8>, // undecoded, since can be u16 or i32s?
}
impl ReadParam {
    pub fn pairs(&self) -> Vec<(u16, u16)> {
        self.values
            .chunks(2)
            .enumerate()
            .map(|(pos, value)| (self.register + pos as u16, Utils::u16ify(value, 0)))
            .collect()
    }

    fn decode(input: &[u8]) -> Result<Self> {
        let len = input.len();
        if len < 24 {
            bail!("ReadParam::decode packet too short");
        }

        let protocol = Utils::u16ify(input, 2);
        let datalog = Serial::new(&input[8..18])?;

        let data = &input[18..];
        let register = Utils::u16ify(data, 0);

        let mut value_len = 2;
        let mut value_offset = 2;

        if Self::has_value_length_bytes(protocol) {
            value_len = Utils::u16ify(data, value_offset) as usize;
            value_offset += 2;
        }

        let values = data[value_offset..].to_vec();

        if values.len() != value_len {
            bail!(
                "ReadParam::decode mismatch: values.len()={}, value_length_byte={}",
                values.len(),
                value_len
            );
        }

        Ok(Self {
            datalog,
            register,
            values,
        })
    }

    fn has_value_length_bytes(protocol: u16) -> bool {
        protocol == 2
    }
}

impl PacketCommon for ReadParam {
    fn protocol(&self) -> u16 {
        2
    }

    fn datalog(&self) -> Serial {
        self.datalog
    }
    fn set_datalog(&mut self, datalog: Serial) {
        self.datalog = datalog;
    }
    fn inverter(&self) -> Option<Serial> {
        None
    }
    fn set_inverter(&mut self, _datalog: Serial) {}

    fn tcp_function(&self) -> TcpFunction {
        TcpFunction::ReadParam
    }

    fn bytes(&self) -> Vec<u8> {
        vec![self.register() as u8, 0]
    }

    fn register(&self) -> u16 {
        self.register
    }

    fn value(&self) -> u16 {
        Utils::u16ify(&self.values, 0)
    }
}

/////////////
//
// WRITE PARAM
//
/////////////

#[derive(Eq, PartialEq, Clone, Debug)]
pub struct WriteParam {
    pub datalog: Serial,
    pub register: u16,   // first register of values
    pub values: Vec<u8>, // undecoded, since can be u16 or i32s?
}
impl WriteParam {
    pub fn pairs(&self) -> Vec<(u16, u16)> {
        self.values
            .chunks(2)
            .enumerate()
            .map(|(pos, value)| (self.register + pos as u16, Utils::u16ify(value, 0)))
            .collect()
    }

    fn decode(input: &[u8]) -> Result<Self> {
        let len = input.len();
        if len < 21 {
            bail!("WriteParam::decode packet too short");
        }

        let protocol = Utils::u16ify(input, 2);
        let datalog = Serial::new(&input[8..18])?;

        let data = &input[18..];
        let register = u16::from(data[0]);

        let mut value_len = 2;
        let mut value_offset = 1;

        if Self::has_value_length_bytes(protocol) {
            value_len = Utils::u16ify(data, value_offset) as usize;
            value_offset += 2;
        }

        let values = data[value_offset..].to_vec();

        if values.len() != value_len {
            bail!(
                "WriteParam::decode mismatch: values.len()={}, value_length_byte={}",
                values.len(),
                value_len
            );
        }

        Ok(Self {
            datalog,
            register,
            values,
        })
    }

    fn has_value_length_bytes(_protocol: u16) -> bool {
        false
    }
}

impl PacketCommon for WriteParam {
    fn protocol(&self) -> u16 {
        2
    }

    fn datalog(&self) -> Serial {
        self.datalog
    }
    fn set_datalog(&mut self, datalog: Serial) {
        self.datalog = datalog;
    }
    fn inverter(&self) -> Option<Serial> {
        None
    }
    fn set_inverter(&mut self, _datalog: Serial) {}

    fn tcp_function(&self) -> TcpFunction {
        TcpFunction::WriteParam
    }

    fn bytes(&self) -> Vec<u8> {
        let mut data = vec![0; 2];

        data[0..2].copy_from_slice(&self.register.to_le_bytes());

        let len = self.values.len() as u16;
        data.extend_from_slice(&len.to_le_bytes());

        let mut m = Vec::new();
        for i in &self.values {
            m.extend_from_slice(&i.to_le_bytes());
        }
        data.append(&mut m);

        data
    }

    fn register(&self) -> u16 {
        self.register
    }

    fn value(&self) -> u16 {
        Utils::u16ify(&self.values, 0)
    }
}

pub struct Parser;
impl Parser {
    pub fn parse(input: &[u8]) -> Result<Packet> {
        let input_len = input.len() as u8;
        if input_len < 18 {
            bail!("packet less than 18 bytes?");
        }

        if input[0..2] != [161, 26] {
            bail!("invalid packet prefix");
        }

        if input_len < input[4] - 6 {
            bail!(
                "Parser::parse mismatch: input.len()={},  frame_length={}",
                input_len,
                input[4] - 6
            );
        }

        let r = match TcpFunction::try_from(input[7])? {
            TcpFunction::Heartbeat => Packet::Heartbeat(Heartbeat::decode(input)?),
            TcpFunction::TranslatedData => Packet::TranslatedData(TranslatedData::decode(input)?),
            TcpFunction::ReadParam => Packet::ReadParam(ReadParam::decode(input)?),
            TcpFunction::WriteParam => Packet::WriteParam(WriteParam::decode(input)?),
            //_ => bail!("unhandled: tcp_function={} input={:?}", input[7], input),
        };

        Ok(r)
    }
}

pub struct StatusString;
impl StatusString {
    pub fn from_value(status: u16) -> &'static str {
        match status {
            0x00 => "Standby",
            0x02 => "FW Updating",
            0x04 => "PV On-grid",
            0x08 => "PV Charge",
            0x0C => "PV Charge On-grid",
            0x10 => "Battery On-grid",
            0x11 => "Bypass",
            0x14 => "PV & Battery On-grid",
            0x19 => "PV Charge + Bypass",
            0x20 => "AC Charge",
            0x28 => "PV & AC Charge",
            0x40 => "Battery Off-grid",
            0x80 => "PV Off-grid",
            0xC0 => "PV & Battery Off-grid",
            0x88 => "PV Charge Off-grid",

            _ => "Unknown",
        }
    }
}

pub struct WarningCodeString;
impl WarningCodeString {
    pub fn from_value(value: u32) -> &'static str {
        if value == 0 {
            return "OK";
        }

        (0..=31)
            .find(|i| value & (1 << i) > 0)
            .map(Self::from_bit)
            .unwrap()
    }

    fn from_bit(bit: usize) -> &'static str {
        match bit {
            0 => "W000: Battery communication failure",
            1 => "W001: AFCI communication failure",
            2 => "W002: AFCI high",
            3 => "W003: Meter communication failure",
            4 => "W004: Both charge and discharge forbidden by battery",
            5 => "W005: Auto test failed",
            6 => "W006: Reserved",
            7 => "W007: LCD communication failure",
            8 => "W008: FW version mismatch",
            9 => "W009: Fan stuck",
            10 => "W010: Reserved",
            11 => "W011: Parallel number out of range",
            12 => "W012: Bat On Mos",
            13 => "W013: Overtemperature (NTC reading is too high)",
            14 => "W014: Reserved",
            15 => "W015: Battery reverse connection",
            16 => "W016: Grid power outage",
            17 => "W017: Grid voltage out of range",
            18 => "W018: Grid frequency out of range",
            19 => "W019: Reserved",
            20 => "W020: PV insulation low",
            21 => "W021: Leakage current high",
            22 => "W022: DCI high",
            23 => "W023: PV short",
            24 => "W024: Reserved",
            25 => "W025: Battery voltage high",
            26 => "W026: Battery voltage low",
            27 => "W027: Battery open circuit",
            28 => "W028: EPS overload",
            29 => "W029: EPS voltage high",
            30 => "W030: Meter reverse connection",
            31 => "W031: DCV high",

            _ => todo!("Unknown Warning"),
        }
    }
}

pub struct FaultCodeString;
impl FaultCodeString {
    pub fn from_value(value: u32) -> &'static str {
        if value == 0 {
            return "OK";
        }

        (0..=31)
            .find(|i| value & (1 << i) > 0)
            .map(Self::from_bit)
            .unwrap()
    }

    fn from_bit(bit: usize) -> &'static str {
        match bit {
            0 => "E000: Internal communication fault 1",
            1 => "E001: Model fault",
            2 => "E002: BatOnMosFail",
            3 => "E003: CT Fail",
            4 => "E004: Reserved",
            5 => "E005: Reserved",
            6 => "E006: Reserved",
            7 => "E007: Reserved",
            8 => "E008: CAN communication error in parallel system",
            9 => "E009: master lost in parallel system",
            10 => "E010: multiple master units in parallel system",
            11 => "E011: AC input inconsistent in parallel system",
            12 => "E012: UPS short",
            13 => "E013: Reverse current on UPS output",
            14 => "E014: Bus short",
            15 => "E015: Phase error in three phase system",
            16 => "E016: Relay check fault",
            17 => "E017: Internal communication fault 2",
            18 => "E018: Internal communication fault 3",
            19 => "E019: Bus voltage high",
            20 => "E020: EPS connection fault",
            21 => "E021: PV voltage high",
            22 => "E022: Over current protection",
            23 => "E023: Neutral fault",
            24 => "E024: PV short",
            25 => "E025: Radiator temperature over range",
            26 => "E026: Internal fault",
            27 => "E027: Sample inconsistent between Main CPU and redundant CPU",
            28 => "E028: Reserved",
            29 => "E029: Reserved",
            30 => "E030: Reserved",
            31 => "E031: Internal communication fault 4",
            _ => todo!("Unknown Fault"),
        }
    }
}

pub struct BatteryStatusString;
impl BatteryStatusString {
    pub fn decode_status_9(value: u16) -> Vec<&'static str> {
        let mut statuses = Vec::new();
        
        // Decode each bit of bat_status_9
        if value & (1 << 0) != 0 { statuses.push("Cell Balancing Active"); }
        if value & (1 << 1) != 0 { statuses.push("Charging Enabled"); }
        if value & (1 << 2) != 0 { statuses.push("Discharging Enabled"); }
        if value & (1 << 3) != 0 { statuses.push("Heating Active"); }
        if value & (1 << 4) != 0 { statuses.push("Battery Full"); }
        if value & (1 << 5) != 0 { statuses.push("Battery Empty"); }
        if value & (1 << 6) != 0 { statuses.push("SOC Calibration Active"); }
        if value & (1 << 7) != 0 { statuses.push("BMS Fault Present"); }
        if value & (1 << 8) != 0 { statuses.push("BMS Warning Present"); }
        if value & (1 << 9) != 0 { statuses.push("Protection Active"); }
        if value & (1 << 10) != 0 { statuses.push("Battery Disconnected"); }
        if value & (1 << 11) != 0 { statuses.push("Battery Sleeping"); }
        if value & (1 << 12) != 0 { statuses.push("Battery Standby"); }
        if value & (1 << 13) != 0 { statuses.push("Battery Power Save"); }
        if value & (1 << 14) != 0 { statuses.push("Battery Self Testing"); }
        if value & (1 << 15) != 0 { statuses.push("Battery Fault"); }

        if statuses.is_empty() {
            statuses.push("No Status Flags Set");
        }
        
        statuses
    }

    pub fn decode_status_inv(value: u16) -> Vec<&'static str> {
        let mut statuses = Vec::new();
        
        // Decode each bit of bat_status_inv
        if value & (1 << 0) != 0 { statuses.push("Inverter Battery Active"); }
        if value & (1 << 1) != 0 { statuses.push("Inverter Charging"); }
        if value & (1 << 2) != 0 { statuses.push("Inverter Discharging"); }
        if value & (1 << 3) != 0 { statuses.push("Battery Communication OK"); }
        if value & (1 << 4) != 0 { statuses.push("BMS Protocol Matched"); }
        if value & (1 << 5) != 0 { statuses.push("Battery Type Matched"); }
        if value & (1 << 6) != 0 { statuses.push("Battery Power Limited"); }
        if value & (1 << 7) != 0 { statuses.push("Battery Current Limited"); }
        if value & (1 << 8) != 0 { statuses.push("Battery Voltage Limited"); }
        if value & (1 << 9) != 0 { statuses.push("Battery Temperature Limited"); }
        if value & (1 << 10) != 0 { statuses.push("Battery SOC Limited"); }
        if value & (1 << 11) != 0 { statuses.push("Battery Maintenance Mode"); }
        if value & (1 << 12) != 0 { statuses.push("Battery Force Charging"); }
        if value & (1 << 13) != 0 { statuses.push("Battery Force Discharging"); }
        if value & (1 << 14) != 0 { statuses.push("Battery Calibration Mode"); }
        if value & (1 << 15) != 0 { statuses.push("Battery Protection Active"); }

        if statuses.is_empty() {
            statuses.push("No Status Flags Set");
        }
        
        statuses
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ModbusError {
    IllegalFunction = 0x01,    // Function code not supported
    IllegalAddress = 0x02,     // Data address outside valid range
    IllegalValue = 0x03,       // Data value out of bounds or invalid
    SlaveFailure = 0x04,       // Read/write operation failed
    SlaveIsBusy = 0x06,       // Device is busy processing another command
}

impl ModbusError {
    pub fn from_code(code: u8) -> Option<Self> {
        match code {
            0x01 => Some(Self::IllegalFunction),
            0x02 => Some(Self::IllegalAddress),
            0x03 => Some(Self::IllegalValue),
            0x04 => Some(Self::SlaveFailure),
            0x06 => Some(Self::SlaveIsBusy),
            _ => None,
        }
    }

    pub fn description(&self) -> &'static str {
        match self {
            Self::IllegalFunction => "Function code not supported by the inverter",
            Self::IllegalAddress => "Data address is outside valid range",
            Self::IllegalValue => "Data value is out of bounds or invalid",
            Self::SlaveFailure => "Inverter read/write operation failed",
            Self::SlaveIsBusy => "Inverter is busy processing another command",
        }
    }
}
