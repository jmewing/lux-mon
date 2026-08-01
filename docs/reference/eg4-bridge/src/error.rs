
/// Creates an anyhow error with the current file and line number
#[macro_export]
macro_rules! file_error {
    ($($arg:tt)*) => {
        anyhow!(
            "[{}:{}] {}",
            std::path::Path::new(file!()).file_name().unwrap().to_string_lossy(),
            line!(),
            format!($($arg)*)
        )
    };
}

/// Creates an anyhow error with the current file and line number, and includes a source error
#[macro_export]
macro_rules! file_error_with_source {
    ($source:expr, $($arg:tt)*) => {
        anyhow!(
            "[{}:{}] {}: {}",
            std::path::Path::new(file!()).file_name().unwrap().to_string_lossy(),
            line!(),
            format!($($arg)*),
            $source
        )
    };
}

/// Creates an anyhow error with the current file and line number, and includes a source error
#[macro_export]
macro_rules! file_error_with_source_no_fmt {
    ($source:expr, $msg:expr) => {
        anyhow!(
            "[{}:{}] {}: {}",
            std::path::Path::new(file!()).file_name().unwrap().to_string_lossy(),
            line!(),
            $msg,
            $source
        )
    };
} 