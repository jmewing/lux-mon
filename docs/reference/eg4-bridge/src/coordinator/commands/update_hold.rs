use crate::prelude::*;
use crate::eg4::inverter::WaitForReply;
use crate::eg4::{
    packet::{Packet, RegisterBit, DeviceFunction, TranslatedData},
    inverter::ChannelData,
};

pub struct UpdateHold {
    channels: Channels,
    inverter: config::Inverter,
    register: u16,
    bit: RegisterBit,
    enable: bool,
}

impl UpdateHold {
    pub fn new(
        channels: Channels,
        inverter: config::Inverter,
        register: u16,
        bit: RegisterBit,
        enable: bool,
    ) -> Self {
        Self {
            channels,
            inverter,
            register,
            bit,
            enable,
        }
    }

    pub async fn run(&self) -> Result<()> {
        let mut receiver = self.channels.from_inverter.subscribe();

        // First read the current value
        let read_packet = Packet::TranslatedData(TranslatedData {
            datalog: self.inverter.datalog().expect("datalog must be set"),
            device_function: DeviceFunction::ReadHold,
            inverter: self.inverter.serial().expect("serial must be set"),
            register: self.register,
            values: vec![1, 0],
        });

        self.channels
            .to_inverter
            .send(ChannelData::Packet(read_packet.clone()))
            .map_err(|e| anyhow!("send(to_inverter) failed: {}", e))?;

        let read_packet = receiver.wait_for_reply(&read_packet).await?;
        let current_value = read_packet.value();
        let new_value = if self.enable {
            current_value | (self.bit.clone() as u16)
        } else {
            current_value & !(self.bit.clone() as u16)
        };

        // Now write the new value
        let write_packet = Packet::TranslatedData(TranslatedData {
            datalog: self.inverter.datalog().expect("datalog must be set"),
            device_function: DeviceFunction::WriteSingle,
            inverter: self.inverter.serial().expect("serial must be set"),
            register: self.register,
            values: new_value.to_le_bytes().to_vec(),
        });

        self.channels
            .to_inverter
            .send(ChannelData::Packet(write_packet.clone()))
            .map_err(|e| anyhow!("send(to_inverter) failed: {}", e))?;

        let write_packet = receiver.wait_for_reply(&write_packet).await?;
        if write_packet.value() != new_value {
            bail!(
                "failed to update register {:?}, got back value {} (wanted {})",
                self.register,
                write_packet.value(),
                new_value
            );
        }

        Ok(())
    }
}
