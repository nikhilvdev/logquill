//! The rotating file transport (`docs/spec/05-runtime-and-transports.md`; decision D26).
//!
//! Each record is appended as one line (optionally a Fernet token) and flushed; when the file
//! reaches `max_bytes` it is rotated to `path.1`, the previous `path.1` to `path.2`, and so on up
//! to `backup_count`, optionally compressed (`path.1.gz`, `path.1.zip`). The file is always
//! reopened afterwards, even if a rename failed, so a rotation error cannot leave the transport
//! without a file.

use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::fernet::Fernet;
use crate::transport::{Formatter, Transport};

/// How a rotated file is compressed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Compression {
    /// `path.N.gz`
    Gz,
    /// `path.N.zip` holding one deflated entry named like the live file.
    Zip,
}

impl Compression {
    /// `gz` or `zip`.
    pub fn parse(name: &str) -> Result<Compression, String> {
        match name {
            "gz" => Ok(Compression::Gz),
            "zip" => Ok(Compression::Zip),
            other => Err(format!("compression must be 'gz' or 'zip', got '{other}'")),
        }
    }

    fn suffix(self) -> &'static str {
        match self {
            Compression::Gz => ".gz",
            Compression::Zip => ".zip",
        }
    }
}

/// Settings for a [`FileTransport`].
#[derive(Clone, Debug)]
pub struct FileOptions {
    /// Rotate once the file reaches this size; 0 never rotates. Default 10 MiB.
    pub max_bytes: u64,
    /// How many rotated files to keep; 0 deletes the file on rotation. Default 5.
    pub backup_count: usize,
    pub formatter: Formatter,
    /// Compress files as they are rotated out.
    pub compression: Option<Compression>,
    /// Encrypt every line with this Fernet key (URL-safe base64, 32 bytes).
    pub encrypt_key: Option<String>,
}

impl Default for FileOptions {
    fn default() -> Self {
        FileOptions {
            max_bytes: 10 * 1024 * 1024,
            backup_count: 5,
            formatter: Formatter::Json,
            compression: None,
            encrypt_key: None,
        }
    }
}

/// Appends records to a file, rotating by size.
pub struct FileTransport {
    path: PathBuf,
    options: FileOptions,
    fernet: Option<Fernet>,
    file: Option<File>,
    size: u64,
    /// Whether the path is a regular file; only those are rotated.
    rotatable: bool,
}

fn backup_path(path: &Path, index: usize, compression: Option<Compression>) -> PathBuf {
    let mut name = path.as_os_str().to_owned();
    name.push(format!(
        ".{index}{}",
        compression.map_or("", Compression::suffix)
    ));
    PathBuf::from(name)
}

/// Opens `path` for appending. The size is 0 for anything that is not a regular file (`/dev/null`,
/// `/dev/stdout`, a named pipe): those cannot be rotated, so they are never counted towards it.
fn open_append(path: &Path) -> io::Result<(File, u64)> {
    let file = OpenOptions::new().create(true).append(true).open(path)?;
    let meta = file.metadata()?;
    let size = if meta.is_file() { meta.len() } else { 0 };
    Ok((file, size))
}

/// Writes `source` compressed to `target`.
fn compress(source: &Path, target: &Path, compression: Compression) -> io::Result<()> {
    let mut input = File::open(source)?;
    let output = File::create(target)?;
    match compression {
        Compression::Gz => {
            let mut encoder = flate2::write::GzEncoder::new(output, flate2::Compression::default());
            io::copy(&mut input, &mut encoder)?;
            encoder.finish()?;
        }
        Compression::Zip => {
            let name = source
                .file_name()
                .map_or_else(|| "log".to_string(), |n| n.to_string_lossy().to_string());
            let mut archive = zip::ZipWriter::new(output);
            let options = zip::write::SimpleFileOptions::default()
                .compression_method(zip::CompressionMethod::Deflated);
            archive
                .start_file(name, options)
                .map_err(io::Error::other)?;
            io::copy(&mut input, &mut archive)?;
            archive.finish().map_err(io::Error::other)?;
        }
    }
    Ok(())
}

impl FileTransport {
    /// Opens `path` for appending, creating parent directories. A bad `encrypt_key` is refused here.
    pub fn new(path: impl Into<PathBuf>, options: FileOptions) -> Result<FileTransport, String> {
        let path = path.into();
        let fernet = options
            .encrypt_key
            .as_deref()
            .map(Fernet::new)
            .transpose()?;
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
        let rotatable = file.metadata().is_ok_and(|m| m.is_file());
        Ok(FileTransport {
            path,
            options,
            fernet,
            file: Some(file),
            size,
            rotatable,
        })
    }

    fn rotate(&mut self) -> Result<(), String> {
        self.file = None; // close before renaming (required on Windows)
        let mut failure = None;
        let mut step = |result: io::Result<()>| {
            if let Err(e) = result {
                failure.get_or_insert(e.to_string());
            }
        };
        let compression = self.options.compression;
        if self.options.backup_count > 0 {
            for index in (1..self.options.backup_count).rev() {
                let from = backup_path(&self.path, index, compression);
                if from.exists() {
                    let to = backup_path(&self.path, index + 1, compression);
                    let _ = fs::remove_file(&to);
                    step(fs::rename(&from, &to));
                }
            }
            let first = backup_path(&self.path, 1, compression);
            let _ = fs::remove_file(&first);
            match compression {
                None => step(fs::rename(&self.path, &first)),
                Some(kind) => {
                    // Keep the live file if compressing fails, so no record is lost.
                    let done = compress(&self.path, &first, kind)
                        .and_then(|()| fs::remove_file(&self.path));
                    step(done);
                }
            }
        } else {
            step(fs::remove_file(&self.path));
        }
        // Reopen whatever happened above, so one failed rename never leaves us without a file.
        match open_append(&self.path) {
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
        let text = self.fernet.as_ref().map_or_else(
            || formatted.to_string(),
            |f| f.encrypt(formatted.as_bytes()),
        );
        let line = format!("{text}\n");
        file.write_all(line.as_bytes())
            .and_then(|()| file.flush())
            .map_err(|e| e.to_string())?;
        self.size += line.len() as u64;
        if self.rotatable && self.options.max_bytes > 0 && self.size >= self.options.max_bytes {
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
