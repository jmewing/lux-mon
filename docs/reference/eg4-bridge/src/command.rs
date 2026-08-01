use crate::prelude::*;

#[derive(Debug, Clone)]
pub enum Command {
    ReadInputs(config::Inverter, u16),
    ReadInput(config::Inverter, u16, u16),
    ReadHold(config::Inverter, u16, u16),
    ReadParam(config::Inverter, u16),
    ReadAcChargeTime(config::Inverter, u16),
    ReadAcFirstTime(config::Inverter, u16),
    ReadChargePriorityTime(config::Inverter, u16),
    ReadForcedDischargeTime(config::Inverter, u16),
    SetHold(config::Inverter, u16, u16),
    WriteParam(config::Inverter, u16, u16),
    SetAcChargeTime(config::Inverter, u16, [u8; 4]),
    SetAcFirstTime(config::Inverter, u16, [u8; 4]),
    SetChargePriorityTime(config::Inverter, u16, [u8; 4]),
    SetForcedDischargeTime(config::Inverter, u16, [u8; 4]),
    ChargeRate(config::Inverter, u16),
    DischargeRate(config::Inverter, u16),
    AcCharge(config::Inverter, bool),
    ChargePriority(config::Inverter, bool),
    ForcedDischarge(config::Inverter, bool),
    AcChargeRate(config::Inverter, u16),
    AcChargeSocLimit(config::Inverter, u16),
    DischargeCutoffSocLimit(config::Inverter, u16),
}

impl Command {
    pub fn to_result_topic(&self) -> String {
        use Command::*;

        let rest = match self {
            ReadInputs(inverter, c) => format!("{}/read/inputs/{}", inverter.datalog().map(|s| s.to_string()).unwrap_or_default(), c),
            ReadInput(inverter, register, _) => format!("{}/read/input/{}", inverter.datalog().map(|s| s.to_string()).unwrap_or_default(), register),
            ReadHold(inverter, register, _) => format!("{}/read/hold/{}", inverter.datalog().map(|s| s.to_string()).unwrap_or_default(), register),
            ReadParam(inverter, register) => format!("{}/read/param/{}", inverter.datalog().map(|s| s.to_string()).unwrap_or_default(), register),
            ReadAcChargeTime(inverter, num) => format!("{}/read/ac_charge/{}", inverter.datalog().map(|s| s.to_string()).unwrap_or_default(), num),
            ReadAcFirstTime(inverter, num) => format!("{}/read/ac_first/{}", inverter.datalog().map(|s| s.to_string()).unwrap_or_default(), num),
            ReadChargePriorityTime(inverter, num) => format!("{}/read/charge_priority/{}", inverter.datalog().map(|s| s.to_string()).unwrap_or_default(), num),
            ReadForcedDischargeTime(inverter, num) => format!("{}/read/forced_discharge/{}", inverter.datalog().map(|s| s.to_string()).unwrap_or_default(), num),
            SetHold(inverter, register, _) => format!("{}/set/hold/{}", inverter.datalog().map(|s| s.to_string()).unwrap_or_default(), register),
            WriteParam(inverter, register, _) => format!("{}/set/param/{}", inverter.datalog().map(|s| s.to_string()).unwrap_or_default(), register),
            SetAcChargeTime(inverter, num, _) => format!("{}/set/ac_charge/{}", inverter.datalog().map(|s| s.to_string()).unwrap_or_default(), num),
            SetAcFirstTime(inverter, num, _) => format!("{}/set/ac_first/{}", inverter.datalog().map(|s| s.to_string()).unwrap_or_default(), num),
            SetChargePriorityTime(inverter, num, _) => format!("{}/set/charge_priority/{}", inverter.datalog().map(|s| s.to_string()).unwrap_or_default(), num),
            SetForcedDischargeTime(inverter, num, _) => format!("{}/set/forced_discharge/{}", inverter.datalog().map(|s| s.to_string()).unwrap_or_default(), num),
            AcCharge(inverter, _) => format!("{}/set/ac_charge", inverter.datalog().map(|s| s.to_string()).unwrap_or_default()),
            ChargePriority(inverter, _) => format!("{}/set/charge_priority", inverter.datalog().map(|s| s.to_string()).unwrap_or_default()),
            ForcedDischarge(inverter, _) => format!("{}/set/forced_discharge", inverter.datalog().map(|s| s.to_string()).unwrap_or_default()),
            ChargeRate(inverter, _) => format!("{}/set/charge_rate_pct", inverter.datalog().map(|s| s.to_string()).unwrap_or_default()),
            DischargeRate(inverter, _) => format!("{}/set/discharge_rate_pct", inverter.datalog().map(|s| s.to_string()).unwrap_or_default()),
            AcChargeRate(inverter, _) => format!("{}/set/ac_charge_rate_pct", inverter.datalog().map(|s| s.to_string()).unwrap_or_default()),
            AcChargeSocLimit(inverter, _) => format!("{}/set/ac_charge_soc_limit_pct", inverter.datalog().map(|s| s.to_string()).unwrap_or_default()),
            DischargeCutoffSocLimit(inverter, _) => format!("{}/set/discharge_cutoff_soc_limit_pct", inverter.datalog().map(|s| s.to_string()).unwrap_or_default()),
        };

        format!("result/{}", rest)
    }
}
