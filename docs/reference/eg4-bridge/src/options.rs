use clap::Parser;

/// EG4 Bridge - A bridge for EG4 inverters
#[derive(Debug, Parser)]
#[clap(author, version)]
pub struct Options {
    /// Config file to read
    #[clap(short = 'c', long = "config", default_value = "config.yaml")]
    pub config_file: String,

    /// Optional runtime limit in seconds
    #[clap(short = 't', long = "time")]
    pub runtime: Option<u64>,
}

impl Options {
    pub fn new() -> Self {
        Self::parse()
    }
}
