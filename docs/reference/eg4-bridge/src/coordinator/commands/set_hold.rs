use crate::prelude::*;

use eg4::{
    inverter::WaitForReply,
    packet::{DeviceFunction, TranslatedData},
};

pub struct SetHold {
    channels: Channels,
    inverter: config::Inverter,
    register: u16,
    value: u16,
}

impl SetHold {
    pub fn new<U>(channels: Channels, inverter: config::Inverter, register: U, value: u16) -> Self
    where
        U: Into<u16>,
    {
        Self {
            channels,
            inverter,
            register: register.into(),
            value,
        }
    }

    pub async fn run(&self) -> Result<Packet> {
        // Skip write if inverter is in read-only mode
        if self.inverter.read_only() {
            bail!("Cannot set holding register {} to value {} - inverter {} is in read-only mode", 
                self.register, self.value, self.inverter.datalog().map(|s| s.to_string()).unwrap_or_default());
        }

        let packet = Packet::TranslatedData(TranslatedData {
            datalog: self.inverter.datalog().expect("datalog must be set for set_hold command"),
            device_function: DeviceFunction::WriteSingle,
            inverter: self.inverter.serial().expect("serial must be set for set_hold command"),
            register: self.register,
            values: self.value.to_le_bytes().to_vec(),
        });

        let mut receiver = self.channels.from_inverter.subscribe();

        // Log the packet being sent
        if let Packet::TranslatedData(td) = &packet {
            info!("[set_hold] Sending TranslatedData packet to inverter - function: {:?}, register: {}, datalog: {}", 
                td.device_function, self.register, td.datalog);
        }

        if let Err(e) = self.channels.to_coordinator.send(crate::coordinator::ChannelData::SendPacket(packet.clone())) {
            bail!("Failed to send packet to coordinator: {}", e);
        }

        let packet = receiver.wait_for_reply(&packet).await?;
        if packet.value() != self.value {
            bail!(
                "failed to set register {}, got back value {} (wanted {})",
                self.register,
                packet.value(),
                self.value
            );
        }

        Ok(packet)
    }
}
