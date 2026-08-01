use crate::prelude::*;
use serde::Deserialize;
use std::collections::HashMap;

#[derive(Debug, Clone, Deserialize)]
pub struct Register {
    pub register_number: u16,
    pub name: String,
    pub description: String,
    #[serde(rename = "datatype")]
    pub data_type: String,
    #[serde(default)]
    pub access: String,
    #[serde(default = "default_scaling")]
    pub scaling: f64,
    #[serde(default)]
    pub unit: String,
    #[serde(default)]
    pub shortname: String,
    #[serde(default)]
    pub read_only: bool,
}

fn default_scaling() -> f64 {
    1.0
}

#[derive(Debug, Clone, Deserialize)]
pub struct RegisterType {
    pub register_type: String,
    pub register_map: Vec<Register>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct RegisterMap {
    pub registers: Vec<RegisterType>,
}

#[derive(Clone)]
pub struct RegisterParser {
    registers: HashMap<u16, Register>,
}

impl RegisterParser {
    pub fn new(register_file: &str) -> Result<Self> {
        let content = std::fs::read_to_string(register_file)
            .map_err(|err| anyhow!("Error reading register file {}: {}", register_file, err))?;
        
        // Create a map of byte positions to line numbers
        let mut line_numbers: HashMap<usize, usize> = HashMap::new();
        let mut current_line = 1;
        for (pos, byte) in content.bytes().enumerate() {
            if byte == b'\n' {
                current_line += 1;
            }
            line_numbers.insert(pos, current_line);
        }

        let mut register_map: RegisterMap = serde_json::from_str(&content)
            .map_err(|err| anyhow!("Error parsing register file: {}", err))?;

        // Set access field based on read_only
        for register_type in &mut register_map.registers {
            for register in &mut register_type.register_map {
                if register.access.is_empty() {
                    register.access = if register.read_only { "read_only".to_string() } else { "read_write".to_string() };
                }
            }
        }

        let mut registers: HashMap<u16, Register> = HashMap::new();
        let mut shortnames: HashMap<String, (String, u16, usize)> = HashMap::new();
        let mut duplicates = Vec::new();
        
        // Parse the JSON content to find positions of register definitions
        let value: serde_json::Value = serde_json::from_str(&content)?;
        
        for (type_idx, register_type) in register_map.registers.iter().enumerate() {
            let mut type_registers: HashMap<u16, Register> = HashMap::new();
            
            // Get the JSON value for this register type
            let type_value = value.get("registers")
                .and_then(|arr| arr.get(type_idx))
                .ok_or_else(|| anyhow!("Could not find register type at index {}", type_idx))?;
            
            // Get the line number for this register type
            let _type_line = if let Some(pos) = content.find(&format!("\"register_type\":\"{}\"", register_type.register_type)) {
                line_numbers.get(&pos).copied().unwrap_or(0)
            } else {
                0
            };
            
            // First check for duplicates within this register type
            for (reg_idx, register) in register_type.register_map.iter().enumerate() {
                // Get the JSON value for this register
                let _reg_value = type_value.get("register_map")
                    .and_then(|arr| arr.get(reg_idx))
                    .ok_or_else(|| anyhow!("Could not find register at index {}", reg_idx))?;
                
                // Get the line number for this register
                let reg_line = if let Some(pos) = content.find(&format!("\"register_number\":{}", register.register_number)) {
                    line_numbers.get(&pos).copied().unwrap_or(0)
                } else {
                    0
                };
                
                if let Some(existing) = type_registers.get(&register.register_number) {
                    duplicates.push(format!(
                        "Register number {} is defined multiple times in type '{}':\n  - First: {} ({}) at line {}\n  - Second: {} ({}) at line {}",
                        register.register_number,
                        register_type.register_type,
                        existing.description,
                        existing.shortname,
                        reg_line,
                        register.description,
                        register.shortname,
                        reg_line
                    ));
                } else {
                    type_registers.insert(register.register_number, register.clone());
                }

                // Check for duplicate shortnames across all types
                let shortname = if !register.shortname.is_empty() {
                    register.shortname.clone()
                } else {
                    register.name.clone()
                };

                if let Some((existing_type, existing_number, existing_line)) = shortnames.get(&shortname) {
                    duplicates.push(format!(
                        "Shortname '{}' is used multiple times:\n  - First: register {} in type '{}' at line {}\n  - Second: register {} in type '{}' at line {}",
                        shortname,
                        existing_number,
                        existing_type,
                        existing_line,
                        register.register_number,
                        register_type.register_type,
                        reg_line
                    ));
                } else {
                    shortnames.insert(shortname, (register_type.register_type.clone(), register.register_number, reg_line));
                }
            }

            // Add registers from this type to the main map
            for (number, register) in type_registers {
                registers.insert(number, register);
            }
        }

        if !duplicates.is_empty() {
            let error_msg = format!(
                "Found {} duplicate register definitions:\n{}",
                duplicates.len(),
                duplicates.join("\n")
            );
            error!("{}", error_msg);
            bail!("{}", error_msg);
        }

        Ok(Self { registers })
    }

    pub fn get_register(&self, register_number: u16) -> Option<&Register> {
        self.registers.get(&register_number)
    }

    pub fn decode_registers(&self, raw_data: &HashMap<String, String>, show_unknown: bool, register_type: &str) -> HashMap<String, f64> {
        let mut decoded = HashMap::new();
        
        for (reg_num_str, hex_value) in raw_data {
            if let Ok(reg_num) = reg_num_str.parse::<u16>() {
                if let Some(register) = self.get_register(reg_num) {
                    let value = register.decode_value(hex_value);
                    let field_name = if !register.shortname.is_empty() {
                        register.shortname.clone()
                    } else {
                        register.name.clone()
                    };
                    decoded.insert(field_name, value);
                } else if show_unknown {
                    let value = u16::from_str_radix(hex_value, 16)
                        .unwrap_or(0) as f64;
                    decoded.insert(format!("{}_unknown_{}", register_type, reg_num), value);
                }
            }
        }

        decoded
    }
}

impl Register {
    pub fn decode_value(&self, hex_value: &str) -> f64 {
        let value = u16::from_str_radix(hex_value, 16)
            .unwrap_or(0) as f64;
        
        if self.data_type == "float" {
            value * self.scaling
        } else {
            value
        }
    }
} 