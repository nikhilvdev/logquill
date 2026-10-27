//! Reading a log file as it grows (`tail -f`, `dev`): from a saved offset, complete lines only,
//! and starting over when the file is rotated or truncated.

use std::fs::{File, Metadata};
use std::io::{self, Read, Seek, SeekFrom};
use std::path::PathBuf;

pub struct Follower {
    path: PathBuf,
    offset: u64,
    identity: Option<u64>,
    /// The start of a line whose end has not been written yet.
    partial: Vec<u8>,
}

#[cfg(unix)]
fn identity_of(meta: &Metadata) -> u64 {
    std::os::unix::fs::MetadataExt::ino(meta)
}

// shortcut: without inodes only a shrinking file is noticed as a rotation; a rotated file that grows
// past the old offset before the next poll is read from the middle. Upgrade with the file index on Windows.
#[cfg(not(unix))]
fn identity_of(_meta: &Metadata) -> u64 {
    0
}

impl Follower {
    pub fn new(path: impl Into<PathBuf>) -> Follower {
        Follower {
            path: path.into(),
            offset: 0,
            identity: None,
            partial: Vec::new(),
        }
    }

    /// Calls `line` with each complete line written since the last call. A missing file is not an
    /// error (it may be between rotations): it just yields nothing. A different file at the path,
    /// or a shorter one, is read from its start.
    pub fn poll(&mut self, mut line: impl FnMut(&str)) -> io::Result<()> {
        let mut file = match File::open(&self.path) {
            Ok(file) => file,
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(()),
            Err(e) => return Err(e),
        };
        let meta = file.metadata()?;
        let identity = identity_of(&meta);
        if self.identity.is_some_and(|old| old != identity) || meta.len() < self.offset {
            self.offset = 0;
            self.partial.clear();
        }
        self.identity = Some(identity);
        file.seek(SeekFrom::Start(self.offset))?;
        let mut chunk = [0u8; 64 * 1024];
        loop {
            let read = file.read(&mut chunk)?;
            if read == 0 {
                return Ok(());
            }
            self.offset += read as u64;
            self.partial.extend_from_slice(&chunk[..read]);
            while let Some(end) = self.partial.iter().position(|b| *b == b'\n') {
                let bytes: Vec<u8> = self.partial.drain(..=end).collect();
                let text = String::from_utf8_lossy(&bytes);
                let text = text.trim_end_matches(['\n', '\r']);
                if !text.trim().is_empty() {
                    line(text);
                }
            }
        }
    }
}
