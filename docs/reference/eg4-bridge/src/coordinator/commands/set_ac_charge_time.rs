use crate::prelude::*;

#[allow(dead_code)]
pub struct SetAcChargeTime {
    channels: Channels,
    inverter: config::Inverter,
    values: [u8; 4],
}

impl SetAcChargeTime {
    pub fn new(channels: Channels, inverter: config::Inverter, values: [u8; 4]) -> Self {
        Self {
            channels,
            inverter,
            values,
        }
    }

    pub async fn run(&self) -> Result<()> {
        // Implementation will be added later
        Ok(())
    }
} 