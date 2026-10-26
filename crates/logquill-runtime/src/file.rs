//! The rotating file transport (`docs/spec/05-runtime-and-transports.md`; decision D26).
//!
//! Each record is appended as one line and flushed; when the file reaches `max_bytes` it is
//! rotated to `path.1`, the previous `path.1` to `path.2`, and so on up to `backup_count`. The
//! file is always reopened afterwards, even if a rename failed, so a rotation error cannot leave
//! the transport without a file.

use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::transport::{Formatter, Transport};

/// Settings for a [`FileTransport`].
#[derive(Clone, Debug)]
pub struct FileOptions {
    /// Rotate once the file reaches this size; 0 never rotates. Default 10 MiB.
    pub max_bytes: u64,
    /// How many rotated files to keep; 0 deletes the file on rotation. Default 5.
    pub backup_count: usize,
    pub formatter: Formatter,
}

impl Default for FileOptions {
    fn default() -> Self {
        FileOptions {
            max_bytes: 10 * 1024 * 1024,
            backup_count: 5,
            formatter: Formatter::Json,
        }
    }
}

/// Appends records to a file, rotating by size.
pub struct FileTransport {
    path: PathBuf,
    options: FileOptions,
    file: Option<File>,
    size: u64,
}

fn backup_path(path: &Path, index: usize) -> PathBuf {
    let mut name = path.as_os_str().to_owned();
    name.push(format!(".{index}"));
    PathBuf::from(name)
}

fn open_append(path: &Path) -> std::io::Result<(File, u64)> {
    let file = OpenOptions::new().create(true).append(true).open(path)?;
    let size = file.metadata()?.len();
    Ok((file, size))
}

impl FileTransport {
    /// Opens `path` for appending, creating parent directories.
    pub fn new(path: impl Into<PathBuf>, options: FileOptions) -> Result<FileTransport, String> {
        let path = path.into();
        if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
            fs::create_dir_all(parent).map_err(|e| {
                format!(
                    "FileTransport: couldn't create {} \u{2014} {e}",
                    parent.display()
                )
            })?;
        }
        let (file, size) = open_append(&path).map_err(|e| {
            format!(
                "FileTransport: couldn't open {} \u{2014} {e}",
                path.display()
            )
        })?;
        Ok(FileTransport {
            path,
            options,
            file: Some(file),
            size,
        })
    }

    fn rotate(&mut self) -> Result<(), String> {
        self.file = None; // close before renaming (required on Windows)
        let mut failure = None;
        let mut step = |result: std::io::Result<()>| {
            if let Err(e) = result {
                failure.get_or_insert(e.to_string());
            }
        };
        if self.options.backup_count > 0 {
            for index in (1..self.options.backup_count).rev() {
                let from = backup_path(&self.path, index);
                if from.exists() {
                    let to = backup_path(&self.path, index + 1);
                    let _ = fs::remove_file(&to);
                    step(fs::rename(&from, &to));
                }
            }
            let first = backup_path(&self.path, 1);
            let _ = fs::remove_file(&first);
            step(fs::rename(&self.path, &first));
        } else {
            step(fs::remove_file(&self.path));
        }
        // Reopen whatever happened above, so one failed rename never leaves us without a file.
        let reopened = open_append(&self.path);
        match reopened {
            Ok((file, size)) => {
                self.file = Some(file);
                self.size = size;
            }
            Err(e) => {
                failure.get_or_insert(format!(
                    "couldn't reopen {} \u{2014} {e}",
                    self.path.display()
                ));
            }
        }
        failure.map_or(Ok(()), |e| {
            Err(format!(
                "FileTransport: rotating {} failed \u{2014} {e}",
                self.path.display()
            ))
        })
    }
}

impl Transport for FileTransport {
    fn format(&self, record: &Value) -> String {
        self.options.formatter.format(record)
    }

    fn write(&mut self, formatted: &str, _record: &Value) -> Result<(), String> {
        if self.file.is_none() {
            let (file, size) = open_append(&self.path).map_err(|e| {
                format!(
                    "FileTransport: couldn't reopen {} \u{2014} {e}",
                    self.path.display()
                )
            })?;
            self.file = Some(file);
            self.size = size;
        }
        let Some(file) = self.file.as_mut() else {
            return Err("FileTransport: no open file".to_string());
        };
        let line = format!("{formatted}\n");
        file.write_all(line.as_bytes())
            .and_then(|()| file.flush())
            .map_err(|e| e.to_string())?;
        self.size += line.len() as u64;
        if self.options.max_bytes > 0 && self.size >= self.options.max_bytes {
            self.rotate()?;
        }
        Ok(())
    }

    fn flush(&mut self) -> Result<(), String> {
        self.file
            .as_mut()
            .map_or(Ok(()), |f| f.flush().map_err(|e| e.to_string()))
    }

    fn close(&mut self) -> Result<(), String> {
        let result = self.flush();
        self.file = None;
        result
    }
}
