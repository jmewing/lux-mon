use crate::register::RegisterParser;

/// Parse and decode an input register value according to Table 7 of the protocol specification
pub fn parse_input_register(reg: u16, value: u32, schema: &RegisterParser) -> String {
    if let Some(register) = schema.get_register(reg) {
        let decoded_value = register.decode_value(&format!("{:04x}", value));
        format!("Input Register: {} - {} ({}): {} {}", 
            reg, 
            register.name,
            register.description,
            decoded_value,
            register.unit
        )
    } else {
        format!("Input Register: {} - Unknown register: {}", reg, value)
    }
} 
