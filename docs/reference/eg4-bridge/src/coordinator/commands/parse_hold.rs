use crate::register::RegisterParser;

/// Parse and decode a hold register value according to Table 8 of the protocol specification
pub fn parse_hold_register(reg: u16, value: u32, schema: &RegisterParser) -> String {
    if let Some(register) = schema.get_register(reg) {
        let decoded_value = register.decode_value(&format!("{:04x}", value));
        format!("Hold Register: {} - {} ({}): {} {}", 
            reg, 
            register.name,
            register.description,
            decoded_value,
            register.unit
        )
    } else {
        format!("Hold Register: {} - Unknown register: {}", reg, value)
    }
} 
