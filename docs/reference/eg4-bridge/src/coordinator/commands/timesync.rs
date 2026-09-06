use crate::prelude::*;

use chrono::TimeZone;

use eg4::{
    inverter::WaitForReply,
    packet::{DeviceFunction, TranslatedData},
};

/// TimeSync handles the synchronization of time between the system and the EG4 inverter.
/// This is important for accurate logging and scheduling of operations.
pub struct TimeSync {
    channels: Channels,
    inverter: config::Inverter,
}

impl TimeSync {
    /// Creates a new TimeSync instance for a specific inverter
    /// 
    /// # Arguments
    /// * `channels` - Communication channels for sending/receiving packets
    /// * `inverter` - The inverter configuration to sync time with
    pub fn new(channels: Channels, inverter: config::Inverter) -> Self {
        Self { channels, inverter }
    }

    /// Executes the time synchronization process
    /// 
    /// This function:
    /// 1. Checks if time sync is allowed (not in read-only mode)
    /// 2. Reads the current time from the inverter
    /// 3. Compares it with the system time
    /// 4. Updates the inverter's time if the difference is significant
    /// 
    /// # Returns
    /// * `Result<()>` - Ok if successful, error if any step fails
    pub async fn run(&self) -> Result<()> {
        // Create a packet to read the current time from register 12
        // Register 12 contains the inverter's current time in BCD format
        let packet = Packet::TranslatedData(TranslatedData {
            datalog: self.inverter.datalog().expect("datalog must be set for timesync command"),
            device_function: DeviceFunction::ReadHold,
            inverter: self.inverter.serial().expect("serial must be set for timesync command"),
            register: 12,
            values: vec![3, 0],  // Read 3 registers (6 bytes) starting at offset 0
        });

        let mut receiver = self.channels.from_inverter.subscribe();

        // Send the read request to the inverter
        if let Err(e) = self.channels.to_coordinator.send(crate::coordinator::ChannelData::SendPacket(packet.clone())) {
            bail!("Failed to send packet to coordinator: {}", e);
        }

        // Wait for and process the inverter's response
        if let Packet::TranslatedData(td) = receiver.wait_for_reply(&packet).await? {
            // Extract time components from the response
            // Values are in BCD format: [year, month, day, hour, minute, second]
            let year = td.values[0] as u32;
            let month = td.values[1] as u32;
            let day = td.values[2] as u32;
            let hour = td.values[3] as u32;
            let minute = td.values[4] as u32;
            let second = td.values[5] as u32;

            // Convert inverter time to UTC DateTime
            // Inverter uses years since 2000, so we add 2000 to get the actual year
            let dt = chrono::Utc
                .with_ymd_and_hms(2000 + year as i32, month, day, hour, minute, second)
                .unwrap();

            // Get current system time in UTC and adjust for local timezone offset
            // This ensures we compare times in the same timezone
            let offset_in_sec =
                chrono::Duration::seconds(chrono::Local::now().offset().local_minus_utc() as i64);
            let now = Utils::utc() + offset_in_sec;

            // Calculate the time difference between inverter and system
            let time_diff = dt - now;
            info!(
                "Time sync for inverter {}: {}",
                self.inverter.datalog().map(|s| s.to_string()).unwrap_or_default(),
                time_diff
            );

            // Define thresholds for time synchronization
            // Maximum allowed time difference (10 minutes) - prevents large jumps
            let max_limit = chrono::Duration::seconds(600);
            // Minimum time difference to trigger update (30 seconds) - prevents unnecessary updates
            let min_limit = chrono::Duration::seconds(30);

            // Skip time sync if inverter is in read-only mode to prevent accidental changes
            if self.inverter.read_only() {
                info!("Skipping time sync for inverter {} (read-only mode)",
                    self.inverter.datalog().map(|s| s.to_string()).unwrap_or_default());
                return Ok(());
            }

            // Only update if time difference is significant but not too large
            // This prevents both unnecessary updates and dangerous large time jumps
            if (time_diff > min_limit && time_diff <= max_limit) || 
               (time_diff < -min_limit && time_diff >= -max_limit) {
                // Create and send the time update packet
                let packet = self.set_time_packet(now);

                if let Err(e) = self.channels.to_coordinator.send(crate::coordinator::ChannelData::SendPacket(packet.clone())) {
                    bail!("Failed to send packet to coordinator: {}", e);
                }

                // Wait for confirmation of the time update
                if let Packet::TranslatedData(_) = receiver.wait_for_reply(&packet).await? {
                    debug!("time set ok");
                } else {
                    warn!("time set didn't get confirmation reply!");
                }
            } else if time_diff.abs() > max_limit {
                // Log a warning if the time difference is too large
                // This might indicate a problem that needs manual intervention
                warn!(
                    "Time difference of {} exceeds maximum allowed adjustment of 10 minutes. Manual intervention may be required.",
                    time_diff
                );
            }
        }

        Ok(())
    }

    /// Creates a packet to set the inverter's time
    /// 
    /// # Arguments
    /// * `now` - The current system time in UTC
    /// 
    /// # Returns
    /// * `Packet` - A packet containing the new time values
    fn set_time_packet(&self, now: chrono::DateTime<chrono::Utc>) -> Packet {
        use chrono::{Datelike, Timelike};

        Packet::TranslatedData(TranslatedData {
            datalog: self.inverter.datalog().expect("datalog must be set for timesync command"),
            device_function: DeviceFunction::WriteMulti,
            inverter: self.inverter.serial().expect("serial must be set for timesync command"),
            register: 12,
            values: vec![
                (now.year() - 2000) as u8,  // Convert year to years since 2000
                now.month() as u8,
                now.day() as u8,
                now.hour() as u8,
                now.minute() as u8,
                now.second() as u8,
            ],
        })
    }
}
