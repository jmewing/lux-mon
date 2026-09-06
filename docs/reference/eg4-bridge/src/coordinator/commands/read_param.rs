use crate::prelude::*;

use eg4::{
    inverter::WaitForReply,
    packet::{ReadParam as ReadParamPacket, Packet},
};

use crate::coordinator::Channels;
use crate::config;

pub struct ReadParam {
    channels: Channels,
    inverter: config::Inverter,
    register: u16,
}

impl ReadParam {
    pub fn new<U>(channels: Channels, inverter: config::Inverter, register: U) -> Self
    where
        U: Into<u16>,
    {
        Self {
            channels,
            inverter,
            register: register.into(),
        }
    }

    pub async fn run(&self) -> Result<Packet> {
        let packet = Packet::ReadParam(ReadParamPacket {
            datalog: self.inverter.datalog().expect("datalog must be set for read_param command"),
            register: self.register,
            values: vec![], // unused for read param
        });

        let mut receiver = self.channels.from_inverter.subscribe();

        if let Err(e) = self.channels.to_coordinator.send(crate::coordinator::ChannelData::SendPacket(packet.clone())) {
            bail!("Failed to send packet to coordinator: {}", e);
        }

        let packet = receiver.wait_for_reply(&packet).await?;
        Ok(packet)
    }
}
