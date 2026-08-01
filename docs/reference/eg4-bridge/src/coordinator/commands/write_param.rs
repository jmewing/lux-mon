use crate::prelude::*;
use log::{info, error};

use eg4::{
    inverter::WaitForReply,
    packet::{WriteParam as WriteParamPacket, Packet},
};

use crate::coordinator::Channels;
use crate::config;

pub struct WriteParam {
    channels: Channels,
    inverter: config::Inverter,
    register: u16,
    value: u16,
}

impl WriteParam {
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
        info!("Starting write param operation for inverter {} at register {} with value {}", 
            self.inverter.datalog().expect("datalog must be set for write_param command"),
            self.register,
            self.value
        );

        let packet = Packet::WriteParam(WriteParamPacket {
            datalog: self.inverter.datalog().expect("datalog must be set for write_param command"),
            register: self.register,
            values: self.value.to_le_bytes().to_vec(),
        });

        let mut receiver = self.channels.from_inverter.subscribe();

        info!("Sending write param packet to coordinator");
        if let Err(e) = self.channels.to_coordinator.send(crate::coordinator::ChannelData::SendPacket(packet.clone())) {
            bail!("Failed to send packet to coordinator: {}", e);
        }

        info!("Waiting for reply from inverter");
        let packet = receiver.wait_for_reply(&packet).await?;
        // WriteParam packets seem to reply with 0 on success, very odd
        if packet.value() != 0 {
            error!("Failed to set register {} - received non-zero response: {}", self.register, packet.value());
            bail!("failed to set register {}", self.register);
        }

        info!("Successfully wrote value {} to register {}", self.value, self.register);
        Ok(packet)
    }
}
