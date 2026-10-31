//! Fernet tokens, so a log file can be encrypted line by line and read back by the old Python
//! and JavaScript tools (`docs/spec/05-runtime-and-transports.md`, "File transport").
//!
//! A token is `base64url(0x80 ‖ 8-byte big-endian seconds ‖ 16-byte IV ‖ AES-128-CBC(PKCS7) ‖
//! HMAC-SHA256)`. The 32-byte key is URL-safe base64; its first 16 bytes sign, its last 16 encrypt.

use aes::Aes128;
use base64::engine::general_purpose::URL_SAFE;
use base64::Engine;
use cbc::cipher::block_padding::Pkcs7;
use cbc::cipher::{BlockDecryptMut, BlockEncryptMut, KeyIvInit};
use hmac::{Hmac, Mac};
use sha2::Sha256;

type Encryptor = cbc::Encryptor<Aes128>;
type Decryptor = cbc::Decryptor<Aes128>;
type HmacSha256 = Hmac<Sha256>;

const VERSION: u8 = 0x80;
const BAD_KEY: &str =
    "Fernet key must be 32 url-safe base64-encoded bytes \u{2014} generate one with \
generate_fernet_key() (or Python's cryptography.fernet.Fernet.generate_key())";
const NOT_A_TOKEN: &str = "Not a Fernet token \u{2014} was this line written with encrypt_key?";
const BAD_TOKEN: &str =
    "Fernet token failed verification \u{2014} wrong key, or the line was edited";

/// A Fernet key, ready to encrypt and decrypt.
#[derive(Clone)]
pub struct Fernet {
    signing: [u8; 16],
    encryption: [u8; 16],
}

/// A new random key in the form [`Fernet::new`] accepts.
pub fn generate_fernet_key() -> String {
    let mut key = [0u8; 32];
    // The operating system's random source failing is not something a logger can recover from.
    getrandom::fill(&mut key).expect("the operating system has no random source");
    URL_SAFE.encode(key)
}

impl Fernet {
    /// Reads a key; the error says how to make a valid one.
    pub fn new(key: &str) -> Result<Fernet, String> {
        let bytes = URL_SAFE
            .decode(key.trim())
            .map_err(|_| BAD_KEY.to_string())?;
        let bytes: [u8; 32] = bytes.try_into().map_err(|_| BAD_KEY.to_string())?;
        let mut signing = [0u8; 16];
        let mut encryption = [0u8; 16];
        signing.copy_from_slice(&bytes[..16]);
        encryption.copy_from_slice(&bytes[16..]);
        Ok(Fernet {
            signing,
            encryption,
        })
    }

    /// The token for `plaintext`, stamped now with a fresh random IV.
    pub fn encrypt(&self, plaintext: &[u8]) -> String {
        let mut iv = [0u8; 16];
        getrandom::fill(&mut iv).expect("the operating system has no random source");
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| d.as_secs());
        self.encrypt_with(plaintext, now, iv)
    }

    /// The token for a given time and IV (for tests and interoperability checks).
    pub fn encrypt_with(&self, plaintext: &[u8], timestamp: u64, iv: [u8; 16]) -> String {
        let ciphertext = Encryptor::new(&self.encryption.into(), &iv.into())
            .encrypt_padded_vec_mut::<Pkcs7>(plaintext);
        let mut body = Vec::with_capacity(1 + 8 + 16 + ciphertext.len() + 32);
        body.push(VERSION);
        body.extend_from_slice(&timestamp.to_be_bytes());
        body.extend_from_slice(&iv);
        body.extend_from_slice(&ciphertext);
        let mut mac = self.mac();
        mac.update(&body);
        body.extend_from_slice(&mac.finalize().into_bytes());
        URL_SAFE.encode(body)
    }

    fn mac(&self) -> HmacSha256 {
        // HMAC accepts any key length, so this cannot fail.
        HmacSha256::new_from_slice(&self.signing)
            .unwrap_or_else(|_| HmacSha256::new(&Default::default()))
    }

    /// The plaintext of a token, or why it cannot be read.
    pub fn decrypt(&self, token: &str) -> Result<Vec<u8>, String> {
        let bytes = URL_SAFE
            .decode(token.trim())
            .map_err(|_| NOT_A_TOKEN.to_string())?;
        if bytes.len() < 1 + 8 + 16 + 16 + 32 || bytes[0] != VERSION {
            return Err(NOT_A_TOKEN.to_string());
        }
        let (signed, tag) = bytes.split_at(bytes.len() - 32);
        let mut mac = self.mac();
        mac.update(signed);
        mac.verify_slice(tag).map_err(|_| BAD_TOKEN.to_string())?;
        let iv: [u8; 16] = signed[9..25]
            .try_into()
            .map_err(|_| NOT_A_TOKEN.to_string())?;
        Decryptor::new(&self.encryption.into(), &iv.into())
            .decrypt_padded_vec_mut::<Pkcs7>(&signed[25..])
            .map_err(|_| BAD_TOKEN.to_string())
    }
}
